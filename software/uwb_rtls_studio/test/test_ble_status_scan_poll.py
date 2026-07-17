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


def test_post_connect_background_scan_is_disabled():
    _ensure_qt_app()
    protocol = _FakeProtocol()
    model = DeviceModel(protocol, command_bus=_FakeCommandBus())

    calls = []
    model.start_scan = lambda *args, **kwargs: calls.append((args, kwargs))
    shared_app_state.connection_status = "Connected"
    model._connected_mac = "92:62:DE:B4:AF:F8"
    model._connection_status = "Connected"

    model._schedule_background_scan_after_connect()
    model._resume_background_scan_after_connect()

    assert calls == []
    assert not model._background_scan_resume_timer.isActive()

    model.deleteLater()
    shared_app_state.connection_status = "Disconnected"
    shared_app_state.connected_device = {}


def test_ble_disconnect_does_not_auto_restart_scan():
    _ensure_qt_app()
    protocol = _FakeProtocol()
    model = DeviceModel(protocol, command_bus=_FakeCommandBus())

    calls = []
    model.start_scan = lambda *args, **kwargs: calls.append((args, kwargs))
    shared_app_state.ble_scan_active = False
    shared_app_state.connection_status = "Connected"
    shared_app_state.connected_device = {"name": "Anchor-1", "mac": "92:62:DE:B4:AF:F8"}
    model._connected_mac = "92:62:DE:B4:AF:F8"
    model._connected_name = "Anchor-1"
    model._connection_status = "Connected"

    pkt = CommandFactory().ble_status_resp(int(VvAddress.CENTRAL), int(VvAddress.HOST), 21)
    model._last_device_rx_monotonic = time.monotonic() - 31.0
    pkt.ble_status_resp.state = pb.BLE_STATE_SCANNING
    pkt.ble_status_resp.rssi_dbm = -70
    pkt.ble_status_resp.disconnect_reason = 0x08

    model._handle_ble_status(pkt.ble_status_resp)

    assert calls == []
    assert model.connection_status == "Disconnected"

    model.deleteLater()
    shared_app_state.ble_scan_active = False
    shared_app_state.connection_status = "Disconnected"
    shared_app_state.connected_device = {}

def test_background_scan_connecting_state_keeps_logical_link_connected():
    _ensure_qt_app()
    protocol = _FakeProtocol()
    model = DeviceModel(protocol, command_bus=_FakeCommandBus())

    shared_app_state.ble_scan_active = True
    shared_app_state.connection_status = "Connected"
    shared_app_state.connected_device = {"name": "Anchor-1", "mac": "92:62:DE:B4:AF:F8"}
    model._connected_mac = "92:62:DE:B4:AF:F8"
    model._connected_name = "Anchor-1"
    model._connection_status = "Connected"
    model._last_device_rx_monotonic = time.monotonic()

    pkt = CommandFactory().ble_status_resp(int(VvAddress.CENTRAL), int(VvAddress.HOST), 11)
    pkt.ble_status_resp.state = pb.BLE_STATE_CONNECTING
    pkt.ble_status_resp.rssi_dbm = -61

    model._handle_ble_status(pkt.ble_status_resp)

    assert model.connection_status == "Connected"
    assert model.connected_mac == "92:62:DE:B4:AF:F8"
    assert shared_app_state.connection_status == "Connected"
    assert model._link_health_snapshot()["health"] == "OK"

    model._last_device_rx_monotonic = time.monotonic() - 16.0
    assert model._link_health_snapshot()["health"] == "WARNING"
    model._last_device_rx_monotonic = time.monotonic() - 31.0
    assert model._link_health_snapshot()["health"] == "LOST"

    model._link_health_timer.stop()
    model.deleteLater()
    shared_app_state.ble_scan_active = False
    shared_app_state.connection_status = "Disconnected"
    shared_app_state.connected_device = {}


def test_background_scan_timeout_clears_confirmed_lost_link():
    _ensure_qt_app()
    protocol = _FakeProtocol()
    model = DeviceModel(protocol, command_bus=_FakeCommandBus())

    shared_app_state.ble_scan_active = True
    shared_app_state.connection_status = "Connected"
    shared_app_state.connected_device = {"name": "Anchor-3", "mac": "CF:81:2A:23:64:D6"}
    model._connected_mac = "CF:81:2A:23:64:D6"
    model._connected_name = "Anchor-3"
    model._connection_status = "Connected"
    model._last_device_rx_monotonic = time.monotonic() - 31.0
    model._suppress_next_disconnect_scan = True

    states = []
    progress = []
    model.connection_state_changed.connect(states.append)
    model.connection_progress_changed.connect(progress.append)

    pkt = CommandFactory().ble_status_resp(int(VvAddress.CENTRAL), int(VvAddress.HOST), 12)
    pkt.ble_status_resp.state = pb.BLE_STATE_SCANNING
    pkt.ble_status_resp.rssi_dbm = -58
    pkt.ble_status_resp.disconnect_reason = 0x08

    model._handle_ble_status(pkt.ble_status_resp)

    assert model.connection_status == "Disconnected"
    assert model.connected_mac == ""
    assert shared_app_state.connection_status == "Disconnected"
    assert shared_app_state.connected_device == {}
    assert states[-1]["status"] == "Disconnected"
    assert progress[-1]["progress"] == 0

    model._link_health_timer.stop()
    model.deleteLater()
    shared_app_state.ble_scan_active = False
    shared_app_state.connection_status = "Disconnected"
    shared_app_state.connected_device = {}

def test_async_disconnect_event_clears_link_immediately():
    _ensure_qt_app()
    model = DeviceModel(_FakeProtocol(), command_bus=_FakeCommandBus())
    notifications = []
    model.ble_notification_requested.connect(notifications.append)

    shared_app_state.ble_scan_active = True
    shared_app_state.connection_status = "Connected"
    shared_app_state.connected_device = {"name": "Anchor-3", "mac": "CF:81:2A:23:64:D6"}
    model._connected_mac = "CF:81:2A:23:64:D6"
    model._connected_name = "Anchor-3"
    model._connection_status = "Connected"
    model._last_device_rx_monotonic = time.monotonic()
    model._suppress_next_disconnect_scan = True

    pkt = CommandFactory().ble_status_resp(int(VvAddress.CENTRAL), int(VvAddress.HOST), 0)
    pkt.ble_status_resp.state = pb.BLE_STATE_IDLE
    pkt.ble_status_resp.rssi_dbm = -58
    pkt.ble_status_resp.disconnect_reason = 0x08

    model._handle_ble_status(
        pkt.ble_status_resp,
        packet_seq=0,
        packet_src=int(VvAddress.CENTRAL),
    )

    assert model.connection_status == "Disconnected"
    assert model.connected_mac == ""
    assert shared_app_state.connected_device == {}
    assert notifications[-1]["reason_code"] == 0x08
    assert notifications[-1]["reason_code_hex"] == "0x08"

    model._link_health_timer.stop()
    model.deleteLater()
    shared_app_state.ble_scan_active = False
    shared_app_state.connection_status = "Disconnected"
    shared_app_state.connected_device = {}

def test_manual_disconnect_emits_reason_notification():
    _ensure_qt_app()
    model = DeviceModel(_FakeProtocol(), command_bus=_FakeCommandBus())
    notifications = []
    model.ble_notification_requested.connect(notifications.append)

    shared_app_state.ble_scan_active = False
    shared_app_state.connection_status = "Disconnecting"
    shared_app_state.connected_device = {"name": "Anchor-3", "mac": "CF:81:2A:23:64:D6"}
    model._connected_mac = "CF:81:2A:23:64:D6"
    model._connected_name = "Anchor-3"
    model._connection_status = "Disconnecting"
    model._suppress_next_disconnect_scan = True

    pkt = CommandFactory().ble_status_resp(int(VvAddress.CENTRAL), int(VvAddress.HOST), 13)
    pkt.ble_status_resp.state = pb.BLE_STATE_IDLE
    pkt.ble_status_resp.rssi_dbm = -58
    pkt.ble_status_resp.disconnect_reason = 0x16

    model._handle_ble_status(pkt.ble_status_resp)

    assert model.connection_status == "Disconnected"
    assert notifications[-1]["kind"] == "disconnect"
    assert notifications[-1]["reason_code"] == 0x16
    assert notifications[-1]["reason_code_hex"] == "0x16"
    assert notifications[-1]["message"].startswith("Disconnected by user.")

    model._link_health_timer.stop()
    model.deleteLater()
    shared_app_state.connection_status = "Disconnected"
    shared_app_state.connected_device = {}

def test_manual_disconnect_waits_for_late_hci_reason():
    _ensure_qt_app()
    model = DeviceModel(_FakeProtocol(), command_bus=_FakeCommandBus())
    notifications = []
    model.ble_notification_requested.connect(notifications.append)

    shared_app_state.ble_scan_active = False
    shared_app_state.connection_status = "Disconnecting"
    shared_app_state.connected_device = {"name": "Anchor-3", "mac": "CF:81:2A:23:64:D6"}
    model._connected_mac = "CF:81:2A:23:64:D6"
    model._connected_name = "Anchor-3"
    model._connection_status = "Disconnecting"
    model._suppress_next_disconnect_scan = True

    immediate = CommandFactory().ble_status_resp(int(VvAddress.CENTRAL), int(VvAddress.HOST), 14)
    immediate.ble_status_resp.state = pb.BLE_STATE_IDLE
    immediate.ble_status_resp.disconnect_reason = 0
    model._handle_ble_status(immediate.ble_status_resp)

    assert notifications == []
    assert model._pending_manual_disconnect_notification is True

    late = CommandFactory().ble_status_resp(int(VvAddress.CENTRAL), int(VvAddress.HOST), 15)
    late.ble_status_resp.state = pb.BLE_STATE_IDLE
    late.ble_status_resp.disconnect_reason = 0x16
    model._handle_ble_status(late.ble_status_resp)

    assert len(notifications) == 1
    assert notifications[0]["reason_code"] == 0x16
    assert notifications[0]["reason_code_hex"] == "0x16"
    assert model._pending_manual_disconnect_notification is False
    assert model._manual_disconnect_notify_timer.isActive() is False

    model._link_health_timer.stop()
    model.deleteLater()
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