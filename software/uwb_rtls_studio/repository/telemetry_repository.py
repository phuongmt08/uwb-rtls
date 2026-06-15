"""
Repository for telemetry packets such as battery and MCU/UWB/IMU health.
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from utils.app_state import shared_app_state


class TelemetryRepository(QObject):
    telemetry_updated = pyqtSignal(dict)

    def __init__(self, telemetry_model=None, parent=None):
        super().__init__(parent)
        self._latest_by_device: dict[str, dict] = {}
        self._telemetry_model = telemetry_model

    def handle_packet(self, param_name: str, pkt, device_key: str = "") -> bool:
        if param_name == "battery_info_resp":
            self.save_battery_info(device_key or "default", self.parse_battery_info(pkt.battery_info_resp))
            return True
        return False

    def parse_battery_info(self, resp) -> dict:
        present_fields = {field.name for field, _ in resp.ListFields()}

        def value_or_none(name: str):
            if name not in present_fields:
                return None
            return getattr(resp, name)

        return {
            "bat_voltage_mv": value_or_none("bat_voltage_mv"),
            "bat_soc_percent": value_or_none("bat_soc_percent"),
            "remaining_min": value_or_none("remaining_min"),
            "is_charging": value_or_none("is_charging"),
            "mcu_temp_c": value_or_none("mcu_temp_c"),
            "mcu_voltage_mv": value_or_none("mcu_voltage_mv"),
            "vdda_mv": value_or_none("mcu_voltage_mv"),
            "uwb_temp_c": value_or_none("uwb_temp_c"),
            "uwb_voltage_mv": value_or_none("uwb_voltage_mv"),
            "uwb_vbat_mv": value_or_none("uwb_voltage_mv"),
            "imu_temp_c": value_or_none("imu_temp_c"),
            "error_mask": value_or_none("error_mask"),
        }

    def save_battery_info(self, device_key: str, info: dict) -> None:
        data = {key: value for key, value in info.items() if value is not None}
        data["device_key"] = device_key
        if self._telemetry_model:
            data = self._telemetry_model.handle_battery_info(data)
            data["device_key"] = device_key
        self._latest_by_device[device_key] = data
        shared_app_state.battery_info = data
        self.telemetry_updated.emit(data)

    def latest(self, device_key: str = "default") -> dict:
        return self._latest_by_device.get(device_key, {}).copy()
