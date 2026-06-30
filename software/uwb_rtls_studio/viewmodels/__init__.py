"""
===============================================================================
  UWB RTLS Studio — ViewModels Package
===============================================================================
  Package     : viewmodels/
  Description : Chứa toàn bộ presentation logic (ViewModel layer).
                ViewModel là CẦU NỐI giữa Model (data) và View (UI).
                ViewModel đọc/ghi Model, expose data qua Qt Signals,
                và handle user actions từ View.

  MVVM Role   : VIEWMODEL — Business Logic + Presentation Logic.
                ViewModel KHÔNG import trực tiếp View classes.
                Giao tiếp với View qua Qt Signals/Slots.

  Quy tắc MVVM:
    ┌──────────┐    Signal/Slot    ┌──────────────┐    Direct     ┌─────────┐
    │   View   │ ◄──────────────► │  ViewModel   │ ──────────── │  Model  │
    │  (UI)    │                   │  (Logic)     │              │  (Data) │
    └──────────┘                   └──────────────┘              └─────────┘
         │                               │                           │
         │  View gọi ViewModel methods   │  ViewModel đọc/ghi Model │
         │  ViewModel emit signals       │  trực tiếp               │
         └───────────────────────────────┘                           │
                                         └───────────────────────────┘

  Sub-modules:
    ├── dongle_viewmodel.py      → Logic detect + connect dongle
    ├── scan_viewmodel.py        → Logic scan BLE + select device
    ├── main_viewmodel.py        → Logic chính: tab switching, session
    ├── device_info_viewmodel.py → Logic tab Device Info
    ├── live_tracking_viewmodel.py → Logic tab Live Tracking
    ├── config_viewmodel.py      → Logic tab Config Parameters
    ├── calibration_viewmodel.py → Logic tab Calibration
    └── log_viewmodel.py         → Logic tab Log Viewer
===============================================================================
"""
