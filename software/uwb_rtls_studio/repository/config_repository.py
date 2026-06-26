"""
Repository for configuration protobuf packets.

The data layer keeps decoded packets in the raw packet store. This repository
converts the configuration responses into plain dictionaries and publishes them
to SharedAppState so every tab can consume the same parsed state.
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from utils.app_state import shared_app_state


class ConfigRepository(QObject):
    sys_config_updated = pyqtSignal(dict)
    sys_ranging_cfg_updated = pyqtSignal(dict)
    sensor_fusion_cfg_updated = pyqtSignal(dict)
    pos_calib_cfg_updated = pyqtSignal(dict)
    device_type_updated = pyqtSignal(int)


    def __init__(self, parent=None):
        super().__init__(parent)
        self._sys_config: dict = {}
        self._sys_ranging_cfg: dict = {}
        self._sensor_fusion_cfg: dict = {}
        self._pos_calib_cfg: dict = {}

    @property
    def sys_config(self) -> dict:
        return self._sys_config.copy()

    @property
    def sys_ranging_cfg(self) -> dict:
        return self._sys_ranging_cfg.copy()

    @property
    def sensor_fusion_cfg(self) -> dict:
        return self._sensor_fusion_cfg.copy()

    @property
    def pos_calib_cfg(self) -> dict:
        return self._pos_calib_cfg.copy()

    def handle_packet(self, param_name: str, pkt) -> bool:
        if param_name == "sys_config_resp":
            self.save_sys_config(self.parse_sys_config(pkt.sys_config_resp.config))
            return True
        if param_name == "sys_ranging_cfg_resp":
            self.save_sys_ranging_cfg(self.parse_sys_ranging_cfg(pkt.sys_ranging_cfg_resp.config))
            return True
        if param_name == "sensor_fusion_cfg_resp":
            self.save_sensor_fusion_cfg(self.parse_sensor_fusion_cfg(pkt.sensor_fusion_cfg_resp.config))
            return True
        if param_name == "pos_calib_cfg_resp":
            self.save_pos_calib_cfg(self.parse_pos_calib_cfg(pkt.pos_calib_cfg_resp.config))
            return True
        if param_name == "device_type_set":
            self.save_device_type(int(getattr(pkt.device_type_set, "device_type", 0)))
            return True
        return False

    def parse_sys_config(self, cfg) -> dict:
        return {
            "role": int(getattr(cfg, "role", 0)),
            "device_id": int(getattr(cfg, "device_id", 0)),
            "ranging_period_ms": int(getattr(cfg, "ranging_period_ms", 0)),
            "rx_timeout_ms": int(getattr(cfg, "rx_timeout_ms", 0)),
            "uwb_channel": int(getattr(cfg, "uwb_channel", 0)),
            "uwb_prf": int(getattr(cfg, "uwb_prf", 0)),
            "uwb_data_rate": int(getattr(cfg, "uwb_data_rate", 0)),
            "uwb_preamble_code": int(getattr(cfg, "uwb_preamble_code", 0)),
            "tx_antenna_delay": int(getattr(cfg, "tx_antenna_delay", 0)),
            "rx_antenna_delay": int(getattr(cfg, "rx_antenna_delay", 0)),
            "tx_power": int(getattr(cfg, "tx_power", 0)),
            "anchor_list": bytes(getattr(cfg, "anchor_list", b"")),
            "power_mode": int(getattr(cfg, "power_mode", 0)),
            "uwb_preamble_len": int(getattr(cfg, "uwb_preamble_len", 0)),
            "uwb_rx_pac": int(getattr(cfg, "uwb_rx_pac", 0)),
            "uwb_ns_sfd": int(getattr(cfg, "uwb_ns_sfd", 0)),
            "uwb_phr_mode": int(getattr(cfg, "uwb_phr_mode", 0)),
            "smart_tx_power": bool(getattr(cfg, "smart_tx_power", False)),
            "pg_delay": int(getattr(cfg, "pg_delay", 0)),
        }

    def parse_sys_ranging_cfg(self, cfg) -> dict:
        return {
            "rx_timeout_ms": int(getattr(cfg, "rx_timeout_ms", 0)),
            "ranging_period_ms": int(getattr(cfg, "ranging_period_ms", 0)),
        }

    def parse_sensor_fusion_cfg(self, cfg) -> dict:
        return {
            "alpha": float(getattr(cfg, "alpha", 0.0)),
            "kappa": float(getattr(cfg, "kappa", 0.0)),
            "beta": float(getattr(cfg, "beta", 0.0)),
            "q_a": float(getattr(cfg, "q_a", 0.0)),
            "q_g": float(getattr(cfg, "q_g", 0.0)),
            "r_uwb": float(getattr(cfg, "r_uwb", 0.0)),
            "init_p_px": float(getattr(cfg, "init_p_px", 0.0)),
            "init_p_py": float(getattr(cfg, "init_p_py", 0.0)),
            "init_p_vx": float(getattr(cfg, "init_p_vx", 0.0)),
            "init_p_vy": float(getattr(cfg, "init_p_vy", 0.0)),
            "init_p_theta": float(getattr(cfg, "init_p_theta", 0.0)),
            "init_p_bias_ax": float(getattr(cfg, "init_p_bias_ax", 0.0)),
            "init_p_bias_ay": float(getattr(cfg, "init_p_bias_ay", 0.0)),
            "init_p_bias_gz": float(getattr(cfg, "init_p_bias_gz", 0.0)),
        }

    def parse_pos_calib_cfg(self, cfg) -> dict:
        return {
            "enable_anchor_auto_calib": bool(getattr(cfg, "enable_anchor_auto_calib", False)),
            "enable_tag_auto_calib": bool(getattr(cfg, "enable_tag_auto_calib", False)),
            "ref_distance_xy_m": float(getattr(cfg, "ref_distance_xy_m", 0.0)),
            "tag_height_m": float(getattr(cfg, "tag_height_m", 0.0)),
            "anchor_height_m": float(getattr(cfg, "anchor_height_m", 0.0)),
            "calib_anchor_id": int(getattr(cfg, "calib_anchor_id", 0)),
            "samples": int(getattr(cfg, "samples", 0)),
            "error_threshold_m": float(getattr(cfg, "error_threshold_m", 0.0)),
            "min_delta_step": int(getattr(cfg, "min_delta_step", 0)),
            "max_rounds": int(getattr(cfg, "max_rounds", 0)),
            "max_std_m": float(getattr(cfg, "max_std_m", 0.0)),
            "damping": float(getattr(cfg, "damping", 0.0)),
            "iterations": int(getattr(cfg, "iterations", 0)),
            "last_pair_error_mean_m": float(getattr(cfg, "last_pair_error_mean_m", 0.0)),
            "iterations_taken": int(getattr(cfg, "iterations_taken", 0)),
            "last_pair_error_spread_m": float(getattr(cfg, "last_pair_error_spread_m", 0.0)),
            "last_pair_std_mean_m": float(getattr(cfg, "last_pair_std_mean_m", 0.0)),
            "last_usable_pair_count": int(getattr(cfg, "last_usable_pair_count", 0)),
            "last_rejected_pair_count": int(getattr(cfg, "last_rejected_pair_count", 0)),
            "rejected_batch_count": int(getattr(cfg, "rejected_batch_count", 0)),
            "last_pair_error_rms_m": float(getattr(cfg, "last_pair_error_rms_m", 0.0)),
            "last_pair_error_max_abs_m": float(getattr(cfg, "last_pair_error_max_abs_m", 0.0)),
            "last_pair_error_mean_abs_m": float(getattr(cfg, "last_pair_error_mean_abs_m", 0.0)),
        }

    def save_sys_config(self, data: dict) -> None:
        self._sys_config = data.copy()
        shared_app_state.sys_config = self._sys_config
        self.sys_config_updated.emit(self.sys_config)

    def save_sys_ranging_cfg(self, data: dict) -> None:
        self._sys_ranging_cfg = data.copy()
        shared_app_state.sys_ranging_cfg = self._sys_ranging_cfg
        self.sys_ranging_cfg_updated.emit(self.sys_ranging_cfg)

    def save_sensor_fusion_cfg(self, data: dict) -> None:
        self._sensor_fusion_cfg = data.copy()
        shared_app_state.sensor_fusion_cfg = self._sensor_fusion_cfg
        self.sensor_fusion_cfg_updated.emit(self.sensor_fusion_cfg)

    def save_pos_calib_cfg(self, data: dict) -> None:
        self._pos_calib_cfg = data.copy()
        shared_app_state.pos_calib_cfg = self._pos_calib_cfg
        self.pos_calib_cfg_updated.emit(self.pos_calib_cfg)

    def save_device_type(self, device_type: int) -> None:
        shared_app_state.device_type = device_type
        self.device_type_updated.emit(device_type)
