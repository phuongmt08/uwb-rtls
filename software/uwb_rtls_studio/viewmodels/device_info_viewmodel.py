"""
===============================================================================
  UWB RTLS Studio — Device Info ViewModel
===============================================================================
  File        : viewmodels/device_info_viewmodel.py
  Description : ViewModel cho tab "Device Info" (Tab 1).
                Hiển thị thông tin chi tiết device đang connected.

  MVVM Role   : VIEWMODEL

  Tab này hiển thị (User-Facing):
    ┌─────────────────────────────────────────────────────────┐
    │  DEVICE INFO TAB                                        │
    ├─────────────────────────────────────────────────────────┤
    │  ┌─ Device Identity ──────────────────────────────────┐ │
    │  │  Device Type: TAG / ANCHOR                         │ │
    │  │  Device Role: Tag / Anchor                         │ │
    │  │  Serial Number: 12345                              │ │
    │  │  HW Version: 3                                     │ │
    │  │  FW Version: 1.2.1 (build 45, sha: abc123)         │ │
    │  │  UID: 00:01:02:03:04:05:06:07                      │ │
    │  └────────────────────────────────────────────────────┘ │
    │  ┌─ Battery & Telemetry ──────────────────────────────┐ │
    │  │  Battery: 88% (3.7V) ████████░░ [Charging: No]     │ │
    │  │  Remaining: ~120 min                                │ │
    │  │  MCU Temp: 25.3°C  |  UWB Temp: 24.8°C             │ │
    │  │  IMU Temp: 25.1°C  |  VDDA: 3300mV                 │ │
    │  │  Error Mask: 0x0000                                 │ │
    │  └────────────────────────────────────────────────────┘ │
    │  ┌─ BLE Connection ───────────────────────────────────┐ │
    │  │  State: CONNECTED | RSSI: -45 dBm                  │ │
    │  │  Conn Params: 15-30ms / Latency: 0 / Timeout: 4s   │ │
    │  └────────────────────────────────────────────────────┘ │
    │  ┌─ Time Sync ────────────────────────────────────────┐ │
    │  │  Device Time: 2026-05-30 12:30:00 UTC+7             │ │
    │  │  [Sync Now] button                                  │ │
    │  └────────────────────────────────────────────────────┘ │
    └─────────────────────────────────────────────────────────┘

  Signals:
    - device_info_updated(info: dict)
    - telemetry_updated(telemetry: dict)
    - ble_status_updated(status: dict)
    - time_sync_completed(success: bool)

  Protocol Messages:
    - device_information_get_t / _resp_t  (4, 5)
    - battery_info_get_t / _resp_t        (61, 60)
    - ble_status_get_t / _resp_t          (34, 35)
    - ble_conn_params_get_t / _resp_t     (47, 49)
    - time_sync_get_t / _set_t / _resp_t  (6, 7, 8)
===============================================================================
"""
pass
