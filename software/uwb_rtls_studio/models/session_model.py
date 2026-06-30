"""Session lifecycle state model.

This model owns in-memory app-session and stream-run state. It does not write
files and does not send protocol commands; repositories and services do that.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime

from PyQt6.QtCore import QObject, pyqtSignal


@dataclass
class StreamRunState:
    run_id: str
    stream_type: str
    index: int
    started_at: datetime
    ended_at: datetime | None = None
    active: bool = True
    sample_count: int = 0
    end_reason: str = ""
    files: list[str] = field(default_factory=list)
    device_key: str = ""

    @property
    def duration_sec(self) -> float:
        end = self.ended_at or datetime.now()
        return max(0.0, (end - self.started_at).total_seconds())

    def to_meta(self) -> dict:
        data = asdict(self)
        data["started_at"] = self.started_at.isoformat()
        data["ended_at"] = self.ended_at.isoformat() if self.ended_at else ""
        data["duration_sec"] = self.duration_sec
        return data


@dataclass
class AppSessionState:
    session_id: str
    started_at: datetime
    ended_at: datetime | None = None
    active: bool = True
    device_snapshot: dict = field(default_factory=dict)
    dongle_snapshot: dict = field(default_factory=dict)
    runs: list[StreamRunState] = field(default_factory=list)

    @property
    def duration_sec(self) -> float:
        end = self.ended_at or datetime.now()
        return max(0.0, (end - self.started_at).total_seconds())

    def to_meta(self) -> dict:
        return {
            "session_id": self.session_id,
            "session_type": "SESSION",
            "start_time_iso": self.started_at.isoformat(),
            "end_time_iso": self.ended_at.isoformat() if self.ended_at else "",
            "duration_sec": self.duration_sec,
            "device_info": self.device_snapshot.copy(),
            "dongle_info": self.dongle_snapshot.copy(),
            "runs": [run.to_meta() for run in self.runs],
        }


class SessionModel(QObject):
    session_started = pyqtSignal(str)
    session_ending = pyqtSignal(str)
    session_ended = pyqtSignal(str)
    run_started = pyqtSignal(str, int)
    run_ended = pyqtSignal(str, int, list)
    session_state_changed = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state: AppSessionState | None = None
        self._run_counters = {"ranging": 0, "log": 0}

    @property
    def state(self) -> AppSessionState | None:
        return self._state

    @property
    def session_id(self) -> str:
        return self._state.session_id if self._state else ""

    @property
    def is_active(self) -> bool:
        return bool(self._state and self._state.active)

    def start_app_session(
        self,
        device_snapshot: dict | None = None,
        dongle_snapshot: dict | None = None,
        session_id: str = "",
    ) -> str:
        if self._state and self._state.active:
            return self._state.session_id

        now = datetime.now()
        resolved_id = session_id or f"SES_{now.strftime('%Y%m%d_%H%M%S')}_session"
        self._state = AppSessionState(
            session_id=resolved_id,
            started_at=now,
            device_snapshot=dict(device_snapshot or {}),
            dongle_snapshot=dict(dongle_snapshot or {}),
        )
        self._run_counters = {"ranging": 0, "log": 0}
        self.session_started.emit(resolved_id)
        self._emit_state()
        return resolved_id

    def ensure_app_session(
        self,
        device_snapshot: dict | None = None,
        dongle_snapshot: dict | None = None,
    ) -> str:
        if self._state and self._state.active:
            if device_snapshot:
                self._state.device_snapshot.update(device_snapshot)
            if dongle_snapshot:
                self._state.dongle_snapshot.update(dongle_snapshot)
            return self._state.session_id
        return self.start_app_session(device_snapshot, dongle_snapshot)

    def end_app_session(self, reason: str = "USER_END_SESSION") -> dict:
        if not self._state:
            return {}
        if self._state.active:
            self.session_ending.emit(self._state.session_id)
            self._state.active = False
            self._state.ended_at = datetime.now()
        meta = self.build_session_meta()
        meta["end_reason"] = reason
        self.session_ended.emit(self._state.session_id)
        self._emit_state()
        return meta

    def open_ranging_run(self) -> StreamRunState:
        return self._open_run("ranging")

    def close_ranging_run(
        self,
        sample_count: int = 0,
        files: list[str] | None = None,
        end_reason: str = "SESSION_END_REASON_RANGING_RESULTS",
    ) -> StreamRunState | None:
        return self._close_run("ranging", sample_count, files, end_reason)

    def open_log_run(self, device_key: str = "") -> StreamRunState:
        return self._open_run("log", device_key=device_key)

    def close_log_run(
        self,
        line_count: int = 0,
        files: list[str] | None = None,
        end_reason: str = "SESSION_END_REASON_LOG_DATA",
    ) -> StreamRunState | None:
        return self._close_run("log", line_count, files, end_reason)

    def active_runs(self) -> list[StreamRunState]:
        if not self._state:
            return []
        return [run for run in self._state.runs if run.active]

    def active_run(self, stream_type: str) -> StreamRunState | None:
        if not self._state:
            return None
        for run in reversed(self._state.runs):
            if run.stream_type == stream_type and run.active:
                return run
        return None

    def build_session_meta(self) -> dict:
        if not self._state:
            return {}
        return self._state.to_meta()

    def build_runs_meta(self) -> list[dict]:
        if not self._state:
            return []
        return [run.to_meta() for run in self._state.runs]

    def _open_run(self, stream_type: str, device_key: str = "") -> StreamRunState:
        self.ensure_app_session()
        existing = self.active_run(stream_type)
        if existing:
            return existing

        assert self._state is not None
        self._run_counters[stream_type] = self._run_counters.get(stream_type, 0) + 1
        index = self._run_counters[stream_type]
        run = StreamRunState(
            run_id=f"{stream_type}_run_{index:03d}",
            stream_type=stream_type,
            index=index,
            started_at=datetime.now(),
            device_key=device_key,
        )
        self._state.runs.append(run)
        self.run_started.emit(stream_type, index)
        self._emit_state()
        return run

    def _close_run(
        self,
        stream_type: str,
        sample_count: int,
        files: list[str] | None,
        end_reason: str,
    ) -> StreamRunState | None:
        run = self.active_run(stream_type)
        if not run:
            return None

        run.active = False
        run.ended_at = datetime.now()
        run.sample_count = int(sample_count or 0)
        run.files = list(files or [])
        run.end_reason = end_reason
        self.run_ended.emit(stream_type, run.index, run.files)
        self._emit_state()
        return run

    def _emit_state(self) -> None:
        self.session_state_changed.emit(self.build_session_meta())

