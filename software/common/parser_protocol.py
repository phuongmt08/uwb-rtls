from __future__ import annotations

from .commands import CommandFactory
from .transport import HdlcChunk, HdlcCodec, HostTransport, VvAddress, VvProtocol as _VvProtocol


class VvProtocol(_VvProtocol):
    def __init__(self) -> None:
        super().__init__()
        self._commands = CommandFactory()

    # Host-side wrapper methods with explicit signatures.
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
    def build_time_sync_set(self, src: int, dst: int, seq: int, unix_time_ms: int | None = None, timezone_offset: int = 7 * 60):
        return self._commands.time_sync_set(src, dst, seq, unix_time_ms=unix_time_ms, timezone_offset=timezone_offset)
    def build_time_sync_resp(self, src: int, dst: int, seq: int):
        return self._commands.time_sync_resp(src, dst, seq)
    def build_time_sync_adv_set(self, src: int, dst: int, seq: int, device_type: int | None = None, device_id: int = 1, unix_time_ms: int | None = None, timezone_offset: int = 7 * 60):
        return self._commands.time_sync_adv_set(src, dst, seq, device_type=device_type, device_id=device_id, unix_time_ms=unix_time_ms, timezone_offset=timezone_offset)
    def build_sys_config_get(self, src: int, dst: int, seq: int):
        return self._commands.sys_config_get(src, dst, seq)
    def build_sys_config_set(
        self,
        src: int,
        dst: int,
        seq: int,
        role: int | None = None,
        device_id: int = 1,
        ranging_period_ms: int = 300,
        rx_timeout_ms: int = 120,
        uwb_channel: int = 5,
        uwb_prf: int = 64,
        uwb_data_rate: int = 2,
        uwb_preamble_code: int = 9,
        tx_antenna_delay: int = 16436,
        rx_antenna_delay: int = 16436,
        tx_power: int = 0,
        anchor_list: bytes = b"",
        power_mode: int = 3,
        uwb_preamble_len: int = 0x34,
        uwb_rx_pac: int = 2,
        uwb_ns_sfd: int = 1,
        uwb_phr_mode: int = 0,
        smart_tx_power: bool = True,
        pg_delay: int = 0xC2,
    ):
        return self._commands.sys_config_set(
            src, dst, seq,
            role=role, device_id=device_id, ranging_period_ms=ranging_period_ms, rx_timeout_ms=rx_timeout_ms,
            uwb_channel=uwb_channel, uwb_prf=uwb_prf, uwb_data_rate=uwb_data_rate, uwb_preamble_code=uwb_preamble_code,
            tx_antenna_delay=tx_antenna_delay, rx_antenna_delay=rx_antenna_delay, tx_power=tx_power,
            anchor_list=anchor_list, power_mode=power_mode, uwb_preamble_len=uwb_preamble_len,
            uwb_rx_pac=uwb_rx_pac, uwb_ns_sfd=uwb_ns_sfd, uwb_phr_mode=uwb_phr_mode,
            smart_tx_power=smart_tx_power, pg_delay=pg_delay,
        )
    def build_sys_config_resp(self, src: int, dst: int, seq: int):
        return self._commands.sys_config_resp(src, dst, seq)
    def build_sys_ranging_cfg_get(self, src: int, dst: int, seq: int):
        return self._commands.sys_ranging_cfg_get(src, dst, seq)
    def build_sys_ranging_cfg_set(self, src: int, dst: int, seq: int, period_ms: int | None = None, timeout_ms: int | None = None):
        return self._commands.sys_ranging_cfg_set(src, dst, seq, period_ms=period_ms, timeout_ms=timeout_ms)
    def build_sys_ranging_cfg_resp(self, src: int, dst: int, seq: int):
        return self._commands.sys_ranging_cfg_resp(src, dst, seq)
    def build_ranging_start(
        self,
        src: int,
        dst: int,
        seq: int,
        yaw_deg: int | float = 0,
        is_ukf_reinit: bool = False,
    ):
        return self._commands.ranging_start(
            src,
            dst,
            seq,
            yaw_deg=yaw_deg,
            is_ukf_reinit=is_ukf_reinit,
        )
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
    def build_sensor_fusion_cfg_set(
        self,
        src: int,
        dst: int,
        seq: int,
        enable_fusion: bool = True,
        mode: int = 0,
        process_noise_accel: float = 0.1,
        process_noise_gyro: float = 0.01,
        measurement_noise_uwb: float = 0.05,
        imu_sample_rate_hz: int = 100,
    ):
        return self._commands.sensor_fusion_cfg_set(
            src, dst, seq,
            enable_fusion=enable_fusion, mode=mode, process_noise_accel=process_noise_accel,
            process_noise_gyro=process_noise_gyro, measurement_noise_uwb=measurement_noise_uwb,
            imu_sample_rate_hz=imu_sample_rate_hz,
        )
    def build_sensor_fusion_cfg_resp(self, src: int, dst: int, seq: int):
        return self._commands.sensor_fusion_cfg_resp(src, dst, seq)
    def build_prefilter_cfg_get(self, src: int, dst: int, seq: int):
        return self._commands.prefilter_cfg_get(src, dst, seq)
    def build_prefilter_cfg_set(
        self,
        src: int,
        dst: int,
        seq: int,
        enable: bool = True,
        recover_d2: float = 0.25,
        reject_d2: float = 4.0,
        r_base: float = 0.1,
        r_gate: float = 0.5,
        velocity_weight: float = 0.2,
        min_covariance: float = 0.01,
    ):
        return self._commands.prefilter_cfg_set(
            src, dst, seq,
            enable=enable,
            recover_d2=recover_d2,
            reject_d2=reject_d2,
            r_base=r_base,
            r_gate=r_gate,
            velocity_weight=velocity_weight,
            min_covariance=min_covariance,
        )
    def build_prefilter_cfg_resp(self, src: int, dst: int, seq: int):
        return self._commands.prefilter_cfg_resp(src, dst, seq)
    def build_device_reset(self, src: int, dst: int, seq: int):
        return self._commands.device_reset(src, dst, seq)
    def build_uwb_reset(self, src: int, dst: int, seq: int):
        return self._commands.uwb_reset(src, dst, seq)
    def build_factory_config_reset(self, src: int, dst: int, seq: int):
        return self._commands.factory_config_reset(src, dst, seq)
    def build_device_type_set(self, src: int, dst: int, seq: int, device_type: int | None = None):
        return self._commands.device_type_set(src, dst, seq, device_type=device_type)
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
    def build_ble_adv_config_set(self, src: int, dst: int, seq: int, enable: bool = True, serial_number: int = 0, device_name: str = ""):
        return self._commands.ble_adv_config_set(src, dst, seq, enable=enable, serial_number=serial_number, device_name=device_name)
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
    def build_pos_calib_cfg_set(
        self,
        src: int,
        dst: int,
        seq: int,
        enable_anchor_auto_calib: bool = True,
        enable_tag_auto_calib: bool = True,
        ref_distance_xy_m: float = 2.0,
        tag_height_m: float = 1.0,
        anchor_height_m: float = 2.5,
        calib_anchor_id: int = 1,
        samples: int = 10,
        error_threshold_m: float = 0.3,
        min_delta_step: int = 1,
        max_rounds: int = 10,
        max_std_m: float = 0.2,
        damping: float = 0.1,
        iterations: int = 100,
    ):
        return self._commands.pos_calib_cfg_set(
            src, dst, seq,
            enable_anchor_auto_calib=enable_anchor_auto_calib, enable_tag_auto_calib=enable_tag_auto_calib,
            ref_distance_xy_m=ref_distance_xy_m, tag_height_m=tag_height_m, anchor_height_m=anchor_height_m,
            calib_anchor_id=calib_anchor_id, samples=samples, error_threshold_m=error_threshold_m,
            min_delta_step=min_delta_step, max_rounds=max_rounds, max_std_m=max_std_m,
            damping=damping, iterations=iterations,
        )
    def build_pos_calib_cfg_resp(self, src: int, dst: int, seq: int):
        return self._commands.pos_calib_cfg_resp(src, dst, seq)
    def build_anchor_layout_get(self, src: int, dst: int, seq: int):
        return self._commands.anchor_layout_get(src, dst, seq)
    def build_anchor_layout_set(self, src: int, dst: int, seq: int, anchors: list | None = None):
        return self._commands.anchor_layout_set(src, dst, seq, anchors)
    def build_anchor_layout_resp(self, src: int, dst: int, seq: int):
        return self._commands.anchor_layout_resp(src, dst, seq)
    def build_flash_verify(self, src: int, dst: int, seq: int):
        return self._commands.flash_verify(src, dst, seq)
    def build_ble_conn_params_get(self, src: int, dst: int, seq: int):
        return self._commands.ble_conn_params_get(src, dst, seq)
    def build_ble_conn_params_set(
        self,
        src: int,
        dst: int,
        seq: int,
        min_interval_ms: int = 20,
        max_interval_ms: int = 40,
        slave_latency: int = 0,
        sup_timeout_ms: int = 3000,
    ):
        return self._commands.ble_conn_params_set(
            src, dst, seq,
            min_interval_ms=min_interval_ms, max_interval_ms=max_interval_ms,
            slave_latency=slave_latency, sup_timeout_ms=sup_timeout_ms,
        )
    def build_ble_conn_params_resp(self, src: int, dst: int, seq: int):
        return self._commands.ble_conn_params_resp(src, dst, seq)
    def build_ble_disconnect(self, src: int, dst: int, seq: int, reason: int = 0):
        return self._commands.ble_disconnect(src, dst, seq, reason=reason)
    def build_ble_scan_start(
        self,
        src: int,
        dst: int,
        seq: int,
        duration_ms: int = 5000,
        interval_ms: int = 160,
        window_ms: int = 80,
        active_scanning: bool = True,
    ):
        return self._commands.ble_scan_start(
            src, dst, seq,
            duration_ms=duration_ms, interval_ms=interval_ms, window_ms=window_ms, active_scanning=active_scanning,
        )
    def build_ble_scan_stop(self, src: int, dst: int, seq: int):
        return self._commands.ble_scan_stop(src, dst, seq)
    def build_ble_connect(self, src: int, dst: int, seq: int, mac_address: bytes = b"\x00\x11\x22\x33\x44\x55"):
        return self._commands.ble_connect(src, dst, seq, mac_address=mac_address)
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
    def build_calib_start(
        self,
        src: int,
        dst: int,
        seq: int,
        sample_target: int = 32,
        tag_x_m: float = 2.0,
        tag_y_m: float = 2.0,
        tag_z_m: float = 1.0,
        reference_position_valid: bool = True,
    ):
        return self._commands.calib_start(
            src,
            dst,
            seq,
            sample_target=sample_target,
            tag_x_m=tag_x_m,
            tag_y_m=tag_y_m,
            tag_z_m=tag_z_m,
            reference_position_valid=reference_position_valid,
        )
    def build_calib_stop(self, src: int, dst: int, seq: int):
        return self._commands.calib_stop(src, dst, seq)
    def build_calib_candidate_apply(self, src: int, dst: int, seq: int, anchor_mask: int = 0xF):
        return self._commands.calib_candidate_apply(src, dst, seq, anchor_mask=anchor_mask)
    def build_end_session(self, src: int, dst: int, seq: int, reason: int = 0):
        return self._commands.end_session(src, dst, seq, reason=reason)
    def build_factory_otp_write(
        self,
        src: int,
        dst: int,
        seq: int,
        confirm_magic: int = 0x4F545057,
        otp_type: int = 0,
        device_type: int = 2,
        tx_antenna_delay: int = 0,
        rx_antenna_delay: int = 0,
        value_u32: int = 0,
        value_u8: int = 0,
    ):
        return self._commands.factory_otp_write(
            src,
            dst,
            seq,
            confirm_magic=confirm_magic,
            otp_type=otp_type,
            device_type=device_type,
            tx_antenna_delay=tx_antenna_delay,
            rx_antenna_delay=rx_antenna_delay,
            value_u32=value_u32,
            value_u8=value_u8,
        )
    def build_imu_reset(self, src: int, dst: int, seq: int):
        return self._commands.imu_reset(src, dst, seq)
    def build_imu_calib_start(self, src: int, dst: int, seq: int):
        return self._commands.imu_calib_start(src, dst, seq)
    def build_rtos_resource_get(self, src: int, dst: int, seq: int):
        return self._commands.rtos_resource_get(src, dst, seq)
    def build_rtos_resource_resp(self, src: int, dst: int, seq: int):
        return self._commands.rtos_resource_resp(src, dst, seq)
    def build_rtos_task_stats_get(self, src: int, dst: int, seq: int):
        return self._commands.rtos_task_stats_get(src, dst, seq)
    def build_rtos_task_stats_resp(self, src: int, dst: int, seq: int):
        return self._commands.rtos_task_stats_resp(src, dst, seq)
