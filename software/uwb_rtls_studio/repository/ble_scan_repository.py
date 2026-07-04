"""
Repository/cache for BLE scan and advertising status packets.
"""
from __future__ import annotations

import re
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
        timestamp_ms = int(getattr(res, "local_timestamp_ms", 0) or 0)
        timestamp_s = int(getattr(res, "local_timestamp_s", 0) or 0)
        if timestamp_ms <= 0 and timestamp_s > 0:
            timestamp_ms = timestamp_s * 1000
        elif timestamp_s <= 0 and timestamp_ms > 0:
            timestamp_s = timestamp_ms // 1000
        return {
            "device_type": int(getattr(res, "device", 0)),
            "device_id": int(getattr(res, "device_id", 0)),
            "bat_soc_percent": int(getattr(res, "bat_soc_percent", 0)),
            "local_timestamp_s": timestamp_s,
            "local_timestamp_ms": timestamp_ms,
            "status_flags": int(getattr(res, "status_flags", 0)),
            "warning_count": int(getattr(res, "warning_count", 0)),
            "error_count": int(getattr(res, "error_count", 0)),
            "last_seen": time.monotonic(),
        }

    def save_scan_result(self, data: dict) -> None:
        mac = data.get("mac")
        if not mac:
            return
        mac = self._normalize_mac(mac)
        data = dict(data)
        data["mac"] = mac
        if mac not in self._device_order:
            self._device_order[mac] = self._next_device_order
            self._next_device_order += 1
        current = self._devices.get(mac, {})
        current.update(data)
        current["order"] = self._device_order[mac]
        for candidate in self._merge_candidates(current):
            if candidate in self._adv_status_by_device_id:
                current.update(self._adv_status_by_device_id[candidate])
                break
        self._devices[mac] = current
        self.scan_results_updated.emit(self.merged_results())

    def seed_devices(self, devices: list[dict], emit: bool = True) -> None:
        """Import a scan snapshot captured before this repository had listeners."""
        for dev in devices or []:
            data = dict(dev or {})
            mac = self._normalize_mac(data.get("mac", ""))
            if not mac:
                continue
            data["mac"] = mac
            data["last_seen"] = time.monotonic()
            if "serial_number" not in data and data.get("serial"):
                try:
                    data["serial_number"] = int(str(data.get("serial")), 0)
                except (TypeError, ValueError):
                    data["serial_number"] = 0
            if data.get("serial_number") and not data.get("serial"):
                data["serial"] = f"0x{int(data.get('serial_number')):08X}"
            if mac not in self._device_order:
                order = data.get("order")
                if order is None:
                    order = self._next_device_order
                self._device_order[mac] = int(order)
                self._next_device_order = max(self._next_device_order, int(order) + 1)
            current = self._devices.get(mac, {})
            current.update(data)
            current["order"] = self._device_order[mac]
            self._devices[mac] = current
        if emit:
            self.scan_results_updated.emit(self.merged_results())

    def get_device(self, mac: str) -> dict:
        return self._devices.get(self._normalize_mac(mac), {}).copy()

    def save_adv_status(self, data: dict) -> None:
        device_id = data.get("device_id")
        if device_id is None:
            return
        self._adv_status_by_device_id[device_id] = data.copy()
        for device in self._devices.values():
            if device_id in self._merge_candidates(device):
                device.update(data)
        self.scan_results_updated.emit(self.merged_results())

    def merged_results(self) -> list[dict]:
        results = [d.copy() for d in self._devices.values()]
        results.sort(key=lambda item: item.get("order", 0))
        return results

    def prune_stale_devices(self, timeout_s: float) -> None:
        # Device rows are intentionally retained after discovery so the main
        # window keeps the full scan snapshot until the user rescans or exits.
        now = time.monotonic()
        stale_ids = [
            device_id for device_id, data in self._adv_status_by_device_id.items()
            if now - data.get("last_seen", 0) > timeout_s
        ]
        for device_id in stale_ids:
            del self._adv_status_by_device_id[device_id]

        if stale_ids:
            self.scan_results_updated.emit(self.merged_results())

    def reset_for_rescan(self, preserve_devices: list[dict] | None = None, emit: bool = True) -> None:
        self._devices.clear()
        self._adv_status_by_device_id.clear()
        self._device_order.clear()
        self._next_device_order = 0
        self.seed_devices(preserve_devices or [], emit=False)
        if emit:
            self.scan_results_updated.emit(self.merged_results())

    def clear(self) -> None:
        self._devices.clear()
        self._adv_status_by_device_id.clear()
        self._device_order.clear()
        self._next_device_order = 0
        self.scan_results_updated.emit([])
    @staticmethod
    def _normalize_mac(mac: str) -> str:
        return str(mac or "").strip().replace("-", ":").upper()

    @staticmethod
    def _device_name_candidate(device: dict) -> int:
        name = str(device.get("name") or "").strip()
        match = re.search(r"(\d+)$", name)
        if not match:
            return 0
        try:
            return int(match.group(1))
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _merge_candidates(cls, device: dict) -> tuple[int, ...]:
        serial_number = int(device.get("serial_number") or 0)
        device_id = int(device.get("device_id") or 0)
        name_candidate = cls._device_name_candidate(device)
        candidates = []
        for candidate in (
            device_id,
            serial_number,
            serial_number & 0xFFFF if serial_number else 0,
            name_candidate,
        ):
            if candidate and candidate not in candidates:
                candidates.append(candidate)
        return tuple(candidates)
