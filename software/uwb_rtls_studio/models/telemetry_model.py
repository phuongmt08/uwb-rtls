"""Telemetry state model with validity and freshness tracking."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from PyQt6.QtCore import QObject, QTimer, pyqtSignal


FRESHNESS_NEVER = "never_received"
FRESHNESS_FRESH = "fresh"
FRESHNESS_STALE = "stale"
FRESHNESS_FAILED = "failed"


@dataclass
class TelemetryField:
    value: object | None = None
    valid: bool = False
    received_at: float | None = None
    freshness: str = FRESHNESS_NEVER

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "valid": self.valid,
            "received_at": self.received_at,
            "freshness": self.freshness,
        }


@dataclass
class TelemetrySnapshot:
    battery: dict[str, TelemetryField] = field(default_factory=dict)
    ble_status: dict[str, TelemetryField] = field(default_factory=dict)
    rtos_resource: dict[str, TelemetryField] = field(default_factory=dict)
    rtos_tasks: list[dict] = field(default_factory=list)


class TelemetryModel(QObject):
    battery_updated = pyqtSignal(dict)
    ble_status_updated = pyqtSignal(dict)
    rtos_resource_updated = pyqtSignal(dict)
    rtos_task_stats_updated = pyqtSignal(list)
    telemetry_snapshot_updated = pyqtSignal(dict)
    telemetry_freshness_changed = pyqtSignal(str, str)

    BATTERY_KEYS = (
        "bat_voltage_mv",
        "bat_soc_percent",
        "remaining_min",
        "is_charging",
        "mcu_temp_c",
        "mcu_voltage_mv",
        "vdda_mv",
        "uwb_temp_c",
        "uwb_voltage_mv",
        "uwb_vbat_mv",
        "imu_temp_c",
        "error_mask",
    )
    BLE_KEYS = (
        "state",
        "state_name",
        "display_state",
        "rssi_dbm",
        "disconnect_reason",
        "disconnect_reason_hex",
        "disconnect_reason_name",
        "conn_interval",
        "slave_latency",
        "supervision_timeout",
        "phy",
    )
    RTOS_KEYS = (
        "sample_window_ms",
        "cpu_busy_permille",
        "cpu_busy_percent",
        "heap_free_bytes",
        "heap_min_ever_free_bytes",
        "min_stack_free_bytes",
        "min_stack_task_id",
        "task_count",
        "health_flags",
    )

    def __init__(self, stale_after_s: float = 30.0, parent=None):
        super().__init__(parent)
        self.stale_after_s = float(stale_after_s)
        self._snapshot = TelemetrySnapshot()
        self._freshness_timer = QTimer(self)
        self._freshness_timer.setInterval(1000)
        self._freshness_timer.timeout.connect(self.refresh_freshness)
        self._freshness_timer.start()

    def handle_battery_info(self, data: dict, received_at: float | None = None) -> dict:
        now = time.time() if received_at is None else float(received_at)
        self._update_group(self._snapshot.battery, self.BATTERY_KEYS, data, now)
        flat = self._flatten_group(self._snapshot.battery)
        flat.update({"valid": True, "source": "device_rx", "received_at": now})
        self.battery_updated.emit(flat)
        self._emit_snapshot()
        return flat

    def handle_ble_status(self, data: dict, received_at: float | None = None) -> dict:
        return self._handle_ble_update(data, received_at)

    def handle_ble_conn_params(self, data: dict, received_at: float | None = None) -> dict:
        return self._handle_ble_update(data, received_at)

    def _handle_ble_update(self, data: dict, received_at: float | None = None) -> dict:
        now = time.time() if received_at is None else float(received_at)
        self._update_group(self._snapshot.ble_status, self.BLE_KEYS, data, now)
        flat = self._flatten_group(self._snapshot.ble_status)
        flat.update({"valid": True, "source": "device_rx", "received_at": now})
        self.ble_status_updated.emit(flat)
        self._emit_snapshot()
        return flat

    def handle_rtos_resource(self, data: dict, received_at: float | None = None) -> dict:
        now = time.time() if received_at is None else float(received_at)
        self._update_group(self._snapshot.rtos_resource, self.RTOS_KEYS, data, now)
        flat = self._flatten_group(self._snapshot.rtos_resource)
        flat.update({"valid": True, "source": "device_rx", "received_at": now})
        self.rtos_resource_updated.emit(flat)
        self._emit_snapshot()
        return flat

    def handle_rtos_task_stats(self, tasks: list[dict], received_at: float | None = None) -> list[dict]:
        now = time.time() if received_at is None else float(received_at)
        self._snapshot.rtos_tasks = [dict(item, valid=True, received_at=now) for item in tasks]
        self.rtos_task_stats_updated.emit(self.rtos_task_stats)
        self._emit_snapshot()
        return self.rtos_task_stats

    def mark_query_failed(self, command_name: str) -> None:
        group_name = self._group_for_command(command_name)
        if not group_name:
            return
        group = getattr(self._snapshot, group_name)
        for field in group.values():
            field.freshness = FRESHNESS_FAILED
        self.telemetry_freshness_changed.emit(group_name, FRESHNESS_FAILED)
        self._emit_snapshot()

    def snapshot(self) -> dict:
        return {
            "battery": {key: field.to_dict() for key, field in self._snapshot.battery.items()},
            "ble_status": {key: field.to_dict() for key, field in self._snapshot.ble_status.items()},
            "rtos_resource": {key: field.to_dict() for key, field in self._snapshot.rtos_resource.items()},
            "rtos_tasks": self.rtos_task_stats,
        }

    def display_snapshot(self) -> dict:
        data = {}
        battery = self._flatten_group(self._snapshot.battery)
        data.update({
            "bat_soc_percent": battery.get("bat_soc_percent"),
            "bat_voltage_str": self._fmt_voltage(battery.get("bat_voltage_mv")),
            "remaining_str": self._fmt_remaining(battery.get("remaining_min")),
            "charging_str": self._fmt_bool(battery.get("is_charging")),
            "mcu_temp_str": self._fmt_temp(battery.get("mcu_temp_c")),
            "uwb_temp_str": self._fmt_temp(battery.get("uwb_temp_c")),
            "imu_temp_str": self._fmt_temp(battery.get("imu_temp_c")),
            "vdda_str": self._fmt_voltage(battery.get("vdda_mv", battery.get("mcu_voltage_mv"))),
            "uwb_vbat_str": self._fmt_voltage(battery.get("uwb_vbat_mv", battery.get("uwb_voltage_mv"))),
        })
        rtos = self._flatten_group(self._snapshot.rtos_resource)
        data.update({
            "heap_usage": self._fmt_bytes(rtos.get("heap_free_bytes")),
            "stack_usage": self._fmt_bytes(rtos.get("min_stack_free_bytes")),
            "cpu_usage": self._fmt_percent(rtos.get("cpu_busy_percent")),
        })
        return data

    @property
    def rtos_task_stats(self) -> list[dict]:
        return [item.copy() for item in self._snapshot.rtos_tasks]

    def is_stale(self, group_name: str) -> bool:
        group = getattr(self._snapshot, group_name, {})
        return any(field.freshness == FRESHNESS_STALE for field in group.values())

    def refresh_freshness(self) -> None:
        now = time.time()
        for group_name in ("battery", "ble_status", "rtos_resource"):
            group = getattr(self._snapshot, group_name)
            changed = False
            for field in group.values():
                if not field.valid or field.received_at is None:
                    continue
                freshness = FRESHNESS_STALE if now - field.received_at > self.stale_after_s else FRESHNESS_FRESH
                if freshness != field.freshness:
                    field.freshness = freshness
                    changed = True
            if changed:
                state = FRESHNESS_STALE if self.is_stale(group_name) else FRESHNESS_FRESH
                self.telemetry_freshness_changed.emit(group_name, state)
        self._emit_snapshot()

    def _update_group(self, group: dict[str, TelemetryField], keys: tuple[str, ...], data: dict, now: float) -> None:
        for key in keys:
            if key not in data:
                continue
            group[key] = TelemetryField(
                value=data.get(key),
                valid=True,
                received_at=now,
                freshness=FRESHNESS_FRESH,
            )

    @staticmethod
    def _flatten_group(group: dict[str, TelemetryField]) -> dict:
        return {key: field.value for key, field in group.items() if field.valid}

    def _emit_snapshot(self) -> None:
        self.telemetry_snapshot_updated.emit(self.snapshot())

    @staticmethod
    def _group_for_command(command_name: str) -> str:
        if command_name == "battery_info_get":
            return "battery"
        if command_name == "ble_status_get":
            return "ble_status"
        if command_name == "rtos_resource_get":
            return "rtos_resource"
        return ""

    @staticmethod
    def _fmt_voltage(value) -> str:
        if value is None:
            return "--"
        return f"{float(value) / 1000.0:.2f}V"

    @staticmethod
    def _fmt_remaining(value) -> str:
        if value is None:
            return "--"
        return f"{int(value)} min"

    @staticmethod
    def _fmt_bool(value) -> str:
        if value is None:
            return "--"
        return "Yes" if bool(value) else "No"

    @staticmethod
    def _fmt_temp(value) -> str:
        if value is None:
            return "--"
        return f"{float(value):.1f} C"

    @staticmethod
    def _fmt_bytes(value) -> str:
        if value is None:
            return "--"
        value = int(value)
        if value >= 1024:
            return f"{value / 1024.0:.1f} KB"
        return f"{value} B"

    @staticmethod
    def _fmt_percent(value) -> str:
        if value is None:
            return "--"
        return f"{float(value):.1f}%"

