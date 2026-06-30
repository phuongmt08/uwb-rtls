# CMD Flow Classification

## once
- `device_information_get`
- `sys_config_get`
- `sys_config_set`
- `sys_ranging_cfg_get`
- `sys_ranging_cfg_set`
- `ranging_status_get`
- `sensor_fusion_cfg_get`
- `sensor_fusion_cfg_set`
- `imu_reset`
- `imu_calib_start`
- `device_reset`
- `uwb_reset`
- `factory_config_reset`
- `device_type_set`
- `device_type_get`
- `ble_adv_config_set`
- `log_clear`
- `host_transport_set`
- `pos_calib_cfg_get`
- `pos_calib_cfg_set`
- `anchor_layout_get`
- `anchor_layout_set`
- `enter_to_bootloader`
- `calib_status_get`
- `rtos_resource_get`
- `rtos_task_stats_get`
- `ble_conn_params_get`
- `ble_conn_params_set`

## event
- `ranging_start`
- `ranging_stop`
- `ranging_result`
- `end_session`
- `ble_disconnect`
- `ble_connect`
- `ble_scan_start`
- `ble_scan_stop`
- `log_data`
- `sys_config_set`
- `sys_ranging_cfg_set`
- `sensor_fusion_cfg_set`
- `pos_calib_cfg_set`
- `anchor_layout_set`
- `ble_conn_params_set`
- `ble_adv_config_set`
- `device_type_set`
- `device_reset`
- `uwb_reset`
- `factory_config_reset`
- `enter_to_bootloader`
- `imu_reset`
- `imu_calib_start`
- `calib_status_get`
- `time_sync_adv_set`

## polling/interval
- `ble_status_get`  # 10s
- `battery_info_get`  # 2s