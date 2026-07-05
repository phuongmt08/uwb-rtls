"""
===============================================================================
  UWB RTLS Studio - Scan ViewModel
===============================================================================
  File        : viewmodels/scan_viewmodel.py
  Description : Strict MVVM ViewModel for BLE scan popup presentation.
===============================================================================
"""
from __future__ import annotations
import logging
from PyQt6.QtCore import QObject, pyqtSignal

from models.scan_model import ScanModel

log = logging.getLogger(__name__)


class ScanViewModel(QObject):
    # Signals for the view
    scan_started = pyqtSignal()
    scan_stopped = pyqtSignal()
    device_list_updated = pyqtSignal(list)
    device_connecting = pyqtSignal(str)
    device_connected = pyqtSignal(dict)
    connection_failed = pyqtSignal(str)
    connection_progress_updated = pyqtSignal(dict)
    log_message = pyqtSignal(str)
    dongle_disconnected = pyqtSignal(str)

    def __init__(self, model: ScanModel, parent=None):
        super().__init__(parent)
        self.model = model

        # Bind model signals to view presentation signals
        self.model.device_list_changed.connect(self._on_device_list_changed)
        self.model.connect_success.connect(self.device_connected.emit)
        self.model.connect_failed.connect(self._on_connect_failed)
        if hasattr(self.model, "connection_progress_changed"):
            self.model.connection_progress_changed.connect(self._on_connection_progress)
        self.model.dongle_disconnected.connect(self.dongle_disconnected.emit)
    # ── Action từ View ───────────────────────────────────────────────
    def start_scan(self) -> None:
        self.log_message.emit("Sending ble_scan_start...")
        self.model.start_scan()
        self.scan_started.emit()
        self.log_message.emit("BLE scan started (continuous mode)")

    def restart_scan(self) -> None:
        self.log_message.emit("Restarting BLE scan...")
        self.model.restart_scan()
        self.scan_started.emit()
        self.log_message.emit("BLE scan restarted")

    def stop_scan(self) -> None:
        self.model.stop_scan()
        self.scan_stopped.emit()
        self.log_message.emit("BLE scan stopped")

    def connect_device(self, mac_hex: str) -> None:
        self.device_connecting.emit(mac_hex)
        self.log_message.emit(f"Connecting to {mac_hex}...")

        success = self.model.connect_device(mac_hex)
        if not success:
            self.connection_failed.emit(f"Device {mac_hex} not in scan list")
            self.log_message.emit("❌ Connect failed: device not found")

    def cleanup(self) -> None:
        self.model.cleanup()
# ── Presentation Logic ───────────────────────────────────────────
    def _on_device_list_changed(self, device_list: list) -> None:
        # Nếu có logic chuyển đổi format (ví dụ thêm đuôi ' dBm' hoặc dịch màu) 
        # thì sẽ xử lý ở đây. Tạm thời pass list cho View trực tiếp xử lý item text.
        self.device_list_updated.emit(device_list)

    def _on_connect_failed(self, msg: str) -> None:
        self.connection_failed.emit(msg)
        self.log_message.emit(str(msg))

    def _on_connection_progress(self, info: dict) -> None:
        self.connection_progress_updated.emit(info)
        message = info.get("message")
        if message:
            self.log_message.emit(str(message))
