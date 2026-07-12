"""Runtime mode switches shared by the app and local test scripts.

Change UWB_RTLS_TEST_MODE to choose how the desktop app boots:
  1 = offline/mock test mode: skip dongle+scan popups and use fake data.
  0 = real hardware mode: require dongle, scan device, protobuf commands, realtime data.

The UWB_RTLS_TEST_MODE environment variable can override this file when needed.
"""
from __future__ import annotations

import os
from typing import Any

UWB_RTLS_TEST_MODE = 1

_TRUE_VALUES = {"1", "true", "yes", "on", "test", "mock"}
_FALSE_VALUES = {"0", "false", "no", "off", "real", "hardware"}


def _parse_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    return default


def is_test_mode() -> bool:
    """Return True for offline/mock mode, False for real hardware mode."""
    import sys
    if getattr(sys, 'frozen', False):
        return False
    return _parse_bool(os.getenv("UWB_RTLS_TEST_MODE"), bool(UWB_RTLS_TEST_MODE))


def mode_label() -> str:
    return "TEST/MOCK" if is_test_mode() else "REAL/HARDWARE"


def mock_device_identity() -> tuple[str, str]:
    return "Mock Device", "00:11:22:33:44:55"


def seed_mock_app_state(shared_app_state, device_name: str | None = None, mac_address: str | None = None) -> None:
    """Seed UI-facing state for offline testing without touching serial/protobuf IO."""
    name, mac = mock_device_identity()
    device_name = device_name or name
    mac_address = mac_address or mac

    shared_app_state.connected_device = {
        "mac_address": mac_address,
        "device_name": device_name,
        "Role": "TAG",
        "Type": "Tag",
        "device_role": "TAG",
        "fw_version": "offline-mock",
        "hw_version": "offline-mock",
        "serial_number": "MOCK-0001",
    }
    shared_app_state.battery_info = {
        "bat_voltage_mv": 3820,
        "bat_soc_percent": 92,
        "remaining_min": 360,
        "is_charging": False,
        "mcu_temp_c": 27.5,
        "mcu_voltage_mv": 3300,
        "uwb_temp_c": 32.0,
        "uwb_voltage_mv": 3290,
        "imu_temp_c": 28.0,
        "error_mask": 0,
    }
    shared_app_state.rtos_resource = mock_rtos_resource()
    shared_app_state.rtos_task_stats = mock_rtos_task_stats()


def mock_rtos_resource() -> dict[str, Any]:
    return {
        "sample_window_ms": 1000,
        "cpu_busy_permille": 80,
        "cpu_busy_percent": 8.0,
        "heap_free_bytes": 18536,
        "heap_min_ever_free_bytes": 14336,
        "min_stack_free_bytes": 9,
        "min_stack_task_id": 4,
        "task_count": 6,
        "health_flags": 0,
    }


def mock_rtos_task_stats() -> list[dict[str, Any]]:
    return [
        {"task_id": 1, "cpu_permille": 460, "cpu_percent": 46.0, "stack_min_free_bytes": 1024, "name": "Logger"},
        {"task_id": 2, "cpu_permille": 80, "cpu_percent": 8.0, "stack_min_free_bytes": 768, "name": "Network"},
        {"task_id": 3, "cpu_permille": 130, "cpu_percent": 13.0, "stack_min_free_bytes": 640, "name": "IO"},
        {"task_id": 4, "cpu_permille": 490, "cpu_percent": 49.0, "stack_min_free_bytes": 9, "name": "UwbRanging"},
        {"task_id": 5, "cpu_permille": 10, "cpu_percent": 1.0, "stack_min_free_bytes": 896, "name": "Tmr Svc"},
        {"task_id": 0, "cpu_permille": 920, "cpu_percent": 92.0, "stack_min_free_bytes": 2048, "name": "IDLE"},
    ]
