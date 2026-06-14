"""
===============================================================================
  UWB RTLS Studio — Unified Application State & Thread Registry
===============================================================================
  File        : utils/app_state.py
  Description : Centralized state management ("Shared Memory"), Job State Machine,
                Thread Registry, and central retry/timeout configuration.
                This allows all tabs to synchronize states seamlessly.
===============================================================================
"""
from __future__ import annotations

import logging
import threading
from typing import Dict, Any, List, Optional, Callable
from PyQt6.QtCore import QObject, pyqtSignal

from common.query_state_machine import QueryQueueManager, QueryState

log = logging.getLogger(__name__)

# ── Centralized Retry & Timeout Configurations ────────────────────────────────
# Modifying these values updates retry/timeout behavior across the entire app.
QUERY_TIMEOUT_S = 0.25         # Time to wait for expected response (seconds)
QUERY_MAX_RETRIES = 3          # Maximum attempts per command on timeout

# Polling intervals in milliseconds
POLL_BATTERY_MS = 30000        # Battery polling interval (30s)
POLL_BLE_STATUS_MS = 5000      # BLE status polling interval (5s)
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
    # ── Reactive State Signals ───────────────────────────────────────
    connection_status_changed = pyqtSignal(str)   # "Disconnected", "Connecting", "Connected"
    connected_device_changed = pyqtSignal(dict)    # Device info (mac, name, role, fw_version, hw_version, serial)
    battery_info_changed = pyqtSignal(dict)       # Voltage, SOC, remains, charging, temps...
    ble_status_changed = pyqtSignal(dict)          # BLE state, rssi, disconnect reason
    ranging_active_changed = pyqtSignal(bool)      # Ranging active/stopped
    ranging_stats_changed = pyqtSignal(dict)       # total_count, success_count, rms_error_m...
    calib_status_changed = pyqtSignal(dict)         # state, progress, iteration, peer ready mask...
    anchor_layout_changed = pyqtSignal(list)       # List of fixed anchors positions
    sys_config_changed = pyqtSignal(dict)          # UWB role, channel, tx/rx antenna delays...
    sys_ranging_cfg_changed = pyqtSignal(dict)     # Rx timeout, ranging period
    sensor_fusion_cfg_changed = pyqtSignal(dict)   # alpha, kappa, noise covariances...
    pos_calib_cfg_changed = pyqtSignal(dict)       # Auto calibration parameters
    rtos_resource_changed = pyqtSignal(dict)       # CPU, heap, stack, task count, health flags
    rtos_task_stats_changed = pyqtSignal(list)     # Per-task CPU and stack snapshots

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

        # ── State Store (Private Variables) ───────────────────────────
        self._connection_status = "Disconnected"
        self._connected_device: Dict[str, Any] = {}
        self._battery_info: Dict[str, Any] = {}
        self._ble_status: Dict[str, Any] = {}
        self._ranging_active = False
        self.current_session_id = ""
        self._ranging_stats: Dict[str, Any] = {}
        self._calib_status: Dict[str, Any] = {}
        self._anchor_layout: List[Dict[str, Any]] = []
        self._sys_config: Dict[str, Any] = {}
        self._sys_ranging_cfg: Dict[str, Any] = {}
        self._sensor_fusion_cfg: Dict[str, Any] = {}
        self._pos_calib_cfg: Dict[str, Any] = {}
        self._rtos_resource: Dict[str, Any] = {}
        self._rtos_task_stats: List[Dict[str, Any]] = []

        # Job State Machine storage
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._query_manager: QueryQueueManager | None = None

    # ── Getters / Setters with Reactive Signaling ──────────────────────

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
            if val:
                import datetime
                timestamp_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                self.current_session_id = f"SES_{timestamp_str}_ranging"
            else:
                self.current_session_id = ""
            self.ranging_active_changed.emit(val)

    @property
    def ranging_stats(self) -> Dict[str, Any]:
        return self._ranging_stats.copy()

    @ranging_stats.setter
    def ranging_stats(self, val: Dict[str, Any]) -> None:
        self._ranging_stats = val.copy()
        self.ranging_stats_changed.emit(self._ranging_stats)

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
    def rtos_task_stats(self) -> List[Dict[str, Any]]:
        return [item.copy() for item in self._rtos_task_stats]

    @rtos_task_stats.setter
    def rtos_task_stats(self, val: List[Dict[str, Any]]) -> None:
        self._rtos_task_stats = [item.copy() for item in val]
        self.rtos_task_stats_changed.emit(self.rtos_task_stats)

    # ── Global Query Queue Management (Retry/Timeout logic) ─────────

    def init_query_manager(self, send_packet_fn: Callable[[str, int, Dict[str, Any]], Any]) -> None:
        """Initialize the global query queue manager with the packet sending function."""
        self._query_manager = QueryQueueManager(
            send_packet_fn=send_packet_fn,
            timeout_s=QUERY_TIMEOUT_S,
            max_retries=QUERY_MAX_RETRIES,
            on_complete_fn=self._on_query_complete
        )
        log.info("[SharedAppState] Query manager initialized.")

    def enqueue_query(self, command_name: str, dst_addr: int, **kwargs) -> None:
        """Add a query to the sequential execution queue."""
        if not hasattr(self, '_query_manager') or not self._query_manager:
            log.warning("[SharedAppState] Query manager not initialized. Can't enqueue.")
            return
        
        self._query_manager.add_query(command_name, dst_addr, **kwargs)
        if not self._query_manager.is_running:
            self.update_job("query_queue", JobState.RUNNING)
            self._query_manager.start()

    def handle_incoming_packet(self, param_name: str, pkt: Any) -> None:
        """Route incoming packets to the query queue manager to check for response matches."""
        if hasattr(self, '_query_manager') and self._query_manager:
            self._query_manager.handle_response(param_name, pkt)

    def _on_query_complete(self, results: List[Dict[str, Any]]) -> None:
        """Called when the sequential query queue finishes execution."""
        success_count = sum(1 for r in results if r["status"] == "SUCCESS")
        total_count = len(results)
        
        status = JobState.SUCCESS if success_count == total_count else JobState.FAILED
        self.update_job("query_queue", status, progress=100)
        
        log.info("--- Global Query Queue Execution Report ---")
        for r in results:
            log.info(f"  Query: {r['command_name']} -> {r['status']} (retries: {r['retries']})")

    # ── Job State Machine Implementation ─────────────────────────────

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
