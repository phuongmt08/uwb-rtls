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
import threading

from PyQt6.QtCore import QObject, pyqtSignal
from google.protobuf.message import DecodeError

# Add common to path
_common_dir = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "common")
)
if _common_dir not in sys.path:
    sys.path.insert(0, os.path.dirname(_common_dir))

from common.transport import VvProtocol, HdlcCodec, FRAME_TYPE_PROTOBUF, VvAddress
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

    def __init__(self, serial_service, parent=None):
        super().__init__(parent)
        self._serial = serial_service
        self._protocol = VvProtocol()
        self._commands = CommandFactory()
        self._seq = 0
        self._seq_lock = threading.Lock()
        self._packet_repository = None
        self._last_log_data_seq: int | None = None

        # Connect serial RX → decode
        self._serial.data_received.connect(self.on_serial_data)

    # ── Properties ───────────────────────────────────────────────────

    @property
    def pb(self):
        """Expose protobuf module cho external use."""
        return pb

    @property
    def commands(self) -> CommandFactory:
        return self._commands

    def set_packet_repository(self, repository) -> None:
        """Attach decoded-packet repository for raw/debug and shared parsers."""
        self._packet_repository = repository

    # ── RX Path ──────────────────────────────────────────────────────

    def on_serial_data(self, data: bytes) -> None:
        """Được gọi khi SerialService nhận raw bytes.
        Decode HDLC → protobuf → dispatch.
        """
        if data:
            shared_raw_packet_store.append_serial_chunk(RawSerialChunk.from_bytes(data))

        try:
            packets = []
            for chunk in self._protocol.hdlc.feed(data):
                if chunk.frame_type != FRAME_TYPE_PROTOBUF:
                    continue

                try:
                    packets.append(self._protocol.decode_packet(chunk.payload))
                except DecodeError as exc:
                    msg = f"Protobuf decode error: payload_len={len(chunk.payload)} err={exc}"
                    log.warning(msg)
                    self.decode_error.emit(msg)
        except Exception as e:
            self.decode_error.emit(f"HDLC decode error: {e}")
            return

        for pkt in packets:
            param = pkt.WhichOneof("params")
            if param is None:
                continue

            if param == "log_data":
                self._warn_on_log_seq_gap(int(pkt.hdr.seq))

            # Special handling cho ACK
            if param == "ack":
                self.ack_received.emit(pkt.ack.ack_seq, pkt.ack.response)
                self.packet_received.emit(param, pkt)
                log.debug("RX: %s seq=%d ack_seq=%d", param, pkt.hdr.seq, pkt.ack.ack_seq)
                continue

            if self._packet_repository:
                try:
                    self._packet_repository.handle_packet(param, pkt)
                except Exception as e:
                    log.error("Failed to forward packet to packet repository: %s", e)

            # Route to global query manager in shared app state
            try:
                from utils.app_state import shared_app_state
                shared_app_state.handle_incoming_packet(param, pkt)
            except Exception as e:
                log.error("Failed to forward packet to shared_app_state: %s", e)

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
            builder_name: tên method trong CommandFactory (e.g. 'ble_scan_start')
            dst_addr: Địa chỉ đích. Nếu None, tự suy ra theo command catalog.
            src_addr: Địa chỉ nguồn (mặc định ADDR_HOST)
            **kwargs: extra args cho builder

        Returns:
            packet_t đã gửi (để caller track seq nếu cần).
        """
        seq = self.next_seq()
        target_addr = default_destination_for(builder_name) if dst_addr is None else dst_addr
        builder = getattr(self._commands, builder_name)
        pkt = builder(src_addr, target_addr, seq, **kwargs)
        self.send_packet(pkt)
        return pkt
