"""
===============================================================================
  UWB RTLS Studio — Query State Machine
===============================================================================
  File        : services/query_state_machine.py
  Description : Sequential Command-Response Queue & State Machine.
                Host-side orchestration only. It does not change firmware
                behavior or protobuf wire format.
===============================================================================
"""
from __future__ import annotations

import logging
import time
import threading
from typing import Callable, Any, Dict, List

from PyQt6.QtCore import QObject, QTimer, Qt, pyqtSignal

log = logging.getLogger(__name__)


class QueryState:
    IDLE = "IDLE"
    PENDING = "PENDING"
    RETRY_PENDING = "RETRY_PENDING"
    SENT = "SENT"
    WAITING = "WAITING"
    SUCCESS = "SUCCESS"
    TIMEOUT = "TIMEOUT"
    FAILED = "FAILED"


class QueryTransaction:
    """Represents a single query transaction in the state machine."""

    def __init__(self, command_name: str, dst_addr: int, expected_response: str, kwargs: dict, priority: int = 0):
        self.command_name = command_name
        self.dst_addr = dst_addr
        self.expected_response = expected_response
        self.kwargs = kwargs
        self.priority = int(priority)

        self.status = QueryState.PENDING
        self.retries = 0
        self.sent_time = 0.0
        self.received_time = 0.0
        self.response_packet = None
        self.seq = None
        self.ack_received = False
        self.ack_response = None
        self.pending_response_name = ""
        self.pending_response_packet = None
        self.pending_response_seq = None

class QueryQueueManager(QObject):
    TERMINAL_STATES = {QueryState.SUCCESS, QueryState.TIMEOUT, QueryState.FAILED}
    RESPONSE_RESCUE_STATES = {
        QueryState.SENT,
        QueryState.WAITING,
        QueryState.RETRY_PENDING,
        QueryState.TIMEOUT,
        QueryState.FAILED,
    }

    @staticmethod
    def _tx_label(tx: QueryTransaction | None) -> str:
        if tx is None:
            return "<no-active-query>"
        seq = "-" if tx.seq is None else str(tx.seq)
        return f"cmd={tx.command_name} expected={tx.expected_response or '-'} dst={tx.dst_addr} seq={seq} retries={tx.retries} status={tx.status}"

    @staticmethod
    def _looks_like_response_name(param_name: str) -> bool:
        name = str(param_name or "")
        return name.endswith("_resp") or name == "ack" or name.endswith("_status")
    """
    Manages a queue of query transactions. Sends queries sequentially,
    waits for the expected response, and retries on timeout.
    """

    _send_next_requested = pyqtSignal()

    RESPONSE_MAP = {
        "device_information_get": "device_information_resp",
        "battery_info_get": "battery_info_resp",
        "time_sync_get": "time_sync_resp",
        "time_sync_set": "time_sync_resp",
        "anchor_layout_get": "anchor_layout_resp",
        "sys_config_get": "sys_config_resp",
        "sys_config_set": "sys_config_resp",
        "sys_ranging_cfg_get": "sys_ranging_cfg_resp",
        "sys_ranging_cfg_set": "sys_ranging_cfg_resp",
        "sensor_fusion_cfg_get": "sensor_fusion_cfg_resp",
        "sensor_fusion_cfg_set": "sensor_fusion_cfg_resp",
        "pos_calib_cfg_get": "pos_calib_cfg_resp",
        "ble_status_get": "ble_status_resp",
        "ble_conn_params_get": "ble_conn_params_resp",
        "ble_conn_params_set": "ble_conn_params_resp",
        "ranging_status_get": "ranging_status_resp",
        "calib_status_get": "calib_status_resp",
        "rtos_resource_get": "rtos_resource_resp",
        "rtos_task_stats_get": "rtos_task_stats_resp",
        "device_type_get": "device_type_set",
        "prefilter_cfg_get": "prefilter_cfg_resp",
        "prefilter_cfg_set": "prefilter_cfg_resp",
        "zone_profile_get": "zone_profile_resp",
    }
    ACK_RESPONSE_OK = 1
    ACKED_GET_WAIT_S = 3.0
    INTER_COMMAND_GAP_S = 0.05
    def __init__(
        self,
        send_packet_fn: Callable[[str, int, Dict[str, Any]], Any],
        timeout_s: float = 0.2,
        max_retries: int = 3,
        on_complete_fn: Callable[[List[Dict[str, Any]]], None] | None = None,
        response_map: Dict[str, str] | None = None,
        parent=None,
    ):
        """
        Args:
            send_packet_fn: Callback function to execute sending: fn(command_name, dst_addr, **kwargs)
            timeout_s: Time to wait for expected response (in seconds)
            max_retries: Maximum retries per command on timeout
            on_complete_fn: Callback function called when all queries finish
        """
        super().__init__(parent)
        self.send_packet_fn = send_packet_fn
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.on_complete_fn = on_complete_fn
        self.response_map = dict(response_map or self._load_response_map())

        self.queue: List[QueryTransaction] = []
        self.current_transaction: QueryTransaction | None = None
        self.lock = threading.RLock()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._on_timeout)
        self._send_next_requested.connect(self._send_next, Qt.ConnectionType.QueuedConnection)
        self.is_running = False

        self._last_send_time = 0.0
    @classmethod
    def _load_response_map(cls) -> Dict[str, str]:
        try:
            try:
                from .commands import CommandCatalog
            except ImportError:
                from common.commands import CommandCatalog
            mapped = CommandCatalog().query_response_map()
            if mapped:
                return mapped
        except Exception as exc:
            log.debug("Falling back to QueryQueueManager.RESPONSE_MAP: %s", exc)
        return dict(cls.RESPONSE_MAP)

    def add_query(self, command_name: str, dst_addr: int, **kwargs) -> None:
        """Add a query to the queue."""
        expected_response = self.response_map.get(command_name, "")
        if not expected_response:
            log.warning(f"No expected response mapped for command '{command_name}'. Defaulting to None.")

        tx = QueryTransaction(command_name, dst_addr, expected_response, kwargs, self._priority_for(kwargs))
        with self.lock:
            if not self.is_running:
                self.queue = [item for item in self.queue if item.status not in self.TERMINAL_STATES]
            log.debug("Query queued: %s", self._tx_label(tx))
            insert_at = len(self.queue)
            if tx.priority > 0:
                for index, item in enumerate(self.queue):
                    if item.status not in (QueryState.PENDING, QueryState.RETRY_PENDING):
                        continue
                    if item.priority < tx.priority:
                        insert_at = index
                        break
            self.queue.insert(insert_at, tx)

    @staticmethod
    def _priority_for(kwargs: dict) -> int:
        traffic_class = str((kwargs or {}).get("traffic_class", "") or "").strip().lower()
        if traffic_class in {"connection", "critical"}:
            return 100
        if traffic_class in {"manual", "user"}:
            return 80
        if traffic_class == "bootstrap":
            return 20
        return 0

    def start(self) -> None:
        """Start the query process."""
        with self.lock:
            if self.is_running:
                log.warning("QueryQueueManager is already running.")
                return
            self.is_running = True

        log.info(f"Starting sequential query queue with {len(self.queue)} commands...")
        with self.lock:
            for tx in self.queue:
                log.debug("Queue snapshot: %s", self._tx_label(tx))
        self._request_send_next()

    def handle_response(self, param_name: str, pkt: Any) -> bool:
        """
        Processes incoming packet response. Returns True if the response resolved
        the current query or rescued another in-batch query that timed out early.
        """
        with self.lock:
            if not self.is_running:
                return False

            src_addr = self._packet_src_addr(pkt)

            tx = self.current_transaction
            if tx and tx.expected_response == param_name:
                if src_addr is None or int(src_addr) == int(tx.dst_addr):
                    self._mark_response_success(tx, param_name, pkt)
                    self.timer.stop()
                    self._request_send_next()
                    return True
                log.debug(
                    "Query RX ignored, wrong source: active=%s got_param=%s got_src=%s",
                    self._tx_label(tx),
                    param_name,
                    src_addr,
                )

            rescued_tx = self._find_rescuable_transaction(param_name, src_addr, exclude=tx)
            if rescued_tx is not None:
                self._mark_response_success(rescued_tx, param_name, pkt, late=True)
                return True

            if self._looks_like_response_name(param_name):
                seq_val = pkt.hdr.seq if hasattr(pkt, "hdr") and hasattr(pkt.hdr, "seq") else None
                log.debug(
                    "Query RX did not resolve active query: active=%s got_param=%s got_seq=%s got_src=%s",
                    self._tx_label(tx),
                    param_name,
                    seq_val,
                    src_addr,
                )

        return False

    @staticmethod
    def _packet_src_addr(pkt: Any) -> int | None:
        hdr = getattr(pkt, "hdr", None)
        addr = getattr(hdr, "addr", None)
        src = getattr(addr, "src", None)
        return int(src) if src is not None else None

    def _find_rescuable_transaction(
        self, param_name: str, src_addr: int | None = None, exclude: QueryTransaction | None = None
    ) -> QueryTransaction | None:
        """Find an older in-flight query that this late payload can still satisfy."""
        for item in self.queue:
            if item is exclude:
                continue
            if item.expected_response != param_name:
                continue
            if item.status not in self.RESPONSE_RESCUE_STATES:
                continue
            if src_addr is not None and int(item.dst_addr) != int(src_addr):
                continue
            return item
        return None

    def _mark_response_success(self, tx: QueryTransaction, param_name: str, pkt: Any, late: bool = False) -> None:
        seq_val = pkt.hdr.seq if hasattr(pkt, "hdr") and hasattr(pkt.hdr, "seq") else None
        seq_matches = tx.seq is not None and seq_val is not None and int(seq_val) == int(tx.seq)
        if tx.seq is not None and seq_val is not None and not seq_matches:
            log.debug(
                "Accepting query response '%s' for '%s' despite seq mismatch: expected request seq=%s got response seq=%s",
                param_name,
                tx.command_name,
                tx.seq,
                seq_val,
            )

        tx.status = QueryState.SUCCESS
        tx.received_time = time.monotonic()
        tx.response_packet = pkt
        tx.pending_response_name = ""
        tx.pending_response_packet = None
        tx.pending_response_seq = None

        if seq_val is None:
            seq_val = tx.seq
        seq_str = f" seq={seq_val}" if seq_val is not None else ""
        ack_str = " acked" if tx.ack_received else ""
        late_str = " late" if late else ""
        log.info(
            f"Query RX{late_str}: '{param_name}' <- dst={tx.dst_addr}{seq_str}{ack_str} "
            f"(success for '{tx.command_name}', attempt {tx.retries + 1})"
        )

    def handle_ack(self, ack_seq: int, response: int, src_addr: int | None = None) -> bool:
        """
        Processes incoming ACK packets. For non-GET commands, a matching ACK can
        resolve the current transaction even when the firmware does not send the
        mapped response payload back immediately.
        """
        with self.lock:
            if not self.is_running or not self.current_transaction:
                return False

            tx = self.current_transaction
            if tx.seq is None or int(ack_seq) != int(tx.seq):
                log.debug(
                    "Ignoring ACK for non-active query: active=%s ack_seq=%s response=%s",
                    self._tx_label(tx),
                    ack_seq,
                    response,
                )
                return False

            if src_addr is not None and int(src_addr) != int(tx.dst_addr):
                log.debug(
                    "Ignoring ACK from unexpected device: active=%s ack_seq=%s got_src=%s",
                    self._tx_label(tx),
                    ack_seq,
                    src_addr,
                )
                return False

            if not self._command_accepts_ack_success(tx.command_name):
                tx.ack_received = True
                tx.ack_response = int(response)
                if int(response) != self.ACK_RESPONSE_OK:
                    self.timer.stop()
                    tx.status = QueryState.FAILED
                    tx.received_time = time.monotonic()
                    log.warning(
                        "Query ACK: '%s' seq=%s returned NACK response=%s while waiting for payload '%s'.",
                        tx.command_name,
                        ack_seq,
                        response,
                        tx.expected_response,
                    )
                    self._request_send_next()
                    return True

                if tx.pending_response_packet is not None and tx.pending_response_name == tx.expected_response:
                    tx.status = QueryState.SUCCESS
                    tx.received_time = time.monotonic()
                    tx.response_packet = tx.pending_response_packet
                    seq_val = tx.pending_response_seq if tx.pending_response_seq is not None else tx.seq
                    tx.pending_response_name = ""
                    tx.pending_response_packet = None
                    tx.pending_response_seq = None
                    self.timer.stop()
                    seq_str = f" seq={seq_val}" if seq_val is not None else ""
                    log.info(
                        f"Query RX: '{tx.expected_response}' <- dst={tx.dst_addr}{seq_str} acked "
                        f"(success for '{tx.command_name}', attempt {tx.retries + 1})"
                    )
                    self._request_send_next()
                    return True

                tx.status = QueryState.WAITING
                self.timer.start(max(1, int(self.ACKED_GET_WAIT_S * 1000)))
                log.debug(
                    "ACK received for GET query '%s' seq=%s; extending wait for payload '%s'.",
                    tx.command_name,
                    tx.seq,
                    tx.expected_response,
                )
                return True

            self.timer.stop()
            tx.received_time = time.monotonic()
            if int(response) == self.ACK_RESPONSE_OK:
                tx.status = QueryState.SUCCESS
                log.info(
                    "Query ACK: '%s' seq=%s accepted as success (attempt %s).",
                    tx.command_name,
                    ack_seq,
                    tx.retries + 1,
                )
            else:
                tx.status = QueryState.FAILED
                log.warning(
                    "Query ACK: '%s' seq=%s returned NACK response=%s.",
                    tx.command_name,
                    ack_seq,
                    response,
                )

            self._request_send_next()
            return True

    @staticmethod
    def _command_accepts_ack_success(command_name: str) -> bool:
        """
        GET queries must still wait for their response payload because the UI
        depends on that data. Non-GET commands may complete on transport ACK.
        """
        return not str(command_name or "").endswith("_get")

    def _request_send_next(self) -> None:
        """Schedule the next TX step on this QObject's Qt thread."""
        self._send_next_requested.emit()

    def _send_next(self) -> None:
        """Sends the next pending query from the queue."""
        with self.lock:
            if not self.is_running:
                return

            self.timer.stop()

            pending = [tx for tx in self.queue if tx.status in (QueryState.PENDING, QueryState.RETRY_PENDING)]
            if not pending:
                self.is_running = False
                self.current_transaction = None
                log.info("All queries in queue finished.")
                results = []
                for tx in self.queue:
                    results.append(
                        {
                            "command_name": tx.command_name,
                            "dst_addr": tx.dst_addr,
                            "expected_response": tx.expected_response,
                            "status": tx.status,
                            "retries": tx.retries,
                            "sent_time": tx.sent_time,
                            "received_time": tx.received_time,
                            "seq": tx.seq,
                            "kwargs": dict(tx.kwargs),
                            "priority": tx.priority,
                            "ack_received": tx.ack_received,
                            "ack_response": tx.ack_response,
                            "response_seq": (tx.response_packet.hdr.seq if tx.response_packet is not None and hasattr(tx.response_packet, "hdr") and hasattr(tx.response_packet.hdr, "seq") else None),
                            "response_packet": tx.response_packet,
                        }
                    )
                self.queue.clear()
                if self.on_complete_fn:
                    self.on_complete_fn(results)
                return

            elapsed = time.monotonic() - self._last_send_time
            if self._last_send_time and elapsed < self.INTER_COMMAND_GAP_S:
                delay_ms = max(1, int((self.INTER_COMMAND_GAP_S - elapsed) * 1000))
                QTimer.singleShot(delay_ms, self._request_send_next)
                return

            tx = pending[0]
            self.current_transaction = tx
            tx.status = QueryState.SENT
            tx.sent_time = time.monotonic()
            self._last_send_time = tx.sent_time
            tx.ack_received = False
            tx.ack_response = None
            tx.pending_response_name = ""
            tx.pending_response_packet = None
            tx.pending_response_seq = None
            try:
                sent_pkt = self.send_packet_fn(tx.command_name, tx.dst_addr, **tx.kwargs)
                if sent_pkt is not None:
                    if hasattr(sent_pkt, "hdr") and hasattr(sent_pkt.hdr, "seq"):
                        tx.seq = sent_pkt.hdr.seq
                    elif isinstance(sent_pkt, int):
                        tx.seq = sent_pkt
                else:
                    # Packet was blocked/skipped by CommandBus (e.g. manual test mode or flag disabled)
                    # We mark it failed immediately without waiting for a non-existent timeout
                    tx.status = QueryState.FAILED
                    self._request_send_next()
                    return
            except Exception as e:
                log.error(f"Failed to send query packet: {e}")
                tx.status = QueryState.FAILED
                self._request_send_next()
                return

            seq_str = f" seq={tx.seq}" if tx.seq is not None else ""
            log.info(f"Query TX: '{tx.command_name}' -> dst={tx.dst_addr}{seq_str} (attempt {tx.retries + 1})")
            log.debug("Query armed: %s timeout_s=%.3f", self._tx_label(tx), self.timeout_s)

            if not tx.expected_response:
                tx.status = QueryState.SUCCESS
                tx.received_time = time.monotonic()
                self._request_send_next()
                return

            self.timer.start(max(1, int(self.timeout_s * 1000)))

    def _on_timeout(self) -> None:
        """Handles timeout event for current query."""
        with self.lock:
            if not self.is_running or not self.current_transaction:
                return

            tx = self.current_transaction
            if tx.ack_received and not self._command_accepts_ack_success(tx.command_name):
                if tx.retries < self.max_retries:
                    tx.retries += 1
                    tx.status = QueryState.RETRY_PENDING
                    log.warning(
                        f"Query TIMEOUT waiting for payload '{tx.expected_response}' after ACK to '{tx.command_name}'. "
                        f"Retrying GET request ({tx.retries}/{self.max_retries}) because payload was not observed."
                    )
                else:
                    tx.status = QueryState.TIMEOUT
                    log.error(
                        f"Query TIMEOUT waiting for payload '{tx.expected_response}' after ACK to '{tx.command_name}'. "
                        f"Failed after {self.max_retries} retries."
                    )
            elif tx.retries < self.max_retries:
                tx.retries += 1
                tx.status = QueryState.RETRY_PENDING
                log.warning(
                    f"Query TIMEOUT waiting for '{tx.expected_response}' to '{tx.command_name}'. "
                    f"Retrying ({tx.retries}/{self.max_retries})..."
                )
            else:
                tx.status = QueryState.TIMEOUT
                log.error(
                    f"Query TIMEOUT waiting for '{tx.expected_response}' to '{tx.command_name}'. "
                    f"Failed after {self.max_retries} retries."
                )

        self._request_send_next()

    def reset(self) -> None:
        """Clear the queue and stop any active timeouts/transactions."""
        with self.lock:
            self.timer.stop()
            self.queue.clear()
            self.current_transaction = None
            self.is_running = False
            self._last_send_time = 0.0
            log.info("QueryQueueManager reset successfully.")
