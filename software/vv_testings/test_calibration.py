from __future__ import annotations
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


from common.commands import CommandFactory
from vv_test_session import VvTestSession

from test_common import first_param, send_and_print


def _anchor_tuples(anchors) -> list[tuple[int, float, float, float]]:
    return [
        (
            int(anchor.anchor_id),
            round(float(anchor.x_m), 3),
            round(float(anchor.y_m), 3),
            round(float(anchor.z_m), 3),
        )
        for anchor in anchors
    ]


def _format_anchor_layout(anchors) -> str:
    items = [
        f"A{anchor_id}({x_m:.3f},{y_m:.3f},{z_m:.3f})"
        for anchor_id, x_m, y_m, z_m in _anchor_tuples(anchors)
    ]
    return "[" + ", ".join(items) + "]"


def run(session: VvTestSession, src: int, dst: int) -> bool:
    factory = CommandFactory()
    print("\n=== ANCHOR LAYOUT TESTS ===")

    ok = True

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

    set_anchors = set_layout_pkt.anchor_layout_set.anchors
    get_anchors = layout_resp.anchor_layout_resp.anchors if layout_resp else []
    layout_ok = layout_resp is not None and _anchor_tuples(set_anchors) == _anchor_tuples(get_anchors)
    ok = ok and layout_ok

    print("Anchor layout check:")
    print(f"  SET count={len(set_anchors)} anchors={_format_anchor_layout(set_anchors)}")
    if layout_resp:
        print(f"  GET count={len(get_anchors)} anchors={_format_anchor_layout(get_anchors)}")
    else:
        print("  GET <not received>")
    print(f"  RESULT {'PASS' if layout_ok else 'FAIL'}")

    return ok
