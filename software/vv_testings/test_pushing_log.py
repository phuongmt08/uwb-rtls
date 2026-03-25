from __future__ import annotations

import argparse
import time
from datetime import datetime
from dataclasses import dataclass
from typing import Iterable

import serial
from serial import SerialException
from serial.tools import list_ports

import protocol_pb2 as pb
from parser_protocol import HostTransport, VvAddress, VvProtocol

BAUD_DEFAULT = 115200
READ_TIMEOUT_S = 0.05
HOST_ACTIVITY_PING_S = 5.0
LOG_POLL_PERIOD_S = 1.0
MAX_RECORD_LEN = 512
EPOCH_MS_MIN_FOR_DATETIME = 946684800000  # 2000-01-01 00:00:00 UTC


def packet_name(pkt: pb.packet_t) -> str:
    return pkt.WhichOneof("params") or "<none>"


@dataclass
class ProbeResult:
    port: str
    baud: int


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
    def __init__(self, ser: serial.Serial, proto: VvProtocol, dst: int, verbose: bool = False, clear_first: bool = False):
        self.ser = ser
        self.proto = proto
        self.dst = dst
        self.verbose = verbose
        self.clear_first = clear_first
        self.log_parser = FlashLogStreamParser()
        self.last_ping = 0.0
        self.last_poll = 0.0

    def _send_packet(self, pkt: pb.packet_t) -> None:
        frame = self.proto.wrap_packet(pkt)
        self.ser.write(frame)
        self.ser.flush()

    def _build_none(self) -> pb.packet_t:
        pkt = pb.packet_t()
        pkt.hdr.addr.src = int(VvAddress.HOST)
        pkt.hdr.addr.dst = self.dst
        pkt.hdr.seq = self.proto.next_seq()
        pkt.none.dummy = 0
        return pkt

    def _build_transport_set(self) -> pb.packet_t:
        pkt = pb.packet_t()
        pkt.hdr.addr.src = int(VvAddress.HOST)
        pkt.hdr.addr.dst = self.dst
        pkt.hdr.seq = self.proto.next_seq()
        pkt.host_transport_set.transport = int(HostTransport.USB)
        return pkt

    def _build_log_data_get(self) -> pb.packet_t:
        pkt = pb.packet_t()
        pkt.hdr.addr.src = int(VvAddress.HOST)
        pkt.hdr.addr.dst = self.dst
        pkt.hdr.seq = self.proto.next_seq()
        pkt.log_data.type = pb.LOG_TYPE_DEVICE_LOG
        return pkt

    def _build_time_sync_set(self) -> pb.packet_t:
        pkt = pb.packet_t()
        pkt.hdr.addr.src = int(VvAddress.HOST)
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
        pkt.hdr.addr.src = int(VvAddress.HOST)
        pkt.hdr.addr.dst = dst
        pkt.hdr.seq = self.proto.next_seq()
        pkt.ack.ack_seq = ack_seq
        pkt.ack.response = pb.PACKET_ACK_RESPONSE_ACK
        return pkt

    def _build_log_clear_all(self) -> pb.packet_t:
        pkt = pb.packet_t()
        pkt.hdr.addr.src = int(VvAddress.HOST)
        pkt.hdr.addr.dst = self.dst
        pkt.hdr.seq = self.proto.next_seq()
        pkt.log_clear.type = pb.LOG_TYPE_DEVICE_LOG
        pkt.log_clear.offset = 0
        pkt.log_clear.length = 0xFFFFFFFF
        return pkt

    def bootstrap(self) -> None:
        self._send_packet(self._build_none())
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

        if name == "log_data":
            payload = bytes(pkt.log_data.data)
            lines = self.log_parser.feed(payload)
            for line in lines:
                print(line)

            ack_pkt = self._build_ack(pkt.hdr.seq, int(pkt.hdr.addr.src))
            self._send_packet(ack_pkt)
            return

        if self.verbose:
            print(f"[PKT] {name} src={pkt.hdr.addr.src} dst={pkt.hdr.addr.dst} seq={pkt.hdr.seq}")

    def loop(self) -> None:
        while True:
            now = time.time()

            if now - self.last_ping >= HOST_ACTIVITY_PING_S:
                self._send_packet(self._build_none())
                self.last_ping = now

            if now - self.last_poll >= LOG_POLL_PERIOD_S:
                self._send_packet(self._build_log_data_get())
                self.last_poll = now

            raw = self.ser.read(self.ser.in_waiting or 1)
            if not raw:
                continue

            packets = self.proto.decode_from_frames(raw)
            for pkt in packets:
                self._process_packet(pkt)


def _score_port(p: list_ports.ListPortInfo) -> int:
    score = 0
    desc = (p.description or "").lower()
    manu = (p.manufacturer or "").lower()
    hwid = (p.hwid or "").lower()

    if "stm" in desc or "stm" in manu:
        score += 4
    if "usb serial" in desc or "virtual com" in desc:
        score += 2
    if "vid:pid=0483" in hwid:
        score += 6
    if "bluetooth" in desc:
        score -= 4
    return score


def prioritized_ports() -> Iterable[list_ports.ListPortInfo]:
    ports = list(list_ports.comports())
    ports.sort(key=_score_port, reverse=True)
    return ports


def auto_detect_port() -> ProbeResult | None:
    for p in prioritized_ports():
        try:
            with serial.Serial(p.device, BAUD_DEFAULT, timeout=READ_TIMEOUT_S):
                return ProbeResult(port=p.device, baud=BAUD_DEFAULT)
        except Exception:
            continue
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Realtime device-log test (only log output)")
    parser.add_argument("--port", default=None, help="COM port, example COM7")
    parser.add_argument("--baud", type=int, default=BAUD_DEFAULT, help="Baud rate")
    parser.add_argument(
        "--dst",
        type=int,
        default=int(VvAddress.ANCHOR),
        help="Device destination address (default: ANCHOR=2)",
    )
    parser.add_argument("--verbose", action="store_true", help="Print non-log packets")
    parser.add_argument(
        "--clear-first",
        action="store_true",
        help="Clear existing flash log backlog once before realtime streaming",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    port = args.port
    if not port:
        probe = auto_detect_port()
        if probe is None:
            print("No serial port found. Use --port COMx")
            return 2
        port = probe.port

    proto = VvProtocol()

    try:
        with serial.Serial(port, args.baud, timeout=READ_TIMEOUT_S) as ser:
            ser.reset_input_buffer()
            ser.reset_output_buffer()

            print(f"Connected {port} @ {args.baud}")
            print("Starting realtime log stream (only log lines)... Ctrl+C to stop")

            tester = LogRealtimeTester(
                ser=ser,
                proto=proto,
                dst=args.dst,
                verbose=args.verbose,
                clear_first=args.clear_first,
            )
            tester.bootstrap()
            tester.loop()

    except KeyboardInterrupt:
        print("\nStopped")
        return 0
    except SerialException as exc:
        print(f"Serial error: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
