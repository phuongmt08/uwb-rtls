"""Orchestrates app session, ranging runs, and log runs."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal

from common import protocol_pb2 as pb
from common.transport import VvAddress
from models.session_model import SessionModel, StreamRunState
from repository.session_repository import SessionRepository
from services.traffic_scheduler import shared_traffic_scheduler
from utils.app_state import shared_app_state

log = logging.getLogger(__name__)


class SessionRunManager(QObject):
    ranging_run_saved = pyqtSignal(str, int, list)
    log_run_saved = pyqtSignal(str, int, list)
    session_closed = pyqtSignal(str)

    def __init__(
        self,
        session_model: SessionModel,
        session_repository: SessionRepository,
        *,
        device_info_vm=None,
        ranging_model=None,
        log_model=None,
        parent=None,
    ):
        super().__init__(parent)
        self.session_model = session_model
        self.session_repository = session_repository
        self.device_info_vm = device_info_vm
        self.ranging_model = ranging_model
        self.log_model = log_model
        self._active_ranging_record: dict[str, Any] | None = None
        if self.ranging_model and hasattr(self.ranging_model, "session_sample_recorded"):
            self.ranging_model.session_sample_recorded.connect(self._on_ranging_sample_recorded)

    @property
    def session_id(self) -> str:
        return self.session_model.session_id

    def ensure_session(self) -> str:
        session_id = self.session_model.ensure_app_session(
            device_snapshot=self._device_snapshot(),
            dongle_snapshot={},
        )
        self._persist_session_meta()
        return session_id

    def open_ranging_run(self) -> StreamRunState:
        session_id = self.ensure_session()
        run = self.session_model.open_ranging_run()
        if not self._active_ranging_record or self._active_ranging_record.get("run_index") != run.index:
            meta = self._run_open_meta(run)
            record = self.session_repository.begin_ranging_run(session_id, run.index, meta=meta)
            self._active_ranging_record = {
                "session_id": session_id,
                "run_index": run.index,
                "path": record.get("path", ""),
                "export_path": self.session_repository.begin_sensor_fusion_result_export(meta),
                "files": [record.get("filename", "")] if record.get("filename") else [],
                "sample_count": 0,
            }

        self._persist_session_meta()
        return run

    def close_ranging_run(self, send_end: bool = True) -> tuple[str, list[str]]:
        session_id = self.ensure_session()
        shared_traffic_scheduler.begin_closing()
        run = self.session_model.active_run("ranging")
        if not run:
            return session_id, []

        meta = self._run_base_meta(run, "SESSION_END_REASON_RANGING_RESULTS")
        record = self._active_ranging_record
        if record and record.get("session_id") == session_id and int(record.get("run_index", 0) or 0) == run.index:
            files = self.session_repository.finalize_ranging_run(
                session_id,
                run.index,
                files=record.get("files", []),
                meta=meta,
                sample_count=int(record.get("sample_count", 0) or 0),
            )
            sample_count = int(record.get("sample_count", 0) or 0)
            self._active_ranging_record = None

        else:
            positions = self._collect_positions()
            fusion = self._collect_fusion_positions()
            files = self.session_repository.save_ranging_run(
                session_id,
                run.index,
                positions=positions,
                fusion_positions=fusion,
                meta=meta,
            )
            sample_count = max(len(positions or []), len(fusion or []))

        self.session_model.close_ranging_run(
            sample_count=sample_count,
            files=files,
            end_reason="SESSION_END_REASON_RANGING_RESULTS",
        )
        self._persist_session_meta()
        if send_end:
            self._send_end_session(pb.SESSION_END_REASON_RANGING_RESULTS)
        self.ranging_run_saved.emit(session_id, run.index, files)
        return session_id, files

    def open_log_run(self, device_key: str = "") -> StreamRunState:
        self.ensure_session()
        run = self.session_model.open_log_run(device_key=device_key)
        self._persist_session_meta()
        return run

    def close_log_run(self, send_end: bool = True, clear_buffers: bool = False) -> tuple[str, list[str]]:
        session_id = self.ensure_session()
        shared_traffic_scheduler.begin_closing()
        run = self.session_model.active_run("log")
        logs = self._collect_logs()
        if self.log_model:
            self.log_model.stop_log_stream()
        if not run and not logs:
            return session_id, []
        if not run:
            run = self.session_model.open_log_run(device_key=self._device_key())

        meta = self._run_base_meta(run, "SESSION_END_REASON_LOG_DATA")
        files = self.session_repository.save_log_run(
            session_id,
            run.index,
            logs=logs,
            meta=meta,
        )
        self.session_model.close_log_run(
            line_count=len(logs),
            files=files,
            end_reason="SESSION_END_REASON_LOG_DATA",
        )
        self._persist_session_meta()
        if send_end:
            self._send_end_session(pb.SESSION_END_REASON_LOG_DATA)
        if clear_buffers and self.log_model:
            self.log_model.clear_session_logs()
            self.log_model.clear_live_logs()
        self.log_run_saved.emit(session_id, run.index, files)
        return session_id, files

    def end_all_active(self, duration_sec: float = 0.0, send_device_end: bool = True) -> str:
        session_id = self.ensure_session()
        sent_ranging_end = False
        sent_log_end = False

        if shared_app_state.ranging_active or self.session_model.active_run("ranging"):
            try:
                if self.ranging_model and getattr(self.ranging_model, "is_ranging", False):
                    self.ranging_model.stop_ranging()
            finally:
                self.close_ranging_run(send_end=send_device_end)
                sent_ranging_end = bool(send_device_end)

        active_log_run = self.session_model.active_run("log")
        has_log_stream = bool(shared_app_state.log_streaming)
        has_log_payload = bool(self._collect_logs())
        if has_log_stream or has_log_payload:
            self.close_log_run(send_end=send_device_end)
            sent_log_end = bool(send_device_end)
        elif active_log_run:
            self.session_model.close_log_run(
                line_count=0,
                files=[],
                end_reason="SESSION_END_REASON_UNSPECIFIED",
            )

        meta = self.session_model.end_app_session(reason="USER_END_SESSION")
        if duration_sec:
            meta["duration_sec"] = float(duration_sec)
        if meta:
            self.session_repository.ensure_session(meta)
        self.session_closed.emit(session_id)

        if send_device_end and not sent_ranging_end and not sent_log_end:
            self._send_end_session(pb.SESSION_END_REASON_UNSPECIFIED)

        return session_id

    def start_new_session(self) -> str:
        if self.session_model.is_active:
            return self.session_model.session_id
        session_id = self.session_model.start_app_session(device_snapshot=self._device_snapshot())
        self._persist_session_meta()
        self.open_log_run(device_key=self._device_key())
        return session_id

    def _persist_session_meta(self) -> None:
        meta = self.session_model.build_session_meta()
        if not meta:
            return
        meta["statistics"] = self._collect_statistics()
        self.session_repository.ensure_session(meta)

    def _run_open_meta(self, run: StreamRunState) -> dict:
        return {
            "run_id": run.run_id,
            "stream_type": run.stream_type,
            "index": run.index,
            "start_time_iso": run.started_at.isoformat(),
            "end_time_iso": "",
            "duration_sec": 0.0,
            "end_reason": "",
            "device_key": run.device_key,
        }

    def _on_ranging_sample_recorded(self, sample: dict) -> None:
        record = self._active_ranging_record
        if not record or not self.session_model.active_run("ranging"):
            return
        if not isinstance(sample, dict):
            return
        if str(sample.get("source", "") or "") != "sensor_fusion":
            return

        self._append_ranging_record(sample.copy())

    def _append_ranging_record(self, sample: dict) -> None:
        record = self._active_ranging_record
        if not record:
            return
        next_index = int(record.get("sample_count", 0) or 0) + 1
        self.session_repository.append_ranging_run_sample(
            str(record.get("path", "")),
            sample,
            next_index,
        )
        self.session_repository.append_sensor_fusion_result_export_sample(
            str(record.get("export_path", "")),
            sample,
            next_index,
        )
        record["sample_count"] = next_index

    def _run_base_meta(self, run: StreamRunState, end_reason: str) -> dict:
        now = datetime.now()
        return {
            "run_id": run.run_id,
            "stream_type": run.stream_type,
            "index": run.index,
            "start_time_iso": run.started_at.isoformat(),
            "end_time_iso": now.isoformat(),
            "duration_sec": max(0.0, (now - run.started_at).total_seconds()),
            "end_reason": end_reason,
            "device_key": run.device_key,
        }

    def _send_end_session(self, reason: int, await_completion: bool = False) -> None:
        if not self.device_info_vm:
            return
        try:
            reason_name = pb.session_end_reason_t.Name(reason)
            stop_target = "log" if reason == pb.SESSION_END_REASON_LOG_DATA else "ranging" if reason == pb.SESSION_END_REASON_RANGING_RESULTS else "unknown"
            log.info(
                "Sending end_session: reason=%s src=%s dst=%s action=stop_%s",
                reason_name,
                f"HOST({int(VvAddress.HOST)})",
                f"MCU({int(VvAddress.MCU)})",
                stop_target,
            )
            self.device_info_vm.request_end_session(reason=reason, await_completion=await_completion)
        except Exception as exc:
            log.warning("Failed to send end_session(%s): %s", reason, exc)

    def _collect_positions(self) -> list[dict[str, Any]]:
        if not self.ranging_model:
            return []
        return [item.copy() for item in getattr(self.ranging_model, "position_history", [])]

    def _collect_fusion_positions(self) -> list[dict[str, Any]]:
        if not self.ranging_model:
            return []
        return [item.copy() for item in getattr(self.ranging_model, "fusion_history", [])]

    def _collect_logs(self) -> list[dict[str, Any]]:
        if not self.log_model:
            return []
        return [entry.copy() for entry in self.log_model.session_logs]

    def _collect_statistics(self) -> dict[str, Any]:
        stats = shared_app_state.ranging_stats
        return {
            "total_packets_rx": int(stats.get("total_count", 0) or 0),
            "success_packets_rx": int(stats.get("success_count", 0) or 0),
            "avg_rms_error_m": float(stats.get("last_rms_error_m", 0.0) or 0.0),
        }

    def _device_snapshot(self) -> dict:
        dev = shared_app_state.connected_device
        model = getattr(self.device_info_vm, "model", None)
        return {
            "mac_address": getattr(model, "connected_mac", "") or dev.get("mac", ""),
            "device_name": getattr(model, "connected_name", "") or dev.get("name", ""),
            "device_role": dev.get("Role", dev.get("role", "")),
            "fw_version": dev.get("Firmware", dev.get("fw_version", "")),
            "serial_number": dev.get("Serial Number", dev.get("serial_number", "")),
        }

    def _device_key(self) -> str:
        snap = self._device_snapshot()
        return snap.get("mac_address") or snap.get("device_name") or "default"
