from __future__ import annotations
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


from typing import Iterable

from common import protocol_pb2 as pb
from google.protobuf.json_format import MessageToDict

from vv_test_session import VvTestSession


def packet_name(pkt: pb.packet_t) -> str:
    return pkt.WhichOneof("params") or "<none>"


def has_ack_for_seq(packets: Iterable[pb.packet_t], seq: int) -> bool:
    for pkt in packets:
        if pkt.WhichOneof("params") == "ack" and pkt.ack.ack_seq == seq:
            return True
    return False


def has_param(packets: Iterable[pb.packet_t], param_name: str) -> bool:
    for pkt in packets:
        if pkt.WhichOneof("params") == param_name:
            return True
    return False


def packet_to_dict(pkt: pb.packet_t) -> dict:
    return MessageToDict(
        pkt,
        preserving_proto_field_name=True,
        use_integers_for_enums=False,
    )


def print_packet(prefix: str, pkt: pb.packet_t) -> None:
    print(f"{prefix} {packet_name(pkt)}: {packet_to_dict(pkt)}")


def print_received_packets(packets: list[pb.packet_t]) -> None:
    if not packets:
        print("RX <none>")
        return

    for idx, pkt in enumerate(packets, start=1):
        print_packet(f"RX[{idx}]", pkt)


def send_and_print(
    session: VvTestSession,
    title: str,
    pkt: pb.packet_t,
    timeout_s: float = 0.45,
) -> list[pb.packet_t]:
    print(f"\n--- {title} ---")
    print_packet("TX", pkt)
    packets = session.send_and_wait(pkt, timeout_s=timeout_s)
    print_received_packets(packets)
    return packets


def first_param(packets: Iterable[pb.packet_t], param_name: str) -> pb.packet_t | None:
    for pkt in packets:
        if pkt.WhichOneof("params") == param_name:
            return pkt
    return None


def run_case(
    session: VvTestSession,
    title: str,
    pkt: pb.packet_t,
    expected_param: str | None = None,
    timeout_s: float = 0.45,
) -> bool:
    packets = send_and_print(session, title, pkt, timeout_s=timeout_s)
    ok = False

    if expected_param is not None:
        ok = has_param(packets, expected_param)
    else:
        ok = has_ack_for_seq(packets, pkt.hdr.seq) or len(packets) > 0

    status = "PASS" if ok else "FAIL"
    received = ",".join(packet_name(p) for p in packets) if packets else "<none>"
    print(f"[{status}] {title} -> RX: {received}")
    return ok