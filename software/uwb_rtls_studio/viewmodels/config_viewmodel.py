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
    - reset_completed(reset_type: str)

  Protocol Messages:
    - sys_config_get_t / _set_t / _resp_t         (10, 11, 12)
    - sys_ranging_cfg_get_t / _set_t / _resp_t     (13, 14, 15)
    - sensor_fusion_cfg_get_t / _set_t / _resp_t   (21, 22, 23)
    - anchor_layout_get_t / _set_t / _resp_t       (43, 44, 45)
    - ble_conn_params_get_t / _set_t / _resp_t     (47, 48, 49)
    - device_reset_t (24), uwb_reset_t (25), factory_config_reset_t (26)
===============================================================================
"""
import logging
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

log = logging.getLogger(__name__)


class ConfigViewModel(QObject):
    # Signals for View updates
    anchor_layout_updated = pyqtSignal(list)
    sys_config_updated = pyqtSignal(dict)
    sys_ranging_cfg_updated = pyqtSignal(dict)
    sensor_fusion_cfg_updated = pyqtSignal(dict)
    prefilter_cfg_updated = pyqtSignal(dict)
    pos_calib_cfg_updated = pyqtSignal(dict)
    ble_conn_params_updated = pyqtSignal(dict)
    scan_devices_updated = pyqtSignal(list)
    device_type_updated = pyqtSignal(int)

    def __init__(self, device_model, ranging_model, command_bus=None, ble_scan_repo=None, parent=None):
        super().__init__(parent)
        self.model = device_model
        self.ranging_model = ranging_model
        self._ble_scan_repo = ble_scan_repo
        self._bulk_targets: list[dict] = []
        self._bulk_snapshot: dict | None = None
        self._bulk_index = 0
        self._bulk_timer = QTimer(self)
        self._bulk_timer.setSingleShot(True)
        self._bulk_timer.timeout.connect(self._write_next_bulk_target)

        # Bind Model parsed signals to ViewModel update signals
        from utils.app_state import shared_app_state
        self._shared_app_state = shared_app_state
        shared_app_state.anchor_layout_changed.connect(self.anchor_layout_updated.emit)
        shared_app_state.sys_config_changed.connect(self.sys_config_updated.emit)
        shared_app_state.sys_ranging_cfg_changed.connect(self.sys_ranging_cfg_updated.emit)
        shared_app_state.sensor_fusion_cfg_changed.connect(self.sensor_fusion_cfg_updated.emit)
        shared_app_state.prefilter_cfg_changed.connect(self.prefilter_cfg_updated.emit)
        shared_app_state.pos_calib_cfg_changed.connect(self.pos_calib_cfg_updated.emit)
        if hasattr(self.model, "ble_conn_params_parsed"):
            self.model.ble_conn_params_parsed.connect(self.ble_conn_params_updated.emit)
        shared_app_state.device_type_changed.connect(self.device_type_updated.emit)
        if self._ble_scan_repo:
            self._ble_scan_repo.scan_results_updated.connect(self.scan_devices_updated.emit)

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
        if self._shared_app_state.prefilter_cfg:
            self.prefilter_cfg_updated.emit(self._shared_app_state.prefilter_cfg)
        if self._shared_app_state.pos_calib_cfg:
            self.pos_calib_cfg_updated.emit(self._shared_app_state.pos_calib_cfg)
        if self._shared_app_state.device_type:
            self.device_type_updated.emit(self._shared_app_state.device_type)
        if self._ble_scan_repo:
            self.scan_devices_updated.emit(self._ble_scan_repo.merged_results())

    # ── Command Triggers (called by View) ───────────────────────────

    def read_device_config(self, target: dict | None = None, force: bool = True):
        """Read all config groups after the selected target is connected.

        Khi người dùng bấm thủ công, luôn dùng force=True để bypass cache
        và đảm bảo gói tin được gửi xuống phần cứng thật.
        """
        target = dict(target or {})

        def operation():
            log.info("Read from Device requested (force=%s) for target: %s", force, target)
            if hasattr(self.model, "retry_failed_connected_device_queries"):
                return bool(self.model.retry_failed_connected_device_queries("read from device button"))
            if hasattr(self.model, "refresh_connected_device_from_hardware"):
                return bool(self.model.refresh_connected_device_from_hardware("read from device button"))
            self.read_anchor_layout(force=force, traffic_class="bootstrap")
            self.read_ranging_config(force=force, traffic_class="bootstrap")
            self.read_sys_config(force=force, traffic_class="bootstrap")
            self.read_sensor_fusion_config(force=force, traffic_class="bootstrap")
            self.read_prefilter_config(force=force, traffic_class="bootstrap")
            self.read_pos_calib_config(force=force, traffic_class="bootstrap")
            self.read_ble_conn_params(force=force, traffic_class="bootstrap")
            self.read_device_type(force=force, traffic_class="bootstrap")
            self.read_calibration_status(force=force, traffic_class="bootstrap")  # calib_status_get
            return True

        return operation()

    def write_device_config(
        self,
        target: dict | None,
        anchors: list | None = None,
        ranging_config: dict | None = None,
        sys_config: dict | None = None,
        sensor_fusion_config: dict | None = None,
        prefilter_config: dict | None = None,
        pos_calib_config: dict | None = None,
        ble_conn_params_config: dict | None = None,
        ble_adv_config: dict | None = None,
        device_type: int | None = None,
        host_transport: int | None = None,
        factory_otp_config: dict | None = None,
    ):
        """Write one captured UI snapshot to the selected target with ACK-based sequencing."""
        target = dict(target or {})
        log.info("Writing selected config for target: %s", target)

        steps: list[dict] = []
        if anchors is not None:
            anchors_copied = [dict(anchor) for anchor in anchors]
            steps.append({
                "label": "anchor_layout_set",
                "command": "anchor_layout_set",
                "method": "set_anchor_layout",
                "args": [anchors_copied],
            })
        if ranging_config is not None:
            ranging_params = dict(ranging_config or {})
            steps.append({
                "label": "sys_ranging_cfg_set",
                "command": "sys_ranging_cfg_set",
                "method": "set_ranging_config",
                "kwargs": {
                    "period_ms": ranging_params.get("period_ms", 0),
                    "timeout_ms": ranging_params.get("timeout_ms", 0),
                },
            })
        if sys_config is not None:
            steps.append({
                "label": "sys_config_set",
                "command": "sys_config_set",
                "method": "set_sys_config",
                "args": [dict(sys_config or {})],
            })
        if sensor_fusion_config is not None:
            steps.append({
                "label": "sensor_fusion_cfg_set",
                "command": "sensor_fusion_cfg_set",
                "method": "set_sensor_fusion_config",
                "args": [dict(sensor_fusion_config or {})],
            })
        if prefilter_config is not None:
            steps.append({
                "label": "prefilter_cfg_set",
                "command": "prefilter_cfg_set",
                "method": "set_prefilter_config",
                "args": [dict(prefilter_config or {})],
            })
        if pos_calib_config:
            steps.append({
                "label": "pos_calib_cfg_set",
                "command": "pos_calib_cfg_set",
                "method": "set_pos_calib_config",
                "args": [dict(pos_calib_config or {})],
            })
        if ble_adv_config is not None:
            params = dict(ble_adv_config or {})
            steps.append({
                "label": "ble_adv_config_set",
                "command": "ble_adv_config_set",
                "method": "set_ble_adv_config",
                "kwargs": {
                    "enable": bool(params.get("enable", True)),
                    "serial_number": int(params.get("serial_number", 0) or 0),
                    "device_name": str(params.get("device_name", "")),
                },
            })
        if ble_conn_params_config is not None:
            params = dict(ble_conn_params_config or {})
            steps.append({
                "label": "ble_conn_params_set",
                "command": "ble_conn_params_set",
                "method": "set_ble_conn_params",
                "kwargs": {
                    "min_interval_ms": params.get("min_interval_ms", 20),
                    "max_interval_ms": params.get("max_interval_ms", 40),
                    "slave_latency": params.get("slave_latency", 0),
                    "sup_timeout_ms": params.get("sup_timeout_ms", 3000),
                },
            })
        if device_type is not None:
            steps.append({
                "label": "device_type_set",
                "command": "device_type_set",
                "method": "set_device_type",
                "args": [int(device_type)],
            })
        if host_transport is not None:
            steps.append({
                "label": "host_transport_set",
                "command": "host_transport_set",
                "method": "set_host_transport",
                "args": [int(host_transport)],
            })
        if factory_otp_config:
            params = dict(factory_otp_config or {})
            steps.append({
                "label": "factory_otp_write",
                "command": "factory_otp_write",
                "method": "write_factory_otp",
                "kwargs": {
                    "confirm_magic": params.get("confirm_magic", 0x4F545057),
                    "otp_type": params.get("otp_type", 0),
                    "device_type": params.get("device_type", 2),
                    "tx_antenna_delay": params.get("tx_antenna_delay", 0),
                    "rx_antenna_delay": params.get("rx_antenna_delay", 0),
                    "value_u32": params.get("value_u32", 0),
                    "value_u8": params.get("value_u8", 0),
                },
            })

        if hasattr(self.model, "write_config_sequence"):
            return bool(self.model.write_config_sequence(steps))
        log.warning("DeviceModel does not support ACK-based config write sequence.")
        return False

    def write_factory_otp(
        self,
        confirm_magic: int = 0x4F545057,
        otp_type: int = 0,
        device_type: int = 2,
        tx_antenna_delay: int = 0,
        rx_antenna_delay: int = 0,
        value_u32: int = 0,
        value_u8: int = 0,
    ):
        log.info("Sending factory OTP write command to MCU: type=%d", otp_type)
        self.model.write_factory_otp(
            confirm_magic=confirm_magic,
            otp_type=otp_type,
            device_type=device_type,
            tx_antenna_delay=tx_antenna_delay,
            rx_antenna_delay=rx_antenna_delay,
            value_u32=value_u32,
            value_u8=value_u8,
        )


    def read_anchor_layout(self, force: bool = False, traffic_class: str = ""):
        # BE/API: fetch anchor layout for the Config tab.
        log.info("Requesting anchor layout from MCU via global query queue... (force=%s)", force)
        self.model.request_anchor_layout(force=force, traffic_class=traffic_class)

    def write_anchor_layout(self, anchors: list):
        # BE/API: persist anchor layout from Config tab.
        log.info("Sending anchor layout set command to MCU: %s", anchors)
        self.ranging_model.set_anchor_layout(anchors)
        self.model.set_anchor_layout(anchors)

    def read_ranging_config(self, force: bool = False, traffic_class: str = ""):
        # BE/API: fetch ranging config for the Config tab.
        log.info("Requesting system ranging config from MCU via global query queue... (force=%s)", force)
        self.model.request_ranging_config(force=force, traffic_class=traffic_class)

    def write_ranging_config(self, period_ms: int, timeout_ms: int):
        # BE/API: update ranging config from Config tab.
        log.info("Sending ranging config set command to MCU: period=%d ms, timeout=%d ms", period_ms, timeout_ms)
        self.model.set_ranging_config(period_ms=period_ms, timeout_ms=timeout_ms)

    def read_sys_config(self, force: bool = False, traffic_class: str = ""):
        # BE/API: fetch UWB system config for the Config tab.
        log.info("Requesting system configuration from MCU via global query queue... (force=%s)", force)
        self.model.request_sys_config(force=force, traffic_class=traffic_class)

    def write_sys_config(self, config_data: dict):
        # BE/API: update UWB system config from Config tab.
        params = dict(config_data or {})
        log.info("Sending sys config set command to MCU: %s", params)
        self.model.set_sys_config(params)

    def read_sensor_fusion_config(self, force: bool = False, traffic_class: str = ""):
        # BE/API: fetch sensor-fusion config for the Config tab.
        log.info("Requesting sensor fusion configuration from MCU via global query queue... (force=%s)", force)
        self.model.request_sensor_fusion_config(force=force, traffic_class=traffic_class)

    def write_sensor_fusion_config(self, config_data: dict):
        # BE/API: update sensor-fusion config from Config tab.
        params = dict(config_data or {})
        log.info("Sending sensor fusion config set command to MCU: %s", params)
        self.model.set_sensor_fusion_config(params)

    def read_prefilter_config(self, force: bool = False, traffic_class: str = ""):
        # BE/API: fetch positioning prefilter config for the Config tab.
        log.info("Requesting prefilter configuration from MCU via global query queue... (force=%s)", force)
        self.model.request_prefilter_config(force=force, traffic_class=traffic_class)

    def write_prefilter_config(self, config_data: dict):
        # BE/API: update positioning prefilter config from Config tab.
        params = dict(config_data or {})
        log.info("Sending prefilter config set command to MCU: %s", params)
        self.model.set_prefilter_config(params)
    def read_pos_calib_config(self, force: bool = False, traffic_class: str = ""):
        # BE/API: fetch position-calibration config for the Config tab.
        log.info("Requesting position calibration configuration from MCU via global query queue... (force=%s)", force)
        self.model.request_pos_calib_config(force=force, traffic_class=traffic_class)

    def write_pos_calib_config(self, config_data: dict):
        # BE/API: update position-calibration config from Config tab.
        params = dict(config_data or {})
        log.info("Sending position calibration config set command to MCU: %s", params)
        self.model.set_pos_calib_config(params)

    def write_ble_conn_params(
        self,
        min_interval_ms: int,
        max_interval_ms: int,
        slave_latency: int,
        sup_timeout_ms: int,
    ):
        # BE/API: update BLE connection parameters on the central device (Dongle).
        log.info("Sending BLE connection params set command to Central: min=%d, max=%d, latency=%d, timeout=%d",
                 min_interval_ms, max_interval_ms, slave_latency, sup_timeout_ms)
        self.model.set_ble_conn_params(
            min_interval_ms=min_interval_ms,
            max_interval_ms=max_interval_ms,
            slave_latency=slave_latency,
            sup_timeout_ms=sup_timeout_ms,
        )

    def read_ble_conn_params(self, force: bool = False, traffic_class: str = ""):
        log.info("Requesting BLE connection parameters from Central via global query queue... (force=%s)", force)
        if hasattr(self.model, "request_ble_conn_params"):
            self.model.request_ble_conn_params(force=force, traffic_class=traffic_class)

    def write_ble_adv_config(self, enable: bool, serial_number: int, device_name: str):
        log.info("Sending BLE advertising config: enable=%s, serial=%d, name=%s", enable, serial_number, device_name)
        self.model.set_ble_adv_config(enable=enable, serial_number=serial_number, device_name=device_name)

    def read_device_type(self, force: bool = False, traffic_class: str = ""):
        log.info("Requesting device type from MCU... (force=%s)", force)
        self.model.request_device_type(force=force, traffic_class=traffic_class)

    def read_calibration_status(self, force: bool = False, traffic_class: str = ""):
        # BE/API: fetch calibration status for the Config tab.
        log.info("Requesting calibration status from MCU via global query queue... (force=%s)", force)
        self.model.request_calibration_status(force=force, traffic_class=traffic_class)

    def write_device_type(self, device_type: int):
        log.info("Sending set device type command: %d", device_type)
        self.model.set_device_type(device_type)

    def set_host_transport(self, transport: int):
        log.info("Sending host transport set command: transport=%d", transport)
        self.model.set_host_transport(transport)

    def write_all_device_configs(self, targets: list[dict], snapshot: dict, delay_ms: int = 2500):
        _ = targets
        _ = snapshot
        _ = delay_ms
        log.warning("Broadcast write is not implemented yet; ignoring write_all_device_configs request.")
        self._bulk_targets = []
        self._bulk_snapshot = None
        self._bulk_index = 0
        self._bulk_timer.stop()
        return False

    def _write_next_bulk_target(self):
        self._bulk_targets = []
        self._bulk_snapshot = None
        self._bulk_index = 0
        self._bulk_timer.stop()
        return False

    def device_reset(self):
        # BE/API: device lifecycle action from Config tab.
        log.warning("Sending device_reset command to MCU...")
        self.model.request_device_reset()

    def uwb_reset(self):
        # BE/API: device lifecycle action from Config tab.
        log.warning("Sending uwb_reset command to MCU...")
        self.model.request_uwb_reset()

    def factory_reset(self):
        # BE/API: device lifecycle action from Config tab.
        log.warning("Sending factory_config_reset command to MCU...")
        self.model.request_factory_config_reset()

    def enter_bootloader(self):
        # BE/API: device lifecycle action from Config tab.
        log.warning("Sending enter_to_bootloader command to MCU...")
        self.model.request_enter_bootloader()
