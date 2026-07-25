"""
===============================================================================
  UWB RTLS Studio — Config Model
===============================================================================
  File        : models/config_model.py
  Description : Data model cho tất cả configuration parameters.
                Bao gồm UWB config, BLE config, ranging config,
                sensor fusion config, và calibration config.

  MVVM Role   : MODEL — chỉ chứa data + validation.

  Dữ liệu được quản lý:
    ┌─────────────────────┬──────────────────────────────────────────────┐
    │ Config Group        │ Fields                                      │
    ├─────────────────────┼──────────────────────────────────────────────┤
    │ UWB Config          │ role, device_id, channel, prf, data_rate,   │
    │                     │ preamble_code, tx/rx antenna delay,         │
    │                     │ tx_power, anchor_list, power_mode           │
    ├─────────────────────┼──────────────────────────────────────────────┤
    │ Ranging Config      │ rx_timeout_ms, ranging_period_ms            │
    ├─────────────────────┼──────────────────────────────────────────────┤
    │ Sensor Fusion       │ alpha, kappa, beta, q_a, q_g, r_uwb,       │
    │ Config              │ init_p_* (8 fields)                         │
    ├─────────────────────┼──────────────────────────────────────────────┤
    │ Calibration Config  │ enable flags, ref_distance, heights,        │
    │                     │ samples, thresholds, damping, iterations,   │
    │                     │ diagnostics (error mean, std, rms, ...)     │
    ├─────────────────────┼──────────────────────────────────────────────┤
    │ BLE Config          │ adv enable, device_name, conn_params        │
    └─────────────────────┴──────────────────────────────────────────────┘

  Được sử dụng bởi:
    - ConfigViewModel     → đọc/ghi config, send GET/SET commands
    - ConfigTabView       → hiển thị form để user chỉnh config
    - CalibrationTabView  → hiển thị calibration params

  Protocol Messages liên quan:
    - sys_config_get_t / sys_config_set_t / sys_config_resp_t (10-12)
    - sys_ranging_cfg_get_t / _set_t / _resp_t (13-15)
    - sensor_fusion_cfg_get_t / _set_t / _resp_t (21-23)
    - ble_adv_config_t (33), ble_conn_params_* (47-49)
===============================================================================
"""

