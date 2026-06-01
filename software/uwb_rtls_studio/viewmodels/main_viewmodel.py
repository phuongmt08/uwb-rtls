"""
===============================================================================
  UWB RTLS Studio — Main ViewModel
===============================================================================
  File        : viewmodels/main_viewmodel.py
  Description : ViewModel chính cho MainWindow.
                Quản lý tab switching, session lifecycle, và
                coordination giữa các sub-ViewModels.

  MVVM Role   : VIEWMODEL — coordinator cho tất cả tabs.

  Chức năng chính:
    1. Tab management: xác định tab nào active, enable/disable tabs
    2. Session lifecycle:
       - Start session (ranging / streaming / log)
       - End session → dừng protobuf activities + save to repository
       - Session state tracking
    3. User/Developer mode switching:
       - User mode:  5 tabs visible, nhưng giới hạn chi tiết:
           • Config: chỉ hiện User sections (anchor layout, time sync, ...)
           • Log: chỉ hiện Device logs (INFO/WARN/ERROR)
           • Calibration: ẩn hoàn toàn
       - Developer mode: 5 tabs visible, full access tất cả sections
    4. Status bar updates: connection info, battery, session timer
    5. Coordinate sub-ViewModels: gọi methods của các tab ViewModels
    6. End Session flow:
       a. Gửi end_session_t (tag=65) với reason phù hợp
       b. Dừng ranging/streaming/log (chỉ dừng protobuf activities)
       c. Bundle toàn bộ session data → save vào SessionRepository
       d. Session history được lưu vĩnh viễn trong data/sessions/
       e. App vẫn chạy, dongle vẫn connected, BLE vẫn connected
       f. User có thể bắt đầu session mới ngay
    7. App shutdown: cleanup khi user đóng app hoàn toàn
       - Nếu session đang active → auto end session + save trước
       - Disconnect BLE, close serial, stop workers

  Signals (emit cho View):
    - tab_changed(index: int)              → Active tab changed
    - session_started(session_type: str)   → Session bắt đầu
    - session_ended(reason: str)           → Session kết thúc
    - session_saved(session_id: str)       → Session đã save vào repository
    - mode_changed(mode: str)              → User ↔ Developer
    - status_updated(info: dict)           → Status bar data
    - app_shutdown_requested()             → App sắp đóng

  Slots (nhận từ View):
    - on_tab_selected(index: int)
    - on_end_session()
    - on_toggle_mode()
    - on_app_close()

  Sử dụng:
    - Models : SessionModel, DeviceModel (connected device info)
    - Sub-VMs: DeviceInfoVM, LiveTrackingVM, ConfigVM, CalibVM, LogVM
    - Services: SerialService, ProtocolService
    - Repository: SessionRepository (save session data)
===============================================================================
"""

