"""
===============================================================================
  UWB RTLS Studio — Dongle Model
===============================================================================
  File        : models/dongle_model.py
  Description : Lớp Model quản lý dữ liệu và logic kết nối Dongle.
                - Quản lý luồng quét USB (DongleDetectWorker).
                - Giao tiếp với SerialService để mở cổng.
                - Giao tiếp với ProtocolService để gửi/nhận lệnh verify.
===============================================================================
"""
from __future__ import annotations
import logging
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from services.serial_service import SerialService
from services.protocol_service import ProtocolService
from services.dongle_detect_service import DongleInfo
from workers.dongle_detect_worker import DongleDetectWorker

log = logging.getLogger(__name__)

_VERIFY_TIMEOUT_MS = 3000

class DongleModel(QObject):
    # Signals
    dongle_found = pyqtSignal(object)       # DongleInfo
    dongle_verified = pyqtSignal(dict)      # dict thông tin device
    error_occurred = pyqtSignal(str)
    search_timeout = pyqtSignal()
    ports_scanned = pyqtSignal(int)

    def __init__(self, serial_service: SerialService, protocol_service: ProtocolService, parent=None):
        super().__init__(parent)
        self._serial = serial_service
        self._protocol = protocol_service
        self._worker: DongleDetectWorker | None = None
        self._current_info: DongleInfo | None = None
        
        self._verify_timer = QTimer(self)
        self._verify_timer.setSingleShot(True)
        self._verify_timer.timeout.connect(self._on_verify_timeout)
        
        self._protocol.packet_received.connect(self._on_packet)

    def start_detection(self) -> None:
        self.stop_detection()
        self._worker = DongleDetectWorker()
        self._worker.dongle_found.connect(self._on_dongle_found)
        self._worker.port_scanned.connect(self.ports_scanned.emit)
        self._worker.timeout.connect(self.search_timeout.emit)
        self._worker.start()

    def stop_detection(self) -> None:
        if self._worker:
            self._worker.stop()
            self._worker.wait(2000)
            self._worker = None
        self._verify_timer.stop()

    def _on_dongle_found(self, info: DongleInfo) -> None:
        self._current_info = info
        self.dongle_found.emit(info)
        try:
            self._serial.open(info.port)
        except Exception as e:
            self.error_occurred.emit(f"Cannot open {info.port}: {e}")
            return
            
        self._protocol.send_command("device_information_get")
        self._verify_timer.start(_VERIFY_TIMEOUT_MS)

    def _on_packet(self, param_name: str, pkt) -> None:
        if param_name == "device_information_resp":
            self._verify_timer.stop()
            resp = pkt.device_information_resp
            info_dict = {
                "port": self._current_info.port if self._current_info else "",
                "device_type": resp.device_type,
                "role": resp.role,
                "serial_number": resp.serial_number,
                "hw_version": resp.hw_version,
                "fw_version": f"v{resp.fw_version.major}.{resp.fw_version.minor}.{resp.fw_version.patch}",
                "verified": True
            }
            self.dongle_verified.emit(info_dict)

    def _on_verify_timeout(self) -> None:
        log.warning("Device verify timeout, proceeding unverified")
        info_dict = {
            "port": self._current_info.port if self._current_info else "",
            "device_type": 0,
            "role": 0,
            "serial_number": 0,
            "hw_version": 0,
            "fw_version": "unknown",
            "verified": False
        }
        self.dongle_verified.emit(info_dict)
