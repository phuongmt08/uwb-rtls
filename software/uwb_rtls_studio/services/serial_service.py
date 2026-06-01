"""
===============================================================================
  UWB RTLS Studio — Serial Service
===============================================================================
  File        : services/serial_service.py
  Description : Quản lý kết nối Serial/USB giữa PC và Dongle.
                Singleton service, được share giữa tất cả ViewModels.

  MVVM Role   : SERVICE — I/O layer.

  Chức năng:
    - Open/Close COM port (pyserial)
    - Read thread (non-blocking, chạy trên QThread)
    - Write Queue & Throttling (Hàng đợi & Điều tiết):
        + Các Tab muốn gửi lệnh phải đẩy vào Write Queue, KHÔNG gửi thẳng.
        + Tránh tình trạng 5 Tab cùng gửi lệnh đồng loạt làm Dongle bị overflow.
        + Luồng gửi sẽ lấy từng lệnh từ Queue ra gửi một cách tuần tự.
    - Auto-reconnect khi mất kết nối
    - Emit raw bytes cho ProtocolService decode

  Thread model:
    ┌─────────────┐     ┌──────────────┐     ┌───────────────┐
    │ Main Thread  │     │ Read Thread   │     │ USB/Serial    │
    │ (Qt UI)      │     │ (QThread)     │     │ (Hardware)    │
    │              │     │               │     │               │
    │  write() ───────►  │               │ ──► │  TX bytes     │
    │              │     │  read loop ◄──────── │  RX bytes     │
    │  ◄── data_received signal          │     │               │
    └─────────────┘     └──────────────┘     └───────────────┘

  Signals:
    - data_received(data: bytes)       → Raw data từ serial
    - connection_lost()                → Serial disconnected
    - error_occurred(msg: str)         → Lỗi I/O

  Dependencies: pyserial
===============================================================================
"""
pass
