"""
===============================================================================
  UWB RTLS Studio — Dongle Detect Service
===============================================================================
  File        : services/dongle_detect_service.py
  Description : Auto-detect NRF52840 dongle qua USB VID/PID.
                Scan tất cả COM ports, tìm port match dongle.

  MVVM Role   : SERVICE — hardware detection.

  Chức năng:
    - Enumerate tất cả COM ports (serial.tools.list_ports)
    - Filter theo VID/PID (Nordic NRF52840 = VID:0x1915)
    - Return port info nếu tìm thấy
    - Hỗ trợ hot-plug detection (polling)

  Dependencies: pyserial (serial.tools.list_ports)
===============================================================================
"""
pass
