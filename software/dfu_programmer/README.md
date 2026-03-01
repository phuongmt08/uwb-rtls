# UWB-RTLS Programmer

Ứng dụng Python GUI để nạp firmware qua USB DFU (STM32 ROM DFU/bootloader DFU), tối ưu cho layout hiện tại:

- App range: `0x0800C000 .. 0x0803FFFF`
- App sectors: `0x0800C000`, `0x08010000`, `0x08020000`

## Tính năng

- Scan theo VID/PID đang nhập (mặc định `0483:DF11`) để tránh bắt nhầm thiết bị DFU khác
- Auto Connect từ danh sách thiết bị đã scan
- Erase theo bảng sector có checkbox (S3..S7), gồm cả vùng App và Data storage
- Mass erase (gửi lệnh DFU `0x41`)
- Flash file `.hex` (Intel HEX), tự lấy địa chỉ từ file
- Nút chọn file `Browse` + lưu danh sách đường dẫn HEX gần đây
- Verify readback sau khi flash
- Progress + log realtime

## Yêu cầu

- Python 3.10+
- Trên Windows: thiết bị DFU cần driver WinUSB/libusb (thường cài bằng Zadig)

## Cài đặt

```bat
cd software
install_requirements.bat
```

Script sẽ tự tạo virtualenv tại `software\\.venv` và cài toàn bộ modules từ `software\\requirements.txt`.
Script dùng `py -3.12`, nên cần cài Python 3.12 trên máy.

## Chạy

```bash
python main.py
```
    
## Quy trình khuyến nghị

1. Connect device
2. Chọn file `.hex` bằng `Browse` (hoặc chọn lại từ danh sách recent)
3. Tick sector cần xoá trong bảng (nếu không tick, `Erase App Sectors` sẽ báo lỗi)
4. Chạy `Erase App Sectors` hoặc `Mass Erase` nếu cần
5. Chạy `Flash`
6. Chạy `Verify`

## Lưu ý

- App chỉ được phép flash trong vùng `0x0800C000 .. 0x0803FFFF`.
- Nếu verify lỗi do target reset/disconnect sau download manifest, connect lại rồi verify lần nữa.
- Nếu không connect được trên Windows, kiểm tra driver bằng Zadig (WinUSB) cho interface DFU.
- Nếu báo `No backend available`: PyUSB chưa thấy `libusb`. Dùng Python `3.12` cho venv, cài `libusb-package`, và cài driver DFU bằng Zadig (WinUSB/libusbK).
