"""Main window coordinator for UWB RTLS Studio.

The MainWindow view owns only widgets and user confirmation dialogs. This
ViewModel owns session lifecycle orchestration: collect domain data from the
sub-ViewModels/models, request firmware commands, and persist the session
through SessionRepository.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal

from common import protocol_pb2 as pb
from data.raw_packet_store import shared_raw_packet_store
from repository.session_repository import SessionRepository
from utils.app_state import shared_app_state

log = logging.getLogger(__name__)


class MainViewModel(QObject):
    session_saved = pyqtSignal(str)
    session_save_failed = pyqtSignal(str)
    session_ended = pyqtSignal(str)
    mode_changed = pyqtSignal(str)

    def __init__(
        self,
        *,
        live_tracking_vm=None,
        device_info_vm=None,
        log_vm=None,
        session_repository: SessionRepository | None = None,
        session_run_manager=None,
        parent=None,
    ):
        super().__init__(parent)
        self.live_tracking_vm = live_tracking_vm
        self.device_info_vm = device_info_vm
        self.log_vm = log_vm
        self._session_repository = session_repository or SessionRepository()
        self._session_run_manager = session_run_manager
        self._pending_end_session_id = ""

        if self.device_info_vm:
            self.device_info_vm.device_info_updated.connect(self._on_device_info_updated)
            if hasattr(self.device_info_vm, "end_session_result"):
                self.device_info_vm.end_session_result.connect(self._on_end_session_result)

    def set_mode(self, is_developer: bool) -> None:
        self.mode_changed.emit("developer" if is_developer else "user")

    def end_session(self, duration_sec: float = 0.0, reason: int = 0, await_device_completion: bool = True) -> str:
        """Stop recording locally, then optionally wait for device end-session confirmation."""
        end_reason = int(reason or pb.SESSION_END_REASON_UNSPECIFIED)
        if self._session_run_manager:
            session_id = self._session_run_manager.end_all_active(
                duration_sec=duration_sec,
                send_device_end=True,
            )
            self._clear_live_session_buffers()
            if self.log_vm:
                self.log_vm.refresh_sessions()
            self.session_saved.emit(session_id)
            self._pending_end_session_id = ""
            self.session_ended.emit(session_id)
            return session_id

        session_id = self.save_active_session(duration_sec=duration_sec)
        self._clear_live_session_buffers()
        self._pending_end_session_id = session_id
        self._request_device_end_session(reason=end_reason, await_completion=await_device_completion)
        return session_id

    def start_session(self) -> str:
        if self._session_run_manager:
            session_id = self._session_run_manager.start_new_session()
            return session_id
        from datetime import datetime
        now = datetime.now()
        shared_app_state.current_session_id = f"SES_{now.strftime('%Y%m%d_%H%M%S')}_session"
        if self.log_vm:
            self.log_vm.clear_session_logs()
        return shared_app_state.current_session_id

    def _on_device_info_updated(self, info: dict):
        return

    def save_active_session(self, duration_sec: float = 0.0) -> str:
        now = datetime.now()
        now_iso = now.isoformat()
        session_type = self._detect_session_type()
        session_id = shared_app_state.current_session_id or (
            f"SES_{now.strftime('%Y%m%d_%H%M%S')}_{session_type.lower()}"
        )

        positions = self._collect_positions()
        fusion_positions = self._collect_fusion_positions()
        logs = self._collect_logs()
        stats = self._collect_statistics(positions)
        device_info = self._collect_device_info()
        device_config = self._collect_device_config()

        session_meta = {
            "session_id": session_id,
            "session_type": session_type,
            "start_time_iso": now_iso,
            "end_time_iso": now_iso,
            "duration_sec": float(duration_sec or 0.0),
            "end_reason": "USER_END_SESSION",
            "device_info": device_info,
            "statistics": stats,
        }

        try:
            saved_session_id = self._session_repository.save_session(
                session_meta,
                device_config=device_config,
                anchors=shared_app_state.anchor_layout,
                positions=positions,
                fusion_positions=fusion_positions,
                logs=logs,
            )
            if self.log_vm:
                self.log_vm.refresh_sessions()
            self.session_saved.emit(saved_session_id)
            return saved_session_id
        except Exception as exc:
            message = f"Failed to save session {session_id}: {exc}"
            log.exception(message)
            self.session_save_failed.emit(message)
            raise

    def _request_device_end_session(self, reason: int, await_completion: bool = False) -> None:
        if not self.device_info_vm:
            if self._pending_end_session_id:
                session_id = self._pending_end_session_id
                self._pending_end_session_id = ""
                self.session_ended.emit(session_id)
            return
        try:
            self.device_info_vm.request_end_session(
                reason=reason,
                await_completion=await_completion,
            )
        except Exception as exc:
            log.warning("Failed to send end_session command: %s", exc)
            self._pending_end_session_id = ""
            self.session_save_failed.emit(f"Failed to send end_session command: {exc}")

    def _on_end_session_result(self, result: dict) -> None:
        session_id = self._pending_end_session_id
        if not session_id:
            return
        if result.get("success"):
            self._pending_end_session_id = ""
            self.session_ended.emit(session_id)
            return
        self._pending_end_session_id = ""
        message = result.get("message") or "End session failed."
        self.session_save_failed.emit(str(message))

    def _clear_live_session_buffers(self) -> None:
        model = getattr(self.live_tracking_vm, "model", None)
        if model and hasattr(model, "clear_history"):
            model.clear_history()
        shared_raw_packet_store.clear()
        if self.log_vm:
            self.log_vm.clear_session_logs()

    def _detect_session_type(self) -> str:
        if shared_app_state.ranging_active:
            return "RANGING"
        if self._collect_positions():
            return "RANGING"
        if self._collect_logs():
            return "LOG"
        return "SESSION"

    def _collect_positions(self) -> list[dict[str, Any]]:
        model = getattr(self.live_tracking_vm, "model", None)
        if not model:
            return []

        raw_positions = getattr(model, "position_history", [])
        positions = []
        for idx, item in enumerate(raw_positions, start=1):
            timestamp_ms = int(item.get("timestamp_ms", 0))
            positions.append(
                {
                    "time": self._format_received_time(item),
                    "timestamp_ms": timestamp_ms,
                    "packet_timestamp_ms": int(item.get("packet_timestamp_ms", 0) or 0),
                    "seq": int(item.get("seq", idx)),
                    "source": item.get("source", "ranging"),
                    "x_m": float(item.get("x_m", item.get("x", 0.0))),
                    "y_m": float(item.get("y_m", item.get("y", 0.0))),
                    "z_m": float(item.get("z_m", item.get("z", 0.0))),
                    "rms_error_m": float(item.get("rms_error_m", item.get("rms", 0.0))),
                    "anchor_mask": int(item.get("anchor_mask", 0) or 0),
                    "d1_mm": item.get("d1_mm", ""),
                    "d2_mm": item.get("d2_mm", ""),
                    "d3_mm": item.get("d3_mm", ""),
                    "d4_mm": item.get("d4_mm", ""),
                    "ukf_x_m": item.get("ukf_x_m", ""),
                    "ukf_y_m": item.get("ukf_y_m", ""),
                    "ukf_yaw_deg": item.get("ukf_yaw_deg", ""),
                    "tril_x_m": item.get("tril_x_m", ""),
                    "tril_y_m": item.get("tril_y_m", ""),
                    "yaw_deg": item.get("yaw_deg", ""),
                    "ranging_error_count": item.get("ranging_error_count", ""),
                    "prefilter_reject_count": item.get("prefilter_reject_count", ""),
                }
            )
        return positions

    def _collect_fusion_positions(self) -> list[dict[str, Any]]:
        model = getattr(self.live_tracking_vm, "model", None)
        if not model:
            return []

        raw_positions = getattr(model, "fusion_history", [])
        positions = []
        for idx, item in enumerate(raw_positions, start=1):
            timestamp_ms = int(item.get("timestamp_ms", 0))
            positions.append(
                {
                    "time": self._format_received_time(item),
                    "timestamp_ms": timestamp_ms,
                    "packet_timestamp_ms": int(item.get("packet_timestamp_ms", 0) or 0),
                    "seq": int(item.get("seq", idx)),
                    "source": item.get("source", "sensor_fusion"),
                    "status": "Update" if int(item.get("ukf_step", 0)) == 1 else "Predict",
                    "ukf_step": int(item.get("ukf_step", 0)),
                    "x_m": float(item.get("ukf_x_m", 0.0)),
                    "y_m": float(item.get("ukf_y_m", 0.0)),
                    "z_m": 0.0,
                    "rms_error_m": 0.0,
                    "anchor_mask": int(item.get("anchor_mask", 0) or 0),
                    "ukf_x_m": float(item.get("ukf_x_m", 0.0)),
                    "ukf_y_m": float(item.get("ukf_y_m", 0.0)),
                    "ukf_yaw_deg": float(item.get("ukf_yaw_deg", 0.0)),
                    "tril_x_m": float(item.get("tril_x_m", 0.0)),
                    "tril_y_m": float(item.get("tril_y_m", 0.0)),
                    "yaw_deg": float(item.get("yaw_deg", 0.0)),
                    "ranging_error_count": int(item.get("ranging_error_count", 0)),
                    "prefilter_reject_count": int(item.get("prefilter_reject_count", 0)),
                    "zone_id": item.get("zone_id", ""),
                    "room_id": item.get("room_id", ""),
                    "local_x_m": item.get("local_x_m", ""),
                    "local_y_m": item.get("local_y_m", ""),
                    "local_z_m": item.get("local_z_m", ""),
                    "anchors": item.get("anchors", []),
                }
            )
        return positions

    def _collect_logs(self) -> list[dict[str, Any]]:
        if not self.log_vm:
            return []
        return [entry.copy() for entry in self.log_vm.session_logs]

    def _collect_statistics(self, positions: list[dict[str, Any]]) -> dict[str, Any]:
        stats = shared_app_state.ranging_stats
        total = int(stats.get("total_count", len(positions)))
        success = int(stats.get("success_count", len(positions)))
        avg_rms = 0.0
        if positions:
            avg_rms = sum(p.get("rms_error_m", 0.0) for p in positions) / len(positions)
        else:
            avg_rms = float(stats.get("last_rms_error_m", 0.0))

        return {
            "total_packets_rx": total,
            "success_packets_rx": success,
            "avg_rms_error_m": avg_rms,
            "raw_capture": shared_raw_packet_store.stats(),
        }

    @staticmethod
    def _format_time_from_ms(timestamp_ms: int) -> str:
        if not timestamp_ms:
            return ""
        try:
            return datetime.fromtimestamp(timestamp_ms / 1000.0).strftime("%d/%m/%Y %H:%M:%S")
        except Exception:
            return ""

    @staticmethod
    def _format_received_time(item: dict[str, Any]) -> str:
        received_at = float(item.get("received_at", 0.0) or 0.0)
        if received_at <= 0:
            return ""
        try:
            return datetime.fromtimestamp(received_at).strftime("%d/%m/%Y %H:%M:%S")
        except Exception:
            return ""

    def _collect_device_info(self) -> dict[str, Any]:
        model = getattr(self.device_info_vm, "model", None)
        app_state_device = shared_app_state.connected_device
        return {
            "mac_address": getattr(model, "connected_mac", "") or app_state_device.get("mac", ""),
            "device_name": getattr(model, "connected_name", "") or app_state_device.get("name", ""),
            "device_role": app_state_device.get("Role", app_state_device.get("role", "")),
            "fw_version": app_state_device.get("Firmware", app_state_device.get("fw_version", "")),
            "serial_number": app_state_device.get(
                "Serial Number",
                app_state_device.get("serial_number", ""),
            ),
        }

    def _collect_device_config(self) -> dict[str, Any]:
        sys_config = shared_app_state.sys_config
        sys_ranging = shared_app_state.sys_ranging_cfg
        fusion = shared_app_state.sensor_fusion_cfg
        if not (sys_config or sys_ranging or fusion):
            return {}

        return {
            "uwb_role": sys_config.get("role"),
            "uwb_channel": sys_config.get("uwb_channel"),
            "uwb_prf": sys_config.get("uwb_prf"),
            "uwb_data_rate": sys_config.get("uwb_data_rate"),
            "tx_antenna_delay": sys_config.get("tx_antenna_delay"),
            "rx_antenna_delay": sys_config.get("rx_antenna_delay"),
            "tx_power": sys_config.get("tx_power"),
            "preamble_code": sys_config.get("uwb_preamble_code"),
            "fusion_alpha": fusion.get("alpha"),
            "fusion_beta": fusion.get("beta"),
            "fusion_kappa": fusion.get("kappa"),
            "q_a": fusion.get("q_a"),
            "q_g": fusion.get("q_g"),
            "r_uwb": fusion.get("r_uwb"),
            "ranging_period_ms": sys_ranging.get(
                "ranging_period_ms",
                sys_config.get("ranging_period_ms"),
            ),
            "rx_timeout_ms": sys_ranging.get(
                "rx_timeout_ms",
                sys_config.get("rx_timeout_ms"),
            ),
        }
