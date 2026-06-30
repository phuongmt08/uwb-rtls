import os
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
PROJECT = os.path.normpath(os.path.join(ROOT, ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

from PyQt6.QtCore import QCoreApplication
# Ensure QCoreApplication exists before anything else
_app = QCoreApplication.instance() or QCoreApplication([])

import utils.app_state
try:
    # Check if the wrapped C++ object is still valid
    utils.app_state.shared_app_state.objectName()
except RuntimeError:
    # If deleted (e.g. from previous pytest session teardown), re-create it
    utils.app_state.shared_app_state = utils.app_state.SharedAppState()

from PyQt6.QtCore import QObject, pyqtSignal
from viewmodels.calibration_viewmodel import CalibrationViewModel
from utils.app_state import shared_app_state


class MockDeviceModel(QObject):
    sys_config_parsed = pyqtSignal(dict)
    pos_calib_cfg_parsed = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.sent_sys_configs = []
        self.sent_pos_configs = []

    def set_sys_config(self, **kwargs):
        self.sent_sys_configs.append(kwargs)

    def set_pos_calib_config(self, **kwargs):
        self.sent_pos_configs.append(kwargs)

    def request_pos_calib_config(self):
        pass

    def request_calibration_status(self):
        pass


def test_calibration_apply_sequence():
    model = MockDeviceModel()
    vm = CalibrationViewModel(model)

    # Track updates received from vm.status_updated
    statuses = []
    vm.status_updated.connect(lambda s: statuses.append(s))

    # Initiating sequence
    pos_config = {"samples": 15, "iterations": 50}
    vm.apply_results_sequence(tx_delay=1234, rx_delay=5678, pos_config=pos_config)

    # Should emit 10% progress
    assert len(statuses) == 1
    assert statuses[-1]["progress_percent"] == 10
    assert statuses[-1]["custom_status_text"] == "Applying UWB Antenna delays (1/2)..."
    assert vm._apply_state == "sending_sys"
    assert len(model.sent_sys_configs) == 1
    assert model.sent_sys_configs[0]["tx_antenna_delay"] == 1234
    assert model.sent_sys_configs[0]["rx_antenna_delay"] == 5678

    # Simulate device responding with matching sys config
    model.sys_config_parsed.emit({
        "tx_antenna_delay": 1234,
        "rx_antenna_delay": 5678,
    })

    # Should progress to 60% and send pos config
    assert len(statuses) == 2
    assert statuses[-1]["progress_percent"] == 60
    assert statuses[-1]["custom_status_text"] == "Applying Position parameters (2/2)..."
    assert vm._apply_state == "sending_pos"
    assert len(model.sent_pos_configs) == 1
    assert model.sent_pos_configs[0] == pos_config

    # Simulate device responding with pos config parsed
    model.pos_calib_cfg_parsed.emit(pos_config)

    # Should progress to 100% and go back to idle
    assert len(statuses) == 3
    assert statuses[-1]["progress_percent"] == 100
    assert statuses[-1]["custom_status_text"] == "Calibration applied successfully!"
    assert vm._apply_state == "idle"


def test_calibration_apply_sequence_wrong_sys_ignored():
    model = MockDeviceModel()
    vm = CalibrationViewModel(model)

    statuses = []
    vm.status_updated.connect(lambda s: statuses.append(s))

    # Initiating sequence
    pos_config = {"samples": 15}
    vm.apply_results_sequence(tx_delay=1234, rx_delay=5678, pos_config=pos_config)

    assert statuses[-1]["progress_percent"] == 10

    # Simulate device responding with DIFFERENT sys config (should be ignored)
    model.sys_config_parsed.emit({
        "tx_antenna_delay": 1111,
        "rx_antenna_delay": 5678,
    })

    # State and progress shouldn't change
    assert len(statuses) == 1
    assert vm._apply_state == "sending_sys"


def test_calibration_apply_sequence_timeout():
    model = MockDeviceModel()
    vm = CalibrationViewModel(model)

    statuses = []
    vm.status_updated.connect(lambda s: statuses.append(s))

    failures = []
    vm.operation_failed.connect(lambda f: failures.append(f))

    # Initiating sequence
    pos_config = {"samples": 15}
    vm.apply_results_sequence(tx_delay=1234, rx_delay=5678, pos_config=pos_config)

    assert vm._apply_state == "sending_sys"

    # Trigger watchdog timeout manually
    vm._on_apply_timeout()

    assert vm._apply_state == "idle"
    assert len(failures) == 1
    assert "Timeout" in failures[0]
    assert statuses[-1]["progress_percent"] == 0
    assert "Apply failed: Timeout" in statuses[-1]["custom_status_text"]


def test_calibration_is_applying():
    model = MockDeviceModel()
    vm = CalibrationViewModel(model)

    assert not vm.is_applying

    pos_config = {"samples": 15}
    vm.apply_results_sequence(tx_delay=1234, rx_delay=5678, pos_config=pos_config)

    assert vm.is_applying

    model.sys_config_parsed.emit({
        "tx_antenna_delay": 1234,
        "rx_antenna_delay": 5678,
    })
    assert vm.is_applying

    model.pos_calib_cfg_parsed.emit(pos_config)
    assert not vm.is_applying
