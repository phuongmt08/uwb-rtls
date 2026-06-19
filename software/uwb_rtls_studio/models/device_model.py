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
from utils.app_state import shared_app_state, JobState
from utils.constants import (
    DEVICE_TIMEOUT_S,
    TIME_SYNC_THRESHOLD_MS,
    DEVICE_TYPE_LABELS,
)

log = logging.getLogger(__name__)


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
    sys_config_parsed = pyqtSignal(dict)
    sys_ranging_cfg_parsed = pyqtSignal(dict)
    sensor_fusion_cfg_parsed = pyqtSignal(dict)
    pos_calib_cfg_parsed = pyqtSignal(dict)


    def __init__(self, protocol: ProtocolService, telemetry_repo=None, ble_scan_repo=None, command_bus=None, parent=None):
        super().__init__(parent)
        self._protocol = protocol
        self._telemetry_repo = telemetry_repo
        self._ble_scan_repo = ble_scan_repo
        self._command_bus = command_bus
        
        # ── State (single source of truth) ───────────────────────────
        self._connected_mac = ""
        self._connected_name = ""
        self._connection_status = "Disconnected"
        self._is_scanning = False
        self._pending_connect_mac = ""
        self._session_bootstrap_done = False
        self._session_start_events_done = False
        self._log_stream_requested = False
        self._scan_device_order: dict[str, int] = {}
        self._next_scan_device_order = 0
        self._connected_grace_until = 0.0
        self._session_start_scheduled = False
        self._pending_target_operation = None

        # Advertising devices storage
        self._adv_devices = {}                  # mac_hex -> scan fields
        self._adv_status_by_device_id = {}      # device_id -> adv status fields

        # ── Protocol listener ────────────────────────────────────────
        self._protocol.packet_received.connect(self._on_packet)

        # ── Prune timer for stale advertising devices ────────────────
        self._prune_timer = QTimer(self)
        self._prune_timer.timeout.connect(self._prune_devices)
        
        # ── BLE status check timer (10s interval) ──────────────────
        self._ble_status_timer = QTimer(self)
        self._ble_status_timer.setInterval(10000)
        self._ble_status_timer.timeout.connect(self._poll_ble_status)

        self._session_bootstrap_timer = QTimer(self)
        self._session_bootstrap_timer.setSingleShot(True)
        self._session_bootstrap_timer.timeout.connect(self._run_scheduled_session_start)

        # ── Serial Connection Lost Listener ─────────────────────────
        self._protocol._serial.connection_lost.connect(self.on_connection_lost)

    def _request_query(self, command_name: str, dst_addr: int, **kwargs):
        cache_ttl_s = kwargs.pop("cache_ttl_s", None)
        force = kwargs.pop("force", False)
        if self._command_bus:
            return self._command_bus.request(
                command_name,
                dst_addr=dst_addr,
                cache_ttl_s=cache_ttl_s,
                force=force,
                **kwargs,
            )
        shared_app_state.enqueue_query(command_name, dst_addr=dst_addr, **kwargs)
        return True

    def _send_command(self, command_name: str, dst_addr: int, **kwargs):
        if self._command_bus:
            return self._command_bus.send(command_name, dst_addr=dst_addr, **kwargs)
        return self._protocol.send_command(command_name, dst_addr=dst_addr, **kwargs)

    def send_command(self, command_name: str, dst_addr: int = VvAddress.CENTRAL, **kwargs):
        """Public model command path used by ViewModels when no CommandBus is injected."""
        return self._send_command(command_name, dst_addr=dst_addr, **kwargs)

    def request_end_session(self, reason: int = 0):
        """Request firmware/session shutdown through the shared command path."""
        # BE/API: session lifecycle action owned by Device Info flow.
        return self._send_command("end_session", dst_addr=VvAddress.MCU, reason=reason)

    def request_ble_disconnect(self, reason: int = 0):
        """Disconnect current BLE peripheral through the shared command path."""
        # BE/API: BLE lifecycle action owned by Device Info flow.
        return self._send_command("ble_disconnect", dst_addr=VvAddress.CENTRAL, reason=reason)

    def request_anchor_layout(self):
        # BE/API: legacy backend helper for Config/Calibration orchestration.
        return self._request_query("anchor_layout_get", dst_addr=VvAddress.MCU)

    def set_anchor_layout(self, anchors: list):
        # BE/API: legacy backend helper for Config/Calibration orchestration.
        return self._send_command("anchor_layout_set", dst_addr=VvAddress.MCU, anchors=anchors)

    def request_ranging_config(self):
        # BE/API: legacy backend helper for Config tab orchestration.
        return self._request_query("sys_ranging_cfg_get", dst_addr=VvAddress.MCU)

    def set_ranging_config(self, period_ms: int, timeout_ms: int):
        # BE/API: legacy backend helper for Config tab orchestration.
        shared_app_state.sys_ranging_cfg = {
            "ranging_period_ms": period_ms,
            "rx_timeout_ms": timeout_ms,
        }
        return self._send_command(
            "sys_ranging_cfg_set",
            dst_addr=VvAddress.MCU,
            period_ms=period_ms,
            timeout_ms=timeout_ms,
        )

    def request_sys_config(self):
        # BE/API: legacy backend helper for Config tab orchestration.
        return self._request_query("sys_config_get", dst_addr=VvAddress.MCU)

    def set_sys_config(self, **kwargs):
        # BE/API: legacy backend helper for Config tab orchestration.
        return self._send_command("sys_config_set", dst_addr=VvAddress.MCU, **kwargs)

    def request_sensor_fusion_config(self):
        # BE/API: legacy backend helper for Config tab orchestration.
        return self._request_query("sensor_fusion_cfg_get", dst_addr=VvAddress.MCU)

    def set_sensor_fusion_config(self, **kwargs):
        # BE/API: legacy backend helper for Config tab orchestration.
        return self._send_command("sensor_fusion_cfg_set", dst_addr=VvAddress.MCU, **kwargs)

    def request_pos_calib_config(self):
        # BE/API: legacy backend helper for Config tab orchestration.
        return self._request_query("pos_calib_cfg_get", dst_addr=VvAddress.MCU)

    def set_pos_calib_config(self, **kwargs):
        # BE/API: legacy backend helper for Config tab orchestration.
        return self._send_command("pos_calib_cfg_set", dst_addr=VvAddress.MCU, **kwargs)

    def request_ble_conn_params(self):
        # BE/API: backend helper for Device Info BLE connection parameters.
        return self._request_query("ble_conn_params_get", dst_addr=VvAddress.CENTRAL)

    def set_ble_conn_params(
        self,
        min_interval_ms: int,
        max_interval_ms: int,
        slave_latency: int,
        sup_timeout_ms: int,
    ):
        # BE/API: backend helper for BLE connection parameter updates.
        return self._send_command(
            "ble_conn_params_set",
            dst_addr=VvAddress.CENTRAL,
            min_interval_ms=min_interval_ms,
            max_interval_ms=max_interval_ms,
            slave_latency=slave_latency,
            sup_timeout_ms=sup_timeout_ms,
        )

    def request_device_reset(self):
        # BE/API: lifecycle action exposed to Config tab.
        return self._send_command("device_reset", dst_addr=VvAddress.MCU)

    def request_uwb_reset(self):
        # BE/API: lifecycle action exposed to Config tab.
        return self._send_command("uwb_reset", dst_addr=VvAddress.MCU)

    def request_factory_config_reset(self):
        # BE/API: lifecycle action exposed to Config tab.
        return self._send_command("factory_config_reset", dst_addr=VvAddress.MCU)

    def request_enter_bootloader(self):
        # BE/API: lifecycle action exposed to Config tab.
        return self._send_command("enter_to_bootloader", dst_addr=VvAddress.MCU)

    def request_calibration_status(self):
        return self._request_query(
            "calib_status_get",
            dst_addr=VvAddress.MCU,
            cache_ttl_s=0.0,
            force=False,
        )

    def request_imu_reset(self):
        return self._send_command("imu_reset", dst_addr=VvAddress.MCU)

    def request_imu_calibration(self):
        return self._send_command("imu_calib_start", dst_addr=VvAddress.MCU)

    def execute_for_target(self, target: dict | None, operation):
        """
        Run a config operation against the selected BLE peripheral.

        Current GET messages have no device-id field, so another scanned
        target must be connected before its MCU configuration can be queried.
        """
        target = dict(target or {})
        target_mac = self._normalize_mac(target.get("mac", ""))
        connected_mac = self._normalize_mac(self._connected_mac)

        if not target_mac or target_mac == connected_mac:
            operation()
            return True

        self._pending_target_operation = {
            "mac": target_mac,
            "operation": operation,
        }
        self.connect_device(target_mac)
        return True

    @staticmethod
    def _normalize_mac(mac: str) -> str:
        return str(mac or "").strip().replace("-", ":").upper()

    def _run_pending_target_operation(self):
        pending = self._pending_target_operation
        if not pending:
            return
        if self._normalize_mac(self._connected_mac) != pending["mac"]:
            return

        self._pending_target_operation = None
        try:
            pending["operation"]()
        except Exception:
            log.exception("Target config operation failed for %s", pending["mac"])

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
        self._connection_status = "Connected"
        self._session_bootstrap_done = False
        self._session_start_events_done = False
        self._log_stream_requested = False
        self._connected_grace_until = time.monotonic() + 1.5
        self._session_start_scheduled = False
        self.connection_state_changed.emit({
            "name": name, "mac": mac, "status": "Connected", "SwitchToLogTab": True
        })
        log.info("Connected device set: %s (%s)", name, mac)

        # BE/API: confirm connection state from dongle after seeding the device.
        self._request_query("ble_status_get", dst_addr=VvAddress.CENTRAL, cache_ttl_s=0.0, force=True)

        # Start periodic BLE status polling (10s interval)
        if not self._ble_status_timer.isActive():
            self._ble_status_timer.start()

        # Start session bootstrap after a short grace period.
        self.schedule_session_start(delay_ms=1500, force=True)

    def schedule_session_start(self, delay_ms: int = 1500, force: bool = False):
        """Schedule the initial telemetry bootstrap after connect/reconnect."""
        if self._session_start_scheduled and not force:
            return False
        self._session_start_scheduled = True
        self._session_bootstrap_timer.start(max(0, delay_ms))
        return True

    def _run_scheduled_session_start(self):
        """Run once after connect grace period so early MCU/Central APIs do not race BLE setup."""
        self.request_initial_telemetry()
        self.request_session_start_events()

    def request_initial_telemetry(self, force: bool = False):
        """Fetch baseline/static state once after a device session starts."""
        if self._session_bootstrap_done and not force:
            log.info("Initial session bootstrap already requested; skipping duplicate startup queries.")
            return False

        log.info("Requesting initial device-session telemetry...")
        self._session_bootstrap_done = True
        
        shared_app_state.update_job("initial_telemetry", JobState.RUNNING)
        
        # BE/API: session bootstrap queries owned by Device Info.
        self._request_query("device_information_get", dst_addr=VvAddress.MCU)
        self._sync_host_time_once()

        # Load the connected device configuration once. Repository signals
        # remain the source of all response-driven UI updates.
        self._request_query("anchor_layout_get", dst_addr=VvAddress.MCU)
        self._request_query("sys_config_get", dst_addr=VvAddress.MCU)
        self._request_query("sys_ranging_cfg_get", dst_addr=VvAddress.MCU)
        self._request_query("sensor_fusion_cfg_get", dst_addr=VvAddress.MCU)
        self._request_query("pos_calib_cfg_get", dst_addr=VvAddress.MCU)
        
        # BE/API: confirm dongle BLE state for the current device session.
        self._request_query("ble_status_get", dst_addr=VvAddress.CENTRAL)
        return True

    def _sync_host_time_once(self):
        """Set host time after connect, then verify it with one GET."""
        host_time_ms = int(time.time() * 1000)
        local_time_struct = time.localtime()
        timezone_offset = getattr(time, "timezone", 0)
        if getattr(time, "daylight", 0) and local_time_struct.tm_isdst:
            timezone_offset = getattr(time, "altzone", timezone_offset)
        tz_offset_min = int((-timezone_offset) / 60)

        self._send_command(
            "time_sync_set",
            dst_addr=VvAddress.MCU,
            unix_time_ms=host_time_ms,
            timezone_offset=tz_offset_min,
        )
        QTimer.singleShot(
            200,
            lambda: self._request_query(
                "time_sync_get",
                dst_addr=VvAddress.MCU,
                cache_ttl_s=0.0,
                force=True,
            ),
        )

    def request_session_start_events(self, force: bool = False):
        """Trigger session-start data events that should be fetched once per connection."""
        if self._session_start_events_done and not force:
            log.info("Session start events already requested; skipping duplicate event queries.")
            return False

        log.info("Requesting Device Info session-start data...")
        self._session_start_events_done = True
        shared_app_state.update_job("session_start_events", JobState.SUCCESS)
        # BE/API: Device Info telemetry snapshot for the connected device.
        self._request_query("battery_info_get", dst_addr=VvAddress.MCU, cache_ttl_s=0.0, force=True)
        # BE/API: Device Info BLE connection parameter snapshot.
        self._request_query("ble_conn_params_get", dst_addr=VvAddress.CENTRAL, cache_ttl_s=0.0, force=True)
        return True

    def request_log_stream(self, force: bool = False):
        """Trigger firmware/device log streaming for the current connected device."""
        if not self._connected_mac:
            return False
        if self._log_stream_requested and not force:
            return False
        self._log_stream_requested = True
        # BE/API: incoming log_data packets update UI; this call is only the stream trigger.
        return self._send_command("log_data", dst_addr=VvAddress.MCU)


    def start_scan(self, clear_results: bool = False):
        """Start or resume BLE advertising scan for the current UI surface."""
        if clear_results:
            self._clear_scan_cache()

        if not self._is_scanning:
            self._send_command(
                "ble_scan_start",
                src_addr=self._protocol.pb.PACKET_ADDR_HOST,
                dst_addr=self._protocol.pb.PACKET_ADDR_CENTRAL
            )
            self._is_scanning = True

        self._prune_timer.start(5000)
        log.info("Background scan started")

    def stop_scan(self):
        """Stop BLE advertising scan."""
        if self._is_scanning:
            self._send_command(
                "ble_scan_stop",
                src_addr=self._protocol.pb.PACKET_ADDR_HOST,
                dst_addr=self._protocol.pb.PACKET_ADDR_CENTRAL
            )
            self._is_scanning = False
            self._prune_timer.stop()
            
            # Clear stale scan data since we are no longer listening
            self._adv_devices.clear()
            self._adv_status_by_device_id.clear()
            self._scan_device_order.clear()
            self._next_scan_device_order = 0
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
                self._send_command(
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
            self._connection_status = "Disconnecting"
            self._connected_mac = ""
            self._connected_name = ""
            self._session_bootstrap_done = False
            self._session_start_events_done = False
            self._log_stream_requested = False
            self._session_start_scheduled = False
            self._connected_grace_until = 0.0
            self._ble_status_timer.stop()
            self._session_bootstrap_timer.stop()
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
            self._send_command(
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
        self._connection_status = "Connecting"
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
        self._connection_status = "Disconnected"
        self._is_scanning = False
        self._session_bootstrap_done = False
        self._session_start_events_done = False
        self._log_stream_requested = False
        self._session_start_scheduled = False
        self._connected_grace_until = 0.0
        self._prune_timer.stop()
        self._ble_status_timer.stop()
        self._session_bootstrap_timer.stop()
        self._pending_target_operation = None

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
            
        info = {
            "Type": DEVICE_TYPE_LABELS.get(device_type, str(device_type)),
            "Role": role_str,
            "Serial Number": f"0x{resp.serial_number:08X}" if hasattr(resp, 'serial_number') else "-",
            "Firmware": f"v{resp.fw_version.major}.{resp.fw_version.minor}.{resp.fw_version.patch}",
            "Hardware Rev": str(getattr(resp, 'hw_version', '')),
            "UID": getattr(resp, "uid", b"").hex().upper() if getattr(resp, "uid", b"") else "-",
        }
        self.device_info_parsed.emit(info)
        
        dev = shared_app_state.connected_device
        dev.update(info)
        shared_app_state.connected_device = dev

    def _handle_battery_info(self, resp):
        present_fields = {field.name for field, _ in resp.ListFields()}

        def value_or_none(name: str):
            if name not in present_fields:
                return None
            return getattr(resp, name)

        info = {
            "bat_voltage_mv": value_or_none("bat_voltage_mv"),
            "bat_soc_percent": value_or_none("bat_soc_percent"),
            "remaining_min": value_or_none("remaining_min"),
            "is_charging": value_or_none("is_charging"),
            "mcu_temp_c": value_or_none("mcu_temp_c"),
            "mcu_voltage_mv": value_or_none("mcu_voltage_mv"),
            "vdda_mv": value_or_none("mcu_voltage_mv"),
            "uwb_temp_c": value_or_none("uwb_temp_c"),
            "uwb_voltage_mv": value_or_none("uwb_voltage_mv"),
            "uwb_vbat_mv": value_or_none("uwb_voltage_mv"),
            "imu_temp_c": value_or_none("imu_temp_c"),
            "error_mask": value_or_none("error_mask"),
        }
        info = {key: value for key, value in info.items() if value is not None}
        self.battery_info_parsed.emit(info)
        if not self._telemetry_repo:
            shared_app_state.battery_info = info

    def _handle_ble_status(self, resp):
        state = getattr(resp, 'state', 0)
        rssi = getattr(resp, 'rssi_dbm', 0)

        # Log received BLE status state on terminal
        BLE_STATE_NAMES = {
            0: "UNSPECIFIED",
            1: "IDLE",
            2: "SCANNING",
            3: "ADVERTISING",
            4: "CONNECTING",
            5: "CONNECTED"
        }
        state_str = BLE_STATE_NAMES.get(state, f"UNKNOWN({state})")
        log.info("Received ble_status_resp: state=%d (%s), rssi=%d dBm", state, state_str, rssi)

        ble_info = {
            "state": state,
            "rssi_dbm": rssi,
        }
        self.ble_status_parsed.emit(ble_info)
        
        # Write to Shared App State
        curr_ble = shared_app_state.ble_status
        curr_ble.update(ble_info)
        shared_app_state.ble_status = curr_ble

        # ── Connection state machine ────────────────────────────────
        pb = self._protocol.pb

        if state == pb.BLE_STATE_CONNECTED and self._connected_mac:
            log.info("Dongle confirmed BLE_STATE_CONNECTED.")
            if self._connection_status != "Connected":
                self._connection_status = "Connected"
                self._session_bootstrap_done = False
                self._session_start_events_done = False
                self._log_stream_requested = False
                shared_app_state.connection_status = "Connected"
                dev_info = shared_app_state.connected_device
                dev_info.update({"name": self._connected_name, "mac": self._connected_mac})
                shared_app_state.connected_device = dev_info

                self.connection_state_changed.emit({
                    "name": self._connected_name,
                    "mac": self._connected_mac,
                    "status": "Connected",
                    "SwitchToLogTab": True,
                })
                
                # Start/Restart the periodic BLE status checking timer
                if not self._ble_status_timer.isActive():
                    self._ble_status_timer.start()

                # NOTE: Do NOT auto-start scan here.
                # Scanning causes dongle firmware to exit CONNECTED LED state.
                # User can manually start scan from UI if needed.

            QTimer.singleShot(250, self._run_pending_target_operation)

        elif self._connected_mac and state not in (
            pb.BLE_STATE_CONNECTED,
            pb.BLE_STATE_CONNECTING,
            pb.BLE_STATE_SCANNING,
        ):
            log.warning("BLE state changed to %d while device was connected — lost connection.", state)
            self._connected_mac = ""
            self._connected_name = ""
            self._connection_status = "Disconnected"
            self._session_bootstrap_done = False
            self._session_start_events_done = False
            self._log_stream_requested = False
            self._session_start_scheduled = False
            shared_app_state.connection_status = "Disconnected"
            shared_app_state.connected_device = {}
            self._ble_status_timer.stop()
            self._session_bootstrap_timer.stop()
            self._pending_target_operation = None
            self.connection_state_changed.emit({
                "name": "-", "mac": "-", "status": "Disconnected"
            })
            # Restart scan so user can find it again
            self.start_scan()

    def _poll_ble_status(self):
        """Poll BLE status from Central device (dongle) periodically."""
        if self._connected_mac:
            log.debug("Polling BLE status from dongle...")
            try:
                self._request_query("ble_status_get", dst_addr=VvAddress.CENTRAL, cache_ttl_s=0.0, force=True)
            except Exception as e:
                log.error("Failed to send ble_status_get: %s", e)

    def _handle_ble_conn_params(self, resp):
        p = getattr(resp, 'params', None)
        if p:
            self.ble_conn_params_parsed.emit({
                "min_interval_ms": getattr(p, 'min_interval_ms', 0),
                "max_interval_ms": getattr(p, 'max_interval_ms', 0),
                "slave_latency": getattr(p, 'slave_latency', 0),
                "sup_timeout_ms": getattr(p, 'sup_timeout_ms', 0),
                "phy": getattr(p, 'phy', "-"),
            })

    def _handle_time_sync(self, resp):
        """Publish the event-driven time-sync response."""
        dev_time_ms = getattr(resp, 'unix_time_ms', 0)
        host_time_ms = int(time.time() * 1000)

        # Calculate timezone offset
        local_time_struct = time.localtime()
        timezone_offset = getattr(time, 'timezone', 0)
        if getattr(time, 'daylight', 0) and local_time_struct.tm_isdst:
            timezone_offset = getattr(time, 'altzone', timezone_offset)
        tz_offset_sec = -timezone_offset
        tz_offset_min = int(tz_offset_sec / 60)

        time_diff_ms = abs(host_time_ms - dev_time_ms)
        is_synced = time_diff_ms <= TIME_SYNC_THRESHOLD_MS

        self.time_sync_result.emit({
            "dev_time_ms": dev_time_ms,
            "host_time_ms": host_time_ms,
            "tz_offset_sec": tz_offset_sec,
            "tz_offset_min": tz_offset_min,
            "time_diff_ms": time_diff_ms,
            "is_synced": is_synced,
            "was_corrected": False,
        })

    def _handle_sys_config(self, resp):
        if not resp.HasField("config"):
            log.warning("Received sys_config_resp without config submessage.")
            return
        cfg = resp.config
        cfg_dict = {
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
        }
        self.sys_config_parsed.emit(cfg_dict)

    def _handle_sys_ranging_cfg(self, resp):
        if not resp.HasField("config"):
            log.warning("Received sys_ranging_cfg_resp without config submessage.")
            return
        cfg = resp.config
        cfg_dict = {
            "rx_timeout_ms": cfg.rx_timeout_ms,
            "ranging_period_ms": cfg.ranging_period_ms,
        }
        self.sys_ranging_cfg_parsed.emit(cfg_dict)

    def _handle_sensor_fusion_cfg(self, resp):
        if not resp.HasField("config"):
            log.warning("Received sensor_fusion_cfg_resp without config submessage.")
            return
        cfg = resp.config
        cfg_dict = {
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
        }
        self.sensor_fusion_cfg_parsed.emit(cfg_dict)

    def _handle_pos_calib_cfg(self, resp):
        if not resp.HasField("config"):
            log.warning("Received pos_calib_cfg_resp without config submessage.")
            return
        cfg = resp.config
        cfg_dict = {
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
        }
        self.pos_calib_cfg_parsed.emit(cfg_dict)


    # ── Scan result handling ─────────────────────────────────────────

    def _handle_scan_result(self, res):
        mac_hex = ":".join(f"{b:02X}" for b in res.mac_address)
        if mac_hex not in self._adv_devices:
            self._adv_devices[mac_hex] = {}
        device_data = {
            "name": res.name or f"UWB-{mac_hex[-5:]}",
            "mac": mac_hex,
            "rssi": getattr(res, 'rssi_dbm', 0),
            "serial_number": getattr(res, 'serial_number', 0),
            "last_seen": time.monotonic()
        }
        if mac_hex not in self._scan_device_order:
            self._scan_device_order[mac_hex] = self._next_scan_device_order
            self._next_scan_device_order += 1
        device_data["order"] = self._scan_device_order[mac_hex]
        self._adv_devices[mac_hex].update(device_data)
        self._emit_merged_scan_data()

    def _handle_adv_status(self, res):
        status_data = {
            "device_type": getattr(res, 'device', 0),
            "device_id": getattr(res, 'device_id', 0),
            "bat_soc_percent": getattr(res, 'bat_soc_percent', 0),
            "local_timestamp_ms": getattr(res, 'local_timestamp_ms', 0),
            "status_flags": getattr(res, 'status_flags', 0),
            "warning_count": getattr(res, 'warning_count', 0),
            "error_count": getattr(res, 'error_count', 0),
            "last_seen": time.monotonic()
        }
        self._adv_status_by_device_id[res.device_id] = status_data
        
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

        merged_list.sort(key=lambda x: x.get("order", 0))
        self.scan_data_updated.emit(merged_list)

    def _prune_devices(self):
        """Remove advertising devices not seen for > 15 seconds."""
        now = time.monotonic()
        stale_macs = [mac for mac, d in self._adv_devices.items()
                      if now - d.get("last_seen", 0) > DEVICE_TIMEOUT_S]
        for mac in stale_macs:
            del self._adv_devices[mac]
            self._scan_device_order.pop(mac, None)

        stale_ids = [did for did, d in self._adv_status_by_device_id.items()
                     if now - d.get("last_seen", 0) > DEVICE_TIMEOUT_S]
        for did in stale_ids:
            del self._adv_status_by_device_id[did]

        if self._ble_scan_repo:
            self._ble_scan_repo.prune_stale_devices(DEVICE_TIMEOUT_S)

        if stale_macs or stale_ids:
            self._emit_merged_scan_data()

    def _clear_scan_cache(self) -> None:
        self._adv_devices.clear()
        self._adv_status_by_device_id.clear()
        self._scan_device_order.clear()
        self._next_scan_device_order = 0
        if self._ble_scan_repo and hasattr(self._ble_scan_repo, "clear"):
            self._ble_scan_repo.clear()
        self.scan_data_updated.emit([])
