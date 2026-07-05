"""
==============================================================================
  UWB RTLS Studio — Scan Model
==============================================================================
  File        : models/scan_model.py
  Description : Model managing BLE scanning, device discovery list updates,
                manual refresh, and connect commands.

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
from utils.constants import STOP_TO_CONNECT_DELAY_MS
from common.transport import VvAddress
from utils.ble_hci import normalize_hci_reason

log = logging.getLogger(__name__)

_CONNECT_TIMEOUT_MS = 15000
_CONNECT_TIME_SYNC_TIMEOUT_MS = 1500
_SCAN_RESTART_DELAY_MS = 250
_STATUS_POLL_INTERVAL_MS = 500


class ScanModel(QObject):
    # Signals
    device_list_changed = pyqtSignal(list)
    connect_success = pyqtSignal(dict)
    connect_failed = pyqtSignal(str)
    connection_progress_changed = pyqtSignal(dict)
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
        self._connect_stage = "idle"
        self._ble_connected_seen = False
        self._pending_time_sync_seq: int | None = None
        self._connected_info: dict = {}
        
        self._protocol.packet_received.connect(self._on_packet)
        self._protocol.ack_received.connect(self._on_ack)
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

        self._time_sync_ack_timer = QTimer(self)
        self._time_sync_ack_timer.setSingleShot(True)
        self._time_sync_ack_timer.timeout.connect(self._on_time_sync_timeout)

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
        self._connect_stage = "selected"
        self._ble_connected_seen = False
        self._pending_time_sync_seq = None
        self._connected_info = {}
        self._emit_progress(10, f"Selected {mac_hex}. Preparing BLE connect...")
        
        # We MUST add a delay here! The dongle needs time to process ble_scan_stop 
        # before it can accept ble_connect. Without this delay, the first connect command 
        # is ignored by the firmware, requiring a second click.
        QTimer.singleShot(STOP_TO_CONNECT_DELAY_MS, lambda: self._do_connect(mac_hex))
        return True

    def _do_connect(self, mac_hex: str) -> None:
        mac_bytes = bytes.fromhex(mac_hex.replace(":", ""))
        self._connect_stage = "ble_connect"
        pkt = self._send_command(
            "ble_connect",
            src_addr=self._protocol.pb.PACKET_ADDR_HOST,
            dst_addr=self._protocol.pb.PACKET_ADDR_CENTRAL,
            mac_address=mac_bytes
        )
        if pkt is None:
            self._is_connecting = False
            self._connect_stage = "idle"
            self.connect_failed.emit("Failed to send ble_connect.")
            return
        self._emit_progress(35, f"Connecting BLE MAC {mac_hex}...")
        self._connect_timer.start(_CONNECT_TIMEOUT_MS)
        self._status_poll_timer.start()

    def cleanup(self) -> None:
        self.stop_scan()
        self._connect_timer.stop()
        self._status_poll_timer.stop()
        self._time_sync_ack_timer.stop()
        self._is_connecting = False
        self._connect_stage = "idle"
        self._pending_time_sync_seq = None
        try:
            self._serial.connection_lost.disconnect(self._on_connection_lost)
        except Exception:
            pass
        try:
            self._protocol.packet_received.disconnect(self._on_packet)
        except Exception:
            pass
        try:
            self._protocol.ack_received.disconnect(self._on_ack)
        except Exception:
            pass

    def _on_packet(self, param_name: str, pkt) -> None:
        if param_name == "ble_scan_result":
            self._handle_scan_result(pkt.ble_scan_result)
        elif param_name == "ble_status_resp":
            self._handle_ble_status(pkt.ble_status_resp)
        elif param_name == "device_information_resp":
            self._handle_device_info(pkt.device_information_resp)

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

        reason_code, has_reason = self._disconnect_reason_from(status)

        if status.state == pb.BLE_STATE_CONNECTED:
            self._ble_connected_seen = True
            if self._connect_stage in ("ble_connect", "selected"):
                log.info("BLE link up; requesting device information before completing connect.")
                self._connect_stage = "device_info"
                self._emit_progress(55, "BLE link established. Reading device information...")
                self._send_command(
                    "device_information_get",
                    src_addr=self._protocol.pb.PACKET_ADDR_HOST,
                    dst_addr=VvAddress.MCU,
                )
            elif self._connect_stage == "final_status":
                self._finish_connect_success()
        elif status.state == pb.BLE_STATE_CONNECTING:
            if self._connect_stage in ("selected", "ble_connect"):
                self._connect_stage = "ble_connect"
                self._emit_progress(45, "Dongle is establishing BLE link...")
            return
        elif has_reason and reason_code:
            reason = normalize_hci_reason(reason_code)
            log.warning(
                "Popup connect failed with BLE state %s reason=%s (%s).",
                int(status.state),
                reason["code_hex"],
                reason["name"],
            )
            self._connect_timer.stop()
            self._status_poll_timer.stop()
            self._time_sync_ack_timer.stop()
            self._pending_time_sync_seq = None
            self._is_connecting = False
            self._connect_stage = "idle"
            self.connect_failed.emit(self._reason_text(reason))
            return

    def _handle_device_info(self, resp) -> None:
        if not self._is_connecting or self._connect_stage != "device_info":
            return

        self._connected_info = {
            "status": "connected",
            "device_type": getattr(resp, "device_type", 0),
            "role": getattr(resp, "role", 0),
            "serial_number": getattr(resp, "serial_number", 0),
        }
        log.info("Device information received during popup connect; sending optional time_sync_set.")
        self._connect_stage = "time_sync"
        self._emit_progress(72, "Device information received. Setting device time...")
        self._send_time_sync_set()

    def _send_time_sync_set(self) -> None:
        try:
            pkt = self._send_command(
                "time_sync_set",
                src_addr=self._protocol.pb.PACKET_ADDR_HOST,
                dst_addr=VvAddress.MCU,
                unix_time_ms=int(time.time() * 1000),
                timezone_offset=self._host_timezone_offset_min(),
            )
        except Exception as exc:
            log.warning("Popup time_sync_set failed to send; continuing connect flow: %s", exc)
            self._begin_final_status_check("Time sync send failed; checking final BLE state...")
            return

        if pkt is None:
            log.warning("Popup time_sync_set was not sent; continuing connect flow.")
            self._begin_final_status_check("Time sync unavailable; checking final BLE state...")
            return

        self._pending_time_sync_seq = int(pkt.hdr.seq)
        self._time_sync_ack_timer.start(_CONNECT_TIME_SYNC_TIMEOUT_MS)
        self._emit_progress(82, "Setting device time...")

    def _on_ack(self, ack_seq: int, response: int) -> None:
        if self._pending_time_sync_seq is None:
            return
        if int(ack_seq) != int(self._pending_time_sync_seq):
            return

        self._time_sync_ack_timer.stop()
        self._pending_time_sync_seq = None
        if int(response) == int(pb.PACKET_ACK_RESPONSE_ACK):
            self._begin_final_status_check("Time synchronized. Confirming final BLE state...")
        else:
            log.warning("Popup time_sync_set NACK response=%s; continuing connect flow.", response)
            self._begin_final_status_check("Time sync skipped by device. Confirming final BLE state...")

    def _on_time_sync_timeout(self) -> None:
        if self._pending_time_sync_seq is None:
            return
        log.warning("Popup time_sync_set ACK timeout for seq=%s; continuing connect flow.", self._pending_time_sync_seq)
        self._pending_time_sync_seq = None
        self._begin_final_status_check("Time sync timed out. Confirming final BLE state...")

    def _begin_final_status_check(self, message: str) -> None:
        if not self._is_connecting:
            return
        self._connect_stage = "final_status"
        self._emit_progress(90, message)
        self._poll_connect_status()

    def _finish_connect_success(self) -> None:
        if not self._is_connecting:
            return
        self._connect_timer.stop()
        self._status_poll_timer.stop()
        self._time_sync_ack_timer.stop()
        self._is_connecting = False
        self._connect_stage = "connected"
        self._pending_time_sync_seq = None
        self._emit_progress(100, "Connected.")
        log.info("Popup connect flow complete.")
        info = {"status": "connected"}
        info.update(self._connected_info)
        self.connect_success.emit(info)

    def _prune_stale_devices(self) -> None:
        # Keep the last discovered snapshot until the user manually rescans.
        return

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
        self._connect_stage = "idle"
        self._status_poll_timer.stop()
        self._time_sync_ack_timer.stop()
        self._pending_time_sync_seq = None
        self.connect_failed.emit("BLE connect flow timed out.")

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

    @staticmethod
    def _disconnect_reason_from(resp) -> tuple[int, bool]:
        reason = int(getattr(resp, "disconnect_reason", 0) or 0) & 0xFF
        has_reason = reason != 0
        try:
            has_reason = bool(resp.HasField("disconnect_reason")) or has_reason
        except Exception:
            pass
        return reason, has_reason

    @staticmethod
    def _reason_text(reason: dict) -> str:
        return f"{reason.get('code_hex', '0x00')} - {reason.get('name', 'Unknown HCI Error')}"

    def _emit_progress(self, progress: int, message: str) -> None:
        self.connection_progress_changed.emit({
            "progress": max(0, min(100, int(progress))),
            "message": message,
            "mac": self.connected_mac,
            "phase": self._connect_stage,
        })

    @staticmethod
    def _host_timezone_offset_min() -> int:
        local_time_struct = time.localtime()
        timezone_offset = getattr(time, "timezone", 0)
        if getattr(time, "daylight", 0) and local_time_struct.tm_isdst:
            timezone_offset = getattr(time, "altzone", timezone_offset)
        return int((-timezone_offset) / 60)

