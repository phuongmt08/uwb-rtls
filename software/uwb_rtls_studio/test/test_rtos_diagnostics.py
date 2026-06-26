from __future__ import annotations

import os
import sys


CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
STUDIO_DIR = os.path.dirname(CURRENT_DIR)
SOFTWARE_DIR = os.path.dirname(STUDIO_DIR)

if STUDIO_DIR not in sys.path:
    sys.path.insert(0, STUDIO_DIR)
if SOFTWARE_DIR not in sys.path:
    sys.path.insert(0, SOFTWARE_DIR)

from common.transport import VvAddress
from common.commands import CommandFactory
from repository.diagnostics_repository import DiagnosticsRepository


def test_rtos_resource_response_is_parsed_for_heap_stack_cpu():
    pkt = CommandFactory().rtos_resource_resp(
        src=int(VvAddress.MCU),
        dst=int(VvAddress.HOST),
        seq=1,
    )
    pkt.rtos_resource_resp.sample_window_ms = 1000
    pkt.rtos_resource_resp.cpu_busy_permille = 375
    pkt.rtos_resource_resp.heap_free_bytes = 18536
    pkt.rtos_resource_resp.heap_min_ever_free_bytes = 14336
    pkt.rtos_resource_resp.min_stack_free_bytes = 9
    pkt.rtos_resource_resp.min_stack_task_id = 4
    pkt.rtos_resource_resp.task_count = 6
    pkt.rtos_resource_resp.health_flags = 0x02

    parsed = DiagnosticsRepository().parse_rtos_resource(pkt.rtos_resource_resp)

    assert parsed == {
        "sample_window_ms": 1000,
        "cpu_busy_permille": 375,
        "cpu_busy_percent": 37.5,
        "heap_free_bytes": 18536,
        "heap_min_ever_free_bytes": 14336,
        "min_stack_free_bytes": 9,
        "min_stack_task_id": 4,
        "task_count": 6,
        "health_flags": 0x02,
    }


def test_rtos_task_stats_response_is_parsed_for_per_task_cpu_stack():
    pkt = CommandFactory().rtos_task_stats_resp(
        src=int(VvAddress.MCU),
        dst=int(VvAddress.HOST),
        seq=2,
    )
    del pkt.rtos_task_stats_resp.tasks[:]
    task = pkt.rtos_task_stats_resp.tasks.add()
    task.task_id = 4
    task.cpu_permille = 490
    task.stack_min_free_bytes = 9
    task.name = "UwbRanging"

    parsed = DiagnosticsRepository().parse_rtos_task_stats(pkt.rtos_task_stats_resp)

    assert parsed == [
        {
            "task_id": 4,
            "cpu_permille": 490,
            "cpu_percent": 49.0,
            "stack_min_free_bytes": 9,
            "name": "UwbRanging",
        }
    ]