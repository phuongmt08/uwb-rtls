import time
from dataclasses import dataclass
import serial
from serial import SerialException
from serial.tools import list_ports
import os
import sys

parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

from common.transport import VvAddress, VvProtocol

READ_TIMEOUT_S = 0.05
BAUD_CANDIDATES = [115200]

@dataclass
class ProbeResult:
    port: str
    baud: int
    serial_number: int

class DongleSession:
    def __init__(self, port: str, baud: int = 115200, debug: bool = True):
        self.port = port
        self.baud = baud
        self.debug = debug
        self.proto = VvProtocol()
        self.ser = None

    def __enter__(self):
        self.ser = serial.Serial(self.port, self.baud, timeout=READ_TIMEOUT_S)
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()
        time.sleep(0.06)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.ser is not None:
            self.ser.close()
            self.ser = None

    def dbg(self, msg: str):
        if self.debug:
            print(f"[DBG] {msg}")

    def packet_name(self, pkt) -> str:
        return pkt.WhichOneof("params") or "<none>"

    def packet_hdr(self, pkt) -> str:
        base = f"src={pkt.hdr.addr.src} dst={pkt.hdr.addr.dst} seq={pkt.hdr.seq}"
        if pkt.WhichOneof("params") == "ack":
            base += f" ack_seq={pkt.ack.ack_seq}"
        return base

    def recv_packets(self, timeout_s: float) -> list:
        if self.ser is None:
            raise RuntimeError("Session is not opened")
        deadline = time.time() + timeout_s
        packets = []
        while time.time() < deadline:
            data = self.ser.read(self.ser.in_waiting or 1)
            if not data:
                continue
            try:
                decoded = self.proto.decode_from_frames(data)
            except Exception as exc:
                self.dbg(f"decode exception: {exc}")
                decoded = []
            packets.extend(decoded)
        return packets

    def send_packet(self, pkt):
        if self.ser is None:
            raise RuntimeError("Session is not opened")
        frame = self.proto.wrap_packet(pkt)
        self.dbg(f"TX {self.packet_name(pkt)} ({self.packet_hdr(pkt)})")
        self.ser.write(frame)
        self.ser.flush()

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
    def _score_port(p) -> int:
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
    def auto_probe(cls, src: int = int(VvAddress.DEBUG), debug: bool = True):
        dst = int(VvAddress.CENTRAL)
        for port_info in cls.prioritized_ports():
            for baud in BAUD_CANDIDATES:
                print(f"[PROBE] Trying port {port_info.device} at baud {baud}...")
                try:
                    with cls(port_info.device, baud=baud, debug=debug) as sess:
                        time.sleep(0.15)
                        sess.ser.reset_input_buffer()
                        
                        for attempt in range(3):
                            if attempt > 0:
                                print(f"[PROBE] Retry {attempt} on {port_info.device}...")
                            
                            seq = sess.proto.next_seq()
                            pkt = sess.proto.pb.packet_t()
                            pkt.hdr.addr.src = src
                            pkt.hdr.addr.dst = dst
                            pkt.hdr.seq = seq
                            pkt.device_information_get.dummy = 0
                            
                            match, packets = sess.send_expect_param(pkt, "ack", timeout_s=0.4)
                            
                            if packets:
                                print(f"[PROBE] Received {len(packets)} packets on {port_info.device}:")
                                for p in packets:
                                    print(f"  - Type: {sess.packet_name(p)} | Hdr: {sess.packet_hdr(p)}")
                            else:
                                print(f"[PROBE] No packets received on {port_info.device}")
                                
                            if match is not None:
                                print(f"[PROBE] Success! Matched ACK on {port_info.device}.")
                                return ProbeResult(
                                    port=port_info.device,
                                    baud=baud,
                                    serial_number=0,  # Dongle returns ACK only, no SN
                                )
                            time.sleep(0.1)
                except SerialException as e:
                    print(f"[PROBE] SerialException on {port_info.device}: {e}")
                    continue
        return None
