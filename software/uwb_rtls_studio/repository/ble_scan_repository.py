"""
Repository/cache for BLE scan and advertising status packets.
"""
from __future__ import annotations

import logging
import re
import time

from PyQt6.QtCore import QObject, pyqtSignal

from utils.constants import DEVICE_TYPE_LABELS_SHORT

log = logging.getLogger(__name__)


class BleScanRepository(QObject):
    scan_results_updated = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._devices: dict[str, dict] = {}
        self._adv_status_by_key: dict[int, dict] = {}
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
        name = str(getattr(res, "name", "") or "").strip() or "-"
        serial_number = int(getattr(res, "serial_number", 0) or 0)
        return {
            "name": name,
            "mac": mac_hex,
            "rssi": int(getattr(res, "rssi_dbm", 0)),
            "serial_number": serial_number,
            "serial": f"0x{serial_number:08X}" if serial_number else "",
            "device_type": self._device_type_from_name(name),
            "last_seen": time.monotonic(),
        }

    def parse_adv_status(self, res) -> dict:
        timestamp_s = int(getattr(res, "local_timestamp_s", 0) or 0)
        timestamp_ms = int(getattr(res, "local_timestamp_ms", 0) or 0)
        if timestamp_ms <= 0 and timestamp_s > 0:
            timestamp_ms = timestamp_s * 1000
        elif timestamp_s <= 0 and timestamp_ms > 0:
            timestamp_s = timestamp_ms // 1000
        serial_number = int(getattr(res, "serial_number", 0) or 0)
        device_id = int(getattr(res, "device_id", 0) or 0)
        device_type = int(getattr(res, "device", 0) or 0)
        return {
            "device_type": device_type,
            "device_id": device_id,
            "serial_number": serial_number,
            "serial": f"0x{serial_number:08X}" if serial_number else "",
            "bat_soc_percent": int(getattr(res, "bat_soc_percent", 0) or 0),
            "local_timestamp_s": timestamp_s,
            "local_timestamp_ms": timestamp_ms,
            "status_flags": int(getattr(res, "status_flags", 0) or 0),
            "warning_count": int(getattr(res, "warning_count", 0) or 0),
            "error_count": int(getattr(res, "error_count", 0) or 0),
            "adv_status_seen": True,
            "last_seen": time.monotonic(),
            "name_hint": self._synthesized_name(device_type, device_id, serial_number),
        }

    def save_scan_result(self, data: dict) -> None:
        mac = self._normalize_mac(data.get("mac", ""))
        if not mac:
            return

        current = dict(self._devices.get(mac, {}))
        preserved_serial_number = int(current.get("serial_number") or 0)
        preserved_serial = str(current.get("serial") or "")

        merged = dict(data)
        merged["mac"] = mac
        if mac not in self._device_order:
            self._device_order[mac] = self._next_device_order
            self._next_device_order += 1
        merged["order"] = self._device_order[mac]

        current.update(merged)
        if not current.get("serial_number") and preserved_serial_number:
            current["serial_number"] = preserved_serial_number
        if current.get("serial_number") and not current.get("serial"):
            current["serial"] = f"0x{int(current.get('serial_number')):08X}"
        elif not current.get("serial") and preserved_serial:
            current["serial"] = preserved_serial
        if not current.get("device_type"):
            current["device_type"] = self._device_type_from_name(current.get("name", ""))

        self._apply_cached_adv_status(current)
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
            if not data.get("device_type"):
                data["device_type"] = self._device_type_from_name(data.get("name", ""))
            if mac not in self._device_order:
                order = data.get("order")
                if order is None:
                    order = self._next_device_order
                self._device_order[mac] = int(order)
                self._next_device_order = max(self._next_device_order, int(order) + 1)
            current = self._devices.get(mac, {})
            current.update(data)
            current["order"] = self._device_order[mac]
            self._apply_cached_adv_status(current)
            self._devices[mac] = current
        if emit:
            self.scan_results_updated.emit(self.merged_results())

    def get_device(self, mac: str) -> dict:
        return self._devices.get(self._normalize_mac(mac), {}).copy()

    def save_adv_status(self, data: dict) -> None:
        cached = dict(data)
        keys = self._adv_status_keys(cached)
        device_id = int(cached.get("device_id") or 0)
        serial_number = int(cached.get("serial_number") or 0)

        if not keys:
            if len(self._devices) == 1:
                only_mac = next(iter(self._devices))
                device = dict(self._devices[only_mac])
                self._merge_adv_payload(device, cached, match_key="single-device-fallback")
                self._devices[only_mac] = device
                self.scan_results_updated.emit(self.merged_results())
            else:
                log.info(
                    "BLE adv status has no device_id/serial_number; host cannot map it deterministically."
                )
            return

        for key in keys:
            self._adv_status_by_key[key] = cached.copy()

        updated = False
        for mac, device in list(self._devices.items()):
            current = dict(device)
            if self._apply_cached_adv_status(current):
                self._devices[mac] = current
                updated = True

        if updated:
            self.scan_results_updated.emit(self.merged_results())
        else:
            log.debug(
                "Cached BLE adv status awaiting scan match: device_id=%s serial=%s",
                device_id,
                serial_number,
            )

    def merged_results(self) -> list[dict]:
        results = [d.copy() for d in self._devices.values()]
        results.sort(key=lambda item: item.get("order", 0))
        return results

    def prune_stale_devices(self, timeout_s: float) -> None:
        # Device rows are intentionally retained after discovery so the main
        # window keeps the full scan snapshot until the user rescans or exits.
        now = time.monotonic()
        stale_keys = [
            key for key, data in self._adv_status_by_key.items()
            if now - data.get("last_seen", 0) > timeout_s
        ]
        for key in stale_keys:
            del self._adv_status_by_key[key]

        if stale_keys:
            self.scan_results_updated.emit(self.merged_results())

    def reset_for_rescan(self, preserve_devices: list[dict] | None = None, emit: bool = True) -> None:
        self._devices.clear()
        self._adv_status_by_key.clear()
        self._device_order.clear()
        self._next_device_order = 0
        self.seed_devices(preserve_devices or [], emit=False)
        if emit:
            self.scan_results_updated.emit(self.merged_results())

    def clear(self) -> None:
        self._devices.clear()
        self._adv_status_by_key.clear()
        self._device_order.clear()
        self._next_device_order = 0
        self.scan_results_updated.emit([])

    def _apply_cached_adv_status(self, device: dict) -> bool:
        for candidate in self._merge_candidates(device):
            adv_data = self._adv_status_by_key.get(candidate)
            if not adv_data:
                continue
            self._merge_adv_payload(device, adv_data, match_key=str(candidate))
            return True
        return False

    def _merge_adv_payload(self, device: dict, adv_data: dict, *, match_key: str = "") -> None:
        serial_number = int(device.get("serial_number") or adv_data.get("serial_number") or 0)
        if serial_number:
            device["serial_number"] = serial_number
            if not device.get("serial"):
                device["serial"] = f"0x{serial_number:08X}"
        elif not device.get("serial") and adv_data.get("serial"):
            device["serial"] = str(adv_data.get("serial") or "")

        device["device_id"] = int(adv_data.get("device_id") or device.get("device_id") or 0)
        device["device_type"] = int(adv_data.get("device_type") or device.get("device_type") or 0)
        device["bat_soc_percent"] = int(adv_data.get("bat_soc_percent", 0) or 0)
        device["local_timestamp_s"] = int(adv_data.get("local_timestamp_s", 0) or 0)
        device["local_timestamp_ms"] = int(adv_data.get("local_timestamp_ms", 0) or 0)
        device["status_flags"] = int(adv_data.get("status_flags", 0) or 0)
        device["warning_count"] = int(adv_data.get("warning_count", 0) or 0)
        device["error_count"] = int(adv_data.get("error_count", 0) or 0)
        device["adv_status_seen"] = True
        if match_key:
            device["adv_match_key"] = match_key

        current_name = str(device.get("name") or "").strip()
        if current_name in {"", "-"}:
            device["name"] = self._synthesized_name(
                int(device.get("device_type") or 0),
                int(device.get("device_id") or 0),
                int(device.get("serial_number") or 0),
            )

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

    @staticmethod
    def _device_type_from_name(name: str) -> int:
        text = str(name or "").strip().upper()
        if "ANCHOR" in text:
            return 2
        if "GATEWAY" in text:
            return 3
        if "DEBUG" in text:
            return 4
        if "TAG" in text:
            return 1
        return 0

    @staticmethod
    def _synthesized_name(device_type: int, device_id: int, serial_number: int) -> str:
        label = DEVICE_TYPE_LABELS_SHORT.get(int(device_type or 0), "DEV")
        if device_id:
            return f"{label}-{int(device_id)}"
        if serial_number:
            return f"{label}-0x{int(serial_number):08X}"
        return "-"

    @staticmethod
    def _adv_status_keys(data: dict) -> tuple[int, ...]:
        serial_number = int(data.get("serial_number") or 0)
        device_id = int(data.get("device_id") or 0)
        keys = []
        for candidate in (
            device_id,
            serial_number,
            serial_number & 0xFFFF if serial_number else 0,
        ):
            if candidate and candidate not in keys:
                keys.append(candidate)
        return tuple(keys)

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
