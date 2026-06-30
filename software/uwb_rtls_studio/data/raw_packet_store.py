"""
Bounded in-memory store for recent raw packets.

This is a debug aid, not the application source of truth. Repositories and
models publish parsed domain data to SharedAppState for UI synchronization.
"""
from __future__ import annotations

import base64
import json
import queue
from collections import deque
from pathlib import Path
from threading import Lock, Thread

from data.raw_packet import RawPacket, RawSerialChunk


class RawPacketStore:
    def __init__(self, max_packets: int = 1000):
        self._packets = deque(maxlen=max_packets)
        self._serial_chunks = deque(maxlen=max_packets)
        self._lock = Lock()
        self._last_seq_by_route: dict[tuple[int, int], int] = {}
        self._packet_gap_count = 0
        self._last_packet_gap: dict | None = None
        self._runtime_dir = Path(__file__).resolve().parent / "runtime"
        self._serial_file = self._runtime_dir / "raw_serial_chunks.jsonl"
        self._packet_file = self._runtime_dir / "raw_packets.jsonl"
        self._parsed_file = self._runtime_dir / "parsed_packets.jsonl"
        self._capture_generation = 0
        self._disk_queue: queue.Queue[tuple] = queue.Queue()
        self._prepare_runtime_capture()
        self._writer_thread = Thread(
            target=self._disk_write_loop,
            name="RawPacketStoreWriter",
            daemon=True,
        )
        self._writer_thread.start()

    def append(self, packet: RawPacket) -> None:
        with self._lock:
            gap = self._detect_packet_gap(packet)
            self._packets.append(packet)
            generation = self._capture_generation
        self._disk_queue.put((generation, "packet", packet, gap))

    def append_proto_async(self, param_name: str, pkt) -> None:
        with self._lock:
            generation = self._capture_generation
        self._disk_queue.put((generation, "proto", param_name, pkt))

    def append_serial_chunk(self, chunk: RawSerialChunk) -> None:
        with self._lock:
            self._serial_chunks.append(chunk)
            generation = self._capture_generation
        self._disk_queue.put((generation, "serial", chunk))

    def recent(self) -> list[RawPacket]:
        with self._lock:
            return list(self._packets)

    def recent_packets(self) -> list[RawPacket]:
        return self.recent()

    def recent_serial_chunks(self) -> list[RawSerialChunk]:
        with self._lock:
            return list(self._serial_chunks)

    def clear(self) -> None:
        with self._lock:
            self._packets.clear()
            self._serial_chunks.clear()
            self._last_seq_by_route.clear()
            self._packet_gap_count = 0
            self._last_packet_gap = None
            self._capture_generation += 1
            self._prepare_runtime_capture()

    def stats(self) -> dict:
        with self._lock:
            return {
                "packet_count": len(self._packets),
                "serial_chunk_count": len(self._serial_chunks),
                "runtime_dir": str(self._runtime_dir),
                "serial_capture_file": str(self._serial_file),
                "packet_capture_file": str(self._packet_file),
                "packet_gap_count": self._packet_gap_count,
                "last_packet_gap": self._last_packet_gap.copy() if self._last_packet_gap else None,
            }

    def _detect_packet_gap(self, packet: RawPacket) -> dict | None:
        route = (packet.src_addr, packet.dst_addr)
        seq = int(packet.seq or 0)
        previous = self._last_seq_by_route.get(route)
        self._last_seq_by_route[route] = seq
        if previous is None or previous == 0 or seq == 0:
            return None

        expected = (previous + 1) & 0xFFFFFFFF
        if seq == expected:
            return None

        gap = {
            "src_addr": packet.src_addr,
            "dst_addr": packet.dst_addr,
            "previous_seq": previous,
            "current_seq": seq,
            "expected_seq": expected,
            "param_name": packet.param_name,
            "received_at": packet.received_at,
        }
        self._packet_gap_count += 1
        self._last_packet_gap = gap
        return gap

    def _prepare_runtime_capture(self) -> None:
        self._runtime_dir.mkdir(parents=True, exist_ok=True)
        self._serial_file.write_text("", encoding="utf-8")
        self._packet_file.write_text("", encoding="utf-8")
        self._parsed_file.write_text("", encoding="utf-8")

    def _disk_write_loop(self) -> None:
        while True:
            item = self._disk_queue.get()
            if item is None:
                break
            if not item:
                continue

            generation = item[0]
            kind = item[1]
            with self._lock:
                if generation != self._capture_generation:
                    continue

            if kind == "serial":
                self._append_serial_chunk_to_disk(item[2])
                continue

            if kind == "packet":
                self._append_packet_to_disk(item[2], gap=item[3])
                continue

            if kind == "proto":
                packet = RawPacket.from_proto(item[2], item[3])
                with self._lock:
                    if generation != self._capture_generation:
                        continue
                    gap = self._detect_packet_gap(packet)
                    self._packets.append(packet)
                self._append_packet_to_disk(packet, gap=gap)

    def close(self) -> None:
        self._disk_queue.put(None)
        if self._writer_thread.is_alive():
            self._writer_thread.join(timeout=1.0)

    def _append_serial_chunk_to_disk(self, chunk: RawSerialChunk) -> None:
        record = {
            "received_at": chunk.received_at,
            "size": len(chunk.payload),
            "payload_hex": chunk.payload.hex(),
            "payload_base64": base64.b64encode(chunk.payload).decode("ascii"),
        }
        self._append_jsonl(self._serial_file, record)

    def _append_packet_to_disk(self, packet: RawPacket, gap: dict | None = None) -> None:
        record = {
            "received_at": packet.received_at,
            "param_name": packet.param_name,
            "src_addr": packet.src_addr,
            "dst_addr": packet.dst_addr,
            "seq": packet.seq,
            "payload_size": len(packet.payload),
            "payload_hex": packet.payload.hex(),
            "payload_base64": base64.b64encode(packet.payload).decode("ascii"),
        }
        if gap:
            record["seq_gap"] = gap
        self._append_jsonl(self._packet_file, record)

        parsed_record = {
            "received_at": packet.received_at,
            "param_name": packet.param_name,
            "src_addr": packet.src_addr,
            "dst_addr": packet.dst_addr,
            "seq": packet.seq,
            "parsed_data": packet.parsed_dict if packet.parsed_dict is not None else {},
        }
        self._append_jsonl(self._parsed_file, parsed_record)

    def _append_jsonl(self, path: Path, record: dict) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True))
            handle.write("\n")


shared_raw_packet_store = RawPacketStore()
