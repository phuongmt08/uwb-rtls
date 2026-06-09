"""
==============================================================================
  UWB RTLS Studio — Device Model
==============================================================================
  File        : models/device_model.py
  Description : Model managing the state and communication logic of the connected
                BLE peripheral. Acts as the sole Source of Truth for connected
                device state, telemetry updates, and BLE scanning results.

  MVVM Role   : MODEL — State Management & Business/Domain logic.

  Thread Model:
    - Main GUI Thread: All methods and signal slot handlers execute strictly
      on this thread.
    - Protocol incoming signals are queued via PyQt to ensure that packet processing
      and state mutation are confined to the Main GUI Thread.
==============================================================================
"""
import logging
import time
from PyQt6.QtCore import QObject, pyqtSignal, QTimer

from services.protocol_service import ProtocolService
from common.transport import VvAddress

log = logging.getLogger(__name__)

# Timeout for stale advertising devices (seconds)
_DEVICE_TIMEOUT_S = 5.0
# Delay after ble_scan_stop before sending ble_connect (ms)
_STOP_TO_CONNECT_DELAY_MS = 400
# Time sync drift threshold (ms) — auto-correct if exceeded
_TIME_SYNC_THRESHOLD_MS = 5000

_DEVICE_TYPE_LABELS = {
    0: "UNSPECIFIED",
    1: "TAG",
    2: "ANCHOR",
    3: "GATEWAY",
    4: "DEBUG_TOOL",
}


class DeviceModel(QObject):
    """
    Single source of truth cho device state.

    Signals emitted (consumed by ViewModel):
      - device_info_parsed(dict)         : parsed device_information_resp
      - battery_info_parsed(dict)        : parsed battery_info_resp
      - ble_status_parsed(dict)          : parsed ble_status_resp (state + rssi)
      - time_sync_result(dict)           : parsed time_sync_resp + host comparison
      - scan_data_updated(list)          : merged advertising device list
      - connection_state_changed(dict)   : connected/disconnected/connecting status
    """

    # ── Signals ──────────────────────────────────────────────────────
    device_info_parsed = pyqtSignal(dict)
    battery_info_parsed = pyqtSignal(dict)
    ble_status_parsed = pyqtSignal(dict)
    ble_conn_params_parsed = pyqtSignal(dict)
    time_sync_result = pyqtSignal(dict)       # {dev_time_ms, host_time_ms, tz_offset_sec, time_diff_ms, is_synced, was_corrected}
    scan_data_updated = pyqtSignal(list)      # merged advertising device list
    connection_state_changed = pyqtSignal(dict)  # {name, mac, status}
    anchor_layout_parsed = pyqtSignal(list)
    sys_config_parsed = pyqtSignal(dict)
    sys_ranging_cfg_parsed = pyqtSignal(dict)
    sensor_fusion_cfg_parsed = pyqtSignal(dict)
    pos_calib_cfg_parsed = pyqtSignal(dict)


    def __init__(self, protocol: ProtocolService, parent=None):
        super().__init__(parent)
        self._protocol = protocol

        # ── State (single source of truth) ───────────────────────────
        self._connected_mac = ""
        self._connected_name = ""
        self._is_scanning = False
        self._pending_connect_mac = ""

        # Advertising devices storage
        self._adv_devices = {}                  # mac_hex -> scan fields
        self._adv_status_by_device_id = {}      # device_id -> adv status fields

        # ── Protocol listener ────────────────────────────────────────
        self._protocol.packet_received.connect(self._on_packet)

        # ── Prune timer for stale advertising devices ────────────────
        self._prune_timer = QTimer(self)
        self._prune_timer.timeout.connect(self._prune_devices)
        
        # ── Serial Connection Lost Listener ─────────────────────────
        self._protocol._serial.connection_lost.connect(self.on_connection_lost)

    # ═══════════════════════════════════════════════════════════════════
    #  PUBLIC PROPERTIES (read-only access for ViewModel)
    # ═══════════════════════════════════════════════════════════════════

    @property
    def connected_mac(self) -> str:
        return self._connected_mac

    @property
    def connected_name(self) -> str:
        return self._connected_name

    @property
    def is_scanning(self) -> bool:
        return self._is_scanning

    @property
    def is_connected(self) -> bool:
        return bool(self._connected_mac)

    # ═══════════════════════════════════════════════════════════════════
    #  COMMAND METHODS (called by ViewModel)
    # ═══════════════════════════════════════════════════════════════════

    def set_connected_device(self, name: str, mac: str):
        """Called by main.py / ViewModel after ScanPopup to seed initial state."""
        self._connected_mac = mac
        self._connected_name = name
        self.connection_state_changed.emit({
            "name": name, "mac": mac, "status": "Connected", "SwitchToLogTab": True
        })
        log.info("Connected device set: %s (%s)", name, mac)


    def request_initial_telemetry(self):
        """Send GET commands once upon connection to fetch the baseline state.
        Further updates should be pushed automatically by the Firmware as events."""
        log.info("Requesting initial baseline telemetry from device...")
        
        # Requests intended for the MCU (battery, device info, time sync)
        self._protocol.send_command("device_information_get", dst_addr=VvAddress.MCU)
        self._protocol.send_command("battery_info_get", dst_addr=VvAddress.MCU)
        self._protocol.send_command("time_sync_get", dst_addr=VvAddress.MCU)
        
        # Requests intended for the BLE Central logic
        self._protocol.send_command("ble_status_get", dst_addr=VvAddress.CENTRAL)
        self._protocol.send_command("ble_conn_params_get", dst_addr=VvAddress.CENTRAL)

        # Requests intended for layout and other configs
        self._protocol.send_command("anchor_layout_get", dst_addr=VvAddress.MCU)
        self._protocol.send_command("sys_config_get", dst_addr=VvAddress.MCU)
        self._protocol.send_command("sys_ranging_cfg_get", dst_addr=VvAddress.MCU)
        self._protocol.send_command("sensor_fusion_cfg_get", dst_addr=VvAddress.MCU)
        self._protocol.send_command("pos_calib_cfg_get", dst_addr=VvAddress.MCU)


    def start_scan(self):
        """Start BLE advertising scan. Clears previous scan data."""
        self._adv_devices.clear()
        self._adv_status_by_device_id.clear()
        self.scan_data_updated.emit([])

        # Only send the start command if the dongle is not currently connected.
        # When connected, the central firmware automatically runs background scanning,
        # so sending this command would override the LED state of the dongle.
        if not self.is_connected:
            self._protocol.send_command(
                "ble_scan_start",
                src_addr=self._protocol.pb.PACKET_ADDR_HOST,
                dst_addr=self._protocol.pb.PACKET_ADDR_CENTRAL
            )
        else:
            log.info("Background scanning enabled locally (central is already scanning internally)")

        self._is_scanning = True
        self._prune_timer.start(5000)
        log.info("Background scan started")

    def stop_scan(self):
        """Stop BLE advertising scan."""
        if self._is_scanning:
            self._protocol.send_command(
                "ble_scan_stop",
                src_addr=self._protocol.pb.PACKET_ADDR_HOST,
                dst_addr=self._protocol.pb.PACKET_ADDR_CENTRAL
            )
            self._is_scanning = False
            self._prune_timer.stop()
            
            # Clear stale scan data since we are no longer listening
            self._adv_devices.clear()
            self._adv_status_by_device_id.clear()
            self.scan_data_updated.emit([])

    def connect_device(self, mac_hex: str):
        """
        Connect to a device from the advertising list.
        Flow: ble_disconnect old (if any) → delay → stop_scan → delay → ble_connect new
        """
        if not mac_hex:
            return

        log.info("Connect request: %s", mac_hex)
        name = self._adv_devices.get(mac_hex, {}).get("name", "Unknown")

        # Guard: Ignore if already connected to this exact device
        if self._connected_mac == mac_hex:
            log.info("Already connected to %s. Ignoring connect request.", mac_hex)
            return

        # Cancel any pending connect
        self._pending_connect_mac = mac_hex

        delay_ms = 0
        # 1) If already connected to a different device, disconnect first
        if self._connected_mac:
            try:
                self._protocol.send_command(
                    "ble_disconnect", 
                    src_addr=self._protocol.pb.PACKET_ADDR_HOST, 
                    dst_addr=self._protocol.pb.PACKET_ADDR_CENTRAL
                )
            except Exception:
                pass
            
            # Emit disconnecting state immediately
            self.connection_state_changed.emit({
                "name": self._connected_name, "mac": self._connected_mac, "status": "Disconnecting"
            })
            self._connected_mac = ""
            self._connected_name = ""
            delay_ms = 150 # Reduced delay

        # 2) Stop scan
        # Delay stop scan slightly if we just disconnected, to avoid command overlap
        QTimer.singleShot(delay_ms, self.stop_scan)

        total_delay = delay_ms + 350 # Faster transition (350ms instead of 400ms)

        # 3) After dongle finishes stopping, send ble_connect
        QTimer.singleShot(total_delay, lambda: self._do_connect(mac_hex, name))

    def _do_connect(self, mac_hex: str, name: str):
        """Actually send ble_connect after scan has stopped."""
        # Guard: make sure this is still the intended connect
        if self._pending_connect_mac != mac_hex:
            return

        try:
            mac_bytes = bytes.fromhex(mac_hex.replace(":", ""))
            self._protocol.send_command(
                "ble_connect", 
                src_addr=self._protocol.pb.PACKET_ADDR_HOST, 
                dst_addr=self._protocol.pb.PACKET_ADDR_CENTRAL, 
                mac_address=mac_bytes
            )
            log.info("ble_connect sent for %s (%s)", name, mac_hex)
        except Exception as e:
            log.error("ble_connect failed: %s", e)
            return

        # Update state + emit UI status
        self._connected_mac = mac_hex
        self._connected_name = name
        self.connection_state_changed.emit({
            "name": name, "mac": mac_hex, "status": "Connecting"
        })

        # Clear pending
        self._pending_connect_mac = ""

    def on_connection_lost(self):
        """Called when dongle is physically disconnected."""
        log.warning("Dongle physically disconnected!")
        self._connected_mac = ""
        self._connected_name = ""
        self._is_scanning = False
        self._prune_timer.stop()

        self.connection_state_changed.emit({
            "name": "-", "mac": "-", "status": "Disconnected"
        })

    # ═══════════════════════════════════════════════════════════════════
    #  PACKET HANDLER — Parse protocol responses into clean dicts
    # ═══════════════════════════════════════════════════════════════════

    def _on_packet(self, param_name: str, pkt):
        if param_name == "device_information_resp":
            self._handle_device_info(pkt.device_information_resp)
        elif param_name == "battery_info_resp":
            self._handle_battery_info(pkt.battery_info_resp)
        elif param_name == "ble_status_resp":
            self._handle_ble_status(pkt.ble_status_resp)
        elif param_name == "time_sync_resp":
            self._handle_time_sync(pkt.time_sync_resp)
        elif param_name == "ble_scan_result":
            self._handle_scan_result(pkt.ble_scan_result)
        elif param_name == "ble_adv_status":
            self._handle_adv_status(pkt.ble_adv_status)
        elif param_name == "ble_conn_params_resp":
            self._handle_ble_conn_params(pkt.ble_conn_params_resp)
        elif param_name == "anchor_layout_resp":
            self._handle_anchor_layout(pkt.anchor_layout_resp)
        elif param_name == "sys_config_resp":
            self._handle_sys_config(pkt.sys_config_resp)
        elif param_name == "sys_ranging_cfg_resp":
            self._handle_sys_ranging_cfg(pkt.sys_ranging_cfg_resp)
        elif param_name == "sensor_fusion_cfg_resp":
            self._handle_sensor_fusion_cfg(pkt.sensor_fusion_cfg_resp)
        elif param_name == "pos_calib_cfg_resp":
            self._handle_pos_calib_cfg(pkt.pos_calib_cfg_resp)


    def _handle_device_info(self, resp):
        device_type = getattr(resp, 'device_type', 0)
        
        # Map role according to device_role_t: 1 = TAG, 2 = ANCHOR
        role_val = getattr(resp, 'role', 0)
        if role_val == 1:
            role_str = "TAG"
        elif role_val == 2:
            role_str = "ANCHOR"
        else:
            role_str = "UNSPECIFIED"
            
        self.device_info_parsed.emit({
            "Type": _DEVICE_TYPE_LABELS.get(device_type, str(device_type)),
            "Role": role_str,
            "Serial Number": f"0x{resp.serial_number:08X}" if hasattr(resp, 'serial_number') else "-",
            "Firmware": f"v{resp.fw_version.major}.{resp.fw_version.minor}.{resp.fw_version.patch}",
            "Hardware Rev": str(getattr(resp, 'hw_version', '')),
        })

    def _handle_battery_info(self, resp):
        self.battery_info_parsed.emit({
            "bat_voltage_mv": getattr(resp, 'bat_voltage_mv', 0),
            "bat_soc_percent": getattr(resp, 'bat_soc_percent', 0),
            "remaining_min": getattr(resp, 'remaining_min', 0),
            "is_charging": getattr(resp, 'is_charging', False),
            "mcu_temp_c": getattr(resp, 'mcu_temp_c', 0.0),
            "vdda_mv": getattr(resp, 'vdda_mv', 0),
            "uwb_temp_c": getattr(resp, 'uwb_temp_c', 0.0),
            "uwb_vbat_mv": getattr(resp, 'uwb_vbat_mv', 0),
            "imu_temp_c": getattr(resp, 'imu_temp_c', 0.0),
            "error_mask": getattr(resp, 'error_mask', 0),
        })

    def _handle_ble_status(self, resp):
        state = getattr(resp, 'state', 0)
        rssi = getattr(resp, 'rssi_dbm', 0)

        self.ble_status_parsed.emit({
            "state": state,
            "rssi_dbm": rssi,
        })


        # ── Connection state machine ────────────────────────────────
        pb = self._protocol.pb

        if state == pb.BLE_STATE_CONNECTED and self._connected_mac:
            log.info("Dongle confirmed BLE_STATE_CONNECTED.")
            self.connection_state_changed.emit({
                "name": self._connected_name,
                "mac": self._connected_mac,
                "status": "Connected",
                "SwitchToLogTab": True,
            })
            # Resume scanning to populate 'Other Advertising Devices' table
            # Added 500ms delay to prevent firmware race condition which causes LED to turn off ("sáng xong tắt ngay")
            QTimer.singleShot(500, self.start_scan)

        elif state == pb.BLE_STATE_IDLE and self._connected_mac:
            log.warning("BLE_STATE_IDLE while device was connected — lost connection.")
            self._connected_mac = ""
            self._connected_name = ""
            self.connection_state_changed.emit({
                "name": "-", "mac": "-", "status": "Disconnected"
            })
            # Restart scan so user can find it again
            self.start_scan()

    def _handle_ble_conn_params(self, resp):
        p = getattr(resp, 'params', None)
        if p:
            self.ble_conn_params_parsed.emit({
                "min_interval_ms": getattr(p, 'min_interval_ms', 0),
                "max_interval_ms": getattr(p, 'max_interval_ms', 0),
                "slave_latency": getattr(p, 'slave_latency', 0),
                "sup_timeout_ms": getattr(p, 'sup_timeout_ms', 0),
            })

    def _handle_time_sync(self, resp):
        """Parse time_sync_resp, compare with host, auto-correct if drift > threshold."""
        dev_time_ms = getattr(resp, 'unix_time_ms', 0)
        host_time_ms = int(time.time() * 1000)

        # Calculate timezone offset
        local_time_struct = time.localtime()
        timezone_offset = getattr(time, 'timezone', 0)
        if getattr(time, 'daylight', 0) and local_time_struct.tm_isdst:
            timezone_offset = getattr(time, 'altzone', timezone_offset)
        tz_offset_sec = -timezone_offset

        time_diff_ms = abs(host_time_ms - dev_time_ms)
        is_synced = time_diff_ms <= _TIME_SYNC_THRESHOLD_MS
        was_corrected = False

        # Auto-correct if drift exceeds threshold
        if not is_synced:
            log.info("Time out of sync (diff %d ms). Sending time_sync_set...", time_diff_ms)
            try:
                self._protocol.send_command(
                    "time_sync_set",
                    dst_addr=VvAddress.PERIPHERAL,
                    unix_time_ms=host_time_ms,
                    timezone_offset=tz_offset_sec
                )
                was_corrected = True
            except Exception as e:
                log.warning("Failed to send time_sync_set: %s", e)

        self.time_sync_result.emit({
            "dev_time_ms": dev_time_ms,
            "host_time_ms": host_time_ms,
            "tz_offset_sec": tz_offset_sec,
            "time_diff_ms": time_diff_ms,
            "is_synced": is_synced,
            "was_corrected": was_corrected,
        })

    def _handle_anchor_layout(self, resp):
        anchors = []
        for a in resp.anchors:
            anchors.append({
                "anchor_id": a.anchor_id,
                "x_m": a.x_m,
                "y_m": a.y_m,
                "z_m": a.z_m,
            })
        self.anchor_layout_parsed.emit(anchors)

    def _handle_sys_config(self, resp):
        cfg = resp.config
        self.sys_config_parsed.emit({
            "role": cfg.role,
            "device_id": cfg.device_id,
            "ranging_period_ms": cfg.ranging_period_ms,
            "rx_timeout_ms": cfg.rx_timeout_ms,
            "uwb_channel": cfg.uwb_channel,
            "uwb_prf": cfg.uwb_prf,
            "uwb_data_rate": cfg.uwb_data_rate,
            "uwb_preamble_code": cfg.uwb_preamble_code,
            "tx_antenna_delay": cfg.tx_antenna_delay,
            "rx_antenna_delay": cfg.rx_antenna_delay,
            "tx_power": cfg.tx_power,
            "anchor_list": cfg.anchor_list,
            "power_mode": cfg.power_mode,
        })

    def _handle_sys_ranging_cfg(self, resp):
        cfg = resp.config
        self.sys_ranging_cfg_parsed.emit({
            "rx_timeout_ms": cfg.rx_timeout_ms,
            "ranging_period_ms": cfg.ranging_period_ms,
        })

    def _handle_sensor_fusion_cfg(self, resp):
        cfg = resp.config
        self.sensor_fusion_cfg_parsed.emit({
            "alpha": cfg.alpha,
            "kappa": cfg.kappa,
            "beta": cfg.beta,
            "q_a": cfg.q_a,
            "q_g": cfg.q_g,
            "r_uwb": cfg.r_uwb,
            "init_p_px": cfg.init_p_px,
            "init_p_py": cfg.init_p_py,
            "init_p_vx": cfg.init_p_vx,
            "init_p_vy": cfg.init_p_vy,
            "init_p_theta": cfg.init_p_theta,
            "init_p_bias_ax": cfg.init_p_bias_ax,
            "init_p_bias_ay": cfg.init_p_bias_ay,
            "init_p_bias_gz": cfg.init_p_bias_gz,
        })

    def _handle_pos_calib_cfg(self, resp):
        cfg = resp.config
        self.pos_calib_cfg_parsed.emit({
            "enable_anchor_auto_calib": cfg.enable_anchor_auto_calib,
            "enable_tag_auto_calib": cfg.enable_tag_auto_calib,
            "ref_distance_xy_m": cfg.ref_distance_xy_m,
            "tag_height_m": cfg.tag_height_m,
            "anchor_height_m": cfg.anchor_height_m,
            "calib_anchor_id": cfg.calib_anchor_id,
            "samples": cfg.samples,
            "error_threshold_m": cfg.error_threshold_m,
            "min_delta_step": cfg.min_delta_step,
            "max_rounds": cfg.max_rounds,
            "max_std_m": cfg.max_std_m,
            "damping": cfg.damping,
            "iterations": cfg.iterations,
        })


    # ── Scan result handling ─────────────────────────────────────────

    def _handle_scan_result(self, res):
        mac_hex = ":".join(f"{b:02X}" for b in res.mac_address)
        if mac_hex not in self._adv_devices:
            self._adv_devices[mac_hex] = {}
        self._adv_devices[mac_hex].update({
            "name": res.name or f"UWB-{mac_hex[-5:]}",
            "mac": mac_hex,
            "rssi": getattr(res, 'rssi_dbm', 0),
            "serial_number": getattr(res, 'serial_number', 0),
            "last_seen": time.monotonic()
        })
        self._emit_merged_scan_data()

    def _handle_adv_status(self, res):
        self._adv_status_by_device_id[res.device_id] = {
            "device_type": getattr(res, 'device', 0),
            "device_id": getattr(res, 'device_id', 0),
            "bat_soc_percent": getattr(res, 'bat_soc_percent', 0),
            "local_timestamp_ms": getattr(res, 'local_timestamp_ms', 0),
            "status_flags": getattr(res, 'status_flags', 0),
            "warning_count": getattr(res, 'warning_count', 0),
            "error_count": getattr(res, 'error_count', 0),
            "last_seen": time.monotonic()
        }
        self._emit_merged_scan_data()

    def _emit_merged_scan_data(self):
        """Merge scan results + adv_status and emit."""
        merged_list = []
        for d in self._adv_devices.values():
            sn = d.get("serial_number")
            adv_status = self._adv_status_by_device_id.get(sn, {}) if sn else {}
            item = d.copy()
            item.update(adv_status)
            merged_list.append(item)

        # Sort by MAC (stable order) to prevent UI row jumps
        merged_list.sort(key=lambda x: x["mac"])
        self.scan_data_updated.emit(merged_list)

    def _prune_devices(self):
        """Remove advertising devices not seen for > 15 seconds."""
        now = time.monotonic()
        stale_macs = [mac for mac, d in self._adv_devices.items()
                      if now - d.get("last_seen", 0) > _DEVICE_TIMEOUT_S]
        for mac in stale_macs:
            del self._adv_devices[mac]

        stale_ids = [did for did, d in self._adv_status_by_device_id.items()
                     if now - d.get("last_seen", 0) > _DEVICE_TIMEOUT_S]
        for did in stale_ids:
            del self._adv_status_by_device_id[did]

        if stale_macs or stale_ids:
            self._emit_merged_scan_data()
