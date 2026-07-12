"""
==============================================================================
  UWB RTLS Studio - Device Model
==============================================================================
  File        : models/device_model.py
  Description : Model managing the state and communication logic of the connected
                BLE peripheral. Acts as the sole Source of Truth for connected
                device state, telemetry updates, and BLE scanning results.

  MVVM Role   : MODEL - State Management & Business/Domain logic.

  Thread Model:
    - Main GUI Thread: All methods and signal slot handlers execute strictly
      on this thread.
    - Protocol incoming signals are queued via PyQt to ensure that packet processing
      and state mutation are confined to the Main GUI Thread.
==============================================================================
"""
import logging
import re
import time
from PyQt6.QtCore import QObject, pyqtSignal, QTimer

from services.protocol_service import ProtocolService
from services.time_sync_manager import TimeSyncManager
from common.transport import VvAddress
from utils.app_state import shared_app_state, JobState
from utils.ble_hci import normalize_hci_reason
from utils.constants import (
    STOP_TO_CONNECT_DELAY_MS,
    TIME_SYNC_THRESHOLD_MS,
    DEVICE_TYPE_LABELS,
)

log = logging.getLogger(__name__)

BACKGROUND_SCAN_RESUME_DELAY_MS = 1000
CONNECT_RETRY_DELAY_MS = 500
CONNECT_TIMEOUT_MS = 10000
MAX_CONNECT_RETRIES = 2
CONNECT_TIME_SYNC_ACK_TIMEOUT_MS = 1500
END_SESSION_ACK_TIMEOUT_MS = 1500
END_SESSION_STATUS_POLL_INTERVAL_MS = 400
END_SESSION_STATUS_POLL_TIMEOUT_MS = 3000
END_SESSION_VALID_STATES = {0, 1, 3}

BLE_STATE_NAMES = {
    0: "BLE_STATE_UNSPECIFIED",
    1: "BLE_STATE_IDLE",
    2: "BLE_STATE_SCANNING",
    3: "BLE_STATE_ADVERTISING",
    4: "BLE_STATE_CONNECTING",
    5: "BLE_STATE_CONNECTED",
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

    # Signals
    device_info_parsed = pyqtSignal(dict)
    battery_info_parsed = pyqtSignal(dict)
    ble_status_parsed = pyqtSignal(dict)
    ble_conn_params_parsed = pyqtSignal(dict)
    time_sync_result = pyqtSignal(dict)       # {dev_time_ms, host_time_ms, tz_offset_sec, time_diff_ms, is_synced, was_corrected}
    scan_data_updated = pyqtSignal(list)      # merged advertising device list
    connection_state_changed = pyqtSignal(dict)  # {name, mac, status}
    connection_progress_changed = pyqtSignal(dict)
    ble_notification_requested = pyqtSignal(dict)
    end_session_result = pyqtSignal(dict)
    sys_config_parsed = pyqtSignal(dict)
    sys_ranging_cfg_parsed = pyqtSignal(dict)
    sensor_fusion_cfg_parsed = pyqtSignal(dict)
    pos_calib_cfg_parsed = pyqtSignal(dict)
    device_type_parsed = pyqtSignal(int)


    def __init__(self, protocol: ProtocolService, telemetry_repo=None, ble_scan_repo=None, config_repo=None, command_bus=None, parent=None):
        super().__init__(parent)
        self._protocol = protocol
        self._telemetry_repo = telemetry_repo
        self._ble_scan_repo = ble_scan_repo
        self._config_repo = config_repo
        self._command_bus = command_bus
        
        # State (single source of truth)
        self._connected_mac = ""
        self._connected_name = ""
        self._connection_status = "Disconnected"
        self._is_scanning = False
        self._pending_connect_mac = ""
        self._pending_connect_name = ""
        self._session_bootstrap_done = False
        self._session_start_events_done = False
        self._received_query_payloads: set[str] = set()
        self._last_query_progress_report: dict[str, tuple[str, ...]] = {}
        self._log_stream_requested = False
        self._scan_device_order: dict[str, int] = {}
        self._next_scan_device_order = 0
        self._connected_grace_until = 0.0
        self._session_start_scheduled = False

        self._connect_retry_count = 0
        self._connect_generation = 0
        self._pending_end_session_seq: int | None = None
        self._pending_device_type_set_seq: int | None = None
        self._pending_end_session_reason = 0
        self._pending_end_session_ack = False
        self._pending_end_session_state_seen = False
        self._suppress_next_disconnect_scan = False
        self._time_sync_manager = TimeSyncManager(
            request_query_fn=self._request_query,
            send_command_fn=self._send_command,
            host_time_fn=time.time,
            timezone_offset_fn=self._host_timezone_offset_min,
            parent=self,
        )


        # Advertising devices storage
        self._adv_devices = {}                  # mac_hex -> scan fields
        self._adv_status_by_device_id = {}      # device_id -> adv status fields

        # Protocol listener
        self._protocol.packet_received.connect(self._on_packet)
        self._protocol.ack_received.connect(self._time_sync_manager.handle_ack)
        self._protocol.ack_received.connect(self._on_ack_received)
        shared_app_state.manual_test_mode_changed.connect(self._on_manual_test_mode_changed)
        shared_app_state.log_streaming_changed.connect(self._on_log_stream_state_changed)
        shared_app_state.rtos_resource_changed.connect(lambda _data: self._mark_query_received("rtos_resource_resp"))
        shared_app_state.rtos_task_stats_changed.connect(lambda _data: self._mark_query_received("rtos_task_stats_resp"))

        if self._telemetry_repo is not None:
            self._telemetry_repo.telemetry_updated.connect(self._on_repository_battery_info)
        if self._ble_scan_repo is not None:
            self._ble_scan_repo.scan_results_updated.connect(self._on_repository_scan_results)
        if self._config_repo is not None:
            self._config_repo.sys_config_updated.connect(self._on_repository_sys_config)
            self._config_repo.sys_ranging_cfg_updated.connect(self._on_repository_sys_ranging_cfg)
            self._config_repo.sensor_fusion_cfg_updated.connect(self._on_repository_sensor_fusion_cfg)
            self._config_repo.pos_calib_cfg_updated.connect(self._on_repository_pos_calib_cfg)
            self._config_repo.device_type_updated.connect(self._on_repository_device_type)

        # Prune timer for stale advertising devices
        self._prune_timer = QTimer(self)
        self._prune_timer.timeout.connect(self._prune_devices)
        
        # BLE status check timer (10s interval)
        self._ble_status_timer = QTimer(self)
        self._ble_status_timer.setInterval(10000)
        self._ble_status_timer.timeout.connect(self._poll_ble_status)

        # RTOS task stats poll timer (5s interval)
        self._rtos_task_stats_timer = QTimer(self)
        self._rtos_task_stats_timer.setInterval(5000)
        self._rtos_task_stats_timer.timeout.connect(lambda: self.request_rtos_task_stats(force=True))

        # battery_info is received via device telemetry stream (1s push); no host-side poll timer needed.

        self._ble_transition_timer = QTimer(self)
        self._ble_transition_timer.setInterval(500)
        self._ble_transition_timer.timeout.connect(self._poll_ble_transition_status)

        self._connect_timeout_timer = QTimer(self)
        self._connect_timeout_timer.setSingleShot(True)
        self._connect_timeout_timer.timeout.connect(self._on_connect_timeout)

        self._background_scan_resume_timer = QTimer(self)
        self._background_scan_resume_timer.setSingleShot(True)
        self._background_scan_resume_timer.timeout.connect(self._resume_background_scan_after_connect)

        self._manual_scan_stop_timer = QTimer(self)
        self._manual_scan_stop_timer.setSingleShot(True)
        self._manual_scan_stop_timer.timeout.connect(self.stop_scan)

        self._session_bootstrap_timer = QTimer(self)
        self._session_bootstrap_timer.setSingleShot(True)
        self._session_bootstrap_timer.timeout.connect(self._run_scheduled_session_start)

        self._end_session_ack_timeout_timer = QTimer(self)
        self._end_session_ack_timeout_timer.setSingleShot(True)
        self._end_session_ack_timeout_timer.timeout.connect(self._on_end_session_ack_timeout)

        self._end_session_poll_delay_timer = QTimer(self)
        self._end_session_poll_delay_timer.setSingleShot(True)
        self._end_session_poll_delay_timer.timeout.connect(self._start_end_session_status_polling)

        self._end_session_status_poll_timer = QTimer(self)
        self._end_session_status_poll_timer.setInterval(END_SESSION_STATUS_POLL_INTERVAL_MS)
        self._end_session_status_poll_timer.timeout.connect(self._poll_end_session_status)

        self._end_session_status_timeout_timer = QTimer(self)
        self._end_session_status_timeout_timer.setSingleShot(True)
        self._end_session_status_timeout_timer.timeout.connect(self._on_end_session_status_timeout)

        self._active_connecting_handshake = False
        self._handshake_device_info_received = False
        self._handshake_sys_config_received = False
        self._handshake_time_sync_done = False
        self._handshake_final_ble_connected = False
        self._pending_handshake_time_sync_seq: int | None = None
        self._handshake_timeout_timer = QTimer(self)
        self._handshake_timeout_timer.setSingleShot(True)
        self._handshake_timeout_timer.timeout.connect(self._on_handshake_timeout)

        self._handshake_time_sync_timer = QTimer(self)
        self._handshake_time_sync_timer.setSingleShot(True)
        self._handshake_time_sync_timer.timeout.connect(self._on_handshake_time_sync_timeout)

        # Serial connection lost listener
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
        return shared_app_state.enqueue_query(command_name, dst_addr=dst_addr, **kwargs)

    def _send_command(self, command_name: str, dst_addr: int, **kwargs):
        if self._command_bus:
            return self._command_bus.send(command_name, dst_addr=dst_addr, **kwargs)
        kwargs.pop("traffic_class", None)
        return self._protocol.send_command(command_name, dst_addr=dst_addr, **kwargs)

    def send_command(self, command_name: str, dst_addr: int = VvAddress.CENTRAL, **kwargs):
        """Public model command path used by ViewModels when no CommandBus is injected."""
        return self._send_command(command_name, dst_addr=dst_addr, **kwargs)

    @staticmethod
    def _non_negative_value(value, default):
        try:
            numeric = int(value)
        except (TypeError, ValueError):
            return default
        return numeric if numeric >= 0 else default

    @classmethod
    def _sanitize_sys_config(cls, kwargs: dict) -> dict:
        sanitized = dict(kwargs)
        defaults = {
            "role": 1,
            "device_id": 1,
            "ranging_period_ms": 300,
            "rx_timeout_ms": 120,
            "uwb_channel": 5,
            "uwb_prf": 64,
            "uwb_data_rate": 2,
            "uwb_preamble_code": 9,
            "tx_antenna_delay": 16436,
            "rx_antenna_delay": 16436,
            "tx_power": 0,
            "power_mode": 3,
            "uwb_preamble_len": 0x34,
            "uwb_rx_pac": 2,
            "uwb_ns_sfd": 1,
            "uwb_phr_mode": 0,
            "pg_delay": 0xC2,
        }
        for key, default in defaults.items():
            if key in sanitized:
                sanitized[key] = cls._non_negative_value(sanitized[key], default)
        return sanitized

    @classmethod
    def _sanitize_pos_calib_config(cls, kwargs: dict) -> dict:
        sanitized = dict(kwargs)
        defaults = {
            "calib_anchor_id": 1,
            "samples": 10,
            "min_delta_step": 1,
            "max_rounds": 10,
            "iterations": 100,
        }
        for key, default in defaults.items():
            if key in sanitized:
                sanitized[key] = cls._non_negative_value(sanitized[key], default)
        return sanitized

    def request_end_session(self, reason: int = 0, await_completion: bool = False):
        """Request firmware/session shutdown through the shared command path."""
        # BE/API: session lifecycle action owned by Device Info flow.
        pkt = self._send_command(
            "end_session",
            dst_addr=VvAddress.MCU,
            reason=reason,
            traffic_class="manual",
        )
        if await_completion:
            if pkt is None:
                self._emit_end_session_failure("Failed to send end_session command.")
            else:
                seq = int(getattr(getattr(pkt, "hdr", None), "seq", 0) or 0)
                self._begin_end_session_confirmation(seq, reason)
        return pkt

    def request_ble_disconnect(self, reason: int = 0):
        """Disconnect current BLE peripheral through the shared command path."""
        # BE/API: BLE lifecycle action owned by Device Info flow.
        return self._send_command("ble_disconnect", dst_addr=VvAddress.CENTRAL, reason=reason)

    def request_anchor_layout(self, force: bool = False, traffic_class: str = ""):
        # BE/API: legacy backend helper for Config/Calibration orchestration.
        if self._is_anchor:
            log.debug("request_anchor_layout skipped: device is ANCHOR")
            return None
        return self._request_query("anchor_layout_get", dst_addr=VvAddress.MCU, force=force, cache_ttl_s=0.0 if force else None, traffic_class=traffic_class)

    def set_anchor_layout(self, anchors: list):
        # BE/API: legacy backend helper for Config/Calibration orchestration.
        if self._is_anchor:
            log.debug("set_anchor_layout skipped: device is ANCHOR")
            return None
        return self._send_command("anchor_layout_set", dst_addr=VvAddress.MCU, anchors=anchors)

    def request_ranging_config(self, force: bool = False, traffic_class: str = ""):
        # BE/API: legacy backend helper for Config tab orchestration.
        return self._request_query("sys_ranging_cfg_get", dst_addr=VvAddress.MCU, force=force, cache_ttl_s=0.0 if force else None, traffic_class=traffic_class)

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

    def request_sys_config(self, force: bool = False, traffic_class: str = ""):
        # BE/API: legacy backend helper for Config tab orchestration.
        return self._request_query("sys_config_get", dst_addr=VvAddress.MCU, force=force, cache_ttl_s=0.0 if force else None, traffic_class=traffic_class)

    def set_sys_config(self, **kwargs):
        # BE/API: legacy backend helper for Config tab orchestration.
        return self._send_command("sys_config_set", dst_addr=VvAddress.MCU, **self._sanitize_sys_config(kwargs))

    def request_sensor_fusion_config(self, force: bool = False, traffic_class: str = ""):
        # BE/API: legacy backend helper for Config tab orchestration.
        if self._is_anchor:
            log.debug("request_sensor_fusion_config skipped: device is ANCHOR")
            return None
        return self._request_query("sensor_fusion_cfg_get", dst_addr=VvAddress.MCU, force=force, cache_ttl_s=0.0 if force else None, traffic_class=traffic_class)

    def set_sensor_fusion_config(self, **kwargs):
        # BE/API: legacy backend helper for Config tab orchestration.
        if self._is_anchor:
            log.debug("set_sensor_fusion_config skipped: device is ANCHOR")
            return None
        return self._send_command("sensor_fusion_cfg_set", dst_addr=VvAddress.MCU, **kwargs)

    def request_pos_calib_config(self, force: bool = False, traffic_class: str = ""):
        # BE/API: legacy backend helper for Config tab orchestration.
        return self._request_query("pos_calib_cfg_get", dst_addr=VvAddress.MCU, force=force, cache_ttl_s=0.0 if force else None, traffic_class=traffic_class)

    def set_pos_calib_config(self, **kwargs):
        # BE/API: legacy backend helper for Config tab orchestration.
        return self._send_command("pos_calib_cfg_set", dst_addr=VvAddress.MCU, **self._sanitize_pos_calib_config(kwargs))

    def request_ble_conn_params(self, force: bool = False, traffic_class: str = ""):
        # BE/API: backend helper for Device Info BLE connection parameters.
        return self._request_query("ble_conn_params_get", dst_addr=VvAddress.CENTRAL, force=force, cache_ttl_s=0.0 if force else None, traffic_class=traffic_class)

    def request_rtos_resource(self, force: bool = False, traffic_class: str = ""):
        # BE/API: MCU RTOS heap/CPU/stack resource snapshot for Device Info.
        return self._request_query("rtos_resource_get", dst_addr=VvAddress.MCU, force=force, cache_ttl_s=0.0 if force else None, traffic_class=traffic_class)

    def request_rtos_task_stats(self, force: bool = False, traffic_class: str = ""):
        # BE/API: MCU per-task CPU/stack snapshot for Device Info.
        return self._request_query("rtos_task_stats_get", dst_addr=VvAddress.MCU, force=force, cache_ttl_s=0.0 if force else None, traffic_class=traffic_class)

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
            min_interval_ms=self._non_negative_value(min_interval_ms, 20),
            max_interval_ms=self._non_negative_value(max_interval_ms, 40),
            slave_latency=self._non_negative_value(slave_latency, 0),
            sup_timeout_ms=self._non_negative_value(sup_timeout_ms, 3000),
        )

    def set_ble_adv_config(self, enable: bool, serial_number: int, device_name: str):
        # BE/API: update peripheral BLE advertising payload/config.
        return self._send_command(
            "ble_adv_config_set",
            dst_addr=VvAddress.PERIPHERAL,
            enable=enable,
            serial_number=serial_number,
            device_name=device_name,
        )

    def set_host_transport(self, transport: int):
        # BE/API: select MCU host transport without touching flash/FOTA flows.
        return self._send_command("host_transport_set", dst_addr=VvAddress.MCU, transport=transport)


    def request_device_type(self, force: bool = False, traffic_class: str = ""):
        # BE/API: fetch MCU device type for the Config tab.
        return self._request_query("device_type_get", dst_addr=VvAddress.MCU, force=force, cache_ttl_s=0.0 if force else None, traffic_class=traffic_class)

    def set_device_type(self, device_type: int):
        # BE/API: update MCU device type from the Config tab.
        device_type = int(device_type)
        shared_app_state.device_type = device_type
        self.device_type_parsed.emit(device_type)
        pkt = self._send_command("device_type_set", dst_addr=VvAddress.MCU, device_type=device_type)
        if pkt is not None:
            if hasattr(pkt, "hdr") and hasattr(pkt.hdr, "seq"):
                self._pending_device_type_set_seq = int(pkt.hdr.seq)
        return pkt

    def write_factory_otp(
        self,
        confirm_magic: int = 0x4F545057,
        otp_type: int = 0,
        device_type: int = 2,
        tx_antenna_delay: int = 0,
        rx_antenna_delay: int = 0,
        value_u32: int = 0,
        value_u8: int = 0,
    ):
        # BE/API: Write factory OTP configuration to MCU (one-time irreversible).
        return self._send_command(
            "factory_otp_write",
            dst_addr=VvAddress.MCU,
            confirm_magic=confirm_magic,
            otp_type=otp_type,
            device_type=device_type,
            tx_antenna_delay=tx_antenna_delay,
            rx_antenna_delay=rx_antenna_delay,
            value_u32=value_u32,
            value_u8=value_u8,
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

    def request_calibration_status(self, force: bool = False, traffic_class: str = ""):
        return self._request_query(
            "calib_status_get",
            dst_addr=VvAddress.MCU,
            cache_ttl_s=0.0 if force else None,
            force=force,
            traffic_class=traffic_class,
        )

    def request_calibration_start(
        self,
        sample_target: int = 32,
        tag_x_m: float = 2.0,
        tag_y_m: float = 2.0,
        tag_z_m: float = 1.0,
    ):
        if self._is_anchor:
            log.debug("request_calibration_start skipped: device is ANCHOR")
            return None
        return self._send_command(
            "calib_start",
            dst_addr=VvAddress.MCU,
            sample_target=sample_target,
            tag_x_m=tag_x_m,
            tag_y_m=tag_y_m,
            tag_z_m=tag_z_m,
            reference_position_valid=True,
        )

    def request_calibration_stop(self):
        if self._is_anchor:
            log.debug("request_calibration_stop skipped: device is ANCHOR")
            return None
        return self._send_command("calib_stop", dst_addr=VvAddress.MCU)

    def request_calibration_candidate_apply(self, anchor_mask: int):
        if self._is_anchor:
            log.debug("request_calibration_candidate_apply skipped: device is ANCHOR")
            return None
        return self._send_command(
            "calib_candidate_apply",
            dst_addr=VvAddress.MCU,
            anchor_mask=anchor_mask,
        )

    def request_imu_reset(self):
        if self._is_anchor:
            log.debug("request_imu_reset skipped: device is ANCHOR")
            return None
        return self._send_command("imu_reset", dst_addr=VvAddress.MCU)

    def request_imu_calibration(self):
        if self._is_anchor:
            log.debug("request_imu_calibration skipped: device is ANCHOR")
            return None
        return self._send_command("imu_calib_start", dst_addr=VvAddress.MCU)

    def execute_for_target(self, target: dict | None, operation):
        """
        Run a config operation for the device that is currently connected.

        Config and calibration actions no longer auto-switch BLE targets.
        The UI is responsible for making the desired device the active
        connection first, then invoking the operation on that connection.
        """
        _ = target
        operation()
        return True

    @staticmethod
    def _normalize_mac(mac: str) -> str:
        return str(mac or "").strip().replace("-", ":").upper()

    @staticmethod
    def _disconnect_reason_from(resp) -> tuple[int, bool]:
        reason = int(getattr(resp, "disconnect_reason", 0) or 0) & 0xFF
        has_reason = reason != 0
        try:
            has_reason = bool(resp.HasField("disconnect_reason")) or has_reason
        except (AttributeError, ValueError):
            pass
        return reason, has_reason

    @staticmethod
    def _display_ble_state(state: int, connected_state: int) -> str:
        _ = connected_state
        return BLE_STATE_NAMES.get(int(state), f"BLE_STATE_UNKNOWN({int(state)})")

    def _emit_connection_progress(
        self,
        progress: int,
        message: str,
        *,
        phase: str = "connection",
        status: str = JobState.RUNNING,
        error_msg: str = "",
    ) -> None:
        progress = max(0, min(100, int(progress)))
        payload = {
            "phase": phase,
            "progress": progress,
            "message": message,
            "status": status,
            "retries": self._connect_retry_count,
            "error_msg": error_msg,
            "mac": self._pending_connect_mac or self._connected_mac,
            "name": self._pending_connect_name or self._connected_name,
        }
        self.connection_progress_changed.emit(payload)
        shared_app_state.update_job(
            "ble_connection",
            status,
            progress=progress,
            retries=self._connect_retry_count,
            error_msg=error_msg,
        )

    def _emit_ble_notification(
        self,
        *,
        kind: str,
        title: str,
        message: str,
        reason: dict | None = None,
        auto_close_ms: int = 3000,
    ) -> None:
        payload = {
            "kind": kind,
            "title": title,
            "message": message,
            "auto_close_ms": int(auto_close_ms),
        }
        if reason:
            payload.update({
                "reason_code": reason.get("code"),
                "reason_code_hex": reason.get("code_hex", "0x00"),
                "reason_name": reason.get("name", "Unknown HCI Error"),
            })
        self.ble_notification_requested.emit(payload)

    @staticmethod
    def _reason_text(reason: dict | None) -> str:
        if not reason:
            return ""
        code_hex = str(reason.get("code_hex") or "0x00")
        name = str(reason.get("name") or "Unknown HCI Error")
        return f"{code_hex} - {name}"

    def _reset_connect_attempts(self) -> None:
        self._connect_retry_count = 0
        self._connect_generation += 1
        self._connect_timeout_timer.stop()

    def _cancel_active_connect_flow(self) -> None:
        """Drop any in-flight connect handshake state before changing target."""
        self._connect_timeout_timer.stop()
        self._handshake_timeout_timer.stop()
        self._handshake_time_sync_timer.stop()
        self._pending_handshake_time_sync_seq = None
        self._active_connecting_handshake = False
        self._handshake_device_info_received = False
        self._handshake_sys_config_received = False
        self._handshake_time_sync_done = False
        self._handshake_final_ble_connected = False
        self._session_bootstrap_timer.stop()
        self._session_start_scheduled = False

    @staticmethod
    def _reset_query_pipeline() -> None:
        """Flush queued non-critical queries so connect/switch owns the wire."""
        try:
            shared_app_state.cancel_query_pipeline("connect/switch flow reset")
        except AttributeError:
            manager = getattr(shared_app_state, "_query_manager", None)
            if manager is not None:
                manager.reset()

    def _stop_device_session_flows(self, clear_received: bool = True) -> None:
        """Stop bootstrap and interval flows before switching BLE targets."""
        self._set_background_polling_enabled(False)
        self._background_scan_resume_timer.stop()
        self._session_bootstrap_timer.stop()
        self._session_start_scheduled = False
        self._session_bootstrap_done = False
        self._session_start_events_done = False
        self._log_stream_requested = False
        self._reset_time_sync_flow()
        if clear_received:
            self._received_query_payloads.clear()
            self._last_query_progress_report.clear()

    def _start_connect_handshake(self) -> None:
        """Run the strict post-connect handshake with no unrelated queries mixed in."""
        if not self._active_connecting_handshake:
            return
        if self._handshake_timeout_timer.isActive():
            return

        self._handshake_device_info_received = False
        self._handshake_sys_config_received = False
        self._handshake_time_sync_done = False
        self._pending_handshake_time_sync_seq = None
        self._received_query_payloads.clear()
        self._handshake_time_sync_timer.stop()
        self._session_bootstrap_timer.stop()
        self._session_start_scheduled = False
        self._set_background_polling_enabled(False)
        self._reset_query_pipeline()
        self._handshake_timeout_timer.start(6000)
        self._emit_connection_progress(
            65,
            "BLE link established. Reading device information...",
            phase="connecting",
            status=JobState.RUNNING,
        )
        self._request_query(
            "device_information_get",
            dst_addr=VvAddress.MCU,
            cache_ttl_s=0.0,
            force=True,
            traffic_class="connection",
        )
        log.info("Application connect handshake started: device_information_get -> time_sync_set -> ble_status_get.")

    def _schedule_connect_retry(
        self,
        mac_hex: str,
        name: str,
        *,
        reason: dict | None = None,
        detail: str = "",
    ) -> None:
        mac_hex = self._normalize_mac(mac_hex)
        if not mac_hex or self._pending_connect_mac != mac_hex:
            return

        self._connect_timeout_timer.stop()
        self._connect_retry_count += 1
        reason_text = ""
        if reason and reason.get("code"):
            reason_text = self._reason_text(reason)
        elif detail:
            reason_text = detail
        else:
            reason_text = "No BLE connected state received."

        if self._connect_retry_count > MAX_CONNECT_RETRIES:
            self._fail_connect_attempt(mac_hex, name, reason_text, reason=reason)
            return

        self._connection_status = "Connecting"
        shared_app_state.connection_status = "Connecting"
        self.connection_state_changed.emit({
            "name": name or mac_hex,
            "mac": mac_hex,
            "status": "Connecting",
        })
        self._emit_ble_notification(
            kind="connect_retry",
            title="BLE connect failed",
            message=f"{reason_text}. Retrying now...",
            reason=reason if reason and reason.get("code") else None,
            auto_close_ms=3000,
        )
        self._emit_connection_progress(
            73,
            f"Retry {self._connect_retry_count}: {reason_text}",
            status=JobState.RETRYING,
            error_msg=reason_text
        )

        generation = self._connect_generation
        QTimer.singleShot(
            CONNECT_RETRY_DELAY_MS,
            lambda: self._retry_connect(mac_hex, name, generation),
        )

    def _retry_connect(self, mac_hex: str, name: str, generation: int) -> None:
        if generation != self._connect_generation:
            return
        if self._pending_connect_mac != mac_hex:
            return
        self._background_scan_resume_timer.stop()
        self.stop_scan()
        self._do_connect(mac_hex, name)

    def _fail_connect_attempt(self, mac_hex: str, name: str, reason_text: str, *, reason: dict | None = None) -> None:
        self._cancel_active_connect_flow()
        self._reset_query_pipeline()
        self._pending_connect_mac = ""
        self._pending_connect_name = ""
        self._connected_mac = ""
        self._connected_name = ""
        self._connection_status = "Disconnected"
        shared_app_state.connection_status = "Disconnected"
        self.connection_state_changed.emit({
            "name": name or mac_hex or "-",
            "mac": mac_hex or "-",
            "status": "Disconnected",
        })
        self._emit_connection_progress(0, f"Connect failed: {reason_text}", status=JobState.FAILED, error_msg=reason_text)
        self._emit_ble_notification(
            kind="error",
            title="BLE connect failed",
            message=reason_text,
            reason=reason if reason and reason.get("code") else None,
            auto_close_ms=6000,
        )

    def _set_background_polling_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled) and bool(self._connected_mac) and not shared_app_state.manual_test_mode_enabled
        if enabled:
            if not self._ble_status_timer.isActive():
                self._ble_status_timer.start()
            if not self._rtos_task_stats_timer.isActive():
                self._rtos_task_stats_timer.start()
            return
        self._ble_status_timer.stop()
        self._rtos_task_stats_timer.stop()

    def _schedule_background_scan_after_connect(self) -> None:
        # Disable automatic background scan after connect by user request
        self._background_scan_resume_timer.stop()


    def _resume_background_scan_after_connect(self) -> None:
        # Disabled auto scan after connect by user request
        pass

    def _on_manual_test_mode_changed(self, enabled: bool) -> None:
        self._set_background_polling_enabled(not enabled)

    def _on_log_stream_state_changed(self, active: bool) -> None:
        if not active and self.is_connected:
            log.info("Log stream stopped. Refreshing baseline configuration and battery/BLE status queries to update UI.")
            self.request_initial_telemetry(force=False)
            self.request_session_start_events(force=False)

    # ======================================================================
    #  PUBLIC PROPERTIES (read-only access for ViewModel)
    # ======================================================================

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

    @property
    def _is_anchor(self) -> bool:
        """True if the currently connected device has role ANCHOR."""
        return self._connected_role == "ANCHOR"

    @property
    def connected_role(self) -> str:
        return self._connected_role

    @property
    def _connected_role(self) -> str:
        device = shared_app_state.connected_device
        role = device.get("Role") or device.get("device_role") or device.get("role")
        return str(role or "").strip().upper()

    @property
    def _role_known(self) -> bool:
        return self._connected_role in {"TAG", "ANCHOR"}

    def _initial_telemetry_complete(self) -> bool:
        required = (
            "device_information_resp",
            "sys_config_resp",
            "sys_ranging_cfg_resp",
            "pos_calib_cfg_resp",
            "calib_status_resp",
            "ranging_status_resp",
            "device_type_set",
        )
        if not all(self._query_received(name) for name in required):
            return False
        if self._role_known and not self._is_anchor:
            return self._query_received("anchor_layout_resp") and self._query_received("sensor_fusion_cfg_resp")
        return True

    def _session_start_events_complete(self) -> bool:
        return all(
            self._query_received(name)
            for name in (
                "battery_info_resp",
                "ble_status_resp",
                "ble_conn_params_resp",
                "rtos_resource_resp",
                "rtos_task_stats_resp",
            )
        )

    def _refresh_session_query_completion(self) -> None:
        if self._initial_telemetry_complete() and not self._session_bootstrap_done:
            self._session_bootstrap_done = True
            shared_app_state.update_job("initial_telemetry", JobState.SUCCESS, progress=100)
            log.info("Initial session telemetry complete from decoded payloads.")
        if self._session_start_events_complete() and not self._session_start_events_done:
            self._session_start_events_done = True
            shared_app_state.update_job("session_start_events", JobState.SUCCESS, progress=100)
            log.info("Session-start event telemetry complete from decoded payloads.")

    def _mark_query_received(self, response_name: str) -> None:
        name = str(response_name or "").strip()
        if name:
            self._received_query_payloads.add(name)
            self._refresh_session_query_completion()

    def _query_received(self, response_name: str) -> bool:
        return str(response_name or "").strip() in self._received_query_payloads

    def _log_query_progress_report(self, name: str, items: list[tuple[str, bool, bool]]) -> None:
        parts: list[str] = []
        signature: list[str] = []
        for response_name, is_done, is_applicable in items:
            if is_done:
                state = "OK"
            elif not is_applicable:
                state = "SKIPPED"
            else:
                state = "PENDING"
            signature.append(f"{response_name}:{state}")
            parts.append(f"{response_name}={state}")
        snapshot = tuple(signature)
        if self._last_query_progress_report.get(name) == snapshot:
            return
        self._last_query_progress_report[name] = snapshot
        log.info("[%s] %s", name, ", ".join(parts))

    @property
    def connection_status(self) -> str:
        return self._connection_status

    @property
    def pending_connect_mac(self) -> str:
        return self._pending_connect_mac

    # ======================================================================
    #  COMMAND METHODS (called by ViewModel)
    # ======================================================================

    def set_connected_device(self, name: str, mac: str):
        """Called by main.py / ViewModel after ScanPopup to seed initial state."""
        shared_app_state.clear_device_session_state()
        self._received_query_payloads.clear()
        self._connected_mac = mac
        self._connected_name = name
        self._connection_status = "Connected"
        self._session_bootstrap_done = False
        self._session_start_events_done = False
        self._log_stream_requested = False
        self._connected_grace_until = time.monotonic() + 1.5
        self._session_start_scheduled = False

        self._pending_connect_mac = ""
        self._pending_connect_name = ""
        self._connect_retry_count = 0
        self._connect_timeout_timer.stop()
        self._reset_time_sync_flow()
        dev_info = dict(shared_app_state.connected_device or {})
        dev_info.update({"name": name, "mac": mac})
        if not str(dev_info.get("Role") or "").strip() and str(dev_info.get("device_role") or "").strip():
            dev_info["Role"] = str(dev_info.get("device_role")).strip().upper()
        shared_app_state.connected_device = dev_info
        self.connection_state_changed.emit({
            "name": name, "mac": mac, "status": "Connected", "SwitchToLogTab": True
        })
        log.info("Connected device set: %s (%s)", name, mac)

        # BE/API: confirm connection state from dongle after seeding the device.
        # The connected BLE state is already known here from the scan/connect flow.
        # Keep ble_status_get on its dedicated polling/transition paths only.

        # Start periodic BLE status polling (10s interval)
        self._set_background_polling_enabled(True)

        # Keep the connected device stable briefly, then resume passive discovery
        # so the UI can show other advertising devices for switch-connect.
        self._schedule_background_scan_after_connect()

        # Start session bootstrap after a short grace period.
        self.schedule_session_start(delay_ms=1500, force=False)

    def schedule_session_start(self, delay_ms: int = 1500, force: bool = False):
        """Schedule the initial telemetry bootstrap after connect/reconnect."""
        if self._session_start_scheduled and not force:
            return False
        self._session_start_scheduled = True
        self._session_bootstrap_timer.start(max(0, delay_ms))
        return True

    def _run_scheduled_session_start(self):
        """Run connect handshake first; normal app APIs start only after it reaches 100%."""
        if self._active_connecting_handshake:
            self._start_connect_handshake()
            return

        self.request_initial_telemetry(force=False)
        self.request_session_start_events(force=False)

    def _check_handshake_completion(self):
        if not (self._handshake_device_info_received and self._handshake_time_sync_done and self._handshake_final_ble_connected):
            return

        log.info("Connect handshake complete: device info, time sync attempt, and final BLE connected state confirmed.")
        self._handshake_timeout_timer.stop()
        self._handshake_time_sync_timer.stop()
        self._pending_handshake_time_sync_seq = None
        self._active_connecting_handshake = False

        self._connection_status = "Connected"
        shared_app_state.connection_status = "Connected"
        self._pending_connect_mac = ""
        self._pending_connect_name = ""

        dev_info = shared_app_state.connected_device
        dev_info.update({"name": self._connected_name, "mac": self._connected_mac})
        shared_app_state.connected_device = dev_info

        self.connection_state_changed.emit({
            "name": self._connected_name,
            "mac": self._connected_mac,
            "status": "Connected",
            "SwitchToLogTab": True,
        })
        self._emit_connection_progress(100, f"Connected to {self._connected_name or self._connected_mac}.", phase="connected", status=JobState.SUCCESS)

        self._set_background_polling_enabled(True)
        self._schedule_background_scan_after_connect()
        self._session_start_scheduled = False
        generation = self._connect_generation
        QTimer.singleShot(350, lambda: self._start_post_connect_bootstrap(generation))

    def _start_post_connect_bootstrap(self, generation: int) -> None:
        if generation != self._connect_generation:
            return
        if self._connection_status != "Connected" or not self._connected_mac:
            return
        self._received_query_payloads.clear()
        self._last_query_progress_report.clear()
        shared_app_state.clear_query_payload_markers()
        self._session_bootstrap_done = False
        self._session_start_events_done = False
        self._log_stream_requested = False
        log.info("Starting full post-connect API bootstrap after connect settle delay.")
        self.request_initial_telemetry(force=False)
        self.request_session_start_events(force=False)

    def _on_handshake_timeout(self):
        log.warning("Connect handshake timed out before final BLE connected confirmation.")
        ble_state = shared_app_state.ble_status
        reason_code = ble_state.get("disconnect_reason")
        reason = None
        if reason_code:
            reason = normalize_hci_reason(reason_code)
        self._active_connecting_handshake = False
        self._handshake_time_sync_timer.stop()
        self._pending_handshake_time_sync_seq = None
        self.disconnect_device()
        message = "Device did not complete the connect handshake."
        if reason:
            message = f"{message} Reason: {self._reason_text(reason)}."
        self._emit_ble_notification(
            kind="error",
            title="Connection failed",
            message=message,
            reason=reason,
            auto_close_ms=8000,
        )

    def request_initial_telemetry(self, force: bool = False):
        """Fetch baseline/static state once after a device session starts."""
        requested = False

        log.info("Requesting initial device-session telemetry...")
        if not self._session_bootstrap_done:
            shared_app_state.update_job("initial_telemetry", JobState.RUNNING)

        if force or not self._query_received("device_information_resp"):
            requested = bool(self._request_query("device_information_get", dst_addr=VvAddress.MCU, force=force, cache_ttl_s=0.0 if force else None, traffic_class="bootstrap")) or requested

        self._start_time_sync_correction(reason="session_start")

        if force or not self._query_received("sys_config_resp"):
            requested = bool(self.request_sys_config(force=force, traffic_class="bootstrap")) or requested
        if force or not self._query_received("sys_ranging_cfg_resp"):
            requested = bool(self.request_ranging_config(force=force, traffic_class="bootstrap")) or requested
        if force or not self._query_received("pos_calib_cfg_resp"):
            requested = bool(self.request_pos_calib_config(force=force, traffic_class="bootstrap")) or requested
        # Baseline status APIs are fetched on every device session, even when
        # their tabs have not been opened yet.
        if force or not self._query_received("calib_status_resp"):
            requested = bool(self.request_calibration_status(force=force, traffic_class="bootstrap")) or requested
        if force or not self._query_received("ranging_status_resp"):
            requested = bool(self._request_query("ranging_status_get", dst_addr=VvAddress.MCU, force=force, cache_ttl_s=0.0 if force else None, traffic_class="bootstrap")) or requested
        if force or not self._query_received("device_type_set"):
            requested = bool(self.request_device_type(force=force, traffic_class="bootstrap")) or requested
        # BLE status is polled by the dedicated 10s background timer.
        # Do not mix ble_status_get into normal session bootstrap queries.

        if self._role_known:
            if force or not self._query_received("anchor_layout_resp"):
                requested = bool(self.request_anchor_layout(force=force, traffic_class="bootstrap")) or requested
            if force or not self._query_received("sensor_fusion_cfg_resp"):
                requested = bool(self.request_sensor_fusion_config(force=force, traffic_class="bootstrap")) or requested

        self._session_bootstrap_done = self._initial_telemetry_complete()
        self._log_query_progress_report(
            "initial_telemetry",
            [
                ("device_information_resp", self._query_received("device_information_resp"), True),
                ("sys_config_resp", self._query_received("sys_config_resp"), True),
                ("sys_ranging_cfg_resp", self._query_received("sys_ranging_cfg_resp"), True),
                ("pos_calib_cfg_resp", self._query_received("pos_calib_cfg_resp"), True),
                ("calib_status_resp", self._query_received("calib_status_resp"), True),
                ("ranging_status_resp", self._query_received("ranging_status_resp"), True),
                ("device_type_set", self._query_received("device_type_set"), True),
                ("anchor_layout_resp", self._query_received("anchor_layout_resp"), self._role_known and not self._is_anchor),
                ("sensor_fusion_cfg_resp", self._query_received("sensor_fusion_cfg_resp"), self._role_known and not self._is_anchor),
            ],
        )
        if self._session_bootstrap_done:
            shared_app_state.update_job("initial_telemetry", JobState.SUCCESS, progress=100)
            if not requested and not force:
                log.info("Initial session telemetry already available; skipping duplicate startup queries.")
        return requested

    def _start_time_sync_correction(self, reason: str = "manual") -> bool:
        return self._time_sync_manager.start(reason=reason)

    def _send_connect_time_sync_set(self) -> None:
        if not self._active_connecting_handshake:
            return
        try:
            pkt = self._send_command(
                "time_sync_set",
                dst_addr=VvAddress.MCU,
                unix_time_ms=int(time.time() * 1000),
                timezone_offset=self._host_timezone_offset_min(),
                traffic_class="connection",
            )
        except Exception as exc:
            log.warning("Connect time_sync_set failed to send; continuing handshake: %s", exc)
            self._complete_connect_time_sync_step("Time sync send failed; confirming final BLE state...")
            return

        if pkt is None:
            log.warning("Connect time_sync_set was not sent; continuing handshake.")
            self._complete_connect_time_sync_step("Time sync unavailable; confirming final BLE state...")
            return

        self._pending_handshake_time_sync_seq = int(pkt.hdr.seq)
        self._handshake_time_sync_timer.start(CONNECT_TIME_SYNC_ACK_TIMEOUT_MS)
        self._emit_connection_progress(82, "Setting device time...", phase="connecting", status=JobState.RUNNING)

    def _on_handshake_time_sync_timeout(self) -> None:
        if self._pending_handshake_time_sync_seq is None:
            return
        log.warning("Connect time_sync_set ACK timeout for seq=%s; continuing handshake.", self._pending_handshake_time_sync_seq)
        self._pending_handshake_time_sync_seq = None
        self._complete_connect_time_sync_step("Time sync timed out; confirming final BLE state...")

    def _complete_connect_time_sync_step(self, message: str) -> None:
        if not self._active_connecting_handshake:
            return
        self._handshake_time_sync_done = True
        self._emit_connection_progress(90, message, phase="connecting", status=JobState.RUNNING)
        if not self._handshake_final_ble_connected:
            self._request_query("ble_status_get", dst_addr=VvAddress.CENTRAL, cache_ttl_s=0.0, force=True, traffic_class="connection")
        self._check_handshake_completion()

    def _begin_end_session_confirmation(self, seq: int, reason: int) -> None:
        self._clear_end_session_confirmation()
        self._pending_end_session_seq = int(seq)
        self._pending_end_session_reason = int(reason or 0)
        self._pending_end_session_ack = False
        self._pending_end_session_state_seen = False
        self._suppress_next_disconnect_scan = True
        self._end_session_ack_timeout_timer.start(END_SESSION_ACK_TIMEOUT_MS)

    def _clear_end_session_confirmation(self) -> tuple[int | None, int]:
        seq = self._pending_end_session_seq
        reason = self._pending_end_session_reason
        self._pending_end_session_seq = None
        self._pending_end_session_reason = 0
        self._pending_end_session_ack = False
        self._pending_end_session_state_seen = False
        self._end_session_ack_timeout_timer.stop()
        self._end_session_poll_delay_timer.stop()
        self._end_session_status_poll_timer.stop()
        self._end_session_status_timeout_timer.stop()
        return seq, reason

    def _emit_end_session_success(self, state: int, state_name: str) -> None:
        seq, reason = self._clear_end_session_confirmation()
        self.end_session_result.emit({
            "success": True,
            "seq": seq,
            "reason": reason,
            "state": int(state),
            "state_name": state_name,
            "message": f"End session confirmed by BLE state {state_name}.",
        })

    def _emit_end_session_failure(self, message: str) -> None:
        seq, reason = self._clear_end_session_confirmation()
        self._suppress_next_disconnect_scan = False
        self.end_session_result.emit({
            "success": False,
            "seq": seq,
            "reason": reason,
            "message": message,
        })

    def _on_ack_received(self, ack_seq: int, response: int) -> None:
        if (
            hasattr(self, "_pending_device_type_set_seq")
            and self._pending_device_type_set_seq is not None
            and int(ack_seq) == int(self._pending_device_type_set_seq)
        ):
            self._pending_device_type_set_seq = None
            if int(response) == int(self._protocol.pb.PACKET_ACK_RESPONSE_ACK):
                log.info("device_type_set ACK received. Scheduling device_type_get in 1s.")
                QTimer.singleShot(1000, lambda: self.request_device_type(force=True))
            else:
                log.warning("device_type_set returned NACK response=%s", response)
            return

        if (
            self._pending_handshake_time_sync_seq is not None
            and int(ack_seq) == int(self._pending_handshake_time_sync_seq)
        ):
            self._handshake_time_sync_timer.stop()
            self._pending_handshake_time_sync_seq = None
            if int(response) == int(self._protocol.pb.PACKET_ACK_RESPONSE_ACK):
                self._complete_connect_time_sync_step("Time synchronized. Confirming final BLE state...")
            else:
                log.warning("Connect time_sync_set NACK response=%s; continuing handshake.", response)
                self._complete_connect_time_sync_step("Time sync skipped by device. Confirming final BLE state...")
            return

        pending_seq = self._pending_end_session_seq
        if pending_seq is None or int(ack_seq) != int(pending_seq):
            return
        self._end_session_ack_timeout_timer.stop()
        if int(response) != int(self._protocol.pb.PACKET_ACK_RESPONSE_ACK):
            try:
                response_name = self._protocol.pb.packet_ack_response_t.Name(int(response))
            except Exception:
                response_name = f"ACK_RESPONSE_{int(response)}"
            self._emit_end_session_failure(f"end_session returned {response_name}.")
            return
        self._pending_end_session_ack = True
        self._end_session_poll_delay_timer.start(250)
        self._end_session_status_timeout_timer.start(END_SESSION_STATUS_POLL_TIMEOUT_MS)

    def _on_end_session_ack_timeout(self) -> None:
        self._emit_end_session_failure("Timed out waiting for end_session ACK.")

    def _start_end_session_status_polling(self) -> None:
        if self._pending_end_session_seq is None or not self._pending_end_session_ack:
            return
        if not self._pending_end_session_state_seen and not self._end_session_status_poll_timer.isActive():
            self._poll_end_session_status()
            self._end_session_status_poll_timer.start()

    def _poll_end_session_status(self) -> None:
        if self._pending_end_session_seq is None or not self._pending_end_session_ack:
            self._end_session_status_poll_timer.stop()
            return
        try:
            self._request_query(
                "ble_status_get",
                dst_addr=VvAddress.CENTRAL,
                cache_ttl_s=0.0,
                force=True,
                traffic_class="manual",
            )
        except Exception as exc:
            log.error("Failed to poll end-session ble_status_get: %s", exc)

    def _on_end_session_status_timeout(self) -> None:
        self._emit_end_session_failure("Timed out waiting for BLE state after end_session.")

    def _queue_time_sync_set(self):
        return self._time_sync_manager._queue_set()

    def _queue_time_sync_verify(self):
        return self._time_sync_manager._queue_verify()

    def _host_timezone_offset_min(self) -> int:
        local_time_struct = time.localtime()
        timezone_offset = getattr(time, "timezone", 0)
        if getattr(time, "daylight", 0) and local_time_struct.tm_isdst:
            timezone_offset = getattr(time, "altzone", timezone_offset)
        return int((-timezone_offset) / 60)

    def send_time_sync_adv(self, device_type: int, device_id: int) -> bool:
        """Send a time sync advertising set command to a specific device."""
        host_time_ms = int(time.time() * 1000)
        tz_offset_min = self._host_timezone_offset_min()
        log.info("Sending time_sync_adv_set to MCU: type=%d, id=%d, time=%d, tz=%d",
        device_type, device_id, host_time_ms, tz_offset_min)
        return self._send_command(
            "time_sync_adv_set",
            dst_addr=VvAddress.MCU,
            device_type=device_type,
            device_id=device_id,
            unix_time_ms=host_time_ms,
            timezone_offset=tz_offset_min,
        )


    def _reset_time_sync_flow(self) -> None:
        self._time_sync_manager.reset()

    def _handle_time_sync_drift(self, time_diff_ms: int) -> None:
        if not self._connected_mac:
            self._time_sync_manager.reset()
            return
        if not self._time_sync_manager.is_active:
            self._start_time_sync_correction(reason=f"event drift {time_diff_ms}ms")
            return
        self._time_sync_manager._handle_drift(time_diff_ms)

    def request_session_start_events(self, force: bool = False):
        """Trigger session-start data events that should be fetched once per connection."""
        requested = False

        log.info("Requesting Device Info session-start data...")
        if not self._session_start_events_done:
            shared_app_state.update_job("session_start_events", JobState.RUNNING)

        if force or not self._query_received("battery_info_resp"):
            requested = bool(self._request_query("battery_info_get", dst_addr=VvAddress.MCU, cache_ttl_s=0.0, force=True, traffic_class="bootstrap")) or requested
        if force or not self._query_received("ble_conn_params_resp"):
            requested = bool(self.request_ble_conn_params(force=force, traffic_class="bootstrap")) or requested
        if force or not self._query_received("ble_status_resp"):
            requested = bool(self._request_query("ble_status_get", dst_addr=VvAddress.CENTRAL, force=force, cache_ttl_s=0.0 if force else None, traffic_class="bootstrap")) or requested
        if force or not self._query_received("rtos_resource_resp"):
            requested = bool(self.request_rtos_resource(force=force, traffic_class="bootstrap")) or requested
        if force or not self._query_received("rtos_task_stats_resp"):
            requested = bool(self.request_rtos_task_stats(force=force, traffic_class="bootstrap")) or requested

        self._session_start_events_done = self._session_start_events_complete()
        self._log_query_progress_report(
            "session_start_events",
            [
                ("battery_info_resp", self._query_received("battery_info_resp"), True),
                ("ble_conn_params_resp", self._query_received("ble_conn_params_resp"), True),
                ("ble_status_resp", self._query_received("ble_status_resp"), True),
                ("rtos_resource_resp", self._query_received("rtos_resource_resp"), True),
                ("rtos_task_stats_resp", self._query_received("rtos_task_stats_resp"), True),
            ],
        )
        if self._session_start_events_done:
            shared_app_state.update_job("session_start_events", JobState.SUCCESS, progress=100)
            if not requested and not force:
                log.info("Session start events already available; skipping duplicate event queries.")
        return requested

    def request_log_stream(self, force: bool = False):
        """Trigger firmware/device log streaming for the current connected device."""
        if not self._connected_mac:
            return False
        if self._log_stream_requested and not force:
            return False
        self._log_stream_requested = True
        # BE/API: incoming log_data packets update UI; this call is only the stream trigger.
        return self._send_command("log_data", dst_addr=VvAddress.MCU)


    def seed_scan_devices(self, devices: list[dict], emit: bool = True) -> None:
        """Seed scanned devices captured by the startup scan popup."""
        now = time.monotonic()
        for dev in devices or []:
            data = dict(dev or {})
            mac = self._normalize_mac(data.get("mac", ""))
            if not mac:
                continue
            data["mac"] = mac
            data["last_seen"] = now
            if mac not in self._scan_device_order:
                order = data.get("order")
                if order is None:
                    order = self._next_scan_device_order
                self._scan_device_order[mac] = int(order)
                self._next_scan_device_order = max(self._next_scan_device_order, int(order) + 1)
            data["order"] = self._scan_device_order[mac]
            current = self._adv_devices.get(mac, {})
            current.update(data)
            self._adv_devices[mac] = current
        if self._ble_scan_repo and hasattr(self._ble_scan_repo, "seed_devices"):
            self._ble_scan_repo.seed_devices(devices, emit=emit)
        elif emit:
            self._emit_merged_scan_data()

    def refresh_scan_results(self) -> None:
        """Re-emit the current advertising-device snapshot without starting a new BLE scan."""
        if self._ble_scan_repo:
            self.scan_data_updated.emit(self._ble_scan_repo.merged_results())
            return
        self._emit_merged_scan_data()

    def _connected_scan_preserve(self) -> list[dict]:
        mac = self._normalize_mac(self._connected_mac)
        if not mac:
            return []
        source = self._adv_devices.get(mac, {}).copy()
        if not source and self._ble_scan_repo and hasattr(self._ble_scan_repo, "get_device"):
            source = self._ble_scan_repo.get_device(mac)
        source.update({
            "mac": mac,
            "name": source.get("name") or self._connected_name or f"UWB-{mac[-5:]}",
            "last_seen": time.monotonic(),
            "order": 0,
        })
        return [source]

    def _reset_scan_cache_for_rescan(self) -> None:
        preserve = self._connected_scan_preserve()
        self._adv_devices.clear()
        self._adv_status_by_device_id.clear()
        self._scan_device_order.clear()
        self._next_scan_device_order = 0
        for item in preserve:
            mac = self._normalize_mac(item.get("mac", ""))
            if not mac:
                continue
            item = dict(item)
            item["mac"] = mac
            item["order"] = self._next_scan_device_order
            self._scan_device_order[mac] = self._next_scan_device_order
            self._next_scan_device_order += 1
            self._adv_devices[mac] = item
        if self._ble_scan_repo and hasattr(self._ble_scan_repo, "reset_for_rescan"):
            self._ble_scan_repo.reset_for_rescan(preserve, emit=True)
        elif self._ble_scan_repo and hasattr(self._ble_scan_repo, "clear"):
            self._ble_scan_repo.clear()
            if preserve and hasattr(self._ble_scan_repo, "seed_devices"):
                self._ble_scan_repo.seed_devices(preserve, emit=False)
        self._emit_merged_scan_data()

    def start_scan(self, clear_results: bool = False, force: bool = False, duration_ms: int = 5000):
        """Start a finite user-triggered BLE advertising scan and merge results."""
        if self._is_scanning:
            log.info("BLE scan already active; ignoring duplicate scan request.")
            return False

        duration_ms = max(0, int(duration_ms or 0))
        if clear_results:
            self._reset_scan_cache_for_rescan()

        sent = self._send_command(
            "ble_scan_start",
            src_addr=self._protocol.pb.PACKET_ADDR_HOST,
            dst_addr=self._protocol.pb.PACKET_ADDR_CENTRAL,
            duration_ms=duration_ms,
            traffic_class="manual",
        )
        if sent is None:
            log.warning("ble_scan_start was not sent; manual scan did not start.")
            return False

        self._is_scanning = True
        shared_app_state.ble_scan_active = True
        if duration_ms > 0:
            self._manual_scan_stop_timer.start(duration_ms + 250)
        log.info("Manual BLE scan started for %d ms", duration_ms)
        return True

    def stop_scan(self):
        """Stop BLE advertising scan without clearing the discovered device list."""
        self._manual_scan_stop_timer.stop()
        if self._is_scanning:
            self._send_command(
                "ble_scan_stop",
                src_addr=self._protocol.pb.PACKET_ADDR_HOST,
                dst_addr=self._protocol.pb.PACKET_ADDR_CENTRAL,
                traffic_class="manual",
            )
            self._is_scanning = False
            shared_app_state.ble_scan_active = False
            self._prune_timer.stop()
            self._emit_merged_scan_data()
        else:
            shared_app_state.ble_scan_active = False

    def connect_device(self, mac_hex: str):
        """
        Connect to a device from the advertising list.
        Flow: if another device is active, send ble_disconnect and wait until
        dongle reports a non-connected BLE state before sending ble_connect.
        """
        mac_hex = self._normalize_mac(mac_hex)
        if not mac_hex:
            return

        log.info("Connect request: %s", mac_hex)
        repo_device = {}
        if self._ble_scan_repo and hasattr(self._ble_scan_repo, "get_device"):
            repo_device = self._ble_scan_repo.get_device(mac_hex)
        name = self._adv_devices.get(mac_hex, {}).get("name") or repo_device.get("name") or "Unknown"

        if self._connected_mac == mac_hex and self._connection_status == "Connected":
            log.info("Already connected to %s. Ignoring connect request.", mac_hex)
            self._emit_connection_progress(100, f"Already connected to {name or mac_hex}.", status=JobState.SUCCESS)
            return


        if self._pending_connect_mac != mac_hex:
            self._reset_connect_attempts()

        self._pending_connect_mac = mac_hex
        self._pending_connect_name = name

        if self._connected_mac and self._connected_mac != mac_hex:
            if self._connection_status == "Connecting":
                self._cancel_active_connect_flow()
            log.info("Switching BLE target: stop scan before disconnecting %s and connecting %s", self._connected_mac, mac_hex)
            self._emit_connection_progress(10, f"Switching target device to {name or mac_hex}...", status=JobState.RUNNING)
            self._stop_device_session_flows(clear_received=True)
            self._connect_timeout_timer.stop()
            self._reset_query_pipeline()
            self.stop_scan()
            QTimer.singleShot(STOP_TO_CONNECT_DELAY_MS, self.disconnect_device)
            return

        if self._connection_status == "Disconnecting":
            log.info("Disconnect in progress; pending connect to %s will start after disconnect confirms.", mac_hex)
            return

        self._background_scan_resume_timer.stop()
        self.stop_scan()
        self._emit_connection_progress(30, f"Connecting to {name or mac_hex}...", phase="connecting", status=JobState.RUNNING)
        QTimer.singleShot(STOP_TO_CONNECT_DELAY_MS, lambda: self._do_connect(mac_hex, name))

    def disconnect_device(self, reason: int = 0):
        if not self._connected_mac and self._connection_status != "Connecting":
            return False

        current_name = self._connected_name or "-"
        current_mac = self._connected_mac or "-"
        self._manual_scan_stop_timer.stop()
        self._stop_device_session_flows(clear_received=True)
        self._reset_query_pipeline()
        try:
            self._send_command(
                "ble_disconnect",
                src_addr=self._protocol.pb.PACKET_ADDR_HOST,
                dst_addr=self._protocol.pb.PACKET_ADDR_CENTRAL,
                reason=reason,
            )
            log.info("ble_disconnect sent for %s (%s)", current_name, current_mac)
            self._emit_connection_progress(30, f"Disconnecting {current_name}...", status=JobState.RUNNING)
        except Exception as exc:
            log.warning("ble_disconnect failed: %s", exc)
            return False

        self._background_scan_resume_timer.stop()
        self._cancel_active_connect_flow()
        self._connect_timeout_timer.stop()
        self._connection_status = "Disconnecting"
        shared_app_state.connection_status = "Disconnecting"
        self._set_background_polling_enabled(False)
        self._session_bootstrap_timer.stop()
        self._reset_time_sync_flow()
        self.connection_state_changed.emit({
            "name": current_name,
            "mac": current_mac,
            "status": "Disconnecting",
        })
        if not self._ble_transition_timer.isActive():
            self._ble_transition_timer.start()
        return True

    def _do_connect(self, mac_hex: str, name: str):
        """Actually send ble_connect after scan has stopped."""
        if self._pending_connect_mac != mac_hex:
            return

        self._cancel_active_connect_flow()
        self._stop_device_session_flows(clear_received=True)
        self._active_connecting_handshake = True
        self._handshake_final_ble_connected = False
        self._set_background_polling_enabled(False)
        self._reset_query_pipeline()
        try:
            mac_bytes = bytes.fromhex(mac_hex.replace(":", ""))
            sent = self._send_command(
                "ble_connect",
                src_addr=self._protocol.pb.PACKET_ADDR_HOST,
                dst_addr=self._protocol.pb.PACKET_ADDR_CENTRAL,
                mac_address=mac_bytes
            )
            if sent is None:
                raise RuntimeError("ble_connect command was not sent")
            log.info("ble_connect sent for %s (%s)", name, mac_hex)
            self._emit_connection_progress(50, f"Connecting to {name or mac_hex}...", status=JobState.RUNNING)
        except Exception as e:
            log.error("ble_connect failed: %s", e)
            self._schedule_connect_retry(
                mac_hex,
                name,
                detail=f"Failed to send ble_connect: {e}",
            )
            return

        self._connected_mac = mac_hex
        self._connected_name = name
        self._connection_status = "Connecting"
        shared_app_state.connection_status = "Connecting"
        self.connection_state_changed.emit({
            "name": name, "mac": mac_hex, "status": "Connecting"
        })
        self._connect_timeout_timer.start(CONNECT_TIMEOUT_MS)
        if not self._ble_transition_timer.isActive():
            self._ble_transition_timer.start()

    def _on_connect_timeout(self):
        if self._connection_status != "Connecting":
            return
        if not self._pending_connect_mac:
            return
        self._schedule_connect_retry(
            self._pending_connect_mac,
            self._pending_connect_name or self._pending_connect_mac,
            detail="Timed out waiting for BLE_STATE_CONNECTED",
        )

    def on_connection_lost(self):
        """Called when dongle is physically disconnected."""
        log.warning("Dongle physically disconnected!")
        if self._pending_end_session_seq is not None:
            self._emit_end_session_failure("Dongle disconnected while waiting for end_session confirmation.")
        shared_app_state.clear_device_session_state()
        self._connected_mac = ""
        self._connected_name = ""
        self._connection_status = "Disconnected"
        self._is_scanning = False
        self._session_bootstrap_done = False
        self._session_start_events_done = False
        self._received_query_payloads.clear()
        self._log_stream_requested = False
        self._session_start_scheduled = False

        self._connected_grace_until = 0.0
        self._pending_connect_mac = ""
        self._pending_connect_name = ""
        self._connect_retry_count = 0
        self._connect_generation += 1
        self._prune_timer.stop()
        self._ble_status_timer.stop()
        self._rtos_task_stats_timer.stop()
        self._ble_transition_timer.stop()
        self._connect_timeout_timer.stop()
        self._background_scan_resume_timer.stop()
        self._manual_scan_stop_timer.stop()
        shared_app_state.ble_scan_active = False
        self._session_bootstrap_timer.stop()
        self._handshake_timeout_timer.stop()
        self._handshake_time_sync_timer.stop()
        self._pending_handshake_time_sync_seq = None
        self._active_connecting_handshake = False
        self._reset_time_sync_flow()

        self.connection_state_changed.emit({
            "name": "-", "mac": "-", "status": "Disconnected"
        })
        self._emit_connection_progress(0, "BLE: Disconnected", status=JobState.IDLE)

    # ======================================================================
    #  PACKET HANDLER - Parse protocol responses into clean dicts
    # ======================================================================

    def _on_packet(self, param_name: str, pkt):
        if shared_app_state.manual_test_mode_enabled:
            return
        if param_name == "device_information_resp":
            self._handle_device_info(pkt.device_information_resp)
        elif param_name == "battery_info_resp":
            if self._telemetry_repo is None:
                self._handle_battery_info(pkt.battery_info_resp)
        elif param_name == "ble_status_resp":
            self._handle_ble_status(pkt.ble_status_resp)
        elif param_name == "time_sync_resp":
            self._handle_time_sync(pkt.time_sync_resp)
        elif param_name == "ble_scan_result":
            if self._ble_scan_repo is None:
                self._handle_scan_result(pkt.ble_scan_result)
        elif param_name == "ble_adv_status":
            if self._ble_scan_repo is None:
                self._handle_adv_status(pkt.ble_adv_status)
        elif param_name == "ble_conn_params_resp":
            self._handle_ble_conn_params(pkt.ble_conn_params_resp)
        elif param_name == "sys_config_resp":
            if self._config_repo is None:
                self._handle_sys_config(pkt.sys_config_resp)
        elif param_name == "sys_ranging_cfg_resp":
            if self._config_repo is None:
                self._handle_sys_ranging_cfg(pkt.sys_ranging_cfg_resp)
        elif param_name == "sensor_fusion_cfg_resp":
            if self._config_repo is None:
                self._handle_sensor_fusion_cfg(pkt.sensor_fusion_cfg_resp)
        elif param_name == "pos_calib_cfg_resp":
            if self._config_repo is None:
                self._handle_pos_calib_cfg(pkt.pos_calib_cfg_resp)
        elif param_name == "device_type_set":
            if self._config_repo is None:
                self._handle_device_type(pkt.device_type_set)
        elif param_name == "anchor_layout_resp":
            self._mark_query_received("anchor_layout_resp")
        elif param_name == "calib_status_resp":
            self._mark_query_received("calib_status_resp")
        elif param_name == "ranging_status_resp":
            self._mark_query_received("ranging_status_resp")

    def _on_repository_battery_info(self, info: dict) -> None:
        self._mark_query_received("battery_info_resp")
        payload = {key: value for key, value in dict(info or {}).items() if key != "device_key"}
        self.battery_info_parsed.emit(payload)

    def _on_repository_scan_results(self, merged_list: list) -> None:
        self._adv_devices = {}
        self._adv_status_by_device_id = {}
        self._scan_device_order.clear()
        self._next_scan_device_order = 0
        for index, item in enumerate(merged_list or []):
            device = dict(item or {})
            mac = self._normalize_mac(device.get("mac", ""))
            if not mac:
                continue
            device["mac"] = mac
            order = int(device.get("order", index) or index)
            device["order"] = order
            self._adv_devices[mac] = device
            self._scan_device_order[mac] = order
            self._next_scan_device_order = max(self._next_scan_device_order, order + 1)
            for candidate in self._adv_status_merge_candidates(device):
                if candidate:
                    self._adv_status_by_device_id[candidate] = device.copy()
        self.scan_data_updated.emit([dict(item) for item in (merged_list or [])])

    def _on_repository_sys_config(self, cfg_dict: dict) -> None:
        self._mark_query_received("sys_config_resp")
        self.sys_config_parsed.emit(dict(cfg_dict or {}))

    def _on_repository_sys_ranging_cfg(self, cfg_dict: dict) -> None:
        self._mark_query_received("sys_ranging_cfg_resp")
        self.sys_ranging_cfg_parsed.emit(dict(cfg_dict or {}))

    def _on_repository_sensor_fusion_cfg(self, cfg_dict: dict) -> None:
        self._mark_query_received("sensor_fusion_cfg_resp")
        self.sensor_fusion_cfg_parsed.emit(dict(cfg_dict or {}))

    def _on_repository_pos_calib_cfg(self, cfg_dict: dict) -> None:
        self._mark_query_received("pos_calib_cfg_resp")
        self.pos_calib_cfg_parsed.emit(dict(cfg_dict or {}))

    def _on_repository_device_type(self, device_type: int) -> None:
        self._mark_query_received("device_type_set")
        self.device_type_parsed.emit(int(device_type or 0))

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
        self._mark_query_received("device_information_resp")
        self.device_info_parsed.emit(info)
        
        dev = shared_app_state.connected_device
        dev.update(info)
        shared_app_state.connected_device = dev

        if self._active_connecting_handshake and self._handshake_timeout_timer.isActive():
            self._handshake_device_info_received = True
            self._emit_connection_progress(72, "Device information received.", phase="connecting", status=JobState.RUNNING)
            self._send_connect_time_sync_set()
            return

        if self._connected_mac:
            self.request_initial_telemetry(force=False)

    def _handle_device_type(self, resp):
        device_type = int(getattr(resp, 'device_type', 0))
        shared_app_state.device_type = device_type
        self.device_type_parsed.emit(device_type)

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
        self._mark_query_received("battery_info_resp")
        self.battery_info_parsed.emit(info)
        if not self._telemetry_repo:
            shared_app_state.battery_info = info

    def _handle_ble_status(self, resp):
        state = int(getattr(resp, 'state', 0) or 0)
        rssi = int(getattr(resp, 'rssi_dbm', 0) or 0)
        reason_code, has_reason = self._disconnect_reason_from(resp)
        reason = normalize_hci_reason(reason_code)

        state_str = BLE_STATE_NAMES.get(state, f"BLE_STATE_UNKNOWN({state})")
        if has_reason and reason_code:
            log.info(
                "Received ble_status_resp: state=%d (%s), rssi=%d dBm, reason=%s (%s)",
                state,
                state_str,
                rssi,
                reason["code_hex"],
                reason["name"],
            )
        else:
            log.info("Received ble_status_resp: state=%d (%s), rssi=%d dBm", state, state_str, rssi)

        pb = self._protocol.pb
        display_state = self._display_ble_state(state, pb.BLE_STATE_CONNECTED)
        ble_info = {
            "state": state,
            "state_name": state_str,
            "display_state": display_state,
            "rssi_dbm": rssi,
            "disconnect_reason": reason_code,
            "disconnect_reason_hex": reason["code_hex"],
            "disconnect_reason_name": reason["name"],
        }
        self._mark_query_received("ble_status_resp")
        self.ble_status_parsed.emit(ble_info)

        curr_ble = shared_app_state.ble_status
        curr_ble.update(ble_info)
        shared_app_state.ble_status = curr_ble

        if (
            self._pending_end_session_seq is not None
            and self._pending_end_session_ack
            and state in END_SESSION_VALID_STATES
        ):
            had_connected_device = bool(self._connected_mac)
            self._pending_end_session_state_seen = True
            self._emit_end_session_success(state, state_str)
            if not had_connected_device:
                self._suppress_next_disconnect_scan = False

        if state == pb.BLE_STATE_CONNECTING and self._connected_mac:
            if self._connection_status == "Disconnecting":
                return
            if self._connection_status != "Connecting":
                log.info("Dongle reported BLE_STATE_CONNECTING for %s.", self._connected_mac)
                self._connection_status = "Connecting"
                shared_app_state.connection_status = "Connecting"
                target_mac = self._pending_connect_mac or self._connected_mac or "-"
                target_name = self._pending_connect_name or self._connected_name or "device"
                self.connection_state_changed.emit({
                    "name": target_name,
                    "mac": target_mac,
                    "status": "Connecting",
                })
            else:
                target_name = self._pending_connect_name or self._connected_name or "device"
            self._emit_connection_progress(
                73,
                f"Establishing link to {target_name}...",
                phase="connecting",
                status=JobState.RUNNING
            )
            return

        if state == pb.BLE_STATE_CONNECTED and self._connected_mac:
            if self._connection_status == "Disconnecting":
                return
            if self._pending_connect_mac and self._pending_connect_mac != self._connected_mac:
                return

            if self._active_connecting_handshake:
                self._handshake_final_ble_connected = True
                self._connect_timeout_timer.stop()
                self._ble_transition_timer.stop()
                self._connection_status = "Connecting"
                shared_app_state.connection_status = "Connecting"
                if not (self._handshake_device_info_received and self._handshake_time_sync_done):
                    log.debug(
                        "BLE_STATE_CONNECTED received while connect handshake is waiting: "
                        "device_info=%s time_sync=%s",
                        self._handshake_device_info_received,
                        self._handshake_time_sync_done,
                    )
                    self._start_connect_handshake()
                self._check_handshake_completion()
                return

            log.info("Dongle confirmed BLE_STATE_CONNECTED. Initiating application handshake...")
            self._connect_timeout_timer.stop()
            self._ble_transition_timer.stop()

            if self._connection_status != "Connected":
                self._connect_retry_count = 0
                self._session_bootstrap_done = False
                self._session_start_events_done = False
                self._log_stream_requested = False
                
                self._emit_connection_progress(
                    60,
                    "BLE link established. Waiting for device handshake...",
                    phase="connecting",
                    status=JobState.RUNNING
                )
                
                self.schedule_session_start(delay_ms=250, force=True)

            return

        elif self._connected_mac and state not in (
            pb.BLE_STATE_CONNECTED,
            pb.BLE_STATE_CONNECTING,
        ):
            if state in (pb.BLE_STATE_SCANNING, pb.BLE_STATE_IDLE) and self._connection_status in ("Connected", "Connecting") and not reason_code:
                # Dongle is scanning/idle normally while connected or connecting, with no error reason. Ignore it.
                return
            previous_status = self._connection_status
            previous_name = self._connected_name
            previous_mac = self._connected_mac
            next_mac = self._pending_connect_mac
            next_name = self._pending_connect_name
            switch_requested = bool(next_mac and next_mac != previous_mac)
            connect_failed = previous_status == "Connecting" and next_mac == previous_mac
            normal_disconnect = previous_status == "Connected" and not switch_requested and not connect_failed

            log.warning(
                "BLE state changed to %d while device was connected/disconnecting; current=%s next=%s reason=%s (%s).",
                state,
                previous_mac,
                next_mac or "-",
                reason["code_hex"],
                reason["name"],
            )
            self._connected_mac = ""
            self._connected_name = ""
            if switch_requested:
                self._connection_status = "Connecting"
                shared_app_state.connection_status = "Connecting"
            else:
                self._connection_status = "Disconnected"
                shared_app_state.connection_status = "Disconnected"
            self._session_bootstrap_done = False
            self._session_start_events_done = False
            self._log_stream_requested = False
            self._session_start_scheduled = False

            shared_app_state.connected_device = {}
            if not switch_requested:
                self._active_connecting_handshake = False
                self._handshake_timeout_timer.stop()
                self._handshake_time_sync_timer.stop()
                self._pending_handshake_time_sync_seq = None
            self._ble_status_timer.stop()
            self._rtos_task_stats_timer.stop()
            self._ble_transition_timer.stop()
            self._connect_timeout_timer.stop()
            self._background_scan_resume_timer.stop()
            self._session_bootstrap_timer.stop()
            self._reset_time_sync_flow()
            display_name = (next_name or next_mac or "-") if switch_requested else (previous_name or "-")
            display_mac = (next_mac or "-") if switch_requested else (previous_mac or "-")
            self.connection_state_changed.emit({
                "name": display_name, "mac": display_mac, "status": self._connection_status
            })
            if switch_requested:
                self._emit_connection_progress(40, f"Connecting to {next_name or next_mac}...", status=JobState.RUNNING)
            elif not connect_failed:
                self._emit_connection_progress(0, "BLE: Disconnected", status=JobState.IDLE)

            if normal_disconnect and has_reason and reason_code:
                self._emit_ble_notification(
                    kind="disconnect",
                    title="BLE disconnected",
                    message=f"State: {state_str}",
                    reason=reason,
                    auto_close_ms=8000,
                )

            if connect_failed:
                detail = f"Dongle reported {state_str} before connection completed"
                self._schedule_connect_retry(
                    next_mac,
                    next_name or previous_name or next_mac,
                    reason=reason if has_reason and reason_code else None,
                    detail=detail,
                )
            elif switch_requested:
                log.info(
                    "BLE disconnect confirmed for %s. Scheduling connect to %s after 1000 ms.",
                    previous_mac,
                    next_mac,
                )
                QTimer.singleShot(1000, lambda: self._do_connect(next_mac, next_name))
            else:
                self._pending_connect_mac = ""
                self._pending_connect_name = ""
                if self._suppress_next_disconnect_scan:
                    self._suppress_next_disconnect_scan = False
                else:
                    self.start_scan()

    def _poll_ble_status(self):
        """Poll BLE status only when no higher-priority stream is active."""
        if not self._connected_mac:
            return
        if shared_app_state.ranging_active or shared_app_state.log_streaming:
            log.debug("Skipping ble_status_get while ranging/log stream is active.")
            return
        log.debug("Polling BLE status from dongle...")
        try:
            self._request_query("ble_status_get", dst_addr=VvAddress.CENTRAL, cache_ttl_s=0.0, force=True, traffic_class="background")
        except Exception as e:
            log.error("Failed to send ble_status_get: %s", e)

    def _poll_ble_transition_status(self):
        if self._connection_status not in ("Connecting", "Disconnecting"):
            self._ble_transition_timer.stop()
            return
        try:
            self._request_query("ble_status_get", dst_addr=VvAddress.CENTRAL, cache_ttl_s=0.0, force=True, traffic_class="connection")
        except Exception as exc:
            log.error("Failed to poll transition ble_status_get: %s", exc)

    # _poll_battery_info removed: battery_info is received via device telemetry push (1s auto-stream).

    def _handle_ble_conn_params(self, resp):
        p = getattr(resp, 'params', None)
        if p is None:
            return
        self._mark_query_received("ble_conn_params_resp")
        # ByteSize()==0: BLE params sub-message is empty or not configured.
        # Emit {} so UI keeps placeholder values instead of fake zeroes.
        if p.ByteSize() == 0:
            self.ble_conn_params_parsed.emit({})
            return
        self.ble_conn_params_parsed.emit({
            "min_interval_ms": getattr(p, 'min_interval_ms', 0),
            "max_interval_ms": getattr(p, 'max_interval_ms', 0),
            "slave_latency": getattr(p, 'slave_latency', 0),
            "sup_timeout_ms": getattr(p, 'sup_timeout_ms', 0),
            "phy": getattr(p, 'phy', "-"),
        })

    def _handle_time_sync(self, resp):
        """Publish event-driven time state and correct drift only when needed."""
        result = self._time_sync_manager.handle_response(resp)
        self.time_sync_result.emit(result)

    def _handle_sys_config(self, resp):
        self._mark_query_received("sys_config_resp")
        if not resp.HasField("config"):
            # Empty packet: emit {} so UI resets to placeholder "-".
            self.sys_config_parsed.emit({})
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
            "uwb_preamble_len": cfg.uwb_preamble_len,
            "uwb_rx_pac": cfg.uwb_rx_pac,
            "uwb_ns_sfd": cfg.uwb_ns_sfd,
            "uwb_phr_mode": cfg.uwb_phr_mode,
            "smart_tx_power": cfg.smart_tx_power,
            "pg_delay": cfg.pg_delay,
        }
        self.sys_config_parsed.emit(cfg_dict)
        # sys_config_resp belongs to the post-connect API bootstrap, not the connect gate.

    def _handle_sys_ranging_cfg(self, resp):
        self._mark_query_received("sys_ranging_cfg_resp")
        if not resp.HasField("config"):
            # Empty packet: emit {} so UI resets to placeholder "-".
            self.sys_ranging_cfg_parsed.emit({})
            return
        cfg = resp.config
        cfg_dict = {
            "rx_timeout_ms": cfg.rx_timeout_ms,
            "ranging_period_ms": cfg.ranging_period_ms,
        }
        self.sys_ranging_cfg_parsed.emit(cfg_dict)

    def _handle_sensor_fusion_cfg(self, resp):
        self._mark_query_received("sensor_fusion_cfg_resp")
        if not resp.HasField("config"):
            # Empty packet: emit {} so UI resets to placeholder "-".
            self.sensor_fusion_cfg_parsed.emit({})
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
        self._mark_query_received("pos_calib_cfg_resp")
        if not resp.HasField("config"):
            # Empty packet: emit {} so UI resets to placeholder "-".
            self.pos_calib_cfg_parsed.emit({})
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
            "last_pair_error_mean_m": cfg.last_pair_error_mean_m,
            "iterations_taken": cfg.iterations_taken,
            "last_pair_error_spread_m": cfg.last_pair_error_spread_m,
            "last_pair_std_mean_m": cfg.last_pair_std_mean_m,
            "last_usable_pair_count": cfg.last_usable_pair_count,
            "last_rejected_pair_count": cfg.last_rejected_pair_count,
            "rejected_batch_count": cfg.rejected_batch_count,
            "last_pair_error_rms_m": cfg.last_pair_error_rms_m,
            "last_pair_error_max_abs_m": cfg.last_pair_error_max_abs_m,
            "last_pair_error_mean_abs_m": cfg.last_pair_error_mean_abs_m,
        }
        self.pos_calib_cfg_parsed.emit(cfg_dict)


    # Scan result handling

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
        timestamp_s = int(getattr(res, 'local_timestamp_s', 0) or 0)
        timestamp_ms = int(getattr(res, 'local_timestamp_ms', 0) or 0)
        if timestamp_ms <= 0 and timestamp_s > 0:
            timestamp_ms = timestamp_s * 1000
        elif timestamp_s <= 0 and timestamp_ms > 0:
            timestamp_s = timestamp_ms // 1000
        status_data = {
            "device_type": getattr(res, 'device', 0),
            "device_id": getattr(res, 'device_id', 0),
            "bat_soc_percent": getattr(res, 'bat_soc_percent', 0),
            "local_timestamp_s": timestamp_s,
            "local_timestamp_ms": timestamp_ms,
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
            adv_status = {}
            for candidate in self._adv_status_merge_candidates(d):
                if candidate in self._adv_status_by_device_id:
                    adv_status = self._adv_status_by_device_id.get(candidate, {})
                    break
            item = d.copy()
            item.update(adv_status)
            merged_list.append(item)

        merged_list.sort(key=lambda x: x.get("order", 0))
        self.scan_data_updated.emit(merged_list)

    @staticmethod
    def _adv_status_name_candidate(device: dict) -> int:
        name = str(device.get("name") or "").strip()
        match = re.search(r"(\d+)$", name)
        if not match:
            return 0
        try:
            return int(match.group(1))
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _adv_status_merge_candidates(cls, device: dict) -> tuple[int, ...]:
        serial_number = int(device.get("serial_number") or 0)
        device_id = int(device.get("device_id") or 0)
        name_candidate = cls._adv_status_name_candidate(device)
        candidates = []
        for candidate in (
            device_id,
            serial_number,
            serial_number & 0xFFFF if serial_number else 0,
            name_candidate,
        ):
            if candidate and candidate not in candidates:
                candidates.append(candidate)
        return tuple(candidates)

    def _prune_devices(self):
        """Do not auto-remove or age-out discovered devices during runtime."""
        return

    def _clear_scan_cache(self) -> None:
        self._adv_devices.clear()
        self._adv_status_by_device_id.clear()
        self._scan_device_order.clear()
        self._next_scan_device_order = 0
        if self._ble_scan_repo and hasattr(self._ble_scan_repo, "clear"):
            self._ble_scan_repo.clear()
        self.scan_data_updated.emit([])
