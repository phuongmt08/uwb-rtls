"""
===============================================================================
  UWB RTLS Studio — Dongle Detect Worker
===============================================================================
  File        : workers/dongle_detect_worker.py
  Description : QThread tự động detect dongle bằng protobuf probe.
                Tham khảo logic từ uwb_rtls_programmer/utils/dongle_session.py

  Logic:
    1. Worker start → probe tất cả COM ports hiện tại (initial scan)
    2. Nếu tìm thấy → emit dongle_found → stop
    3. Nếu chưa tìm thấy → monitor port changes (so sánh port list)
    4. Khi có COM port MỚI xuất hiện → probe port mới đó
    5. Mỗi port probe tối đa 3 lần, không ACK → skip → port tiếp theo
    6. Timeout toàn bộ quá trình: DONGLE_DETECT_TIMEOUT_S

  Dependencies: chỉ pyserial + common/ (KHÔNG dùng WMI/pywin32)

  Giải thích:
    - Worker chạy background thread, KHÔNG block UI.
    - Detect port changes bằng cách so sánh set(port names) — rất nhẹ.
    - Chỉ probe khi có port MỚI xuất hiện (event-like behavior).
    - Tất cả giao tiếp qua Qt signals (thread-safe).
===============================================================================
"""
from __future__ import annotations

import logging
import time

from PyQt6.QtCore import QThread, pyqtSignal

from services.dongle_detect_service import DongleDetectService, DongleInfo
from utils.constants import DONGLE_DETECT_TIMEOUT_S

log = logging.getLogger(__name__)

# Interval giữa các lần check port list thay đổi (ms)
_PORT_CHECK_INTERVAL_MS = 800


class DongleDetectWorker(QThread):
    """Background thread: detect dongle bằng protobuf probe."""

    # Signals
    dongle_found = pyqtSignal(object)   # DongleInfo
    port_scanned = pyqtSignal(int)      # Số lượng COM ports scanned
    port_probing = pyqtSignal(str)      # Port đang probe
    timeout = pyqtSignal()              # Hết thời gian

    def __init__(self, parent=None):
        super().__init__(parent)
        self._service = DongleDetectService()
        self._should_stop = False

    def stop(self):
        """Yêu cầu worker dừng."""
        self._should_stop = True

    def run(self):
        """Main entry: probe ban đầu, sau đó monitor port changes."""
        self._should_stop = False
        deadline = time.monotonic() + DONGLE_DETECT_TIMEOUT_S

        # ── Phase 1: Initial scan tất cả COM ports hiện tại ──────────
        log.info("Phase 1: Initial COM port probe...")
        result = self._scan_all_ports()
        if result is not None:
            self.dongle_found.emit(result)
            return

        if self._should_stop:
            return

        # ── Phase 2: Monitor port changes ─────────────────────────────
        # Giống cách programmer hoạt động: chỉ probe khi có port mới.
        # So sánh danh sách port mỗi 800ms — rất nhẹ, chỉ gọi
        # serial.tools.list_ports.comports() (không mở port).
        log.info("Phase 2: Monitoring for new COM ports...")
        known_ports = {p.device for p in self._service.list_all_ports()}

        while not self._should_stop:
            if time.monotonic() > deadline:
                self.timeout.emit()
                return

            self.msleep(_PORT_CHECK_INTERVAL_MS)

            if self._should_stop:
                return

            current_ports = {p.device for p in self._service.list_all_ports()}
            new_ports = current_ports - known_ports

            if new_ports:
                log.info("New COM port(s) detected: %s", new_ports)
                known_ports = current_ports

                # Đợi driver enumerate xong
                self.msleep(500)

                # Probe tất cả ports (ưu tiên port mới nhưng scan hết
                # để tránh miss trường hợp driver enumerate chậm)
                result = self._scan_all_ports()
                if result is not None:
                    self.dongle_found.emit(result)
                    return
            else:
                known_ports = current_ports

    def _scan_all_ports(self) -> DongleInfo | None:
        """Probe tất cả COM ports hiện tại. Return DongleInfo hoặc None."""
        ports = self._service.list_all_ports()
        self.port_scanned.emit(len(ports))

        if not ports:
            log.info("No COM ports found.")
            return None

        # Sort theo priority (giống programmer _score_port)
        ports.sort(key=self._service._score_port, reverse=True)

        for port_info in ports:
            if self._should_stop:
                return None
            self.port_probing.emit(port_info.device)
            result = self._service.probe_port(port_info.device)
            if result is not None:
                return result

        return None
