"""
Repository/cache for BLE scan and advertising status packets.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import QObject, pyqtSignal


class BleScanRepository(QObject):
    scan_results_updated = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._devices: dict[str, dict] = {}
        self._adv_status_by_device_id: dict[int, dict] = {}
        self._device_order: dict[str, int] = {}
        self._next_device_order = 0

    def handle_packet(self, param_name: str, pkt) -> bool:
        if param_name == "ble_scan_result":
            self.save_scan_result(self.parse_scan_result(pkt.ble_scan_result))
            return True
        if param_name == "ble_adv_status":
            self.save_adv_status(self.parse_adv_status(pkt.ble_adv_status))
            return True
        return False

    def parse_scan_result(self, res) -> dict:
        mac_hex = ":".join(f"{b:02X}" for b in res.mac_address)
        return {
            "name": res.name or f"UWB-{mac_hex[-5:]}",
            "mac": mac_hex,
            "rssi": int(getattr(res, "rssi_dbm", 0)),
            "serial_number": int(getattr(res, "serial_number", 0)),
            "serial": f"0x{int(getattr(res, 'serial_number', 0)):08X}" if getattr(res, "serial_number", 0) else "",
            "last_seen": time.monotonic(),
        }

    def parse_adv_status(self, res) -> dict:
        return {
            "device_type": int(getattr(res, "device", 0)),
            "device_id": int(getattr(res, "device_id", 0)),
            "bat_soc_percent": int(getattr(res, "bat_soc_percent", 0)),
            "local_timestamp_ms": int(getattr(res, "local_timestamp_ms", 0)),
            "status_flags": int(getattr(res, "status_flags", 0)),
            "warning_count": int(getattr(res, "warning_count", 0)),
            "error_count": int(getattr(res, "error_count", 0)),
            "last_seen": time.monotonic(),
        }

    def save_scan_result(self, data: dict) -> None:
        mac = data.get("mac")
        if not mac:
            return
        if mac not in self._device_order:
            self._device_order[mac] = self._next_device_order
            self._next_device_order += 1
        current = self._devices.get(mac, {})
        current.update(data)
        current["order"] = self._device_order[mac]
        serial_number = current.get("serial_number")
        if serial_number in self._adv_status_by_device_id:
            current.update(self._adv_status_by_device_id[serial_number])
        self._devices[mac] = current
        self.scan_results_updated.emit(self.merged_results())

    def save_adv_status(self, data: dict) -> None:
        device_id = data.get("device_id")
        if device_id is None:
            return
        self._adv_status_by_device_id[device_id] = data.copy()
        for device in self._devices.values():
            if device.get("serial_number") == device_id:
                device.update(data)
        self.scan_results_updated.emit(self.merged_results())

    def merged_results(self) -> list[dict]:
        results = [d.copy() for d in self._devices.values()]
        results.sort(key=lambda item: item.get("order", 0))
        return results

    def prune_stale_devices(self, timeout_s: float) -> None:
        now = time.monotonic()
        stale_macs = [
            mac for mac, data in self._devices.items()
            if now - data.get("last_seen", 0) > timeout_s
        ]
        for mac in stale_macs:
            del self._devices[mac]
            self._device_order.pop(mac, None)

        stale_ids = [
            device_id for device_id, data in self._adv_status_by_device_id.items()
            if now - data.get("last_seen", 0) > timeout_s
        ]
        for device_id in stale_ids:
            del self._adv_status_by_device_id[device_id]

        if stale_macs or stale_ids:
            self.scan_results_updated.emit(self.merged_results())

    def clear(self) -> None:
        self._devices.clear()
        self._adv_status_by_device_id.clear()
        self._device_order.clear()
        self._next_device_order = 0
        self.scan_results_updated.emit([])
