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
from common.transport import VvAddress
from services.traffic_scheduler import shared_traffic_scheduler
from utils.app_state import QUERY_MAX_RETRIES, QUERY_TIMEOUT_S, shared_app_state
from utils.command_flags import is_command_enabled

log = logging.getLogger(__name__)


class CommandBus(QObject):
    response_received = pyqtSignal(str, object)
    cache_hit = pyqtSignal(str, object)
    command_sent = pyqtSignal(str)

    DEFAULT_CACHE_TTL_S = 2.0
    # Pending window must cover full sequential retry budget, otherwise another
    # caller can enqueue a duplicate GET while the first transaction is still
    # retrying — flooding the dongle/BLE path and dropping late responses.
    PENDING_TTL_S = max(3.0, QUERY_TIMEOUT_S * (QUERY_MAX_RETRIES + 1) + 1.0)
    INVALIDATE_ON_SEND = {
        "anchor_layout_set": "anchor_layout_resp",
        "sys_ranging_cfg_set": "sys_ranging_cfg_resp",
        "sys_config_set": "sys_config_resp",
        "sensor_fusion_cfg_set": "sensor_fusion_cfg_resp",
        "prefilter_cfg_set": "prefilter_cfg_resp",
        "ble_conn_params_set": "ble_conn_params_resp",
        "time_sync_set": "time_sync_resp",
    }
    # Out-of-band TX that may run while the sequential GET queue is armed.
    # Everything else with a mapped response is forced through the queue so
    # host TX never interleaves mid-wait with an in-flight GET.
    OUT_OF_BAND_TRAFFIC_CLASSES = {
        "connection",
        "critical",
        "manual",
        "user",
    }
    OUT_OF_BAND_COMMANDS = {
        "ble_connect",
        "ble_disconnect",
        "ble_scan_start",
        "ble_scan_stop",
        "ranging_start",
        "ranging_stop",
        "end_session",
        "log_data",
        "log_clear",
        "device_reset",
        "uwb_reset",
        "enter_to_bootloader",
    }
    ACK_RESPONSE_OK = 1

    def __init__(self, protocol_service, parent=None):
        super().__init__(parent)
        self._protocol = protocol_service
        self._catalog = CommandCatalog()
        self._cache: dict[str, tuple[float, object]] = {}
        self._pending: dict[str, float] = {}
        self._pending_ack_by_seq: dict[int, tuple[str, str]] = {}
        self._redispatching_cache = False
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
        command_params: dict[str, Any] | None = None,
        manual_bypass: bool = False,
        traffic_class: str = "",
        flow_name: str = "",
        timeout_s: float | None = None,
        max_retries: int | None = None,
    ) -> bool:
        """
        Request a command-response packet through the global queue.

        Returns True when a new command is enqueued, False when a fresh cache or
        pending request already covers the caller's need.
        """
        params = dict(command_params or {})
        traffic_class = str(traffic_class or "").strip().lower()
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
            self.send(
                command_name,
                dst_addr=dst_addr,
                command_params=params,
                manual_bypass=manual_bypass,
                traffic_class=traffic_class,
                flow_name=flow_name,
            )
            return True

        now = time.monotonic()
        cached = self._cache.get(expected_response)
        if not force and cached and now - cached[0] <= ttl:
            log.debug("CommandBus cache hit: %s -> %s", command_name, expected_response)
            self._serve_cache_hit(expected_response, cached[1])
            return False

        pending_until = self._pending.get(expected_response, 0.0)
        if not force and pending_until > now:
            log.debug("CommandBus dedupe pending: %s waits for %s", command_name, expected_response)
            return False

        target_addr = default_destination_for(command_name) if dst_addr is None else dst_addr
        enqueued = shared_app_state.enqueue_query(
            command_name,
            dst_addr=target_addr,
            command_params=params,
            traffic_class=traffic_class,
            flow_name=flow_name,
            timeout_s=timeout_s,
            max_retries=max_retries,
        )
        if not enqueued:
            self._pending.pop(expected_response, None)
            log.debug("CommandBus enqueue skipped: %s -> %s", command_name, expected_response)
            return False

        self._pending[expected_response] = now + self.PENDING_TTL_S
        self.command_sent.emit(command_name)
        return True

    def send(
        self,
        command_name: str,
        dst_addr: int | None = None,
        command_params: dict[str, Any] | None = None,
        manual_bypass: bool = False,
        traffic_class: str = "",
        flow_name: str = "",
        from_query_queue: bool = False,
        src_addr: int | None = None,
    ):
        params = dict(command_params or {})
        traffic_class = str(traffic_class or "").strip().lower()
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

        if not from_query_queue and self._should_serialize_through_queue(command_name, traffic_class):
            target_addr = default_destination_for(command_name) if dst_addr is None else dst_addr
            log.info(
                "Serializing out-of-band command through query queue to avoid mid-wait TX: %s (class=%s)",
                command_name,
                traffic_class or "default",
            )
            enqueued = shared_app_state.enqueue_query(
                command_name,
                dst_addr=target_addr,
                command_params=params,
                traffic_class=traffic_class or "user",
                flow_name=flow_name,
            )
            if enqueued:
                try:
                    expected_response = self._catalog.expected_response_for(command_name)
                except KeyError:
                    expected_response = ""
                if expected_response:
                    self._pending[expected_response] = time.monotonic() + self.PENDING_TTL_S
                self.command_sent.emit(command_name)
            return None

        self.invalidate_for_command(command_name)
        target_addr = default_destination_for(command_name) if dst_addr is None else dst_addr
        source_addr = int(src_addr) if src_addr is not None else int(VvAddress.HOST)
        pkt = self._protocol.send_command(
            command_name,
            dst_addr=target_addr,
            src_addr=source_addr,
            command_params=params,
        )
        self.command_sent.emit(command_name)
        return pkt

    def invalidate_for_command(self, command_name: str) -> None:
        response_name = self.INVALIDATE_ON_SEND.get(command_name)
        if response_name:
            self._cache.pop(response_name, None)
            self._pending.pop(response_name, None)

    def clear_pending(self, response_name: str | None = None) -> None:
        """Drop pending markers so timed-out queries can be re-issued cleanly."""
        if response_name:
            self._pending.pop(str(response_name), None)
            return
        self._pending.clear()

    def invalidate_response(self, response_name: str) -> None:
        """Drop both cache and pending for a single response name."""
        name = str(response_name or "")
        if not name:
            return
        self._cache.pop(name, None)
        self._pending.pop(name, None)

    def invalidate_all_responses(self) -> None:
        """Drop all response cache entries (used when session markers are reset)."""
        self._cache.clear()
        self._pending.clear()

    def _should_serialize_through_queue(self, command_name: str, traffic_class: str) -> bool:
        """Return True when an immediate send would race an in-flight GET wait."""
        traffic = str(traffic_class or "").strip().lower()
        if traffic in self.OUT_OF_BAND_TRAFFIC_CLASSES:
            return False
        if command_name in self.OUT_OF_BAND_COMMANDS:
            return False
        manager = getattr(shared_app_state, "_query_manager", None)
        if manager is None or not getattr(manager, "is_running", False):
            return False
        current = getattr(manager, "current_transaction", None)
        if current is None:
            return False
        # Only serialize when the queue is actively waiting on a payload/ACK.
        status = str(getattr(current, "status", "") or "")
        if status not in {"SENT", "WAITING", "RETRY_PENDING"}:
            return False
        try:
            expected = self._catalog.expected_response_for(command_name)
        except KeyError:
            expected = ""
        # Fire-and-forget commands without a mapped response still risk bus
        # contention; keep only true control-plane exceptions out-of-band.
        return bool(expected) or command_name.endswith("_set")

    def _serve_cache_hit(self, response_name: str, pkt) -> None:
        """
        Re-deliver a cached payload into the normal RX path.

        Callers that clear local "received" markers then re-request with
        force=False previously hit cache and received nothing — UI stayed empty
        even though the packet had already arrived from the dongle.
        """
        if not shared_app_state.should_accept_decoded_packet(response_name, pkt):
            log.debug("CommandBus cache-hit blocked by session gate: %s", response_name)
            self._cache.pop(response_name, None)
            self._pending.pop(response_name, None)
            return
        self.cache_hit.emit(response_name, pkt)
        if self._redispatching_cache:
            return
        self._redispatching_cache = True
        try:
            # Mirror ProtocolService._dispatch_decoded_packets order so cache
            # hits update repositories, query markers, and model handlers.
            repository = getattr(self._protocol, "_packet_repository", None)
            if repository is not None:
                try:
                    repository.handle_packet(response_name, pkt)
                except Exception as exc:
                    log.debug("Cache-hit repository redispatch failed for %s: %s", response_name, exc)
            try:
                shared_app_state.handle_incoming_packet(response_name, pkt)
            except Exception as exc:
                log.debug("Cache-hit shared-state redispatch failed for %s: %s", response_name, exc)
            # Fan out to models via the normal protocol signal (also refreshes
            # CommandBus cache/pending through _on_packet_received).
            self._protocol.packet_received.emit(response_name, pkt)
        finally:
            self._redispatching_cache = False

    def _on_packet_sent(self, param_name: str, pkt) -> None:
        response_name = self.INVALIDATE_ON_SEND.get(param_name)
        if not response_name:
            return
        hdr = getattr(pkt, "hdr", None)
        seq = getattr(hdr, "seq", None)
        if seq is None:
            return
        dst = getattr(getattr(hdr, "addr", None), "dst", None)
        self._pending_ack_by_seq[int(seq)] = (param_name, response_name, dst)

    def _on_ack_received(self, ack_seq: int, response: int, src_addr: int | None = None) -> None:
        entry = self._pending_ack_by_seq.get(int(ack_seq))
        if not entry:
            return
        command_name, response_name, expected_src = entry
        if src_addr is not None and expected_src is not None and int(src_addr) != int(expected_src):
            log.debug(
                "CommandBus ignoring ACK seq=%s from unexpected src=%s (expected %s) for %s",
                ack_seq, src_addr, expected_src, command_name,
            )
            return
        self._pending_ack_by_seq.pop(int(ack_seq), None)
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
        if not shared_app_state.should_accept_decoded_packet(param_name, pkt):
            log.debug("CommandBus RX cache blocked by session gate: %s", param_name)
            self._pending.pop(param_name, None)
            return
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
        self._redispatching_cache = False
        log.info("CommandBus reset cache.")


shared_command_bus: CommandBus | None = None


def init_shared_command_bus(protocol_service) -> CommandBus:
    global shared_command_bus
    shared_command_bus = CommandBus(protocol_service)
    return shared_command_bus
