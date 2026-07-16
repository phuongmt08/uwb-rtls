"""
===============================================================================
  UWB RTLS Studio - Unified Application State & Thread Registry
===============================================================================
  File        : utils/app_state.py
  Description : Centralized state management ("Shared Memory"), Job State Machine,
                Thread Registry, and central retry/timeout configuration.
                This allows all tabs to synchronize states seamlessly.
===============================================================================
"""
from __future__ import annotations

import logging
import re
import threading
from typing import Dict, Any, List, Optional, Callable, Tuple
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from services.query_state_machine import QueryQueueManager, QueryState
from utils.command_flags import is_command_enabled

log = logging.getLogger(__name__)

# Centralized Retry & Timeout Configurations
# Modifying these values updates retry/timeout behavior across the entire app.
QUERY_TIMEOUT_S = 2.5          # BLE response wait; avoids false retries on slow/fragmented replies
QUERY_MAX_RETRIES = 0          # Immediate per-command retry is disabled; recovery runs by wave.
# GET recovery is bounded by wave. A failed command is retried only after the
# current flow has reported all commands that were actually sent.
QUERY_RECOVERY_MAX_WAVES = 3

# Polling intervals in milliseconds
POLL_BATTERY_MS = 10000        # Battery polling interval (10s)
POLL_BLE_STATUS_MS = 10000      # BLE status polling interval (10s)
POLL_RANGING_STATUS_MS = 5000  # Ranging statistics polling interval (5s)
POLL_CALIB_STATUS_MS = 2000    # Calibration progress polling interval (2s)


class JobState:
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    RETRYING = "RETRYING"


class ThreadRegistry:
    """Registry to track and safely monitor/manage all background threads and workers."""
    def __init__(self):
        self._threads: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def register(self, name: str, thread_obj: Any) -> None:
        with self._lock:
            self._threads[name] = thread_obj
            log.info(f"[ThreadRegistry] Registered thread: '{name}' ({type(thread_obj).__name__})")

    def unregister(self, name: str) -> None:
        with self._lock:
            if name in self._threads:
                del self._threads[name]
                log.info(f"[ThreadRegistry] Unregistered thread: '{name}'")

    def is_running(self, name: str) -> bool:
        with self._lock:
            thread_obj = self._threads.get(name)
            if not thread_obj:
                return False
            # Supports both QThread and standard python Thread
            if hasattr(thread_obj, "isRunning"):
                return thread_obj.isRunning()
            elif hasattr(thread_obj, "is_alive"):
                return thread_obj.is_alive()
            return False

    def get_thread(self, name: str) -> Optional[Any]:
        with self._lock:
            return self._threads.get(name)

    def stop_all(self) -> None:
        with self._lock:
            log.info("[ThreadRegistry] Stopping all registered threads...")
            for name, thread_obj in list(self._threads.items()):
                try:
                    if hasattr(thread_obj, "stop"):
                        thread_obj.stop()
                    if hasattr(thread_obj, "quit"):
                        thread_obj.quit()
                    if hasattr(thread_obj, "wait"):
                        thread_obj.wait(1000)
                except Exception as e:
                    log.error(f"[ThreadRegistry] Error stopping thread '{name}': {e}")


class SharedAppState(QObject):
    """
    Singleton Shared Memory & Reactive State.
    Any tab can read/write and connect to signals to stay fully synchronized.
    """
    # Reactive State Signals
    connection_status_changed = pyqtSignal(str)   # "Disconnected", "Connecting", "Connected"
    connected_device_changed = pyqtSignal(dict)    # Device info (mac, name, role, fw_version, hw_version, serial)
    battery_info_changed = pyqtSignal(dict)       # Voltage, SOC, remains, charging, temps...
    ble_status_changed = pyqtSignal(dict)          # BLE state, rssi, disconnect reason
    ranging_active_changed = pyqtSignal(bool)      # Ranging active/stopped
    log_streaming_changed = pyqtSignal(bool)       # Firmware log stream active/stopped
    ble_scan_active_changed = pyqtSignal(bool)     # User-triggered BLE scan active/stopped
    ranging_stats_changed = pyqtSignal(dict)       # total_count, success_count, rms_error_m...
    calib_status_changed = pyqtSignal(dict)         # state, progress, iteration, peer ready mask...
    anchor_layout_changed = pyqtSignal(list)       # List of fixed anchors positions
    zone_profiles_changed = pyqtSignal(dict)       # zone_id -> zone profile/anchor layout from firmware
    sys_config_changed = pyqtSignal(dict)          # UWB role, channel, tx/rx antenna delays...
    sys_ranging_cfg_changed = pyqtSignal(dict)     # Rx timeout, ranging period
    sensor_fusion_cfg_changed = pyqtSignal(dict)   # alpha, kappa, noise covariances...
    prefilter_cfg_changed = pyqtSignal(dict)       # Positioning prefilter thresholds
    pos_calib_cfg_changed = pyqtSignal(dict)       # Auto calibration parameters
    rtos_resource_changed = pyqtSignal(dict)       # CPU, heap, stack, task count, health flags
    rtos_task_stats_changed = pyqtSignal(list)     # Per-task CPU and stack snapshots
    manual_test_mode_changed = pyqtSignal(bool)    # Communication tab test-mode gate
    device_type_changed = pyqtSignal(int)          # Device type (Tag=1, Anchor=2, Gateway=3, Debug=4)
    device_session_reset = pyqtSignal(str)      # Emitted before switching away from a device
    query_notification_requested = pyqtSignal(dict)  # Toast notification for query/NACK issues
    query_flow_completed = pyqtSignal(str)  # Emitted once a flow has printed its final report

    # Job State Machine signal
    # Params: job_name, status, progress (0-100), retries, error_msg
    job_state_changed = pyqtSignal(str, str, int, int, str)

    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        with SharedAppState._lock:
            if SharedAppState._instance is not None:
                return
            super().__init__()
            SharedAppState._instance = self
            self._initialized = True
            self.threads = ThreadRegistry()

        # State Store (Private Variables)
        self._connection_status = "Disconnected"
        self._connected_device: Dict[str, Any] = {}
        self._battery_info: Dict[str, Any] = {}
        self._ble_status: Dict[str, Any] = {}
        self._ranging_active = False
        self._log_streaming = False
        self._ble_scan_active = False
        self.current_session_id = ""
        self._ranging_stats: Dict[str, Any] = {}
        self._calib_status: Dict[str, Any] = {}
        self._anchor_layout: List[Dict[str, Any]] = []
        self._zone_profiles: Dict[int, Dict[str, Any]] = {}
        self._sys_config: Dict[str, Any] = {}
        self._sys_ranging_cfg: Dict[str, Any] = {}
        self._sensor_fusion_cfg: Dict[str, Any] = {}
        self._prefilter_cfg: Dict[str, Any] = {}
        self._pos_calib_cfg: Dict[str, Any] = {}
        self._rtos_resource: Dict[str, Any] = {}
        self._rtos_task_stats: List[Dict[str, Any]] = []
        self._manual_test_mode_enabled = False
        self._device_type = 0

        # Job State Machine storage
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._query_manager: QueryQueueManager | None = None
        self._query_generation = 0
        self._query_recovery_attempts: Dict[str, int] = {}
        self._query_flow_results: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._last_query_flow_reports: Dict[str, List[Dict[str, Any]]] = {}
        self._query_recovery_pending = False
        self._deferred_background_queries: Dict[Tuple[str, int, str], Dict[str, Any]] = {}
        self._deferred_manual_flow_queries: Dict[Tuple[str, int, str], Dict[str, Any]] = {}
        self._manual_flow_active = ""
        self._received_payload_names: set[str] = set()
        self._active_query_flow = ""
        self._query_start_scheduled = False
        self._device_session_payloads_enabled = False

    # Getters / Setters with Reactive Signaling

    @property
    def connection_status(self) -> str:
        return self._connection_status

    @connection_status.setter
    def connection_status(self, val: str) -> None:
        if self._connection_status != val:
            self._connection_status = val
            self.connection_status_changed.emit(val)

    @property
    def connected_device(self) -> Dict[str, Any]:
        return self._connected_device.copy()

    @connected_device.setter
    def connected_device(self, val: Dict[str, Any]) -> None:
        self._connected_device = val.copy()
        self.connected_device_changed.emit(self._connected_device)

    @property
    def battery_info(self) -> Dict[str, Any]:
        return self._battery_info.copy()

    @battery_info.setter
    def battery_info(self, val: Dict[str, Any]) -> None:
        self._battery_info = val.copy()
        self.battery_info_changed.emit(self._battery_info)

    @property
    def ble_status(self) -> Dict[str, Any]:
        return self._ble_status.copy()

    @ble_status.setter
    def ble_status(self, val: Dict[str, Any]) -> None:
        self._ble_status = val.copy()
        self.ble_status_changed.emit(self._ble_status)

    @property
    def ranging_active(self) -> bool:
        return self._ranging_active

    @ranging_active.setter
    def ranging_active(self, val: bool) -> None:
        if self._ranging_active != val:
            self._ranging_active = val
            self.ranging_active_changed.emit(val)

    @property
    def ble_scan_active(self) -> bool:
        return self._ble_scan_active

    @ble_scan_active.setter
    def ble_scan_active(self, val: bool) -> None:
        enabled = bool(val)
        if self._ble_scan_active != enabled:
            self._ble_scan_active = enabled
            self.ble_scan_active_changed.emit(enabled)

    @property
    def ranging_stats(self) -> Dict[str, Any]:
        return self._ranging_stats.copy()

    @ranging_stats.setter
    def ranging_stats(self, val: Dict[str, Any]) -> None:
        self._ranging_stats = val.copy()
        self.ranging_stats_changed.emit(self._ranging_stats)

    @property
    def log_streaming(self) -> bool:
        return self._log_streaming

    @log_streaming.setter
    def log_streaming(self, val: bool) -> None:
        enabled = bool(val)
        if self._log_streaming != enabled:
            self._log_streaming = enabled
            self.log_streaming_changed.emit(enabled)

    @property
    def calib_status(self) -> Dict[str, Any]:
        return self._calib_status.copy()

    @calib_status.setter
    def calib_status(self, val: Dict[str, Any]) -> None:
        self._calib_status = val.copy()
        self.calib_status_changed.emit(self._calib_status)

    @property
    def anchor_layout(self) -> List[Dict[str, Any]]:
        return list(self._anchor_layout)

    @anchor_layout.setter
    def anchor_layout(self, val: List[Dict[str, Any]]) -> None:
        self._anchor_layout = list(val)
        self.anchor_layout_changed.emit(self._anchor_layout)
        self.zone_profiles_changed.emit(self.zone_profiles)

    @property
    def zone_profiles(self) -> Dict[int, Dict[str, Any]]:
        return {int(zone_id): dict(profile) for zone_id, profile in self._zone_profiles.items()}

    def update_zone_profile(self, profile: Dict[str, Any]) -> None:
        data = dict(profile or {})
        zone_id = self._parse_optional_int(data.get("zone_id"))
        if zone_id is None or zone_id <= 0:
            return
        self._zone_profiles[int(zone_id)] = data
        self.zone_profiles_changed.emit(self.zone_profiles)

    @property
    def sys_config(self) -> Dict[str, Any]:
        return self._sys_config.copy()

    @sys_config.setter
    def sys_config(self, val: Dict[str, Any]) -> None:
        self._sys_config = val.copy()
        self.sys_config_changed.emit(self._sys_config)

    @property
    def sys_ranging_cfg(self) -> Dict[str, Any]:
        return self._sys_ranging_cfg.copy()

    @sys_ranging_cfg.setter
    def sys_ranging_cfg(self, val: Dict[str, Any]) -> None:
        self._sys_ranging_cfg = val.copy()
        self.sys_ranging_cfg_changed.emit(self._sys_ranging_cfg)

    @property
    def sensor_fusion_cfg(self) -> Dict[str, Any]:
        return self._sensor_fusion_cfg.copy()

    @sensor_fusion_cfg.setter
    def sensor_fusion_cfg(self, val: Dict[str, Any]) -> None:
        self._sensor_fusion_cfg = val.copy()
        self.sensor_fusion_cfg_changed.emit(self._sensor_fusion_cfg)

    @property
    def prefilter_cfg(self) -> Dict[str, Any]:
        return self._prefilter_cfg.copy()

    @prefilter_cfg.setter
    def prefilter_cfg(self, val: Dict[str, Any]) -> None:
        self._prefilter_cfg = val.copy()
        self.prefilter_cfg_changed.emit(self._prefilter_cfg)
    @property
    def device_type(self) -> int:
        return self._device_type

    @device_type.setter
    def device_type(self, val: int) -> None:
        self._device_type = val
        self.device_type_changed.emit(self._device_type)

    @property
    def pos_calib_cfg(self) -> Dict[str, Any]:
        return self._pos_calib_cfg.copy()

    @pos_calib_cfg.setter
    def pos_calib_cfg(self, val: Dict[str, Any]) -> None:
        self._pos_calib_cfg = val.copy()
        self.pos_calib_cfg_changed.emit(self._pos_calib_cfg)

    @property
    def rtos_resource(self) -> Dict[str, Any]:
        return self._rtos_resource.copy()

    @rtos_resource.setter
    def rtos_resource(self, val: Dict[str, Any]) -> None:
        self._rtos_resource = val.copy()
        self.rtos_resource_changed.emit(self._rtos_resource)

    @property
    def manual_test_mode_enabled(self) -> bool:
        return self._manual_test_mode_enabled

    @manual_test_mode_enabled.setter
    def manual_test_mode_enabled(self, val: bool) -> None:
        enabled = bool(val)
        if self._manual_test_mode_enabled != enabled:
            self._manual_test_mode_enabled = enabled
            if enabled and self._query_manager:
                self.cancel_query_pipeline("manual test mode enabled")
            self.manual_test_mode_changed.emit(enabled)

    @property
    def rtos_task_stats(self) -> List[Dict[str, Any]]:
        return [item.copy() for item in self._rtos_task_stats]

    @rtos_task_stats.setter
    def rtos_task_stats(self, val: List[Dict[str, Any]]) -> None:
        self._rtos_task_stats = [item.copy() for item in val]
        self.rtos_task_stats_changed.emit(self.rtos_task_stats)

    TAG_ONLY_QUERY_COMMANDS = {"anchor_layout_get", "sensor_fusion_cfg_get", "prefilter_cfg_get", "zone_profile_get"}

    DEVICE_SESSION_PAYLOADS = {
        "device_information_resp",
        "battery_info_resp",
        "time_sync_resp",
        "ble_conn_params_resp",
        "anchor_layout_resp",
        "zone_profile_resp",
        "sys_config_resp",
        "sys_ranging_cfg_resp",
        "sensor_fusion_cfg_resp",
        "prefilter_cfg_resp",
        "pos_calib_cfg_resp",
        "ranging_status_resp",
        "calib_status_resp",
        "rtos_resource_resp",
        "rtos_task_stats_resp",
        "device_type_set",
        "ranging_result",
        "sensor_fusion_result",
        "calib_data",
    }

    def enable_device_session_payloads(self, reason: str = "") -> None:
        self._device_session_payloads_enabled = True
        if reason:
            log.debug("[SharedAppState] Device session payload gate opened: %s", reason)

    def disable_device_session_payloads(self, reason: str = "") -> None:
        self._device_session_payloads_enabled = False
        if reason:
            log.debug("[SharedAppState] Device session payload gate closed: %s", reason)

    def should_accept_device_session_payload(self, param_name: str) -> bool:
        name = str(param_name or "")
        if name not in self.DEVICE_SESSION_PAYLOADS:
            return True
        if not self._device_session_payloads_enabled:
            return self._is_active_query_response(name)
        # Fresh bootstrap payloads can arrive while the UI still says Connecting.
        # Block only when the session gate is closed, not just because status has
        # not reached Connected yet.
        return self._connection_status in {"Connecting", "Connected"} or self._is_active_query_response(name)

    def should_accept_decoded_packet(self, param_name: str, pkt: Any) -> bool:
        """Return True when a decoded payload belongs to the active device session."""
        name = str(param_name or "")
        if not self.should_accept_device_session_payload(name):
            return False
        if not self._device_session_identity_matches(name, pkt):
            log.debug("[SharedAppState] Ignoring stale/mismatched device payload: %s", name)
            return False
        return True

    def _device_session_identity_matches(self, param_name: str, pkt: Any) -> bool:
        name = str(param_name or "")
        if name not in self.DEVICE_SESSION_PAYLOADS:
            return True

        expected_id = self._connected_device_id()
        expected_serial = self._connected_device_serial()
        expected_role = self._connected_device_role_value()
        payload_id = self._packet_device_id(name, pkt)
        payload_serial = self._packet_serial_number(name, pkt)
        payload_role = self._packet_role_value(name, pkt)

        if expected_id is not None and payload_id is not None and payload_id != expected_id:
            log.info(
                "[SharedAppState] Rejected %s for device_id=%s while connected target is device_id=%s",
                name,
                payload_id,
                expected_id,
            )
            return False
        if expected_serial is not None and payload_serial is not None and payload_serial != expected_serial:
            log.info(
                "[SharedAppState] Rejected %s for serial=%s while connected target is serial=%s",
                name,
                payload_serial,
                expected_serial,
            )
            return False
        if expected_role is not None and payload_role is not None and payload_role != expected_role:
            log.info(
                "[SharedAppState] Rejected %s for role=%s while connected target role=%s",
                name,
                payload_role,
                expected_role,
            )
            return False
        return True

    def _connected_device_id(self) -> int | None:
        device = dict(self._connected_device or {})
        for key in ("device_id", "Device ID", "id"):
            parsed = self._parse_optional_int(device.get(key))
            if parsed is not None and parsed > 0:
                return parsed
        text = str(device.get("name") or device.get("Device Name") or "")
        match = re.search(r"\b(?:anchor|tag)[-_ ]*(\d+)\b", text, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None

    def _connected_device_serial(self) -> int | None:
        device = dict(self._connected_device or {})
        for key in ("serial_number", "serial", "Serial Number"):
            parsed = self._parse_optional_int(device.get(key))
            if parsed is not None and parsed > 0:
                return parsed
        return None

    def _connected_device_role_value(self) -> int | None:
        device = dict(self._connected_device or {})
        role = str(device.get("Role") or device.get("device_role") or device.get("role") or "").strip().upper()
        if role == "TAG":
            return 1
        if role == "ANCHOR":
            return 2
        return None

    @staticmethod
    def _parse_optional_int(value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, int):
            return int(value)
        text = str(value).strip()
        if not text or text == "-":
            return None
        try:
            return int(text, 0)
        except ValueError:
            return None

    @staticmethod
    def _packet_payload(param_name: str, pkt: Any) -> Any:
        return getattr(pkt, str(param_name or ""), None)

    def _packet_device_id(self, param_name: str, pkt: Any) -> int | None:
        payload = self._packet_payload(param_name, pkt)
        if payload is None:
            return None
        if param_name == "sys_config_resp":
            cfg = getattr(payload, "config", None)
            return self._parse_optional_int(getattr(cfg, "device_id", None))
        if param_name == "ble_adv_status":
            return self._parse_optional_int(getattr(payload, "device_id", None))
        return self._parse_optional_int(getattr(payload, "device_id", None))

    def _packet_serial_number(self, param_name: str, pkt: Any) -> int | None:
        payload = self._packet_payload(param_name, pkt)
        if payload is None:
            return None
        return self._parse_optional_int(getattr(payload, "serial_number", None))

    def _packet_role_value(self, param_name: str, pkt: Any) -> int | None:
        payload = self._packet_payload(param_name, pkt)
        if payload is None:
            return None
        if param_name == "sys_config_resp":
            cfg = getattr(payload, "config", None)
            return self._parse_optional_int(getattr(cfg, "role", None))
        return self._parse_optional_int(getattr(payload, "role", None))

    def clear_query_payload_markers(self) -> None:
        """Forget decoded response markers without clearing UI state.

        Also drop CommandBus response cache/pending so a follow-up bootstrap
        with force=False cannot 'cache hit' into silence after markers were
        cleared (classic symptom: dongle already replied, app keeps retrying).
        """
        self._received_payload_names.clear()
        try:
            from services.command_bus import shared_command_bus
            if shared_command_bus is not None:
                shared_command_bus.invalidate_all_responses()
        except Exception as exc:
            log.debug("Could not invalidate command bus cache with payload markers: %s", exc)

    def clear_connected_device_cached_data(self, reason: str = "read from device refresh") -> None:
        """Clear cached UI/device data while preserving the active BLE link/session."""
        current_device = dict(self._connected_device or {})
        self.cancel_query_pipeline(reason)
        self.disable_device_session_payloads(reason)
        self._battery_info = {}
        self._ble_status = {}
        self._ranging_stats = {}
        self._calib_status = {}
        self._anchor_layout = []
        self._zone_profiles = {}
        self._sys_config = {}
        self._sys_ranging_cfg = {}
        self._sensor_fusion_cfg = {}
        self._prefilter_cfg = {}
        self._pos_calib_cfg = {}
        self._rtos_resource = {}
        self._rtos_task_stats = []
        self._device_type = 0
        self._received_payload_names.clear()

        # Keep connected_device, connection_status, ranging/log/geofence sessions intact.
        self.connected_device_changed.emit(current_device)
        self.battery_info_changed.emit(self._battery_info)
        self.ble_status_changed.emit(self._ble_status)
        self.ranging_stats_changed.emit(self._ranging_stats)
        self.calib_status_changed.emit(self._calib_status)
        self.anchor_layout_changed.emit(self._anchor_layout)
        self.zone_profiles_changed.emit(self.zone_profiles)
        self.sys_config_changed.emit(self._sys_config)
        self.sys_ranging_cfg_changed.emit(self._sys_ranging_cfg)
        self.sensor_fusion_cfg_changed.emit(self._sensor_fusion_cfg)
        self.prefilter_cfg_changed.emit(self._prefilter_cfg)
        self.pos_calib_cfg_changed.emit(self._pos_calib_cfg)
        self.rtos_resource_changed.emit(self._rtos_resource)
        self.rtos_task_stats_changed.emit(self.rtos_task_stats)
        self.device_type_changed.emit(0)
        self.device_session_reset.emit(reason)
    def clear_device_session_state(self) -> None:
        """Clear all device-specific configurations and telemetry states."""
        self.cancel_query_pipeline("device session state cleared")
        self.disable_device_session_payloads("device session state cleared")
        self._connected_device = {}
        self._battery_info = {}
        self._ble_status = {}
        self._ranging_active = False
        self._log_streaming = False
        self._ble_scan_active = False
        self._ranging_stats = {}
        self._calib_status = {}
        self._anchor_layout = []
        self._zone_profiles = {}
        self._sys_config = {}
        self._sys_ranging_cfg = {}
        self._sensor_fusion_cfg = {}
        self._prefilter_cfg = {}
        self._pos_calib_cfg = {}
        self._rtos_resource = {}
        self._rtos_task_stats = []
        self._device_type = 0
        self._received_payload_names.clear()

        # Emit the changes so that the Views are notified
        self.connected_device_changed.emit(self._connected_device)
        self.battery_info_changed.emit(self._battery_info)
        self.ble_status_changed.emit(self._ble_status)
        self.ranging_active_changed.emit(self._ranging_active)
        self.ble_scan_active_changed.emit(self._ble_scan_active)
        self.device_type_changed.emit(0)
        self.log_streaming_changed.emit(self._log_streaming)
        self.ranging_stats_changed.emit(self._ranging_stats)
        self.calib_status_changed.emit(self._calib_status)
        self.anchor_layout_changed.emit(self._anchor_layout)
        self.zone_profiles_changed.emit(self.zone_profiles)
        self.sys_config_changed.emit(self._sys_config)
        self.sys_ranging_cfg_changed.emit(self._sys_ranging_cfg)
        self.sensor_fusion_cfg_changed.emit(self._sensor_fusion_cfg)
        self.prefilter_cfg_changed.emit(self._prefilter_cfg)
        self.pos_calib_cfg_changed.emit(self._pos_calib_cfg)
        self.rtos_resource_changed.emit(self._rtos_resource)
        self.rtos_task_stats_changed.emit(self.rtos_task_stats)
        self.device_session_reset.emit("device session state cleared")

    def _query_manager_busy(self) -> bool:
        manager = getattr(self, "_query_manager", None)
        return bool(manager and manager.has_active_work())

    @property
    def query_queue_busy(self) -> bool:
        return bool(self._query_recovery_pending or self._query_manager_busy())

    @staticmethod
    def _normalise_query_flow(flow_name: str = "", traffic_class: str = "") -> str:
        flow = str(flow_name or "").strip().lower()
        if flow:
            return flow
        traffic = str(traffic_class or "").strip().lower()
        if traffic == "connection":
            return "connect"
        if traffic == "bootstrap":
            return "connected_device"
        if traffic == "background":
            return "background"
        if traffic in {"manual", "user"}:
            return "user_action"
        return "default"

    @staticmethod
    def _display_flow_name(flow_name: str) -> str:
        labels = {
            "connect": "CONNECT",
            "connected_device": "CONNECTED_DEVICE",
            "write_device": "WRITE_DEVICE",
            "live_tracking_map": "LIVE_TRACKING_MAP",
            "user_action": "USER_ACTION",
            "background": "BACKGROUND",
            "default": "DEFAULT",
        }
        flow = str(flow_name or "default").strip().lower()
        return labels.get(flow, flow.upper())

    @staticmethod
    def _query_recovery_key(flow_name: str, item: Dict[str, Any]) -> str:
        expected = str(item.get("expected_response") or item.get("command_name") or "")
        return f"{str(flow_name or '').strip().lower()}::{expected}"

    def _print_flow_debug(self, flow_name: str) -> None:
        display = self._display_flow_name(flow_name)
        print(f"[FLOW] executing: {display}", flush=True)
        log.info("[FLOW] executing: %s", display)

    @staticmethod
    def _background_query_key(command_name: str, dst_addr: int, params: Dict[str, Any]) -> Tuple[str, int, str]:
        try:
            params_key = repr(sorted(params.items()))
        except Exception:
            params_key = repr(params)
        return (str(command_name or ""), int(dst_addr or 0), params_key)

    def _defer_background_query(
        self,
        command_name: str,
        dst_addr: int,
        params: Dict[str, Any],
        timeout_s: float | None,
        max_retries: int | None,
        flow_name: str = "background",
    ) -> None:
        key = self._background_query_key(command_name, dst_addr, params)
        self._deferred_background_queries[key] = {
            "command_name": str(command_name or ""),
            "dst_addr": int(dst_addr or 0),
            "command_params": dict(params or {}),
            "timeout_s": timeout_s,
            "max_retries": max_retries,
            "flow_name": self._normalise_query_flow(flow_name, "background"),
        }
        log.debug("[SharedAppState] Background query deferred until queue is free: %s", command_name)

    def _flush_deferred_background_queries(self) -> bool:
        if not self._deferred_background_queries or self.query_queue_busy:
            return False
        pending = list(self._deferred_background_queries.values())
        self._deferred_background_queries.clear()
        for item in pending:
            self.enqueue_query(
                str(item.get("command_name") or ""),
                int(item.get("dst_addr") or 0),
                command_params=dict(item.get("command_params") or {}),
                traffic_class="background",
                timeout_s=item.get("timeout_s"),
                max_retries=item.get("max_retries"),
                flow_name=str(item.get("flow_name") or "background"),
                defer_if_busy=False,
            )
        return bool(pending)

    def _defer_manual_flow_query(
        self,
        command_name: str,
        dst_addr: int,
        params: Dict[str, Any],
        traffic_class: str,
        timeout_s: float | None,
        max_retries: int | None,
        recovery_wave: int,
        flow_name: str,
        defer_if_busy: bool,
    ) -> None:
        key = self._background_query_key(command_name, dst_addr, params)
        key = (key[0], key[1], f"{key[2]}|{traffic_class}|{flow_name}|{recovery_wave}")
        self._deferred_manual_flow_queries[key] = {
            "command_name": str(command_name or ""),
            "dst_addr": int(dst_addr or 0),
            "command_params": dict(params or {}),
            "traffic_class": str(traffic_class or ""),
            "timeout_s": timeout_s,
            "max_retries": max_retries,
            "recovery_wave": int(recovery_wave or 0),
            "flow_name": str(flow_name or ""),
            "defer_if_busy": bool(defer_if_busy),
        }
        log.debug("[SharedAppState] Query deferred while %s flow is active: %s", self._manual_flow_active, command_name)

    def _flush_deferred_manual_flow_queries(self) -> bool:
        if not self._deferred_manual_flow_queries:
            return False
        pending = list(self._deferred_manual_flow_queries.values())
        self._deferred_manual_flow_queries.clear()
        for item in pending:
            self.enqueue_query(
                str(item.get("command_name") or ""),
                int(item.get("dst_addr") or 0),
                command_params=dict(item.get("command_params") or {}),
                traffic_class=str(item.get("traffic_class") or ""),
                timeout_s=item.get("timeout_s"),
                max_retries=item.get("max_retries"),
                recovery_wave=int(item.get("recovery_wave") or 0),
                flow_name=str(item.get("flow_name") or ""),
                defer_if_busy=bool(item.get("defer_if_busy", True)),
            )
        return bool(pending)

    def begin_manual_flow(self, flow_name: str) -> str:
        flow = self._normalise_query_flow(flow_name)
        self._manual_flow_active = flow
        self._active_query_flow = flow
        self._print_flow_debug(flow)
        self.update_job("query_queue", JobState.RUNNING)
        return flow

    def record_manual_flow_item(
        self,
        flow_name: str,
        command_name: str,
        *,
        status: str,
        dst_addr: int = 0,
        expected_response: str = "",
        retries: int = 0,
        seq: int | None = None,
        ack_response: int | None = None,
        failure_reason: str = "",
        traffic_class: str = "manual",
    ) -> None:
        flow = self._normalise_query_flow(flow_name)
        item = {
            "command_name": str(command_name or "-"),
            "dst_addr": int(dst_addr or 0),
            "expected_response": str(expected_response or ""),
            "status": str(status or QueryState.FAILED).upper(),
            "retries": int(retries or 0),
            "sent_time": 0.0,
            "received_time": 0.0,
            "seq": seq,
            "command_params": {},
            "priority": 100,
            "traffic_class": str(traffic_class or "manual"),
            "flow_name": flow,
            "ack_received": ack_response is not None,
            "ack_response": ack_response,
            "timeout_s": 0.0,
            "max_retries": 0,
            "recovery_wave": 0,
            "failure_reason": str(failure_reason or ""),
            "response_seq": None,
            "response_packet": None,
        }
        self._remember_query_results(flow, [item])

    def complete_manual_flow(self, flow_name: str) -> None:
        flow = self._normalise_query_flow(flow_name)
        self._print_final_flow_report(flow)
        results = list(self._last_query_flow_reports.get(flow, []))
        success = sum(1 for item in results if item.get("status") == "SUCCESS")
        total = len(results)
        status = JobState.SUCCESS if total and success == total else JobState.FAILED
        self.update_job("query_queue", status, progress=100)
        if self._manual_flow_active == flow:
            self._manual_flow_active = ""
        if self._active_query_flow == flow:
            self._active_query_flow = ""
        self._flush_deferred_manual_flow_queries()
        self._flush_deferred_background_queries()
        self.query_flow_completed.emit(flow)
    # Global Query Queue Management (Retry/Timeout logic)

    def cancel_query_pipeline(self, reason: str = "") -> None:
        """Cancel queued/active queries and invalidate delayed recovery retries."""
        self._query_generation += 1
        self._query_recovery_attempts.clear()
        self._query_flow_results.clear()
        self._last_query_flow_reports.clear()
        self._query_recovery_pending = False
        self._query_start_scheduled = False
        self._active_query_flow = ""
        self._deferred_background_queries.clear()
        if hasattr(self, '_query_manager') and self._query_manager:
            self._query_manager.reset()
        try:
            from services.command_bus import shared_command_bus
            if shared_command_bus:
                shared_command_bus.reset()
        except Exception as exc:
            log.debug("Could not reset command bus while cancelling query pipeline: %s", exc)
        if hasattr(self, '_jobs'):
            self.update_job("query_queue", JobState.IDLE, progress=0)
        if reason:
            log.info("[SharedAppState] Query pipeline cancelled: %s", reason)

    def init_query_manager(self, send_packet_fn: Callable[[str, int, Dict[str, Any]], Any]) -> None:
        """Initialize the global query queue manager with the packet sending function."""
        self._query_manager = QueryQueueManager(
            send_packet_fn=send_packet_fn,
            timeout_s=QUERY_TIMEOUT_S,
            max_retries=QUERY_MAX_RETRIES,
            on_complete_fn=self._on_query_complete,
            on_nack_fn=self._on_query_nack,
        )
        log.info("[SharedAppState] Query manager initialized.")

    def _on_query_nack(self, info: Dict[str, Any]) -> None:
        """Notify the UI when firmware explicitly NACKs a query command."""
        command = str(info.get("command_name") or "packet")
        reason = str(info.get("failure_reason") or "NACK")
        code = int(info.get("ack_response") or 0)
        if reason == "UNIMPLEMENTED":
            message = f"packet {command}: unimplemented"
        else:
            message = f"packet {command}: {reason.lower()}"
        log.warning("[SharedAppState] Query NACK: %s response=%s reason=%s", command, code, reason)
        self.query_notification_requested.emit({
            "kind": "error",
            "title": "Packet not supported" if reason == "UNIMPLEMENTED" else "Packet NACK",
            "message": message,
            "auto_close_ms": 4500,
        })

    def _is_active_query_response(self, param_name: str) -> bool:
        manager = getattr(self, "_query_manager", None)
        if manager is None:
            return False
        checker = getattr(manager, "is_active_expected_response", None)
        if checker is None:
            return False
        try:
            return bool(checker(param_name))
        except Exception as exc:
            log.debug("Could not check active query response gate for %s: %s", param_name, exc)
            return False

    def _query_allowed_for_connected_role(self, command_name: str) -> bool:
        name = str(command_name or "").strip()
        if name not in self.TAG_ONLY_QUERY_COMMANDS:
            return True
        device = dict(self._connected_device or {})
        role = str(device.get("Role") or device.get("device_role") or device.get("role") or "").strip().upper()
        allowed = role == "TAG"
        if not allowed:
            log.info("Skipping TAG-only recovery query for role=%s: %s", role or "UNKNOWN", name)
        return allowed
    def enqueue_query(
        self,
        command_name: str,
        dst_addr: int,
        command_params: dict | None = None,
        traffic_class: str = "",
        timeout_s: float | None = None,
        max_retries: int | None = None,
        recovery_wave: int = 0,
        flow_name: str = "",
        defer_if_busy: bool = True,
    ) -> bool:
        """Add a query to the sequential execution queue.

        Returns True only when the query was actually queued.
        """
        params = dict(command_params or {})
        traffic_class = str(traffic_class or "").strip().lower()
        raw_flow_name = str(flow_name or "").strip().lower()
        flow_name = self._normalise_query_flow(raw_flow_name, traffic_class)
        manual_flow = str(self._manual_flow_active or "").strip().lower()
        if manual_flow:
            if str(command_name or "") == "ble_status_get":
                flow_name = manual_flow
                raw_flow_name = manual_flow
                defer_if_busy = False
            else:
                self._defer_manual_flow_query(
                    command_name,
                    dst_addr,
                    params,
                    traffic_class,
                    timeout_s,
                    max_retries,
                    recovery_wave,
                    flow_name,
                    defer_if_busy,
                )
                return True
        if flow_name == "connected_device" and traffic_class == "bootstrap":
            self.enable_device_session_payloads(f"query queued: {command_name}")
        if max_retries is None and flow_name == "connected_device" and traffic_class == "bootstrap":
            max_retries = 1

        if self._manual_test_mode_enabled:
            log.debug("[SharedAppState] Query skipped by manual test mode: %s", command_name)
            return False

        if not is_command_enabled(command_name):
            log.info("[SharedAppState] Query skipped by command flag: %s", command_name)
            return False

        if traffic_class == "background" and self._query_recovery_pending:
            self._defer_background_query(command_name, dst_addr, params, timeout_s, max_retries, flow_name)
            return True

        if traffic_class == "background" and defer_if_busy and self.query_queue_busy:
            self._defer_background_query(command_name, dst_addr, params, timeout_s, max_retries, flow_name)
            return True

        try:
            from services.traffic_scheduler import shared_traffic_scheduler
            decision = shared_traffic_scheduler.allow_command(
                command_name,
                traffic_class=traffic_class,
                force=traffic_class != "background",
            )
            if not decision.allowed:
                log.debug("[SharedAppState] Query skipped by traffic scheduler: %s (%s)", command_name, decision.reason)
                return False
        except ImportError:
            pass

        if not hasattr(self, '_query_manager') or not self._query_manager:
            log.warning("[SharedAppState] Query manager not initialized. Can't enqueue.")
            return False

        added = self._query_manager.add_query(command_name, dst_addr, command_params=params, traffic_class=traffic_class, flow_name=flow_name, timeout_s=timeout_s, max_retries=max_retries, recovery_wave=recovery_wave)
        if not added:
            return False
        if not self._query_manager.is_running:
            self._schedule_query_start(flow_name)
        return True

    def _schedule_query_start(self, flow_name: str) -> None:
        """Start the queue after a tiny burst window so one flow reports as one batch."""
        if self._query_start_scheduled:
            return
        self._query_start_scheduled = True
        self._active_query_flow = flow_name
        self._print_flow_debug(flow_name)
        self.update_job("query_queue", JobState.RUNNING)

        def _start_if_ready() -> None:
            self._query_start_scheduled = False
            manager = getattr(self, "_query_manager", None)
            if manager is None or manager.is_running:
                return
            if not manager.has_active_work():
                self._active_query_flow = ""
                return
            manager.start()

        # A short debounce is enough to collect request_initial_telemetry(),
        # request_session_start_events(), and any same-tick BLE status poll into
        # one connected-device report instead of one report per first packet.
        QTimer.singleShot(50, _start_if_ready)
    def handle_incoming_packet(self, param_name: str, pkt: Any) -> None:
        """Route incoming packets to the query queue manager to check for response matches."""
        name = str(param_name or "")
        if not self.should_accept_decoded_packet(name, pkt):
            log.debug("[SharedAppState] Ignoring stale payload marker/query response before active session: %s", name)
            return
        if name.endswith("_resp") or name == "device_type_set":
            self._received_payload_names.add(name)
            self._mark_payload_success_in_reports(name)
            self._mark_late_payload_success_after_final_report(name, pkt)
        if hasattr(self, '_query_manager') and self._query_manager:
            self._query_manager.handle_response(param_name, pkt)

    def handle_incoming_ack(self, ack_seq: int, response: int, src_addr: int | None = None) -> None:
        """Route incoming ACK packets to the query queue manager."""
        if hasattr(self, '_query_manager') and self._query_manager:
            self._query_manager.handle_ack(ack_seq, response, src_addr)

    def _query_result_key(self, item: Dict[str, Any]) -> str:
        command = str(item.get("command_name") or "")
        expected = str(item.get("expected_response") or "")
        dst = str(item.get("dst_addr") or "")
        return f"{command}::{expected}::{dst}"

    def _remember_query_results(self, flow_name: str, items: List[Dict[str, Any]]) -> None:
        flow = self._normalise_query_flow(flow_name)
        bucket = self._query_flow_results.setdefault(flow, {})
        for item in items:
            key = self._query_result_key(item)
            previous = bucket.get(key)
            copy_item = dict(item)
            expected = str(copy_item.get("expected_response") or "")
            if expected and self._response_payload_available(expected):
                copy_item["status"] = "SUCCESS"
                copy_item["received_time"] = copy_item.get("received_time") or 0.0
            if previous:
                copy_item["recovery_wave"] = max(
                    int(previous.get("recovery_wave") or 0),
                    int(copy_item.get("recovery_wave") or 0),
                )
            if previous and previous.get("status") == "SUCCESS" and copy_item.get("status") != "SUCCESS":
                continue
            bucket[key] = copy_item

    def _mark_aggregate_payload_success(self, flow_name: str, expected_response: str, recovery_wave: int | None = None) -> None:
        flow = self._normalise_query_flow(flow_name)
        expected = str(expected_response or "")
        for item in self._query_flow_results.get(flow, {}).values():
            if str(item.get("expected_response") or "") == expected:
                item["status"] = "SUCCESS"
                item["received_time"] = item.get("received_time") or 0.0
                if recovery_wave is not None:
                    item["recovery_wave"] = max(int(item.get("recovery_wave") or 0), int(recovery_wave or 0))

    def _mark_payload_success_in_reports(self, expected_response: str) -> None:
        """Mark any queued/reported command satisfied by an event/polling payload."""
        expected = str(expected_response or "")
        if not expected:
            return
        for flow in list(self._query_flow_results):
            self._mark_aggregate_payload_success(flow, expected)

    def _print_final_flow_report(self, flow_name: str) -> None:
        flow = self._normalise_query_flow(flow_name)
        results = list(self._query_flow_results.get(flow, {}).values())
        # Keep the latest connected-device bootstrap report available for the
        # Read button. A later background/polling report (for example
        # ble_status_get) must not replace a full bootstrap report.
        is_bootstrap_report = any(
            str(item.get("traffic_class") or "").strip().lower() == "bootstrap"
            for item in results
        )
        if flow != "connected_device" or is_bootstrap_report or flow not in self._last_query_flow_reports:
            self._last_query_flow_reports[flow] = [
                {key: value for key, value in dict(item).items() if key != "response_packet"}
                for item in results
            ]
        success_count = sum(1 for item in results if item.get("status") == "SUCCESS")
        total = len(results)
        failed = [item for item in results if item.get("status") != "SUCCESS"]
        retry_total = 0
        wave_entries: List[str] = []
        for item in results:
            retries = int(item.get("retries") or 0)
            wave = int(item.get("recovery_wave") or 0)
            retry_total += retries + wave
            if item.get("status") == "SUCCESS" and wave > 0:
                command = str(item.get("command_name") or "-")
                wave_entries.append(f"{command} - WAVE {wave}")
        footer_rows: List[Tuple[str, str]] = [
            ("SUCCESSFUL", f"{success_count}/{total} packets"),
            ("RETRY", str(retry_total)),
        ]
        if wave_entries:
            footer_rows.append(("WAVE", wave_entries[0]))
            for entry in wave_entries[1:]:
                footer_rows.append(("", entry))
        else:
            footer_rows.append(("WAVE", "-"))
        if flow in {"connected_device", "write_device"}:
            ble = self.ble_status
            raw_state = str(ble.get("display_state") or ble.get("state_name") or "-")
            footer_rows.extend([
                ("DEVICE LINK", str(ble.get("connection_status") or self.connection_status or "-").upper()),
                ("LINK HEALTH", str(ble.get("link_health") or "-").upper()),
                ("DONGLE BLE", raw_state.replace("BLE_STATE_", "")),
                ("SCAN", "ACTIVE" if ble.get("scan_active") else "INACTIVE"),
            ])
        for item in failed:
            command = str(item.get("command_name") or "-")
            reason = str(item.get("failure_reason") or "").upper()
            footer_rows.append(("FAILED", f"{command} {reason}".strip()))
        self._print_query_report(results, flow_name=flow, detail_rows=footer_rows)
        self._query_flow_results.pop(flow, None)

    def _on_query_complete(self, results: List[Dict[str, Any]]) -> None:
        """Called when the sequential query queue finishes execution."""
        flow_order: List[str] = []
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        normalised_results: List[Dict[str, Any]] = []
        for raw in results:
            item = dict(raw)
            flow = self._normalise_query_flow(str(item.get("flow_name") or ""), str(item.get("traffic_class") or ""))
            item["flow_name"] = flow
            normalised_results.append(item)
            if flow not in grouped:
                grouped[flow] = []
                flow_order.append(flow)
            grouped[flow].append(item)

        if not flow_order:
            flow_order = [self._active_query_flow or "default"]
            grouped[flow_order[0]] = []

        all_retryable: List[Dict[str, Any]] = []
        all_failed: List[Dict[str, Any]] = []
        total_success = 0
        total_count = 0

        for flow in flow_order:
            group = grouped.get(flow, [])
            for item in group:
                expected = str(item.get("expected_response") or "")
                if expected and self._response_payload_available(expected):
                    item["status"] = "SUCCESS"
                    item["received_time"] = item.get("received_time") or 0.0
            self._remember_query_results(flow, group)
            success_count = sum(1 for r in group if r.get("status") == "SUCCESS")
            total = len(group)
            total_success += success_count
            total_count += total
            failed = [r for r in group if r.get("status") != "SUCCESS"]
            all_failed.extend(failed)

            retryable: List[Dict[str, Any]] = []
            exhausted: List[Dict[str, Any]] = []
            if flow == "connected_device":
                failed_gets = [
                    r for r in failed
                    if r.get("expected_response")
                    and self._query_allowed_for_connected_role(str(r.get("command_name") or ""))
                    and str(r.get("command_name") or "").endswith("_get")
                    and str(r.get("traffic_class") or "").strip().lower() == "bootstrap"
                    and str(r.get("failure_reason") or "").upper() != "UNIMPLEMENTED"
                    and str(r.get("status") or "").upper() != "UNSUPPORTED"
                ]
                for item in failed_gets:
                    key = self._query_recovery_key(flow, item)
                    attempts = self._query_recovery_attempts.get(key, 0) + 1
                    self._query_recovery_attempts[key] = attempts
                    copy_item = dict(item)
                    copy_item["recovery_wave"] = attempts
                    copy_item["flow_name"] = flow
                    if attempts <= QUERY_RECOVERY_MAX_WAVES:
                        retryable.append(copy_item)
                    else:
                        exhausted.append(copy_item)

            for item in group:
                if item.get("status") == "SUCCESS":
                    key = self._query_recovery_key(flow, item)
                    self._query_recovery_attempts.pop(key, None)

            # Do not print intermediate connected-device reports. Keep them
            # aggregated and emit only one final table after recovery ends.
            all_retryable.extend(retryable)

        if all_retryable:
            try:
                from services.command_bus import shared_command_bus
                if shared_command_bus is not None:
                    for item in all_retryable:
                        expected = str(item.get("expected_response") or "")
                        if expected:
                            shared_command_bus.clear_pending(expected)
            except Exception as exc:
                log.debug("Could not clear command bus pending after query failures: %s", exc)

            missing = ", ".join(
                f"{r.get('command_name')}[wave {r.get('recovery_wave')}/{QUERY_RECOVERY_MAX_WAVES}]"
                for r in all_retryable
            )
            log.debug("Connected-device query recovery scheduled: %s", missing)
            self.update_job("query_queue", JobState.RETRYING, progress=95, retries=len(all_retryable), error_msg=missing)
            self._schedule_query_recovery(all_retryable, self._query_generation)
            return

        manual_flow = str(self._manual_flow_active or "").strip().lower()
        completed_flows: List[str] = []
        for flow in flow_order:
            if manual_flow and flow == manual_flow:
                continue
            self._print_final_flow_report(flow)
            completed_flows.append(flow)

        self._query_recovery_pending = False
        self._query_start_scheduled = False
        if completed_flows:
            status = JobState.SUCCESS if total_success == total_count else JobState.FAILED
            self.update_job("query_queue", status, progress=100)
            if not manual_flow:
                self._active_query_flow = ""
            self._flush_deferred_background_queries()
            for completed_flow in completed_flows:
                self.query_flow_completed.emit(str(completed_flow))
        elif not manual_flow:
            status = JobState.SUCCESS if total_success == total_count else JobState.FAILED
            self.update_job("query_queue", status, progress=100)
            self._active_query_flow = ""
            self._flush_deferred_background_queries()

    def _mark_late_payload_success_after_final_report(self, expected_response: str, pkt: Any) -> None:
        """Upgrade a previously failed final report row when its payload arrives late."""
        expected = str(expected_response or "")
        if not expected:
            return
        seq_val = None
        try:
            seq_val = pkt.hdr.seq
        except Exception:
            seq_val = None
        for flow, items in list(self._last_query_flow_reports.items()):
            for item in items:
                if str(item.get("expected_response") or "") != expected:
                    continue
                if str(item.get("status") or "").upper() == "SUCCESS":
                    continue
                command = str(item.get("command_name") or expected)
                previous = str(item.get("status") or "FAIL")
                reason = str(item.get("failure_reason") or "").upper()
                item["status"] = "SUCCESS"
                item["received_time"] = item.get("received_time") or 0.0
                item["late_payload"] = True
                item["late_response_seq"] = seq_val
                self._print_late_payload_report(flow, command, expected, previous, reason, seq_val)
                log.info(
                    "Late payload accepted after final report: flow=%s command=%s response=%s seq=%s previous=%s reason=%s",
                    flow,
                    command,
                    expected,
                    seq_val,
                    previous,
                    reason or "-",
                )
                return

    def _print_late_payload_report(
        self,
        flow_name: str,
        command_name: str,
        response_name: str,
        previous_status: str,
        reason: str,
        seq_val: Any,
    ) -> None:
        title = "Late Packet Report"
        rows = [
            ("FLOW", self._display_flow_name(flow_name)),
            ("PACKET", command_name),
            ("RESPONSE", response_name),
            ("SEQ", "-" if seq_val is None else str(seq_val)),
            ("PREVIOUS", f"{previous_status} {reason}".strip()),
            ("RESULT", "OK - UI UPDATED"),
        ]
        label_width = max([len(title)] + [len(label) for label, _ in rows])
        value_width = max([len(value) for _, value in rows] + [len("OK - UI UPDATED")])
        inner_width = max(label_width + value_width + 5, len(title) + 4)
        border = "|" + "=" * inner_width + "|"
        print(f"|{title:=^{inner_width}}|", flush=True)
        print(border, flush=True)
        for label, value in rows:
            print(f"| {label:<{label_width}} : {value:<{value_width}} |", flush=True)
        print(border, flush=True)

    def _print_query_report(
        self,
        results: List[Dict[str, Any]],
        flow_name: str = "",
        summary: Tuple[str, str] | None = None,
        detail_rows: List[Tuple[str, str]] | None = None,
    ) -> None:
        """Print one compact boxed report for a completed flow."""
        title = "Global  Report Commands"
        command_rows: List[Tuple[str, str]] = []
        for item in results:
            command = str(item.get("command_name") or "-")
            status = "OK" if item.get("status") == "SUCCESS" else "FAIL"
            reason = str(item.get("failure_reason") or "").upper()
            wave = int(item.get("recovery_wave") or 0)
            max_wave = QUERY_RECOVERY_MAX_WAVES if wave else 0
            suffix = f" wave {wave}/{max_wave}" if wave else ""
            if reason and status != "OK":
                suffix = f" {reason}{suffix}"
            command_rows.append((command, f"{status}{suffix}"))

        header_rows: List[Tuple[str, str]] = [("FLOW", self._display_flow_name(flow_name))]
        footer_rows: List[Tuple[str, str]] = []
        if summary is not None:
            footer_rows.append(summary)
        footer_rows.extend(detail_rows or [])
        all_rows = header_rows + command_rows + footer_rows

        label_width = max([len(title)] + [len(label) for label, _ in all_rows]) if all_rows else len(title)
        value_width = max([len("OK")] + [len(value) for _, value in all_rows]) if all_rows else len("OK")
        summary_title = "SUMMARY"
        inner_width = max(label_width + value_width + 5, len(title) + 4, len(summary_title) + 4)
        border = "|" + "=" * inner_width + "|"
        print(f"|{title:=^{inner_width}}|", flush=True)
        print(border, flush=True)
        for label, value in header_rows:
            print(f"| {label:<{label_width}} : {value:<{value_width}} |", flush=True)
        if command_rows:
            print(border, flush=True)
            for label, value in command_rows:
                print(f"| {label:<{label_width}} : {value:<{value_width}} |", flush=True)
        if footer_rows:
            print(border, flush=True)
            print(f"|{summary_title:=^{inner_width}}|", flush=True)
            print(border, flush=True)
            for label, value in footer_rows:
                print(f"| {label:<{label_width}} : {value:<{value_width}} |", flush=True)
        print(border, flush=True)
        log.debug("Query flow report completed: flow=%s items=%d.", flow_name, len(command_rows))

    def failed_queries_from_last_report(self, flow_name: str = "connected_device") -> List[Dict[str, Any]]:
        """Return retryable failed GET commands from the latest final report."""
        flow = self._normalise_query_flow(flow_name)
        failed: List[Dict[str, Any]] = []
        for item in self._last_query_flow_reports.get(flow, []):
            expected = str(item.get("expected_response") or "")
            command = str(item.get("command_name") or "")
            if not expected or not command.endswith("_get"):
                continue
            if self._response_payload_available(expected):
                continue
            if str(item.get("status") or "").upper() == "SUCCESS":
                continue
            reason = str(item.get("failure_reason") or "").upper()
            if reason == "UNIMPLEMENTED" or str(item.get("status") or "").upper() == "UNSUPPORTED":
                continue
            copy_item = dict(item)
            copy_item["flow_name"] = flow
            copy_item["traffic_class"] = "bootstrap"
            copy_item["recovery_wave"] = 0
            failed.append(copy_item)
        return failed

    def reset_recovery_attempts_for_queries(self, flow_name: str, items: List[Dict[str, Any]]) -> None:
        flow = self._normalise_query_flow(flow_name)
        for item in items:
            self._query_recovery_attempts.pop(self._query_recovery_key(flow, item), None)

    def _response_payload_available(self, expected_response: str) -> bool:
        """Return True only when this session/flow has actually received the payload."""
        expected = str(expected_response or "")
        if expected in self._received_payload_names:
            return True
        if expected == "device_type_set":
            return bool(self._device_type) and expected in self._received_payload_names
        if expected == "device_information_resp":
            return expected in self._received_payload_names
        if expected == "time_sync_resp":
            return expected in self._received_payload_names
        return False

    def _schedule_query_recovery(self, retryable: List[Dict[str, Any]], generation: int) -> None:
        self._query_recovery_pending = True

        def _retry_missing_queries() -> None:
            if generation != self._query_generation:
                self._query_recovery_pending = False
                log.debug("Skipping stale query recovery wave generation=%s current=%s", generation, self._query_generation)
                return
            if self._connection_status != "Connected":
                self._query_recovery_pending = False
                log.info("Skipping query recovery because connection_status=%s", self._connection_status)
                self._active_query_flow = ""
                self._query_start_scheduled = False
                self._flush_deferred_background_queries()
                self.query_flow_completed.emit("connected_device")
                return
            if self._query_manager_busy():
                QTimer.singleShot(750, _retry_missing_queries)
                return

            still_missing = []
            for item in retryable:
                expected = str(item.get("expected_response") or "")
                if self._response_payload_available(expected):
                    self._mark_aggregate_payload_success(
                        "connected_device",
                        expected,
                        recovery_wave=int(item.get("recovery_wave") or 0),
                    )
                    log.debug(
                        "[GlobalQueue] Recovery skipped for %s because payload %s is already in app state.",
                        item.get("command_name"),
                        expected,
                    )
                    continue
                still_missing.append(item)

            if not still_missing:
                self._query_recovery_pending = False
                self.update_job("query_queue", JobState.SUCCESS, progress=100)
                self._print_final_flow_report("connected_device")
                log.debug("Connected-device query recovery completed from late payloads.")
                self._active_query_flow = ""
                self._query_start_scheduled = False
                self._flush_deferred_background_queries()
                self.query_flow_completed.emit("connected_device")
                return

            names = ", ".join(f"{item.get('command_name')}[wave {item.get('recovery_wave', '?')}/{QUERY_RECOVERY_MAX_WAVES}]" for item in still_missing)
            log.debug("Connected-device query recovery enqueue: %s", names)
            self.update_job("query_queue", JobState.RETRYING, progress=96, retries=len(still_missing), error_msg=names)
            self._query_recovery_pending = False
            for item in still_missing:
                params = dict(item.get("command_params") or {})
                self.enqueue_query(
                    str(item.get("command_name") or ""),
                    int(item.get("dst_addr") or 0),
                    command_params=params,
                    traffic_class="bootstrap",
                    timeout_s=item.get("timeout_s"),
                    max_retries=item.get("max_retries"),
                    recovery_wave=int(item.get("recovery_wave") or 0),
                    flow_name="connected_device",
                    defer_if_busy=False,
                )

        QTimer.singleShot(750, _retry_missing_queries)

    # Job State Machine Implementation

    def update_job(self, job_name: str, status: str, progress: int = 0, retries: int = 0, error_msg: str = "") -> None:
        """Update job status and notify listeners across all tabs."""
        with self._lock:
            self._jobs[job_name] = {
                "status": status,
                "progress": progress,
                "retries": retries,
                "error_msg": error_msg
            }
        log.info(f"[JobStateMachine] Job '{job_name}' -> status: {status}, progress: {progress}%, retries: {retries}")
        self.job_state_changed.emit(job_name, status, progress, retries, error_msg)

    def get_job_state(self, job_name: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_name)
            return job.copy() if job else None


# Shared singleton instance accessible via import
shared_app_state = SharedAppState()
