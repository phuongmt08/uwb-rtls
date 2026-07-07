from __future__ import annotations

import os
import sys

from PyQt6.QtCore import QCoreApplication

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
STUDIO_DIR = os.path.dirname(CURRENT_DIR)
SOFTWARE_DIR = os.path.dirname(STUDIO_DIR)

if STUDIO_DIR not in sys.path:
    sys.path.insert(0, STUDIO_DIR)
if SOFTWARE_DIR not in sys.path:
    sys.path.insert(0, SOFTWARE_DIR)

from common.commands import CommandFactory
from common.transport import VvAddress
from services.query_state_machine import QueryQueueManager


def _ensure_qt_app():
    return QCoreApplication.instance() or QCoreApplication([])


def _pump_events(cycles: int = 3) -> None:
    app = _ensure_qt_app()
    for _ in range(cycles):
        app.processEvents()


def test_query_queue_marks_time_sync_set_success_on_matching_ack():
    factory = CommandFactory()
    results: list[dict] = []

    def send_packet(command_name: str, dst_addr: int, **kwargs):
        assert command_name == 'time_sync_set'
        return factory.time_sync_set(int(VvAddress.HOST), dst_addr, 39, **kwargs)

    manager = QueryQueueManager(send_packet_fn=send_packet, timeout_s=1.0, max_retries=0, on_complete_fn=results.extend)
    manager.add_query('time_sync_set', int(VvAddress.MCU), unix_time_ms=123456789, timezone_offset=420)

    manager.start()
    _pump_events()
    assert manager.current_transaction is not None

    resolved = manager.handle_ack(39, QueryQueueManager.ACK_RESPONSE_OK)
    _pump_events()

    assert resolved is True
    assert results
    assert results[0]['command_name'] == 'time_sync_set'
    assert results[0]['status'] == 'SUCCESS'


def test_query_queue_does_not_complete_get_query_on_ack_only():
    factory = CommandFactory()
    results: list[dict] = []

    def send_packet(command_name: str, dst_addr: int, **kwargs):
        assert command_name == 'device_information_get'
        return factory.device_information_get(int(VvAddress.HOST), dst_addr, 7)

    manager = QueryQueueManager(send_packet_fn=send_packet, timeout_s=1.0, max_retries=0, on_complete_fn=results.extend)
    manager.add_query('device_information_get', int(VvAddress.MCU))

    manager.start()
    _pump_events()
    assert manager.current_transaction is not None

    resolved = manager.handle_ack(7, QueryQueueManager.ACK_RESPONSE_OK)
    _pump_events()

    assert resolved is False
    assert results == []
    assert manager.is_running is True
    assert manager.current_transaction.command_name == 'device_information_get'

    resp = factory.device_information_resp(int(VvAddress.MCU), int(VvAddress.HOST), 7)
    resolved = manager.handle_response('device_information_resp', resp)
    _pump_events()

    assert resolved is True
    assert results
    assert results[0]['status'] == 'SUCCESS'