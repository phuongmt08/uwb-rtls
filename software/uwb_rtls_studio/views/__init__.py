"""
===============================================================================
  UWB RTLS Studio — Views Package
===============================================================================
  Package     : views/
  Description : Chứa toàn bộ UI components (View layer).
                View KHÔNG chứa business logic.
                View chỉ hiển thị data từ ViewModel và forward user actions.

  MVVM Role   : VIEW — Pure UI, KHÔNG có logic.

  Sub-packages:
    ├── ui/               → .ui files from Qt Designer (FE only)
    │   ├── main_window.ui       → MainWindow layout
    │   ├── device_info_tab.ui   → Tab 1: Device Info
    │   ├── live_tracking_tab.ui → Tab 2: Live Tracking
    │   ├── config_tab.ui        → Tab 3: Configuration
    │   ├── calibration_tab.ui   → Tab 4: Calibration
    │   └── log_tab.ui           → Tab 5: Log & History
    │
    ├── windows/          → Top-level windows (loads .ui + BE logic)
    │   └── main_window.py
    ├── popups/           → Modal popup dialogs (FE + BE, KHÔNG dùng .ui)
    │   ├── dongle_popup.py    → Popup detect dongle
    │   └── scan_popup.py      → Popup scan + select device
    ├── tabs/             → Tab pages trong MainWindow (loads .ui + BE logic)
    │   ├── device_info_tab.py      → Tab 1 (User + Dev)
    │   ├── live_tracking_tab.py    → Tab 2 (User + Dev)
    │   ├── config_tab.py           → Tab 3 (User + Dev, nhưng scoped)
    │   ├── calibration_tab.py      → Tab 4 (Developer only)
    │   └── log_tab.py              → Tab 5 (User + Dev) + Session History
    └── components/       → Reusable UI widgets
        ├── status_bar.py
        ├── glass_button.py
        ├── position_canvas.py
        └── log_text_widget.py

  How to edit UI:
    1. Open any .ui file in Qt Designer (designer.exe)
    2. Edit layout, widgets, properties visually
    3. Save → Python files auto-load the updated .ui at runtime
    4. No compilation needed (uses PyQt6.uic.loadUi())
===============================================================================
"""
