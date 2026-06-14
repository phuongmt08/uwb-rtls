"""
Small raw-data containers for the app RX path.

The data layer intentionally stays close to bytes and packet metadata. Protobuf
meaning is handled in repository classes so models/viewmodels do not need to
know transport details.
"""
from __future__ import annotations

from dataclasses import dataclass
from time import time


@dataclass(frozen=True)
class RawSerialChunk:
    payload: bytes
    received_at: float

    @classmethod
    def from_bytes(cls, payload: bytes) -> "RawSerialChunk":
        return cls(payload=bytes(payload), received_at=time())


@dataclass(frozen=True)
class RawPacket:
    param_name: str
    payload: bytes
    src_addr: int
    dst_addr: int
    seq: int
    received_at: float

    @classmethod
    def from_proto(cls, param_name: str, packet, received_at: float | None = None) -> "RawPacket":
        return cls(
            param_name=param_name,
            payload=packet.SerializeToString(),
            src_addr=int(packet.hdr.addr.src),
            dst_addr=int(packet.hdr.addr.dst),
            seq=int(packet.hdr.seq),
            received_at=received_at if received_at is not None else time(),
        )
