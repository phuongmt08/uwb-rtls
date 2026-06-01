"""
===============================================================================
  UWB RTLS Studio — Session Model
===============================================================================
  File        : models/session_model.py
  Description : Data model cho session lifecycle.
                Một "session" = 1 lần ranging / streaming / log liên tục.

  MVVM Role   : MODEL — chỉ chứa data, KHÔNG có logic.

  ═══════════════════════════════════════════════════════════════════════
  QUAN TRỌNG — End Session ≠ Kill App / Kill Device / Kill Dongle
  ═══════════════════════════════════════════════════════════════════════

  End Session chỉ dừng CÁC HOẠT ĐỘNG PROTOBUF đang chạy:
    - Dừng ranging (ranging_stop_t)
    - Dừng streaming log (end_session_t với reason)
    - Dừng bất kỳ session nào đang active

  End Session KHÔNG:
    - Kill app                    (app vẫn chạy bình thường)
    - Disconnect dongle           (dongle vẫn connected qua USB)
    - Disconnect BLE device       (BLE vẫn connected)
    - Reset device                (device vẫn hoạt động)

  Sau khi End Session:
    1. Gửi lệnh dừng protobuf (end_session_t tag=65)
    2. Tự động SAVE toàn bộ data session vào Repository:
       - Positions CSV        (nếu là ranging session)
       - Logs CSV             (tất cả log trong session)
       - Config snapshot JSON (cấu hình tại thời điểm session)
       - Ranging stats JSON   (nếu là ranging session)
       - Session metadata JSON (thời gian, device, statistics)
    3. Session folder được lưu vĩnh viễn trong data/sessions/
    4. User có thể bắt đầu session mới ngay lập tức
    5. User/Developer có thể mở lại session cũ để debug bất kỳ lúc nào

  Session History:
    - Tất cả sessions được lưu (KHÔNG bị xóa tự động)
    - Browse history qua Session Browser (trong Log Tab)
    - Filter theo: date, type, device, duration
    - Xóa session cũ = manual (user chủ động xóa)

  ═══════════════════════════════════════════════════════════════════════

  Session Types:
    - RANGING_SESSION    → Live position tracking / ranging
    - STREAMING_SESSION  → Continuous data stream (debug)
    - LOG_SESSION        → Đọc log từ flash của device
    - IDLE               → Không có session nào active

  Được sử dụng bởi:
    - MainViewModel       → start/end session, trigger save to repository
    - SessionRepository   → persist session data
    - SessionBrowserVM    → browse past sessions
    - StatusBarView       → hiển thị session info
    - LogTab              → hiển thị session history

  Protocol Messages liên quan:
    - end_session_t          (tag=65) → Gửi lệnh end session
    - session_end_reason_t   (enum)  → LOG_DATA / RANGING_RESULTS / DEBUG_STREAMING

  Data fields:
    @dataclass
    class SessionState:
        session_id: str             # "SES_20260530_123000_ranging"
        session_type: str           # "RANGING" / "STREAMING" / "LOG" / "IDLE"
        is_active: bool             # True khi session đang chạy
        start_time: float           # time.time() bắt đầu
        end_time: float | None      # time.time() kết thúc (None nếu active)
        end_reason: str             # "USER_END_SESSION" / "ERROR" / "TIMEOUT"
        total_packets_rx: int       # Số packets đã nhận
        total_packets_tx: int       # Số packets đã gửi
        error_count: int            # Số lỗi trong session
        elapsed_sec: float          # Tổng thời gian session (computed)

    @dataclass
    class SessionDataBundle:
        \"\"\"Bundle tất cả data của 1 session, dùng để save vào repository.\"\"\"
        session_state: SessionState
        position_samples: list      # Tất cả PositionSample trong session
        log_entries: list           # Tất cả LogEntry trong session
        config_snapshot: dict       # Config tại thời điểm session
        ranging_stats: dict | None  # Ranging statistics (nếu có)
        device_info: dict           # Info device đang connected
        dongle_info: dict           # Info dongle đang connected

    @dataclass
    class SessionMeta:
        \"\"\"Metadata tóm tắt 1 session (lightweight, cho session browser).\"\"\"
        session_id: str
        session_type: str
        start_time_iso: str
        end_time_iso: str
        duration_sec: float
        device_type: str
        serial_number: int
        total_packets: int
        error_count: int
        folder_path: str            # Path tới session folder
===============================================================================
"""
pass
