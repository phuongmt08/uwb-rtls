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

from PyQt6.QtCore import QObject, pyqtSignal
from viewmodels.calibration_viewmodel import CalibrationViewModel
from viewmodels.antenna_delay_calibration_viewmodel import AntennaDelayCalibrationViewModel


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
        self.bcast_sets = []

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

    def request_antenna_delay_bcast_set(self, serial_number, tx_antenna_delay, rx_antenna_delay, persist=False):
        self.bcast_sets.append({
            "serial_number": serial_number,
            "tx": tx_antenna_delay,
            "rx": rx_antenna_delay,
            "persist": persist
        })


class MockRangingModel(QObject):
    anchor_distances_updated = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self.anchor_layout = [
            {"anchor_id": 1, "x_m": 0.0, "y_m": 0.0, "z_m": 0.0},
            {"anchor_id": 2, "x_m": 10.0, "y_m": 0.0, "z_m": 0.0},
        ]


class MockGeofenceRepo:
    def get_anchors(self):
        return [
            {"anchor_id": 1, "serial_number": 0xA1A1A1A1},
            {"anchor_id": 2, "serial_number": 0xB2B2B2B2},
        ]
    def set_anchors(self, anchors):
        pass


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


def test_antenna_delay_parallel_start_all():
    dev_model = MockDeviceModel()
    rng_model = MockRangingModel()
    repo = MockGeofenceRepo()

    vm = AntennaDelayCalibrationViewModel(dev_model, rng_model, repo)
    vm.samples_per_round_target = 5

    ok = vm.start_all(tag_x_m=0.0, tag_y_m=3.0, tag_z_m=0.0)
    assert ok is True
    assert vm.is_running is True

    # Simulate incoming TDMA ranging samples for both Anchors
    for _ in range(5):
        rng_model.anchor_distances_updated.emit([
            {"anchor_id": 1, "distance_mm": 3000},
            {"anchor_id": 2, "distance_mm": 10440},
        ])

    assert len(dev_model.bcast_sets) >= 2
    assert any(b["serial_number"] == 0xA1A1A1A1 for b in dev_model.bcast_sets)
    assert any(b["serial_number"] == 0xB2B2B2B2 for b in dev_model.bcast_sets)
