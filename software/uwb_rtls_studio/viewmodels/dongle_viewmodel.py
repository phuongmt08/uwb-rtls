"""
===============================================================================
  UWB RTLS Studio — Dongle ViewModel
===============================================================================
  File        : viewmodels/dongle_viewmodel.py
  Description : ViewModel cho flow detect + connect USB dongle.
                Xử lý toàn bộ logic từ lúc cắm dongle → detect → connect.

  MVVM Role   : VIEWMODEL — xử lý logic, emit signals cho DonglePopupView.

  Flow chi tiết:
    1. App khởi động → start_detection()
    2. Worker thread scan tất cả COM ports
    3. Tìm port nào có VID/PID match NRF52840 dongle
    4. Nếu tìm thấy → emit dongle_detected signal
       → DonglePopupView hiển thị popup "Detected Dongle Central NRF52840"
    5. Tự động connect (open serial port)
    6. Gửi device_information_get để verify đúng là dongle
    7. Nhận device_information_resp → xác nhận dongle hợp lệ
    8. Emit dongle_connected signal → chuyển sang ScanPopup

  Signals (emit cho View):
    - dongle_detected(port: str)           → Tìm thấy dongle
    - dongle_connected(info: dict)         → Connect thành công
    - dongle_error(msg: str)               → Lỗi detect/connect
    - dongle_disconnected()                → Dongle bị rút / mất kết nối
    - status_message(msg: str)             → Cập nhật status text

  Slots (nhận từ View):
    - on_retry_detection()    → User bấm retry
    - on_cancel()             → User bấm cancel

  Sử dụng:
    - Models : DongleModel (đọc/ghi state)
    - Services: SerialService (mở/đóng COM port)
              : ProtocolService (encode/decode protobuf)
    - Workers : DongleDetectWorker (QThread cho scan ports)
===============================================================================
"""
pass
