"""
===============================================================================
  UWB RTLS Studio — Repository Package
===============================================================================
  Package     : repository/
  Description : Chứa toàn bộ logic lưu trữ, đọc lại, và quản lý
                session history. Đây là PERSISTENCE LAYER trong MVVM.

                Khi user nhấn "End Session":
                  1. Dừng hoạt động protobuf đang chạy (ranging/streaming/log)
                  2. Tự động bundle toàn bộ data → save vào 1 session folder
                  3. Session folder được lưu vĩnh viễn (không mất đi)
                  4. User/Developer có thể browse history sessions để debug

  MVVM Role   : REPOSITORY — Data Persistence + History Management.
                Repository KHÔNG biết gì về View.
                ViewModel gọi Repository để save/load sessions.

  Kiến trúc:
    ┌──────────┐     ┌──────────────┐     ┌──────────────┐
    │ ViewModel│ ──► │  Repository  │ ──► │  File System  │
    │          │ ◄── │              │ ◄── │  (data/)      │
    └──────────┘     └──────────────┘     └──────────────┘

  Sub-modules:
    ├── session_repository.py   → Save/load/list session bundles
    └── session_browser.py      → Browse + filter past sessions
===============================================================================
"""
