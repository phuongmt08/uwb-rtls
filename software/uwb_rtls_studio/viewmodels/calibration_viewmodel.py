"""
===============================================================================
  UWB RTLS Studio — Calibration ViewModel
===============================================================================
  File        : viewmodels/calibration_viewmodel.py
  Description : ViewModel cho tab "Calibration" (Tab 4 — Developer).
                Quản lý anchor layout, antenna delay calibration, IMU calib.

  MVVM Role   : VIEWMODEL

  Sections:
    - Anchor Layout table (read/write anchor positions)
    - Calibration Config (params + start/stop)
    - Calibration Status (progress, diagnostics)
    - IMU Calibration (reset + start)

  Protocol Messages: tags 40-45, 63-64, 69-70
===============================================================================
"""
import logging

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from utils.app_state import shared_app_state

log = logging.getLogger(__name__)


class CalibrationViewModel(QObject):
    status_updated = pyqtSignal(dict)
    running_changed = pyqtSignal(bool)
    operation_failed = pyqtSignal(str)

    TERMINAL_STATES = {4, 5}

    def __init__(self, device_model, parent=None):
        super().__init__(parent)
        self._model = device_model
        self._running = False
        self._latest_status: dict = {}

        # Event-driven apply sequence state
        self._apply_state = "idle"  # "idle" | "sending_sys" | "sending_pos"
        self._pending_pos_config = None
        self._expected_tx_delay = 0
        self._expected_rx_delay = 0

        self._watchdog_timer = QTimer(self)
        self._watchdog_timer.setSingleShot(True)
        self._watchdog_timer.timeout.connect(self._on_apply_timeout)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(500)
        self._poll_timer.timeout.connect(self._poll_status)
        shared_app_state.calib_status_changed.connect(self._on_status)

        # Connect to device configuration parsed events
        self._model.sys_config_parsed.connect(self._on_sys_config_parsed)
        self._model.pos_calib_cfg_parsed.connect(self._on_pos_calib_cfg_parsed)

    @property
    def latest_status(self) -> dict:
        return self._latest_status.copy()

    @property
    def is_applying(self) -> bool:
        return self._apply_state != "idle"

    def initialize(self):
        self._model.request_pos_calib_config()
        self._model.request_calibration_status()

    def start_calibration(self, config: dict):
        try:
            payload = dict(config)
            if "enable_anchor_auto_calib" not in payload:
                payload["enable_anchor_auto_calib"] = True
            self._model.set_pos_calib_config(**payload)
            self._set_running(True)
            self._poll_status()
        except Exception as exc:
            log.exception("Failed to start calibration")
            self.operation_failed.emit(str(exc))

    def stop_calibration(self):
        """Stop host polling; the current protobuf has no calibration-stop command."""
        self._watchdog_timer.stop()
        self._apply_state = "idle"
        self._pending_pos_config = None
        self._set_running(False)

    def apply_results_sequence(self, tx_delay: int, rx_delay: int, pos_config: dict):
        if self.is_applying:
            self.operation_failed.emit("Apply already in progress.")
            return False
        if int(tx_delay) <= 0 or int(rx_delay) <= 0:
            self.operation_failed.emit("Calibration has no valid antenna delay to apply.")
            return False
        log.info("Initiating apply sequence: tx_delay=%s, rx_delay=%s", tx_delay, rx_delay)

        # Step 0: Update apply sequence state and expected values
        self._apply_state = "sending_sys"
        self._pending_pos_config = pos_config.copy()
        self._expected_tx_delay = tx_delay
        self._expected_rx_delay = rx_delay

        # Emit 10% progress and custom status message
        self._latest_status.update({
            "state": 2,  # Collecting
            "progress_percent": 10,
            "custom_status_text": "Applying UWB Antenna delays (1/2)..."
        })
        self.status_updated.emit(self.latest_status)

        # Step 1: Send sys_config_set command to update Antenna Delays
        try:
            cfg = shared_app_state.sys_config.copy()
            cfg.update({
                "tx_antenna_delay": tx_delay,
                "rx_antenna_delay": rx_delay,
            })
            self._watchdog_timer.start(4000)
            self._model.set_sys_config(**cfg)
            QTimer.singleShot(200, self._request_sys_config_verify)
        except Exception as exc:
            self._watchdog_timer.stop()
            self._apply_state = "idle"
            self._pending_pos_config = None
            log.exception("Failed to write UWB delays during apply sequence")
            self.operation_failed.emit(f"Failed to write UWB delays: {exc}")

    def _on_sys_config_parsed(self, cfg_dict: dict):
        if self._apply_state != "sending_sys":
            return

        tx = int(cfg_dict.get("tx_antenna_delay", -1))
        rx = int(cfg_dict.get("rx_antenna_delay", -1))
        if tx != int(self._expected_tx_delay) or rx != int(self._expected_rx_delay):
            log.debug(
                "Received sys_config_parsed but delays do not match. Expected tx=%s, rx=%s. Got tx=%s, rx=%s. Ignoring.",
                self._expected_tx_delay,
                self._expected_rx_delay,
                tx,
                rx,
            )
            return

        log.info("UWB Antenna delays verified on device (1/2).")
        self._watchdog_timer.stop()

        # Step 2: Transition state and emit 60% progress
        self._apply_state = "sending_pos"
        self._latest_status.update({
            "state": 3,  # Calculating
            "progress_percent": 60,
            "custom_status_text": "Applying Position parameters (2/2)..."
        })
        self.status_updated.emit(self.latest_status)

        # Send pos_calib_cfg_set command
        try:
            self._watchdog_timer.start(4000)
            self._model.set_pos_calib_config(**self._pending_pos_config)
            QTimer.singleShot(200, self._request_pos_config_verify)
        except Exception as exc:
            self._watchdog_timer.stop()
            self._apply_state = "idle"
            self._pending_pos_config = None
            log.exception("Failed to write Position config during apply sequence")
            self.operation_failed.emit(f"Failed to write Position parameters: {exc}")

    def _on_pos_calib_cfg_parsed(self, cfg_dict: dict):
        if self._apply_state != "sending_pos":
            return

        if self._pending_pos_config and not self._pos_config_matches(cfg_dict, self._pending_pos_config):
            log.debug("Received pos_calib_cfg_parsed but config does not match pending apply. Ignoring.")
            return

        log.info("Position parameters verified on device (2/2).")
        self._watchdog_timer.stop()

        # Step 3: Transition state back to idle and emit 100% progress
        self._apply_state = "idle"
        self._pending_pos_config = None
        self._latest_status.update({
            "state": 4,  # Done
            "progress_percent": 100,
            "custom_status_text": "Calibration applied successfully!"
        })
        self.status_updated.emit(self.latest_status)

    def _request_sys_config_verify(self):
        if self._apply_state != "sending_sys" or not hasattr(self._model, "request_sys_config"):
            return
        try:
            self._model.request_sys_config(force=True)
        except TypeError:
            self._model.request_sys_config()
        except Exception as exc:
            log.debug("Forced sys_config_get failed during apply verify: %s", exc)

    def _request_pos_config_verify(self):
        if self._apply_state != "sending_pos" or not hasattr(self._model, "request_pos_calib_config"):
            return
        try:
            self._model.request_pos_calib_config(force=True)
        except TypeError:
            self._model.request_pos_calib_config()
        except Exception as exc:
            log.debug("Forced pos_calib_cfg_get failed during apply verify: %s", exc)

    @staticmethod
    def _pos_config_matches(actual: dict, expected: dict) -> bool:
        for key, expected_value in expected.items():
            if key not in actual:
                continue
            actual_value = actual.get(key)
            if isinstance(expected_value, float):
                if abs(float(actual_value) - expected_value) > 1e-5:
                    return False
            elif actual_value != expected_value:
                return False
        return True

    def _on_apply_timeout(self):
        if self._apply_state == "idle":
            return

        state_name = "Antenna delays (1/2)" if self._apply_state == "sending_sys" else "Position parameters (2/2)"
        log.warning(f"Apply calibration timed out waiting for device response in state {self._apply_state}")

        self._apply_state = "idle"
        self._pending_pos_config = None

        self._latest_status.update({
            "state": 5,  # Error
            "progress_percent": 0,
            "custom_status_text": f"Apply failed: Timeout waiting for device to confirm {state_name}"
        })
        self.status_updated.emit(self.latest_status)
        self.operation_failed.emit(f"Timeout waiting for device response to {state_name}")
        return False

    def reset_imu(self):
        return self._model.request_imu_reset()

    def calibrate_imu(self):
        return self._model.request_imu_calibration()

    def _poll_status(self):
        try:
            self._model.request_calibration_status()
        except Exception as exc:
            log.warning("Calibration status poll failed: %s", exc)
            self.operation_failed.emit(str(exc))
            self._set_running(False)

    def _on_status(self, status: dict):
        if self._apply_state != "idle":
            # Ignore device calibration status polling updates during apply sequence
            return
        self._latest_status = dict(status)
        self.status_updated.emit(self.latest_status)
        if int(status.get("state", 0)) in self.TERMINAL_STATES:
            self._set_running(False)

    def _set_running(self, running: bool):
        running = bool(running)
        if running:
            if not self._poll_timer.isActive():
                self._poll_timer.start()
        else:
            self._poll_timer.stop()
        if self._running != running:
            self._running = running
            self.running_changed.emit(running)
