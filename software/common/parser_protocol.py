from __future__ import annotations

from .commands import CommandFactory
from .transport import HdlcChunk, HdlcCodec, HostTransport, VvAddress, VvProtocol as _VvProtocol


class VvProtocol(_VvProtocol):
    def __init__(self) -> None:
        super().__init__()
        self._commands = CommandFactory()

    def build_none(self, src: int, dst: int, seq: int):
        return self._commands.none(src, dst, seq)
    def build_ack(self, src: int, dst: int, seq: int):
        return self._commands.ack(src, dst, seq)
    def build_device_information_get(self, src: int, dst: int, seq: int):
        return self._commands.device_information_get(src, dst, seq)
    def build_device_information_resp(self, src: int, dst: int, seq: int):
        return self._commands.device_information_resp(src, dst, seq)
    def build_time_sync_get(self, src: int, dst: int, seq: int):
        return self._commands.time_sync_get(src, dst, seq)
    def build_time_sync_set(self, src: int, dst: int, seq: int):
        return self._commands.time_sync_set(src, dst, seq)
    def build_time_sync_resp(self, src: int, dst: int, seq: int):
        return self._commands.time_sync_resp(src, dst, seq)
    def build_time_sync_adv_set(self, src: int, dst: int, seq: int):
        return self._commands.time_sync_adv_set(src, dst, seq)
    def build_sys_config_get(self, src: int, dst: int, seq: int):
        return self._commands.sys_config_get(src, dst, seq)
    def build_sys_config_set(self, src: int, dst: int, seq: int):
        return self._commands.sys_config_set(src, dst, seq)
    def build_sys_config_resp(self, src: int, dst: int, seq: int):
        return self._commands.sys_config_resp(src, dst, seq)
    def build_sys_ranging_cfg_get(self, src: int, dst: int, seq: int):
        return self._commands.sys_ranging_cfg_get(src, dst, seq)
    def build_sys_ranging_cfg_set(self, src: int, dst: int, seq: int):
        return self._commands.sys_ranging_cfg_set(src, dst, seq)
    def build_sys_ranging_cfg_resp(self, src: int, dst: int, seq: int):
        return self._commands.sys_ranging_cfg_resp(src, dst, seq)
    def build_ranging_start(self, src: int, dst: int, seq: int):
        return self._commands.ranging_start(src, dst, seq)
    def build_ranging_stop(self, src: int, dst: int, seq: int):
        return self._commands.ranging_stop(src, dst, seq)
    def build_ranging_result(self, src: int, dst: int, seq: int):
        return self._commands.ranging_result(src, dst, seq)
    def build_ranging_status_get(self, src: int, dst: int, seq: int):
        return self._commands.ranging_status_get(src, dst, seq)
    def build_ranging_status_resp(self, src: int, dst: int, seq: int):
        return self._commands.ranging_status_resp(src, dst, seq)
    def build_sensor_fusion_cfg_get(self, src: int, dst: int, seq: int):
        return self._commands.sensor_fusion_cfg_get(src, dst, seq)
    def build_sensor_fusion_cfg_set(self, src: int, dst: int, seq: int):
        return self._commands.sensor_fusion_cfg_set(src, dst, seq)
    def build_sensor_fusion_cfg_resp(self, src: int, dst: int, seq: int):
        return self._commands.sensor_fusion_cfg_resp(src, dst, seq)
    def build_device_reset(self, src: int, dst: int, seq: int):
        return self._commands.device_reset(src, dst, seq)
    def build_uwb_reset(self, src: int, dst: int, seq: int):
        return self._commands.uwb_reset(src, dst, seq)
    def build_factory_config_reset(self, src: int, dst: int, seq: int):
        return self._commands.factory_config_reset(src, dst, seq)
    def build_device_type_set(self, src: int, dst: int, seq: int):
        return self._commands.device_type_set(src, dst, seq)
    def build_device_type_get(self, src: int, dst: int, seq: int):
        return self._commands.device_type_get(src, dst, seq)
    def build_flash_erase(self, src: int, dst: int, seq: int):
        return self._commands.flash_erase(src, dst, seq)
    def build_flash_read(self, src: int, dst: int, seq: int):
        return self._commands.flash_read(src, dst, seq)
    def build_flash_data(self, src: int, dst: int, seq: int):
        return self._commands.flash_data(src, dst, seq)
    def build_flash_write(self, src: int, dst: int, seq: int):
        return self._commands.flash_write(src, dst, seq)
    def build_ble_adv_config_set(self, src: int, dst: int, seq: int):
        return self._commands.ble_adv_config_set(src, dst, seq)
    def build_ble_status_get(self, src: int, dst: int, seq: int):
        return self._commands.ble_status_get(src, dst, seq)
    def build_ble_status_resp(self, src: int, dst: int, seq: int):
        return self._commands.ble_status_resp(src, dst, seq)
    def build_ble_adv_status(self, src: int, dst: int, seq: int):
        return self._commands.ble_adv_status(src, dst, seq)
    def build_log_data(self, src: int, dst: int, seq: int):
        return self._commands.log_data(src, dst, seq)
    def build_log_clear(self, src: int, dst: int, seq: int):
        return self._commands.log_clear(src, dst, seq)
    def build_host_transport_set(self, src: int, dst: int, seq: int, transport: HostTransport = HostTransport.USB):
        pkt = self._commands.host_transport_set(src, dst, seq)
        pkt.host_transport_set.transport = int(transport)
        return pkt
    def build_pos_calib_cfg_get(self, src: int, dst: int, seq: int):
        return self._commands.pos_calib_cfg_get(src, dst, seq)
    def build_pos_calib_cfg_set(self, src: int, dst: int, seq: int):
        return self._commands.pos_calib_cfg_set(src, dst, seq)
    def build_pos_calib_cfg_resp(self, src: int, dst: int, seq: int):
        return self._commands.pos_calib_cfg_resp(src, dst, seq)
    def build_anchor_layout_get(self, src: int, dst: int, seq: int):
        return self._commands.anchor_layout_get(src, dst, seq)
    def build_anchor_layout_set(self, src: int, dst: int, seq: int):
        return self._commands.anchor_layout_set(src, dst, seq)
    def build_anchor_layout_resp(self, src: int, dst: int, seq: int):
        return self._commands.anchor_layout_resp(src, dst, seq)
    def build_flash_verify(self, src: int, dst: int, seq: int):
        return self._commands.flash_verify(src, dst, seq)
    def build_ble_conn_params_get(self, src: int, dst: int, seq: int):
        return self._commands.ble_conn_params_get(src, dst, seq)
    def build_ble_conn_params_set(self, src: int, dst: int, seq: int):
        return self._commands.ble_conn_params_set(src, dst, seq)
    def build_ble_conn_params_resp(self, src: int, dst: int, seq: int):
        return self._commands.ble_conn_params_resp(src, dst, seq)
    def build_ble_disconnect(self, src: int, dst: int, seq: int):
        return self._commands.ble_disconnect(src, dst, seq)
    def build_ble_scan_start(self, src: int, dst: int, seq: int):
        return self._commands.ble_scan_start(src, dst, seq)
    def build_ble_scan_stop(self, src: int, dst: int, seq: int):
        return self._commands.ble_scan_stop(src, dst, seq)
    def build_ble_connect(self, src: int, dst: int, seq: int):
        return self._commands.ble_connect(src, dst, seq)
    def build_ble_scan_result(self, src: int, dst: int, seq: int):
        return self._commands.ble_scan_result(src, dst, seq)
    def build_fota_state_resp(self, src: int, dst: int, seq: int):
        return self._commands.fota_state_resp(src, dst, seq)
    def build_battery_info_resp(self, src: int, dst: int, seq: int):
        return self._commands.battery_info_resp(src, dst, seq)
    def build_battery_info_get(self, src: int, dst: int, seq: int):
        return self._commands.battery_info_get(src, dst, seq)
    def build_enter_to_bootloader(self, src: int, dst: int, seq: int):
        return self._commands.enter_to_bootloader(src, dst, seq)
    def build_calib_status_get(self, src: int, dst: int, seq: int):
        return self._commands.calib_status_get(src, dst, seq)
    def build_calib_status_resp(self, src: int, dst: int, seq: int):
        return self._commands.calib_status_resp(src, dst, seq)
    def build_end_session(self, src: int, dst: int, seq: int, reason: int = 0):
        return self._commands.end_session(src, dst, seq, reason)
    def build_factory_otp_write(self, src: int, dst: int, seq: int):
        return self._commands.factory_otp_write(src, dst, seq)
    def build_imu_reset(self, src: int, dst: int, seq: int):
        return self._commands.imu_reset(src, dst, seq)
    def build_imu_calib_start(self, src: int, dst: int, seq: int):
        return self._commands.imu_calib_start(src, dst, seq)
