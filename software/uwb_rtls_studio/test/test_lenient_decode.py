import sys
from pathlib import Path

from google.protobuf.message import DecodeError

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import protocol_pb2 as pb
from common.commands import CommandFactory
from common.lenient_decode import try_lenient_packet_decode
from common.transport import VvAddress


def _strict_parse_fails(payload: bytes) -> None:
    pkt = pb.packet_t()
    try:
        pkt.ParseFromString(payload)
    except DecodeError:
        return
    raise AssertionError("strict protobuf unexpectedly accepted padded payload")


def test_lenient_decode_recovers_padded_sensor_fusion_cfg_resp():
    factory = CommandFactory()
    original = factory.sensor_fusion_cfg_resp(int(VvAddress.MCU), int(VvAddress.HOST), 112)
    payload = original.SerializeToString() + b"\x00\x00\x00\x00"

    _strict_parse_fails(payload)

    recovered = try_lenient_packet_decode(payload)

    assert recovered is not None
    assert recovered.WhichOneof("params") == "sensor_fusion_cfg_resp"
    assert recovered.hdr.seq == 112
    assert recovered.hdr.addr.src == int(VvAddress.MCU)
    assert recovered.hdr.addr.dst == int(VvAddress.HOST)
    assert recovered.sensor_fusion_cfg_resp.config.beta == original.sensor_fusion_cfg_resp.config.beta


def test_lenient_decode_recovers_padded_anchor_layout_resp():
    factory = CommandFactory()
    original = factory.anchor_layout_resp(int(VvAddress.MCU), int(VvAddress.HOST), 106)
    payload = original.SerializeToString() + b"\x00\x00\x00\x00"

    _strict_parse_fails(payload)

    recovered = try_lenient_packet_decode(payload)

    assert recovered is not None
    assert recovered.WhichOneof("params") == "anchor_layout_resp"
    assert recovered.hdr.seq == 106
    assert len(recovered.anchor_layout_resp.anchors) == len(original.anchor_layout_resp.anchors)
    assert recovered.anchor_layout_resp.anchors[0].anchor_id == original.anchor_layout_resp.anchors[0].anchor_id
