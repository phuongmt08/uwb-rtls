from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import serial
from serial.tools import list_ports


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
STUDIO = ROOT / "uwb_rtls_studio"
if str(STUDIO) not in sys.path:
    sys.path.insert(0, str(STUDIO))

from common.commands import CommandFactory  # noqa: E402
from common.transport import VvAddress, VvProtocol  # noqa: E402
from repository.diagnostics_repository import DiagnosticsRepository  # noqa: E402
from utils.runtime_mode import is_test_mode, mock_rtos_resource, mock_rtos_task_stats, mode_label  # noqa: E402


def _fmt_bytes(value):
    if value is None:
        return "--"
    value = int(value)
    if value >= 1024:
        return f"{value / 1024.0:.1f} KB"
    return f"{value} B"


def _fmt_task(task: dict) -> str:
    name = task.get("name") or f"T{task.get('task_id', 0)}"
    cpu = task.get("cpu_percent")
    stack = task.get("stack_min_free_bytes")
    return f"{name}: cpu={float(cpu):.1f}% stack={stack}"


def build_summary(resource: dict, tasks: list[dict]) -> dict:
    idle = next((t for t in tasks if str(t.get("name", "")).lower().find("idle") >= 0), None)
    idle_cpu = float(idle.get("cpu_percent", 0.0)) if idle else None
    active_cpu = max(0.0, min(100.0, 100.0 - idle_cpu)) if idle_cpu is not None else resource.get("cpu_busy_percent")
    stack_values = [int(t.get("stack_min_free_bytes", 0)) for t in tasks if t.get("stack_min_free_bytes") is not None]
    avg_stack = (sum(stack_values) / len(stack_values)) if stack_values else None
    return {
        "heap_free": _fmt_bytes(resource.get("heap_free_bytes")),
        "heap_min_ever_free": _fmt_bytes(resource.get("heap_min_ever_free_bytes")),
        "stack_min_free": _fmt_bytes(resource.get("min_stack_free_bytes")),
        "stack_avg_free": _fmt_bytes(avg_stack),
        "cpu_busy": f"{float(resource.get('cpu_busy_percent', 0.0)):.1f}%",
        "cpu_active": f"{float(active_cpu):.1f}%" if active_cpu is not None else "--",
        "task_count": int(resource.get("task_count", len(tasks))),
        "health_flags": f"0x{int(resource.get('health_flags', 0)):X}",
    }


def run_mock() -> int:
    resource = mock_rtos_resource()
    tasks = mock_rtos_task_stats()
    print(f"[MODE] {mode_label()}")
    print("TX rtos_resource_get [mock]")
    print("RX rtos_resource_resp", json.dumps(resource, ensure_ascii=False))
    print("TX rtos_task_stats_get [mock]")
    print("RX rtos_task_stats_resp")
    for task in tasks:
        print(" ", _fmt_task(task))
    print("SUMMARY", json.dumps(build_summary(resource, tasks), ensure_ascii=False))
    return 0

def list_serial_ports() -> None:
    ports = list(list_ports.comports())
    if not ports:
        print("No serial ports detected.")
        return
    for p in ports:
        print(f"{p.device:>8}  {p.description}")


def run(port: str | None, baud: int, interval_s: float, repeat: bool, dry_run: bool) -> int:
    factory = CommandFactory()
    proto = VvProtocol()
    repo = DiagnosticsRepository()

    if is_test_mode():
        return run_mock()

    if dry_run:
        for name in ("rtos_resource_get", "rtos_task_stats_get"):
            pkt = getattr(factory, name)(VvAddress.HOST, VvAddress.MCU, 1)
            print(f"{name}: src={int(pkt.hdr.addr.src)} dst={int(pkt.hdr.addr.dst)} seq={int(pkt.hdr.seq)}")
        return 0

    if not port:
        list_serial_ports()
        return 2

    ser = serial.Serial(port=port, baudrate=baud, timeout=0.2, write_timeout=0.5)
    print(f"Opened {port} @ {baud}")

    try:
        seq = 1
        while True:
            for cmd_name in ("rtos_resource_get", "rtos_task_stats_get"):
                pkt = getattr(factory, cmd_name)(VvAddress.HOST, VvAddress.MCU, seq)
                seq += 1
                ser.write(proto.wrap_packet(pkt))
                print(f"TX {cmd_name}")

            deadline = time.time() + max(1.0, interval_s)
            got_resource = None
            got_tasks = None

            while time.time() < deadline and (got_resource is None or got_tasks is None):
                data = ser.read(512)
                if not data:
                    continue
                try:
                    packets = proto.decode_from_frames(data)
                except Exception as exc:
                    print(f"decode error: {exc}")
                    continue
                for pkt in packets:
                    param = pkt.WhichOneof("params")
                    if param == "rtos_resource_resp":
                        got_resource = repo.parse_rtos_resource(pkt.rtos_resource_resp)
                        print("RX rtos_resource_resp", json.dumps(got_resource, ensure_ascii=False))
                    elif param == "rtos_task_stats_resp":
                        got_tasks = repo.parse_rtos_task_stats(pkt.rtos_task_stats_resp)
                        print("RX rtos_task_stats_resp")
                        for task in got_tasks:
                            print(" ", _fmt_task(task))

            if got_resource or got_tasks:
                summary = build_summary(got_resource or {}, got_tasks or [])
                print("SUMMARY", json.dumps(summary, ensure_ascii=False))

            if not repeat:
                break
            time.sleep(interval_s)
    finally:
        ser.close()
        print("Closed serial port")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe RTOS diagnostics commands without modifying the app.")
    parser.add_argument("--port", help="Serial port, e.g. COM11")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--repeat", action="store_true", help="Keep querying every interval seconds")
    parser.add_argument("--dry-run", action="store_true", help="Only print packet headers; do not open serial")
    parser.add_argument("--list-ports", action="store_true", help="List serial ports and exit")
    args = parser.parse_args()

    if args.list_ports:
        list_serial_ports()
        return 0

    return run(args.port, args.baud, args.interval, args.repeat, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
