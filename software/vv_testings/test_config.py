from __future__ import annotations
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


from common.commands import CommandFactory
from vv_test_session import VvTestSession

from test_common import first_param, send_and_print


def run(session: VvTestSession, src: int, dst: int) -> bool:
    factory = CommandFactory()
    print("\n=== CONFIG TESTS ===")

    ok = True

    init_cfg_packets = send_and_print(
        session,
        "sys_config_get (before set)",
        factory.sys_config_get(src, dst, session.proto.next_seq()),
    )
    init_cfg = first_param(init_cfg_packets, "sys_config_resp")
    if init_cfg is None:
        ok = False

    set_cfg_pkt = factory.sys_config_set(src, dst, session.proto.next_seq())
    send_and_print(session, "sys_config_set", set_cfg_pkt)

    new_cfg_packets = send_and_print(
        session,
        "sys_config_get (after set)",
        factory.sys_config_get(src, dst, session.proto.next_seq()),
    )
    new_cfg = first_param(new_cfg_packets, "sys_config_resp")
    if new_cfg is None:
        ok = False

    print("Manual check sys_config:")
    print(f"  SET config={set_cfg_pkt.sys_config_set.config}")
    print(f"  GET(after) config={new_cfg.sys_config_resp.config if new_cfg else '<not received>'}")

    set_ranging_pkt = factory.sys_ranging_cfg_set(src, dst, session.proto.next_seq())
    send_and_print(session, "sys_ranging_cfg_set", set_ranging_pkt)

    ranging_packets = send_and_print(
        session,
        "sys_ranging_cfg_get",
        factory.sys_ranging_cfg_get(src, dst, session.proto.next_seq()),
    )
    ranging_resp = first_param(ranging_packets, "sys_ranging_cfg_resp")
    if ranging_resp is None:
        ok = False

    print("Manual check sys_ranging_cfg:")
    print(f"  SET config={set_ranging_pkt.sys_ranging_cfg_set.config}")
    print(f"  GET config={ranging_resp.sys_ranging_cfg_resp.config if ranging_resp else '<not received>'}")

    send_and_print(session, "sensor_fusion_cfg_set", factory.sensor_fusion_cfg_set(src, dst, session.proto.next_seq()))
    send_and_print(session, "sensor_fusion_cfg_get", factory.sensor_fusion_cfg_get(src, dst, session.proto.next_seq()))
    print("Manual check sensor_fusion_cfg: firmware may return NACK_UNIMPLEMENTED depending build flags.")

    return ok