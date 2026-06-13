from __future__ import annotations
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


import argparse
import time
from datetime import datetime
from typing import Dict, Iterable, Optional, Tuple

import serial
from serial import SerialException

from common import protocol_pb2 as pb
from common.parser_protocol import HostTransport, VvAddress, VvProtocol
from vv_test_session import VvTestSession

BAUD_DEFAULT = 115200
READ_TIMEOUT_S = 0.05
HOST_ACTIVITY_PING_S = 5.0
LOG_POLL_PERIOD_S = 1.0
LOG_ACK_RETRY_PERIOD_S = 1.0
LOG_ACK_RETRY_MAX_RETRIES = 10  # 0 = retry forever
PRINT_PACKET_TRACE = True
PACKET_TRACE_TAG_WIDTH = 7
PACKET_TRACE_NAME_WIDTH = 18
PACKET_TRACE_COUNT_WIDTH = 5
PACKET_TRACE_SEQ_WIDTH = 5
PACKET_TRACE_ADDR_WIDTH = 13
MAX_RECORD_LEN = 512
EPOCH_MS_MIN_FOR_DATETIME = 946684800000  # 2000-01-01 00:00:00 UTC

COLOR_CYAN = "\033[36m"
COLOR_GRAY = "\033[90m"
COLOR_RESET = "\033[0m"


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


class LogRealtimeTester:
    def __init__(self, ser: serial.Serial, proto: VvProtocol, src: int, dst: int, verbose: bool = False, clear_first: bool = False, calibration: bool = False, args=None):
        self.ser = ser
        self.proto = proto
        self.src = src
        self.dst = dst
        self.verbose = verbose
        self.clear_first = clear_first
        self.calibration = calibration
        self.log_parser = FlashLogStreamParser()
        self.last_ping = 0.0
        self.last_mcu_rx_time = 0.0
        self.last_poll = 0.0
        self.pending_log_ack_seq: Optional[int] = None
        self.pending_log_ack_dst: Optional[int] = None
        self.pending_log_ack_confirm_seq: Optional[int] = None
        self.pending_log_ack_sent_at = 0.0
        self.pending_log_ack_retries = 0
        self.tx_counts: Dict[str, int] = {}
        self.rx_counts: Dict[str, int] = {}
        
        self.record = args.record
        self.log_file = None
        self.uwb_file = None

        if self.calibration:
            filename = f"calibration_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            self.log_file = open(filename, "w", encoding="utf-8")
            print(f"Saving calibration logs to {filename}")

        if self.record == "uwb":
            filename = f"uwb_record_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            self.uwb_file = open(filename, "w", encoding="utf-8")
            self.uwb_file.write("timestamp,type,anchor_id,distance,rssi,x,y,z,error_m,frame_error\n")
            self.frame_error_count = 0
            print(f"Recording UWB data to {filename}")

    def __del__(self):
        if hasattr(self, 'log_file') and self.log_file is not None:
            self.log_file.close()
        if hasattr(self, 'uwb_file') and self.uwb_file is not None:
            self.uwb_file.close()

    def _send_packet(self, pkt: pb.packet_t) -> None:
        name = packet_name(pkt)
        self.tx_counts[name] = self.tx_counts.get(name, 0) + 1
        self._print_tx_packet(pkt, name, self.tx_counts[name])

        frame = self.proto.wrap_packet(pkt)
        self.ser.write(frame)
        self.ser.flush()

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

        print(
            f"{COLOR_GRAY}{self._format_packet_trace('[RX]', pkt, name, count)}{COLOR_RESET}",
            flush=True,
        )

    def _build_none(self) -> pb.packet_t:
        pkt = pb.packet_t()
        pkt.hdr.addr.src = self.src
        pkt.hdr.addr.dst = self.dst
        pkt.hdr.seq = self.proto.next_seq()
        pkt.none.dummy = 0
        return pkt

    def _build_transport_set(self) -> pb.packet_t:
        pkt = pb.packet_t()
        pkt.hdr.addr.src = self.src
        pkt.hdr.addr.dst = self.dst
        pkt.hdr.seq = self.proto.next_seq()
        pkt.host_transport_set.transport = int(HostTransport.USB)
        return pkt

    def _build_device_information_get(self) -> pb.packet_t:
        pkt = pb.packet_t()
        pkt.hdr.addr.src = self.src
        pkt.hdr.addr.dst = self.dst
        pkt.hdr.seq = self.proto.next_seq()
        pkt.device_information_get.dummy = 0
        return pkt

    def _build_log_data_get(self) -> pb.packet_t:
        pkt = pb.packet_t()
        pkt.hdr.addr.src = self.src
        pkt.hdr.addr.dst = self.dst
        pkt.hdr.seq = self.proto.next_seq()
        pkt.log_data.type = pb.LOG_TYPE_DEVICE_LOG
        return pkt

    def _build_time_sync_set(self) -> pb.packet_t:
        pkt = pb.packet_t()
        pkt.hdr.addr.src = self.src
        pkt.hdr.addr.dst = self.dst
        pkt.hdr.seq = self.proto.next_seq()

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
        pkt.hdr.seq = self.proto.next_seq()
        pkt.ack.ack_seq = ack_seq
        pkt.ack.response = pb.PACKET_ACK_RESPONSE_ACK
        return pkt

    def _track_log_ack(self, ack_pkt: pb.packet_t) -> None:
        self.pending_log_ack_seq = int(ack_pkt.ack.ack_seq)
        self.pending_log_ack_dst = int(ack_pkt.hdr.addr.dst)
        self.pending_log_ack_confirm_seq = int(ack_pkt.hdr.seq)
        self.pending_log_ack_sent_at = time.time()
        self.pending_log_ack_retries = 0

    def _clear_pending_log_ack(self) -> None:
        self.pending_log_ack_seq = None
        self.pending_log_ack_dst = None
        self.pending_log_ack_confirm_seq = None
        self.pending_log_ack_sent_at = 0.0
        self.pending_log_ack_retries = 0

    def _retry_pending_log_ack(self, now: float) -> None:
        if self.pending_log_ack_seq is None or self.pending_log_ack_dst is None:
            return

        last_ack_activity = max(self.pending_log_ack_sent_at, self.last_mcu_rx_time)
        if now - last_ack_activity < LOG_ACK_RETRY_PERIOD_S:
            return

        if LOG_ACK_RETRY_MAX_RETRIES > 0 and self.pending_log_ack_retries >= LOG_ACK_RETRY_MAX_RETRIES:
            self._clear_pending_log_ack()
            return

        self.pending_log_ack_retries += 1
        self.pending_log_ack_sent_at = now
        ack_pkt = self._build_ack(self.pending_log_ack_seq, self.pending_log_ack_dst)
        self._send_packet(ack_pkt)
        self.pending_log_ack_confirm_seq = int(ack_pkt.hdr.seq)

    def _build_log_clear_all(self) -> pb.packet_t:
        pkt = pb.packet_t()
        pkt.hdr.addr.src = self.src
        pkt.hdr.addr.dst = self.dst
        pkt.hdr.seq = self.proto.next_seq()
        pkt.log_clear.type = pb.LOG_TYPE_DEVICE_LOG
        pkt.log_clear.offset = 0
        pkt.log_clear.length = 0xFFFFFFFF
        return pkt

    def send_end_session(self, reason: int) -> None:
        pkt = pb.packet_t()
        pkt.hdr.addr.src = self.src
        pkt.hdr.addr.dst = self.dst
        pkt.hdr.seq = self.proto.next_seq()
        pkt.end_session.reason = reason
        self._send_packet(pkt)

    def bootstrap(self) -> None:
        self._send_packet(self._build_none())
        time.sleep(0.05)
        self._send_packet(self._build_device_information_get())
        time.sleep(0.05)
        self._send_packet(self._build_time_sync_set())
        time.sleep(0.05)
        self._send_packet(self._build_transport_set())
        time.sleep(0.05)
        if self.clear_first:
            self._send_packet(self._build_log_clear_all())
            time.sleep(0.05)
        self._send_packet(self._build_log_data_get())

    def _process_packet(self, pkt: pb.packet_t) -> None:
        name = packet_name(pkt)
        self.rx_counts[name] = self.rx_counts.get(name, 0) + 1
        self._print_rx_packet(pkt, name, self.rx_counts[name])
        try:
            if int(pkt.hdr.addr.src) == int(VvAddress.MCU):
                self.last_mcu_rx_time = time.time()
                self.pending_log_ack_retries = 0
        except (AttributeError, ValueError):
            pass

        if name == "ack" and self.pending_log_ack_confirm_seq is not None:
            try:
                if int(pkt.hdr.addr.src) == int(VvAddress.MCU) and int(pkt.ack.ack_seq) == self.pending_log_ack_confirm_seq:
                    print(f"[FLOW]  host_ack confirmed by MCU seq={self.pending_log_ack_confirm_seq}")
                    self._clear_pending_log_ack()
            except (AttributeError, ValueError):
                pass

        if name == "log_data":
            payload = bytes(pkt.log_data.data)
            lines = self.log_parser.feed(payload)
            for line in lines:
                print(line)
                
                # Strip ANSI escape codes for processing
                import re
                clean_line = re.sub(r'\x1b\[[0-9;]*m', '', line)

                if self.calibration and self.log_file is not None:
                    self.log_file.write(clean_line + '\n')
                    self.log_file.flush()

                if self.record == "uwb" and self.uwb_file is not None:
                    # Increment frame error count if log level is ERROR
                    if "[ERROR]" in clean_line:
                        self.frame_error_count += 1

                    # Match primary distance: "[TAG] Distance: 6.652 m [A:4 RSSI:0dBm] [C:1]"
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
                        # Log error record even if no distance/pos matched
                        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                        self.uwb_file.write(f"{ts},error,,,,,,,,{self.frame_error_count}\n")
                        self.uwb_file.flush()

            ack_pkt = self._build_ack(pkt.hdr.seq, int(pkt.hdr.addr.src))
            self._send_packet(ack_pkt)
            self._track_log_ack(ack_pkt)
            return

        if self.verbose:
            print(f"[PKT] {name} src={pkt.hdr.addr.src} dst={pkt.hdr.addr.dst} seq={pkt.hdr.seq}")

    def loop(self) -> None:
        while True:
            now = time.time()
            self._retry_pending_log_ack(now)

            if now - self.last_ping >= HOST_ACTIVITY_PING_S:
                self._send_packet(self._build_none())
                self.last_ping = now

            if self.pending_log_ack_seq is None and now - self.last_poll >= LOG_POLL_PERIOD_S:
                self._send_packet(self._build_log_data_get())
                self.last_poll = now

            raw = self.ser.read(self.ser.in_waiting or 1)
            if not raw:
                continue

            packets = self.proto.decode_from_frames(raw)
            for pkt in packets:
                self._process_packet(pkt)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Realtime device-log test (only log output)")
    parser.add_argument("--port", default=None, help="COM port, example COM7")
    parser.add_argument("--baud", type=int, default=BAUD_DEFAULT, help="Baud rate")
    parser.add_argument(
        "--dst",
        type=int,
        default=int(VvAddress.MCU),
        help="Device destination address (default: MCU=1)",
    )
    parser.add_argument(
        "--src",
        type=int,
        default=int(VvAddress.DEBUG),
        help="Source address of the script (default: DEBUG=7 for UART-BLE and when testing via USB where HOST routes to BLE)",
    )
    parser.add_argument("--verbose", action="store_true", help="Print non-log packets")
    parser.add_argument(
        "--clear-first",
        action="store_true",
        help="Clear existing flash log backlog once before realtime streaming",
    )
    parser.add_argument(
        "--calibration",
        action="store_true",
        help="Save log to a file for calibration testing",
    )
    parser.add_argument(
        "--record",
        choices=["uwb"],
        help="Record specific data (uwb) to a CSV file",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    port = args.port
    try:
        if not port:
            probe = VvTestSession.auto_probe(src=args.src, debug=args.verbose)
            if probe is None:
                print("No serial port found or device not responding. Use --port COMx")
                return 2
            port = probe.port

        proto = VvProtocol()

        with serial.Serial(port, args.baud, timeout=READ_TIMEOUT_S) as ser:
            ser.reset_input_buffer()
            ser.reset_output_buffer()

            print(f"Connected {port} @ {args.baud}")
            print("Starting realtime log stream (only log lines)... Ctrl+C to stop")

            tester = LogRealtimeTester(
                ser=ser,
                proto=proto,
                src=args.src,
                dst=args.dst,
                verbose=args.verbose,
                clear_first=args.clear_first,
                calibration=args.calibration,
                args=args,
            )
            tester.bootstrap()
            try:
                tester.loop()
            except KeyboardInterrupt:
                print("\nStopping... Sending end_session to device.")
                tester.send_end_session(pb.SESSION_END_REASON_LOG_DATA)
                time.sleep(0.1)
                return 0

    except KeyboardInterrupt:
        print("\nStopping...")
        return 0
    except SerialException as exc:
        print(f"Serial error: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
