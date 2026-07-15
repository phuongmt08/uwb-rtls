from __future__ import annotations

import struct
from typing import Callable

from google.protobuf.message import DecodeError

from . import protocol_pb2 as pb


class _WireError(ValueError):
    pass


def _read_varint(data: bytes, pos: int, limit: int) -> tuple[int, int]:
    shift = 0
    value = 0
    while pos < limit and shift < 64:
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return value, pos
        shift += 7
    raise _WireError("unterminated varint")


def _read_len(data: bytes, pos: int, limit: int) -> tuple[int, int, int]:
    size, pos = _read_varint(data, pos, limit)
    end = pos + size
    if end > limit:
        raise _WireError("length exceeds buffer")
    return pos, end, end


def _skip_field(data: bytes, pos: int, limit: int, wire_type: int) -> int:
    if wire_type == 0:
        _, pos = _read_varint(data, pos, limit)
        return pos
    if wire_type == 1:
        end = pos + 8
        if end > limit:
            raise _WireError("fixed64 exceeds buffer")
        return end
    if wire_type == 2:
        _, _, end = _read_len(data, pos, limit)
        return end
    if wire_type == 5:
        end = pos + 4
        if end > limit:
            raise _WireError("fixed32 exceeds buffer")
        return end
    raise _WireError(f"unsupported wire type {wire_type}")


def _walk_fields(
    data: bytes,
    start: int,
    limit: int,
    handler: Callable[[int, int, int], int | None],
) -> None:
    pos = start
    while pos < limit:
        key, pos = _read_varint(data, pos, limit)
        if key == 0:
            # Some embedded encoders/bridges leave zero padding at the end of
            # fixed-size buffers. Python protobuf treats that as fatal.
            if all(b == 0 for b in data[pos - 1:limit]):
                return
            raise _WireError("field number 0")
        field_no = key >> 3
        wire_type = key & 0x07
        new_pos = handler(field_no, wire_type, pos)
        if new_pos is None:
            new_pos = _skip_field(data, pos, limit, wire_type)
        if new_pos <= pos and pos < limit:
            raise _WireError("parser did not advance")
        pos = new_pos


def _parse_addr(data: bytes, start: int, limit: int, pkt: pb.packet_t) -> None:
    def handle(field_no: int, wire_type: int, pos: int) -> int | None:
        if wire_type != 0:
            return None
        value, new_pos = _read_varint(data, pos, limit)
        if field_no == 1:
            pkt.hdr.addr.src = int(value)
            return new_pos
        if field_no == 2:
            pkt.hdr.addr.dst = int(value)
            return new_pos
        return None

    _walk_fields(data, start, limit, handle)


def _parse_hdr(data: bytes, start: int, limit: int, pkt: pb.packet_t) -> None:
    def handle(field_no: int, wire_type: int, pos: int) -> int | None:
        if field_no == 1 and wire_type == 2:
            sub_start, sub_end, new_pos = _read_len(data, pos, limit)
            _parse_addr(data, sub_start, sub_end, pkt)
            return new_pos
        if field_no in (2, 3) and wire_type == 0:
            value, new_pos = _read_varint(data, pos, limit)
            if field_no == 2:
                pkt.hdr.seq = int(value)
            else:
                pkt.hdr.timestamp = int(value)
            return new_pos
        return None

    _walk_fields(data, start, limit, handle)


def _parse_float(data: bytes, pos: int, limit: int) -> tuple[float, int]:
    end = pos + 4
    if end > limit:
        raise _WireError("float exceeds buffer")
    return struct.unpack("<f", data[pos:end])[0], end


def _parse_sensor_cfg(data: bytes, start: int, limit: int, cfg: pb.sensor_fusion_cfg_t) -> bool:
    setters = {
        1: "alpha",
        2: "kappa",
        3: "beta",
        4: "q_a",
        5: "q_g",
        6: "r_uwb",
        7: "init_p_px",
        8: "init_p_py",
        9: "init_p_vx",
        10: "init_p_vy",
        11: "init_p_theta",
        12: "init_p_bias_ax",
        13: "init_p_bias_ay",
        14: "init_p_bias_gz",
    }
    seen: set[int] = set()

    def handle(field_no: int, wire_type: int, pos: int) -> int | None:
        if field_no not in setters or wire_type != 5:
            return None
        value, new_pos = _parse_float(data, pos, limit)
        setattr(cfg, setters[field_no], float(value))
        seen.add(field_no)
        return new_pos

    _walk_fields(data, start, limit, handle)
    return bool(seen)


def _parse_sensor_fusion_cfg_resp(data: bytes, start: int, limit: int, pkt: pb.packet_t) -> bool:
    ok = False

    def handle(field_no: int, wire_type: int, pos: int) -> int | None:
        nonlocal ok
        if field_no == 1 and wire_type == 2:
            sub_start, sub_end, new_pos = _read_len(data, pos, limit)
            ok = _parse_sensor_cfg(data, sub_start, sub_end, pkt.sensor_fusion_cfg_resp.config)
            return new_pos
        return None

    _walk_fields(data, start, limit, handle)
    return ok


def _parse_anchor_item(data: bytes, start: int, limit: int, item: pb.anchor_layout_item_t) -> bool:
    seen: set[int] = set()

    def handle(field_no: int, wire_type: int, pos: int) -> int | None:
        if field_no == 1 and wire_type == 0:
            value, new_pos = _read_varint(data, pos, limit)
            item.anchor_id = int(value)
            seen.add(field_no)
            return new_pos
        if field_no in (2, 3, 4) and wire_type == 5:
            value, new_pos = _parse_float(data, pos, limit)
            if field_no == 2:
                item.x_m = float(value)
            elif field_no == 3:
                item.y_m = float(value)
            else:
                item.z_m = float(value)
            seen.add(field_no)
            return new_pos
        return None

    _walk_fields(data, start, limit, handle)
    return 1 in seen


def _parse_anchor_layout_resp(data: bytes, start: int, limit: int, pkt: pb.packet_t) -> bool:
    count = 0

    def handle(field_no: int, wire_type: int, pos: int) -> int | None:
        nonlocal count
        if field_no == 1 and wire_type == 2:
            sub_start, sub_end, new_pos = _read_len(data, pos, limit)
            item = pkt.anchor_layout_resp.anchors.add()
            if not _parse_anchor_item(data, sub_start, sub_end, item):
                del pkt.anchor_layout_resp.anchors[-1]
                raise _WireError("incomplete anchor layout item")
            count += 1
            return new_pos
        return None

    _walk_fields(data, start, limit, handle)
    return count > 0


def try_lenient_packet_decode(payload: bytes) -> pb.packet_t | None:
    """Recover selected current-frame packets from strict protobuf failures.

    This does not use cached data. It only accepts a packet when the current
    payload contains a decodable header and a supported oneof body.
    """
    pkt = pb.packet_t()
    decoded_param: str | None = None

    def handle(field_no: int, wire_type: int, pos: int) -> int | None:
        nonlocal decoded_param
        if field_no == 1 and wire_type == 2:
            sub_start, sub_end, new_pos = _read_len(payload, pos, len(payload))
            _parse_hdr(payload, sub_start, sub_end, pkt)
            return new_pos
        if field_no == 23 and wire_type == 2:
            sub_start, sub_end, new_pos = _read_len(payload, pos, len(payload))
            if _parse_sensor_fusion_cfg_resp(payload, sub_start, sub_end, pkt):
                decoded_param = "sensor_fusion_cfg_resp"
                return new_pos
            raise _WireError("incomplete sensor fusion config")
        if field_no == 51 and wire_type == 2:
            sub_start, sub_end, new_pos = _read_len(payload, pos, len(payload))
            if _parse_anchor_layout_resp(payload, sub_start, sub_end, pkt):
                decoded_param = "anchor_layout_resp"
                return new_pos
            raise _WireError("empty anchor layout")
        return None

    try:
        _walk_fields(payload, 0, len(payload), handle)
    except (_WireError, DecodeError, IndexError, struct.error, ValueError):
        return None

    if decoded_param and pkt.WhichOneof("params") == decoded_param:
        return pkt
    return None
