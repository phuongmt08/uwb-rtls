from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Iterable

from . import protocol_pb2 as pb

from .transport import HostTransport


PacketBuilder = Callable[[int, int, int], pb.packet_t]


@dataclass(frozen=True)
class CommandSpec:
    tag: int
    param_name: str
    builder: PacketBuilder


class CommandFactory:
    def __init__(self) -> None:
        self.pb = pb

    @staticmethod
    def _base(src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = pb.packet_t()
        pkt.hdr.addr.src = src
        pkt.hdr.addr.dst = dst
        pkt.hdr.seq = seq
        return pkt

    def none(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.none.dummy = 0
        return pkt

    def ack(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.ack.ack_seq = 0
        pkt.ack.response = pb.PACKET_ACK_RESPONSE_ACK
        return pkt

    def device_information_get(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.device_information_get.dummy = 0
        return pkt

    def device_information_resp(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.device_information_resp.device_type = pb.DEVICE_TYPE_ANCHOR
        pkt.device_information_resp.role = pb.DEVICE_ROLE_ANCHOR
        pkt.device_information_resp.serial_number = 1
        pkt.device_information_resp.hw_version = 1
        pkt.device_information_resp.uid = b"\x00\x01\x02\x03"
        return pkt

    def time_sync_get(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.time_sync_get.dummy = 0
        return pkt

    def time_sync_set(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.time_sync_set.unix_time_ms = int(time.time() * 1000)
        pkt.time_sync_set.timezone_offset = 7 * 60
        return pkt

    def time_sync_resp(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.time_sync_resp.unix_time_ms = int(time.time() * 1000)
        pkt.time_sync_resp.timezone_offset = 7 * 60
        return pkt

    def sys_config_get(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.sys_config_get.dummy = 0
        return pkt

    def sys_config_set(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        cfg = pkt.sys_config_set.config
        cfg.role = pb.DEVICE_ROLE_ANCHOR
        cfg.device_id = 1
        cfg.ranging_period_ms = 300
        cfg.rx_timeout_ms = 120
        cfg.uwb_channel = 5
        cfg.uwb_prf = 64
        cfg.uwb_data_rate = 6800
        cfg.uwb_preamble_code = 9
        cfg.tx_antenna_delay = 16436
        cfg.rx_antenna_delay = 16436
        cfg.tx_power = 0
        cfg.anchor_list = b""
        return pkt

    def sys_config_resp(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self.sys_config_set(src, dst, seq)
        cfg = pkt.sys_config_set.config
        out = self._base(src, dst, seq)
        out.sys_config_resp.config.CopyFrom(cfg)
        return out

    def sys_ranging_cfg_get(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.sys_ranging_cfg_get.dummy = 0
        return pkt

    def sys_ranging_cfg_set(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.sys_ranging_cfg_set.config.rx_timeout_ms = 120
        pkt.sys_ranging_cfg_set.config.ranging_period_ms = 300
        return pkt

    def sys_ranging_cfg_resp(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.sys_ranging_cfg_resp.config.rx_timeout_ms = 120
        pkt.sys_ranging_cfg_resp.config.ranging_period_ms = 300
        return pkt

    def ranging_start(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.ranging_start.dummy = 0
        return pkt

    def ranging_stop(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.ranging_stop.dummy = 0
        return pkt

    def ranging_result(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.ranging_result.pos_x_m = 0.1
        pkt.ranging_result.pos_y_m = 0.2
        pkt.ranging_result.pos_z_m = 0.0
        pkt.ranging_result.rms_error_m = 0.05
        pkt.ranging_result.timestamp_ms = int(time.time() * 1000) & 0xFFFFFFFF
        anchor = pkt.ranging_result.anchors.add()
        anchor.anchor_id = 1
        anchor.distance_mm = 1000
        anchor.rssi_dbm = -65
        return pkt

    def ranging_status_get(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.ranging_status_get.dummy = 0
        return pkt

    def ranging_status_resp(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.ranging_status_resp.ranging_period_ms = 300
        return pkt

    def sensor_fusion_cfg_get(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.sensor_fusion_cfg_get.dummy = 0
        return pkt

    def sensor_fusion_cfg_set(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        cfg = pkt.sensor_fusion_cfg_set.config
        cfg.mode = pb.FILTER_MODE_KALMAN
        cfg.q_process_noise = 0.1
        cfg.r_base = 0.1
        cfg.innovation_alpha = 0.3
        cfg.r_scale_min = 0.8
        cfg.r_scale_max = 1.2
        return pkt

    def sensor_fusion_cfg_resp(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        cfg = pkt.sensor_fusion_cfg_resp.config
        cfg.mode = pb.FILTER_MODE_KALMAN
        return pkt

    def sensor_fusion_result(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.sensor_fusion_result.ukf_x_m = 0.0
        pkt.sensor_fusion_result.ukf_y_m = 0.0
        pkt.sensor_fusion_result.ukf_yaw_deg = 0.0
        pkt.sensor_fusion_result.tril_x_m = 0.0
        pkt.sensor_fusion_result.tril_y_m = 0.0
        pkt.sensor_fusion_result.yaw_deg = 0.0
        pkt.sensor_fusion_result.error_count = 0
        pkt.sensor_fusion_result.timestamp_ms = 0
        return pkt

    def end_session(self, src: int, dst: int, seq: int, reason: int = 0) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.end_session.reason = reason
        return pkt

    def device_reset(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.device_reset.dummy = 0
        return pkt

    def uwb_reset(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.uwb_reset.dummy = 0
        return pkt

    def factory_config_reset(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.factory_config_reset.magic = 0xA55A55A5
        return pkt

    def device_type_set(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.device_type_set.device_type = pb.DEVICE_TYPE_ANCHOR
        return pkt

    def device_type_get(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.device_type_get.dummy = 0
        return pkt

    def flash_erase(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.flash_erase.partition_id = 1
        pkt.flash_erase.flash_addr_region = pb.FLASH_ADDR_REGION_APPLICATION
        return pkt

    def flash_read(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.flash_read.read_length = 16
        pkt.flash_read.address = 0
        return pkt

    def flash_data(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.flash_data.data = 0
        return pkt

    def flash_write(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.flash_write.address = 0
        pkt.flash_write.data = b"\x00\x01\x02\x03"
        return pkt

    def ble_enable(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.ble_enable.enable = True
        return pkt

    def ble_status_get(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.ble_status_get.dummy = 0
        return pkt

    def ble_status_resp(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.ble_status_resp.state = pb.BLE_STATE_IDLE
        pkt.ble_status_resp.rssi_dbm = -70
        return pkt

    def ble_adv_status(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.ble_adv_status.anchor_id = 1
        pkt.ble_adv_status.battery_level_pct = 88
        pkt.ble_adv_status.status_flags = 0
        pkt.ble_adv_status.warning_count = 0
        pkt.ble_adv_status.error_count = 0
        pkt.ble_adv_status.local_timestamp_s = int(time.time()) & 0xFFFFFFFF
        return pkt

    def log_data(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.log_data.type = pb.LOG_TYPE_DEVICE_LOG
        pkt.log_data.data = b"test-log"
        return pkt

    def log_clear(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.log_clear.type = pb.LOG_TYPE_DEVICE_LOG
        pkt.log_clear.offset = 0
        pkt.log_clear.length = 16
        return pkt

    def host_transport_set(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.host_transport_set.transport = int(HostTransport.USB)
        return pkt

    def pos_calib_cfg_get(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.pos_calib_cfg_get.dummy = 0
        return pkt

    def pos_calib_cfg_set(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        cfg = pkt.pos_calib_cfg_set.config
        cfg.enable_anchor_auto_calib = True
        cfg.enable_tag_auto_calib = True
        cfg.ref_distance_xy_m = 2.0
        cfg.tag_height_m = 1.0
        cfg.anchor_height_m = 2.5
        cfg.calib_anchor_id = 1
        cfg.samples = 10
        cfg.error_threshold_m = 0.3
        cfg.min_delta_step = 1
        cfg.max_rounds = 10
        cfg.max_std_m = 0.2
        return pkt

    def pos_calib_cfg_resp(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.pos_calib_cfg_resp.config.enable_anchor_auto_calib = True
        return pkt

    def anchor_layout_get(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.anchor_layout_get.dummy = 0
        return pkt

    def anchor_layout_set(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        anchor = pkt.anchor_layout_set.anchors.add()
        anchor.anchor_id = 1
        anchor.x_m = 0.0
        anchor.y_m = 0.0
        anchor.z_m = 2.5
        return pkt

    def anchor_layout_resp(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        anchor = pkt.anchor_layout_resp.anchors.add()
        anchor.anchor_id = 1
        return pkt

    def enter_to_bootloader(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.enter_to_bootloader.magic = 0xDEADB007
        return pkt

    def flash_verify(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.flash_verify.dummy = 0
        return pkt

    def fota_state_resp(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.fota_state_resp.state = pb.FOTA_STATE_IDLE
        return pkt

    # ── BLE central commands ──────────────────────────────────────────────────────────

    def ble_scan_start(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.ble_scan_start.duration_ms = 5000  # Default 5 secs
        pkt.ble_scan_start.interval_ms = 160
        pkt.ble_scan_start.window_ms = 80
        pkt.ble_scan_start.active_scanning = True
        return pkt

    def ble_scan_stop(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.ble_scan_stop.dummy = 0
        return pkt
        
    def ble_connect(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.ble_connect.mac_address = b"\x00\x11\x22\x33\x44\x55"
        return pkt

    def ble_disconnect(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.ble_disconnect.reason = 0
        return pkt

    def ble_conn_params_get(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.ble_conn_params_get.dummy = 0
        return pkt

    def ble_conn_params_set(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.ble_conn_params_set.params.min_interval_ms = 15
        pkt.ble_conn_params_set.params.max_interval_ms = 30
        pkt.ble_conn_params_set.params.slave_latency = 0
        pkt.ble_conn_params_set.params.sup_timeout_ms = 4000
        return pkt

    def ble_conn_params_resp(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.ble_conn_params_resp.params.min_interval_ms = 15
        pkt.ble_conn_params_resp.params.max_interval_ms = 30
        pkt.ble_conn_params_resp.params.slave_latency = 0
        pkt.ble_conn_params_resp.params.sup_timeout_ms = 4000
        return pkt
        
    def ble_scan_result(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.ble_scan_result.mac_address = b"\x00\x11\x22\x33\x44\x55"
        pkt.ble_scan_result.rssi_dbm = -50
        pkt.ble_scan_result.serial_number = 12345
        return pkt

    def battery_info_get(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.battery_info_get.dummy = 0
        return pkt

    def battery_info_resp(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.battery_info_resp.bat_voltage_mv = 3300
        pkt.battery_info_resp.bat_soc_percent = 100
        pkt.battery_info_resp.remaining_min = 120
        pkt.battery_info_resp.is_charging = False
        pkt.battery_info_resp.mcu_temp_c = 25.0
        pkt.battery_info_resp.mcu_voltage_mv = 3300
        pkt.battery_info_resp.uwb_temp_c = 25.0
        pkt.battery_info_resp.uwb_voltage_mv = 3300
        pkt.battery_info_resp.imu_temp_c = 25.0
        pkt.battery_info_resp.error_mask = 0
        return pkt


class CommandCatalog:
    def __init__(self, factory: CommandFactory | None = None) -> None:
        self.factory = factory or CommandFactory()
        self._specs = [
            CommandSpec(2, "none", self.factory.none),
            CommandSpec(3, "ack", self.factory.ack),
            CommandSpec(4, "device_information_get", self.factory.device_information_get),
            CommandSpec(5, "device_information_resp", self.factory.device_information_resp),
            CommandSpec(6, "time_sync_get", self.factory.time_sync_get),
            CommandSpec(7, "time_sync_set", self.factory.time_sync_set),
            CommandSpec(8, "time_sync_resp", self.factory.time_sync_resp),
            CommandSpec(10, "sys_config_get", self.factory.sys_config_get),
            CommandSpec(11, "sys_config_set", self.factory.sys_config_set),
            CommandSpec(12, "sys_config_resp", self.factory.sys_config_resp),
            CommandSpec(13, "sys_ranging_cfg_get", self.factory.sys_ranging_cfg_get),
            CommandSpec(14, "sys_ranging_cfg_set", self.factory.sys_ranging_cfg_set),
            CommandSpec(15, "sys_ranging_cfg_resp", self.factory.sys_ranging_cfg_resp),
            CommandSpec(16, "ranging_start", self.factory.ranging_start),
            CommandSpec(17, "ranging_stop", self.factory.ranging_stop),
            CommandSpec(18, "ranging_result", self.factory.ranging_result),
            CommandSpec(19, "ranging_status_get", self.factory.ranging_status_get),
            CommandSpec(20, "ranging_status_resp", self.factory.ranging_status_resp),
            CommandSpec(21, "sensor_fusion_cfg_get", self.factory.sensor_fusion_cfg_get),
            CommandSpec(22, "sensor_fusion_cfg_set", self.factory.sensor_fusion_cfg_set),
            CommandSpec(23, "sensor_fusion_cfg_resp", self.factory.sensor_fusion_cfg_resp),
            CommandSpec(24, "sensor_fusion_result", self.factory.sensor_fusion_result),
            CommandSpec(30, "device_reset", self.factory.device_reset),
            CommandSpec(31, "uwb_reset", self.factory.uwb_reset),
            CommandSpec(32, "factory_config_reset", self.factory.factory_config_reset),
            CommandSpec(33, "device_type_set", self.factory.device_type_set),
            CommandSpec(34, "device_type_get", self.factory.device_type_get),
            CommandSpec(35, "flash_erase", self.factory.flash_erase),
            CommandSpec(36, "flash_read", self.factory.flash_read),
            CommandSpec(37, "flash_data", self.factory.flash_data),
            CommandSpec(38, "flash_write", self.factory.flash_write),
            CommandSpec(39, "ble_enable", self.factory.ble_enable),
            CommandSpec(40, "ble_status_get", self.factory.ble_status_get),
            CommandSpec(41, "ble_status_resp", self.factory.ble_status_resp),
            CommandSpec(42, "ble_adv_status", self.factory.ble_adv_status),
            CommandSpec(43, "log_data", self.factory.log_data),
            CommandSpec(44, "log_clear", self.factory.log_clear),
            CommandSpec(45, "host_transport_set", self.factory.host_transport_set),
            CommandSpec(46, "pos_calib_cfg_get", self.factory.pos_calib_cfg_get),
            CommandSpec(47, "pos_calib_cfg_set", self.factory.pos_calib_cfg_set),
            CommandSpec(48, "pos_calib_cfg_resp", self.factory.pos_calib_cfg_resp),
            CommandSpec(49, "anchor_layout_get", self.factory.anchor_layout_get),
            CommandSpec(50, "anchor_layout_set", self.factory.anchor_layout_set),
            CommandSpec(51, "anchor_layout_resp", self.factory.anchor_layout_resp),
            CommandSpec(53, "ble_conn_params_get", self.factory.ble_conn_params_get),
            CommandSpec(54, "ble_conn_params_set", self.factory.ble_conn_params_set),
            CommandSpec(55, "ble_conn_params_resp", self.factory.ble_conn_params_resp),
            CommandSpec(56, "ble_disconnect", self.factory.ble_disconnect),
            CommandSpec(57, "ble_scan_start", self.factory.ble_scan_start),
            CommandSpec(58, "ble_scan_stop", self.factory.ble_scan_stop),
            CommandSpec(59, "ble_connect", self.factory.ble_connect),
            CommandSpec(60, "ble_scan_result", self.factory.ble_scan_result),
            # FOTA
            CommandSpec(64, "enter_to_bootloader", self.factory.enter_to_bootloader),
            CommandSpec(52, "flash_verify", self.factory.flash_verify),
            CommandSpec(61, "fota_state_resp", self.factory.fota_state_resp),
            CommandSpec(67, "end_session", self.factory.end_session),
            # Power Management
            CommandSpec(62, "battery_info_resp", self.factory.battery_info_resp),
            CommandSpec(63, "battery_info_get", self.factory.battery_info_get),
        ]

    def all(self) -> Iterable[CommandSpec]:
        return tuple(self._specs)

    def get(self, param_name: str) -> CommandSpec:
        for spec in self._specs:
            if spec.param_name == param_name:
                return spec
        raise KeyError(f"Unknown command: {param_name}")
