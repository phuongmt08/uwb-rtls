"""
==============================================================================
  UWB RTLS Studio — Scan Model
==============================================================================
  File        : models/scan_model.py
  Description : Model managing BLE scanning, device discovery list updates,
                stale device pruning, and connect commands.

  MVVM Role   : MODEL — BLE Scan and connect logic.

  Thread Model:
    - Main GUI Thread: Manages discovered device list updates and connection
      timers synchronously on the Main GUI Thread.
    - Initiates scan/connect command requests to ProtocolService.
==============================================================================
"""
from __future__ import annotations
import logging
import time
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from services.protocol_service import ProtocolService
from services.serial_service import SerialService
from common import protocol_pb2 as pb
from utils.constants import DEVICE_TIMEOUT_S, STOP_TO_CONNECT_DELAY_MS
from common.transport import VvAddress

log = logging.getLogger(__name__)

_CONNECT_TIMEOUT_MS = 10000
_SCAN_RESTART_DELAY_MS = 250
_STATUS_POLL_INTERVAL_MS = 500


class ScanModel(QObject):
    # Signals
    device_list_changed = pyqtSignal(list)
    connect_success = pyqtSignal(dict)
    connect_failed = pyqtSignal(str)
    dongle_disconnected = pyqtSignal(str)
    
    def __init__(self, protocol_service: ProtocolService, serial_service: SerialService, command_bus=None, parent=None):
        super().__init__(parent)
        self._protocol = protocol_service
        self._serial = serial_service
        self._command_bus = command_bus
        self._devices: dict[str, dict] = {}
        self._device_order: dict[str, int] = {}
        self._next_device_order = 0
        self.is_scanning = False
        self.connected_mac = ""
        self._is_connecting = False
        
        self._protocol.packet_received.connect(self._on_packet)
        self._serial.connection_lost.connect(self._on_connection_lost)
        
        self._prune_timer = QTimer(self)
        self._prune_timer.timeout.connect(self._prune_stale_devices)
        
        self._connect_timer = QTimer(self)
        self._connect_timer.setSingleShot(True)
        self._connect_timer.timeout.connect(self._on_connect_timeout)

        # Poll ble_status_get after ble_connect to proactively confirm state
        self._status_poll_timer = QTimer(self)
        self._status_poll_timer.setInterval(_STATUS_POLL_INTERVAL_MS)
        self._status_poll_timer.timeout.connect(self._poll_connect_status)

    def _send_command(self, command_name: str, **kwargs):
        if self._command_bus:
            dst_addr = kwargs.pop("dst_addr", self._protocol.pb.PACKET_ADDR_CENTRAL)
            return self._command_bus.send(command_name, dst_addr=dst_addr, **kwargs)
        return self._protocol.send_command(command_name, **kwargs)

    def start_scan(self, clear_results: bool = True) -> None:
        if clear_results:
            self._clear_devices()
        if not self.is_scanning:
            self._send_command(
                "ble_scan_start",
                src_addr=self._protocol.pb.PACKET_ADDR_HOST,
                dst_addr=self._protocol.pb.PACKET_ADDR_CENTRAL
            )
            self.is_scanning = True
        self._prune_timer.start(5000)

    def restart_scan(self) -> None:
        """Force a fresh scan command sequence for the popup retry button."""
        self._clear_devices()
        if self.is_scanning:
            self._send_command(
                "ble_scan_stop",
                src_addr=self._protocol.pb.PACKET_ADDR_HOST,
                dst_addr=self._protocol.pb.PACKET_ADDR_CENTRAL
            )
            self.is_scanning = False
        QTimer.singleShot(_SCAN_RESTART_DELAY_MS, lambda: self.start_scan(clear_results=False))

    def stop_scan(self) -> None:
        if self.is_scanning:
            self._send_command(
                "ble_scan_stop",
                src_addr=self._protocol.pb.PACKET_ADDR_HOST,
                dst_addr=self._protocol.pb.PACKET_ADDR_CENTRAL
            )
            self.is_scanning = False
            self._prune_timer.stop()

    def connect_device(self, mac_hex: str) -> bool:
        if mac_hex not in self._devices:
            return False
            
        self.stop_scan()
        self.connected_mac = mac_hex
        self._is_connecting = True
        
        # We MUST add a delay here! The dongle needs time to process ble_scan_stop 
        # before it can accept ble_connect. Without this delay, the first connect command 
        # is ignored by the firmware, requiring a second click.
        QTimer.singleShot(STOP_TO_CONNECT_DELAY_MS, lambda: self._do_connect(mac_hex))
        return True

    def _do_connect(self, mac_hex: str) -> None:
        mac_bytes = bytes.fromhex(mac_hex.replace(":", ""))
        self._send_command(
            "ble_connect",
            src_addr=self._protocol.pb.PACKET_ADDR_HOST,
            dst_addr=self._protocol.pb.PACKET_ADDR_CENTRAL,
            mac_address=mac_bytes
        )
        self._connect_timer.start(_CONNECT_TIMEOUT_MS)
        self._status_poll_timer.start()

    def cleanup(self) -> None:
        self.stop_scan()
        self._connect_timer.stop()
        self._status_poll_timer.stop()
        self._is_connecting = False
        try:
            self._serial.connection_lost.disconnect(self._on_connection_lost)
        except Exception:
            pass
        try:
            self._protocol.packet_received.disconnect(self._on_packet)
        except Exception:
            pass

    def _on_packet(self, param_name: str, pkt) -> None:
        if param_name == "ble_scan_result":
            self._handle_scan_result(pkt.ble_scan_result)
        elif param_name == "ble_status_resp":
            self._handle_ble_status(pkt.ble_status_resp)

    def _handle_scan_result(self, result) -> None:
        mac_hex = ":".join(f"{b:02X}" for b in result.mac_address)
        if mac_hex not in self._device_order:
            self._device_order[mac_hex] = self._next_device_order
            self._next_device_order += 1

        current = self._devices.get(mac_hex, {})
        current.update({
            "name": result.name or f"UWB-{mac_hex[-5:]}",
            "mac": mac_hex,
            "rssi": result.rssi_dbm,
            "serial": f"0x{result.serial_number:08X}" if result.serial_number else "",
            "last_seen": time.monotonic(),
            "order": self._device_order[mac_hex],
        })
        self._devices[mac_hex] = current
        self._emit_sorted_devices()

    def _handle_ble_status(self, status) -> None:
        if not self._is_connecting:
            return

        if status.state == pb.BLE_STATE_CONNECTED:
            self._connect_timer.stop()
            self._status_poll_timer.stop()
            self._is_connecting = False
            log.info("BLE connection confirmed via ble_status_resp (state=CONNECTED)")
            self.connect_success.emit({"status": "connected"})
        elif status.state == pb.BLE_STATE_CONNECTING:
            return

    def _prune_stale_devices(self) -> None:
        now = time.monotonic()
        stale = [mac for mac, d in self._devices.items() if now - d["last_seen"] > DEVICE_TIMEOUT_S]
        if stale:
            for mac in stale:
                del self._devices[mac]
                self._device_order.pop(mac, None)
            self._emit_sorted_devices()

    def _emit_sorted_devices(self) -> None:
        sorted_list = sorted(self._devices.values(), key=lambda d: d.get("order", 0))
        self.device_list_changed.emit(sorted_list)

    def _clear_devices(self) -> None:
        self._devices.clear()
        self._device_order.clear()
        self._next_device_order = 0
        self.device_list_changed.emit([])

    def _on_connect_timeout(self) -> None:
        self._is_connecting = False
        self._status_poll_timer.stop()
        self.connect_failed.emit("BLE connect timed out.")

    def _poll_connect_status(self) -> None:
        """Proactively send ble_status_get to confirm connection state from dongle."""
        if not self._is_connecting:
            self._status_poll_timer.stop()
            return
        log.debug("Polling ble_status_get during connect...")
        self._send_command(
            "ble_status_get",
            src_addr=self._protocol.pb.PACKET_ADDR_HOST,
            dst_addr=self._protocol.pb.PACKET_ADDR_CENTRAL,
        )

    def _on_connection_lost(self) -> None:
        self.dongle_disconnected.emit("Dongle was disconnected!")

