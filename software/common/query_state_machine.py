"""
===============================================================================
  UWB RTLS Studio — Query State Machine
===============================================================================
  File        : common/query_state_machine.py
  Description : Sequential Command-Response Queue & State Machine.
                Prevents serial and BLE link congestion/packet drops by querying
                one command at a time, checking response packets, and handling
                timeouts and retries.
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
    def __init__(self, command_name: str, dst_addr: int, expected_response: str, kwargs: dict):
        self.command_name = command_name
        self.dst_addr = dst_addr
        self.expected_response = expected_response
        self.kwargs = kwargs
        
        self.status = QueryState.PENDING
        self.retries = 0
        self.sent_time = 0.0
        self.received_time = 0.0
        self.response_packet = None
        self.seq = None

class QueryQueueManager(QObject):
    """
    Manages a queue of query transactions. Sends queries sequentially,
    waits for the expected response, and retries on timeout.
    """
    _send_next_requested = pyqtSignal()
    
    RESPONSE_MAP = {
        "device_information_get": "device_information_resp",
        "battery_info_get": "battery_info_resp",
        "time_sync_get": "time_sync_resp",
        "anchor_layout_get": "anchor_layout_resp",
        "sys_config_get": "sys_config_resp",
        "sys_ranging_cfg_get": "sys_ranging_cfg_resp",
        "sensor_fusion_cfg_get": "sensor_fusion_cfg_resp",
        "pos_calib_cfg_get": "pos_calib_cfg_resp",
        "ble_status_get": "ble_status_resp",
        "ble_conn_params_get": "ble_conn_params_resp",
        "ranging_status_get": "ranging_status_resp",
        "calib_status_get": "calib_status_resp",
        "rtos_resource_get": "rtos_resource_resp",
        "rtos_task_stats_get": "rtos_task_stats_resp",
    }

    def __init__(self, send_packet_fn: Callable[[str, int, Dict[str, Any]], Any], 
                 timeout_s: float = 0.2, 
                 max_retries: int = 3, 
                 on_complete_fn: Callable[[List[Dict[str, Any]]], None] | None = None,
                 response_map: Dict[str, str] | None = None,
                 parent=None):
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
            
        tx = QueryTransaction(command_name, dst_addr, expected_response, kwargs)
        with self.lock:
            self.queue.append(tx)

    def start(self) -> None:
        """Start the query process."""
        with self.lock:
            if self.is_running:
                log.warning("QueryQueueManager is already running.")
                return
            self.is_running = True
            
        log.info(f"Starting sequential query queue with {len(self.queue)} commands...")
        self._request_send_next()

    def handle_response(self, param_name: str, pkt: Any) -> bool:
        """
        Processes incoming packet response. Returns True if the response resolved
        the current query.
        """
        with self.lock:
            if not self.is_running or not self.current_transaction:
                return False
            
            tx = self.current_transaction
            if tx.expected_response == param_name:
                tx.status = QueryState.SUCCESS
                tx.received_time = time.monotonic()
                tx.response_packet = pkt
                
                self.timer.stop()
                
                seq_val = pkt.hdr.seq if hasattr(pkt, "hdr") and hasattr(pkt.hdr, "seq") else tx.seq
                seq_str = f" seq={seq_val}" if seq_val is not None else ""
                log.info(f"Query RX: '{param_name}' <- dst={tx.dst_addr}{seq_str} (success for '{tx.command_name}', attempt {tx.retries + 1})")
                
                self._request_send_next()
                return True
                
        return False

    def _request_send_next(self) -> None:
        """Schedule the next TX step on this QObject's Qt thread."""
        self._send_next_requested.emit()

    def _send_next(self) -> None:
        """Sends the next pending query from the queue."""
        with self.lock:
            if not self.is_running:
                return
            
            self.timer.stop()

            # Get first pending/retry query
            pending = [tx for tx in self.queue if tx.status in (QueryState.PENDING, QueryState.RETRY_PENDING)]
            if not pending:
                # All queries processed
                self.is_running = False
                self.current_transaction = None
                log.info("All queries in queue finished.")
                if self.on_complete_fn:
                    # Construct results list
                    results = []
                    for tx in self.queue:
                        results.append({
                            "command_name": tx.command_name,
                            "dst_addr": tx.dst_addr,
                            "expected_response": tx.expected_response,
                            "status": tx.status,
                            "retries": tx.retries,
                            "sent_time": tx.sent_time,
                            "received_time": tx.received_time,
                            "response_packet": tx.response_packet
                        })
                    # Call completion callback
                    self.on_complete_fn(results)
                return

            tx = pending[0]
            self.current_transaction = tx
            tx.status = QueryState.SENT
            tx.sent_time = time.monotonic()

            try:
                sent_pkt = self.send_packet_fn(tx.command_name, tx.dst_addr, **tx.kwargs)
                if sent_pkt is not None:
                    if hasattr(sent_pkt, "hdr") and hasattr(sent_pkt.hdr, "seq"):
                        tx.seq = sent_pkt.hdr.seq
                    elif isinstance(sent_pkt, int):
                        tx.seq = sent_pkt
            except Exception as e:
                log.error(f"Failed to send query packet: {e}")
                tx.status = QueryState.FAILED
                # Attempt to move to next
                self._request_send_next()
                return

            seq_str = f" seq={tx.seq}" if tx.seq is not None else ""
            log.info(f"Query TX: '{tx.command_name}' -> dst={tx.dst_addr}{seq_str} (attempt {tx.retries + 1})")

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
            if tx.retries < self.max_retries:
                tx.retries += 1
                tx.status = QueryState.RETRY_PENDING
                log.warning(f"Query TIMEOUT waiting for '{tx.expected_response}' to '{tx.command_name}'. Retrying ({tx.retries}/{self.max_retries})...")
            else:
                tx.status = QueryState.TIMEOUT
                log.error(f"Query TIMEOUT waiting for '{tx.expected_response}' to '{tx.command_name}'. Failed after {self.max_retries} retries.")
            
        self._request_send_next()
