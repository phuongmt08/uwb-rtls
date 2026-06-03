"""
===============================================================================
  UWB RTLS Studio — Device Info ViewModel
===============================================================================
  File        : viewmodels/device_info_viewmodel.py
  Description : ViewModel cho tab "Device Info" (Tab 1).
                Hiển thị thông tin chi tiết device đang connected.

  MVVM Role   : VIEWMODEL

  Protocol Messages:
    - device_information_get_t / _resp_t  (4, 5)
    - battery_info_get_t / _resp_t        (61, 60)
    - ble_status_get_t / _resp_t          (34, 35)
    - ble_conn_params_get_t / _resp_t     (47, 49)
===============================================================================
"""
import logging
import time
from PyQt6.QtCore import QObject, pyqtSignal, QTimer
from services.protocol_service import ProtocolService
from common.transport import VvAddress

log = logging.getLogger(__name__)

# Throttle: minimum interval between table UI rebuilds (ms)
_TABLE_REBUILD_INTERVAL_MS = 2000
# Delay after ble_scan_stop before sending ble_connect (ms)
_STOP_TO_CONNECT_DELAY_MS = 400
# Delay after ble_connect before restarting scan (ms)
_CONNECT_TO_SCAN_DELAY_MS = 5000
# Telemetry poll interval (ms)
_TELEMETRY_POLL_MS = 10000


class DeviceInfoViewModel(QObject):
    device_info_updated = pyqtSignal(dict)
    dongle_info_updated = pyqtSignal(dict)
    ble_info_updated = pyqtSignal(dict)
    telemetry_updated = pyqtSignal(dict)
    advertising_devices_updated = pyqtSignal(list, bool)  # list of dicts, is_scanning

    def __init__(self, protocol: ProtocolService, dongle_model=None, parent=None):
        super().__init__(parent)
        self.protocol = protocol
        self.dongle_model = dongle_model
        self.protocol.packet_received.connect(self._on_packet)

        # Advertising devices dict: mac_hex -> {name, mac, rssi, last_seen}
        self._adv_devices = {}
        self._is_scanning = False
        # Connected device tracking
        self._connected_mac = ""
        self._connected_name = ""
        # Pending connect (after scan_stop delay)
        self._pending_connect_mac = ""

        # --- Prune timer: remove stale devices ---
        self._prune_timer = QTimer(self)
        self._prune_timer.timeout.connect(self._prune_devices)

        # --- Telemetry refresh timer ---
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh_telemetry)

        # --- Table rebuild throttle ---
        self._table_dirty = False
        self._table_timer = QTimer(self)
        self._table_timer.timeout.connect(self._flush_table)
        self._table_timer.start(_TABLE_REBUILD_INTERVAL_MS)
        
        # --- Handle Dongle Connection Lifecycle ---
        self.protocol._serial.connection_lost.connect(self._on_connection_lost)
        if self.dongle_model:
            self.dongle_model.dongle_verified.connect(self._on_dongle_reconnected)

        # Delayed init: give the main window time to wire signals
        QTimer.singleShot(300, self._delayed_init)

    def _delayed_init(self):
        """Called once after MainWindow has wired all signals."""
        self.refresh_dongle_info()
        
        # NOTE: If we are already connected (from ScanPopup), DO NOT start background scan
        # because ble_scan_start command causes the Dongle to disconnect the current device!
        if self._connected_mac:
            # Re-emit the connected device info now that UI is listening
            self.device_info_updated.emit({
                "Device Name": self._connected_name,
                "MAC Address": self._connected_mac,
            })
        else:
            self.start_background_scan()
            
        # Start telemetry polling (first poll after 2s, then every N seconds)
        QTimer.singleShot(2000, self._start_telemetry_polling)

    def _start_telemetry_polling(self):
        self.refresh_telemetry()
        self._refresh_timer.start(_TELEMETRY_POLL_MS)

    # ── Dongle Info ──────────────────────────────────────────────────
    def refresh_dongle_info(self):
        if self.dongle_model and hasattr(self.dongle_model, '_current_info') and self.dongle_model._current_info:
            info = self.dongle_model._current_info
            self.dongle_info_updated.emit({
                "Port": info.port,
                "VID / PID": f"{info.vid:04X} / {info.pid:04X}",
                "Serial": info.serial_number or info.description,
                "Status": "Connected"
            })

    def _on_connection_lost(self):
        log.warning("Dongle physically disconnected! Starting auto-detect loop...")
        self._connected_mac = ""
        self._connected_name = ""
        
        # Emit disconnected status for the Device
        self.device_info_updated.emit({
            "Device Name": "-",
            "MAC Address": "-",
            "Status": "Disconnected"
        })
        # Emit disconnected status for the Dongle
        self.dongle_info_updated.emit({
            "Port": "-",
            "VID / PID": "-",
            "Serial": "-",
            "Status": "Disconnected"
        })
        
        # Start auto-detect loop in the background
        if self.dongle_model:
            self.dongle_model.start_detection()

    def _on_dongle_reconnected(self, info_dict: dict):
        log.info("Dongle auto-reconnected and verified.")
        if info_dict.get("verified"):
            self.refresh_dongle_info()
            # Immediately restart scanning for BLE devices
            self.start_background_scan()

    # ── Connected device (from ScanPopup result) ─────────────────────
    def set_connected_device(self, name: str, mac: str):
        """Called by main.py after ScanPopup finishes, to seed initial device info."""
        self._connected_mac = mac
        self._connected_name = name
        self.device_info_updated.emit({
            "Device Name": name,
            "MAC Address": mac,
            "Status": "Connected",
        })
        log.info("Connected device set: %s (%s)", name, mac)

    # ── Background Scan ──────────────────────────────────────────────
    def start_background_scan(self):
        self._adv_devices.clear()
        self.protocol.send_command(
            "ble_scan_start",
            duration_ms=0, interval_ms=160, window_ms=80, active_scanning=True
        )
        self._is_scanning = True
        self._prune_timer.start(5000)
        self._mark_table_dirty()
        log.info("Background scan started")

    def _stop_scan(self):
        if self._is_scanning:
            self.protocol.send_command("ble_scan_stop")
            self._is_scanning = False
            self._prune_timer.stop()

    # ── Telemetry ────────────────────────────────────────────────────
    def refresh_telemetry(self):
        try:
            # Only query the peripheral if we have a connected device
            if self._connected_mac:
                self.protocol.send_command("device_information_get", dst_addr=VvAddress.PERIPHERAL)
                self.protocol.send_command("battery_info_get", dst_addr=VvAddress.PERIPHERAL)
            
            # Always query the dongle's own BLE status
            self.protocol.send_command("ble_status_get", dst_addr=VvAddress.CENTRAL)
        except Exception as e:
            log.warning("refresh_telemetry failed: %s", e)

    # ── Connect / Switch ─────────────────────────────────────────────
    def connect_device(self, mac_hex: str):
        """
        Connect to a device from the advertising list.
        Flow: stop_scan → (delay) → ble_disconnect old → ble_connect new
        """
        log.info("Connect request: %s", mac_hex)
        name = self._adv_devices.get(mac_hex, {}).get("name", "Unknown")

        # Cancel any pending connect
        self._pending_connect_mac = mac_hex

        # 1) If already connected to a different device, disconnect first
        if self._connected_mac and self._connected_mac != mac_hex:
            try:
                self.protocol.send_command("ble_disconnect", reason=0)
            except Exception:
                pass
            self._connected_mac = ""
            self._connected_name = ""

        # 2) Stop scan
        self._stop_scan()

        # 3) After dongle finishes stopping, send ble_connect
        QTimer.singleShot(_STOP_TO_CONNECT_DELAY_MS, lambda: self._do_connect(mac_hex, name))

    def _do_connect(self, mac_hex: str, name: str):
        """Actually send ble_connect after scan has stopped."""
        # Guard: make sure this is still the intended connect
        if self._pending_connect_mac != mac_hex:
            return

        try:
            mac_bytes = bytes.fromhex(mac_hex.replace(":", ""))
            self.protocol.send_command("ble_connect", mac_address=mac_bytes)
            log.info("ble_connect sent for %s (%s)", name, mac_hex)
        except Exception as e:
            log.error("ble_connect failed: %s", e)
            return

        # Update UI immediately with the target name & mac to show "Connecting"
        self._connected_mac = mac_hex
        self._connected_name = name
        self.device_info_updated.emit({
            "Device Name": name,
            "MAC Address": mac_hex,
            "Status": "Connecting"
        })

        # Clear pending
        self._pending_connect_mac = ""

    # ── Packet Handler ───────────────────────────────────────────────
    def _on_packet(self, param_name, pkt):
        if param_name == "device_information_resp":
            resp = pkt.device_information_resp
            self.device_info_updated.emit({
                "Type": "TAG" if resp.device_type == 0 else "GATEWAY",
                "Role": "Peripheral" if resp.role == 1 else "Central",
                "Serial Number": f"0x{resp.serial_number:08X}" if hasattr(resp, 'serial_number') else "-",
                "Firmware": f"v{resp.fw_version.major}.{resp.fw_version.minor}.{resp.fw_version.patch}",
                "Hardware Rev": str(resp.hw_version),
            })

        elif param_name == "battery_info_resp":
            resp = pkt.battery_info_resp
            self.telemetry_updated.emit({
                "bat_voltage_mv": resp.bat_voltage_mv,
                "bat_soc_percent": resp.bat_soc_percent,
                "remaining_min": resp.remaining_min,
                "is_charging": resp.is_charging,
                "mcu_temp_c": resp.mcu_temp_c,
                "vdda_mv": resp.vdda_mv,
                "uwb_temp_c": resp.uwb_temp_c,
                "uwb_vbat_mv": resp.uwb_vbat_mv,
                "imu_temp_c": resp.imu_temp_c,
            })

        elif param_name == "ble_status_resp":
            resp = pkt.ble_status_resp
            self.ble_info_updated.emit({
                "state": resp.state,
                "rssi_dbm": resp.rssi_dbm,
            })
            
            # If the dongle just confirmed the connection, proceed to ask for device info
            if resp.state == self.protocol.pb.BLE_STATE_CONNECTED and self._connected_mac:
                log.info("Dongle confirmed BLE_STATE_CONNECTED. Fetching telemetry from Peripheral.")
                
                # Signal the UI that we are officially connected
                self.device_info_updated.emit({
                    "Device Name": self._connected_name,
                    "MAC Address": self._connected_mac,
                    "Status": "Connected",
                    "SwitchToLogTab": True
                })
                
                # Fetch dev_info_get etc. right away
                self.refresh_telemetry()
                
            elif resp.state == self.protocol.pb.BLE_STATE_IDLE and self._connected_mac:
                # BLE_STATE_IDLE when we had a connected device means the device disconnected
                # (proto does not have BLE_STATE_DISCONNECTED, dongle goes back to IDLE)
                log.warning("Dongle reports BLE_STATE_IDLE while device was connected (Device lost connection).")
                self._connected_mac = ""
                self._connected_name = ""
                self.device_info_updated.emit({
                    "Device Name": "-",
                    "MAC Address": "-",
                    "Status": "Disconnected"
                })
                # Restart scan so user can find it again
                self.start_background_scan()

        elif param_name == "ble_scan_result":
            res = pkt.ble_scan_result
            mac_hex = ":".join(f"{b:02X}" for b in res.mac_address)
            self._adv_devices[mac_hex] = {
                "name": res.name or f"UWB-{mac_hex[-5:]}",
                "mac": mac_hex,
                "rssi": res.rssi_dbm,
                "last_seen": time.monotonic()
            }
            # Don't rebuild table every single packet — just mark dirty
            self._mark_table_dirty()

    # ── Throttled Table Update ───────────────────────────────────────
    def _mark_table_dirty(self):
        self._table_dirty = True

    def _flush_table(self):
        """Called by timer — only rebuild table if data changed."""
        if not self._table_dirty:
            return
        self._table_dirty = False
        # Sort by MAC (stable order) instead of RSSI to prevent row jumps
        sorted_list = sorted(self._adv_devices.values(), key=lambda d: d["mac"])
        self.advertising_devices_updated.emit(sorted_list, self._is_scanning)

    # ── Prune stale devices ──────────────────────────────────────────
    def _prune_devices(self):
        now = time.monotonic()
        stale = [mac for mac, d in self._adv_devices.items() if now - d["last_seen"] > 15.0]
        for mac in stale:
            del self._adv_devices[mac]
        if stale:
            self._mark_table_dirty()
