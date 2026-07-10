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
    parsed_dict: dict | None = None

    @classmethod
    def from_proto(cls, param_name: str, packet, received_at: float | None = None) -> "RawPacket":
        from google.protobuf.json_format import MessageToDict
        try:
            one = packet.WhichOneof("params")
            payload_msg = getattr(packet, one, packet) if one else packet
            try:
                parsed_dict = MessageToDict(
                    payload_msg,
                    preserving_proto_field_name=True,
                    always_print_fields_with_no_presence=True,
                )
            except TypeError:
                parsed_dict = MessageToDict(
                    payload_msg,
                    preserving_proto_field_name=True,
                    including_default_value_fields=True,
                )
        except Exception:
            parsed_dict = {}

        return cls(
            param_name=param_name,
            payload=packet.SerializeToString(),
            src_addr=int(packet.hdr.addr.src),
            dst_addr=int(packet.hdr.addr.dst),
            seq=int(packet.hdr.seq),
            received_at=received_at if received_at is not None else time(),
            parsed_dict=parsed_dict,
        )

