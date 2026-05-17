from __future__ import annotations

from vv_commands import CommandCatalog
from vv_test_session import VvTestSession

from test_common import has_ack_for_seq, packet_name, send_and_print


EXPECTED_RESP_BY_REQUEST = {
    "device_information_get": "device_information_resp",
    "time_sync_get": "time_sync_resp",
    "sys_config_get": "sys_config_resp",
    "sys_ranging_cfg_get": "sys_ranging_cfg_resp",
    "ble_status_get": "ble_status_resp",
    "pos_calib_cfg_get": "pos_calib_cfg_resp",
    "anchor_layout_get": "anchor_layout_resp",
}


# Response/passive packets that are usually not sent as host requests.
PASSIVE_REQUESTS = {
    "ack",
    "device_information_resp",
    "time_sync_resp",
    "sys_config_resp",
    "sys_ranging_cfg_resp",
    "ranging_result",
    "ranging_status_resp",
    "sensor_fusion_cfg_resp",
    "flash_data",
    "ble_status_resp",
    "ble_adv_status",
    "log_data",
    "pos_calib_cfg_resp",
    "anchor_layout_resp",
}


def run(session: VvTestSession, src: int, dst: int) -> bool:
    print("\n=== COMMAND MATRIX (network_cmd tags 2..44) ===")
    catalog = CommandCatalog()

    total = 0
    no_rx = 0

    for spec in catalog.all():
        if spec.param_name in PASSIVE_REQUESTS:
            continue

        total += 1
        pkt = spec.builder(src, dst, session.proto.next_seq())
        packets = send_and_print(session, f"matrix tag={spec.tag} {spec.param_name}", pkt, timeout_s=0.5)

        expected = EXPECTED_RESP_BY_REQUEST.get(spec.param_name)
        got_expected = any(packet_name(p) == expected for p in packets) if expected else False
        got_ack = has_ack_for_seq(packets, pkt.hdr.seq)
        got_any = len(packets) > 0

        status = "RX" if got_any else "NO-RX"
        rx = ",".join(packet_name(p) for p in packets) if packets else "<none>"
        hint = f"expect={expected}" if expected else "expect=ack/any"
        print(f"[{status}] tag={spec.tag:>2} {spec.param_name:<22} {hint} -> RX: {rx}")

        if not got_any:
            no_rx += 1

    print(f"\nMatrix summary: total={total}, no_rx={no_rx}")
    print("Manual check: review each command output above to confirm expected firmware behavior.")
    return no_rx == 0
