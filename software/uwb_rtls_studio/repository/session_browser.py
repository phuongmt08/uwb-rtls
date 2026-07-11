"""
===============================================================================
  UWB RTLS Studio — Session Browser Implementation
===============================================================================
  File        : repository/session_browser.py
  Description : Logic cho việc browse, filter, và review past sessions.
===============================================================================
"""
from repository.session_repository import SessionRepository

class SessionBrowser:
    def __init__(self, repository: SessionRepository):
        self._repo = repository

    def list_all_sessions(self, filters: dict = None) -> list:
        """Trả về danh sách các session metadata dựa theo bộ lọc."""
        return self._repo.list_sessions(filters)

    def load_session(self, session_id: str) -> dict:
        """Tải toàn bộ thông tin chi tiết của session bao gồm metadata và config."""
        meta = self._repo.get_session_meta(session_id)
        # Có thể tải thêm các configsnapshot nếu cần
        return meta

    def get_ranging_data(self, session_id: str) -> list:
        """Tải dữ liệu tọa độ của session."""
        return self._repo.load_session_details(session_id, "ranging")

    def get_fusion_data(self, session_id: str) -> list:
        """Tải dữ liệu sensor fusion của session."""
        return self._repo.load_session_details(session_id, "fusion")

    def get_log_data(self, session_id: str) -> list:
        """Tải dữ liệu log thiết bị của session."""
        return self._repo.load_session_details(session_id, "logs")

    def get_session_folder(self, session_id: str) -> str:
        return self._repo.get_session_folder(session_id)

    def get_browser_root(self) -> str:
        return self._repo.get_browser_root()

    def get_session_storage_folder(self, session_id: str) -> str:
        return self._repo.get_session_storage_folder(session_id)

    def session_file_exists(self, session_id: str, filename: str) -> bool:
        return self._repo.session_file_exists(session_id, filename)

    def count_session_files(self, session_id: str) -> int:
        return self._repo.count_session_files(session_id)

    def count_ranging_runs(self, session_id: str) -> int:
        return self._repo.count_ranging_runs(session_id)

    def count_log_runs(self, session_id: str) -> int:
        return self._repo.count_log_runs(session_id)

    def export_session_to(self, session_id: str, destination_dir: str) -> str:
        return self._repo.export_session_to(session_id, destination_dir)

    def delete_session(self, session_id: str) -> bool:
        """Xóa session khỏi hệ thống."""
        return self._repo.delete_session(session_id)

    def get_session_record_files(self, session_id: str, detail_type: str) -> list:
        return self._repo.get_session_record_files(session_id, detail_type)
