"""
===============================================================================
  UWB RTLS Studio — Dongle Detect Service
===============================================================================
  File        : services/dongle_detect_service.py
  Description : Auto-detect NRF52840 dongle trên USB COM ports.
                Dùng serial.tools.list_ports để enumerate, filter VID/PID.

  MVVM Role   : SERVICE — stateless hardware detection helper.

  API (3 methods, tất cả synchronous):
    find_dongle_port() → ListPortInfo | None
        Scan tất cả COM ports, return port đầu tiên match VID/PID.
    list_all_ports()   → list[ListPortInfo]
        Trả về danh sách tất cả COM ports (debug/log).
    is_dongle(port)    → bool
        Check 1 port có match VID/PID dongle không.

  Giải thích:
    - Service này KHÔNG giữ state, KHÔNG mở port.
    - Nó chỉ "tìm" dongle, việc mở/đóng port do SerialService xử lý.
    - Worker (DongleDetectWorker) sẽ gọi service này trong vòng lặp polling.

  Dependencies: pyserial (serial.tools.list_ports)
===============================================================================
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import serial.tools.list_ports
from serial.tools.list_ports_common import ListPortInfo

from utils.constants import DONGLE_VID, DONGLE_PID


@dataclass(frozen=True)
class DongleInfo:
    """Thông tin dongle đã detect được."""
    port: str               # e.g. "COM5"
    vid: int                # USB Vendor ID
    pid: int                # USB Product ID
    serial_number: str      # USB serial string
    description: str        # Mô tả thiết bị


class DongleDetectService:
    """Stateless service: tìm NRF52840 dongle trên USB COM ports."""

    @staticmethod
    def find_dongle_port() -> Optional[DongleInfo]:
        """Scan tất cả COM ports, return DongleInfo nếu tìm thấy dongle."""
        for port_info in serial.tools.list_ports.comports():
            if DongleDetectService.is_dongle(port_info):
                return DongleInfo(
                    port=port_info.device,
                    vid=port_info.vid or 0,
                    pid=port_info.pid or 0,
                    serial_number=port_info.serial_number or "",
                    description=port_info.description or "",
                )
        return None

    @staticmethod
    def is_dongle(port_info: ListPortInfo) -> bool:
        """Check 1 port có đúng VID/PID của NRF52840 dongle không."""
        return (
            port_info.vid == DONGLE_VID
            and port_info.pid == DONGLE_PID
        )

    @staticmethod
    def list_all_ports() -> list[ListPortInfo]:
        """Liệt kê tất cả COM ports (dùng cho debug/log)."""
        return list(serial.tools.list_ports.comports())
