"""
Repository for configuration protobuf packets.

The data layer keeps decoded packets in the raw packet store. This repository
converts the configuration responses into plain dictionaries and publishes them
to SharedAppState so every tab can consume the same parsed state.
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, pyqtSignal

from utils.app_state import shared_app_state

log = logging.getLogger(__name__)


class ConfigRepository(QObject):
    sys_config_updated = pyqtSignal(dict)
    sys_ranging_cfg_updated = pyqtSignal(dict)
    sensor_fusion_cfg_updated = pyqtSignal(dict)
    prefilter_cfg_updated = pyqtSignal(dict)
    pos_calib_cfg_updated = pyqtSignal(dict)
    device_type_updated = pyqtSignal(int)
    zone_profile_updated = pyqtSignal(dict)


    def __init__(self, parent=None):
        super().__init__(parent)
        self._sys_config: dict = {}
        self._sys_ranging_cfg: dict = {}
        self._sensor_fusion_cfg: dict = {}
        self._prefilter_cfg: dict = {}
        self._pos_calib_cfg: dict = {}
        self._zone_profiles: dict[int, dict] = {}
        shared_app_state.device_session_reset.connect(self.reset_session)

    def reset_session(self, _reason: str = "") -> None:
        self._sys_config = {}
        self._sys_ranging_cfg = {}
        self._sensor_fusion_cfg = {}
        self._prefilter_cfg = {}
        self._pos_calib_cfg = {}
        self._zone_profiles = {}

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
    def prefilter_cfg(self) -> dict:
        return self._prefilter_cfg.copy()

    @property
    def pos_calib_cfg(self) -> dict:
        return self._pos_calib_cfg.copy()

    @property
    def zone_profiles(self) -> dict[int, dict]:
        return {int(zone_id): dict(profile) for zone_id, profile in self._zone_profiles.items()}

    def handle_packet(self, param_name: str, pkt) -> bool:
        if param_name == "sys_config_resp":
            cfg = pkt.sys_config_resp.config
            # ByteSize()==0: firmware gửi gói nhưng config sub-message rỗng hoàn toàn
            # → save {} để UI hiện placeholder, phân biệt với config hợp lệ có tx_delay=0
            if cfg.ByteSize() == 0:
                self.save_sys_config({})
            else:
                self.save_sys_config(self.parse_sys_config(cfg))
            return True
        if param_name == "sys_ranging_cfg_resp":
            cfg = pkt.sys_ranging_cfg_resp.config
            if cfg.ByteSize() == 0:
                self.save_sys_ranging_cfg({})
            else:
                self.save_sys_ranging_cfg(self.parse_sys_ranging_cfg(cfg))
            return True
        if param_name == "sensor_fusion_cfg_resp":
            cfg = pkt.sensor_fusion_cfg_resp.config
            if cfg.ByteSize() == 0:
                self.save_sensor_fusion_cfg({})
            else:
                self.save_sensor_fusion_cfg(self.parse_sensor_fusion_cfg(cfg))
            return True
        if param_name == "prefilter_cfg_resp":
            cfg = pkt.prefilter_cfg_resp.config
            if cfg.ByteSize() == 0:
                self.save_prefilter_cfg({})
            else:
                self.save_prefilter_cfg(self.parse_prefilter_cfg(cfg))
            return True
        if param_name == "pos_calib_cfg_resp":
            cfg = pkt.pos_calib_cfg_resp.config
            # ByteSize()==0: firmware chưa config pos_calib → hiện placeholder thay vì 0
            if cfg.ByteSize() == 0:
                self.save_pos_calib_cfg({})
            else:
                self.save_pos_calib_cfg(self.parse_pos_calib_cfg(cfg))
            return True
        if param_name == "device_type_set":
            self.save_device_type(int(getattr(pkt.device_type_set, "device_type", 0)))
            return True
        if param_name == "zone_profile_resp":
            profile = pkt.zone_profile_resp.profile
            if profile.ByteSize() == 0:
                return True
            self.save_zone_profile(self.parse_zone_profile(profile))
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

    def parse_prefilter_cfg(self, cfg) -> dict:
        return {
            "enable": bool(getattr(cfg, "enable", False)),
            "recover_d2": float(getattr(cfg, "recover_d2", 0.0)),
            "reject_d2": float(getattr(cfg, "reject_d2", 0.0)),
            "r_base": float(getattr(cfg, "r_base", 0.0)),
            "r_gate": float(getattr(cfg, "r_gate", 0.0)),
            "velocity_weight": float(getattr(cfg, "velocity_weight", 0.0)),
            "min_covariance": float(getattr(cfg, "min_covariance", 0.0)),
        }

    def parse_zone_profile(self, profile) -> dict:
        zone_id = int(getattr(profile, "zone_id", 0))
        zone_key = str(zone_id) if zone_id else ""
        anchors = []
        for item in getattr(profile, "anchors", []):
            anchor_id = int(getattr(item, "anchor_id", 0))
            x_m = float(getattr(item, "x_m", 0.0))
            y_m = float(getattr(item, "y_m", 0.0))
            z_m = float(getattr(item, "z_m", 0.0))
            anchors.append({
                "anchor_id": anchor_id,
                "x_m": x_m,
                "y_m": y_m,
                "z_m": z_m,
                "x": x_m,
                "y": y_m,
                "z": z_m,
                "label": f"A{anchor_id}",
                "role": "anchor",
                "device_type": "uwb_anchor",
                "device_id": anchor_id,
                "zone_id": zone_key,
                "zone_name": f"Zone {zone_id}" if zone_id else "",
                "zone_ids": [zone_key] if zone_key else [],
                "zone_names": [f"Zone {zone_id}"] if zone_id else [],
                "room_id": zone_key,
                "local_x_m": x_m,
                "local_y_m": y_m,
                "placed": True,
                "is_scanned": False,
                "sync_state": "synced",
            })
        return {
            "zone_id": zone_id,
            "preamble_code": int(getattr(profile, "preamble_code", 0)),
            "anchor_count": int(getattr(profile, "anchor_count", 0)),
            "anchors": anchors,
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

    def save_prefilter_cfg(self, data: dict) -> None:
        self._prefilter_cfg = data.copy()
        shared_app_state.prefilter_cfg = self._prefilter_cfg
        self.prefilter_cfg_updated.emit(self.prefilter_cfg)
    def save_pos_calib_cfg(self, data: dict) -> None:
        self._pos_calib_cfg = data.copy()
        shared_app_state.pos_calib_cfg = self._pos_calib_cfg
        self.pos_calib_cfg_updated.emit(self.pos_calib_cfg)


    @staticmethod
    def _is_numeric_zone_id(value) -> bool:
        text = str(value or "").strip().lower()
        if not text:
            return False
        if text.isdigit():
            return True
        for prefix in ("zone_", "room_"):
            if text.startswith(prefix) and text[len(prefix):].isdigit():
                return True
        return False

    def _should_preserve_map_anchor_layout(self, profile: dict) -> bool:
        current = shared_app_state.anchor_layout
        if not current:
            return False
        profile_zone = str(profile.get("zone_id", "") or "")
        for anchor in current:
            room_id = anchor.get("room_id", anchor.get("zone_id", ""))
            if room_id and not self._is_numeric_zone_id(room_id):
                log.debug(
                    "Preserving geofence map anchor layout while caching zone_profile_resp zone=%s; current room_id=%s",
                    profile_zone or "-",
                    room_id,
                )
                return True
        return False

    def save_zone_profile(self, data: dict) -> None:
        profile = dict(data or {})
        zone_id = int(profile.get("zone_id", 0) or 0)
        if zone_id <= 0:
            return
        self._zone_profiles[zone_id] = profile
        shared_app_state.update_zone_profile(profile)
        anchors = [dict(anchor) for anchor in profile.get("anchors", [])]
        if anchors and not self._should_preserve_map_anchor_layout(profile):
            shared_app_state.anchor_layout = anchors
        self.zone_profile_updated.emit(dict(profile))

    def save_device_type(self, device_type: int) -> None:
        shared_app_state.device_type = device_type
        self.device_type_updated.emit(device_type)
