"""
===============================================================================
  UWB RTLS Studio — Periodic Poll Worker
===============================================================================
  File        : workers/periodic_poll_worker.py
  Description : QTimer-based worker poll định kỳ các thông tin:
                battery, BLE status, ranging status, calib status.

  Chức năng:
    - Poll battery info mỗi 30s
    - Poll BLE status mỗi 10s
    - Poll ranging status mỗi 5s (khi ranging active)
    - Poll calib status mỗi 2s (khi calibrating)
    - Configurable intervals
===============================================================================
"""
pass
