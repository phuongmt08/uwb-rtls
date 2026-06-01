"""
===============================================================================
  UWB RTLS Studio — Device Model
===============================================================================
  File        : models/device_model.py
  Description : Data model cho các BLE devices đã scan được (Anchors + Tags).
                Quản lý danh sách device, thông tin quảng bá (advertisement),
                và trạng thái kết nối với từng device.

  MVVM Role   : MODEL — chỉ chứa data, KHÔNG có logic giao tiếp.

  Dữ liệu được quản lý:
    - Danh sách scanned devices (cả Anchor lẫn Tag)
    - Thông tin advertisement mỗi device
    - Connected device hiện tại (tag đang theo dõi)
    - BLE connection parameters

  Được sử dụng bởi:
    - ScanViewModel         → cập nhật scan results vào list
    - ScanPopupView         → hiển thị danh sách devices
    - MainViewModel         → reference connected device info
    - DeviceInfoTabView     → hiển thị chi tiết device

  Protocol Messages liên quan:
    - ble_scan_start_t       (tag=51) → Bắt đầu scan
    - ble_scan_stop_t        (tag=52) → Dừng scan
    - ble_scan_result_t      (tag=54) → Mỗi device tìm thấy
    - ble_connect_t          (tag=53) → Connect tới device
    - ble_disconnect_t       (tag=50) → Disconnect
    - ble_adv_status_t       (tag=36) → Adv data của device
    - device_information_resp_t (tag=5) → Device info chi tiết
    - ble_conn_params_resp_t (tag=49) → Connection params

  Data fields:
    @dataclass
    class ScannedDevice:
        mac_address: bytes          # 6-byte MAC
        rssi_dbm: int               # Cường độ tín hiệu
        name: str                   # Tên BLE device
        serial_number: int          # Serial từ adv data
        device_type: str            # "TAG" / "ANCHOR" / "UNKNOWN"
        last_seen_ts: float         # Timestamp lần cuối nhận adv
        is_selected: bool           # User đã chọn device này chưa

    @dataclass
    class ConnectedDevice:
        mac_address: bytes
        device_type: str
        device_role: str
        fw_version: str
        hw_version: int
        serial_number: int
        uid: bytes
        rssi_dbm: int
        connection_ts: float        # Thời điểm connect thành công
        ble_conn_params: dict       # min/max interval, latency, timeout

    @dataclass
    class DeviceListState:
        scanned_devices: list[ScannedDevice]    # Tất cả devices đã scan
        connected_device: ConnectedDevice | None # Device đang connect
        is_scanning: bool                        # Đang quét hay không
        scan_duration_ms: int                    # Thời gian mỗi lần quét
===============================================================================
"""

