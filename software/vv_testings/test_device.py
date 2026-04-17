from __future__ import annotations
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


from common.commands import CommandFactory
from vv_test_session import VvTestSession

from test_common import first_param, send_and_print


def run(session: VvTestSession, src: int, dst: int) -> bool:
    factory = CommandFactory()
    print("\n=== DEVICE TESTS ===")

    ok = True

    info_packets = send_and_print(
        session,
        "device_information_get",
        factory.device_information_get(src, dst, session.proto.next_seq()),
    )
    info_resp = first_param(info_packets, "device_information_resp")
    if info_resp is None:
        ok = False

    set_type_pkt = factory.device_type_set(src, dst, session.proto.next_seq())
    send_and_print(session, "device_type_set", set_type_pkt)

    get_type_packets = send_and_print(
        session,
        "device_type_get",
        factory.device_type_get(src, dst, session.proto.next_seq()),
    )
    type_resp = first_param(get_type_packets, "device_type_set")
    if type_resp is None:
        ok = False

    print("Manual check device_type:")
    print(f"  SET device_type={set_type_pkt.device_type_set.device_type}")
    print(
        "  GET response(device_type_set)="
        f"{type_resp.device_type_set.device_type if type_resp else '<not received>'}"
    )

    send_and_print(session, "host_transport_set", factory.host_transport_set(src, dst, session.proto.next_seq()))
    send_and_print(session, "device_reset", factory.device_reset(src, dst, session.proto.next_seq()))

    return ok