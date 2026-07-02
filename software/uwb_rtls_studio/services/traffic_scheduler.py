"""Traffic coordination for concurrent control, ranging, and log flows.

This scheduler protects the app and firmware from avoidable background API
traffic while high-rate streams are active. It does not change protobuf packets
or own UI state; it only decides whether host-side background queries should be
sent now or skipped until the next timer tick.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from PyQt6.QtCore import QObject, pyqtSignal

from utils.app_state import shared_app_state

log = logging.getLogger(__name__)


class TrafficState:
    IDLE = "idle"
    CONTROL_ONLY = "control_only"
    RANGING_ACTIVE = "ranging_active"
    LOG_ACTIVE = "log_active"
    RANGING_AND_LOG_ACTIVE = "ranging_and_log_active"
    BLE_SCANNING = "ble_scanning"
    CLOSING = "closing"


@dataclass(frozen=True)
class TrafficDecision:
    allowed: bool
    reason: str = ""
    state: str = TrafficState.IDLE


class TrafficScheduler(QObject):
    """Small state machine for stream-aware host command throttling."""

    state_changed = pyqtSignal(str)
    command_skipped = pyqtSignal(str, str)

    BACKGROUND_POLL_COMMANDS = {
        "battery_info_get",
        "ble_status_get",
        "ranging_status_get",
    }

    STREAM_CONTROL_COMMANDS = {
        "ranging_start",
        "ranging_stop",
        "log_data",
        "log_clear",
        "end_session",
    }

    CONNECTION_COMMANDS = {
        "ble_connect",
        "ble_disconnect",
        "ble_scan_start",
        "ble_scan_stop",
        "ble_status_get",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = TrafficState.IDLE
        self._closing_until = 0.0
        shared_app_state.ranging_active_changed.connect(self._refresh_state)
        shared_app_state.log_streaming_changed.connect(self._refresh_state)
        shared_app_state.ble_scan_active_changed.connect(self._refresh_state)
        shared_app_state.connection_status_changed.connect(lambda _status: self._refresh_state())
        self._refresh_state()

    @property
    def state(self) -> str:
        return self._state

    def begin_closing(self, duration_s: float = 1.0) -> None:
        self._closing_until = max(self._closing_until, time.monotonic() + max(0.0, duration_s))
        self._refresh_state()

    def allow_command(
        self,
        command_name: str,
        *,
        traffic_class: str = "",
        force: bool = False,
    ) -> TrafficDecision:
        """Return whether a command should be sent immediately."""
        self._refresh_state()
        traffic_class = str(traffic_class or "").strip().lower()

        if command_name in self.STREAM_CONTROL_COMMANDS:
            return TrafficDecision(True, "stream-control", self._state)

        if traffic_class in {"connection", "manual", "user", "bootstrap", "critical"}:
            return TrafficDecision(True, traffic_class, self._state)

        is_background = (
            traffic_class == "background"
            or command_name in self.BACKGROUND_POLL_COMMANDS
        )
        if is_background and self._state in {
            TrafficState.RANGING_ACTIVE,
            TrafficState.LOG_ACTIVE,
            TrafficState.RANGING_AND_LOG_ACTIVE,
            TrafficState.BLE_SCANNING,
            TrafficState.CLOSING,
        }:
            reason = f"background poll deferred while {self._state}"
            self.command_skipped.emit(command_name, reason)
            return TrafficDecision(False, reason, self._state)

        if force and traffic_class != "background":
            return TrafficDecision(True, "forced", self._state)

        return TrafficDecision(True, "allowed", self._state)

    def _refresh_state(self) -> None:
        now = time.monotonic()
        if self._closing_until > now:
            new_state = TrafficState.CLOSING
        elif shared_app_state.ble_scan_active:
            new_state = TrafficState.BLE_SCANNING
        elif shared_app_state.ranging_active and shared_app_state.log_streaming:
            new_state = TrafficState.RANGING_AND_LOG_ACTIVE
        elif shared_app_state.ranging_active:
            new_state = TrafficState.RANGING_ACTIVE
        elif shared_app_state.log_streaming:
            new_state = TrafficState.LOG_ACTIVE
        elif shared_app_state.connection_status == "Connected":
            new_state = TrafficState.CONTROL_ONLY
        else:
            new_state = TrafficState.IDLE

        if new_state != self._state:
            self._state = new_state
            log.info("[TrafficScheduler] state -> %s", self._state)
            self.state_changed.emit(self._state)


shared_traffic_scheduler = TrafficScheduler()
