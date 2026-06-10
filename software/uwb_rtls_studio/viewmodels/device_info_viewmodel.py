"""
===============================================================================
  UWB RTLS Studio — Device Info ViewModel
===============================================================================
  File        : viewmodels/device_info_viewmodel.py
  Description : ViewModel cho tab "Device Info" (Tab 1).
                Hiển thị thông tin chi tiết device đang connected.

  MVVM Role   : VIEWMODEL — Presentation Logic Only

  Thread Model:
    - Main GUI Thread: Binds Model signals and View updates synchronously on this thread.
    - Lắng nghe Model signals → format data → emit UI signals
    - KHÔNG gọi protocol.send_command() trực tiếp
    - KHÔNG giữ duplicate state (connected_mac, is_scanning, etc.)
    - Tất cả state đều lấy từ DeviceModel

  Event-driven Architecture:
    - Host only fetches initial telemetry baseline once on connection.
    - All further updates are pushed by Firmware automatically.
===============================================================================
"""
import logging
import time
from PyQt6.QtCore import QObject, pyqtSignal, QTimer

log = logging.getLogger(__name__)


class DeviceInfoViewModel(QObject):
    """
    ViewModel for Device Info tab.

    Signals emitted (consumed by DeviceInfoTab View):
      - device_info_updated(dict)             : device identity fields
      - ble_info_updated(dict)                : BLE connection info
      - telemetry_updated(dict)               : battery + temperature + voltage
      - advertising_devices_updated(list,bool) : scan list + is_scanning flag
      - time_sync_updated(str, bool, bool)    : time_str, is_synced, is_syncing
    """

    # ── Signals cho View ─────────────────────────────────────────────
    device_info_updated = pyqtSignal(dict)
    ble_info_updated = pyqtSignal(dict)
    telemetry_updated = pyqtSignal(dict)
    advertising_devices_updated = pyqtSignal(list, bool)   # list of dicts, is_scanning
    time_sync_updated = pyqtSignal(str, bool, bool)        # local_time_str, is_synced, is_syncing

    def __init__(self, device_model, dongle_model=None, parent=None):
        super().__init__(parent)
        self.model = device_model
        self.dongle_model = dongle_model

        # ── Bind Model signals → ViewModel presentation ─────────────
        self.model.device_info_parsed.connect(self._on_device_info_parsed)
        self.model.battery_info_parsed.connect(self._on_battery_info_parsed)
        self.model.ble_status_parsed.connect(self._on_ble_status_parsed)
        self.model.ble_conn_params_parsed.connect(self._on_ble_conn_params_parsed)
        self.model.time_sync_result.connect(self._on_time_sync_result)
        self.model.scan_data_updated.connect(self._on_scan_data_updated)
        self.model.connection_state_changed.connect(self._on_connection_state_changed)

        # ── Handle Dongle Connection Lifecycle ───────────────────────
        if self.dongle_model:
            self.dongle_model.dongle_verified.connect(self._on_dongle_reconnected)


    # ═══════════════════════════════════════════════════════════════════
    #  INITIALIZATION
    # ═══════════════════════════════════════════════════════════════════

    def _delayed_init(self):
        """Called once after MainWindow has wired all signals."""
        if self.model.is_connected:
            # Re-emit the connected device info now that UI is listening
            self.device_info_updated.emit({
                "Device Name": self.model.connected_name,
                "MAC Address": self.model.connected_mac,
            })
            
            # Request initial telemetry since we just transitioned from ScanPopup
            self.model.request_initial_telemetry()
            
            # Start background scanning to populate 'Other Advertising Devices'
            self.model.start_scan()
        else:
            # No device connected yet — start scanning
            self.model.start_scan()





    # ═══════════════════════════════════════════════════════════════════
    #  PUBLIC METHODS (called by main.py or View)
    # ═══════════════════════════════════════════════════════════════════

    def set_connected_device(self, name: str, mac: str):
        """Called by main.py after ScanPopup finishes, to seed initial device info."""
        self.model.set_connected_device(name, mac)

    def connect_device(self, mac_hex: str):
        """Called by View when user clicks Connect on a scanned device."""
        self.model.connect_device(mac_hex)

    # ═══════════════════════════════════════════════════════════════════
    #  DONGLE LIFECYCLE
    # ═══════════════════════════════════════════════════════════════════

    def _on_dongle_reconnected(self, info_dict: dict):
        """Dongle auto-reconnected and verified."""
        log.info("Dongle auto-reconnected and verified.")
        if info_dict.get("verified"):
            self.model.start_scan()

    # ═══════════════════════════════════════════════════════════════════
    #  PRESENTATION LOGIC (Model signal → format → UI signal)
    # ═══════════════════════════════════════════════════════════════════

    def _on_device_info_parsed(self, data: dict):
        """Forward device info with connected device name/mac merged."""
        merged = {
            "Device Name": self.model.connected_name,
            "MAC Address": self.model.connected_mac,
        }
        merged.update(data)
        self.device_info_updated.emit(merged)

    def _on_battery_info_parsed(self, data: dict):
        """Format telemetry data before sending to View."""
        formatted_data = {
            "bat_soc_percent": data.get("bat_soc_percent", 0),
            "bat_voltage_str": f"{data.get('bat_voltage_mv', 0) / 1000.0:.2f}V",
            "remaining_str": f"{data.get('remaining_min', 0)} min",
            "charging_str": "Yes" if data.get("is_charging") else "No",
            "mcu_temp_str": f"{data.get('mcu_temp_c', 0):.1f} °C",
            "uwb_temp_str": f"{data.get('uwb_temp_c', 0):.1f} °C",
            "imu_temp_str": f"{data.get('imu_temp_c', 0):.1f} °C",
            "vdda_str": f"{data.get('vdda_mv', 0) / 1000.0:.2f}V",
            "uwb_vbat_str": f"{data.get('uwb_vbat_mv', 0) / 1000.0:.2f}V",
            "heap_usage": data.get("heap_usage", "-"),
            "stack_usage": data.get("stack_usage", "-"),
            "cpu_usage": data.get("cpu_usage", "-")
        }
        self.telemetry_updated.emit(formatted_data)

    def _on_ble_status_parsed(self, info: dict):
        """Forward BLE status to View."""
        self.ble_info_updated.emit({
            "state": info.get("state"),
            "rssi_dbm": info.get("rssi_dbm"),
        })

    def _on_ble_conn_params_parsed(self, params: dict):
        """Forward BLE connection parameters to View."""
        self.ble_info_updated.emit({
            "conn_interval": f"{params.get('min_interval_ms', 0)} - {params.get('max_interval_ms', 0)} ms",
            "slave_latency": params.get("slave_latency"),
            "supervision_timeout": params.get("sup_timeout_ms"),
        })

    def _on_connection_state_changed(self, info: dict):
        """Model reports connection state change → emit to View."""
        self.device_info_updated.emit({
            "Device Name": info.get("name", "-"),
            "MAC Address": info.get("mac", "-"),
            "Status": info.get("status", "Unknown"),
            "SwitchToLogTab": info.get("SwitchToLogTab", False),
        })

        if info.get("status") == "Connected" and info.get("SwitchToLogTab"):
            self.model.request_initial_telemetry()

    def _on_time_sync_result(self, data: dict):
        """Convert raw time sync data → formatted UI signal."""
        dev_time_ms = data["dev_time_ms"]
        is_synced = data["is_synced"]
        was_corrected = data["was_corrected"]

        # Format the device's time to a readable string
        try:
            dev_time_sec = dev_time_ms / 1000.0
            dt_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(dev_time_sec))
        except Exception:
            dt_str = "Invalid Time"

        # is_syncing = correction was just sent
        self.time_sync_updated.emit(dt_str, is_synced, was_corrected)

    def _on_scan_data_updated(self, merged_list: list):
        """Forward scan data to View with scanning state from Model."""
        self.advertising_devices_updated.emit(merged_list, self.model.is_scanning)
