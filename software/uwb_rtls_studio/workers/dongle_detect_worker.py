"""
===============================================================================
  UWB RTLS Studio — Dongle Detect Worker
===============================================================================
  File        : workers/dongle_detect_worker.py
  Description : QThread scan tất cả COM ports tìm NRF52840 dongle.
                Chạy non-blocking để không freeze UI.

  Chức năng:
    - Enumerate COM ports
    - Filter theo VID/PID
    - Emit found/not_found signal
    - Retry logic với timeout
===============================================================================
"""
pass
