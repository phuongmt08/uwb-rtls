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

