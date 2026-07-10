from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Iterable

from . import protocol_pb2 as pb

from .transport import HostTransport, VvAddress


PacketBuilder = Callable[[int, int, int], pb.packet_t]

DEFAULT_ANCHOR_LAYOUT = (
    (1, 0.0, 0.0, 0.895),
    (2, 11.76, 0.0, 0.895),
    (3, 0.0, 14.2, 0.895),
    (4, 11.76, 14.2, 0.895),
)


@dataclass(frozen=True)
class CommandSpec:
    tag: int
    param_name: str
    builder: PacketBuilder
    expected_response: str = ""

# Host-side routing hints only.
# These sets help the app choose a sensible default destination address.
# They do not change the firmware protocol or protobuf wire format.
_MCU_COMMANDS = {
    "device_information_get",
    "time_sync_get",
    "time_sync_set",
    "time_sync_adv_set",
    "sys_config_get",
    "sys_config_set",
    "sys_ranging_cfg_get",
    "sys_ranging_cfg_set",
    "ranging_start",
    "ranging_stop",
    "ranging_status_get",
    "sensor_fusion_cfg_get",
    "sensor_fusion_cfg_set",
    "imu_reset",
    "imu_calib_start",
    "device_reset",
    "uwb_reset",
    "factory_config_reset",
    "device_type_set",
    "device_type_get",
    "flash_erase",
    "flash_read",
    "flash_write",
    "flash_verify",
    "log_clear",
    "host_transport_set",
    "pos_calib_cfg_get",
    "pos_calib_cfg_set",
    "anchor_layout_get",
    "anchor_layout_set",
    "battery_info_get",
    "enter_to_bootloader",
    "calib_status_get",
    "factory_otp_write",
    "rtos_resource_get",
    "rtos_task_stats_get",
    "prefilter_cfg_get",
    "prefilter_cfg_set",
    "end_session",
    "log_data",
    "zone_switch",
    "zone_profile_set",
    "zone_profile_get",
    "calib_start",
    "calib_stop",
    "calib_candidate_apply",
}

_CENTRAL_COMMANDS = {
    "ble_status_get",
    "ble_conn_params_get",
    "ble_conn_params_set",
    "ble_disconnect",
    "ble_scan_start",
    "ble_scan_stop",
    "ble_connect",
}

_PERIPHERAL_COMMANDS = {
    "ble_adv_config_set",
}

_VEHICLE_COMMANDS = {
    "vehicle_control",
}


def mapped_destination_for(command_name: str) -> int | None:
    """Return the explicit host-side destination hint for a command name, if known."""
    if command_name in _MCU_COMMANDS:
        return int(VvAddress.MCU)
    if command_name in _PERIPHERAL_COMMANDS:
        return int(VvAddress.PERIPHERAL)
    if command_name in _CENTRAL_COMMANDS:
        return int(VvAddress.CENTRAL)
    if command_name in _VEHICLE_COMMANDS:
        return int(VvAddress.VEHICLE)
    return None


def default_destination_for(command_name: str) -> int:
    """Return the host-side default destination for a command name."""
    mapped = mapped_destination_for(command_name)
    if mapped is not None:
        return mapped
    return int(VvAddress.CENTRAL)


class CommandFactory:
    def __init__(self) -> None:
        self.pb = pb
        # Mock/default identity for test and simulation helpers only.
        # Real device identity should come from the actual device response fields.
        self.default_device_type = pb.DEVICE_TYPE_ANCHOR
        self.default_device_role = pb.DEVICE_ROLE_ANCHOR

    def set_device_identity(self, device_type: int, role: int | None = None) -> None:
        """Update the mock/default identity used by packet builders."""
        self.default_device_type = device_type
        if role is None:
            if device_type == pb.DEVICE_TYPE_TAG:
                role = pb.DEVICE_ROLE_TAG
            elif device_type == pb.DEVICE_TYPE_ANCHOR:
                role = pb.DEVICE_ROLE_ANCHOR
            else:
                role = pb.DEVICE_ROLE_UNSPECIFIED
        self.default_device_role = role

    def _resolve_device_identity(
        self,
        device_type: int | None = None,
        role: int | None = None,
    ) -> tuple[int, int]:
        resolved_type = self.default_device_type if device_type is None else device_type
        if role is not None:
            resolved_role = role
        elif resolved_type == pb.DEVICE_TYPE_TAG:
            resolved_role = pb.DEVICE_ROLE_TAG
        elif resolved_type == pb.DEVICE_TYPE_ANCHOR:
            resolved_role = pb.DEVICE_ROLE_ANCHOR
        else:
            resolved_role = self.default_device_role
        return resolved_type, resolved_role

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

    def device_information_resp(
        self,
        src: int,
        dst: int,
        seq: int,
        device_type: int | None = None,
        role: int | None = None,
    ) -> pb.packet_t:
        # Test/mock helper: when no device payload is available, synthesize a
        # realistic response using the current default identity.
        pkt = self._base(src, dst, seq)
        resolved_type, resolved_role = self._resolve_device_identity(device_type, role)
        pkt.device_information_resp.device_type = resolved_type
        pkt.device_information_resp.role = resolved_role
        pkt.device_information_resp.serial_number = 1
        pkt.device_information_resp.hw_version = 1
        pkt.device_information_resp.uid = b"\x00\x01\x02\x03"
        return pkt

    def time_sync_get(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.time_sync_get.dummy = 0
        return pkt

    def time_sync_set(self, src: int, dst: int, seq: int,
        unix_time_ms: int | None = None,
        timezone_offset: int = 420) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.time_sync_set.unix_time_ms = unix_time_ms if unix_time_ms is not None else int(time.time() * 1000)
        pkt.time_sync_set.timezone_offset = timezone_offset
        return pkt

    def time_sync_adv_set(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.time_sync_adv_set.device_type = pb.DEVICE_TYPE_ANCHOR
        pkt.time_sync_adv_set.device_id = 1
        pkt.time_sync_adv_set.unix_time_ms = int(time.time() * 1000)
        pkt.time_sync_adv_set.timezone_offset = 7 * 60
        return pkt

    def time_sync_resp(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.time_sync_resp.unix_time_ms = int(time.time() * 1000)
        pkt.time_sync_resp.timezone_offset = 7 * 60
        return pkt

    def time_sync_adv_set(
        self,
        src: int,
        dst: int,
        seq: int,
        device_type: int | None = None,
        device_id: int = 1,
        unix_time_ms: int | None = None,
        timezone_offset: int = 7 * 60,
    ) -> pb.packet_t:
        # Test/mock helper: real devices should provide their own identity.
        pkt = self._base(src, dst, seq)
        pkt.time_sync_adv_set.device_type = self.default_device_type if device_type is None else device_type
        pkt.time_sync_adv_set.device_id = device_id
        pkt.time_sync_adv_set.unix_time_ms = unix_time_ms if unix_time_ms is not None else int(time.time() * 1000)
        pkt.time_sync_adv_set.timezone_offset = timezone_offset
        return pkt

    def sys_config_get(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.sys_config_get.dummy = 0
        return pkt

    def sys_config_set(self, src: int, dst: int, seq: int,
                       role: int | None = None,
                       device_id: int = 1,
                       ranging_period_ms: int = 300,
                       rx_timeout_ms: int = 120,
                       uwb_channel: int = 5,
                       uwb_prf: int = 64,
                       uwb_data_rate: int = 6800,
                       uwb_preamble_code: int = 9,
                       tx_antenna_delay: int = 16436,
                       rx_antenna_delay: int = 16436,
                       tx_power: int = 0,
                       anchor_list: bytes = b"",
                       power_mode: int = pb.ANCHOR_POWER_MODE_PERFORMANCE,
                       uwb_preamble_len: int = 0,
                       uwb_rx_pac: int = 0,
                       uwb_ns_sfd: int = 0,
                       uwb_phr_mode: int = 0,
                       smart_tx_power: bool = False,
                       pg_delay: int = 0) -> pb.packet_t:
        # Test/mock helper: defaults are only for simulation and fixtures.
        pkt = self._base(src, dst, seq)
        resolved_role = self.default_device_role if role is None else role
        cfg = pkt.sys_config_set.config
        cfg.role = resolved_role
        cfg.device_id = device_id
        cfg.ranging_period_ms = ranging_period_ms
        cfg.rx_timeout_ms = rx_timeout_ms
        cfg.uwb_channel = uwb_channel
        cfg.uwb_prf = uwb_prf
        cfg.uwb_data_rate = uwb_data_rate
        cfg.uwb_preamble_code = uwb_preamble_code
        cfg.tx_antenna_delay = tx_antenna_delay
        cfg.rx_antenna_delay = rx_antenna_delay
        cfg.tx_power = tx_power
        cfg.anchor_list = anchor_list
        cfg.power_mode = power_mode
        cfg.uwb_preamble_len = uwb_preamble_len
        cfg.uwb_rx_pac = uwb_rx_pac
        cfg.uwb_ns_sfd = uwb_ns_sfd
        cfg.uwb_phr_mode = uwb_phr_mode
        cfg.smart_tx_power = smart_tx_power
        cfg.pg_delay = pg_delay
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

    def sys_ranging_cfg_set(self, src: int, dst: int, seq: int, period_ms: int | None = None, timeout_ms: int | None = None) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.sys_ranging_cfg_set.config.rx_timeout_ms = timeout_ms if timeout_ms is not None else 120
        pkt.sys_ranging_cfg_set.config.ranging_period_ms = period_ms if period_ms is not None else 300
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
        anchor.fp_amp = 500
        return pkt

    def ranging_status_get(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.ranging_status_get.dummy = 0
        return pkt

    def ranging_status_resp(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.ranging_status_resp.ranging_period_ms = 300
        pkt.ranging_status_resp.ranging_total_count = 0
        pkt.ranging_status_resp.ranging_success_count = 0
        pkt.ranging_status_resp.ranging_failed_count = 0
        pkt.ranging_status_resp.ranging_timeout_count = 0
        pkt.ranging_status_resp.last_ranging_time_ms = 0
        pkt.ranging_status_resp.last_rms_error_m = 0.0
        pkt.ranging_status_resp.last_avg_rssi_dbm = 0
        pkt.ranging_status_resp.last_update_timestamp_ms = 0
        return pkt

    def sensor_fusion_cfg_get(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.sensor_fusion_cfg_get.dummy = 0
        return pkt

    def sensor_fusion_cfg_set(self, src: int, dst: int, seq: int,
                              alpha: float = 1e-3,
                              kappa: float = 0.0,
                              beta: float = 2.0,
                              q_a: float = 0.1,
                              q_g: float = 0.01,
                              r_uwb: float = 0.1,
                              init_p_px: float = 1.0,
                              init_p_py: float = 1.0,
                              init_p_vx: float = 0.1,
                              init_p_vy: float = 0.1,
                              init_p_theta: float = 0.1,
                              init_p_bias_ax: float = 0.01,
                              init_p_bias_ay: float = 0.01,
                              init_p_bias_gz: float = 0.01) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        cfg = pkt.sensor_fusion_cfg_set.config
        cfg.alpha = alpha
        cfg.kappa = kappa
        cfg.beta = beta
        cfg.q_a = q_a
        cfg.q_g = q_g
        cfg.r_uwb = r_uwb
        cfg.init_p_px = init_p_px
        cfg.init_p_py = init_p_py
        cfg.init_p_vx = init_p_vx
        cfg.init_p_vy = init_p_vy
        cfg.init_p_theta = init_p_theta
        cfg.init_p_bias_ax = init_p_bias_ax
        cfg.init_p_bias_ay = init_p_bias_ay
        cfg.init_p_bias_gz = init_p_bias_gz
        return pkt

    def sensor_fusion_cfg_resp(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        self._fill_default_sensor_fusion_cfg(pkt.sensor_fusion_cfg_resp.config)
        return pkt

    @staticmethod
    def _fill_default_sensor_fusion_cfg(cfg: pb.sensor_fusion_cfg_t) -> None:
        cfg.alpha = 0.001
        cfg.kappa = 0.0
        cfg.beta = 2.0
        cfg.q_a = 0.2
        cfg.q_g = 0.01
        cfg.r_uwb = 0.15
        cfg.init_p_px = 0.1
        cfg.init_p_py = 0.1
        cfg.init_p_vx = 0.1
        cfg.init_p_vy = 0.1
        cfg.init_p_theta = 1.0e-6
        cfg.init_p_bias_ax = 1.0e-5
        cfg.init_p_bias_ay = 1.0e-5
        cfg.init_p_bias_gz = 1.0e-6

    def sensor_fusion_result(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.sensor_fusion_result.ukf_x_m = 0
        pkt.sensor_fusion_result.ukf_y_m = 0
        pkt.sensor_fusion_result.ukf_yaw_deg = 0
        pkt.sensor_fusion_result.tril_x_m = 0
        pkt.sensor_fusion_result.tril_y_m = 0
        pkt.sensor_fusion_result.yaw_deg = 0
        pkt.sensor_fusion_result.anchor_mask = 0
        pkt.sensor_fusion_result.ranging_error_count = 0
        pkt.sensor_fusion_result.timestamp_ms = 0
        pkt.sensor_fusion_result.zone_id = 0
        return pkt

    def imu_reset(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.imu_reset.dummy = 0
        return pkt

    def imu_calib_start(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.imu_calib_start.dummy = 0
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

    def device_type_set(self, src: int, dst: int, seq: int, device_type: int | None = None) -> pb.packet_t:
        # Test/mock helper: lets the simulator express TAG/ANCHOR identity.
        pkt = self._base(src, dst, seq)
        pkt.device_type_set.device_type = self.default_device_type if device_type is None else device_type
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
        pkt.flash_data.data = b""
        return pkt

    def flash_write(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.flash_write.address = 0
        pkt.flash_write.data = b"\x00\x01\x02\x03"
        return pkt

    def ble_adv_config_set(
        self,
        src: int,
        dst: int,
        seq: int,
        enable: bool = True,
        serial_number: int = 0,
        device_name: str = "",
    ) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.ble_adv_config_set.enable = bool(enable)
        pkt.ble_adv_config_set.serial_number = int(serial_number)
        pkt.ble_adv_config_set.device_name = str(device_name or "")
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

    def ble_adv_status(self, src: int, dst: int, seq: int, device_type: int | None = None) -> pb.packet_t:
        # Test/mock helper: advertising status should match the simulated device.
        pkt = self._base(src, dst, seq)
        pkt.ble_adv_status.device = self.default_device_type if device_type is None else device_type
        pkt.ble_adv_status.device_id = 1
        pkt.ble_adv_status.bat_soc_percent = 88
        pkt.ble_adv_status.status_flags = 0
        pkt.ble_adv_status.warning_count = 0
        pkt.ble_adv_status.error_count = 0
        pkt.ble_adv_status.local_timestamp_s = int(time.time()) & 0xFFFFFFFF
        return pkt

    def log_data(
        self,
        src: int,
        dst: int,
        seq: int,
        log_type: int = pb.LOG_TYPE_DEVICE_LOG,
        data: bytes = b"",
    ) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.log_data.type = log_type
        pkt.log_data.data = data or b""
        return pkt

    def log_clear(
        self,
        src: int,
        dst: int,
        seq: int,
        log_type: int = pb.LOG_TYPE_DEVICE_LOG,
        offset: int = 0,
        length: int = 0,
    ) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.log_clear.type = log_type
        pkt.log_clear.offset = offset
        pkt.log_clear.length = length
        return pkt

    def host_transport_set(
        self,
        src: int,
        dst: int,
        seq: int,
        transport: int = int(HostTransport.USB),
    ) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.host_transport_set.transport = int(transport)
        return pkt
    def pos_calib_cfg_get(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.pos_calib_cfg_get.dummy = 0
        return pkt

    def pos_calib_cfg_set(self, src: int, dst: int, seq: int,
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
                          iterations: int = 100) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        cfg = pkt.pos_calib_cfg_set.config
        cfg.enable_anchor_auto_calib = enable_anchor_auto_calib
        cfg.enable_tag_auto_calib = enable_tag_auto_calib
        cfg.ref_distance_xy_m = ref_distance_xy_m
        cfg.tag_height_m = tag_height_m
        cfg.anchor_height_m = anchor_height_m
        cfg.calib_anchor_id = calib_anchor_id
        cfg.samples = samples
        cfg.error_threshold_m = error_threshold_m
        cfg.min_delta_step = min_delta_step
        cfg.max_rounds = max_rounds
        cfg.max_std_m = max_std_m
        cfg.damping = damping
        cfg.iterations = iterations
        return pkt

    def pos_calib_cfg_resp(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.pos_calib_cfg_resp.config.enable_anchor_auto_calib = True
        return pkt

    def prefilter_cfg_get(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.prefilter_cfg_get.dummy = 0
        return pkt

    @staticmethod
    def _fill_default_prefilter(cfg: pb.prefilter_cfg_t) -> None:
        cfg.enable = True
        cfg.recover_d2 = 5.0
        cfg.reject_d2 = 7.5
        cfg.r_base = 0.05
        cfg.r_gate = 0.10
        cfg.velocity_weight = 0.5
        cfg.min_covariance = 1.0e-6

    def prefilter_cfg_set(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        self._fill_default_prefilter(pkt.prefilter_cfg_set.config)
        return pkt

    def prefilter_cfg_resp(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        self._fill_default_prefilter(pkt.prefilter_cfg_resp.config)
        return pkt

    def vehicle_control_speed_steering(
        self,
        src: int,
        dst: int,
        seq: int,
        speed_mps: float = 0.0,
        steering_angle_rad: float = 0.0,
        valid_for_ms: int = 100,
        emergency_stop: bool = False,
    ) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.vehicle_control.command_seq = seq
        pkt.vehicle_control.valid_for_ms = valid_for_ms
        pkt.vehicle_control.emergency_stop = emergency_stop
        pkt.vehicle_control.speed_steering.speed_mps = speed_mps
        pkt.vehicle_control.speed_steering.steering_angle_rad = steering_angle_rad
        return pkt

    def vehicle_control_target_xy(
        self,
        src: int,
        dst: int,
        seq: int,
        target_x_m: float = 0.0,
        target_y_m: float = 0.0,
        tolerance_m: float = 0.10,
        valid_for_ms: int = 250,
        emergency_stop: bool = False,
    ) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.vehicle_control.command_seq = seq
        pkt.vehicle_control.valid_for_ms = valid_for_ms
        pkt.vehicle_control.emergency_stop = emergency_stop
        pkt.vehicle_control.target_xy.target_x_m = target_x_m
        pkt.vehicle_control.target_xy.target_y_m = target_y_m
        pkt.vehicle_control.target_xy.tolerance_m = tolerance_m
        return pkt

    def vehicle_status(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.vehicle_status.last_command_seq = seq
        pkt.vehicle_status.accepted = True
        pkt.vehicle_status.active_command_type = pb.VEHICLE_COMMAND_TYPE_SPEED_STEERING
        pkt.vehicle_status.fault_flags = 0
        return pkt

    def anchor_layout_get(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.anchor_layout_get.dummy = 0
        return pkt

    def anchor_layout_set(self, src: int, dst: int, seq: int, anchors: list | None = None) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.anchor_layout_set.SetInParent()
        if anchors is None:
            self._fill_default_anchor_layout(pkt.anchor_layout_set.anchors)
        else:
            for a in anchors:
                anchor = pkt.anchor_layout_set.anchors.add()
                anchor.anchor_id = int(a.get("anchor_id", 0))
                anchor.x_m = float(a.get("x_m", 0.0))
                anchor.y_m = float(a.get("y_m", 0.0))
                anchor.z_m = float(a.get("z_m", 0.0))
        return pkt

    def anchor_layout_resp(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        self._fill_default_anchor_layout(pkt.anchor_layout_resp.anchors)
        return pkt

    @staticmethod
    def _fill_default_anchor_layout(anchors) -> None:
        for anchor_id, x_m, y_m, z_m in DEFAULT_ANCHOR_LAYOUT:
            anchor = anchors.add()
            anchor.anchor_id = anchor_id
            anchor.x_m = x_m
            anchor.y_m = y_m
            anchor.z_m = z_m

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

    def calib_status_get(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.calib_status_get.dummy = 0
        return pkt

    def calib_status_resp(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.calib_status_resp.SetInParent()
        pkt.calib_status_resp.state = pb.CALIB_STATE_IDLE
        pkt.calib_status_resp.progress_percent = 0
        pkt.calib_status_resp.current_iteration = 0
        pkt.calib_status_resp.total_iterations = 0
        return pkt

    def factory_otp_write(
        self,
        src: int,
        dst: int,
        seq: int,
        confirm_magic: int = 0x4F545057,
        otp_type: int = 0,
        device_type: int = pb.DEVICE_TYPE_ANCHOR,
        tx_antenna_delay: int = 0,
        rx_antenna_delay: int = 0,
        value_u32: int = 0,
        value_u8: int = 0,
    ) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.factory_otp_write.confirm_magic = confirm_magic
        pkt.factory_otp_write.otp_type = otp_type
        pkt.factory_otp_write.device_type = device_type
        pkt.factory_otp_write.tx_antenna_delay = tx_antenna_delay
        pkt.factory_otp_write.rx_antenna_delay = rx_antenna_delay
        pkt.factory_otp_write.value_u32 = value_u32
        pkt.factory_otp_write.value_u8 = value_u8
        return pkt

    def rtos_resource_get(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.rtos_resource_get.dummy = 0
        return pkt

    def rtos_resource_resp(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.rtos_resource_resp.SetInParent()
        pkt.rtos_resource_resp.sample_window_ms = 0
        pkt.rtos_resource_resp.cpu_busy_permille = 0
        pkt.rtos_resource_resp.heap_free_bytes = 0
        pkt.rtos_resource_resp.heap_min_ever_free_bytes = 0
        pkt.rtos_resource_resp.min_stack_free_bytes = 0
        pkt.rtos_resource_resp.min_stack_task_id = 0
        pkt.rtos_resource_resp.task_count = 0
        pkt.rtos_resource_resp.health_flags = 0
        return pkt

    def rtos_task_stats_get(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.rtos_task_stats_get.dummy = 0
        return pkt

    def rtos_task_stats_resp(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        task = pkt.rtos_task_stats_resp.tasks.add()
        task.task_id = 0
        task.name = "test"
        return pkt

    # ── BLE central commands ──────────────────────────────────────────────────────────

    def ble_scan_start(self, src: int, dst: int, seq: int,
                       duration_ms: int = 5000,
                       interval_ms: int = 160,
                       window_ms: int = 80,
                       active_scanning: bool = True) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.ble_scan_start.duration_ms = duration_ms
        pkt.ble_scan_start.interval_ms = interval_ms
        pkt.ble_scan_start.window_ms = window_ms
        pkt.ble_scan_start.active_scanning = active_scanning
        return pkt

    def ble_scan_stop(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.ble_scan_stop.dummy = 0
        return pkt
        
    def ble_connect(self, src: int, dst: int, seq: int,
                    mac_address: bytes = b"\x00\x11\x22\x33\x44\x55") -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.ble_connect.mac_address = mac_address
        return pkt

    def ble_disconnect(self, src: int, dst: int, seq: int, reason: int = 0) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.ble_disconnect.reason = reason
        return pkt

    def ble_conn_params_get(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.ble_conn_params_get.dummy = 0
        return pkt

    def ble_conn_params_set(
        self,
        src: int,
        dst: int,
        seq: int,
        min_interval_ms: int = 15,
        max_interval_ms: int = 30,
        slave_latency: int = 0,
        sup_timeout_ms: int = 4000,
    ) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.ble_conn_params_set.params.min_interval_ms = min_interval_ms
        pkt.ble_conn_params_set.params.max_interval_ms = max_interval_ms
        pkt.ble_conn_params_set.params.slave_latency = slave_latency
        pkt.ble_conn_params_set.params.sup_timeout_ms = sup_timeout_ms
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
        pkt.ble_scan_result.name = ""  # proto field 3: device name
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

    def zone_switch(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        return pkt

    def zone_profile_set(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.zone_profile_set.profile.zone_id = 1
        pkt.zone_profile_set.profile.preamble_code = 17
        pkt.zone_profile_set.profile.anchor_count = 4
        for anchor_id, x_m, y_m in (
            (1, 0.0, 0.0),
            (2, 4.0, 0.0),
            (3, 0.0, 4.0),
            (4, 4.0, 4.0),
        ):
            anchor = pkt.zone_profile_set.profile.anchors.add()
            anchor.anchor_id = anchor_id
            anchor.x_m = x_m
            anchor.y_m = y_m
            anchor.z_m = 2.0
        return pkt

    def zone_profile_get(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.zone_profile_get.zone_id = 1
        return pkt

    def zone_profile_resp(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.zone_profile_resp.profile.zone_id = 1
        pkt.zone_profile_resp.profile.preamble_code = 17
        pkt.zone_profile_resp.profile.anchor_count = 4
        return pkt

    def calib_start(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.calib_start.sample_target = 32
        pkt.calib_start.tag_x_m = 2.0
        pkt.calib_start.tag_y_m = 2.0
        pkt.calib_start.tag_z_m = 1.0
        pkt.calib_start.reference_position_valid = True
        return pkt

    def calib_stop(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.calib_stop.dummy = 0
        return pkt

    def calib_candidate_apply(self, src: int, dst: int, seq: int) -> pb.packet_t:
        pkt = self._base(src, dst, seq)
        pkt.calib_candidate_apply.anchor_mask = 0xF
        return pkt


class CommandCatalog:
    def __init__(self, factory: CommandFactory | None = None) -> None:
        self.factory = factory or CommandFactory()
        self._specs = [
            CommandSpec(2, "none", self.factory.none),
            CommandSpec(3, "ack", self.factory.ack),
            CommandSpec(4, "device_information_get", self.factory.device_information_get, "device_information_resp"),
            CommandSpec(5, "device_information_resp", self.factory.device_information_resp),
            CommandSpec(6, "time_sync_get", self.factory.time_sync_get, "time_sync_resp"),
            CommandSpec(7, "time_sync_set", self.factory.time_sync_set, "time_sync_resp"),
            CommandSpec(8, "time_sync_resp", self.factory.time_sync_resp),
            CommandSpec(9, "time_sync_adv_set", self.factory.time_sync_adv_set),
            CommandSpec(10, "sys_config_get", self.factory.sys_config_get, "sys_config_resp"),
            CommandSpec(11, "sys_config_set", self.factory.sys_config_set, "sys_config_resp"),
            CommandSpec(12, "sys_config_resp", self.factory.sys_config_resp),
            CommandSpec(13, "sys_ranging_cfg_get", self.factory.sys_ranging_cfg_get, "sys_ranging_cfg_resp"),
            CommandSpec(14, "sys_ranging_cfg_set", self.factory.sys_ranging_cfg_set, "sys_ranging_cfg_resp"),
            CommandSpec(15, "sys_ranging_cfg_resp", self.factory.sys_ranging_cfg_resp),
            CommandSpec(16, "ranging_start", self.factory.ranging_start),
            CommandSpec(17, "ranging_stop", self.factory.ranging_stop),
            CommandSpec(18, "ranging_result", self.factory.ranging_result),
            CommandSpec(19, "ranging_status_get", self.factory.ranging_status_get, "ranging_status_resp"),
            CommandSpec(20, "ranging_status_resp", self.factory.ranging_status_resp),
            CommandSpec(21, "sensor_fusion_cfg_get", self.factory.sensor_fusion_cfg_get, "sensor_fusion_cfg_resp"),
            CommandSpec(22, "sensor_fusion_cfg_set", self.factory.sensor_fusion_cfg_set, "sensor_fusion_cfg_resp"),
            CommandSpec(23, "sensor_fusion_cfg_resp", self.factory.sensor_fusion_cfg_resp),
            CommandSpec(24, "sensor_fusion_result", self.factory.sensor_fusion_result),
            # IMU
            CommandSpec(25, "imu_reset", self.factory.imu_reset),
            CommandSpec(26, "imu_calib_start", self.factory.imu_calib_start),
            # System commands
            CommandSpec(30, "device_reset", self.factory.device_reset),
            CommandSpec(31, "uwb_reset", self.factory.uwb_reset),
            CommandSpec(32, "factory_config_reset", self.factory.factory_config_reset),
            CommandSpec(33, "device_type_set", self.factory.device_type_set),
            CommandSpec(34, "device_type_get", self.factory.device_type_get, "device_type_set"),
            CommandSpec(35, "flash_erase", self.factory.flash_erase),
            CommandSpec(36, "flash_read", self.factory.flash_read),
            CommandSpec(37, "flash_data", self.factory.flash_data),
            CommandSpec(38, "flash_write", self.factory.flash_write),
            # BLE
            CommandSpec(39, "ble_adv_config_set", self.factory.ble_adv_config_set),
            CommandSpec(40, "ble_status_get", self.factory.ble_status_get, "ble_status_resp"),
            CommandSpec(41, "ble_status_resp", self.factory.ble_status_resp),
            CommandSpec(42, "ble_adv_status", self.factory.ble_adv_status),
            CommandSpec(43, "log_data", self.factory.log_data),
            CommandSpec(44, "log_clear", self.factory.log_clear),
            CommandSpec(45, "host_transport_set", self.factory.host_transport_set),
            # Calibration
            CommandSpec(46, "pos_calib_cfg_get", self.factory.pos_calib_cfg_get, "pos_calib_cfg_resp"),
            CommandSpec(47, "pos_calib_cfg_set", self.factory.pos_calib_cfg_set, "pos_calib_cfg_resp"),
            CommandSpec(48, "pos_calib_cfg_resp", self.factory.pos_calib_cfg_resp),
            CommandSpec(49, "anchor_layout_get", self.factory.anchor_layout_get, "anchor_layout_resp"),
            CommandSpec(50, "anchor_layout_set", self.factory.anchor_layout_set, "anchor_layout_resp"),
            CommandSpec(51, "anchor_layout_resp", self.factory.anchor_layout_resp),
            CommandSpec(52, "flash_verify", self.factory.flash_verify),
            # BLE Central
            CommandSpec(53, "ble_conn_params_get", self.factory.ble_conn_params_get, "ble_conn_params_resp"),
            CommandSpec(54, "ble_conn_params_set", self.factory.ble_conn_params_set, "ble_conn_params_resp"),
            CommandSpec(55, "ble_conn_params_resp", self.factory.ble_conn_params_resp),
            CommandSpec(56, "ble_disconnect", self.factory.ble_disconnect),
            CommandSpec(57, "ble_scan_start", self.factory.ble_scan_start),
            CommandSpec(58, "ble_scan_stop", self.factory.ble_scan_stop),
            CommandSpec(59, "ble_connect", self.factory.ble_connect),
            CommandSpec(60, "ble_scan_result", self.factory.ble_scan_result),
            # FOTA
            CommandSpec(61, "fota_state_resp", self.factory.fota_state_resp),
            # Battery
            CommandSpec(62, "battery_info_resp", self.factory.battery_info_resp),
            CommandSpec(63, "battery_info_get", self.factory.battery_info_get, "battery_info_resp"),
            CommandSpec(64, "enter_to_bootloader", self.factory.enter_to_bootloader),
            # Calib status
            CommandSpec(65, "calib_status_get", self.factory.calib_status_get, "calib_status_resp"),
            CommandSpec(66, "calib_status_resp", self.factory.calib_status_resp),
            CommandSpec(67, "end_session", self.factory.end_session),
            # Factory OTP
            CommandSpec(68, "factory_otp_write", self.factory.factory_otp_write),
            # RTOS diagnostics
            CommandSpec(71, "rtos_resource_get", self.factory.rtos_resource_get, "rtos_resource_resp"),
            CommandSpec(72, "rtos_resource_resp", self.factory.rtos_resource_resp),
            CommandSpec(73, "rtos_task_stats_get", self.factory.rtos_task_stats_get, "rtos_task_stats_resp"),
            CommandSpec(74, "rtos_task_stats_resp", self.factory.rtos_task_stats_resp),
            CommandSpec(75, "prefilter_cfg_get", self.factory.prefilter_cfg_get, "prefilter_cfg_resp"),
            CommandSpec(76, "prefilter_cfg_set", self.factory.prefilter_cfg_set, "prefilter_cfg_resp"),
            CommandSpec(77, "prefilter_cfg_resp", self.factory.prefilter_cfg_resp),
            CommandSpec(78, "vehicle_control", self.factory.vehicle_control_speed_steering),
            CommandSpec(79, "vehicle_status", self.factory.vehicle_status),
            CommandSpec(80, "zone_switch", self.factory.zone_switch),
            CommandSpec(81, "zone_profile_set", self.factory.zone_profile_set),
            CommandSpec(82, "zone_profile_get", self.factory.zone_profile_get, "zone_profile_resp"),
            CommandSpec(83, "zone_profile_resp", self.factory.zone_profile_resp),
            CommandSpec(84, "calib_start", self.factory.calib_start),
            CommandSpec(85, "calib_stop", self.factory.calib_stop),
            CommandSpec(86, "calib_candidate_apply", self.factory.calib_candidate_apply),
        ]

    def all(self) -> Iterable[CommandSpec]:
        return tuple(self._specs)

    def get(self, param_name: str) -> CommandSpec:
        for spec in self._specs:
            if spec.param_name == param_name:
                return spec
        raise KeyError(f"Unknown command: {param_name}")

    def get_by_tag(self, tag: int) -> CommandSpec:
        """Lookup command spec by proto oneof tag number (for RX decode)."""
        for spec in self._specs:
            if spec.tag == tag:
                return spec
        raise KeyError(f"Unknown tag: {tag}")

    def tag_to_param_name(self, tag: int) -> str:
        """Convert proto tag number to param_name string."""
        return self.get_by_tag(tag).param_name

    def param_name_to_tag(self, param_name: str) -> int:
        """Convert param_name string to proto tag number."""
        return self.get(param_name).tag

    def expected_response_for(self, param_name: str) -> str:
        """Return expected response param for query/set commands, if any."""
        return self.get(param_name).expected_response

    def query_response_map(self) -> dict[str, str]:
        """Map command param_name -> expected response param_name."""
        return {
            spec.param_name: spec.expected_response
            for spec in self._specs
            if spec.expected_response
        }

