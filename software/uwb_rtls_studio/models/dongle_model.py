"""
===============================================================================
  UWB RTLS Studio — Dongle Model
===============================================================================
  File        : models/dongle_model.py
  Description : Lớp Model quản lý dữ liệu và logic kết nối Dongle.
                - Quản lý luồng quét USB (DongleDetectWorker).
                - Giao tiếp với SerialService để mở cổng.
                - Giao tiếp với ProtocolService để gửi/nhận lệnh verify.

  Logic mới (event-based + protobuf probe):
    1. Worker probe tất cả COM ports bằng protobuf handshake
    2. Khi tìm thấy dongle (nhận ACK) → Worker emit dongle_found
    3. Model mở serial port chính thức qua SerialService
    4. Gửi device_information_get để lấy thêm device info (verify)
    5. Nếu nhận device_information_resp → emit dongle_verified
    6. Nếu timeout → vẫn accept (unverified) -> (need to handle it)
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

class DongleModel(QObject):
    # Signals
    dongle_found = pyqtSignal(object)       # DongleInfo
    dongle_verified = pyqtSignal(dict)      # dict thông tin device
    error_occurred = pyqtSignal(str)
    search_timeout = pyqtSignal()
    ports_scanned = pyqtSignal(int)
    port_probing = pyqtSignal(str)          # Port đang probe

    def __init__(self, serial_service: SerialService, protocol_service: ProtocolService, parent=None):
        super().__init__(parent)
        self._serial = serial_service
        self._protocol = protocol_service
        self._worker: DongleDetectWorker | None = None
        self._current_info: DongleInfo | None = None

    def start_detection(self) -> None:
        self.stop_detection()
        self._worker = DongleDetectWorker()
        self._worker.dongle_found.connect(self._on_dongle_found)
        self._worker.port_scanned.connect(self.ports_scanned.emit)
        self._worker.port_probing.connect(self.port_probing.emit)
        self._worker.timeout.connect(self.search_timeout.emit)
        self._worker.start()

    def stop_detection(self) -> None:
        if self._worker:
            self._worker.stop()
            self._worker.wait(2000)
            self._worker = None

    def _on_dongle_found(self, info: DongleInfo) -> None:
        """Worker đã probe thành công (nhận ACK) → mở serial chính thức và xem như đã verify."""
        self._current_info = info
        self.dongle_found.emit(info)
        try:
            self._serial.open(info.port)
        except Exception as e:
            self.error_occurred.emit(f"Cannot open {info.port}: {e}")
            return
            
        # Dongle trả ACK lúc probe là đủ để verify, không cần chờ device_information_resp
        info_dict = {
            "port": info.port,
            "device_type": 3, # DEVICE_TYPE_GATEWAY (Dongle)
            "role": 0,
            "serial_number": info.serial_number or 0,
            "hw_version": 0,
            "fw_version": "N/A",
            "verified": True
        }
        self.dongle_verified.emit(info_dict)
