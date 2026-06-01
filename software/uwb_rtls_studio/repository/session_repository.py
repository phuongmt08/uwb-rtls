"""
===============================================================================
  UWB RTLS Studio — Session Repository
===============================================================================
  File        : repository/session_repository.py
  Description : Quản lý lưu trữ và đọc lại session data.
                Mỗi session được save thành 1 folder riêng biệt trong data/.
                Toàn bộ history sessions được giữ lại vĩnh viễn.

  MVVM Role   : REPOSITORY — persistence layer.

  Khi End Session được trigger:
    1. ViewModel gọi repository.save_session(session_state, data_bundle)
    2. Repository tạo folder mới: data/sessions/SES_20260530_123000_ranging/
    3. Ghi các files vào folder:
       - session_meta.json     → metadata: type, start/end time, device info
       - positions.csv         → tất cả position samples (nếu ranging)
       - logs.csv              → tất cả log entries trong session
       - config_snapshot.json  → config snapshot tại thời điểm session
       - ranging_stats.json    → ranging statistics (nếu ranging)
       - raw_packets.bin       → raw protobuf packets (optional, cho deep debug)
    4. Emit session_saved signal với session_id

  Session History:
    - Tất cả sessions được lưu trong data/sessions/
    - KHÔNG bị xóa tự động (persistence)
    - User/Developer có thể browse qua Session Browser
    - Hỗ trợ filter theo: date, type, device, duration
    - Hỗ trợ delete individual sessions nếu muốn

  Storage Structure:
    data/sessions/
    ├── SES_20260530_123000_ranging/       ← Session 1
    │   ├── session_meta.json
    │   ├── positions.csv
    │   ├── logs.csv
    │   ├── config_snapshot.json
    │   ├── ranging_stats.json
    │   └── raw_packets.bin (optional)
    ├── SES_20260530_140500_streaming/     ← Session 2
    │   ├── session_meta.json
    │   ├── logs.csv
    │   └── config_snapshot.json
    ├── SES_20260531_091500_log/           ← Session 3
    │   ├── session_meta.json
    │   ├── device_logs.csv
    │   └── config_snapshot.json
    └── ...                                ← Tất cả history được giữ lại

  Session Metadata JSON (session_meta.json):
    {
        "session_id": "SES_20260530_123000_ranging",
        "session_type": "RANGING",
        "start_time_iso": "2026-05-30T12:30:00+07:00",
        "end_time_iso": "2026-05-30T12:35:23+07:00",
        "duration_sec": 323.0,
        "end_reason": "USER_END_SESSION",
        "device_info": {
            "device_type": "TAG",
            "serial_number": 12345,
            "fw_version": "1.2.1",
            "mac_address": "00:11:22:33:44:55"
        },
        "dongle_info": {
            "port": "COM3",
            "serial_number": 99999
        },
        "statistics": {
            "total_packets_rx": 3230,
            "total_packets_tx": 15,
            "error_count": 2,
            "position_samples": 3210,
            "avg_update_rate_hz": 10.0,
            "avg_rms_error_m": 0.045
        },
        "files": [
            "positions.csv",
            "logs.csv",
            "config_snapshot.json",
            "ranging_stats.json"
        ]
    }

  Public API:
    - save_session(session_state, data_bundle) → session_id
    - list_sessions(filter=None) → list[SessionMeta]
    - load_session(session_id) → SessionBundle
    - delete_session(session_id) → bool
    - get_session_meta(session_id) → SessionMeta
    - get_sessions_dir() → Path

  Được sử dụng bởi:
    - MainViewModel       → on_end_session() → save
    - SessionBrowserVM    → list, load, delete
    - DataExportService   → (deprecated, replaced by repository)
===============================================================================
"""
pass
