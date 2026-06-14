"""
Repository for telemetry packets such as battery and MCU/UWB/IMU health.
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from utils.app_state import shared_app_state


class TelemetryRepository(QObject):
    telemetry_updated = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._latest_by_device: dict[str, dict] = {}

    def handle_packet(self, param_name: str, pkt, device_key: str = "") -> bool:
        if param_name == "battery_info_resp":
            self.save_battery_info(device_key or "default", self.parse_battery_info(pkt.battery_info_resp))
            return True
        return False

    def parse_battery_info(self, resp) -> dict:
        return {
            "bat_voltage_mv": int(getattr(resp, "bat_voltage_mv", 0)),
            "bat_soc_percent": int(getattr(resp, "bat_soc_percent", 0)),
            "remaining_min": int(getattr(resp, "remaining_min", 0)),
            "is_charging": bool(getattr(resp, "is_charging", False)),
            "mcu_temp_c": float(getattr(resp, "mcu_temp_c", 0.0)),
            "mcu_voltage_mv": int(getattr(resp, "mcu_voltage_mv", 0)),
            "vdda_mv": int(getattr(resp, "mcu_voltage_mv", 0)),
            "uwb_temp_c": float(getattr(resp, "uwb_temp_c", 0.0)),
            "uwb_voltage_mv": int(getattr(resp, "uwb_voltage_mv", 0)),
            "uwb_vbat_mv": int(getattr(resp, "uwb_voltage_mv", 0)),
            "imu_temp_c": float(getattr(resp, "imu_temp_c", 0.0)),
            "error_mask": int(getattr(resp, "error_mask", 0)),
        }

    def save_battery_info(self, device_key: str, info: dict) -> None:
        data = info.copy()
        data["device_key"] = device_key
        self._latest_by_device[device_key] = data
        shared_app_state.battery_info = data
        self.telemetry_updated.emit(data)

    def latest(self, device_key: str = "default") -> dict:
        return self._latest_by_device.get(device_key, {}).copy()
