"""
===============================================================================
  UWB RTLS Studio — Dongle Detect Worker
===============================================================================
  File        : workers/dongle_detect_worker.py
  Description : QThread polling tìm NRF52840 dongle trên USB COM ports.
                Chạy background, emit signal khi found/not_found.

  Giải thích:
    - Worker chạy vòng lặp: gọi DongleDetectService.find_dongle_port()
      mỗi DONGLE_DETECT_POLL_MS (500ms).
    - Nếu tìm thấy → emit dongle_found(DongleInfo) → stop.
    - Nếu hết timeout → emit timeout() → stop.
    - Có thể cancel bất cứ lúc nào qua stop().
===============================================================================
"""
from __future__ import annotations

import time

from PyQt6.QtCore import QThread, pyqtSignal

from services.dongle_detect_service import DongleDetectService, DongleInfo
from utils.constants import DONGLE_DETECT_TIMEOUT_S, DONGLE_DETECT_POLL_MS


class DongleDetectWorker(QThread):
    """Background thread: polling tìm dongle trên app."""

    # Signals
    dongle_found = pyqtSignal(object)   # DongleInfo
    port_scanned = pyqtSignal(int)      # Số lượng COM ports scanned
    timeout = pyqtSignal()              # Hết thời gian

    def __init__(self, parent=None):
        super().__init__(parent)
        self._service = DongleDetectService()
        self._should_stop = False

    def stop(self):
        """Yêu cầu worker dừng."""
        self._should_stop = True

    def run(self):
        """Main loop: scan COM ports cho đến khi tìm thấy hoặc timeout."""
        self._should_stop = False
        deadline = time.monotonic() + DONGLE_DETECT_TIMEOUT_S
        poll_s = DONGLE_DETECT_POLL_MS / 1000.0

        while not self._should_stop:
            # Check timeout
            if time.monotonic() > deadline:
                self.timeout.emit()
                return

            # Scan
            ports = self._service.list_all_ports()
            self.port_scanned.emit(len(ports))

            info = self._service.find_dongle_port()
            if info is not None:
                self.dongle_found.emit(info)
                return

            # Wait trước khi scan lại
            self.msleep(int(poll_s * 1000))
