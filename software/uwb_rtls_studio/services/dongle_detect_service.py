"""
===============================================================================
  UWB RTLS Studio — Dongle Detect Service
===============================================================================
  File        : services/dongle_detect_service.py
  Description : Hardware-level auto-detection helper for NRF52840 dongles.
                Sends a probe handshake over serial to locate active ports.

  MVVM Role   : SERVICE — stateless hardware detection helper.

  Logic detect:
    1. Liệt kê tất cả COM ports hiện có
    2. Với mỗi port: mở serial → gửi device_information_get → chờ ACK
    3. Retry tối đa MAX_PROBE_RETRIES lần nếu không nhận ACK
    4. Port nào trả ACK → return DongleInfo
    5. Không match VID/PID, hoàn toàn dựa trên protobuf response (optional)

  Dependencies: pyserial, common.transport, common.commands
===============================================================================
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional, List

import serial
import serial.tools.list_ports
from serial.tools.list_ports_common import ListPortInfo

from utils.constants import (
    SERIAL_BAUD_RATE,
    PROBE_READ_TIMEOUT_S,
    PROBE_RESPONSE_TIMEOUT_S,
    MAX_PROBE_RETRIES,
)

import sys
import os

_common_dir = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "common")
)
if _common_dir not in sys.path:
    sys.path.insert(0, os.path.dirname(_common_dir))

from common.transport import VvProtocol, VvAddress
from common.commands import CommandFactory

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DongleInfo:
    """Thông tin dongle đã detect được qua protobuf handshake."""
    port: str               # e.g. "COM5"
    vid: int                # USB Vendor ID (0 nếu không có)
    pid: int                # USB Product ID (0 nếu không có)
    serial_number: str      # USB serial string
    description: str        # Mô tả thiết bị


class DongleDetectService:
    """Service: tìm NRF52840 dongle bằng protobuf probe trên tất cả COM ports.

    Thay vì dựa vào VID/PID (có thể sai), service gửi device_information_get
    packet tới mỗi COM port và chờ ACK. Cổng nào ACK → đó là dongle.
    """

    def __init__(self):
        self._proto = VvProtocol()
        self._commands = CommandFactory()

    def probe_port(self, port_name: str) -> Optional[DongleInfo]:
        """Mở port, gửi device_information_get, chờ ACK.
        Retry tối đa MAX_PROBE_RETRIES lần.
        Return DongleInfo nếu nhận được ACK, None nếu không.
        """
        for attempt in range(MAX_PROBE_RETRIES):
            try:
                result = self._single_probe(port_name, attempt)
                if result is not None:
                    return result
            except (serial.SerialException, OSError) as e:
                log.debug(
                    "Probe %s attempt %d failed: %s",
                    port_name, attempt + 1, e,
                )
                break  # Port lỗi → skip luôn, không retry
            except Exception as e:
                log.debug(
                    "Probe %s attempt %d unexpected error: %s",
                    port_name, attempt + 1, e,
                )
                break

        return None

    def _single_probe(self, port_name: str, attempt: int) -> Optional[DongleInfo]:
        """Thực hiện 1 lần probe trên port."""
        log.debug("Probing %s (attempt %d/%d)...", port_name, attempt + 1, MAX_PROBE_RETRIES)

        ser = serial.Serial(
            port=port_name,
            baudrate=SERIAL_BAUD_RATE,
            timeout=PROBE_READ_TIMEOUT_S,
        )
        try:
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            time.sleep(0.06)  # Cho port ổn định

            # Build device_information_get packet
            seq = self._proto.next_seq()
            pkt = self._commands.device_information_get(
                int(VvAddress.HOST),
                int(VvAddress.CENTRAL),
                seq,
            )

            # Send HDLC-wrapped packet
            frame = self._proto.wrap_packet(pkt)
            ser.write(frame)
            ser.flush()
            log.debug("TX device_information_get to %s (seq=%d)", port_name, seq)

            # Wait for response
            deadline = time.time() + PROBE_RESPONSE_TIMEOUT_S
            hdlc_decoder = type(self._proto.hdlc)()  # Fresh decoder per probe

            while time.time() < deadline:
                data = ser.read(ser.in_waiting or 1)
                if not data:
                    continue

                try:
                    chunks = hdlc_decoder.feed(data)
                except Exception:
                    continue

                for chunk in chunks:
                    if chunk.frame_type != 0:  # FRAME_TYPE_PROTOBUF
                        continue
                    try:
                        resp = self._proto.decode_packet(chunk.payload)
                        param = resp.WhichOneof("params")
                        log.debug(
                            "RX from %s: param=%s seq=%d",
                            port_name, param, resp.hdr.seq,
                        )

                        # Nhận ACK hoặc device_information_resp → đây là dongle
                        if param in ("ack", "device_information_resp"):
                            # Lấy thông tin USB nếu có
                            port_info = self._get_port_info(port_name)
                            log.info(
                                "✅ Dongle found on %s (received %s)",
                                port_name, param,
                            )
                            return DongleInfo(
                                port=port_name,
                                vid=port_info.vid or 0 if port_info else 0,
                                pid=port_info.pid or 0 if port_info else 0,
                                serial_number=(port_info.serial_number or "") if port_info else "",
                                description=(port_info.description or "") if port_info else "",
                            )
                    except Exception:
                        continue

            log.debug("No ACK from %s (attempt %d)", port_name, attempt + 1)
            return None

        finally:
            try:
                ser.close()
            except Exception:
                pass

    @staticmethod
    def _get_port_info(port_name: str) -> Optional[ListPortInfo]:
        """Lấy ListPortInfo cho port_name từ system."""
        for info in serial.tools.list_ports.comports():
            if info.device == port_name:
                return info
        return None

    @staticmethod
    def _score_port(port_info: ListPortInfo) -> int:
        """Ưu tiên port giống dongle (STM VCP, USB serial).
        Tham khảo: uwb_rtls_programmer/utils/dongle_session.py::_score_port()
        """
        score = 0
        desc = (port_info.description or "").lower()
        manu = (port_info.manufacturer or "").lower()
        hwid = (port_info.hwid or "").lower()

        if "stm" in desc or "stm" in manu:
            score += 4
        if "usb serial" in desc or "virtual com" in desc:
            score += 2
        if "vid:pid=0483" in hwid:
            score += 6
        if "bluetooth" in desc:
            score -= 4

        return score

    @staticmethod
    def list_all_ports() -> List[ListPortInfo]:
        """Liệt kê tất cả COM ports (dùng cho debug/log)."""
        return list(serial.tools.list_ports.comports())

    def find_dongle_port(self) -> Optional[DongleInfo]:
        """Scan và probe tất cả COM ports, return DongleInfo nếu tìm thấy.

        Ports được sắp xếp theo priority score (giống programmer).
        Mỗi port được probe với MAX_PROBE_RETRIES retry.
        """
        ports = list(serial.tools.list_ports.comports())
        # Sort theo priority (cao → thấp)
        ports.sort(key=self._score_port, reverse=True)

        log.info("Scanning %d COM port(s): %s",
                 len(ports),
                 [p.device for p in ports])

        for port_info in ports:
            result = self.probe_port(port_info.device)
            if result is not None:
                return result

        return None
