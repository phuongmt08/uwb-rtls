from __future__ import annotations
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


from dataclasses import dataclass
from typing import Iterable

from common import protocol_pb2 as pb
from common.commands import CommandCatalog
from vv_test_session import VvTestSession

from test_common import ack_for_seq, packet_name


EXPECTED_RESP_BY_REQUEST = {
    "device_information_get": "device_information_resp",
    "time_sync_get": "time_sync_resp",
    "sys_config_get": "sys_config_resp",
    "sys_ranging_cfg_get": "sys_ranging_cfg_resp",
    "ranging_status_get": "ranging_status_resp",
    "device_type_get": "device_type_set",
    "ble_status_get": "ble_status_resp",
    "battery_info_get": "battery_info_resp",
    "rtos_resource_get": "rtos_resource_resp",
    "rtos_task_stats_get": "rtos_task_stats_resp",
    "prefilter_cfg_get": "prefilter_cfg_resp",
    "anchor_layout_get": "anchor_layout_resp",
    "zone_profile_get": "zone_profile_resp",
}

GROUP_BY_REQUEST = {
    "none": "core",
    "device_information_get": "device_information",
    "time_sync_get": "time_sync",
    "time_sync_set": "time_sync",
    "time_sync_bcast_set": "time_sync",
    "sys_config_get": "sys_config",
    "sys_config_set": "sys_config",
    "sys_ranging_cfg_get": "sys_ranging_cfg",
    "sys_ranging_cfg_set": "sys_ranging_cfg",
    "ranging_start": "ranging_control",
    "ranging_stop": "ranging_control",
    "ranging_status_get": "ranging_status",
    "sensor_fusion_cfg_get": "sensor_fusion_cfg",
    "sensor_fusion_cfg_set": "sensor_fusion_cfg",
    "imu_reset": "imu",
    "imu_calib_start": "imu",
    "device_type_set": "device_type",
    "device_type_get": "device_type",
    "flash_read": "flash",
    "ble_adv_config_set": "ble",
    "ble_status_get": "ble",
    "log_clear": "log",
    "host_transport_set": "host_transport",
    "anchor_layout_get": "anchor_layout",
    "anchor_layout_set": "anchor_layout",
    "rtos_resource_get": "rtos",
    "rtos_task_stats_get": "rtos",
    "battery_info_get": "battery_info",
    "prefilter_cfg_get": "prefilter_cfg",
    "prefilter_cfg_set": "prefilter_cfg",
    "zone_switch": "zone_switch",
    "zone_profile_set": "zone_profile",
    "zone_profile_get": "zone_profile",
}

SHORT_NAME_BY_REQUEST = {
    "none": "none",
    "device_information_get": "get",
    "time_sync_get": "get",
    "time_sync_set": "set",
    "time_sync_bcast_set": "bcast_set",
    "sys_config_get": "get",
    "sys_config_set": "set",
    "sys_ranging_cfg_get": "get",
    "sys_ranging_cfg_set": "set",
    "ranging_start": "start",
    "ranging_stop": "stop",
    "ranging_status_get": "get",
    "sensor_fusion_cfg_get": "get",
    "sensor_fusion_cfg_set": "set",
    "imu_reset": "reset",
    "imu_calib_start": "calib_start",
    "device_type_set": "set",
    "device_type_get": "get",
    "flash_read": "read",
    "ble_adv_config_set": "adv_config_set",
    "ble_status_get": "status_get",
    "log_clear": "clear",
    "host_transport_set": "set",
    "anchor_layout_get": "get",
    "anchor_layout_set": "set",
    "rtos_resource_get": "resource_get",
    "rtos_task_stats_get": "task_stats_get",
    "battery_info_get": "get",
    "prefilter_cfg_get": "get",
    "prefilter_cfg_set": "set",
    "zone_switch": "set",
    "zone_profile_set": "set",
    "zone_profile_get": "get",
}

ACK_RESPONSE_NAMES = {
    int(pb.PACKET_ACK_RESPONSE_ACK): "ACK",
    int(pb.PACKET_ACK_RESPONSE_NACK_BAD_CRC): "NACK_BAD_CRC",
    int(pb.PACKET_ACK_RESPONSE_NACK_UNIMPLEMENTED): "NACK_UNIMPLEMENTED",
    int(pb.PACKET_ACK_RESPONSE_NACK_TIMED_OUT): "NACK_TIMED_OUT",
    int(pb.PACKET_ACK_RESPONSE_NACK_BUSY): "NACK_BUSY",
    int(pb.PACKET_ACK_RESPONSE_NACK_CMD_FAILED): "NACK_CMD_FAILED",
    int(pb.PACKET_ACK_RESPONSE_NACK_INVALID_TYPE): "NACK_INVALID_TYPE",
}


# Packets that are not MCU commands or are unsafe for an automated self-test.
SKIPPED_REQUESTS = {
    "ack",
    "device_information_resp",
    "time_sync_resp",
    "sys_config_resp",
    "sys_ranging_cfg_resp",
    "ranging_result",
    "ranging_status_resp",
    "sensor_fusion_cfg_resp",
    "sensor_fusion_result",
    "flash_data",
    "ble_status_resp",
    "ble_adv_status",
    "ble_scan_result",
    "log_data",
    "anchor_layout_resp",
    "ble_conn_params_resp",
    "battery_info_resp",
    "rtos_resource_resp",
    "rtos_task_stats_resp",
    "prefilter_cfg_resp",
    "fota_state_resp",
    "vehicle_status",
    # Ends host/session state; keep it out of aggregate tests so later commands are not muted.
    "end_session",
    # Avoid commands that can erase/write flash, reset the board, or jump to the bootloader.
    "device_reset",
    "uwb_reset",
    "factory_config_reset",
    "enter_to_bootloader",
    "flash_erase",
    "flash_write",
    "flash_verify",
    "factory_otp_write",
    # Central/vehicle commands are not MCU app commands in this test path.
    "ble_conn_params_get",
    "ble_conn_params_set",
    "ble_disconnect",
    "ble_scan_start",
    "ble_scan_stop",
    "ble_connect",
    "vehicle_control",
    "zone_switch",
    "zone_profile_set",
    "zone_profile_get",
    "zone_profile_resp",
}


@dataclass(frozen=True)
class CommandResult:
    group: str
    name: str
    short_name: str
    tag: int
    ok: bool
    outcome: str
    expected: str
    received: str


def _ack_response_name(response: int) -> str:
    return ACK_RESPONSE_NAMES.get(int(response), f"ACK_RESPONSE_{int(response)}")


def _packet_names(packets: Iterable[pb.packet_t]) -> list[str]:
    return [packet_name(pkt) for pkt in packets]


def _format_received(packets: list[pb.packet_t], ack: pb.packet_t | None) -> str:
    names = _packet_names(packets)
    if ack is not None:
        ack_name = _ack_response_name(ack.ack.response)
        names = [name if name != "ack" else ack_name for name in names]
    return "+".join(names) if names else "NO_RX"


def _format_expected(param_name: str) -> str:
    expected = EXPECTED_RESP_BY_REQUEST.get(param_name)
    return f"{expected} or ACK/NACK" if expected else "ACK/NACK"


def _make_result(spec, ok: bool, outcome: str, expected: str, received: str) -> CommandResult:
    return CommandResult(
        group=GROUP_BY_REQUEST.get(spec.param_name, spec.param_name),
        name=spec.param_name,
        short_name=SHORT_NAME_BY_REQUEST.get(spec.param_name, spec.param_name),
        tag=spec.tag,
        ok=ok,
        outcome=outcome,
        expected=expected,
        received=received,
    )


def _print_group_results(results: list[CommandResult]) -> None:
    grouped: dict[str, list[CommandResult]] = {}
    for result in results:
        grouped.setdefault(result.group, []).append(result)

    print("\nMCU command self-test results:")
    for group, group_results in grouped.items():
        group_ok = all(result.ok for result in group_results)
        status = "PASS" if group_ok else "FAIL"
        details = " ".join(f"{result.short_name}={result.outcome}" for result in group_results)
        print(f"[{status}] {group:<22} {details}")


def _print_failures(results: list[CommandResult]) -> None:
    failures = [result for result in results if not result.ok]
    if not failures:
        return

    print("\nFailed commands:")
    for result in failures:
        print(
            f"  - tag={result.tag:>2} {result.name}: "
            f"expected {result.expected}, got {result.received}"
        )


def run(session: VvTestSession, src: int, dst: int) -> bool:
    print("\n=== MCU COMMAND SELF-TEST ===")
    catalog = CommandCatalog()

    total = 0
    passed = 0
    failed = 0
    skipped = 0
    results: list[CommandResult] = []

    for spec in catalog.all():
        if spec.param_name in SKIPPED_REQUESTS:
            skipped += 1
            continue

        total += 1
        title = f"selftest tag={spec.tag} {spec.param_name}"
        try:
            pkt = spec.builder(src, dst, session.proto.next_seq())
        except Exception as exc:
            failed += 1
            result = _make_result(
                spec,
                ok=False,
                outcome="BUILD_ERROR",
                expected="packet build",
                received=str(exc),
            )
            results.append(result)
            continue

        packets = session.send_and_wait(pkt, timeout_s=0.6)

        expected = EXPECTED_RESP_BY_REQUEST.get(spec.param_name)
        got_expected = any(packet_name(p) == expected for p in packets) if expected else False
        ack = ack_for_seq(packets, pkt.hdr.seq)
        ok = got_expected or (ack is not None)
        received = _format_received(packets, ack)

        if got_expected:
            outcome = received
        elif ack is not None:
            outcome = _ack_response_name(ack.ack.response)
        else:
            outcome = "NO_RX"

        if ok:
            passed += 1
        else:
            failed += 1

        results.append(
            _make_result(
                spec,
                ok=ok,
                outcome=outcome,
                expected=_format_expected(spec.param_name),
                received=received,
            )
        )

    _print_group_results(results)
    _print_failures(results)
    print("\nMCU command self-test summary:")
    print(f"  total_run={total}")
    print(f"  pass={passed}")
    print(f"  fail={failed}")
    print(f"  skipped={skipped}")
    print("  result=PASS" if failed == 0 else "  result=FAIL")
    return failed == 0
