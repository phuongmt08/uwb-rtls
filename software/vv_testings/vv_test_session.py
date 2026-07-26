from __future__ import annotations
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import time

from dataclasses import dataclass
from typing import Iterable

import serial
from serial import SerialException
from serial.tools import list_ports

from common.transport import VvAddress, VvProtocol


READ_TIMEOUT_S = 0.05
BAUD_CANDIDATES = [115200]


@dataclass
class ProbeResult:
    port: str
    baud: int
    serial_number: int


class VvTestSession:
    def __init__(self, port: str, baud: int = 115200, debug: bool = True):
        self.port = port
        self.baud = baud
        self.debug = debug
        self.proto = VvProtocol()
        self.ser: serial.Serial | None = None

    def __enter__(self) -> "VvTestSession":
        self.ser = serial.Serial(
            self.port,
            self.baud,
            timeout=READ_TIMEOUT_S,
            write_timeout=3.0,
        )
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()
        time.sleep(0.06)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.ser is not None:
            self.ser.close()
            self.ser = None

    def dbg(self, msg: str) -> None:
        if self.debug:
            print(f"[DBG] {msg}")

    def packet_name(self, pkt) -> str:
        return pkt.WhichOneof("params") or "<none>"

    def packet_hdr(self, pkt) -> str:
        base = f"src={pkt.hdr.addr.src} dst={pkt.hdr.addr.dst} seq={pkt.hdr.seq}"
        if pkt.WhichOneof("params") == "ack":
            base += f" ack_seq={pkt.ack.ack_seq}"
        return base

    def recv_packets(self, timeout_s: float, break_on_recv: bool = False) -> list:
        if self.ser is None:
            raise RuntimeError("Session is not opened")
        deadline = time.time() + timeout_s
        packets = []
        while time.time() < deadline:
            in_wait = self.ser.in_waiting
            if in_wait == 0:
                time.sleep(0.001)
                continue
            data = self.ser.read(in_wait)
            if not data:
                continue
            try:
                decoded = self.proto.decode_from_frames(data)
            except Exception as exc:
                self.dbg(f"decode exception: {exc}")
                decoded = []
            if decoded:
                packets.extend(decoded)
                if break_on_recv:
                    break
        return packets

    def send_packet(self, pkt) -> None:
        if self.ser is None:
            raise RuntimeError("Session is not opened")
        frame = self.proto.wrap_packet(pkt)
        self.dbg(f"TX {self.packet_name(pkt)} ({self.packet_hdr(pkt)})")
        written = self.ser.write(frame)
        if written != len(frame):
            raise serial.SerialTimeoutException(
                f"Short serial write: {written}/{len(frame)} bytes"
            )

    def send_and_wait(self, pkt, timeout_s: float = 0.35) -> list:
        self.send_packet(pkt)
        packets = self.recv_packets(timeout_s)
        for rcv in packets:
            self.dbg(f"RX {self.packet_name(rcv)} ({self.packet_hdr(rcv)})")
        return packets

    def send_expect_param(self, pkt, expected_param: str, timeout_s: float = 0.5):
        packets = self.send_and_wait(pkt, timeout_s)
        return self.proto.first_param(packets, expected_param), packets

    @staticmethod
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

    @classmethod
    def prioritized_ports(cls):
        ports = list(list_ports.comports())
        ports.sort(key=cls._score_port, reverse=True)
        return ports

    @classmethod
    @staticmethod
    def _find_probe_match(packets: list, expected_params: Iterable[str], seq: int):
        expected = set(expected_params)
        for pkt in packets:
            param = pkt.WhichOneof("params")
            if param not in expected:
                continue
            if param == "ack" and int(pkt.ack.ack_seq) != int(seq):
                continue
            return pkt
        return None

    @classmethod
    def auto_probe(
        cls,
        src: int = int(VvAddress.DEBUG),
        role: int | None = None,
        debug: bool = True,
        dst: int = int(VvAddress.BCAST),
        expected_params: Iterable[str] = ("device_information_resp",),
        retries: int = 1,
        response_timeout_s: float = 0.5,
    ) -> ProbeResult | None:
        expected_params = tuple(expected_params)

        # If port is forced by environment variable, use it directly
        forced_port = os.environ.get("VV_PORT")
        if forced_port:
            for baud in BAUD_CANDIDATES:
                try:
                    with cls(forced_port, baud=baud, debug=debug) as sess:
                        for _attempt in range(retries):
                            seq = sess.proto.next_seq()
                            pkt = sess.proto.pb.packet_t()
                            pkt.hdr.addr.src = src
                            pkt.hdr.addr.dst = dst
                            pkt.hdr.seq = seq
                            pkt.device_information_get.dummy = 0
                            packets = sess.send_and_wait(pkt, timeout_s=response_timeout_s)
                            match = cls._find_probe_match(packets, expected_params, seq)
                            if match is not None:
                                if match.WhichOneof("params") == "device_information_resp":
                                    dev_role = match.device_information_resp.role
                                    serial_number = int(match.device_information_resp.serial_number)
                                    if role is not None and dev_role != role:
                                        if debug:
                                            print(f"Forced port {forced_port} has role {dev_role}, expected {role}")
                                        continue
                                elif role is not None:
                                    continue
                                else:
                                    serial_number = 0
                                return ProbeResult(
                                    port=forced_port,
                                    baud=baud,
                                    serial_number=serial_number,
                                )
                            time.sleep(0.1)
                except SerialException:
                    pass
            return None

        for port_info in cls.prioritized_ports():
            for baud in BAUD_CANDIDATES:
                try:
                    with cls(port_info.device, baud=baud, debug=debug) as sess:
                        for _attempt in range(retries):
                            seq = sess.proto.next_seq()
                            pkt = sess.proto.pb.packet_t()
                            pkt.hdr.addr.src = src
                            pkt.hdr.addr.dst = dst
                            pkt.hdr.seq = seq
                            pkt.device_information_get.dummy = 0
                            packets = sess.send_and_wait(pkt, timeout_s=response_timeout_s)
                            match = cls._find_probe_match(packets, expected_params, seq)
                            if match is not None:
                                if match.WhichOneof("params") == "device_information_resp":
                                    dev_role = match.device_information_resp.role
                                    serial_number = int(match.device_information_resp.serial_number)
                                    if role is not None and dev_role != role:
                                        continue
                                elif role is not None:
                                    continue
                                else:
                                    serial_number = 0
                                return ProbeResult(
                                    port=port_info.device,
                                    baud=baud,
                                    serial_number=serial_number,
                                )
                            time.sleep(0.1)
                except SerialException:
                    continue

        return None
