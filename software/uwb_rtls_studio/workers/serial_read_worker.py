"""
===============================================================================
  UWB RTLS Studio — Serial Read Worker
===============================================================================
  File        : workers/serial_read_worker.py
  Description : QThread đọc dữ liệu từ serial port liên tục.
                Emit raw bytes qua signal → SerialService → ProtocolService.

  Chức năng:
    - Chạy vòng lặp đọc serial.read() trên background thread
    - Emit data_received signal khi có data
    - Handle serial errors (port disconnected, timeout)
    - Clean shutdown khi app đóng
===============================================================================
"""
pass
