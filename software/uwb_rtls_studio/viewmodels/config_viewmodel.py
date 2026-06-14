"""
==============================================================================
  UWB RTLS Studio — Config ViewModel
==============================================================================
  File        : viewmodels/config_viewmodel.py
  Description : ViewModel for the "Config Parameters" tab.
                Bridges configuration updates and reset command triggers between
                the View and the DeviceModel.

 MVVM Role   : VIEWMODEL

  ═══════════════════════════════════════════════════════════════════════
  USER vs DEVELOPER — Config Sections Visibility
  ═══════════════════════════════════════════════════════════════════════

  Config Tab hiện cho CẢ HAI mode, nhưng sections khác nhau:

  ┌──────────────────────────────┬──────────┬───────────┐
  │ Config Section               │ User     │ Developer │
  ├──────────────────────────────┼──────────┼───────────┤
  │ Anchor/Tag Layout            │ ✅ Read  │ ✅ R/W    │
  │ Time Sync                    │ ✅ R/W   │ ✅ R/W    │
  │ Ranging Configuration        │ ✅ R/W   │ ✅ R/W    │
  │ UWB Basic (channel, role)    │ ✅ Read  │ ✅ R/W    │
  │ UWB Advanced (antenna delay) │ ❌ Hide  │ ✅ R/W    │
  │ Sensor Fusion (UKF params)   │ ❌ Hide  │ ✅ R/W    │
  │ System Commands (reset)      │ ❌ Hide  │ ✅        │
  │ BLE Connection Params        │ ❌ Hide  │ ✅ R/W    │
  └──────────────────────────────┴──────────┴───────────┘

  Rationale:
    - Anchor/Tag Layout: user cần biết vị trí anchors để setup phòng,
      nhưng chỉ đọc; developer có thể chỉnh sửa.
    - Time Sync: user cần đồng bộ thời gian cho device.
    - Ranging Config: user cần điều chỉnh tốc độ/timeout ranging.
    - UWB Basic: user cần biết channel, role đang dùng.
    - UWB Advanced / Sensor Fusion / System Reset: chỉ cho developer.

  ═══════════════════════════════════════════════════════════════════════

  Tab Layout (Full — Developer Mode):
    ┌─────────────────────────────────────────────────────────┐
    │  CONFIG PARAMETERS TAB                                  │
    ├─────────────────────────────────────────────────────────┤
    │                                                         │
    │  ┌─ 👤 Anchor / Tag Layout (User + Developer) ────────┐ │
    │  │  ┌─────────┬────────┬────────┬────────┐             │ │
    │  │  │ Anchor  │  X (m) │  Y (m) │  Z (m) │             │ │
    │  │  ├─────────┼────────┼────────┼────────┤             │ │
    │  │  │ A1      │  0.00  │  0.00  │  2.50  │             │ │
    │  │  │ A2      │  5.00  │  0.00  │  2.50  │             │ │
    │  │  │ A3      │  0.00  │  4.00  │  2.50  │             │ │
    │  │  │ A4      │  5.00  │  4.00  │  2.50  │             │ │
    │  │  └─────────┴────────┴────────┴────────┘             │ │
    │  │  [📥 Read Layout] [📤 Write Layout]*(dev only)      │ │
    │  └────────────────────────────────────────────────────┘ │
    │                                                         │
    │  ┌─ 👤 Time Synchronization (User + Developer) ───────┐ │
    │  │  Device Time: 2026-05-30 12:30:00 UTC+7             │ │
    │  │  Host Time:   2026-05-30 12:30:00 UTC+7             │ │
    │  │  Offset: 0ms                                         │ │
    │  │  [🔄 Sync Now]                                       │ │
    │  └────────────────────────────────────────────────────┘ │
    │                                                         │
    │  ┌─ 👤 Ranging Configuration (Developer) ──────┐ │
    │  │  Ranging Period: [___] ms    RX Timeout: [___] ms   │ │
    │  │  [📥 Read]  [📤 Write]                               │ │
    │  └────────────────────────────────────────────────────┘ │
    │                                                         │
    │  ┌─ 👤 UWB Basic Info (User: read-only) ──────────────┐ │
    │  │  Role: TAG       Device ID: 1                       │ │
    │  │  Channel: 5      PRF: 64 MHz                        │ │
    │  │  Data Rate: 6800 kbps                               │ │
    │  │  [📥 Read Config]                                    │ │
    │  └────────────────────────────────────────────────────┘ │
    │                                                         │
    │  ── Developer Only ──────────────────────────────────── │
    │                                                         │
    │  ┌─ 🔧 UWB Advanced Config (Developer only) ─────────┐ │
    │  │  Preamble Code: [___]                               │ │
    │  │  TX Antenna Delay: [___]  RX Antenna Delay: [___]   │ │
    │  │  TX Power: [___]         Power Mode: [▼ ...]         │ │
    │  │  [📥 Read]  [📤 Write]                               │ │
    │  └────────────────────────────────────────────────────┘ │
    │                                                         │
    │  ┌─ 🔧 Sensor Fusion (UKF) Config (Developer only) ──┐ │
    │  │  Alpha: [___]  Kappa: [___]  Beta: [___]            │ │
    │  │  Q_accel: [___]  Q_gyro: [___]  R_uwb: [___]       │ │
    │  │  Init P: px[__] py[__] vx[__] vy[__] ...            │ │
    │  │  [📥 Read]  [📤 Write]                               │ │
    │  └────────────────────────────────────────────────────┘ │
    │                                                         │
    │  ┌─ 🔧 BLE Connection Params (Developer only) ───────┐ │
    │  │  Min Interval: [__]ms  Max Interval: [__]ms         │ │
    │  │  Latency: [__]         Sup Timeout: [__]ms          │ │
    │  │  [📥 Read]  [📤 Write]                               │ │
    │  └────────────────────────────────────────────────────┘ │
    │                                                         │
    │  ┌─ 🔧 System Commands (Developer only) ──────────────┐ │
    │  │  [🔄 Device Reset] [🔄 UWB Reset] [⚠ Factory Reset]│ │
    │  └────────────────────────────────────────────────────┘ │
    └─────────────────────────────────────────────────────────┘

  Signals:
    - config_loaded(group: str, config: dict)
    - config_saved(group: str, success: bool)
    - anchor_layout_loaded(anchors: list)
    - anchor_layout_saved(success: bool)
    - time_synced(success: bool)
    - reset_completed(reset_type: str)

  Protocol Messages:
    - sys_config_get_t / _set_t / _resp_t         (10, 11, 12)
    - sys_ranging_cfg_get_t / _set_t / _resp_t     (13, 14, 15)
    - sensor_fusion_cfg_get_t / _set_t / _resp_t   (21, 22, 23)
    - anchor_layout_get_t / _set_t / _resp_t       (43, 44, 45)
    - time_sync_get_t / _set_t / _resp_t           (6, 7, 8)
    - ble_conn_params_get_t / _set_t / _resp_t     (47, 48, 49)
    - device_reset_t (24), uwb_reset_t (25), factory_config_reset_t (26)
===============================================================================
"""
import logging
from PyQt6.QtCore import QObject, pyqtSignal

log = logging.getLogger(__name__)


class ConfigViewModel(QObject):
    # Signals for View updates
    anchor_layout_updated = pyqtSignal(list)
    sys_config_updated = pyqtSignal(dict)
    sys_ranging_cfg_updated = pyqtSignal(dict)
    sensor_fusion_cfg_updated = pyqtSignal(dict)
    pos_calib_cfg_updated = pyqtSignal(dict)

    def __init__(self, device_model, ranging_model, command_bus=None, parent=None):
        super().__init__(parent)
        self.model = device_model
        self.ranging_model = ranging_model

        # Bind Model parsed signals to ViewModel update signals
        from utils.app_state import shared_app_state
        self._shared_app_state = shared_app_state
        shared_app_state.anchor_layout_changed.connect(self.anchor_layout_updated.emit)
        shared_app_state.sys_config_changed.connect(self.sys_config_updated.emit)
        shared_app_state.sys_ranging_cfg_changed.connect(self.sys_ranging_cfg_updated.emit)
        shared_app_state.sensor_fusion_cfg_changed.connect(self.sensor_fusion_cfg_updated.emit)
        shared_app_state.pos_calib_cfg_changed.connect(self.pos_calib_cfg_updated.emit)

    def update_shared_anchor_layout(self, anchors: list):
        self._shared_app_state.anchor_layout = anchors

    def emit_current_state(self):
        """Emit cached config values so the View can render without touching app state."""
        if self._shared_app_state.anchor_layout:
            self.anchor_layout_updated.emit(self._shared_app_state.anchor_layout)
        if self._shared_app_state.sys_ranging_cfg:
            self.sys_ranging_cfg_updated.emit(self._shared_app_state.sys_ranging_cfg)
        if self._shared_app_state.sys_config:
            self.sys_config_updated.emit(self._shared_app_state.sys_config)
        if self._shared_app_state.sensor_fusion_cfg:
            self.sensor_fusion_cfg_updated.emit(self._shared_app_state.sensor_fusion_cfg)
        if self._shared_app_state.pos_calib_cfg:
            self.pos_calib_cfg_updated.emit(self._shared_app_state.pos_calib_cfg)

    # ── Command Triggers (called by View) ───────────────────────────

    def read_anchor_layout(self):
        log.info("Requesting anchor layout from MCU via global query queue...")
        self.model.request_anchor_layout()

    def write_anchor_layout(self, anchors: list):
        log.info("Sending anchor layout set command to MCU: %s", anchors)
        self.ranging_model.set_anchor_layout(anchors)
        self.model.set_anchor_layout(anchors)

    def read_ranging_config(self):
        log.info("Requesting system ranging config from MCU via global query queue...")
        self.model.request_ranging_config()

    def write_ranging_config(self, period_ms: int, timeout_ms: int):
        log.info("Sending ranging config set command to MCU: period=%d ms, timeout=%d ms", period_ms, timeout_ms)
        self.model.set_ranging_config(period_ms=period_ms, timeout_ms=timeout_ms)

    def read_sys_config(self):
        log.info("Requesting system configuration from MCU via global query queue...")
        self.model.request_sys_config()

    def write_sys_config(self, **kwargs):
        log.info("Sending sys config set command to MCU: %s", kwargs)
        self.model.set_sys_config(**kwargs)

    def read_sensor_fusion_config(self):
        log.info("Requesting sensor fusion configuration from MCU via global query queue...")
        self.model.request_sensor_fusion_config()

    def write_sensor_fusion_config(self, **kwargs):
        log.info("Sending sensor fusion config set command to MCU: %s", kwargs)
        self.model.set_sensor_fusion_config(**kwargs)

    def read_pos_calib_config(self):
        log.info("Requesting position calibration configuration from MCU via global query queue...")
        self.model.request_pos_calib_config()

    def write_pos_calib_config(self, **kwargs):
        log.info("Sending position calibration config set command to MCU: %s", kwargs)
        self.model.set_pos_calib_config(**kwargs)

    def device_reset(self):
        log.warning("Sending device_reset command to MCU...")
        self.model.request_device_reset()

    def uwb_reset(self):
        log.warning("Sending uwb_reset command to MCU...")
        self.model.request_uwb_reset()

    def factory_reset(self):
        log.warning("Sending factory_config_reset command to MCU...")
        self.model.request_factory_config_reset()

    def enter_bootloader(self):
        log.warning("Sending enter_to_bootloader command to MCU...")
        self.model.request_enter_bootloader()
