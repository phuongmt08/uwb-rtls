"""
Repository for RTOS diagnostics packets.

Diagnostics are shared by several tabs, so this repository parses the protobuf
responses once and publishes a single state snapshot through SharedAppState.
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from utils.app_state import shared_app_state


class DiagnosticsRepository(QObject):
    rtos_resource_updated = pyqtSignal(dict)
    rtos_task_stats_updated = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rtos_resource: dict = {}
        self._rtos_task_stats: list[dict] = []
        shared_app_state.device_session_reset.connect(self.reset_session)

    def reset_session(self, _reason: str = "") -> None:
        self._rtos_resource = {}
        self._rtos_task_stats = []

    @property
    def rtos_resource(self) -> dict:
        return self._rtos_resource.copy()

    @property
    def rtos_task_stats(self) -> list[dict]:
        return [item.copy() for item in self._rtos_task_stats]

    def handle_packet(self, param_name: str, pkt) -> bool:
        if param_name == "rtos_resource_resp":
            self.save_rtos_resource(self.parse_rtos_resource(pkt.rtos_resource_resp))
            return True
        if param_name == "rtos_task_stats_resp":
            self.save_rtos_task_stats(self.parse_rtos_task_stats(pkt.rtos_task_stats_resp))
            return True
        return False

    def parse_rtos_resource(self, resp) -> dict:
        cpu_busy_permille = int(getattr(resp, "cpu_busy_permille", 0))
        return {
            "sample_window_ms": int(getattr(resp, "sample_window_ms", 0)),
            "cpu_busy_permille": cpu_busy_permille,
            "cpu_busy_percent": cpu_busy_permille / 10.0,
            "heap_free_bytes": int(getattr(resp, "heap_free_bytes", 0)),
            "heap_min_ever_free_bytes": int(getattr(resp, "heap_min_ever_free_bytes", 0)),
            "min_stack_free_bytes": int(getattr(resp, "min_stack_free_bytes", 0)),
            "min_stack_task_id": int(getattr(resp, "min_stack_task_id", 0)),
            "task_count": int(getattr(resp, "task_count", 0)),
            "health_flags": int(getattr(resp, "health_flags", 0)),
        }

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

    def save_rtos_resource(self, data: dict) -> None:
        self._rtos_resource = data.copy()
        shared_app_state.rtos_resource = self._rtos_resource
        self.rtos_resource_updated.emit(self.rtos_resource)

    def save_rtos_task_stats(self, data: list[dict]) -> None:
        self._rtos_task_stats = [item.copy() for item in data]
        shared_app_state.rtos_task_stats = self._rtos_task_stats
        self.rtos_task_stats_updated.emit(self.rtos_task_stats)
