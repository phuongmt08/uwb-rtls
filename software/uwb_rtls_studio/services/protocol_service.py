"""
===============================================================================
  UWB RTLS Studio — Protocol Service
===============================================================================
  File        : services/protocol_service.py
  Description : Encapsulates Protobuf packet serialization/deserialization wrapped in HDLC.
                Acts as the central dispatcher routing parsed packets to subscriber models.

  MVVM Role   : SERVICE — Protocol serialization and routing.
  Giải thích flow:
        RX (nhận):
        SerialService.data_received → on_serial_data()
            → HdlcCodec.feed() → decode protobuf → emit packet_received
        TX (gửi):
        send_command(packet_t) → HDLC wrap → SerialService.write()
  Thread Model:
    ┌────────────────────────────────────────────────────────────────────────┐
    │ 🧵 Thread Contexts:                                                     │
    │ 1. Main GUI Thread:                                                     │
    │    - `send_command()` and `send_packet()` run on this thread.          │
    │    - `on_serial_data()` is executed here: raw bytes emitted from the    │
    │      background thread are marshalled to the Main GUI Thread via Qt's  │
    │      thread-safe Event Loop (Queued Connection).                        │
    │    - Decoded packet signals are emitted on the Main GUI Thread.         │
    └────────────────────────────────────────────────────────────────────────┘

  Signals:
    - packet_received(str, object)  : (param_name, packet_t) dispatched to Models.
    - ack_received(int, int)        : (ack_seq, response_code) for transactions.
    - decode_error(str)             : Decapsulation or parsing failure alert.
===============================================================================
"""
from __future__ import annotations

import sys
import os
import logging
import queue
import threading

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from google.protobuf.message import DecodeError

# Add common to path
_common_dir = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "common")
)
if _common_dir not in sys.path:
    sys.path.insert(0, os.path.dirname(_common_dir))

from common.parser_protocol import VvProtocol
from common.transport import HdlcCodec, FRAME_TYPE_PROTOBUF, VvAddress
from common.commands import CommandFactory, default_destination_for
from common import protocol_pb2 as pb
from data.raw_packet import RawSerialChunk
from data.raw_packet_store import shared_raw_packet_store

log = logging.getLogger(__name__)


class ProtocolService(QObject):
    """Encode/decode protobuf packets, dispatch tới subscribers."""

    # ── Signals ──────────────────────────────────────────────────────
    packet_received = pyqtSignal(str, object)   # (param_name, packet_t)
    packet_sent = pyqtSignal(str, object)       # (param_name, packet_t)
    ack_received = pyqtSignal(int, int)         # (ack_seq, response_code)
    decode_error = pyqtSignal(str)              # error message
    _decoded_packets_ready = pyqtSignal(list)
    _decode_error_ready = pyqtSignal(str)

    def __init__(self, serial_service, parent=None):
        super().__init__(parent)
        self._serial = serial_service
        self._protocol = VvProtocol()   # parser_protocol.VvProtocol — encode/decode/HDLC + build_*() methods
        self._seq = 0
        self._seq_lock = threading.Lock()
        self._packet_repository = None
        self._last_log_data_seq: int | None = None
        self._rx_queue: queue.Queue[bytes | None] = queue.Queue()
        self._rx_stop = threading.Event()
        self._rx_thread = threading.Thread(
            target=self._rx_worker_loop,
            name="ProtocolRxWorker",
            daemon=True,
        )
        self._decoded_packets_ready.connect(
            self._dispatch_decoded_packets,
            Qt.ConnectionType.QueuedConnection,
        )
        self._decode_error_ready.connect(
            self._emit_decode_error,
            Qt.ConnectionType.QueuedConnection,
        )

        # Connect serial RX → decode
        self._serial.data_received.connect(self.on_serial_data)
        self._rx_thread.start()
        try:
            from utils.app_state import shared_app_state
            shared_app_state.threads.register("ProtocolRxWorker", self._rx_thread)
        except Exception as exc:
            log.debug("Could not register ProtocolRxWorker thread: %s", exc)

    # ── Properties ───────────────────────────────────────────────────

    @property
    def pb(self):
        """Expose protobuf module cho external use."""
        return pb

    @property
    def commands(self) -> CommandFactory:
        """Expose CommandFactory cho external use (nằm bên trong VvProtocol)."""
        return self._protocol._commands

    def set_packet_repository(self, repository) -> None:
        """Attach decoded-packet repository for raw/debug and shared parsers."""
        self._packet_repository = repository

    # ── RX Path ──────────────────────────────────────────────────────

    def on_serial_data(self, data: bytes) -> None:
        """Queue raw serial bytes for the protocol worker."""
        if not data:
            return

        self._rx_queue.put(data)

    def _rx_worker_loop(self) -> None:
        """Decode HDLC/protobuf frames away from the GUI thread."""
        while not self._rx_stop.is_set():
            try:
                data = self._rx_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if data is None:
                break

            shared_raw_packet_store.append_serial_chunk(RawSerialChunk.from_bytes(data))
            packets = self._decode_packets(data)
            if packets:
                self._decoded_packets_ready.emit(packets)

    def _decode_packets(self, data: bytes) -> list:
        packets = []
        try:
            for chunk in self._protocol.hdlc.feed(data):
                if chunk.frame_type != FRAME_TYPE_PROTOBUF:
                    continue

                try:
                    packets.append(self._protocol.decode_packet(chunk.payload))
                except DecodeError as exc:
                    msg = f"Protobuf decode error: payload_len={len(chunk.payload)} err={exc}"
                    log.warning(msg)
                    self._decode_error_ready.emit(msg)
        except Exception as exc:
            self._decode_error_ready.emit(f"HDLC decode error: {exc}")
        return packets

    def _emit_decode_error(self, message: str) -> None:
        self.decode_error.emit(message)

    def _dispatch_decoded_packets(self, packets: list) -> None:
        """Dispatch decoded packets on the Qt/main thread."""
        for pkt in packets:
            param = pkt.WhichOneof("params")
            if param is None:
                continue

            if param == "log_data":
                self._warn_on_log_seq_gap(int(pkt.hdr.seq))

            if param == "ack":
                self.ack_received.emit(pkt.ack.ack_seq, pkt.ack.response)
                self.packet_received.emit(param, pkt)
                log.debug("RX: %s seq=%d ack_seq=%d", param, pkt.hdr.seq, pkt.ack.ack_seq)
                continue

            if self._packet_repository:
                try:
                    self._packet_repository.handle_packet(param, pkt)
                except Exception as exc:
                    log.error("Failed to forward packet to packet repository: %s", exc)

            try:
                from utils.app_state import shared_app_state
                shared_app_state.handle_incoming_packet(param, pkt)
            except Exception as exc:
                log.error("Failed to forward packet to shared_app_state: %s", exc)

            self.packet_received.emit(param, pkt)
            log.debug("RX: %s seq=%d", param, pkt.hdr.seq)

    def _warn_on_log_seq_gap(self, seq: int) -> None:
        if self._last_log_data_seq is not None:
            expected = (self._last_log_data_seq + 1) & 0xFFFFFFFF
            if seq != expected:
                msg = f"[MISS] log_data seq jump: expected={expected} got={seq}"
                print(msg, flush=True)
                log.warning(msg)
        self._last_log_data_seq = seq

    # ── TX Path ──────────────────────────────────────────────────────

    def close(self) -> None:
        """Stop the protocol RX worker during application shutdown."""
        self._rx_stop.set()
        self._rx_queue.put(None)
        if self._rx_thread.is_alive():
            self._rx_thread.join(timeout=1.0)
        try:
            from utils.app_state import shared_app_state
            shared_app_state.threads.unregister("ProtocolRxWorker")
        except Exception as exc:
            log.debug("Could not unregister ProtocolRxWorker thread: %s", exc)

    def next_seq(self) -> int:
        """Thread-safe sequence number generator."""
        with self._seq_lock:
            self._seq = (self._seq + 1) & 0xFFFFFFFF
            return self._seq

    def send_packet(self, pkt: pb.packet_t) -> None:
        """Encode packet → HDLC frame → serial write."""
        frame = self._protocol.wrap_packet(pkt)
        self._serial.write(frame)
        param = pkt.WhichOneof("params") or "unknown"
        log.debug("TX: %s seq=%d", param, pkt.hdr.seq)
        self.packet_sent.emit(param, pkt)

    def send_command(self, builder_name: str, dst_addr: int | None = None, src_addr: int = VvAddress.HOST, **kwargs) -> pb.packet_t:
        """Build + send command bằng tên.

        Args:
            builder_name: tên command (e.g. 'ble_scan_start') —
                          method tương ứng là build_<name> trên VvProtocol (parser_protocol).
            dst_addr: Địa chỉ đích. Nếu None, tự suy ra theo command catalog.
            src_addr: Địa chỉ nguồn (mặc định ADDR_HOST)
            **kwargs: extra args cho builder

        Returns:
            packet_t đã gửi (để caller track seq nếu cần).
        """
        seq = self.next_seq()
        target_addr = default_destination_for(builder_name) if dst_addr is None else dst_addr
        builder = getattr(self._protocol, f"build_{builder_name}")
        pkt = builder(src_addr, target_addr, seq, **kwargs)
        self.send_packet(pkt)
        return pkt
