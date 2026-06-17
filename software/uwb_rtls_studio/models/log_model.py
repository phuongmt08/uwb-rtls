"""
Log domain model.

The repository parses incoming log payloads. This model owns the application
state and business decisions around logs: buffering, clearing, app-generated
entries, and acknowledging received firmware log segments.
"""
from __future__ import annotations

import logging
from datetime import datetime

from PyQt6.QtCore import QObject, pyqtSignal

from common.transport import VvAddress

log = logging.getLogger(__name__)


class LogModel(QObject):
    log_entry_added = pyqtSignal(dict)
    log_segment_received = pyqtSignal(dict)

    def __init__(self, log_repository=None, command_bus=None, parent=None):
        super().__init__(parent)
        self._log_repository = log_repository
        self._command_bus = command_bus
        self._live_logs: list[dict] = []
        self._session_logs: list[dict] = []
        self._log_stream_requested = False

        if self._log_repository:
            self._log_repository.log_entry_added.connect(self._on_repository_log_entry)
            self._log_repository.log_segment_received.connect(self._on_log_segment_received)

    @property
    def live_logs(self) -> list[dict]:
        return [entry.copy() for entry in self._live_logs]

    @property
    def session_logs(self) -> list[dict]:
        return [entry.copy() for entry in self._session_logs]

    def clear_session_logs(self) -> None:
        self._session_logs.clear()

    def clear_live_logs(self) -> None:
        self._live_logs.clear()

    def add_live_log(self, timestamp: str, level: str, source: str, message: str) -> dict:
        entry = {
            "timestamp": timestamp or datetime.now().strftime("%H:%M:%S"),
            "level": level or "INFO",
            "source": source or "APP",
            "message": message or "",
        }
        self._append_entry(entry)
        return entry

    def acknowledge_log_segment(self, segment_info: dict) -> bool:
        if not self._command_bus or segment_info.get("length", 0) <= 0:
            return False

        try:
            return bool(
                self._command_bus.send(
                    "log_clear",
                    dst_addr=VvAddress.MCU,
                    log_type=segment_info["log_type"],
                    offset=segment_info["offset"],
                    length=segment_info["length"],
                )
            )
        except Exception as exc:
            log.warning("Failed to send log_clear for segment %s: %s", segment_info, exc)
            return False

    def _on_repository_log_entry(self, entry: dict) -> None:
        self._append_entry(entry)

    def _on_log_segment_received(self, segment_info: dict) -> None:
        self.log_segment_received.emit(segment_info)
        self.acknowledge_log_segment(segment_info)

    def _append_entry(self, entry: dict) -> None:
        safe_entry = dict(entry or {})
        self._live_logs.append(safe_entry)
        self._session_logs.append(safe_entry.copy())
        self.log_entry_added.emit(safe_entry.copy())

    def request_log_stream(self, force: bool = False) -> bool:
        """Trigger firmware/device log streaming for the current connected device."""
        if self._log_stream_requested and not force:
            return False
        self._log_stream_requested = True
        if self._command_bus:
            try:
                log.info("LogModel: Requesting log stream via command_bus...")
                return bool(self._command_bus.send("log_data", dst_addr=VvAddress.MCU))
            except Exception as exc:
                log.warning("LogModel: Failed to send log_data request: %s", exc)
                return False
        return False
    
    
