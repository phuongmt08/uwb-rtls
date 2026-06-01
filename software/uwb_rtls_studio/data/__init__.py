"""
===============================================================================
  UWB RTLS Studio — Data Directory
===============================================================================
  Package     : data/
  Description : Thư mục lưu trữ data output cho debug và analysis.
                Đây là nơi SessionRepository lưu toàn bộ session history.

  ═══════════════════════════════════════════════════════════════════════
  QUAN TRỌNG — Session History được lưu vĩnh viễn tại đây
  ═══════════════════════════════════════════════════════════════════════

  Khi user nhấn End Session:
    1. App dừng protobuf activities (ranging/streaming/log)
    2. Bundle toàn bộ data → save vào data/sessions/SES_xxxx/
    3. Session KHÔNG bị xóa tự động
    4. User/Developer browse history qua Log Tab → Session History

  Storage Structure:
    data/
    └── sessions/                          ← Tất cả session history
        ├── SES_20260530_123000_ranging/   ← Session folder
        │   ├── session_meta.json          ← Metadata (type, time, device, stats)
        │   ├── positions.csv              ← Position samples (nếu ranging)
        │   ├── logs.csv                   ← Log entries trong session
        │   ├── config_snapshot.json       ← Device config snapshot
        │   ├── ranging_stats.json         ← Ranging statistics (nếu ranging)
        │   └── raw_packets.bin            ← Raw protobuf (optional, debug)
        ├── SES_20260530_140500_streaming/
        │   ├── session_meta.json
        │   ├── logs.csv
        │   └── config_snapshot.json
        ├── SES_20260531_091500_log/
        │   ├── session_meta.json
        │   ├── device_logs.csv
        │   └── config_snapshot.json
        └── ... (tất cả history sessions)

  File formats:
    - session_meta.json : Session metadata (JSON)
    - positions.csv     : timestamp_ms, x_m, y_m, z_m, rms_error_m, anchor_distances
    - logs.csv          : timestamp, level, source, message
    - config_snapshot.json : Full device config tại thời điểm session
    - ranging_stats.json : Total/success/failed/timeout counts, rates
    - raw_packets.bin   : Serialized protobuf packets (binary)

  Naming convention:
    SES_{YYYYMMDD}_{HHMMSS}_{type}/
    Ví dụ: SES_20260530_123000_ranging/

  NOTE:
    - Thêm data/sessions/ vào .gitignore
    - Xóa session cũ = user chủ động xóa qua Session Browser
    - App tự tạo thư mục nếu chưa tồn tại
===============================================================================
"""
