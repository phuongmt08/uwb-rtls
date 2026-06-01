"""
===============================================================================
  UWB RTLS Studio — Dongle Model
===============================================================================
  File        : models/dongle_model.py
  Description : Data model cho USB dongle (NRF52840 Central).
                Lưu trữ trạng thái kết nối giữa PC ↔ Dongle qua USB/Serial.

  MVVM Role   : MODEL — chỉ chứa data, KHÔNG có logic giao tiếp.

  Dữ liệu được quản lý:
    - COM port path (VD: "COM3", "/dev/ttyACM0")
    - VID/PID của USB device (để auto-detect dongle)
    - Connection state (disconnected / connecting / connected / error)
    - Serial number & firmware version của dongle
    - Timestamp lần kết nối gần nhất

  Được sử dụng bởi:
    - DongleViewModel     → đọc/ghi state
    - DonglePopupView     → hiển thị trạng thái detect dongle
    - SerialService       → reference port info

  Protocol Messages liên quan:
    - device_information_get_t  (tag=4)   → Lấy info dongle
    - device_information_resp_t (tag=5)   → Response info dongle
    - host_transport_set_t      (tag=39)  → Set transport USB/UART

  Data fields:
    @dataclass
    class DongleState:
        port: str               # "COM3"
        vid: int                # USB Vendor ID  (Nordic = 0x1915)
        pid: int                # USB Product ID (NRF52840 dongle)
        is_connected: bool      # True khi đã open serial thành công
        serial_number: int      # Từ device_information_resp
        fw_version: str         # Từ device_information_resp
        connection_ts: float    # time.time() lúc connect
        error_msg: str          # "" nếu không có lỗi
===============================================================================
"""

