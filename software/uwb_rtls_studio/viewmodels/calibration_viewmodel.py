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
        self._apply_state = "idle"  # "idle" | "sending_sys" | "sending_pos" | "sending_candidate"
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
    def model(self):
        return self._model

    @property
    def latest_status(self) -> dict:
        return self._latest_status.copy()

    @property
    def is_applying(self) -> bool:
        return self._apply_state != "idle"

    def initialize(self):
        # Config/bootstrap flow fetches these packets after connect. Avoid
        # eager duplicate GETs here because they create a separate DEFAULT
        # query report before the connected-device flow starts.
        return None

    def start_calibration(self, config: dict):
        """Start TAG antenna-delay calibration via firmware calib_start."""
        try:
            if getattr(self._model, "connected_role", "") != "TAG":
                self.operation_failed.emit("Antenna calibration is only implemented by TAG firmware.")
                return False

            payload = dict(config or {})
            ref_xy = float(payload.get("ref_distance_xy_m", 2.0) or 2.0)
            sample_target = max(1, min(64, int(payload.get("samples", 32) or 32)))
            tag_x_m = float(payload.get("tag_x_m", ref_xy))
            tag_y_m = float(payload.get("tag_y_m", 0.0))
            tag_z_m = float(payload.get("tag_z_m", payload.get("tag_height_m", 1.0)) or 1.0)

            pkt = self._model.request_calibration_start(
                sample_target=sample_target,
                tag_x_m=tag_x_m,
                tag_y_m=tag_y_m,
                tag_z_m=tag_z_m,
            )
            if pkt is None:
                self.operation_failed.emit("Failed to queue calib_start.")
                return False
            self._latest_status.update({
                "state": 2,
                "progress_percent": 0,
                "sample_count": 0,
                "sample_target": sample_target,
                "custom_status_text": "TAG antenna calibration started.",
            })
            self.status_updated.emit(self.latest_status)
            self._set_running(True)
            self._poll_status()
            return True
        except Exception as exc:
            log.exception("Failed to start TAG antenna calibration")
            self.operation_failed.emit(str(exc))
            return False

    def save_position_calibration_config(self, config: dict):
        """Save pos_calib_cfg_t. Current firmware has no host start/stop for anchor survey."""
        try:
            self._model.set_pos_calib_config(dict(config or {}))
            self._latest_status.update({
                "state": 0,
                "progress_percent": 0,
                "custom_status_text": "Position calibration config sent.",
            })
            self.status_updated.emit(self.latest_status)
            QTimer.singleShot(250, lambda: self._request_pos_config_verify(force_anyway=True))
            return True
        except Exception as exc:
            log.exception("Failed to save position calibration config")
            self.operation_failed.emit(str(exc))
            return False

    def stop_calibration(self):
        try:
            if getattr(self._model, "connected_role", "") == "TAG":
                self._model.request_calibration_stop()
        except Exception as exc:
            log.warning("Failed to send calib_stop: %s", exc)
            self.operation_failed.emit(str(exc))
        self._watchdog_timer.stop()
        self._apply_state = "idle"
        self._pending_pos_config = None
        self._set_running(False)

    def apply_candidate_results(self, anchor_mask: int | None = None):
        if self.is_applying:
            self.operation_failed.emit("Apply already in progress.")
            return False
        if getattr(self._model, "connected_role", "") != "TAG":
            self.operation_failed.emit("Calibration candidate apply is only implemented by TAG firmware.")
            return False
        status = self.latest_status
        if int(status.get("state", 0)) != 4:
            self.operation_failed.emit("Calibration is not done yet.")
            return False
        mask = int(anchor_mask if anchor_mask is not None else status.get("candidate_mask", 0) or 0)
        if mask <= 0:
            self.operation_failed.emit("No valid calibration candidate mask to apply.")
            return False

        try:
            self._apply_state = "sending_candidate"
            self._watchdog_timer.start(4000)
            pkt = self._model.request_calibration_candidate_apply(mask)
            if pkt is None:
                self._watchdog_timer.stop()
                self._apply_state = "idle"
                self.operation_failed.emit("Failed to queue calib_candidate_apply.")
                return False
            self._latest_status.update({
                "state": 3,
                "progress_percent": 95,
                "custom_status_text": f"Applying candidate mask 0x{mask:02X}...",
            })
            self.status_updated.emit(self.latest_status)
            QTimer.singleShot(600, self._finish_candidate_apply)
            return True
        except Exception as exc:
            self._watchdog_timer.stop()
            self._apply_state = "idle"
            log.exception("Failed to apply calibration candidate")
            self.operation_failed.emit(str(exc))
            return False

    def apply_results_sequence(self, tx_delay: int, rx_delay: int, pos_config: dict):
        # Backward-compatible entrypoint; current firmware applies computed candidates.
        return self.apply_candidate_results()

    def _finish_candidate_apply(self):
        if self._apply_state != "sending_candidate":
            return
        self._watchdog_timer.stop()
        self._apply_state = "idle"
        self._latest_status.update({
            "state": 4,
            "progress_percent": 100,
            "custom_status_text": "Calibration apply command sent. Refreshing device config...",
        })
        self.status_updated.emit(self.latest_status)
        try:
            if hasattr(self._model, "request_sys_config"):
                self._model.request_sys_config(force=True)
            self._model.request_calibration_status()
        except Exception as exc:
            log.debug("Refresh after candidate apply failed: %s", exc)

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
            self._model.set_pos_calib_config(dict(self._pending_pos_config or {}))
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

    def _request_pos_config_verify(self, force_anyway: bool = False):
        if not force_anyway and self._apply_state != "sending_pos":
            return
        if not hasattr(self._model, "request_pos_calib_config"):
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

        state_name = "Calibration candidate apply" if self._apply_state == "sending_candidate" else ("Antenna delays (1/2)" if self._apply_state == "sending_sys" else "Position parameters (2/2)")
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
