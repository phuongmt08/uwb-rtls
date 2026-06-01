"""
===============================================================================
  UWB RTLS Studio — Log Model
===============================================================================
  File        : models/log_model.py
  Description : Data model cho log entries.
                Gồm 2 loại: Device Log (từ firmware gửi lên) và
                Application Log (app tự tạo cho debug).

  MVVM Role   : MODEL — chỉ chứa data + ring buffer.

  Dữ liệu được quản lý:
    - Device logs: nhận từ firmware qua log_data_t
    - App logs: tạo bởi app cho debug (INFO, WARN, ERROR, DEBUG)
    - Log buffer: giới hạn N entries để không tràn memory
    - Export-ready data: để save ra .csv/.txt

  Được sử dụng bởi:
    - LogViewModel        → append logs, filter, search
    - LogTabView          → hiển thị log entries
    - StatusBarView       → hiển thị error/warning count

  Protocol Messages liên quan:
    - log_data_t       (tag=37)  → Device log data
    - log_clear_t      (tag=38)  → Xóa log trên device

  Data fields:
    @dataclass
    class LogEntry:
        timestamp: float            # time.time()
        level: str                  # "INFO" / "WARN" / "ERROR" / "DEBUG"
        source: str                 # "DEVICE" / "APP" / "PROTOCOL"
        message: str                # Nội dung log
        raw_data: bytes | None      # Raw bytes (chỉ cho device log)

    @dataclass
    class LogState:
        entries: list[LogEntry]     # Tất cả log entries
        max_entries: int = 5000     # Giới hạn buffer
        unread_error_count: int     # Số error chưa xem
        unread_warn_count: int      # Số warning chưa xem
        filter_level: str           # Current filter ("ALL"/"ERROR"/...)
        search_query: str           # Current search string
===============================================================================
"""
pass
