from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import List, Optional

from google.protobuf.message import DecodeError

from . import protocol_pb2 as pb

class VvAddress(IntEnum):
    NONE = int(pb.PACKET_ADDR_UNSPECIFIED)
    MCU = int(pb.PACKET_ADDR_MCU)
    VEHICLE = int(pb.PACKET_ADDR_VEHICLE)
    CENTRAL = int(pb.PACKET_ADDR_CENTRAL)
    PERIPHERAL = int(pb.PACKET_ADDR_PERIPHERAL)
    HOST = int(pb.PACKET_ADDR_HOST)
    DEBUG = int(pb.PACKET_ADDR_DEBUG)
    BCAST = int(pb.PACKET_ADDR_BCAST)


class HostTransport(IntEnum):
    UNSPECIFIED = 0
    USB = 1
    UART = 2


HDLC_SOF = 0x55
HDLC_MAX_DATA_LEN = 256
FRAME_TYPE_PROTOBUF = 0


@dataclass
class HdlcChunk:
    frame_type: int
    payload: bytes


class HdlcCodec:
    def __init__(self) -> None:
        self._state = 0
        self._frame_type = 0
        self._length = 0
        self._payload = bytearray()

    @staticmethod
    def checksum(data: bytes) -> int:
        return sum(data) & 0xFF

    @staticmethod
    def build(frame_type: int, payload: bytes) -> bytes:
        if len(payload) > HDLC_MAX_DATA_LEN:
            raise ValueError(f"Payload too large: {len(payload)} > {HDLC_MAX_DATA_LEN}")

        header = bytes([
            HDLC_SOF,
            frame_type & 0xFF,
            len(payload) & 0xFF,
            (len(payload) >> 8) & 0xFF,
        ])
        body = header + payload
        return body + bytes([HdlcCodec.checksum(body)])

    def feed(self, data: bytes) -> List[HdlcChunk]:
        chunks: List[HdlcChunk] = []

        for byte in data:
            if self._state == 0:
                if byte == HDLC_SOF:
                    self._state = 1
                    self._payload.clear()
                continue

            if self._state == 1:
                self._frame_type = byte
                self._state = 2
                continue

            if self._state == 2:
                self._length = byte
                self._state = 3
                continue

            if self._state == 3:
                self._length |= (byte << 8)
                if self._length > HDLC_MAX_DATA_LEN:
                    self._reset()
                elif self._length == 0:
                    self._state = 5
                else:
                    self._state = 4
                continue

            if self._state == 4:
                self._payload.append(byte)
                if len(self._payload) >= self._length:
                    self._state = 5
                continue

            if self._state == 5:
                header = bytes([
                    HDLC_SOF,
                    self._frame_type & 0xFF,
                    self._length & 0xFF,
                    (self._length >> 8) & 0xFF,
                ])
                calc = self.checksum(header + bytes(self._payload))
                if calc == byte:
                    chunks.append(HdlcChunk(self._frame_type, bytes(self._payload)))
                self._reset()

        return chunks

    def _reset(self) -> None:
        self._state = 0
        self._frame_type = 0
        self._length = 0
        self._payload.clear()


class VvProtocol:
    def __init__(self) -> None:
        self.pb = pb
        self.hdlc = HdlcCodec()
        self._seq = 0

    def next_seq(self) -> int:
        self._seq = (self._seq + 1) & 0xFFFFFFFF
        return self._seq

    def encode_packet(self, packet: pb.packet_t) -> bytes:
        return packet.SerializeToString()

    def decode_packet(self, payload: bytes) -> pb.packet_t:
        pkt = pb.packet_t()
        pkt.ParseFromString(payload)
        return pkt

    def decode_from_frames(self, data: bytes) -> List[pb.packet_t]:
        packets: List[pb.packet_t] = []
        for chunk in self.hdlc.feed(data):
            if chunk.frame_type != FRAME_TYPE_PROTOBUF:
                continue

            try:
                packets.append(self.decode_packet(chunk.payload))
            except DecodeError:
                # Drop malformed protobuf payloads to keep the stream parser alive.
                continue
        return packets

    def first_param(self, packets: List[pb.packet_t], name: str) -> Optional[pb.packet_t]:
        for pkt in packets:
            if pkt.WhichOneof("params") == name:
                return pkt
        return None

    def wrap_packet(self, packet: pb.packet_t, frame_type: int = 0) -> bytes:
        return self.hdlc.build(frame_type, self.encode_packet(packet))
