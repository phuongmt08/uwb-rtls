"""
Repository for calibration and RTOS diagnostics packets.

Diagnostics are shared by several tabs, so this repository parses the protobuf
responses once and publishes a single state snapshot through SharedAppState.
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from utils.app_state import shared_app_state


class DiagnosticsRepository(QObject):
    calib_status_updated = pyqtSignal(dict)
    rtos_resource_updated = pyqtSignal(dict)
    rtos_task_stats_updated = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._calib_status: dict = {}
        self._rtos_resource: dict = {}
        self._rtos_task_stats: list[dict] = []

    @property
    def calib_status(self) -> dict:
        return self._calib_status.copy()

    @property
    def rtos_resource(self) -> dict:
        return self._rtos_resource.copy()

    @property
    def rtos_task_stats(self) -> list[dict]:
        return [item.copy() for item in self._rtos_task_stats]

    def handle_packet(self, param_name: str, pkt) -> bool:
        if param_name == "calib_status_resp":
            self.save_calib_status(self.parse_calib_status(pkt.calib_status_resp))
            return True
        if param_name == "rtos_resource_resp":
            self.save_rtos_resource(self.parse_rtos_resource(pkt.rtos_resource_resp))
            return True
        if param_name == "rtos_task_stats_resp":
            self.save_rtos_task_stats(self.parse_rtos_task_stats(pkt.rtos_task_stats_resp))
            return True
        return False

    def parse_calib_status(self, resp) -> dict:
        return {
            "state": int(getattr(resp, "state", 0)),
            "progress_percent": int(getattr(resp, "progress_percent", 0)),
            "current_iteration": int(getattr(resp, "current_iteration", 0)),
            "total_iterations": int(getattr(resp, "total_iterations", 0)),
            "last_pair_error_mean_m": float(getattr(resp, "last_pair_error_mean_m", 0.0)),
            "current_antenna_delay": int(getattr(resp, "current_antenna_delay", 0)),
            "peer_ready_mask": int(getattr(resp, "peer_ready_mask", 0)),
            "last_pair_error_spread_m": float(getattr(resp, "last_pair_error_spread_m", 0.0)),
            "rejected_batch_count": int(getattr(resp, "rejected_batch_count", 0)),
            "last_pair_error_rms_m": float(getattr(resp, "last_pair_error_rms_m", 0.0)),
            "last_pair_error_max_abs_m": float(getattr(resp, "last_pair_error_max_abs_m", 0.0)),
            "last_pair_error_mean_abs_m": float(getattr(resp, "last_pair_error_mean_abs_m", 0.0)),
        }

    def parse_rtos_resource(self, resp) -> dict:
        present_fields = {field.name for field, _ in resp.ListFields()}

        def value_or_none(name: str):
            if name not in present_fields:
                return None
            return getattr(resp, name)

        cpu_busy_permille = value_or_none("cpu_busy_permille")
        data = {
            "sample_window_ms": value_or_none("sample_window_ms"),
            "cpu_busy_permille": cpu_busy_permille,
            "heap_free_bytes": value_or_none("heap_free_bytes"),
            "heap_min_ever_free_bytes": value_or_none("heap_min_ever_free_bytes"),
            "min_stack_free_bytes": value_or_none("min_stack_free_bytes"),
            "min_stack_task_id": value_or_none("min_stack_task_id"),
            "task_count": value_or_none("task_count"),
            "health_flags": value_or_none("health_flags"),
        }
        if cpu_busy_permille is not None:
            data["cpu_busy_percent"] = int(cpu_busy_permille) / 10.0
        return {key: value for key, value in data.items() if value is not None}

    def parse_rtos_task_stats(self, resp) -> list[dict]:
        tasks = []
        for task in getattr(resp, "tasks", []):
            cpu_permille = int(getattr(task, "cpu_permille", 0))
            tasks.append({
                "task_id": int(getattr(task, "task_id", 0)),
                "cpu_permille": cpu_permille,
                "cpu_percent": cpu_permille / 10.0,
                "stack_min_free_bytes": int(getattr(task, "stack_min_free_bytes", 0)),
                "name": str(getattr(task, "name", "")),
            })
        return tasks

    def save_calib_status(self, data: dict) -> None:
        self._calib_status = data.copy()
        shared_app_state.calib_status = self._calib_status
        self.calib_status_updated.emit(self.calib_status)

    def save_rtos_resource(self, data: dict) -> None:
        self._rtos_resource = data.copy()
        shared_app_state.rtos_resource = self._rtos_resource
        self.rtos_resource_updated.emit(self.rtos_resource)

    def save_rtos_task_stats(self, data: list[dict]) -> None:
        self._rtos_task_stats = [item.copy() for item in data]
        shared_app_state.rtos_task_stats = self._rtos_task_stats
        self.rtos_task_stats_updated.emit(self.rtos_task_stats)
