from __future__ import annotations

import os
import sys
import math

from PyQt6.QtCore import QCoreApplication


CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
STUDIO_DIR = os.path.dirname(CURRENT_DIR)
SOFTWARE_DIR = os.path.dirname(STUDIO_DIR)

if STUDIO_DIR not in sys.path:
    sys.path.insert(0, STUDIO_DIR)
if SOFTWARE_DIR not in sys.path:
    sys.path.insert(0, SOFTWARE_DIR)

from common import protocol_pb2 as pb
from common.commands import CommandFactory
from data.raw_packet import RawPacket
from data.raw_packet_store import RawPacketStore
from models.ranging_model import RangingModel
from repository.ranging_repository import RangingRepository


def _ensure_qt_app():
    return QCoreApplication.instance() or QCoreApplication([])


def _fixed2(value: float) -> int:
    return int(round(value * 100.0))


def test_sensor_fusion_result_reaches_ranging_model_with_velocity():
    app = _ensure_qt_app()
    repo = RangingRepository()
    model = RangingModel(protocol_service=None, ranging_repo=repo)
    received = []
    model.sensor_fusion_updated.connect(received.append)

    factory = CommandFactory()

    pkt1 = factory.sensor_fusion_result(pb.PACKET_ADDR_MCU, pb.PACKET_ADDR_HOST, 1)
    pkt1.sensor_fusion_result.ukf_x_m = _fixed2(1.0)
    pkt1.sensor_fusion_result.ukf_y_m = _fixed2(2.0)
    pkt1.sensor_fusion_result.ukf_yaw_deg = _fixed2(10.0)
    pkt1.sensor_fusion_result.tril_x_m = _fixed2(0.9)
    pkt1.sensor_fusion_result.tril_y_m = _fixed2(2.1)
    pkt1.sensor_fusion_result.yaw_deg = _fixed2(9.0)
    pkt1.sensor_fusion_result.ranging_error_count = 3
    pkt1.sensor_fusion_result.timestamp_ms = 1000

    pkt2 = factory.sensor_fusion_result(pb.PACKET_ADDR_MCU, pb.PACKET_ADDR_HOST, 2)
    pkt2.sensor_fusion_result.ukf_x_m = _fixed2(2.5)
    pkt2.sensor_fusion_result.ukf_y_m = _fixed2(4.0)
    pkt2.sensor_fusion_result.ukf_yaw_deg = _fixed2(12.0)
    pkt2.sensor_fusion_result.tril_x_m = _fixed2(2.4)
    pkt2.sensor_fusion_result.tril_y_m = _fixed2(4.1)
    pkt2.sensor_fusion_result.yaw_deg = _fixed2(11.0)
    pkt2.sensor_fusion_result.ranging_error_count = 4
    pkt2.sensor_fusion_result.timestamp_ms = 2000

    assert repo.handle_packet("sensor_fusion_result", pkt1) is True
    assert repo.handle_packet("sensor_fusion_result", pkt2) is True

    assert len(received) == 2
    latest = received[-1]
    assert math.isclose(latest["ukf_x_m"], 2.5)
    assert math.isclose(latest["ukf_y_m"], 4.0)
    assert math.isclose(latest["tril_x_m"], 2.4, rel_tol=1e-6)
    assert math.isclose(latest["tril_y_m"], 4.1, rel_tol=1e-6)
    assert latest["ranging_error_count"] == 4
    assert math.isclose(latest["vx_mps"], 1.5)
    assert math.isclose(latest["vy_mps"], 2.0)
    assert latest["seq"] == 2
    assert len(model.fusion_history) == 2
    assert model.fusion_history[-1]["source"] == "sensor_fusion"
    assert app is not None


def test_ranging_result_keeps_seq_and_anchor_distances_for_session_export():
    app = _ensure_qt_app()
    repo = RangingRepository()
    model = RangingModel(protocol_service=None, ranging_repo=repo)

    factory = CommandFactory()
    pkt = factory.ranging_result(pb.PACKET_ADDR_MCU, pb.PACKET_ADDR_HOST, 42)
    pkt.ranging_result.pos_x_m = 1.2
    pkt.ranging_result.pos_y_m = 3.4
    pkt.ranging_result.pos_z_m = 0.5
    pkt.ranging_result.rms_error_m = 0.03
    pkt.ranging_result.ClearField("anchors")

    for anchor_id, distance_mm in [(1, 1100), (2, 2200), (3, 3300), (4, 4400)]:
        anchor = pkt.ranging_result.anchors.add()
        anchor.anchor_id = anchor_id
        anchor.distance_mm = distance_mm
        anchor.fp_amp = 500 + anchor_id

    assert repo.handle_packet("ranging_result", pkt) is True

    assert len(model.position_history) == 1
    sample = model.position_history[0]
    assert sample["source"] == "ranging"
    assert sample["seq"] == 42
    assert sample["d1_mm"] == 1100
    assert sample["d2_mm"] == 2200
    assert sample["d3_mm"] == 3300
    assert sample["d4_mm"] == 4400
    assert sample["anchor_mask"] == 0b11110
    assert math.isclose(sample["x_m"], 1.2, rel_tol=1e-6)
    assert app is not None


def test_raw_packet_store_tracks_sequence_gaps_without_dropping_packets():
    store = RawPacketStore(max_packets=10)
    store.clear()

    packet_1 = RawPacket(
        param_name="ranging_result",
        payload=b"one",
        src_addr=pb.PACKET_ADDR_MCU,
        dst_addr=pb.PACKET_ADDR_HOST,
        seq=1,
        received_at=1.0,
    )
    packet_3 = RawPacket(
        param_name="ranging_result",
        payload=b"three",
        src_addr=pb.PACKET_ADDR_MCU,
        dst_addr=pb.PACKET_ADDR_HOST,
        seq=3,
        received_at=2.0,
    )

    store.append(packet_1)
    store.append(packet_3)

    stats = store.stats()
    assert len(store.recent_packets()) == 2
    assert stats["packet_gap_count"] == 1
    assert stats["last_packet_gap"]["previous_seq"] == 1
    assert stats["last_packet_gap"]["current_seq"] == 3


def test_ranging_status_response_parses_success_rate():
    repo = RangingRepository()
    factory = CommandFactory()
    pkt = factory.ranging_status_resp(pb.PACKET_ADDR_MCU, pb.PACKET_ADDR_HOST, 1)
    pkt.ranging_status_resp.ranging_period_ms = 200
    pkt.ranging_status_resp.ranging_total_count = 100
    pkt.ranging_status_resp.ranging_success_count = 95
    pkt.ranging_status_resp.ranging_failed_count = 3
    pkt.ranging_status_resp.ranging_timeout_count = 2
    pkt.ranging_status_resp.last_ranging_time_ms = 11
    pkt.ranging_status_resp.last_rms_error_m = 0.05
    pkt.ranging_status_resp.last_avg_rssi_dbm = -65

    stats = repo.parse_ranging_status(pkt.ranging_status_resp)

    assert stats["ranging_period_ms"] == 200
    assert stats["total_count"] == 100
    assert stats["success_count"] == 95
    assert stats["failed_count"] == 3
    assert stats["timeout_count"] == 2
    assert math.isclose(stats["success_rate_percent"], 95.0)
