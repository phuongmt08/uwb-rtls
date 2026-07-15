from __future__ import annotations

import json
import os
import sys
import time


CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
STUDIO_DIR = os.path.dirname(CURRENT_DIR)

if STUDIO_DIR not in sys.path:
    sys.path.insert(0, STUDIO_DIR)

from data.raw_packet import RawPacket
from data.raw_packet_store import RawPacketStore


def _packet(name: str, seq: int) -> RawPacket:
    return RawPacket(
        param_name=name,
        payload=name.encode("ascii"),
        src_addr=3,
        dst_addr=5,
        seq=seq,
        received_at=time.time(),
        parsed_dict={"name": name},
    )


def test_clear_keeps_current_process_capture_files(tmp_path):
    store = RawPacketStore(max_packets=10, runtime_dir=tmp_path)
    store.append(_packet("before_clear", 1))
    store.clear()
    store.append(_packet("after_clear", 2))
    store.close()

    records = [
        json.loads(line)
        for line in (tmp_path / "parsed_packets.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [record["param_name"] for record in records] == ["before_clear", "after_clear"]


def test_explicit_truncate_drops_queued_previous_generation(tmp_path):
    store = RawPacketStore(max_packets=10, runtime_dir=tmp_path)
    store.append(_packet("old_generation", 1))
    store.clear(truncate_files=True)
    store.append(_packet("new_generation", 2))
    store.close()

    records = [
        json.loads(line)
        for line in (tmp_path / "parsed_packets.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [record["param_name"] for record in records] == ["new_generation"]