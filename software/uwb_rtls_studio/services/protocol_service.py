"""
===============================================================================
  UWB RTLS Studio — Protocol Service
===============================================================================
  File        : services/protocol_service.py
  Description : Encode/decode protobuf packets qua HDLC framing.
                Wraps common/transport.py + common/commands.py.
                Central dispatcher: route decoded packets → subscribers.

  MVVM Role   : SERVICE — protocol layer.

  Giải thích flow:
    RX (nhận):
      SerialService.data_received → on_serial_data()
        → HdlcCodec.feed() → decode protobuf → emit packet_received
    TX (gửi):
      send_command(packet_t) → HDLC wrap → SerialService.write()

  Signals:
    - packet_received(str, object)  → (param_name, packet_t)
    - ack_received(int, int)        → (ack_seq, response_code)
    - decode_error(str)             → lỗi decode

  Dependencies: common.transport, common.commands
===============================================================================
"""
from __future__ import annotations

import sys
import os
import logging

from PyQt6.QtCore import QObject, pyqtSignal

# Add common to path
_common_dir = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "common")
)
if _common_dir not in sys.path:
    sys.path.insert(0, os.path.dirname(_common_dir))

from common.transport import VvProtocol, HdlcCodec, FRAME_TYPE_PROTOBUF, VvAddress
from common.commands import CommandFactory
from common import protocol_pb2 as pb

log = logging.getLogger(__name__)


class ProtocolService(QObject):
    """Encode/decode protobuf packets, dispatch tới subscribers."""

    # ── Signals ──────────────────────────────────────────────────────
    packet_received = pyqtSignal(str, object)   # (param_name, packet_t)
    ack_received = pyqtSignal(int, int)         # (ack_seq, response_code)
    decode_error = pyqtSignal(str)              # error message

    def __init__(self, serial_service, parent=None):
        super().__init__(parent)
        self._serial = serial_service
        self._protocol = VvProtocol()
        self._commands = CommandFactory()
        self._seq = 0

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

    # ── RX Path ──────────────────────────────────────────────────────

    def on_serial_data(self, data: bytes) -> None:
        """Được gọi khi SerialService nhận raw bytes.
        Decode HDLC → protobuf → dispatch.
        """
        try:
            packets = self._protocol.decode_from_frames(data)
        except Exception as e:
            self.decode_error.emit(f"HDLC decode error: {e}")
            return

        for pkt in packets:
            param = pkt.WhichOneof("params")
            if param is None:
                continue

            # Special handling cho ACK
            if param == "ack":
                self.ack_received.emit(pkt.ack.ack_seq, pkt.ack.response)
                continue

            self.packet_received.emit(param, pkt)
            log.debug("RX: %s seq=%d", param, pkt.hdr.seq)

    # ── TX Path ──────────────────────────────────────────────────────

    def next_seq(self) -> int:
        """Thread-safe sequence number generator."""
        self._seq = (self._seq + 1) & 0xFFFFFFFF
        return self._seq

    def send_packet(self, pkt: pb.packet_t) -> None:
        """Encode packet → HDLC frame → serial write."""
        frame = self._protocol.wrap_packet(pkt)
        self._serial.write(frame)
        param = pkt.WhichOneof("params") or "unknown"
        log.debug("TX: %s seq=%d", param, pkt.hdr.seq)

    def send_command(self, builder_name: str, dst_addr: int = VvAddress.CENTRAL, src_addr: int = VvAddress.HOST, **kwargs) -> pb.packet_t:
        """Build + send command bằng tên.

        Args:
            builder_name: tên method trong CommandFactory (e.g. 'ble_scan_start')
            dst_addr: Địa chỉ đích (mặc định ADDR_CENTRAL)
            src_addr: Địa chỉ nguồn (mặc định ADDR_HOST)
            **kwargs: extra args cho builder

        Returns:
            packet_t đã gửi (để caller track seq nếu cần).
        """
        seq = self.next_seq()
        builder = getattr(self._commands, builder_name)
        pkt = builder(src_addr, dst_addr, seq, **kwargs)
        self.send_packet(pkt)
        return pkt
