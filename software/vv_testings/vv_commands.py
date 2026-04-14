from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Iterable

import protocol_pb2 as pb

from vv_transport import HostTransport


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
        pkt.ranging_start.reset_filter_state = True
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

    def filter_cfg_get(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.filter_cfg_get.dummy = 0
        return pkt

    def filter_cfg_set(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        cfg = pkt.filter_cfg_set.filter_cfg
        cfg.mode = pb.FILTER_MODE_KALMAN
        cfg.q_process_noise = 0.1
        cfg.r_base = 0.1
        cfg.innovation_alpha = 0.3
        cfg.r_scale_min = 0.8
        cfg.r_scale_max = 1.2
        return pkt

    def filter_cfg_resp(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        cfg = pkt.filter_cfg_resp.filter_cfg
        cfg.mode = pb.FILTER_MODE_KALMAN
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
        pkt.ble_adv_status.local_timestamp_ms = int(time.time() * 1000) & 0xFFFFFFFF
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

    # ── FOTA commands ─────────────────────────────────────────────────────────

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
            CommandSpec(9, "sys_config_get", self.factory.sys_config_get),
            CommandSpec(10, "sys_config_set", self.factory.sys_config_set),
            CommandSpec(11, "sys_config_resp", self.factory.sys_config_resp),
            CommandSpec(12, "sys_ranging_cfg_get", self.factory.sys_ranging_cfg_get),
            CommandSpec(13, "sys_ranging_cfg_set", self.factory.sys_ranging_cfg_set),
            CommandSpec(14, "sys_ranging_cfg_resp", self.factory.sys_ranging_cfg_resp),
            CommandSpec(15, "ranging_start", self.factory.ranging_start),
            CommandSpec(16, "ranging_stop", self.factory.ranging_stop),
            CommandSpec(17, "ranging_result", self.factory.ranging_result),
            CommandSpec(18, "ranging_status_get", self.factory.ranging_status_get),
            CommandSpec(19, "ranging_status_resp", self.factory.ranging_status_resp),
            CommandSpec(20, "filter_cfg_get", self.factory.filter_cfg_get),
            CommandSpec(21, "filter_cfg_set", self.factory.filter_cfg_set),
            CommandSpec(22, "filter_cfg_resp", self.factory.filter_cfg_resp),
            CommandSpec(23, "device_reset", self.factory.device_reset),
            CommandSpec(24, "uwb_reset", self.factory.uwb_reset),
            CommandSpec(25, "factory_config_reset", self.factory.factory_config_reset),
            CommandSpec(26, "device_type_set", self.factory.device_type_set),
            CommandSpec(27, "device_type_get", self.factory.device_type_get),
            CommandSpec(28, "flash_erase", self.factory.flash_erase),
            CommandSpec(29, "flash_read", self.factory.flash_read),
            CommandSpec(30, "flash_data", self.factory.flash_data),
            CommandSpec(31, "flash_write", self.factory.flash_write),
            CommandSpec(32, "ble_enable", self.factory.ble_enable),
            CommandSpec(33, "ble_status_get", self.factory.ble_status_get),
            CommandSpec(34, "ble_status_resp", self.factory.ble_status_resp),
            CommandSpec(35, "ble_adv_status", self.factory.ble_adv_status),
            CommandSpec(36, "log_data", self.factory.log_data),
            CommandSpec(37, "log_clear", self.factory.log_clear),
            CommandSpec(38, "host_transport_set", self.factory.host_transport_set),
            CommandSpec(39, "pos_calib_cfg_get", self.factory.pos_calib_cfg_get),
            CommandSpec(40, "pos_calib_cfg_set", self.factory.pos_calib_cfg_set),
            CommandSpec(41, "pos_calib_cfg_resp", self.factory.pos_calib_cfg_resp),
            CommandSpec(42, "anchor_layout_get", self.factory.anchor_layout_get),
            CommandSpec(43, "anchor_layout_set", self.factory.anchor_layout_set),
            CommandSpec(44, "anchor_layout_resp", self.factory.anchor_layout_resp),
            # FOTA
            CommandSpec(62, "enter_to_bootloader", self.factory.enter_to_bootloader),
            CommandSpec(46, "flash_verify", self.factory.flash_verify),
            CommandSpec(57, "fota_state_resp", self.factory.fota_state_resp),
        ]

    def all(self) -> Iterable[CommandSpec]:
        return tuple(self._specs)

    def get(self, param_name: str) -> CommandSpec:
        for spec in self._specs:
            if spec.param_name == param_name:
                return spec
        raise KeyError(f"Unknown command: {param_name}")
