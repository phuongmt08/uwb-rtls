#!/usr/bin/env python3
"""
Top-level BLE MCU log viewer.

This script opens the BLE Central Dongle serial port, scans/connects to the
UWB BLE peripheral, then streams MCU logs over BLE.

Examples:
  python software/vv_testings/test_ble_log.py --port COM28
  python software/vv_testings/test_ble_log.py --port COM28 --name UWB
  python software/vv_testings/test_ble_log.py --port COM28 --mac AA:BB:CC:DD:EE:FF
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def _load_gateway_ble_log():
    script_path = Path(__file__).resolve().parent / "gateway_test" / "test_ble_log.py"
    spec = importlib.util.spec_from_file_location("gateway_ble_log", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load BLE log implementation from {script_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    return int(_load_gateway_ble_log().main())


if __name__ == "__main__":
    raise SystemExit(main())
