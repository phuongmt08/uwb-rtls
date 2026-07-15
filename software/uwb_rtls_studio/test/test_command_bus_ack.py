from __future__ import annotations

import os
import sys

from PyQt6.QtCore import QObject, pyqtSignal

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
STUDIO_DIR = os.path.dirname(CURRENT_DIR)
SOFTWARE_DIR = os.path.dirname(STUDIO_DIR)

if STUDIO_DIR not in sys.path:
    sys.path.insert(0, STUDIO_DIR)
if SOFTWARE_DIR not in sys.path:
    sys.path.insert(0, SOFTWARE_DIR)

from common.commands import CommandFactory
from common.transport import VvAddress
from services.command_bus import CommandBus


class _FakeProtocol(QObject):
    packet_received = pyqtSignal(str, object)
    packet_sent = pyqtSignal(str, object)
    ack_received = pyqtSignal(int, int)

    def __init__(self):
        super().__init__()
        self.factory = CommandFactory()

    def send_command(self, command_name: str, dst_addr: int | None = None, command_params: dict | None = None, seq: int = 1):
        dst = int(VvAddress.MCU) if dst_addr is None else int(dst_addr)
        src = int(VvAddress.HOST)
        params = dict(command_params or {})
        pkt = getattr(self.factory, command_name)(src, dst, int(seq), **params)
        self.packet_sent.emit(command_name, pkt)
        return pkt


def test_command_bus_clears_pending_on_ack_for_set_command():
    protocol = _FakeProtocol()
    bus = CommandBus(protocol)

    pkt = protocol.send_command('time_sync_set', dst_addr=int(VvAddress.MCU), seq=39, command_params={'unix_time_ms': 1, 'timezone_offset': 420})
    bus._pending['time_sync_resp'] = 123.0
    bus._on_packet_sent('time_sync_set', pkt)

    bus._on_ack_received(39, CommandBus.ACK_RESPONSE_OK)

    assert 'time_sync_resp' not in bus._pending
    assert 39 not in bus._pending_ack_by_seq


def test_command_bus_keeps_get_pending_until_payload_arrives():
    protocol = _FakeProtocol()
    bus = CommandBus(protocol)

    bus._pending['device_information_resp'] = 123.0
    resp = protocol.factory.device_information_resp(int(VvAddress.MCU), int(VvAddress.HOST), 7)
    bus._on_packet_received('device_information_resp', resp)

    assert 'device_information_resp' not in bus._pending