"""
Log ViewModel for the Log & Session History tab.
"""
import logging

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from repository.session_browser import SessionBrowser

log = logging.getLogger(__name__)


class LogViewModel(QObject):
    log_entry_added = pyqtSignal(dict)
    log_filtered = pyqtSignal(int)
    session_list_updated = pyqtSignal(list)
    session_details_loaded = pyqtSignal(str, str, list)
    session_deleted = pyqtSignal(str)
    live_logs_cleared = pyqtSignal()
    log_stream_state_changed = pyqtSignal(bool)

    def __init__(self, session_browser: SessionBrowser, log_model=None, session_run_manager=None, parent=None):
        super().__init__(parent)
        self.browser = session_browser
        self._log_model = log_model
        self._session_run_manager = session_run_manager
        self._live_logs = []
        self._log_poll_timer = QTimer(self)
        self._log_poll_timer.setInterval(500)
        self._log_poll_timer.timeout.connect(self._poll_log_timeout)
        if self._log_model:
            self._log_model.log_entry_added.connect(self._on_model_log_entry)
            self._log_model.log_stream_state_changed.connect(self._on_model_log_stream_state_changed)

    @property
    def session_logs(self) -> list[dict]:
        if self._log_model:
            return self._log_model.session_logs
        return [entry.copy() for entry in self._live_logs]

    @property
    def is_log_streaming(self) -> bool:
        if self._log_model and hasattr(self._log_model, "is_log_streaming"):
            return bool(self._log_model.is_log_streaming)
        return False

    def clear_session_logs(self):
        if self._log_model:
            self._log_model.clear_session_logs()

    def set_developer_mode(self, enabled: bool):
        if self._log_model and hasattr(self._log_model, "set_developer_mode"):
            self._log_model.set_developer_mode(enabled)

    def _on_model_log_entry(self, entry: dict):
        if self._session_run_manager:
            self._session_run_manager.open_log_run()
        self._live_logs.append(entry.copy())
        self.log_entry_added.emit(entry)

    def add_live_log(self, timestamp: str, level: str, source: str, message: str):
        entry = {
            "timestamp": timestamp,
            "level": level,
            "source": source,
            "message": message,
        }
        if self._log_model:
            self._log_model.add_live_log(timestamp, level, source, message)
        else:
            self._live_logs.append(entry)
            self.log_entry_added.emit(entry)

    def clear_live_logs(self):
        self._live_logs.clear()
        if self._log_model:
            self._log_model.clear_live_logs()
        self.live_logs_cleared.emit()
        self.log_filtered.emit(0)

    def clear_log_session(self):
        """Send log_clear to firmware and clear only the current live-log table."""
        if self._log_model and hasattr(self._log_model, "request_log_stop"):
            self._log_model.request_log_stop(log_type=1, offset=0, length=0)
        self.live_logs_cleared.emit()
        self.log_filtered.emit(0)

    def refresh_sessions(self, filters: dict = None):
        try:
            sessions = self.browser.list_all_sessions(filters)
            formatted_sessions = []
            for s in sessions:
                session_id = s.get("session_id")
                dur = s.get("duration_sec", 0.0)
                h = int(dur // 3600)
                m = int((dur % 3600) // 60)
                sec = int(dur % 60)
                dur_str = f"{sec}s"
                if m > 0 or h > 0:
                    dur_str = f"{m}m {dur_str}"
                if h > 0:
                    dur_str = f"{h}h {dur_str}"

                formatted_sessions.append({
                    "session_id": session_id,
                    "type": s.get("session_type", "RANGING"),
                    "start_time": s.get("start_time_iso", ""),
                    "duration": dur_str,
                    "device": s.get("connected_device_name", "-"),
                    "total_packets": s.get("total_packets_rx", 0),
                    "success_packets": s.get("success_packets_rx", 0),
                    "avg_rms": s.get("avg_rms_error_m", 0.0),
                    "ranging_count": self._count_ranging_runs(session_id),
                    "session_file_count": self._count_ranging_runs(session_id) + self._count_log_runs(session_id),
                    "browser_path": self.browser.get_session_storage_folder(session_id) if hasattr(self.browser, "get_session_storage_folder") else self.browser.get_browser_root(),
                })
            self.session_list_updated.emit(formatted_sessions)
        except Exception as exc:
            log.error("Error refreshing sessions: %s", exc)

    def _session_file_exists(self, session_id: str, filename: str) -> bool:
        return self.browser.session_file_exists(session_id, filename)

    def _count_session_files(self, session_id: str) -> int:
        return self.browser.count_session_files(session_id)

    def _count_ranging_runs(self, session_id: str) -> int:
        return self.browser.count_ranging_runs(session_id)

    def _count_log_runs(self, session_id: str) -> int:
        if hasattr(self.browser, "count_log_runs"):
            return self.browser.count_log_runs(session_id)
        return 0

    def get_session_folder(self, session_id: str) -> str:
        return self.browser.get_session_folder(session_id)

    def export_session_to(self, session_id: str, destination_dir: str) -> str:
        return self.browser.export_session_to(session_id, destination_dir)

    def load_session_detail(self, session_id: str, detail_type: str):
        log.info("Loading details for session %s (type: %s)", session_id, detail_type)
        try:
            if detail_type in ("ranging", "fusion"):
                data = self.browser.get_session_record_files(session_id, "ranging")
            else:
                data = self.browser.get_session_record_files(session_id, "logs")
            self.session_details_loaded.emit(session_id, detail_type, data)
        except Exception as exc:
            log.error("Error loading session detail: %s", exc)

    def delete_session(self, session_id: str):
        log.warning("Requesting deletion of session: %s", session_id)
        try:
            success = self.browser.delete_session(session_id)
            if success:
                self.session_deleted.emit(session_id)
                self.refresh_sessions()
        except Exception as exc:
            log.error("Error deleting session %s: %s", session_id, exc)

    def start_log_stream(self) -> bool:
        log.info("Requesting start of log stream from device...")
        if self._session_run_manager:
            self._session_run_manager.open_log_run()
        if self._log_model and self._log_model.request_log_stream(force=True):
            self._log_poll_timer.start()
            return True
        return False

    def stop_log_stream(self) -> bool:
        if not self.is_log_streaming:
            return False

        self._log_poll_timer.stop()
        if self._session_run_manager:
            self._session_run_manager.close_log_run(send_end=True, clear_buffers=False)
            self.refresh_sessions()
        elif self._log_model and hasattr(self._log_model, "stop_log_stream"):
            self._log_model.stop_log_stream()
        return True

    def send_host_log_packet(self, packet_name: str, **params) -> dict:
        if not self._log_model:
            return {"ok": False, "error": "Log model is not available"}
        return self._log_model.send_host_log_packet(packet_name, **params)

    def _poll_log_timeout(self):
        if self._log_model and self._log_model.poll_log_timeout():
            return

    def _on_model_log_stream_state_changed(self, is_streaming: bool):
        if not is_streaming:
            self._log_poll_timer.stop()
        self.log_stream_state_changed.emit(bool(is_streaming))