"""
===============================================================================
  UWB RTLS Studio — Scan ViewModel
===============================================================================
  File        : viewmodels/scan_viewmodel.py
  Description : ViewModel cho flow BLE scanning + select device.
                Quản lý quá trình quét BLE, danh sách devices,
                và connect tới device được chọn.

  MVVM Role   : VIEWMODEL — xử lý logic scan, emit signals cho ScanPopupView.

  Flow chi tiết:
    1. Sau khi dongle connected → ScanPopup hiện lên
    2. Tự động bắt đầu scan (gửi ble_scan_start_t)
    3. Dongle scan liên tục → gửi ble_scan_result_t cho mỗi device tìm thấy
    4. ViewModel cập nhật DeviceModel, emit device_found signal
    5. ScanPopupView hiển thị list (table) tất cả devices
       → Hiển thị: Name | MAC | Type (Tag/Anchor) | RSSI | Serial
    6. User chọn 1 device (tag) → bấm Connect
    7. Gửi ble_connect_t → hiện log "Connecting..."
    8. Nhận confirmation → hiện log "Connected to [device]"
       → Hiển thị adv info của device đó
    9. Emit device_connected signal → chuyển sang MainWindow

  Signals (emit cho View):
    - scan_started()                       → Bắt đầu scan
    - scan_stopped()                       → Dừng scan
    - device_found(device: dict)           → Tìm thấy 1 device mới
    - device_updated(mac: bytes, rssi: int) → RSSI updated cho device đã có
    - device_connecting(mac: bytes)        → Đang connecting
    - device_connected(info: dict)         → Connect thành công
    - connection_failed(msg: str)          → Connect thất bại
    - log_message(msg: str)               → Log text cho popup log area

  Slots (nhận từ View):
    - on_start_scan()             → User bấm Start Scan
    - on_stop_scan()              → User bấm Stop Scan
    - on_connect_device(mac)      → User chọn device và bấm Connect
    - on_cancel()                 → User bấm Cancel

  Sử dụng:
    - Models : DeviceModel (đọc/ghi scan results)
    - Services: SerialService, ProtocolService
    - Workers : BLEScanWorker (QThread nhận scan results liên tục)
===============================================================================
"""
pass
