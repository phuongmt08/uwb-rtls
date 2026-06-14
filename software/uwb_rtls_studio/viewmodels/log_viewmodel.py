"""
===============================================================================
  UWB RTLS Studio — Log ViewModel
===============================================================================
  File        : viewmodels/log_viewmodel.py
  Description : ViewModel cho tab "Log & Session History" (Tab 5).
                Quản lý device logs, app logs, và session history browser.

  MVVM Role   : VIEWMODEL

  ═══════════════════════════════════════════════════════════════════════
  QUAN TRỌNG — Log Tab hiện cho CẢ USER và DEVELOPER
  ═══════════════════════════════════════════════════════════════════════

  Lý do: Khi user đang ranging, STM32 / UWB chip / IMU vẫn phát sinh
  các log messages (warnings, errors, status). User cần thấy các log
  này để debug vấn đề (VD: "Ranging timeout", "Low battery", 
  "IMU calibration failed", ...). Không chỉ developer mới cần log.

  Tuy nhiên, level chi tiết khác nhau giữa 2 mode:

  ┌──────────────────────────────┬──────────────┬───────────────┐
  │ Log Feature                  │ User Mode    │ Developer Mode│
  ├──────────────────────────────┼──────────────┼───────────────┤
  │ Device Logs (INFO/WARN/ERR)  │ ✅           │ ✅            │
  │ Device Logs (DEBUG)          │ ❌ Filtered  │ ✅            │
  │ App Internal Logs            │ ❌ Hidden    │ ✅            │
  │ Raw Protocol Logs (TX/RX)    │ ❌ Hidden    │ ✅            │
  │ Session History Browser      │ ✅           │ ✅            │
  │ Export CSV/TXT               │ ✅           │ ✅            │
  │ Clear Device Logs            │ ❌ Hidden    │ ✅            │
  │ Advanced Filters             │ ❌ Hidden    │ ✅            │
  └──────────────────────────────┴──────────────┴───────────────┘

  ═══════════════════════════════════════════════════════════════════════

  Tab Layout:
    ┌─────────────────────────────────────────────────────────────┐
    │  LOG & SESSION HISTORY TAB                                  │
    ├─────────────────────────────────────────────────────────────┤
    │  ┌─ 📋 Live Log (hiện trong cả 2 mode) ──────────────────┐ │
    │  │  ┌─ Filter Bar ──────────────────────────────────────┐ │ │
    │  │  │  Level: [▼ ALL] │ Source: [▼ ALL]*(dev)           │ │ │
    │  │  │  🔍 [Search...]                                    │ │ │
    │  │  │  [Export CSV] [Export TXT] [Clear]*(dev)           │ │ │
    │  │  └───────────────────────────────────────────────────┘ │ │
    │  │  ┌─ Log Area (scrollable) ───────────────────────────┐ │ │
    │  │  │  [12:30:01] INFO  DEVICE  Ranging started          │ │ │
    │  │  │  [12:30:05] WARN  DEVICE  Low battery: 15%         │ │ │
    │  │  │  [12:30:10] ERROR DEVICE  Ranging timeout #3       │ │ │
    │  │  │  [12:30:15] INFO  DEVICE  Position quality: GOOD   │ │ │
    │  │  │  ── Developer only ──────────────────────────────  │ │ │
    │  │  │  [12:30:02] DEBUG PROTOCOL TX: ranging_start       │ │ │
    │  │  │  [12:30:02] DEBUG PROTOCOL RX: ack (OK)            │ │ │
    │  │  │  [12:30:03] DEBUG APP     RangingVM: session init  │ │ │
    │  │  └───────────────────────────────────────────────────┘ │ │
    │  │  Status: 1,234 entries | 2 errors | 1 warning         │ │
    │  └──────────────────────────────────────────────────────┘ │
    │                                                            │
    │  ┌─ 📂 Session History (hiện trong cả 2 mode) ──────────┐ │
    │  │  ┌─ Filter ──────────────────────────────────────────┐│ │
    │  │  │  Date: [From ___] → [To ___]                      ││ │
    │  │  │  Type: [▼ ALL]  Device: [▼ ALL]                   ││ │
    │  │  └───────────────────────────────────────────────────┘│ │
    │  │  ┌────────┬──────────┬─────────┬────────┬───────────┐│ │
    │  │  │ Date   │ Type     │ Device  │ Dura.  │ Actions   ││ │
    │  │  ├────────┼──────────┼─────────┼────────┼───────────┤│ │
    │  │  │ 05/30  │ RANGING  │ TAG-123 │ 5:23   │ [📂][🗑] ││ │
    │  │  │ 05/30  │ LOG      │ TAG-123 │ 1:05   │ [📂][🗑] ││ │
    │  │  │ 05/29  │ RANGING  │ TAG-456 │ 12:30  │ [📂][🗑] ││ │
    │  │  │ 05/28  │ STREAM   │ ANC-789 │ 3:45   │ [📂][🗑] ││ │
    │  │  └────────┴──────────┴─────────┴────────┴───────────┘│ │
    │  │  📂 = Open session (load data for review)             │ │
    │  │  🗑 = Delete session (remove from disk)               │ │
    │  │  Total: 15 sessions | Disk: 2.3 MB                    │ │
    │  └──────────────────────────────────────────────────────┘ │
    └─────────────────────────────────────────────────────────────┘

  Signals:
    - log_entry_added(entry: dict)
    - log_filtered(count: int)
    - session_list_updated(sessions: list)
    - session_opened(session_id: str, data: dict)
    - session_deleted(session_id: str)

  Slots:
    - on_filter_changed(level, source)
    - on_search(query: str)
    - on_export(format: str)
    - on_clear_logs()
    - on_open_session(session_id: str)
    - on_delete_session(session_id: str)
    - on_refresh_sessions()

  Protocol Messages: tags 37 (log_data_t), 38 (log_clear_t)

  Sử dụng:
    - Models: LogModel
    - Repository: SessionRepository, SessionBrowser
===============================================================================
"""
import logging
from PyQt6.QtCore import QObject, pyqtSignal
from repository.session_browser import SessionBrowser

log = logging.getLogger(__name__)

class LogViewModel(QObject):
    # Signals for View updates
    log_entry_added = pyqtSignal(dict)
    log_filtered = pyqtSignal(int)
    session_list_updated = pyqtSignal(list)
    session_details_loaded = pyqtSignal(str, str, list)  # session_id, detail_type, data_list
    session_deleted = pyqtSignal(str)

    def __init__(self, session_browser: SessionBrowser, log_model=None, parent=None):
        super().__init__(parent)
        self.browser = session_browser
        self._log_model = log_model
        self._live_logs = []
        if self._log_model:
            self._log_model.log_entry_added.connect(self._on_model_log_entry)

    @property
    def session_logs(self) -> list[dict]:
        if self._log_model:
            return self._log_model.session_logs
        return [entry.copy() for entry in self._live_logs]

    def clear_session_logs(self):
        if self._log_model:
            self._log_model.clear_session_logs()

    def _on_model_log_entry(self, entry: dict):
        self._live_logs.append(entry.copy())
        self.log_entry_added.emit(entry)

    # ── Live Log Methods ─────────────────────────────────────────────

    def add_live_log(self, timestamp: str, level: str, source: str, message: str):
        """Thêm một dòng live log mới và phát tín hiệu báo cho UI."""
        entry = {
            "timestamp": timestamp,
            "level": level,
            "source": source,
            "message": message
        }
        if self._log_model:
            self._log_model.add_live_log(timestamp, level, source, message)
        else:
            self._live_logs.append(entry)
            self.log_entry_added.emit(entry)

    def clear_live_logs(self):
        """Xóa toàn bộ live logs hiện tại."""
        self._live_logs.clear()
        if self._log_model:
            self._log_model.clear_live_logs()
        self.log_filtered.emit(0)

    # ── Session History Methods ──────────────────────────────────────

    def refresh_sessions(self, filters: dict = None):
        """Tải lại danh sách session từ Repository và cập nhật cho UI."""
        try:
            sessions = self.browser.list_all_sessions(filters)
            # Chuẩn hóa dữ liệu cho UI hiển thị
            formatted_sessions = []
            for s in sessions:
                session_id = s.get("session_id")
                # Tính toán chuỗi hiển thị thời lượng
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
                    "session_file_count": self._count_session_files(session_id),
                    "browser_path": self.browser.get_browser_root(),
                })
            self.session_list_updated.emit(formatted_sessions)
        except Exception as e:
            log.error(f"Error refreshing sessions: {e}")

    def _session_file_exists(self, session_id: str, filename: str) -> bool:
        return self.browser.session_file_exists(session_id, filename)

    def _count_session_files(self, session_id: str) -> int:
        return self.browser.count_session_files(session_id)

    def _count_ranging_runs(self, session_id: str) -> int:
        return self.browser.count_ranging_runs(session_id)

    def get_session_folder(self, session_id: str) -> str:
        return self.browser.get_session_folder(session_id)

    def export_session_to(self, session_id: str, destination_dir: str) -> str:
        return self.browser.export_session_to(session_id, destination_dir)

    def load_session_detail(self, session_id: str, detail_type: str):
        """Tải chi tiết tọa độ hoặc logs của session cũ."""
        log.info(f"Loading details for session {session_id} (type: {detail_type})")
        try:
            if detail_type == "ranging":
                data = self.browser.get_ranging_data(session_id)
            elif detail_type == "fusion":
                data = self.browser.get_fusion_data(session_id)
            else:
                data = self.browser.get_log_data(session_id)
            self.session_details_loaded.emit(session_id, detail_type, data)
        except Exception as e:
            log.error(f"Error loading session detail: {e}")

    def delete_session(self, session_id: str):
        """Xóa session khỏi kho file và làm mới UI."""
        log.warning(f"Requesting deletion of session: {session_id}")
        try:
            success = self.browser.delete_session(session_id)
            if success:
                self.session_deleted.emit(session_id)
                self.refresh_sessions()
        except Exception as e:
            log.error(f"Error deleting session {session_id}: {e}")
