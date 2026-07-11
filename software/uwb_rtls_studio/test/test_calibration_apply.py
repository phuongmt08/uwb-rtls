import os
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
PROJECT = os.path.normpath(os.path.join(ROOT, ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

import pytest
from PyQt6.QtCore import QCoreApplication

_app = QCoreApplication.instance() or QCoreApplication([])

import utils.app_state
try:
    utils.app_state.shared_app_state.objectName()
except RuntimeError:
    utils.app_state.shared_app_state = utils.app_state.SharedAppState()

from PyQt6.QtCore import QObject, pyqtSignal
from common.commands import CommandFactory
from common.parser_protocol import VvProtocol
from viewmodels.calibration_viewmodel import CalibrationViewModel


class MockPacket:
    class Hdr:
        seq = 1

    hdr = Hdr()


class MockDeviceModel(QObject):
    sys_config_parsed = pyqtSignal(dict)
    pos_calib_cfg_parsed = pyqtSignal(dict)

    def __init__(self, role="TAG"):
        super().__init__()
        self.connected_role = role
        self.started = []
        self.stopped = 0
        self.applied_masks = []
        self.sent_pos_configs = []
        self.status_requests = 0
        self.sys_config_requests = 0

    def request_calibration_start(self, **kwargs):
        self.started.append(kwargs)
        return MockPacket()

    def request_calibration_stop(self):
        self.stopped += 1
        return MockPacket()

    def request_calibration_candidate_apply(self, anchor_mask: int):
        self.applied_masks.append(anchor_mask)
        return MockPacket()

    def set_pos_calib_config(self, **kwargs):
        self.sent_pos_configs.append(kwargs)
        return MockPacket()

    def request_pos_calib_config(self, force=False):
        pass

    def request_calibration_status(self):
        self.status_requests += 1

    def request_sys_config(self, force=False):
        self.sys_config_requests += 1



def test_start_calibration_uses_calib_start():
    model = MockDeviceModel(role="TAG")
    vm = CalibrationViewModel(model)

    statuses = []
    vm.status_updated.connect(lambda s: statuses.append(s))

    ok = vm.start_calibration({"samples": 1000, "ref_distance_xy_m": 3.5, "tag_height_m": 1.25})

    assert ok is True
    assert len(model.started) == 1
    assert model.started[0] == {
        "sample_target": 64,
        "tag_x_m": 3.5,
        "tag_y_m": 0.0,
        "tag_z_m": 1.25,
    }
    assert statuses[-1]["custom_status_text"] == "TAG antenna calibration started."
    assert statuses[-1]["sample_target"] == 64



def test_start_calibration_rejects_anchor_role():
    model = MockDeviceModel(role="ANCHOR")
    vm = CalibrationViewModel(model)

    failures = []
    vm.operation_failed.connect(lambda msg: failures.append(msg))

    ok = vm.start_calibration({"samples": 10})

    assert ok is False
    assert model.started == []
    assert "TAG firmware" in failures[-1]



def test_stop_calibration_sends_calib_stop_for_tag():
    model = MockDeviceModel(role="TAG")
    vm = CalibrationViewModel(model)

    vm.stop_calibration()

    assert model.stopped == 1



def test_save_position_config_only_sends_pos_calib_cfg_set():
    model = MockDeviceModel(role="ANCHOR")
    vm = CalibrationViewModel(model)

    statuses = []
    vm.status_updated.connect(lambda s: statuses.append(s))
    config = {"samples": 15, "iterations": 50}

    ok = vm.save_position_calibration_config(config)

    assert ok is True
    assert model.sent_pos_configs == [config]
    assert model.started == []
    assert statuses[-1]["custom_status_text"] == "Position calibration config sent."



def test_candidate_apply_uses_candidate_mask():
    model = MockDeviceModel(role="TAG")
    vm = CalibrationViewModel(model)
    vm._latest_status = {"state": 4, "candidate_mask": 0x05}

    statuses = []
    vm.status_updated.connect(lambda s: statuses.append(s))

    ok = vm.apply_candidate_results()

    assert ok is True
    assert model.applied_masks == [0x05]
    assert vm.is_applying
    assert statuses[-1]["custom_status_text"] == "Applying candidate mask 0x05..."



def test_candidate_apply_requires_done_state_and_mask():
    model = MockDeviceModel(role="TAG")
    vm = CalibrationViewModel(model)
    failures = []
    vm.operation_failed.connect(lambda msg: failures.append(msg))

    vm._latest_status = {"state": 2, "candidate_mask": 0x01}
    assert vm.apply_candidate_results() is False
    assert "not done" in failures[-1]

    vm._latest_status = {"state": 4, "candidate_mask": 0}
    assert vm.apply_candidate_results() is False
    assert "candidate mask" in failures[-1]
    assert model.applied_masks == []



def test_calib_command_builders_have_firmware_fields():
    factory = CommandFactory()
    pkt = factory.calib_start(1, 2, 3, sample_target=12, tag_x_m=1.1, tag_y_m=2.2, tag_z_m=3.3)
    assert pkt.WhichOneof("params") == "calib_start"
    assert pkt.calib_start.sample_target == 12
    assert pkt.calib_start.tag_x_m == pytest.approx(1.1)
    assert pkt.calib_start.tag_y_m == pytest.approx(2.2)
    assert pkt.calib_start.tag_z_m == pytest.approx(3.3)
    assert pkt.calib_start.reference_position_valid is True

    pkt = factory.calib_candidate_apply(1, 2, 4, anchor_mask=0x21)
    assert pkt.WhichOneof("params") == "calib_candidate_apply"
    assert pkt.calib_candidate_apply.anchor_mask == 0x21

    proto = VvProtocol()
    pkt = proto.build_calib_start(1, 2, 5, sample_target=7, tag_x_m=4.0, tag_y_m=5.0, tag_z_m=6.0)
    assert pkt.calib_start.sample_target == 7
    pkt = proto.build_calib_candidate_apply(1, 2, 6, anchor_mask=0x03)
    assert pkt.calib_candidate_apply.anchor_mask == 0x03
