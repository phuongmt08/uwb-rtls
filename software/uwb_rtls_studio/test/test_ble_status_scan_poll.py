from __future__ import annotations

import os
import sys
import time

from PyQt6.QtCore import QCoreApplication, QObject, pyqtSignal

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
STUDIO_DIR = os.path.dirname(CURRENT_DIR)
SOFTWARE_DIR = os.path.dirname(STUDIO_DIR)

if STUDIO_DIR not in sys.path:
    sys.path.insert(0, STUDIO_DIR)
if SOFTWARE_DIR not in sys.path:
    sys.path.insert(0, SOFTWARE_DIR)

from common import protocol_pb2 as pb
from common.commands import CommandFactory
from common.transport import VvAddress
from models.device_model import DeviceModel
from services.traffic_scheduler import shared_traffic_scheduler
from utils.app_state import shared_app_state


_QT_APP = None


def _ensure_qt_app():
    global _QT_APP
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    _QT_APP = app
    return app


class _FakeSerial(QObject):
    connection_lost = pyqtSignal()


class _FakeProtocol(QObject):
    packet_received = pyqtSignal(str, object)
    packet_sent = pyqtSignal(str, object)
    ack_received = pyqtSignal(int, int)

    def __init__(self):
        super().__init__()
        self.pb = pb
        self._serial = _FakeSerial()

    def send_command(self, command_name: str, dst_addr: int | None = None, command_params: dict | None = None):
        return None


class _FakeCommandBus:
    def __init__(self):
        self.requests: list[tuple[str, int, dict]] = []

    def request(self, command_name: str, dst_addr: int, command_params: dict | None = None, cache_ttl_s=None, force=False, traffic_class: str = ""):
        self.requests.append((command_name, dst_addr, dict(command_params or {})))
        return True

    def send(self, command_name: str, dst_addr: int, command_params: dict | None = None, src_addr=None, traffic_class: str = ""):
        return None


def test_manual_scan_status_does_not_clear_connected_device_with_stale_reason():
    _ensure_qt_app()
    protocol = _FakeProtocol()
    command_bus = _FakeCommandBus()
    model = DeviceModel(protocol, command_bus=command_bus)

    shared_app_state.ble_scan_active = True
    shared_app_state.connection_status = "Connected"
    shared_app_state.connected_device = {"name": "Tag-3", "mac": "3F:88:42:0B:47:DB"}
    model._connected_mac = "3F:88:42:0B:47:DB"
    model._connected_name = "Tag-3"
    model._connection_status = "Connected"

    pkt = CommandFactory().ble_status_resp(int(VvAddress.CENTRAL), int(VvAddress.HOST), 10)
    pkt.ble_status_resp.state = pb.BLE_STATE_SCANNING
    pkt.ble_status_resp.rssi_dbm = -50
    pkt.ble_status_resp.disconnect_reason = 0x16

    model._handle_ble_status(pkt.ble_status_resp)

    assert model.connected_mac == "3F:88:42:0B:47:DB"
    assert model.connection_status == "Connected"
    assert shared_app_state.connection_status == "Connected"
    assert shared_app_state.connected_device["mac"] == "3F:88:42:0B:47:DB"

    shared_app_state.ble_scan_active = False
    model._manual_scan_state_grace_until = time.monotonic() + 2.0
    pkt.ble_status_resp.state = pb.BLE_STATE_IDLE

    model._handle_ble_status(pkt.ble_status_resp)

    assert model.connected_mac == "3F:88:42:0B:47:DB"
    assert model.connection_status == "Connected"
    assert shared_app_state.connected_device["mac"] == "3F:88:42:0B:47:DB"

    model.deleteLater()
    shared_app_state.ble_scan_active = False
    shared_app_state.connection_status = "Disconnected"
    shared_app_state.connected_device = {}


def test_ble_status_background_poll_is_allowed_during_manual_scan():
    _ensure_qt_app()
    shared_app_state.ble_scan_active = True
    shared_app_state.connection_status = "Connected"

    decision = shared_traffic_scheduler.allow_command(
        "ble_status_get",
        traffic_class="background",
    )
    battery_decision = shared_traffic_scheduler.allow_command(
        "battery_info_get",
        traffic_class="background",
    )

    assert decision.allowed is True
    assert decision.reason == "ble-status-during-scan"
    assert battery_decision.allowed is False

    shared_app_state.ble_scan_active = False
    shared_app_state.connection_status = "Disconnected"