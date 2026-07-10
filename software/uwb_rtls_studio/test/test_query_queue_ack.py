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


_QT_APP = None


def _ensure_qt_app():
    global _QT_APP
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    _QT_APP = app
    return app


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

    assert resolved is True
    assert results == []
    assert manager.is_running is True
    assert manager.current_transaction.command_name == 'device_information_get'
    assert manager.current_transaction.status == 'WAITING'

    resp = factory.device_information_resp(int(VvAddress.MCU), int(VvAddress.HOST), 7)
    resolved = manager.handle_response('device_information_resp', resp)
    _pump_events()

    assert resolved is True
    assert results
    assert results[0]['status'] == 'SUCCESS'

def test_query_queue_accepts_get_response_even_when_payload_seq_differs():
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
    assert manager.current_transaction.seq == 7

    # Some firmware replies use an independent packet seq instead of echoing the request seq.
    resp = factory.device_information_resp(int(VvAddress.MCU), int(VvAddress.HOST), 155)
    resolved = manager.handle_response('device_information_resp', resp)
    _pump_events()

    assert resolved is True
    assert results
    assert results[0]['status'] == 'SUCCESS'
    assert results[0]['response_packet'].hdr.seq == 155

def test_query_queue_get_ack_retries_until_payload_arrives():
    factory = CommandFactory()
    results: list[dict] = []

    def send_packet(command_name: str, dst_addr: int, **kwargs):
        assert command_name == 'device_information_get'
        return factory.device_information_get(int(VvAddress.HOST), dst_addr, 7)

    manager = QueryQueueManager(send_packet_fn=send_packet, timeout_s=0.01, max_retries=3, on_complete_fn=results.extend)
    manager.add_query('device_information_get', int(VvAddress.MCU))

    manager.start()
    _pump_events()
    assert manager.current_transaction is not None

    resolved = manager.handle_ack(7, QueryQueueManager.ACK_RESPONSE_OK)
    assert resolved is True

    manager._on_timeout()
    _pump_events()

    assert results == []
    assert manager.is_running is True
    assert manager.current_transaction is not None
    assert manager.current_transaction.command_name == 'device_information_get'
    assert manager.current_transaction.retries == 1

    resp = factory.device_information_resp(int(VvAddress.MCU), int(VvAddress.HOST), 155)
    resolved = manager.handle_response('device_information_resp', resp)
    _pump_events()

    assert resolved is True
    assert results
    assert results[0]['status'] == 'SUCCESS'


def test_query_queue_rescues_late_response_for_timed_out_batch_item():
    factory = CommandFactory()
    results: list[dict] = []
    seq_by_command = {
        'sys_config_get': 11,
        'sys_ranging_cfg_get': 12,
    }

    def send_packet(command_name: str, dst_addr: int, **kwargs):
        if command_name == 'sys_config_get':
            return factory.sys_config_get(int(VvAddress.HOST), dst_addr, seq_by_command[command_name])
        if command_name == 'sys_ranging_cfg_get':
            return factory.sys_ranging_cfg_get(int(VvAddress.HOST), dst_addr, seq_by_command[command_name])
        raise AssertionError(command_name)

    manager = QueryQueueManager(send_packet_fn=send_packet, timeout_s=0.01, max_retries=0, on_complete_fn=results.extend)
    manager.INTER_COMMAND_GAP_S = 0.0
    manager.add_query('sys_config_get', int(VvAddress.MCU))
    manager.add_query('sys_ranging_cfg_get', int(VvAddress.MCU))

    manager.start()
    _pump_events()
    assert manager.current_transaction is not None
    assert manager.current_transaction.command_name == 'sys_config_get'

    manager._on_timeout()
    _pump_events()
    assert manager.current_transaction is not None
    assert manager.current_transaction.command_name == 'sys_ranging_cfg_get'

    late_resp = factory.sys_config_resp(int(VvAddress.MCU), int(VvAddress.HOST), 155)
    resolved = manager.handle_response('sys_config_resp', late_resp)
    assert resolved is True

    current_resp = factory.sys_ranging_cfg_resp(int(VvAddress.MCU), int(VvAddress.HOST), 156)
    resolved = manager.handle_response('sys_ranging_cfg_resp', current_resp)
    _pump_events()

    assert resolved is True
    assert results
    by_name = {item['command_name']: item for item in results}
    assert by_name['sys_config_get']['status'] == 'SUCCESS'
    assert by_name['sys_config_get']['response_packet'].hdr.seq == 155
    assert by_name['sys_ranging_cfg_get']['status'] == 'SUCCESS'
