"""
===============================================================================
  UWB RTLS Studio — Workers Package
===============================================================================
  Package     : workers/
  Description : QThread workers cho các tác vụ chạy nền (non-blocking).
                Workers chạy trên background threads để không block UI.

  MVVM Role   : INFRASTRUCTURE — threading support.

  Sub-modules:
    ├── serial_read_worker.py   → Đọc serial port liên tục
    ├── dongle_detect_worker.py → Scan COM ports tìm dongle
    └── periodic_poll_worker.py → Poll định kỳ (battery, status)

  Tất cả workers kế thừa QThread hoặc QRunnable.
  
  CƠ CHẾ HOẠT ĐỘNG VÀ ĐỒNG BỘ:
  - Workers không bao giờ chạm trực tiếp vào Models, ViewModels hay UI.
  - Worker -> Main Thread: Sử dụng Qt Signals (QueuedConnection tự động xử lý an toàn).
  - Main Thread -> Worker: Sử dụng Thread-safe Queues (như queue.Queue).
  
  Ví dụ luồng Serial:
    [Main Thread] gửi lệnh -> [Serial.write Queue] -> [Serial Cổng USB]
    [Serial Cổng USB] -> [SerialReadWorker] -> emit(Signal(bytes)) -> [Main Thread]
===============================================================================
"""
