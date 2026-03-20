from __future__ import annotations

from vv_commands import CommandFactory
from vv_test_session import VvTestSession

from test_common import first_param, send_and_print


def run(session: VvTestSession, src: int, dst: int) -> bool:
    factory = CommandFactory()
    print("\n=== TIME SYNC TESTS ===")

    set_pkt = factory.time_sync_set(src, dst, session.proto.next_seq())
    send_and_print(session, "time_sync_set", set_pkt)

    get_pkt = factory.time_sync_get(src, dst, session.proto.next_seq())
    packets = send_and_print(session, "time_sync_get", get_pkt)
    resp_pkt = first_param(packets, "time_sync_resp")

    print("Manual check: compare the values below")
    print(
        f"  SET unix_time_ms={set_pkt.time_sync_set.unix_time_ms}, "
        f"timezone_offset={set_pkt.time_sync_set.timezone_offset}"
    )
    if resp_pkt is None:
        print("  GET time_sync_resp: <not received>")
        return False

    print(
        f"  GET unix_time_ms={resp_pkt.time_sync_resp.unix_time_ms}, "
        f"timezone_offset={resp_pkt.time_sync_resp.timezone_offset}"
    )
    return True
