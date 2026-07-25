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
from viewmodels.antenna_delay_calibration_viewmodel import AntennaDelayCalibrationViewModel


class MockPacket:
    class Hdr:
        def __init__(self, seq):
            self.seq = seq

    def __init__(self, seq=1):
        self.hdr = self.Hdr(seq)


class MockDeviceModel(QObject):
    scan_data_updated = pyqtSignal(list)
    bcast_apply_ack_received = pyqtSignal(dict)

    def __init__(self, role="TAG"):
        super().__init__()
        self.connected_role = role
        self.bcast_sets = []
        self.discovered_anchor_serials = {}
        self._next_seq = 1

    def request_antenna_delay_bcast_set(self, serial_number, tx_antenna_delay, rx_antenna_delay, persist=False):
        seq = self._next_seq
        self._next_seq += 1
        request = {
            "serial_number": serial_number,
            "tx": tx_antenna_delay,
            "rx": rx_antenna_delay,
            "persist": persist,
            "seq": seq,
        }
        self.bcast_sets.append(request)
        return MockPacket(seq)

    def discovered_anchor_serial_number(self, anchor_id):
        return self.discovered_anchor_serials.get(int(anchor_id), 0)

    def confirm_bcast(self, request_index=-1, success=True):
        request = self.bcast_sets[request_index]
        self.bcast_apply_ack_received.emit({
            "serial_number": request["serial_number"],
            "cmd_seq": request["seq"],
            "cmd_tag": 88,
            "success": success,
        })


class MockRangingModel(QObject):
    anchor_distances_updated = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self.is_ranging = False
        self.start_count = 0
        self.stop_count = 0
        self.anchor_layout = [
            {"anchor_id": 1, "x_m": 0.0, "y_m": 0.0, "z_m": 0.0},
            {"anchor_id": 2, "x_m": 10.0, "y_m": 0.0, "z_m": 0.0},
        ]

    def start_ranging(self):
        self.start_count += 1
        self.is_ranging = True

    def stop_ranging(self):
        self.stop_count += 1
        self.is_ranging = False


class MockGeofenceRepo:
    def __init__(self):
        self.anchors = [
            {
                "anchor_id": 1,
                "serial_number": 0xA1A1A1A1,
                "x_m": 0.0,
                "y_m": 0.0,
                "z_m": 0.0,
            },
            {
                "anchor_id": 2,
                "serial_number": 0xB2B2B2B2,
                "x_m": 10.0,
                "y_m": 0.0,
                "z_m": 0.0,
            },
        ]
        self.save_count = 0

    def get_anchors(self):
        return [dict(anchor) for anchor in self.anchors]

    def set_anchors(self, anchors):
        self.anchors = [dict(anchor) for anchor in anchors]

    def save(self):
        self.save_count += 1


def test_antenna_delay_parallel_start_all():
    dev_model = MockDeviceModel()
    rng_model = MockRangingModel()
    repo = MockGeofenceRepo()

    vm = AntennaDelayCalibrationViewModel(dev_model, rng_model, repo)
    vm.samples_per_round_target = 5

    ok = vm.start_all(tag_x_m=0.0, tag_y_m=3.0, tag_z_m=0.0)
    assert ok is True
    assert vm.is_running is True
    assert rng_model.start_count == 1

    # Simulate incoming TDMA ranging samples for both Anchors
    for _ in range(5):
        rng_model.anchor_distances_updated.emit([
            {"anchor_id": 1, "distance_mm": 3000},
            {"anchor_id": 2, "distance_mm": 10440},
        ])

    # Start All collects every Anchor in parallel, but serializes BCAST applies:
    # the second command is not sent until the first target ACK is received.
    assert len(dev_model.bcast_sets) == 1
    dev_model.confirm_bcast(0)
    assert len(dev_model.bcast_sets) == 2
    dev_model.confirm_bcast(1)

    assert any(b["serial_number"] == 0xA1A1A1A1 for b in dev_model.bcast_sets)
    assert any(b["serial_number"] == 0xB2B2B2B2 for b in dev_model.bcast_sets)
    assert rng_model.stop_count == 1


def test_antenna_delay_ground_truth_is_planar():
    dev_model = MockDeviceModel()
    rng_model = MockRangingModel()
    repo = MockGeofenceRepo()
    repo.anchors = [
        {"anchor_id": 1, "x_m": 3.0, "y_m": 4.0, "z_m": 12.0},
    ]
    vm = AntennaDelayCalibrationViewModel(dev_model, rng_model, repo)

    assert vm.known_distance_for(1, tag_x_m=0.0, tag_y_m=0.0, tag_z_m=99.0) == 5.0


def test_antenna_delay_reuses_active_ranging_session():
    dev_model = MockDeviceModel()
    rng_model = MockRangingModel()
    rng_model.is_ranging = True
    vm = AntennaDelayCalibrationViewModel(dev_model, rng_model, MockGeofenceRepo())

    assert vm.start(anchor_id=1, tag_x_m=0.0, tag_y_m=3.0) is True
    assert rng_model.start_count == 0

    vm.stop()

    assert rng_model.stop_count == 0


def test_antenna_delay_auto_maps_discovered_serial_by_anchor_id():
    dev_model = MockDeviceModel()
    dev_model.discovered_anchor_serials[1] = 0x01276864
    rng_model = MockRangingModel()
    repo = MockGeofenceRepo()
    vm = AntennaDelayCalibrationViewModel(dev_model, rng_model, repo)

    assert vm.serial_number_for(1) == 0x01276864
    assert repo.get_anchors()[0]["serial_number"] == 0x01276864
    assert repo.save_count == 1


def test_antenna_delay_requires_reference_tag_connection():
    dev_model = MockDeviceModel(role="ANCHOR")
    rng_model = MockRangingModel()
    vm = AntennaDelayCalibrationViewModel(dev_model, rng_model, MockGeofenceRepo())
    failures = []
    vm.operation_failed.connect(failures.append)

    assert vm.start_all(tag_x_m=0.0, tag_y_m=0.0) is False
    assert rng_model.start_count == 0
    assert failures and "reference Tag" in failures[-1]


def test_antenna_delay_selected_waits_for_matching_final_ack():
    dev_model = MockDeviceModel()
    rng_model = MockRangingModel()
    vm = AntennaDelayCalibrationViewModel(dev_model, rng_model, MockGeofenceRepo())
    vm.samples_per_round_target = 3
    completed = []
    vm.finished.connect(completed.append)

    assert vm.start(anchor_id=1, tag_x_m=0.0, tag_y_m=3.0) is True
    for _ in range(3):
        rng_model.anchor_distances_updated.emit([
            {"anchor_id": 1, "distance_mm": 3000},
        ])
    vm._process_round()

    assert len(dev_model.bcast_sets) == 1
    assert dev_model.bcast_sets[0]["persist"] is True
    assert completed == []
    assert rng_model.stop_count == 0

    request = dev_model.bcast_sets[0]
    dev_model.bcast_apply_ack_received.emit({
        "serial_number": request["serial_number"] + 1,
        "cmd_seq": request["seq"],
        "cmd_tag": 88,
        "success": True,
    })
    assert completed == []

    dev_model.confirm_bcast(0)

    assert len(completed) == 1
    assert completed[0]["converged"] is True
    assert rng_model.stop_count == 1
