"""Host-side command enable flags.

Set a command to 0 to prevent the app from sending it. This does not change the
protobuf protocol; it only gates host-side calls.
"""
from __future__ import annotations


COMMAND_ENABLE = {
    "device_information_get": 1,
    "time_sync_get": 1,
    "time_sync_set": 1,
    "time_sync_bcast_set": 1,
    "sys_config_get": 1,
    "sys_config_set": 1,
    "sys_ranging_cfg_get": 1,
    "sys_ranging_cfg_set": 1,
    "ranging_start": 1,
    "ranging_stop": 1,
    "ranging_status_get": 1,
    "sensor_fusion_cfg_get": 1,
    "sensor_fusion_cfg_set": 1,
    "imu_reset": 1,
    "imu_calib_start": 1,
    "device_reset": 1,
    "uwb_reset": 1,
    "factory_config_reset": 1,
    "device_type_get": 1,
    "device_type_set": 1,
    "ble_adv_config_set": 1,
    "ble_status_get": 1,
    "ble_conn_params_get": 1,
    "ble_conn_params_set": 1,
    "ble_disconnect": 1,
    "ble_scan_start": 1,
    "ble_scan_stop": 1,
    "ble_connect": 1,
    "log_data": 1,
    "log_clear": 1,
    "host_transport_set": 1,
    "prefilter_cfg_get": 1,
    "prefilter_cfg_set": 1,
    "anchor_layout_get": 1,
    "anchor_layout_set": 1,
    "battery_info_get": 1,
    "enter_to_bootloader": 1,
    "rtos_resource_get": 1,
    "rtos_task_stats_get": 1,
    "end_session": 1,
    "zone_switch": 1,
    "zone_profile_set": 1,
    "zone_profile_get": 1,
}


def is_command_enabled(command_name: str) -> bool:
    return bool(COMMAND_ENABLE.get(command_name, 1))
