from __future__ import annotations
"""
test_ble_log.py  —  Establish BLE connection and capture/view real-time MCU logs.
==================================================================================

Objective:
  1. Auto-probe or connect to the BLE Central Dongle (COM port).
  2. Automatically Scan and locate the BLE Peripheral (TAG/ANCHOR).
  3. Automatically connect to BLE and wait for the CONNECTED state.
  4. Bootstrap a log streaming session with the remote MCU.
  5. Parse, colorize, and print MCU logs in real-time, referencing test_pushing_log.py.
  6. Support optional features like recording UWB coordinates to CSV and calibration logs to text.
  7. Gracefully disconnect and send end_session to the MCU on exit.

Source Address      : HOST (5)
Destination Address : MCU (1) for MCU logs; CENTRAL (3) for Central BLE commands.

Usage:
  python software/vv_testings/gateway_test/test_ble_log.py
  python software/vv_testings/gateway_test/test_ble_log.py --port COM28
"""

import sys
import os
import time
import argparse
import re
import msvcrt
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import serial
from serial import SerialException

# Add parent directories to sys.path
script_dir = Path(__file__).resolve().parent
sys.path.append(str(script_dir.parent))       # software/vv_testings
sys.path.append(str(script_dir.parent.parent))  # software

from common import protocol_pb2 as pb
from common.commands import CommandFactory
from vv_test_session import VvTestSession
from common.transport import VvAddress, HostTransport

# Tuning Constants
READ_TIMEOUT_S = 0.05
LOG_BOOTSTRAP_PACKET_GAP_S = 0.2
LOG_POLL_PERIOD_S = 5.0
LOG_ACK_RETRY_PERIOD_S = 1.0
# Total send attempts for one log ACK, including the first send.
LOG_LOG_ACK_MAX_SEND_ATTEMPTS = 1
# Total send attempts for one keepalive ACK, including the first send.
LOG_KEEPALIVE_ACK_MAX_SEND_ATTEMPTS = 3
LOG_ACK_SETTLE_TIMEOUT_S = 3.0
LOG_IDLE_KEEPALIVE_S = 15.0
LOG_RECV_TIMEOUT_S = 0.01
LOG_END_SESSION_DELAY_S = 0.1
LOG_FIRST_PACKET_TIMEOUT_S = 3.0
PRINT_PACKET_TRACE = True
PACKET_TRACE_TAG_WIDTH = 7
PACKET_TRACE_NAME_WIDTH = 18
PACKET_TRACE_COUNT_WIDTH = 5
PACKET_TRACE_SEQ_WIDTH = 5
PACKET_TRACE_ADDR_WIDTH = 13
MAX_RECORD_LEN = 512
EPOCH_MS_MIN_FOR_DATETIME = 946684800000  # 2000-01-01 00:00:00 UTC

# Color codes
COLOR_GREEN = "\033[32m"
COLOR_RED = "\033[31m"
COLOR_CYAN = "\033[36m"
COLOR_YELLOW = "\033[33m"
COLOR_RESET = "\033[0m"
COLOR_GRAY = "\033[90m"
COLOR_BLUE = "\033[34m"
COLOR_MAGENTA = "\033[35m"


BLE_STATE_NAMES = {
    pb.BLE_STATE_UNSPECIFIED: "UNSPECIFIED",
    pb.BLE_STATE_IDLE: "IDLE",
    pb.BLE_STATE_SCANNING: "SCANNING",
    pb.BLE_STATE_ADVERTISING: "ADVERTISING",
    pb.BLE_STATE_CONNECTING: "CONNECTING",
    pb.BLE_STATE_CONNECTED: "CONNECTED",
}


def packet_name(pkt: pb.packet_t) -> str:
    return pkt.WhichOneof("params") or "<none>"


class FlashLogStreamParser:
    """Parse flash-log stream entries:
    [len_lo][len_hi][raw_record(len)][pad to 4-byte].
    raw_record = [log_type][obj_code][timestamp(6)][msg_len][msg].
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> list[str]:
        self._buf.extend(chunk)
        lines: list[str] = []

        while True:
            if len(self._buf) < 2:
                break

            rec_len = int(self._buf[0]) | (int(self._buf[1]) << 8)
            if rec_len == 0 or rec_len > MAX_RECORD_LEN:
                del self._buf[0:1]
                continue

            entry_len = (2 + rec_len + 3) & ~3
            if len(self._buf) < entry_len:
                break

            rec = bytes(self._buf[2 : 2 + rec_len])
            del self._buf[:entry_len]

            line = self._decode_record(rec)
            if line is not None:
                lines.append(line)

        return lines

    @staticmethod
    def _format_timestamp(timestamp: int) -> str:
        if timestamp >= EPOCH_MS_MIN_FOR_DATETIME:
            try:
                dt = datetime.fromtimestamp(timestamp / 1000.0)
                return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            except (OverflowError, OSError, ValueError):
                pass
        return str(timestamp)

    @staticmethod
    def _decode_record(rec: bytes) -> str | None:
        if len(rec) < 9:
            return None

        log_type = rec[0]
        obj_code = rec[1]
        timestamp = int.from_bytes(rec[2:8], byteorder="little", signed=False)
        msg_len = rec[8]

        if 9 + msg_len > len(rec):
            return None

        msg = rec[9 : 9 + msg_len].decode("utf-8", errors="replace")

        if log_type == 0xFE:
            level = "INFO"
            color = "\033[37m"  # white
        elif log_type == 0xFF:
            level = "DEBUG"
            color = "\033[36m"  # cyan
        elif log_type == 0xFD:
            level = "WARN"
            color = "\033[33m"  # yellow
        else:
            level = "ERROR"
            color = "\033[31m"  # red

        ts_text = FlashLogStreamParser._format_timestamp(timestamp)
        reset = "\033[0m"
        return f"{color}[{ts_text}] [{level:<5}] [0x{obj_code:02X}] {msg}{reset}"


class BleLogTester:
    def __init__(self, session: VvTestSession, factory: CommandFactory, src: int, dst: int,
                 verbose: bool = False, calibration: bool = False, args=None):
        self.session = session
        self.factory = factory
        self.src = src
        self.dst = dst
        self.verbose = verbose
        self.calibration = calibration
        self.log_parser = FlashLogStreamParser()
        self.last_rx_time = time.time()
        self.last_mcu_rx_time = 0.0
        self.latest_mcu_rx_seq: Optional[int] = None
        self.latest_mcu_ackable_seq: Optional[int] = None
        self.log_session_started = False
        self.log_first_packet_seen = False
        self.log_start_deadline = 0.0
        self.last_poll = 0.0
        self.pending_log_ack_seq: Optional[int] = None
        self.pending_log_ack_dst: Optional[int] = None
        self.pending_log_ack_confirm_seq: Optional[int] = None
        self.pending_log_ack_sent_at = 0.0
        self.pending_log_ack_started_at = 0.0
        self.pending_log_ack_retries = 0
        self.pending_none_ack_seq: Optional[int] = None
        self.pending_none_reply_ack_seq: Optional[int] = None
        self.pending_none_reply_ack_dst: Optional[int] = None
        self.pending_none_reply_ack_confirm_seq: Optional[int] = None
        self.pending_none_reply_ack_sent_at = 0.0
        self.pending_none_reply_ack_retries = 0
        self.tx_counts: Dict[str, int] = {}
        self.rx_counts: Dict[str, int] = {}
        self.tx_total = 0
        self.rx_total = 0
        self.tx_ack_sent = 0
        self.tx_log_clear_sent = 0
        self.rx_from_mcu = 0
        self.rx_log_data_bytes = 0
        self.rx_log_records = 0
        self.stop_requested = False

        self.record = args.record if args else None
        self.log_file = None
        self.uwb_file = None

        if self.calibration:
            filename = f"calibration_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            self.log_file = open(filename, "w", encoding="utf-8")
            print(f"{COLOR_GREEN}[+] Saving calibration logs to {filename}{COLOR_RESET}")

        if self.record == "uwb":
            filename = f"uwb_record_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            self.uwb_file = open(filename, "w", encoding="utf-8")
            self.uwb_file.write("timestamp,type,anchor_id,distance,rssi,x,y,z,error_m,frame_error\n")
            self.frame_error_count = 0
            print(f"{COLOR_GREEN}[+] Recording UWB data to {filename}{COLOR_RESET}")

    def __del__(self):
        if hasattr(self, 'log_file') and self.log_file is not None:
            self.log_file.close()
        if hasattr(self, 'uwb_file') and self.uwb_file is not None:
            self.uwb_file.close()

    def _send_packet(self, pkt: pb.packet_t) -> None:
        name = packet_name(pkt)
        self.tx_counts[name] = self.tx_counts.get(name, 0) + 1
        self.tx_total += 1
        count = self.tx_counts[name]
        if name == "ack":
            self.tx_ack_sent += 1
        elif name == "log_clear":
            self.tx_log_clear_sent += 1
        self._print_tx_packet(pkt, name, count)
        self.session.send_packet(pkt)

    @staticmethod
    def _format_counts(counts: Dict[str, int]) -> str:
        if not counts:
            return "none"
        return ",".join(f"{name}={count}" for name, count in sorted(counts.items()))

    @staticmethod
    def _addr_text(value: int) -> str:
        try:
            addr = VvAddress(int(value))
            return f"{addr.name}({int(addr)})"
        except (ValueError, TypeError):
            return f"UNKNOWN({value})"

    @classmethod
    def _packet_hdr_values(cls, pkt: pb.packet_t) -> Tuple[str, str, str]:
        try:
            return (
                str(pkt.hdr.seq),
                cls._addr_text(int(pkt.hdr.addr.src)),
                cls._addr_text(int(pkt.hdr.addr.dst)),
            )
        except (AttributeError, TypeError, ValueError):
            return "?", "?", "?"

    @staticmethod
    def _packet_extra_text(pkt: pb.packet_t, name: str) -> str:
        if name == "ack":
            return f"ack_seq={pkt.ack.ack_seq} response={pkt.ack.response}"
        if name == "log_clear":
            return f"type={pkt.log_clear.type} offset={pkt.log_clear.offset} length={pkt.log_clear.length}"
        if name == "log_data":
            return f"type={pkt.log_data.type} bytes={len(pkt.log_data.data)}"
        if name == "host_transport_set":
            return f"transport={pkt.host_transport_set.transport}"
        return ""

    def _packet_display_name(self, pkt: pb.packet_t, name: str) -> str:
        if name != "ack":
            return name

        try:
            src = VvAddress(int(pkt.hdr.addr.src))
            return f"{src.name.lower()}_ack"
        except (AttributeError, TypeError, ValueError):
            return name

    def _format_packet_trace(self, tag: str, pkt: pb.packet_t, name: str, count: int) -> str:
        seq, src, dst = self._packet_hdr_values(pkt)
        display_name = self._packet_display_name(pkt, name)
        extra = self._packet_extra_text(pkt, name)
        suffix = f" {extra}" if extra else ""
        return (
            f"{tag:<{PACKET_TRACE_TAG_WIDTH}} "
            f"{display_name:<{PACKET_TRACE_NAME_WIDTH}} "
            f"#{count:<{PACKET_TRACE_COUNT_WIDTH}} "
            f"seq={seq:<{PACKET_TRACE_SEQ_WIDTH}} "
            f"src={src:<{PACKET_TRACE_ADDR_WIDTH}} "
            f"dst={dst:<{PACKET_TRACE_ADDR_WIDTH}}"
            f"{suffix}"
        )

    def _print_tx_packet(self, pkt: pb.packet_t, name: str, count: int) -> None:
        if not PRINT_PACKET_TRACE:
            return

        if name == "log_data":
            prefix = "[POLL]"
            color = COLOR_CYAN
        elif name == "ack":
            prefix = "[ACK]"
            color = COLOR_GRAY
        elif name == "log_clear":
            prefix = "[CLEAR]"
            color = COLOR_GRAY
        else:
            prefix = "[TX]"
            color = COLOR_GRAY
        print(
            f"{color}{self._format_packet_trace(prefix, pkt, name, count)}{COLOR_RESET}",
            flush=True,
        )

    def _print_rx_packet(self, pkt: pb.packet_t, name: str, count: int) -> None:
        if not PRINT_PACKET_TRACE:
            return

        if name == "log_data":
            color = COLOR_GRAY
        else:
            color = COLOR_GRAY
        print(
            f"{color}{self._format_packet_trace('[RX]', pkt, name, count)}{COLOR_RESET}",
            flush=True,
        )

    def _build_none(self) -> pb.packet_t:
        pkt = pb.packet_t()
        pkt.hdr.addr.src = self.src
        pkt.hdr.addr.dst = self.dst
        pkt.hdr.seq = self.session.proto.next_seq()
        pkt.none.dummy = 0
        return pkt

    def _send_none(self) -> None:
        pkt = self._build_none()
        self.pending_none_ack_seq = int(pkt.hdr.seq)
        self._send_packet(pkt)

    def _build_transport_set(self) -> pb.packet_t:
        pkt = pb.packet_t()
        pkt.hdr.addr.src = self.src
        pkt.hdr.addr.dst = self.dst
        pkt.hdr.seq = self.session.proto.next_seq()
        pkt.host_transport_set.transport = int(HostTransport.USB)
        return pkt

    def _build_log_data_get(self) -> pb.packet_t:
        pkt = pb.packet_t()
        pkt.hdr.addr.src = self.src
        pkt.hdr.addr.dst = self.dst
        pkt.hdr.seq = self.session.proto.next_seq()
        pkt.log_data.type = pb.LOG_TYPE_DEVICE_LOG
        return pkt

    def _send_log_poll(self) -> None:
        poll_pkt = self._build_log_data_get()
        self._send_packet(poll_pkt)

    def _build_time_sync_set(self) -> pb.packet_t:
        pkt = pb.packet_t()
        pkt.hdr.addr.src = self.src
        pkt.hdr.addr.dst = self.dst
        pkt.hdr.seq = self.session.proto.next_seq()

        now = datetime.now().astimezone()
        offset = now.utcoffset()
        timezone_offset_s = int(offset.total_seconds()) if offset is not None else 0

        pkt.time_sync_set.unix_time_ms = int(time.time() * 1000)
        pkt.time_sync_set.timezone_offset = timezone_offset_s
        return pkt

    def _build_ack(self, ack_seq: int, dst: int) -> pb.packet_t:
        pkt = pb.packet_t()
        pkt.hdr.addr.src = self.src
        pkt.hdr.addr.dst = dst
        pkt.hdr.seq = self.session.proto.next_seq()
        pkt.ack.ack_seq = ack_seq
        pkt.ack.response = pb.PACKET_ACK_RESPONSE_ACK
        return pkt

    def _send_ack_for_packet(self, pkt: pb.packet_t) -> None:
        """Reply to an incoming packet with a transport ACK.

        Keep this separate from log ACK tracking so ACK-of-ACK replies do not
        interfere with the log-state machine.
        """
        ack_pkt = self._build_ack(pkt.hdr.seq, int(pkt.hdr.addr.src))
        self._send_packet(ack_pkt)

    def _track_log_ack(self, ack_pkt: pb.packet_t) -> None:
        self.pending_log_ack_seq = int(ack_pkt.ack.ack_seq)
        self.pending_log_ack_dst = int(ack_pkt.hdr.addr.dst)
        self.pending_log_ack_confirm_seq = int(ack_pkt.hdr.seq)
        self.pending_log_ack_sent_at = time.time()
        self.pending_log_ack_started_at = self.pending_log_ack_sent_at
        self.pending_log_ack_retries = 0

    def _clear_pending_log_ack(self) -> None:
        self.pending_log_ack_seq = None
        self.pending_log_ack_dst = None
        self.pending_log_ack_confirm_seq = None
        self.pending_log_ack_sent_at = 0.0
        self.pending_log_ack_started_at = 0.0
        self.pending_log_ack_retries = 0

    def _track_none_reply_ack(self, ack_pkt: pb.packet_t) -> None:
        self.pending_none_reply_ack_seq = int(ack_pkt.ack.ack_seq)
        self.pending_none_reply_ack_dst = int(ack_pkt.hdr.addr.dst)
        self.pending_none_reply_ack_confirm_seq = int(ack_pkt.hdr.seq)
        self.pending_none_reply_ack_sent_at = time.time()
        self.pending_none_reply_ack_retries = 0

    def _clear_pending_none_reply_ack(self) -> None:
        self.pending_none_reply_ack_seq = None
        self.pending_none_reply_ack_dst = None
        self.pending_none_reply_ack_confirm_seq = None
        self.pending_none_reply_ack_sent_at = 0.0
        self.pending_none_reply_ack_retries = 0

    def request_stop(self) -> None:
        self.stop_requested = True

    def clear_stop_request(self) -> None:
        self.stop_requested = False

    def _retry_pending_log_ack(self, now: float) -> None:
        if self.pending_log_ack_seq is None or self.pending_log_ack_dst is None:
            return

        if self.latest_mcu_ackable_seq is not None and self.pending_log_ack_seq != self.latest_mcu_ackable_seq:
            self._clear_pending_log_ack()
            return

        if self.pending_log_ack_retries >= max(0, LOG_LOG_ACK_MAX_SEND_ATTEMPTS - 1):
            self._clear_pending_log_ack()
            return

        if now - self.pending_log_ack_sent_at < LOG_ACK_RETRY_PERIOD_S:
            return

        self.pending_log_ack_retries += 1
        self.pending_log_ack_sent_at = now
        ack_pkt = self._build_ack(self.pending_log_ack_seq, self.pending_log_ack_dst)
        self._send_packet(ack_pkt)
        self.pending_log_ack_confirm_seq = int(ack_pkt.hdr.seq)

    def _retry_pending_none_reply_ack(self, now: float) -> None:
        if self.pending_none_reply_ack_seq is None or self.pending_none_reply_ack_dst is None:
            return

        if self.latest_mcu_rx_seq is not None and self.pending_none_reply_ack_seq != self.latest_mcu_rx_seq:
            self._clear_pending_none_reply_ack()
            return

        if self.pending_none_reply_ack_retries >= max(0, LOG_KEEPALIVE_ACK_MAX_SEND_ATTEMPTS - 1):
            self._clear_pending_none_reply_ack()
            return

        if now - self.pending_none_reply_ack_sent_at < LOG_ACK_RETRY_PERIOD_S:
            return

        self.pending_none_reply_ack_retries += 1
        self.pending_none_reply_ack_sent_at = now
        ack_pkt = self._build_ack(self.pending_none_reply_ack_seq, self.pending_none_reply_ack_dst)
        self._send_packet(ack_pkt)
        self.pending_none_reply_ack_confirm_seq = int(ack_pkt.hdr.seq)

    def send_end_session(self, reason: int) -> None:
        pkt = pb.packet_t()
        pkt.hdr.addr.src = self.src
        pkt.hdr.addr.dst = self.dst
        pkt.hdr.seq = self.session.proto.next_seq()
        pkt.end_session.reason = reason
        self._send_packet(pkt)

    def bootstrap(self) -> None:
        print("[*] Bootstrapping log session with remote MCU...")
        self._send_none()
        time.sleep(LOG_BOOTSTRAP_PACKET_GAP_S)
        self._send_packet(self._build_time_sync_set())
        time.sleep(LOG_BOOTSTRAP_PACKET_GAP_S)
        self._send_packet(self._build_transport_set())
        time.sleep(LOG_BOOTSTRAP_PACKET_GAP_S)
        self._send_log_poll()
        self.log_session_started = True
        self.log_start_deadline = time.time() + LOG_FIRST_PACKET_TIMEOUT_S
        self.last_poll = time.time()
        print(f"[*] Waiting up to {LOG_FIRST_PACKET_TIMEOUT_S:.1f}s for first log packet...")

    def _handle_log_data_packet(self, pkt: pb.packet_t) -> None:
        self.log_first_packet_seen = True
        payload = bytes(pkt.log_data.data)
        lines = self.log_parser.feed(payload)
        self.rx_log_data_bytes += len(payload)
        self.rx_log_records += len(lines)
        for line in lines:
            print(line)

            # Strip ANSI escape codes for local logging
            clean_line = re.sub(r'\x1b\[[0-9;]*m', '', line)

            if self.calibration and self.log_file is not None:
                self.log_file.write(clean_line + '\n')
                self.log_file.flush()

            if self.record == "uwb" and self.uwb_file is not None:
                if "[ERROR]" in clean_line:
                    self.frame_error_count += 1

                # Match primary distance: "[TAG] Distance: 6.652 m [A:4 RSSI:0dBm]"
                dist_match = re.search(r"Distance:\s+([\d.]+)\s+m\s+\[A:(\d+)\s+RSSI:(-?\d+)dBm\]", clean_line)
                if dist_match:
                    dist, aid, rssi = dist_match.group(1), dist_match.group(2), dist_match.group(3)
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                    self.uwb_file.write(f"{ts},distance,{aid},{dist},{rssi},,,, ,{self.frame_error_count}\n")
                    self.uwb_file.flush()
                    continue

                # Match position: "Tril Px=1.234m Py=5.678m Z=0.44m | Error: +/-0.100m"
                pos_match = re.search(r"Tril Px=([\d.-]+)m Py=([\d.-]+)m Z=([\d.-]+)m\s+\|\s+Error:\s+.([\d.]+)", clean_line)
                if pos_match:
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                    x, y, z, err = pos_match.group(1), pos_match.group(2), pos_match.group(3), pos_match.group(4)
                    self.uwb_file.write(f"{ts},position,,, ,{x},{y},{z},{err},{self.frame_error_count}\n")
                    self.uwb_file.flush()
                elif "[ERROR]" in clean_line:
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                    self.uwb_file.write(f"{ts},error,,,,,,,,{self.frame_error_count}\n")
                    self.uwb_file.flush()

    def _process_packet(self, pkt: pb.packet_t) -> None:
        self.last_rx_time = time.time()
        name = packet_name(pkt)
        self.rx_counts[name] = self.rx_counts.get(name, 0) + 1
        self.rx_total += 1
        self._print_rx_packet(pkt, name, self.rx_counts[name])
        try:
            if int(pkt.hdr.addr.src) == int(VvAddress.MCU):
                self.rx_from_mcu += 1
                self.last_mcu_rx_time = self.last_rx_time
                self.latest_mcu_rx_seq = int(pkt.hdr.seq)
                if name != "ack":
                    self.latest_mcu_ackable_seq = int(pkt.hdr.seq)
                    self._clear_pending_none_reply_ack()

                if name != "ack":
                    self._send_ack_for_packet(pkt)
                    if name == "log_data":
                        ack_pkt = self._build_ack(pkt.hdr.seq, int(pkt.hdr.addr.src))
                        self._track_log_ack(ack_pkt)
        except (AttributeError, ValueError):
            pass

        if name == "ack":
            try:
                if int(pkt.hdr.addr.src) == int(VvAddress.MCU):
                    if self.pending_none_ack_seq is not None and int(pkt.ack.ack_seq) == self.pending_none_ack_seq:
                        ack_pkt = self._build_ack(int(pkt.hdr.seq), int(pkt.hdr.addr.src))
                        self._send_packet(ack_pkt)
                        self._track_none_reply_ack(ack_pkt)
                        self.pending_none_ack_seq = None
                    if self.pending_log_ack_confirm_seq is not None and int(pkt.ack.ack_seq) == self.pending_log_ack_confirm_seq:
                        print(f"[FLOW]  host_ack confirmed by MCU seq={self.pending_log_ack_confirm_seq}")
                        self._clear_pending_log_ack()
                    if (
                        self.pending_none_reply_ack_confirm_seq is not None
                        and int(pkt.ack.ack_seq) == self.pending_none_reply_ack_confirm_seq
                    ):
                        self._clear_pending_none_reply_ack()
            except (AttributeError, ValueError):
                pass

        if name == "log_data":
            self._handle_log_data_packet(pkt)
            return

            payload = bytes(pkt.log_data.data)
            lines = self.log_parser.feed(payload)
            self.rx_log_data_bytes += len(payload)
            self.rx_log_records += len(lines)
            for line in lines:
                print(line)

                # Strip ANSI escape codes for local logging
                clean_line = re.sub(r'\x1b\[[0-9;]*m', '', line)

                if self.calibration and self.log_file is not None:
                    self.log_file.write(clean_line + '\n')
                    self.log_file.flush()

                if self.record == "uwb" and self.uwb_file is not None:
                    if "[ERROR]" in clean_line:
                        self.frame_error_count += 1

                    # Match primary distance: "[TAG] Distance: 6.652 m [A:4 RSSI:0dBm]"
                    dist_match = re.search(r"Distance:\s+([\d.]+)\s+m\s+\[A:(\d+)\s+RSSI:(-?\d+)dBm\]", clean_line)
                    if dist_match:
                        dist, aid, rssi = dist_match.group(1), dist_match.group(2), dist_match.group(3)
                        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                        self.uwb_file.write(f"{ts},distance,{aid},{dist},{rssi},,,, ,{self.frame_error_count}\n")
                        self.uwb_file.flush()
                        continue

                    # Match position: "Tril Px=1.234m Py=5.678m Z=0.44m | Error: ±0.100m"
                    pos_match = re.search(r"Tril Px=([\d.-]+)m Py=([\d.-]+)m Z=([\d.-]+)m\s+\|\s+Error:\s+.([\d.]+)", clean_line)
                    if pos_match:
                        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                        x, y, z, err = pos_match.group(1), pos_match.group(2), pos_match.group(3), pos_match.group(4)
                        self.uwb_file.write(f"{ts},position,,, ,{x},{y},{z},{err},{self.frame_error_count}\n")
                        self.uwb_file.flush()
                    elif "[ERROR]" in clean_line:
                        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                        self.uwb_file.write(f"{ts},error,,,,,,,,{self.frame_error_count}\n")
                        self.uwb_file.flush()

        if name == "ble_status_resp":
            state = pkt.ble_status_resp.state
            state_name = BLE_STATE_NAMES.get(state, f"UNKNOWN({state})")
            if pkt.ble_status_resp.HasField("disconnect_reason"):
                reason = pkt.ble_status_resp.disconnect_reason
                print(f"\n{COLOR_YELLOW}[!] BLE Status Change -> {state_name} | disconnect_reason=0x{reason:02X}{COLOR_RESET}")
                if state == pb.BLE_STATE_IDLE:
                    print(f"{COLOR_RED}[WARNING] BLE disconnected. Peer closed connection or link timed out.{COLOR_RESET}")
            else:
                print(f"\n{COLOR_CYAN}[!] BLE Status Change -> {state_name}{COLOR_RESET}")
            return

        if self.verbose:
            print(f"[PKT] {name} src={pkt.hdr.addr.src} dst={pkt.hdr.addr.dst} seq={pkt.hdr.seq}")

    def loop(self) -> None:
        while True:
            self.poll_once()
            if self.stop_requested:
                return

    def poll_once(self) -> None:
        if self.stop_requested:
            return

        packets = self.session.recv_packets(timeout_s=LOG_RECV_TIMEOUT_S)
        for pkt in packets:
            self._process_packet(pkt)

        now = time.time()
        self._retry_pending_log_ack(now)
        self._retry_pending_none_reply_ack(now)

        if (
            self.log_session_started
            and not self.log_first_packet_seen
            and now >= self.log_start_deadline
            and self.pending_log_ack_seq is None
            and now - self.last_poll >= LOG_POLL_PERIOD_S
        ):
            self._send_log_poll()
            self.last_poll = now

        # Keep-alive ping: only send if we haven't received anything from MCU for a while.
        # This completely avoids packet collisions while logs are actively streaming.
        if now - self.last_rx_time >= LOG_IDLE_KEEPALIVE_S:
            self._send_none()
            self.last_rx_time = now


def _device_uuid_text(p: pb.packet_t) -> str:
    scan = p.ble_scan_result
    if hasattr(scan, "uuid") and scan.uuid:
        raw = bytes(scan.uuid)
        if len(raw) == 16:
            return "-".join(
                [
                    raw[0:4].hex(),
                    raw[4:6].hex(),
                    raw[6:8].hex(),
                    raw[8:10].hex(),
                    raw[10:16].hex(),
                ]
            ).upper()
        return raw.hex().upper()
    return ""


def step_auto_scan_and_connect(session: VvTestSession, factory: CommandFactory,
                               src: int, central_dst: int, scan_timeout_s: float = 6.0,
                               expected_mac: Optional[bytes] = None, target_name_filter: Optional[str] = None) -> Optional[Tuple[bytes, str]]:
    print("\n" + "=" * 58)
    print("  STEP 0: Auto-Scan & BLE Connection")
    print("=" * 58)
    print("[+] Sending BLE Scan Start command to Central...")
    pkt = factory.ble_scan_start(src, central_dst, session.proto.next_seq())
    session.send_packet(pkt)

    discovered_devices: Dict[bytes, Tuple[str, int, str]] = {}  # mac_bytes -> (name, rssi, uuid)

    if expected_mac:
        mac_str_target = ":".join(f"{b:02X}" for b in reversed(expected_mac))
        print(f"[+] Scanning specifically for MAC: {mac_str_target}...")
    else:
        filter_desc = target_name_filter if target_name_filter else "UWB, TAG, ANCHOR, NUS, RTLS"
        print(f"[+] Scanning for target UWB Peripherals matching: {filter_desc}...")

    print(f"[*] Scanning for {scan_timeout_s:.1f} seconds to gather active devices...")
    deadline = time.time() + scan_timeout_s
    while time.time() < deadline:
        pkts = session.recv_packets(timeout_s=0.1)
        for p in pkts:
            if p.WhichOneof("params") == "ble_scan_result":
                mac_bytes = p.ble_scan_result.mac_address
                mac_str = ":".join(f"{b:02X}" for b in reversed(mac_bytes))
                name = p.ble_scan_result.name
                rssi = p.ble_scan_result.rssi_dbm
                uuid_text = _device_uuid_text(p)

                if mac_bytes not in discovered_devices:
                    matched = False
                    if expected_mac:
                        matched = (mac_bytes == expected_mac)
                    else:
                        name_upper = name.upper()
                        uuid_upper = uuid_text.upper()
                        if target_name_filter:
                            matched = target_name_filter.upper() in name_upper or target_name_filter.upper() in uuid_upper
                        else:
                            matched = any(prefix in name_upper for prefix in ["UWB", "TAG", "ANCHOR", "NUS", "RTLS", "NODE"]) or bool(uuid_text)

                    if matched:
                        discovered_devices[mac_bytes] = (name, rssi, uuid_text)
                        uuid_part = f" | UUID: {uuid_text}" if uuid_text else ""
                        print(f"  [Scan] Found matching device: {mac_str} ('{name}'){uuid_part} | RSSI: {rssi} dBm")
        
        if expected_mac and expected_mac in discovered_devices:
            break

    print("[-] Stopping BLE Scan...")
    session.send_packet(factory.ble_scan_stop(src, central_dst, session.proto.next_seq()))
    time.sleep(0.5)

    if not discovered_devices:
        print(f"\n{COLOR_RED}[FAIL] No matching BLE Peripheral device found.{COLOR_RESET}")
        return None

    selected_mac = None
    selected_name = None

    device_list = list(discovered_devices.items())
    if len(device_list) == 1:
        selected_mac, (selected_name, _, _) = device_list[0]
        mac_str = ":".join(f"{b:02X}" for b in reversed(selected_mac))
        print(f"\n{COLOR_GREEN}[+] Automatically selecting the only found device: '{selected_name}' ({mac_str}){COLOR_RESET}")
    else:
        print("\n--- DISCOVERED BLE DEVICES ---")
        for idx, (mac_bytes, (name, rssi, uuid_text)) in enumerate(device_list, 1):
            mac_str = ":".join(f"{b:02X}" for b in reversed(mac_bytes))
            uuid_part = f" | UUID: {uuid_text}" if uuid_text else ""
            print(f"  [{idx}] {mac_str} | Name: '{name}'{uuid_part} | RSSI: {rssi:4d} dBm")
        print("------------------------------")

        while True:
            try:
                choice = input(f"Enter device index (1-{len(device_list)}) or MAC [default first device]: ").strip()
                if not choice:
                    choice_idx = 1
                    selected_mac, (selected_name, _, _) = device_list[choice_idx - 1]
                    break

                if choice.isdigit():
                    choice_idx = int(choice)
                    if 1 <= choice_idx <= len(device_list):
                        selected_mac, (selected_name, _, _) = device_list[choice_idx - 1]
                        break
                    print(f"Invalid index. Please enter a number between 1 and {len(device_list)}.")
                    continue

                if ":" in choice:
                    mac_input = choice.upper()
                    normalized = bytes(reversed([int(x, 16) for x in mac_input.split(":")]))
                    if normalized in discovered_devices:
                        selected_mac = normalized
                        selected_name = discovered_devices[normalized][0]
                        break
                    print(f"Invalid MAC. Please enter one of the discovered devices.")
                    continue

                print("Invalid input. Please enter a device index or MAC address in format AA:BB:CC:DD:EE:FF.")
            except ValueError:
                print("Invalid input. Please enter a device index or MAC address in format AA:BB:CC:DD:EE:FF.")
            except KeyboardInterrupt:
                print("\n[ABORT] User cancelled device selection.")
                return None

    mac_str = ":".join(f"{b:02X}" for b in reversed(selected_mac))
    max_conn_attempts = 3
    connected = False

    for attempt in range(1, max_conn_attempts + 1):
        print(f"[+] Sending BLE connect command to {mac_str} (attempt {attempt}/{max_conn_attempts})...")
        pkt = factory.ble_connect(src, central_dst, session.proto.next_seq())
        pkt.ble_connect.mac_address = selected_mac
        session.send_packet(pkt)

        print("[+] Waiting for BLE connection...")
        connect_deadline = time.time() + 15.0
        retry_needed = False

        while time.time() < connect_deadline:
            pkts = session.recv_packets(timeout_s=0.1)
            for p in pkts:
                if p.WhichOneof("params") == "ble_status_resp":
                    state = p.ble_status_resp.state
                    if state == pb.BLE_STATE_CONNECTED:
                        print(f"{COLOR_GREEN}[OK] BLE CONNECTION ESTABLISHED!{COLOR_RESET}")
                        connected = True
                        break
                    elif state == pb.BLE_STATE_IDLE and p.ble_status_resp.HasField("disconnect_reason"):
                        reason = p.ble_status_resp.disconnect_reason
                        print(f"  {COLOR_YELLOW}[WARNING] Connection attempt {attempt} failed. Reason: 0x{reason:02X}{COLOR_RESET}")
                        retry_needed = True
                        break
            if connected or retry_needed:
                break

        if connected:
            break

        if attempt < max_conn_attempts:
            print("  [INFO] Sending disconnect to clean up Central connection state...")
            session.send_packet(factory.ble_disconnect(src, central_dst, session.proto.next_seq()))
            print("[+] Waiting 1.5s before retrying connection...")
            time.sleep(1.5)

    if not connected:
        print(f"{COLOR_RED}[FAIL] BLE connection timed out or failed permanently after all retries.{COLOR_RESET}")
        return None

    # Ensure scan is fully stopped before starting the log session.
    session.send_packet(factory.ble_scan_stop(src, central_dst, session.proto.next_seq()))
    time.sleep(0.2)
    time.sleep(1.0)
    return selected_mac, selected_name


def _poll_hotkey() -> Optional[str]:
    while msvcrt.kbhit():
        ch = msvcrt.getwch()
        if ch in ("q", "Q", "x", "X"):
            return "stop"
        if ch in ("p", "P"):
            return "poll"
    return None


def _run_log_session(session: VvTestSession, factory: CommandFactory, src_addr: int, mcu_dst: int,
                     tester: BleLogTester, central_dst: int) -> None:
    tester.clear_stop_request()
    print(f"\n{COLOR_CYAN}=== STARTING BLE LOG VIEWER ==={COLOR_RESET}")
    print(f"{COLOR_YELLOW}[HOTKEY] Press 'p' to send log_data_get. Press 'q' or 'x' to stop log, disconnect, and rescan.{COLOR_RESET}")
    tester.bootstrap()

    try:
        while not tester.stop_requested:
            tester.poll_once()

            hotkey = _poll_hotkey()
            if hotkey == "stop":
                tester.request_stop()
                break
            if hotkey == "poll":
                print("[HOTKEY] Sending log_data_get...")
                tester._send_log_poll()
                tester.last_poll = time.time()
    finally:
        print(f"\n{COLOR_YELLOW}[*] Stopping log session... sending end_session and disconnecting current device.{COLOR_RESET}")
        try:
            tester.send_end_session(pb.SESSION_END_REASON_LOG_DATA)
            time.sleep(LOG_END_SESSION_DELAY_S)
            session.send_packet(factory.ble_disconnect(src_addr, central_dst, session.proto.next_seq()))
            time.sleep(0.2)
        except Exception as exc:
            print(f"{COLOR_RED}[WARN] Failed to stop session cleanly: {exc}{COLOR_RESET}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Real-time BLE capture and MCU logs viewer")
    parser.add_argument("--port", default=None, help="COM port of the Central Dongle (e.g. COM7)")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate")
    parser.add_argument("--mac", type=str, default=None, help="Connect directly to this MAC (hex with colons, e.g. AA:BB:CC:DD:EE:FF)")
    parser.add_argument("--name", type=str, default=None, help="Scan only for devices containing this string in their name")
    parser.add_argument("--verbose", action="store_true", help="Print all non-log packet types")
    parser.add_argument("--calibration", action="store_true", help="Save logs to a calibration file")
    parser.add_argument("--record", choices=["uwb"], help="Record specific UWB data to a CSV file")
    parser.add_argument("--src", type=int, default=int(VvAddress.HOST), help="Source address (default: HOST=5)")
    parser.add_argument("--dst", type=int, default=int(VvAddress.MCU), help="MCU Destination address (default: MCU=1)")
    parser.add_argument("--central-dst", type=int, default=int(VvAddress.CENTRAL), help="Central Destination address (default: CENTRAL=3)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    # Source: HOST (5) default
    # Destination: MCU (1) default for MCU logs; CENTRAL (3) default for Central BLE commands
    src_addr = args.src
    mcu_dst = args.dst
    central_dst = args.central_dst

    # Decode specific MAC address if provided
    expected_mac = None
    if args.mac:
        try:
            expected_mac = bytes(reversed([int(x, 16) for x in args.mac.split(":")]))
            if len(expected_mac) != 6:
                raise ValueError("MAC must be 6 bytes")
        except Exception as e:
            print(f"{COLOR_RED}[ERROR] Invalid MAC address format: {args.mac}. Example: AA:BB:CC:DD:EE:FF ({e}){COLOR_RESET}")
            return 1

    port = args.port
    try:
        if not port:
            print("[*] Probing for Central Dongle COM port automatically...")
            # Use HOST as probing source
            probe = VvTestSession.auto_probe(src=src_addr, debug=args.verbose)
            if probe is None:
                # Fallback to JLink or USB port matching
                import serial.tools.list_ports
                ports = serial.tools.list_ports.comports()
                for p in ports:
                    if 'USB' in p.description or 'JLink' in p.description or 'Serial' in p.description:
                        port = p.device
                        print(f"[+] Found USB serial port via fallback scanning: {port}")
                        break
                if not port:
                    print(f"{COLOR_RED}[ERROR] No serial port found. Connect Central Dongle and/or use --port COMx{COLOR_RESET}")
                    return 2
            else:
                port = probe.port

        factory = CommandFactory()

        with VvTestSession(port, args.baud, debug=args.verbose) as session:
            while True:
                res = step_auto_scan_and_connect(
                    session=session,
                    factory=factory,
                    src=src_addr,
                    central_dst=central_dst,
                    expected_mac=expected_mac,
                    target_name_filter=args.name
                )
                if not res:
                    return 1

                tester = BleLogTester(
                    session=session,
                    factory=factory,
                    src=src_addr,
                    dst=mcu_dst,
                    verbose=args.verbose,
                    calibration=args.calibration,
                    args=args
                )

                _run_log_session(session, factory, src_addr, mcu_dst, tester, central_dst)
                print(f"{COLOR_GREEN}[+] Log session stopped. Re-entering scan flow...{COLOR_RESET}")

    except KeyboardInterrupt:
        print(f"\n{COLOR_YELLOW}[*] Stopping...{COLOR_RESET}")
        return 0
    except SerialException as exc:
        print(f"{COLOR_RED}[ERROR] Serial error: {exc}{COLOR_RESET}")
        return 1
    except Exception as exc:
        print(f"{COLOR_RED}[ERROR] Unexpected error: {exc}{COLOR_RESET}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
