import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from __future__ import annotations

from common.commands import CommandFactory
from vv_test_session import VvTestSession

from test_common import first_param, send_and_print


def run(session: VvTestSession, src: int, dst: int) -> bool:
    factory = CommandFactory()
    print("\n=== CALIBRATION / LAYOUT TESTS ===")

    ok = True

    set_calib_pkt = factory.pos_calib_cfg_set(src, dst, session.proto.next_seq())
    send_and_print(session, "pos_calib_cfg_set", set_calib_pkt)

    calib_packets = send_and_print(
        session,
        "pos_calib_cfg_get",
        factory.pos_calib_cfg_get(src, dst, session.proto.next_seq()),
    )
    calib_resp = first_param(calib_packets, "pos_calib_cfg_resp")
    if calib_resp is None:
        ok = False

    print("Manual check pos_calib_cfg:")
    print(f"  SET config={set_calib_pkt.pos_calib_cfg_set.config}")
    print(f"  GET config={calib_resp.pos_calib_cfg_resp.config if calib_resp else '<not received>'}")

    set_layout_pkt = factory.anchor_layout_set(src, dst, session.proto.next_seq())
    send_and_print(session, "anchor_layout_set", set_layout_pkt)

    layout_packets = send_and_print(
        session,
        "anchor_layout_get",
        factory.anchor_layout_get(src, dst, session.proto.next_seq()),
    )
    layout_resp = first_param(layout_packets, "anchor_layout_resp")
    if layout_resp is None:
        ok = False

    print("Manual check anchor_layout:")
    print(f"  SET anchors={list(set_layout_pkt.anchor_layout_set.anchors)}")
    print(
        "  GET anchors="
        f"{list(layout_resp.anchor_layout_resp.anchors) if layout_resp else '<not received>'}"
    )

    return ok
