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

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(500)
        self._poll_timer.timeout.connect(self._poll_status)
        shared_app_state.calib_status_changed.connect(self._on_status)

    @property
    def latest_status(self) -> dict:
        return self._latest_status.copy()

    def initialize(self):
        self._model.request_pos_calib_config()
        self._model.request_calibration_status()

    def start_calibration(self, config: dict):
        try:
            payload = dict(config)
            payload["enable_anchor_auto_calib"] = True
            self._model.set_pos_calib_config(**payload)
            self._set_running(True)
            self._poll_status()
        except Exception as exc:
            log.exception("Failed to start calibration")
            self.operation_failed.emit(str(exc))

    def stop_calibration(self):
        """Stop host polling; the current protobuf has no calibration-stop command."""
        self._set_running(False)

    def apply_results(self, tx_delay: int | None = None, rx_delay: int | None = None):
        delay = int(self._latest_status.get("current_antenna_delay", 0))
        tx_delay = delay if tx_delay is None else int(tx_delay)
        rx_delay = delay if rx_delay is None else int(rx_delay)
        if tx_delay <= 0 or rx_delay <= 0:
            self.operation_failed.emit("Calibration has no valid antenna delay to apply.")
            return False

        cfg = shared_app_state.sys_config
        cfg.update({
            "tx_antenna_delay": tx_delay,
            "rx_antenna_delay": rx_delay,
        })
        self._model.set_sys_config(**cfg)
        return True

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
