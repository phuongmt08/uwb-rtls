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
        # Grow the OS-level RX/TX ring buffers (Windows only). The default driver
        # buffer is small enough that a burst from multiple streaming devices can
        # overflow and silently drop bytes before Python ever reads them.
        if hasattr(self._serial, "set_buffer_size"):
            try:
                self._serial.set_buffer_size(rx_size=65536, tx_size=65536)
            except Exception as exc:
                log.debug("Could not resize serial buffers: %s", exc)
        log.info("Opened serial port %s @ %d baud", port, SERIAL_BAUD_RATE)

        # Start reader thread
        self._running = True
        self._reader_thread = threading.Thread(
            target=self._read_loop,
            name="SerialReader",
            daemon=True,
        )
        self._reader_thread.start()
        try:
            from utils.app_state import shared_app_state
            shared_app_state.threads.register("SerialReader", self._reader_thread)
        except Exception as exc:
            log.debug("Could not register SerialReader thread: %s", exc)

    def write(self, data: bytes) -> None:
        """Ghi data xuống serial. Thread-safe with lock."""
        with self._write_lock:
            if not self.is_open:
                return
            try:
                written = self._serial.write(data)
                # USB-CDC bridges can buffer host TX; flush so the dongle sees
                # the full HDLC frame before the next sequential GET is armed.
                try:
                    self._serial.flush()
                except Exception:
                    pass
                if written is not None and int(written) < len(data):
                    log.warning(
                        "Serial short write: wrote %s/%s bytes on %s",
                        written,
                        len(data),
                        self.port_name,
                    )
            except (serial.SerialException, OSError) as e:
                log.error("Serial write error: %s", e)
                self.error_occurred.emit(str(e))

    def close(self) -> None:
        """Dừng reader thread và đóng serial port."""
        self._running = False

        # 1. Đóng cổng serial trước để giải phóng lệnh read() đang block trong reader thread
        if self._serial and self._serial.is_open:
            try:
                self._serial.flush()
            except Exception:
                pass
            try:
                self._serial.close()
            except Exception:
                pass
            log.info("Closed serial port")
        self._serial = None

        # 2. Đợi reader thread kết thúc (sẽ kết thúc ngay lập tức vì cổng đã đóng)
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1.0)
            self._reader_thread = None

        try:
            from utils.app_state import shared_app_state
            shared_app_state.threads.unregister("SerialReader")
        except Exception as exc:
            log.debug("Could not unregister SerialReader thread: %s", exc)

    # ── Private ──────────────────────────────────────────────────────

    def reset_input_buffer(self) -> None:
        """Discard bytes already buffered by the serial adapter."""
        with self._write_lock:
            if not self.is_open:
                return
            try:
                self._serial.reset_input_buffer()
                log.debug("Serial RX input buffer reset")
            except (serial.SerialException, OSError) as exc:
                log.debug("Could not reset serial RX input buffer: %s", exc)

    def _read_loop(self) -> None:
        """Background thread: đọc serial liên tục, emit signal.

        Reads 1 byte (blocking up to the read timeout) then drains whatever else
        is already sitting in the OS buffer. A fixed-size read(256) would keep
        waiting up to the full timeout trying to fill the chunk, leaving bursts
        sitting in the driver buffer longer than necessary.
        """
        while self._running:
            try:
                if not self._serial or not self._serial.is_open:
                    break
                data = self._serial.read(1)
                if not data:
                    continue
                waiting = int(getattr(self._serial, "in_waiting", 0) or 0)
                if waiting:
                    data += self._serial.read(waiting)
                self.data_received.emit(data)
            except (serial.SerialException, OSError) as e:
                if self._running:
                    log.error("Serial read error: %s", e)
                    self.error_occurred.emit(str(e))
                    self.connection_lost.emit()
                break
        log.debug("Reader thread exited")