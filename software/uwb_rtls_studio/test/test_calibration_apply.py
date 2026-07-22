import os
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
PROJECT = os.path.normpath(os.path.join(ROOT, ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

from PyQt6.QtCore import QCoreApplication

_app = QCoreApplication.instance() or QCoreApplication([])

import utils.app_state
try:
    utils.app_state.shared_app_state.objectName()
except RuntimeError:
    utils.app_state.shared_app_state = utils.app_state.SharedAppState()

from PyQt6.QtCore import QObject
from viewmodels.calibration_viewmodel import CalibrationViewModel


class MockPacket:
    class Hdr:
        seq = 1

    hdr = Hdr()


class MockDeviceModel(QObject):
    def __init__(self, role="TAG"):
        super().__init__()
        self.connected_role = role
        self.sent_pos_configs = []
        self.pos_config_requests = 0
        self.imu_resets = 0
        self.imu_calibrations = 0

    def set_pos_calib_config(self, config_data: dict):
        self.sent_pos_configs.append(dict(config_data or {}))
        return MockPacket()

    def request_pos_calib_config(self, force=False):
        self.pos_config_requests += 1

    def request_imu_reset(self):
        self.imu_resets += 1
        return MockPacket()

    def request_imu_calibration(self):
        self.imu_calibrations += 1
        return MockPacket()


def test_save_position_config_sends_pos_calib_cfg_set():
    model = MockDeviceModel(role="ANCHOR")
    vm = CalibrationViewModel(model)
    config = {"samples": 15, "iterations": 50}

    ok = vm.save_position_calibration_config(config)

    assert ok is True
    assert model.sent_pos_configs == [config]


def test_reset_and_calibrate_imu_delegate_to_model():
    model = MockDeviceModel(role="TAG")
    vm = CalibrationViewModel(model)

    vm.reset_imu()
    vm.calibrate_imu()

    assert model.imu_resets == 1
    assert model.imu_calibrations == 1
