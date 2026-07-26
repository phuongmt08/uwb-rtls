"""
===============================================================================
  UWB RTLS Studio — Calibration ViewModel
===============================================================================
  File        : viewmodels/calibration_viewmodel.py
  Description : ViewModel cho tab "Calibration" (Tab 4 — Developer).
                Quản lý pos_calib_cfg (position auto-calib survey config) và
                IMU calib. Antenna-delay calibration đã chuyển hẳn sang
                AntennaDelayCalibrationViewModel (host-driven, xem
                viewmodels/antenna_delay_calibration_viewmodel.py) — firmware
                không còn state machine calibration nào để poll/apply nữa.

  MVVM Role   : VIEWMODEL

  Sections:
    - Calibration Config (pos_calib_cfg: params + save)
    - IMU Calibration (reset + start)

  Protocol Messages: tags 40-45, 69-70
===============================================================================
"""
import logging

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

log = logging.getLogger(__name__)


class CalibrationViewModel(QObject):
    operation_failed = pyqtSignal(str)

    def __init__(self, device_model, parent=None):
        super().__init__(parent)
        self._model = device_model

    @property
    def model(self):
        return self._model

    def initialize(self):
        # Config/bootstrap flow fetches these packets after connect. Avoid
        # eager duplicate GETs here because they create a separate DEFAULT
        # query report before the connected-device flow starts.
        return None

    def save_position_calibration_config(self, config: dict):
        """Save pos_calib_cfg_t. Current firmware has no host start/stop for anchor survey."""
        try:
            self._model.set_pos_calib_config(dict(config or {}))
            QTimer.singleShot(250, self._request_pos_config_verify)
            return True
        except Exception as exc:
            log.exception("Failed to save position calibration config")
            self.operation_failed.emit(str(exc))
            return False

    def _request_pos_config_verify(self):
        if not hasattr(self._model, "request_pos_calib_config"):
            return
        try:
            self._model.request_pos_calib_config(force=True)
        except TypeError:
            self._model.request_pos_calib_config()
        except Exception as exc:
            log.debug("Forced pos_calib_cfg_get failed during save verify: %s", exc)

    def reset_imu(self):
        return self._model.request_imu_reset()

    def calibrate_imu(self):
        return self._model.request_imu_calibration()
