"""
===============================================================================
  UWB RTLS Studio — Serial Service
===============================================================================
  File        : services/serial_service.py
  Description : Manages serial/USB connection between PC and the NRF52840 Dongle.
                Spawns a dedicated background thread for non-blocking raw reads.

  MVVM Role   : SERVICE — I/O layer.

  Giải thích flow:
    1. open(port) → mở COM port, khởi động _ReaderThread
    2. _ReaderThread chạy vòng lặp serial.read() → emit data_received
    3. write(data) → thread-safe, ghi trực tiếp (pyserial thread-safe cho write)
    4. close() → dừng reader thread, đóng port

  Thread model:
    Main Thread           _ReaderThread           Hardware
    ┌──────────┐         ┌──────────────┐       ┌──────────┐
    │ write() ──────────►│              │──────►│ TX bytes │
    │          │         │ serial.read()│◄──────│ RX bytes │
    │ ◄── data_received  │              │       │          │
    └──────────┘         └──────────────┘       └──────────┘

  Signals:
    - data_received(bytes)    → Raw data từ serial
    - connection_lost()       → Serial disconnected / error
    - error_occurred(str)     → Lỗi I/O

  Dependencies: pyserial
===============================================================================
"""
from __future__ import annotations

import logging
import threading

import serial
from PyQt6.QtCore import QObject, pyqtSignal

from utils.constants import (
    SERIAL_BAUD_RATE,
    SERIAL_READ_TIMEOUT_S,
    SERIAL_WRITE_TIMEOUT_S,
)

log = logging.getLogger(__name__)


class SerialService(QObject):
    """Quản lý 1 kết nối serial tới dongle."""

    # ── Signals ──────────────────────────────────────────────────────
    data_received = pyqtSignal(bytes)       # Raw bytes từ serial
    connection_lost = pyqtSignal()          # Port mất kết nối
    error_occurred = pyqtSignal(str)        # Lỗi I/O message

    def __init__(self, parent=None):
        super().__init__(parent)
        self._serial: serial.Serial | None = None
        self._reader_thread: threading.Thread | None = None
        self._running = False
        self._write_lock = threading.Lock()

    # ── Properties ───────────────────────────────────────────────────

    @property
    def is_open(self) -> bool:
        return self._serial is not None and self._serial.is_open

    @property
    def port_name(self) -> str:
        return self._serial.port if self._serial else ""

    # ── Public API ───────────────────────────────────────────────────

    def open(self, port: str) -> None:
        """Mở COM port và start reader thread.

        Raises serial.SerialException nếu không mở được.
        """
        if self.is_open:
            self.close()

        self._serial = serial.Serial(
            port=port,
            baudrate=SERIAL_BAUD_RATE,
            timeout=SERIAL_READ_TIMEOUT_S,
            write_timeout=SERIAL_WRITE_TIMEOUT_S,
        )
        log.info("Opened serial port %s @ %d baud", port, SERIAL_BAUD_RATE)

        # Start reader thread
        self._running = True
        self._reader_thread = threading.Thread(
            target=self._read_loop,
            name="SerialReader",
            daemon=True,
        )
        self._reader_thread.start()

    def write(self, data: bytes) -> None:
        """Ghi data xuống serial. Thread-safe with lock."""
        with self._write_lock:
            if not self.is_open:
                return
            try:
                self._serial.write(data)
            except (serial.SerialException, OSError) as e:
                log.error("Serial write error: %s", e)
                self.error_occurred.emit(str(e))

    def close(self) -> None:
        """Dừng reader thread và đóng serial port."""
        self._running = False
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=2.0)
            self._reader_thread = None
        if self._serial and self._serial.is_open:
            try:
                self._serial.close()
            except Exception:
                pass
            log.info("Closed serial port")
        self._serial = None

    # ── Private ──────────────────────────────────────────────────────

    def _read_loop(self) -> None:
        """Background thread: đọc serial liên tục, emit signal."""
        while self._running:
            try:
                if not self._serial or not self._serial.is_open:
                    break
                data = self._serial.read(256)
                if data:
                    self.data_received.emit(data)
            except (serial.SerialException, OSError) as e:
                if self._running:
                    log.error("Serial read error: %s", e)
                    self.error_occurred.emit(str(e))
                    self.connection_lost.emit()
                break
        log.debug("Reader thread exited")
