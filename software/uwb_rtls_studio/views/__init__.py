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
    ├── windows/          → Top-level windows
    │   └── main_window.py
    ├── popups/           → Modal popup dialogs
    │   ├── dongle_popup.py    → Popup detect dongle
    │   └── scan_popup.py      → Popup scan + select device
    ├── tabs/             → Tab pages trong MainWindow
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
===============================================================================
"""
