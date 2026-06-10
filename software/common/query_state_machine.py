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

log = logging.getLogger(__name__)

class QueryState:
    IDLE = "IDLE"
    PENDING = "PENDING"
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

class QueryQueueManager:
    """
    Manages a queue of query transactions. Sends queries sequentially,
    waits for the expected response, and retries on timeout.
    """
    
    # Map command builder name -> expected response packet parameter name
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
    }

    def __init__(self, send_packet_fn: Callable[[str, int, Dict[str, Any]], Any], 
                 timeout_s: float = 0.2, 
                 max_retries: int = 3, 
                 on_complete_fn: Callable[[List[Dict[str, Any]]], None] | None = None):
        """
        Args:
            send_packet_fn: Callback function to execute sending: fn(command_name, dst_addr, **kwargs)
            timeout_s: Time to wait for expected response (in seconds)
            max_retries: Maximum retries per command on timeout
            on_complete_fn: Callback function called when all queries finish
        """
        self.send_packet_fn = send_packet_fn
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.on_complete_fn = on_complete_fn
        
        self.queue: List[QueryTransaction] = []
        self.current_transaction: QueryTransaction | None = None
        self.lock = threading.Lock()
        self.timer: threading.Timer | None = None
        self.is_running = False

    def add_query(self, command_name: str, dst_addr: int, **kwargs) -> None:
        """Add a query to the queue."""
        expected_response = self.RESPONSE_MAP.get(command_name, "")
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
        self._send_next()

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
                
                # Cancel timeout timer
                if self.timer:
                    self.timer.cancel()
                    self.timer = None
                
                log.debug(f"Query SUCCESS: '{tx.command_name}' -> '{param_name}' (retries={tx.retries})")
                
                # Send next query on a separate thread to avoid blocking call stack
                threading.Thread(target=self._send_next, daemon=True).start()
                return True
                
        return False

    def _send_next(self) -> None:
        """Sends the next pending query from the queue."""
        with self.lock:
            if not self.is_running:
                return
            
            # Clean up old timer
            if self.timer:
                self.timer.cancel()
                self.timer = None

            # Get first pending/retry query
            pending = [tx for tx in self.queue if tx.status in (QueryState.PENDING, "RETRY_PENDING")]
            if not pending:
                # All queries processed
                self.is_running = False
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
                    threading.Thread(target=self.on_complete_fn, args=(results,), daemon=True).start()
                return

            tx = pending[0]
            self.current_transaction = tx
            tx.status = QueryState.SENT
            tx.sent_time = time.monotonic()

            log.info(f"Query TX: '{tx.command_name}' -> dst={tx.dst_addr} (attempt {tx.retries + 1})")
            
            try:
                self.send_packet_fn(tx.command_name, tx.dst_addr, **tx.kwargs)
            except Exception as e:
                log.error(f"Failed to send query packet: {e}")
                tx.status = QueryState.FAILED
                # Attempt to move to next
                threading.Thread(target=self._send_next, daemon=True).start()
                return

            # Start timeout timer
            self.timer = threading.Timer(self.timeout_s, self._on_timeout)
            self.timer.daemon = True
            self.timer.start()

    def _on_timeout(self) -> None:
        """Handles timeout event for current query."""
        with self.lock:
            if not self.is_running or not self.current_transaction:
                return

            tx = self.current_transaction
            if tx.retries < self.max_retries:
                tx.retries += 1
                tx.status = "RETRY_PENDING"
                log.warning(f"Query TIMEOUT waiting for '{tx.expected_response}' to '{tx.command_name}'. Retrying ({tx.retries}/{self.max_retries})...")
            else:
                tx.status = QueryState.TIMEOUT
                log.error(f"Query TIMEOUT waiting for '{tx.expected_response}' to '{tx.command_name}'. Failed after {self.max_retries} retries.")
            
        self._send_next()
