"""
===============================================================================
  UWB RTLS Studio — Scan Model
===============================================================================
  File        : models/scan_model.py
  Description : Lớp Model xử lý core logic của quá trình BLE Scan.
                - Quản lý danh sách, vòng đời thiết bị (prune stale).
                - Gửi lệnh BLE start/stop/connect qua ProtocolService.
===============================================================================
"""
from __future__ import annotations
import logging
import time
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from services.protocol_service import ProtocolService
from services.serial_service import SerialService
from common import protocol_pb2 as pb

log = logging.getLogger(__name__)

_CONNECT_TIMEOUT_MS = 10000
_DEVICE_TIMEOUT_S = 15.0

class ScanModel(QObject):
    # Signals
    device_list_changed = pyqtSignal(list)
    connect_success = pyqtSignal(dict)
    connect_failed = pyqtSignal(str)
    dongle_disconnected = pyqtSignal(str)
    
    def __init__(self, protocol_service: ProtocolService, serial_service: SerialService, parent=None):
        super().__init__(parent)
        self._protocol = protocol_service
        self._serial = serial_service
        self._devices: dict[str, dict] = {}
        self.is_scanning = False
        
        self._protocol.packet_received.connect(self._on_packet)
        self._serial.connection_lost.connect(self._on_connection_lost)
        
        self._prune_timer = QTimer(self)
        self._prune_timer.timeout.connect(self._prune_stale_devices)
        
        self._connect_timer = QTimer(self)
        self._connect_timer.setSingleShot(True)
        self._connect_timer.timeout.connect(self._on_connect_timeout)

    def start_scan(self) -> None:
        self._devices.clear()
        self.device_list_changed.emit([])
        self._protocol.send_command("ble_scan_start", duration_ms=0, interval_ms=160, window_ms=80, active_scanning=True)
        self.is_scanning = True
        self._prune_timer.start(5000)

    def stop_scan(self) -> None:
        if self.is_scanning:
            self._protocol.send_command("ble_scan_stop")
            self.is_scanning = False
            self._prune_timer.stop()

    def connect_device(self, mac_hex: str) -> bool:
        if mac_hex not in self._devices:
            return False
            
        self.stop_scan()
        
        # We MUST add a delay here! The dongle needs time to process ble_scan_stop 
        # before it can accept ble_connect. Without this delay, the first connect command 
        # is ignored by the firmware, requiring a second click.
        QTimer.singleShot(400, lambda: self._do_connect(mac_hex))
        return True

    def _do_connect(self, mac_hex: str) -> None:
        mac_bytes = bytes.fromhex(mac_hex.replace(":", ""))
        self._protocol.send_command("ble_connect", mac_address=mac_bytes)
        self._connect_timer.start(_CONNECT_TIMEOUT_MS)

    def cleanup(self) -> None:
        self.stop_scan()
        self._connect_timer.stop()
        try:
            self._serial.connection_lost.disconnect(self._on_connection_lost)
        except Exception:
            pass

    def _on_packet(self, param_name: str, pkt) -> None:
        if param_name == "ble_scan_result":
            self._handle_scan_result(pkt.ble_scan_result)
        elif param_name == "ble_status_resp":
            self._handle_ble_status(pkt.ble_status_resp)

    def _handle_scan_result(self, result) -> None:
        mac_hex = ":".join(f"{b:02X}" for b in result.mac_address)
        self._devices[mac_hex] = {
            "name": result.name or f"UWB-{mac_hex[-5:]}",
            "mac": mac_hex,
            "rssi": result.rssi_dbm,
            "serial": f"0x{result.serial_number:08X}" if result.serial_number else "",
            "last_seen": time.monotonic(),
        }
        self._emit_sorted_devices()

    def _handle_ble_status(self, status) -> None:
        if status.state == pb.BLE_STATE_CONNECTED:
            self._connect_timer.stop()
            self.connect_success.emit({"status": "connected"})

    def _prune_stale_devices(self) -> None:
        now = time.monotonic()
        stale = [mac for mac, d in self._devices.items() if now - d["last_seen"] > _DEVICE_TIMEOUT_S]
        if stale:
            for mac in stale:
                del self._devices[mac]
            self._emit_sorted_devices()

    def _emit_sorted_devices(self) -> None:
        sorted_list = sorted(self._devices.values(), key=lambda d: d["rssi"], reverse=True)
        self.device_list_changed.emit(sorted_list)

    def _on_connect_timeout(self) -> None:
        self.connect_failed.emit("BLE connect timed out.")

    def _on_connection_lost(self) -> None:
        self.dongle_disconnected.emit("Dongle was disconnected!")

