from __future__ import annotations

import os
import sys
from types import SimpleNamespace

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
STUDIO_DIR = os.path.dirname(CURRENT_DIR)
SOFTWARE_DIR = os.path.dirname(STUDIO_DIR)

if STUDIO_DIR not in sys.path:
    sys.path.insert(0, STUDIO_DIR)
if SOFTWARE_DIR not in sys.path:
    sys.path.insert(0, SOFTWARE_DIR)

from repository.ble_scan_repository import BleScanRepository
from data.raw_packet import RawPacket
from common import protocol_pb2 as pb


def test_repository_merges_adv_status_by_serial_number():
    repo = BleScanRepository()
    repo.save_scan_result({
        "name": "Tag-4",
        "mac": "AA:BB:CC:DD:EE:04",
        "rssi": -55,
        "serial_number": 10004,
        "serial": "0x00002714",
        "last_seen": 1.0,
    })

    repo.save_adv_status({
        "device_type": 1,
        "device_id": 4,
        "serial_number": 10004,
        "serial": "0x00002714",
        "bat_soc_percent": 91,
        "local_timestamp_s": 1720484170,
        "local_timestamp_ms": 1720484170000,
        "status_flags": 0,
        "warning_count": 0,
        "error_count": 0,
        "last_seen": 2.0,
    })

    device = repo.merged_results()[0]
    assert device["serial_number"] == 10004
    assert device["bat_soc_percent"] == 91
    assert device["warning_count"] == 0
    assert device["error_count"] == 0
    assert device["local_timestamp_ms"] == 1720484170000


def test_repository_merges_adv_status_by_name_suffix_device_id():
    repo = BleScanRepository()
    repo.save_scan_result({
        "name": "Anchor-1",
        "mac": "AA:BB:CC:DD:EE:01",
        "rssi": -61,
        "serial_number": 0,
        "serial": "",
        "last_seen": 1.0,
    })

    repo.save_adv_status({
        "device_type": 2,
        "device_id": 1,
        "serial_number": 0,
        "serial": "",
        "bat_soc_percent": 33,
        "local_timestamp_s": 1720484171,
        "local_timestamp_ms": 1720484171000,
        "status_flags": 0x12,
        "warning_count": 2,
        "error_count": 1,
        "last_seen": 2.0,
    })

    device = repo.merged_results()[0]
    assert device["device_id"] == 1
    assert device["bat_soc_percent"] == 33
    assert device["status_flags"] == 0x12
    assert device["warning_count"] == 2
    assert device["error_count"] == 1


def test_raw_packet_keeps_zero_value_proto_fields_in_debug_dump():
    pkt = pb.packet_t()
    pkt.hdr.addr.src = pb.PACKET_ADDR_CENTRAL
    pkt.hdr.addr.dst = pb.PACKET_ADDR_HOST
    pkt.hdr.seq = 7
    pkt.ble_adv_status.device = pb.DEVICE_TYPE_TAG
    pkt.ble_adv_status.device_id = 4
    pkt.ble_adv_status.bat_soc_percent = 0
    pkt.ble_adv_status.status_flags = 0
    pkt.ble_adv_status.warning_count = 0
    pkt.ble_adv_status.error_count = 0
    pkt.ble_adv_status.local_timestamp_s = 0
    pkt.ble_adv_status.serial_number = 0

    raw = RawPacket.from_proto("ble_adv_status", pkt, received_at=0.0)

    assert raw.parsed_dict is not None
    assert "status_flags" in raw.parsed_dict
    assert "warning_count" in raw.parsed_dict
    assert "error_count" in raw.parsed_dict
    assert "local_timestamp_s" in raw.parsed_dict
