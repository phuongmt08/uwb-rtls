"""
Shared command bus for all ViewModels.

ViewModels use this instead of calling ProtocolService directly for common
commands. It provides a thin shared-memory friendly layer:
  - GET command de-duplication while a matching response is pending.
  - Short-lived response cache for tabs that need the same state.
  - A single place to send direct SET/control commands.
  - Command destination defaults shared with ProtocolService.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal

from common.commands import CommandCatalog, default_destination_for
from services.traffic_scheduler import shared_traffic_scheduler
from utils.app_state import shared_app_state
from utils.command_flags import is_command_enabled

log = logging.getLogger(__name__)


class CommandBus(QObject):
    response_received = pyqtSignal(str, object)
    cache_hit = pyqtSignal(str, object)
    command_sent = pyqtSignal(str)

    DEFAULT_CACHE_TTL_S = 2.0
    PENDING_TTL_S = 3.0
    INVALIDATE_ON_SEND = {
        "anchor_layout_set": "anchor_layout_resp",
        "sys_ranging_cfg_set": "sys_ranging_cfg_resp",
        "sys_config_set": "sys_config_resp",
        "sensor_fusion_cfg_set": "sensor_fusion_cfg_resp",
        "pos_calib_cfg_set": "pos_calib_cfg_resp",
        "ble_conn_params_set": "ble_conn_params_resp",
        "time_sync_set": "time_sync_resp",
    }
    ACK_RESPONSE_OK = 1

    def __init__(self, protocol_service, parent=None):
        super().__init__(parent)
        self._protocol = protocol_service
        self._catalog = CommandCatalog()
        self._cache: dict[str, tuple[float, object]] = {}
        self._pending: dict[str, float] = {}
        self._pending_ack_by_seq: dict[int, tuple[str, str]] = {}
        self.manual_test_mode_enabled = False
        self._protocol.packet_received.connect(self._on_packet_received)
        self._protocol.packet_sent.connect(self._on_packet_sent)
        self._protocol.ack_received.connect(self._on_ack_received)

    def request(
        self,
        command_name: str,
        dst_addr: int | None = None,
        cache_ttl_s: float | None = None,
        force: bool = False,
        **kwargs: Any,
    ) -> bool:
        """
        Request a command-response packet through the global queue.

        Returns True when a new command is enqueued, False when a fresh cache or
        pending request already covers the caller's need.
        """
        manual_bypass = kwargs.pop("manual_bypass", False)
        traffic_class = kwargs.pop("traffic_class", kwargs.pop("_traffic_class", ""))
        if getattr(self, "manual_test_mode_enabled", False) and not manual_bypass:
            log.debug("Command blocked by manual test mode: %s", command_name)
            return False

        if not is_command_enabled(command_name):
            log.info("Command skipped by flag: %s", command_name)
            return False

        decision = shared_traffic_scheduler.allow_command(
            command_name,
            traffic_class=traffic_class,
            force=force,
        )
        if not decision.allowed:
            log.debug("Command deferred by traffic scheduler: %s (%s)", command_name, decision.reason)
            return False

        ttl = self.DEFAULT_CACHE_TTL_S if cache_ttl_s is None else cache_ttl_s
        try:
            expected_response = self._catalog.expected_response_for(command_name)
        except KeyError:
            expected_response = ""
        if not expected_response:
            self.send(command_name, dst_addr=dst_addr, manual_bypass=manual_bypass, traffic_class=traffic_class, **kwargs)
            return True

        now = time.monotonic()
        cached = self._cache.get(expected_response)
        if not force and cached and now - cached[0] <= ttl:
            log.debug("CommandBus cache hit: %s -> %s", command_name, expected_response)
            self.cache_hit.emit(expected_response, cached[1])
            return False

        pending_until = self._pending.get(expected_response, 0.0)
        if not force and pending_until > now:
            log.debug("CommandBus dedupe pending: %s waits for %s", command_name, expected_response)
            return False

        self._pending[expected_response] = now + self.PENDING_TTL_S
        target_addr = default_destination_for(command_name) if dst_addr is None else dst_addr
        shared_app_state.enqueue_query(
            command_name,
            dst_addr=target_addr,
            traffic_class=traffic_class,
            **kwargs,
        )
        self.command_sent.emit(command_name)
        return True

    def send(self, command_name: str, dst_addr: int | None = None, **kwargs: Any):
        manual_bypass = kwargs.pop("manual_bypass", False)
        traffic_class = kwargs.pop("traffic_class", kwargs.pop("_traffic_class", ""))
        if getattr(self, "manual_test_mode_enabled", False) and not manual_bypass:
            log.debug("Command blocked by manual test mode: %s", command_name)
            return None

        if not is_command_enabled(command_name):
            log.info("Command skipped by flag: %s", command_name)
            return None

        decision = shared_traffic_scheduler.allow_command(
            command_name,
            traffic_class=traffic_class,
            force=traffic_class != "background",
        )
        if not decision.allowed:
            log.debug("Command skipped by traffic scheduler: %s (%s)", command_name, decision.reason)
            return None

        self.invalidate_for_command(command_name)
        target_addr = default_destination_for(command_name) if dst_addr is None else dst_addr
        pkt = self._protocol.send_command(command_name, dst_addr=target_addr, **kwargs)
        self.command_sent.emit(command_name)
        return pkt

    def invalidate_for_command(self, command_name: str) -> None:
        response_name = self.INVALIDATE_ON_SEND.get(command_name)
        if response_name:
            self._cache.pop(response_name, None)
            self._pending.pop(response_name, None)

    def _on_packet_sent(self, param_name: str, pkt) -> None:
        response_name = self.INVALIDATE_ON_SEND.get(param_name)
        if not response_name:
            return
        seq = getattr(getattr(pkt, "hdr", None), "seq", None)
        if seq is None:
            return
        self._pending_ack_by_seq[int(seq)] = (param_name, response_name)

    def _on_ack_received(self, ack_seq: int, response: int) -> None:
        entry = self._pending_ack_by_seq.pop(int(ack_seq), None)
        if not entry:
            return
        command_name, response_name = entry
        self._pending.pop(response_name, None)
        if int(response) == self.ACK_RESPONSE_OK:
            log.debug("CommandBus ACK resolved pending: %s -> %s seq=%s", command_name, response_name, ack_seq)
        else:
            log.debug(
                "CommandBus ACK cleared pending after NACK: %s -> %s seq=%s response=%s",
                command_name,
                response_name,
                ack_seq,
                response,
            )

    def _on_packet_received(self, param_name: str, pkt) -> None:
        self._cache[param_name] = (time.monotonic(), pkt)
        self._pending.pop(param_name, None)
        # Once the payload arrives, any ACK bookkeeping for that same command is obsolete.
        seq = getattr(getattr(pkt, "hdr", None), "seq", None)
        if seq is not None:
            self._pending_ack_by_seq.pop(int(seq), None)
        self.response_received.emit(param_name, pkt)

    def reset(self) -> None:
        """Clear cache and pending command tracking."""
        self._cache.clear()
        self._pending.clear()
        self._pending_ack_by_seq.clear()
        log.info("CommandBus reset cache.")


shared_command_bus: CommandBus | None = None


def init_shared_command_bus(protocol_service) -> CommandBus:
    global shared_command_bus
    shared_command_bus = CommandBus(protocol_service)
    return shared_command_bus