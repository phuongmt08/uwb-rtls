"""
===============================================================================
  UWB RTLS Studio — Models Package
===============================================================================
  Package     : models/
  Description : Chứa toàn bộ data classes và state containers (Model layer).
                Model KHÔNG biết gì về UI (View) hay ViewModel.
                Model chỉ chứa DATA + trạng thái (state) + validation logic.

  MVVM Role   : MODEL — Single Source of Truth cho tất cả dữ liệu.

  Sub-modules:
    ├── dongle_model.py      → Trạng thái của USB dongle (NRF52840 Central)
    ├── device_model.py      → Thông tin thiết bị BLE đã scan/connect
    ├── session_model.py     → Session lifecycle + data bundle + session meta
    ├── ranging_model.py     → Dữ liệu ranging + position results
    ├── config_model.py      → UWB config, BLE config, calibration config
    ├── telemetry_model.py   → Battery, temperature, diagnostics
    └── log_model.py         → Log entries (device log + app log)
===============================================================================
"""
