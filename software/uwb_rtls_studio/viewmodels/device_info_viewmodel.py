"""
===============================================================================
  UWB RTLS Studio — Device Info ViewModel
===============================================================================
  File        : viewmodels/device_info_viewmodel.py
  Description : ViewModel cho tab "Device Info" (Tab 1).
                Hiển thị thông tin chi tiết device đang connected.

  MVVM Role   : VIEWMODEL — Presentation Logic Only

  Thread Model:
    - Main GUI Thread: Binds Model signals and View updates synchronously on this thread.
    - Lắng nghe Model signals → format data → emit UI signals
    - KHÔNG gọi protocol.send_command() trực tiếp
    - KHÔNG giữ duplicate state (connected_mac, is_scanning, etc.)
    - Tất cả state đều lấy từ DeviceModel

  Event-driven Architecture:
    - Host only fetches initial telemetry baseline once on connection.
    - All further updates are pushed by Firmware automatically.
===============================================================================
"""
import logging
import time
from PyQt6.QtCore import QObject, pyqtSignal

log = logging.getLogger(__name__)


class DeviceInfoViewModel(QObject):
    """
    ViewModel for Device Info tab.

    Signals emitted (consumed by DeviceInfoTab View):
      - device_info_updated(dict)             : device identity fields
      - ble_info_updated(dict)                : BLE connection info
      - telemetry_updated(dict)               : battery + temperature + voltage
      - advertising_devices_updated(list,bool) : scan list + is_scanning flag
      - time_sync_updated(str, bool, bool)    : time_str, is_synced, is_syncing
    """

    # ── Signals cho View ─────────────────────────────────────────────
    device_info_updated = pyqtSignal(dict)
    ble_info_updated = pyqtSignal(dict)
    telemetry_updated = pyqtSignal(dict)
    advertising_devices_updated = pyqtSignal(list, bool)   # list of dicts, is_scanning
    time_sync_updated = pyqtSignal(str, bool, bool)        # local_time_str, is_synced, is_syncing
    connection_progress_updated = pyqtSignal(dict)
    ble_notification_requested = pyqtSignal(dict)

    def __init__(
        self,
        device_model,
        dongle_model=None,
        telemetry_repo=None,
        ble_scan_repo=None,
        telemetry_model=None,
        parent=None,
    ):
        super().__init__(parent)
        self.model = device_model
        self.dongle_model = dongle_model
        self._telemetry_repo = telemetry_repo
        self._ble_scan_repo = ble_scan_repo
        self._telemetry_model = telemetry_model
        self._last_telemetry: dict = {}
        self._is_developer_mode = False
        self._last_rtos_resource: dict = {}
        self._last_rtos_tasks: list[dict] = []

        # ── Bind Model signals → ViewModel presentation ─────────────
        self.model.device_info_parsed.connect(self._on_device_info_parsed)
        
        if self._telemetry_repo:
            self._telemetry_repo.telemetry_updated.connect(self._on_battery_info_parsed)
        else:
            self.model.battery_info_parsed.connect(self._on_battery_info_parsed)
            
        self.model.ble_status_parsed.connect(self._on_ble_status_parsed)
        self.model.ble_conn_params_parsed.connect(self._on_ble_conn_params_parsed)
        self.model.time_sync_result.connect(self._on_time_sync_result)
        
        if self._ble_scan_repo:
            self._ble_scan_repo.scan_results_updated.connect(self._on_scan_data_updated)
        else:
            self.model.scan_data_updated.connect(self._on_scan_data_updated)
            
        self.model.connection_state_changed.connect(self._on_connection_state_changed)
        if hasattr(self.model, "connection_progress_changed"):
            self.model.connection_progress_changed.connect(self.connection_progress_updated.emit)
        if hasattr(self.model, "ble_notification_requested"):
            self.model.ble_notification_requested.connect(self.ble_notification_requested.emit)

        from utils.app_state import shared_app_state
        shared_app_state.rtos_resource_changed.connect(self._on_rtos_resource_changed)
        shared_app_state.rtos_task_stats_changed.connect(self._on_rtos_task_stats_changed)
        shared_app_state.device_type_changed.connect(self._on_device_type_changed)

        # ── Handle Dongle Connection Lifecycle ───────────────────────
        if self.dongle_model:
            self.dongle_model.dongle_verified.connect(self._on_dongle_reconnected)


    # ═══════════════════════════════════════════════════════════════════
    #  INITIALIZATION
    # ═══════════════════════════════════════════════════════════════════

    def initialize(self):
        """Called once by main.py after MainWindow is shown and all signals are wired.
        Triggers initial telemetry and session start events for the connected device.
        """
        if self.model.is_connected:
            self.model.schedule_session_start(delay_ms=1500, force=True)

    # ═══════════════════════════════════════════════════════════════════
    #  PUBLIC METHODS (called by main.py or View)
    # ═══════════════════════════════════════════════════════════════════

    def set_connected_device(self, name: str, mac: str):
        """Called by main.py after ScanPopup finishes, for command/session routing."""
        self.model.set_connected_device(name, mac)

    def connect_device(self, mac_hex: str):
        """Called by View when user clicks Connect on a scanned device."""
        self.model.connect_device(mac_hex)

    def disconnect_device(self, reason: int = 0):
        """Called by View when user clicks Disconnect on the connected scanned device."""
        return self.model.disconnect_device(reason=reason)

    def send_time_sync_adv(self, device_type: int, device_id: int):
        """Forward time sync advertising set command to the model."""
        self.model.send_time_sync_adv(device_type, device_id)

    def request_end_session(self, reason: int = 0):
        """Forward session shutdown request to the model command path."""
        # BE/API: session shutdown from Device Info flow.
        return self.model.request_end_session(reason=reason)

    def request_ble_disconnect(self, reason: int = 0):
        """Forward BLE disconnect request to the model command path."""
        # BE/API: BLE disconnect from Device Info flow.
        return self.model.request_ble_disconnect(reason=reason)



    # ═══════════════════════════════════════════════════════════════════
    def set_developer_mode(self, enabled: bool):
        self._is_developer_mode = bool(enabled)
        self._emit_rtos_telemetry()

    #  DONGLE LIFECYCLE
    # ═══════════════════════════════════════════════════════════════════

    def _on_dongle_reconnected(self, info_dict: dict):
        """Dongle auto-reconnected and verified."""
        if info_dict.get("verified"):
            log.info("Dongle reconnected; MainWindow will reopen reconnect popups.")

    # ═══════════════════════════════════════════════════════════════════
    #  PRESENTATION LOGIC (Model signal → format → UI signal)
    # ═══════════════════════════════════════════════════════════════════

    def _on_device_info_parsed(self, data: dict):
        """Forward device info with connected device name/mac merged."""
        merged = {
            "Device Name": self.model.connected_name,
            "MAC Address": self.model.connected_mac,
        }
        merged.update(data)
        self.device_info_updated.emit(merged)

    def _on_battery_info_parsed(self, data: dict):
        """Format telemetry data before sending to View."""
        if self._telemetry_model:
            if data.get("source") != "device_rx":
                self._telemetry_model.handle_battery_info(data)
            formatted_data = self._telemetry_model.display_snapshot()
        else:
            formatted_data = {
                "bat_soc_percent": data.get("bat_soc_percent"),
                "bat_voltage_str": self._format_voltage(data.get("bat_voltage_mv")),
                "remaining_str": self._format_remaining(data.get("remaining_min")),
                "charging_str": self._format_bool(data.get("is_charging")),
                "mcu_temp_str": self._format_temp(data.get("mcu_temp_c")),
                "uwb_temp_str": self._format_temp(data.get("uwb_temp_c")),
                "imu_temp_str": self._format_temp(data.get("imu_temp_c")),
                "vdda_str": self._format_voltage(data.get("vdda_mv")),
                "uwb_vbat_str": self._format_voltage(data.get("uwb_vbat_mv")),
                "heap_usage": data.get("heap_usage", "-"),
                "stack_usage": data.get("stack_usage", "-"),
                "cpu_usage": data.get("cpu_usage", "-")
            }
        self._last_telemetry.update(formatted_data)
        self.telemetry_updated.emit(self._last_telemetry.copy())

    def _on_rtos_resource_changed(self, data: dict):
        """Merge RTOS resource data into the telemetry panel without clearing battery data."""
        self._last_rtos_resource = dict(data or {})
        if self._telemetry_model:
            self._telemetry_model.handle_rtos_resource(self._last_rtos_resource)
        self._emit_rtos_telemetry()

    def _on_rtos_task_stats_changed(self, tasks: list):
        """Merge RTOS per-task stats into the telemetry panel without polling UI code."""
        self._last_rtos_tasks = [dict(item) for item in (tasks or [])]
        if self._telemetry_model:
            self._telemetry_model.handle_rtos_task_stats(self._last_rtos_tasks)
        self._emit_rtos_telemetry()

    def _on_device_type_changed(self, device_type: int):
        from utils.constants import DEVICE_TYPE_LABELS
        from utils.app_state import shared_app_state
        type_str = DEVICE_TYPE_LABELS.get(device_type, str(device_type))
        dev = shared_app_state.connected_device
        dev["Type"] = type_str
        shared_app_state._connected_device["Type"] = type_str
        merged = {
            "Device Name": self.model.connected_name,
            "MAC Address": self.model.connected_mac,
        }
        merged.update(dev)
        self.device_info_updated.emit(merged)

    def _emit_rtos_telemetry(self):
        if self._telemetry_model:
            self._last_telemetry.update(self._telemetry_model.display_snapshot())

        self._last_telemetry.update({
            "heap_usage": self._format_rtos_heap(),
            "stack_usage": self._format_rtos_stack(),
            "cpu_usage": self._format_rtos_cpu(),
        })
        self.telemetry_updated.emit(self._last_telemetry.copy())

    def _format_rtos_heap(self) -> str:
        data = self._last_rtos_resource
        if not data:
            return "-"

        free_bytes = data.get("heap_free_bytes")
        min_free = data.get("heap_min_ever_free_bytes")
        total = data.get("heap_total_bytes")

        if self._is_developer_mode:
            parts = []
            if total is not None and free_bytes is not None:
                used = max(0, int(total) - int(free_bytes))
                current_pct = (used / int(total) * 100.0) if int(total) else 0.0
                parts.append(f"used={current_pct:.1f}% ({used}/{int(total)} B)")
                if min_free is not None:
                    peak_used = max(0, int(total) - int(min_free))
                    peak_pct = (peak_used / int(total) * 100.0) if int(total) else 0.0
                    parts.append(f"peak={peak_pct:.1f}%")
            else:
                parts.append(f"free={self._format_bytes(free_bytes)}")
                parts.append(f"min_ever_free={self._format_bytes(min_free)}")
            parts.append(f"sample={int(data.get('sample_window_ms', 0))} ms")
            parts.append(f"tasks={int(data.get('task_count', 0))}")
            parts.append(f"health=0x{int(data.get('health_flags', 0)):X}")
            return " | ".join(parts)

        if total is not None and free_bytes is not None:
            used = max(0, int(total) - int(free_bytes))
            pct = (used / int(total) * 100.0) if int(total) else 0.0
            return f"Used {pct:.1f}% ({used}/{int(total)} B)"
        return f"Free {self._format_bytes(free_bytes)}"

    def _format_rtos_stack(self) -> str:
        tasks = self._last_rtos_tasks
        resource = self._last_rtos_resource
        if not tasks and not resource:
            return "-"

        stack_values = [int(t.get("stack_min_free_bytes", 0)) for t in tasks]
        percent_mode = bool(stack_values) and max(stack_values) <= 100

        if self._is_developer_mode:
            parts = []
            min_free = resource.get("min_stack_free_bytes")
            min_task = resource.get("min_stack_task_id")
            if min_free is not None:
                label = f"min_free={self._format_bytes(min_free)}"
                if min_task is not None:
                    label += f" task={int(min_task)}"
                parts.append(label)
            if stack_values:
                avg = sum(stack_values) / len(stack_values)
                parts.append(f"avg={self._format_stack_value(avg, percent_mode)}")
                parts.extend(
                    f"{self._task_label(task)}:{self._format_stack_value(task.get('stack_min_free_bytes'), percent_mode)}"
                    for task in tasks
                )
            return " | ".join(parts) if parts else "-"

        if stack_values:
            avg = sum(stack_values) / len(stack_values)
            if percent_mode:
                return f"Avg {avg:.1f}%"
            return f"Avg free {self._format_bytes(avg)}"
        return self._format_bytes(resource.get("min_stack_free_bytes"))

    def _format_rtos_cpu(self) -> str:
        tasks = self._last_rtos_tasks
        resource = self._last_rtos_resource
        active_cpu = self._active_cpu_percent(tasks, resource)

        if self._is_developer_mode:
            parts = []
            resource_cpu = resource.get("cpu_busy_percent")
            if resource_cpu is not None:
                parts.append(f"busy={float(resource_cpu):.1f}%")
            if active_cpu is not None:
                parts.append(f"active={float(active_cpu):.1f}%")
            parts.extend(
                f"{self._task_label(task)}:{self._task_cpu_percent(task):.1f}%"
                for task in tasks
            )
            return " | ".join(parts) if parts else "-"

        if active_cpu is not None:
            return f"{float(active_cpu):.1f}%"
        resource_cpu = resource.get("cpu_busy_percent")
        return f"{float(resource_cpu):.1f}%" if resource_cpu is not None else "-"

    @classmethod
    def _active_cpu_percent(cls, tasks: list[dict], resource: dict):
        idle_values = [cls._task_cpu_percent(task) for task in tasks if cls._is_idle_task(task)]
        if idle_values:
            return max(0.0, min(100.0, 100.0 - sum(idle_values)))

        non_idle = [cls._task_cpu_percent(task) for task in tasks if not cls._is_idle_task(task)]
        if non_idle:
            return max(0.0, min(100.0, sum(non_idle)))
        return resource.get("cpu_busy_percent")

    @staticmethod
    def _task_cpu_percent(task: dict) -> float:
        if task.get("cpu_percent") is not None:
            return float(task.get("cpu_percent"))
        return float(task.get("cpu_permille", 0)) / 10.0

    @staticmethod
    def _is_idle_task(task: dict) -> bool:
        name = str(task.get("name", "")).strip().lower()
        return name in {"idle", "tskidle", "idle task"} or "idle" in name

    @staticmethod
    def _task_label(task: dict) -> str:
        name = str(task.get("name", "")).strip()
        return name or f"T{int(task.get('task_id', 0))}"

    def _format_stack_value(self, value, percent_mode: bool) -> str:
        if value is None:
            return "-"
        value = float(value)
        if percent_mode:
            return f"{value:.1f}%"
        return self._format_bytes(value)

    @staticmethod
    def _format_bytes(value):
        if value is None:
            return "-"
        try:
            value = int(value)
        except (TypeError, ValueError):
            return "-"
        if value >= 1024:
            return f"{value / 1024.0:.1f} KB"
        return f"{value} B"

    @staticmethod
    def _format_voltage(value):
        if value is None:
            return "-"
        return f"{float(value) / 1000.0:.2f}V"

    @staticmethod
    def _format_remaining(value):
        if value is None:
            return "-"
        return f"{int(value)} min"

    @staticmethod
    def _format_bool(value):
        if value is None:
            return "-"
        return "Yes" if bool(value) else "No"

    @staticmethod
    def _format_temp(value):
        if value is None:
            return "-"
        return f"{float(value):.1f} C"

    def _on_ble_status_parsed(self, info: dict):
        """Forward BLE status to View and telemetry state."""
        payload = {
            "state": info.get("state"),
            "state_name": info.get("state_name"),
            "display_state": info.get("display_state"),
            "rssi_dbm": info.get("rssi_dbm"),
            "disconnect_reason": info.get("disconnect_reason"),
            "disconnect_reason_hex": info.get("disconnect_reason_hex"),
            "disconnect_reason_name": info.get("disconnect_reason_name"),
        }
        if self._telemetry_model:
            payload.update(self._telemetry_model.handle_ble_status(payload))
        self.ble_info_updated.emit(payload)

    def _on_ble_conn_params_parsed(self, params: dict):
        """Forward BLE connection parameters to View and telemetry state."""
        payload = {
            "conn_interval": f"{params.get('min_interval_ms', 0)} - {params.get('max_interval_ms', 0)} ms",
            "slave_latency": params.get("slave_latency"),
            "supervision_timeout": params.get("sup_timeout_ms"),
            "phy": params.get("phy", "-"),
        }
        if self._telemetry_model:
            payload.update(self._telemetry_model.handle_ble_conn_params(payload))
        self.ble_info_updated.emit(payload)

    def _on_connection_state_changed(self, info: dict):
        """Model reports connection state change -> emit a complete UI snapshot."""
        payload = {
            "Status": info.get("status", "-"),
            "SwitchToLogTab": info.get("SwitchToLogTab", False),
            "Device Name": info.get("name", self.model.connected_name or "-"),
            "MAC Address": info.get("mac", self.model.connected_mac or "-"),
        }
        self.device_info_updated.emit(payload)
        if info.get("status") == "Connected" and info.get("SwitchToLogTab"):
            self.model.schedule_session_start(delay_ms=1500, force=True)

    def _on_time_sync_result(self, data: dict):
        """Convert raw time sync data → formatted UI signal."""
        dev_time_ms = data["dev_time_ms"]
        is_synced = data["is_synced"]
        was_corrected = data["was_corrected"]

        # Format the device's time to a readable string
        try:
            dev_time_sec = dev_time_ms / 1000.0
            dt_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(dev_time_sec))
        except Exception:
            dt_str = "Invalid Time"

        # Only show Syncing while correction is still outside the accepted threshold.
        self.time_sync_updated.emit(dt_str, is_synced, was_corrected and not is_synced)

    def _on_scan_data_updated(self, merged_list: list):
        """Forward scan data to View with scanning state from Model."""
        self.advertising_devices_updated.emit(merged_list, self.model.is_scanning)
