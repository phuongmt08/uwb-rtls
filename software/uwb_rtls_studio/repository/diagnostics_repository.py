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
            resp = pkt.calib_status_resp
            # ByteSize()==0: firmware gửi gói nhưng calib_status sub-message rỗng
            # → save {} để UI reset về placeholder "-"
            if resp.ByteSize() == 0:
                self.save_calib_status({})
            else:
                self.save_calib_status(self.parse_calib_status(resp))
            return True
        if param_name == "rtos_resource_resp":
            self.save_rtos_resource(self.parse_rtos_resource(pkt.rtos_resource_resp))
            return True
        if param_name == "rtos_task_stats_resp":
            self.save_rtos_task_stats(self.parse_rtos_task_stats(pkt.rtos_task_stats_resp))
            return True
        return False

    def parse_calib_status(self, resp) -> dict:
        # sample_count / sample_target — field mới (13/14) thay thế deprecated
        # current_iteration(3) / total_iterations(4). Dùng cả hai để tương thích ngược.
        sample_count = int(getattr(resp, "sample_count", 0))
        sample_target = int(getattr(resp, "sample_target", 0))
        # Fallback về deprecated fields nếu firmware cũ chưa gửi sample_count
        current_iteration = int(getattr(resp, "current_iteration", 0))
        total_iterations = int(getattr(resp, "total_iterations", 0))

        # Parse candidates[]: repeated calib_anchor_candidate_t (field 16)
        candidates = []
        for c in getattr(resp, "candidates", []):
            candidates.append({
                "anchor_id": int(getattr(c, "anchor_id", 0)),
                "known_m": float(getattr(c, "known_m", 0.0)),
                "mean_m": float(getattr(c, "mean_m", 0.0)),
                "error_m": float(getattr(c, "error_m", 0.0)),
                "std_m": float(getattr(c, "std_m", 0.0)),
                "timeout_rate": float(getattr(c, "timeout_rate", 0.0)),
                "valid_count": int(getattr(c, "valid_count", 0)),
                "delta_dw": int(getattr(c, "delta_dw", 0)),
                "suggested_combined_delay": int(getattr(c, "suggested_combined_delay", 0)),
                "suggested_tx_delay": int(getattr(c, "suggested_tx_delay", 0)),
                "suggested_rx_delay": int(getattr(c, "suggested_rx_delay", 0)),
            })

        return {
            "state": int(getattr(resp, "state", 0)),
            "progress_percent": int(getattr(resp, "progress_percent", 0)),
            # Active fields (field 13/14)
            "sample_count": sample_count,
            "sample_target": sample_target,
            # Deprecated fields (field 3/4) — giữ lại để tương thích ngược
            "current_iteration": current_iteration or sample_count,
            "total_iterations": total_iterations or sample_target,
            # Error metrics — active fields
            "last_pair_error_rms_m": float(getattr(resp, "last_pair_error_rms_m", 0.0)),
            "last_pair_error_max_abs_m": float(getattr(resp, "last_pair_error_max_abs_m", 0.0)),
            "last_pair_error_mean_abs_m": float(getattr(resp, "last_pair_error_mean_abs_m", 0.0)),
            # Deprecated error metrics — giữ lại để tương thích ngược
            "last_pair_error_mean_m": float(getattr(resp, "last_pair_error_mean_m", 0.0)),
            "last_pair_error_spread_m": float(getattr(resp, "last_pair_error_spread_m", 0.0)),
            # Other active fields
            "current_antenna_delay": int(getattr(resp, "current_antenna_delay", 0)),
            "peer_ready_mask": int(getattr(resp, "peer_ready_mask", 0)),
            "rejected_batch_count": int(getattr(resp, "rejected_batch_count", 0)),
            "candidate_mask": int(getattr(resp, "candidate_mask", 0)),
            "candidates": candidates,
        }

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
