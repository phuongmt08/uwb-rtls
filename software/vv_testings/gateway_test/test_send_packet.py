from __future__ import annotations
"""
test_send_packet.py - Interactive BLE packet sender for selected MCU commands.

Flow:
  1. Probe or connect to the BLE Central Dongle.
  2. Reuse the BLE scan/connect flow from test_ble_log.py.
  3. Show a command menu.
  4. Enter prepares/sends the selected packet; Backspace returns to menu.

Usage:
  python software/vv_testings/gateway_test/test_send_packet.py
  python software/vv_testings/gateway_test/test_send_packet.py --port COM28
"""

import argparse
import os
import sys
import time
import msvcrt
from pathlib import Path
from typing import Callable, Optional

import serial.tools.list_ports
from google.protobuf.json_format import MessageToDict
from serial import SerialException

script_dir = Path(__file__).resolve().parent
sys.path.append(str(script_dir.parent))         # software/vv_testings
sys.path.append(str(script_dir.parent.parent))  # software

from common import protocol_pb2 as pb
from common.commands import CommandFactory
from common.transport import HostTransport, VvAddress
from vv_test_session import VvTestSession
from test_ble_log import BLE_STATE_NAMES, step_auto_scan_and_connect


COLOR_GREEN = "\033[32m"
COLOR_RED = "\033[31m"
COLOR_CYAN = "\033[36m"
COLOR_YELLOW = "\033[33m"
COLOR_GRAY = "\033[90m"
COLOR_RESET = "\033[0m"

RESPONSE_TIMEOUT_S = 2.0
BOOTSTRAP_GAP_S = 0.2
UINT32_MAX = 0xFFFFFFFF


def packet_name(pkt: pb.packet_t) -> str:
    return pkt.WhichOneof("params") or "<none>"


def packet_to_dict(pkt: pb.packet_t) -> dict:
    return MessageToDict(
        pkt,
        preserving_proto_field_name=True,
        use_integers_for_enums=False,
    )


def print_packet(prefix: str, pkt: pb.packet_t) -> None:
    print(f"{prefix} {packet_name(pkt)}: {packet_to_dict(pkt)}")


def build_ack(session: VvTestSession, src: int, pkt: pb.packet_t) -> pb.packet_t:
    ack = pb.packet_t()
    ack.hdr.addr.src = src
    ack.hdr.addr.dst = int(pkt.hdr.addr.src)
    ack.hdr.seq = session.proto.next_seq()
    ack.ack.ack_seq = int(pkt.hdr.seq)
    ack.ack.response = pb.PACKET_ACK_RESPONSE_ACK
    return ack


def ack_mcu_packet_if_needed(session: VvTestSession, src: int, pkt: pb.packet_t) -> None:
    if packet_name(pkt) == "ack":
        return

    try:
        if int(pkt.hdr.addr.src) != int(VvAddress.MCU):
            return
    except (AttributeError, TypeError, ValueError):
        return

    session.send_packet(build_ack(session, src, pkt))


def recv_and_print(
    session: VvTestSession,
    src: int,
    expected_param: Optional[str] = None,
    timeout_s: float = RESPONSE_TIMEOUT_S,
) -> tuple[Optional[pb.packet_t], list[pb.packet_t]]:
    deadline = time.time() + timeout_s
    packets: list[pb.packet_t] = []
    match: Optional[pb.packet_t] = None

    while time.time() < deadline:
        for pkt in session.recv_packets(timeout_s=0.05):
            packets.append(pkt)
            print_packet("RX", pkt)

            if packet_name(pkt) == "ble_status_resp":
                state = pkt.ble_status_resp.state
                state_name = BLE_STATE_NAMES.get(state, f"UNKNOWN({state})")
                reason = pkt.ble_status_resp.disconnect_reason
                if reason:
                    print(f"{COLOR_YELLOW}[BLE] {state_name}, reason=0x{reason:02X}{COLOR_RESET}")
                else:
                    print(f"{COLOR_CYAN}[BLE] {state_name}{COLOR_RESET}")

            ack_mcu_packet_if_needed(session, src, pkt)

            if expected_param is not None and packet_name(pkt) == expected_param:
                match = pkt
        if match is not None:
            break

    if not packets:
        print(f"{COLOR_GRAY}RX <none>{COLOR_RESET}")
    return match, packets


def send_and_wait(
    session: VvTestSession,
    src: int,
    title: str,
    pkt: pb.packet_t,
    expected_param: Optional[str] = None,
    timeout_s: float = RESPONSE_TIMEOUT_S,
) -> Optional[pb.packet_t]:
    print(f"\n--- {title} ---")
    print_packet("TX", pkt)
    session.send_packet(pkt)
    match, _ = recv_and_print(session, src, expected_param, timeout_s)
    if expected_param is not None and match is None:
        print(f"{COLOR_YELLOW}[WARN] Did not receive {expected_param} within {timeout_s:.1f}s.{COLOR_RESET}")
    return match


def bootstrap_mcu_route(session: VvTestSession, factory: CommandFactory, src: int, dst: int) -> None:
    print("\n[*] Preparing MCU route over BLE...")

    none_pkt = pb.packet_t()
    none_pkt.hdr.addr.src = src
    none_pkt.hdr.addr.dst = dst
    none_pkt.hdr.seq = session.proto.next_seq()
    none_pkt.none.dummy = 0
    send_and_wait(session, src, "none", none_pkt, timeout_s=0.5)
    time.sleep(BOOTSTRAP_GAP_S)

    transport_pkt = factory.host_transport_set(
        src,
        dst,
        session.proto.next_seq(),
        transport=int(HostTransport.USB),
    )
    send_and_wait(session, src, "host_transport_set", transport_pkt, timeout_s=0.5)
    time.sleep(BOOTSTRAP_GAP_S)


def format_config(cfg: pb.uwb_cfg_t) -> str:
    fields = [
        ("role", cfg.role),
        ("device_id", cfg.device_id),
        ("ranging_period_ms", cfg.ranging_period_ms),
        ("rx_timeout_ms", cfg.rx_timeout_ms),
        ("uwb_channel", cfg.uwb_channel),
        ("uwb_prf", cfg.uwb_prf),
        ("uwb_data_rate", cfg.uwb_data_rate),
        ("uwb_preamble_code", cfg.uwb_preamble_code),
        ("tx_antenna_delay", cfg.tx_antenna_delay),
        ("rx_antenna_delay", cfg.rx_antenna_delay),
        ("tx_power", cfg.tx_power),
        ("anchor_list", bytes(cfg.anchor_list).hex(" ").upper()),
        ("power_mode", cfg.power_mode),
        ("uwb_preamble_len", cfg.uwb_preamble_len),
        ("uwb_rx_pac", cfg.uwb_rx_pac),
        ("uwb_ns_sfd", cfg.uwb_ns_sfd),
        ("uwb_phr_mode", cfg.uwb_phr_mode),
        ("smart_tx_power", cfg.smart_tx_power),
        ("pg_delay", cfg.pg_delay),
    ]
    width = max(len(name) for name, _ in fields)
    return "\n".join(f"  {name:<{width}} : {value}" for name, value in fields)


def prompt_uint32(prompt: str, current: int) -> int:
    while True:
        raw = input(f"{prompt} [{current}]: ").strip()
        if raw == "":
            return int(current)
        try:
            value = int(raw, 0)
        except ValueError:
            print("Please enter a decimal or 0x-prefixed integer.")
            continue
        if 0 <= value <= UINT32_MAX:
            return value
        print(f"Value must be between 0 and {UINT32_MAX}.")


def wait_enter_or_backspace(prompt: str = "Press Enter to send, Backspace to return to menu.") -> str:
    print(f"{COLOR_YELLOW}{prompt}{COLOR_RESET}")
    while True:
        key = msvcrt.getwch()
        if key == "\r":
            return "enter"
        if key == "\b":
            return "backspace"
        if key == "\x03":
            raise KeyboardInterrupt


def run_rtos_task_stats_get(
    session: VvTestSession,
    factory: CommandFactory,
    src: int,
    dst: int,
) -> None:
    pkt = factory.rtos_task_stats_get(src, dst, session.proto.next_seq())
    print_packet("\nPrepared", pkt)
    if wait_enter_or_backspace() == "backspace":
        print("[*] Back to menu.")
        return

    resp = send_and_wait(
        session,
        src,
        "rtos_task_stats_get_t",
        pkt,
        expected_param="rtos_task_stats_resp",
    )
    if resp is None:
        return

    tasks = list(resp.rtos_task_stats_resp.tasks)
    if not tasks:
        print("[INFO] rtos_task_stats_resp has no tasks.")
        return

    print("\nRTOS task stats:")
    print(f"{'id':>4}  {'cpu_permille':>11}  {'stack_min_free':>14}  name")
    for task in tasks:
        print(f"{task.task_id:>4}  {task.cpu_permille:>11}  {task.stack_min_free_bytes:>14}  {task.name}")


def get_current_config(
    session: VvTestSession,
    factory: CommandFactory,
    src: int,
    dst: int,
) -> Optional[pb.uwb_cfg_t]:
    get_pkt = factory.sys_config_get(src, dst, session.proto.next_seq())
    resp = send_and_wait(
        session,
        src,
        "sys_config_get_t",
        get_pkt,
        expected_param="sys_config_resp",
    )
    if resp is None:
        return None

    cfg = pb.uwb_cfg_t()
    cfg.CopyFrom(resp.sys_config_resp.config)
    return cfg


def run_sys_config_set(
    session: VvTestSession,
    factory: CommandFactory,
    src: int,
    dst: int,
) -> None:
    cfg = get_current_config(session, factory, src, dst)
    if cfg is None:
        print(f"{COLOR_RED}[FAIL] Cannot prepare sys_config_set_t without current config.{COLOR_RESET}")
        return

    print(f"\n{COLOR_CYAN}Current config from sys_config_get_t:{COLOR_RESET}")
    print(format_config(cfg))
    print("\nOnly these fields are editable here:")
    print("  tx_antenna_delay")
    print("  rx_antenna_delay")
    action = wait_enter_or_backspace("Press Enter to edit delays, Backspace to return to menu.")
    if action == "backspace":
        print("[*] Back to menu. Config was not changed.")
        return

    new_cfg = pb.uwb_cfg_t()
    new_cfg.CopyFrom(cfg)
    new_cfg.tx_antenna_delay = prompt_uint32("tx_antenna_delay", cfg.tx_antenna_delay)
    new_cfg.rx_antenna_delay = prompt_uint32("rx_antenna_delay", cfg.rx_antenna_delay)

    pkt = pb.packet_t()
    pkt.hdr.addr.src = src
    pkt.hdr.addr.dst = dst
    pkt.hdr.seq = session.proto.next_seq()
    pkt.sys_config_set.config.CopyFrom(new_cfg)

    print(f"\n{COLOR_CYAN}Prepared sys_config_set_t:{COLOR_RESET}")
    print(format_config(new_cfg))
    if wait_enter_or_backspace() == "backspace":
        print("[*] Back to menu. Config was not sent.")
        return

    send_and_wait(
        session,
        src,
        "sys_config_set_t",
        pkt,
        expected_param="sys_config_resp",
    )


MenuHandler = Callable[[VvTestSession, CommandFactory, int, int], None]


MENU: list[tuple[str, str, MenuHandler]] = [
    ("1", "rtos_task_stats_get_t", run_rtos_task_stats_get),
    ("2", "sys_config_set_t", run_sys_config_set),
]


def command_menu(session: VvTestSession, factory: CommandFactory, src: int, dst: int) -> None:
    while True:
        print("\n" + "=" * 58)
        print("  BLE Packet Sender")
        print("=" * 58)
        for key, name, _ in MENU:
            print(f"  [{key}] {name}")
        print("  [q] Quit")

        choice = input("Select command: ").strip().lower()
        if choice in ("q", "quit", "exit"):
            return

        for key, name, handler in MENU:
            if choice == key or choice == name.lower():
                print(f"\n[*] Selected {name}.")
                handler(session, factory, src, dst)
                break
        else:
            print("Invalid selection.")


def parse_mac(mac_text: Optional[str]) -> Optional[bytes]:
    if not mac_text:
        return None
    parts = mac_text.split(":")
    if len(parts) != 6:
        raise ValueError("MAC must have 6 bytes, e.g. AA:BB:CC:DD:EE:FF")
    return bytes(reversed([int(part, 16) for part in parts]))


def find_fallback_port() -> Optional[str]:
    for port_info in serial.tools.list_ports.comports():
        desc = port_info.description or ""
        if "USB" in desc or "JLink" in desc or "Serial" in desc:
            return port_info.device
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Interactive BLE packet sender")
    parser.add_argument("--port", default=None, help="COM port of the Central Dongle (e.g. COM7)")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate")
    parser.add_argument("--mac", type=str, default=None, help="Connect directly to this MAC (AA:BB:CC:DD:EE:FF)")
    parser.add_argument("--name", type=str, default=None, help="Scan only for devices containing this string in their name")
    parser.add_argument("--verbose", action="store_true", help="Print debug packet IO")
    parser.add_argument("--src", type=int, default=int(VvAddress.HOST), help="Source address (default: HOST=5)")
    parser.add_argument("--dst", type=int, default=int(VvAddress.MCU), help="MCU destination address (default: MCU=1)")
    parser.add_argument("--central-dst", type=int, default=int(VvAddress.CENTRAL), help="Central destination address (default: CENTRAL=3)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    src_addr = args.src
    mcu_dst = args.dst
    central_dst = args.central_dst

    try:
        expected_mac = parse_mac(args.mac)
    except ValueError as exc:
        print(f"{COLOR_RED}[ERROR] Invalid MAC address: {exc}{COLOR_RESET}")
        return 1

    port = args.port
    try:
        if not port:
            print("[*] Probing for Central Dongle COM port automatically...")
            probe = VvTestSession.auto_probe(src=src_addr, debug=args.verbose)
            if probe is not None:
                port = probe.port
                print(f"[+] Found Central Dongle: {port} @ {probe.baud}")
                args.baud = probe.baud
            else:
                port = find_fallback_port()
                if port:
                    print(f"[+] Found USB serial port via fallback scanning: {port}")
                else:
                    print(f"{COLOR_RED}[ERROR] No serial port found. Connect Central Dongle or use --port COMx.{COLOR_RESET}")
                    return 2

        factory = CommandFactory()
        with VvTestSession(port, args.baud, debug=args.verbose) as session:
            result = step_auto_scan_and_connect(
                session=session,
                factory=factory,
                src=src_addr,
                central_dst=central_dst,
                expected_mac=expected_mac,
                target_name_filter=args.name,
            )
            if not result:
                return 1

            bootstrap_mcu_route(session, factory, src_addr, mcu_dst)
            command_menu(session, factory, src_addr, mcu_dst)

            print("\n[-] Disconnecting BLE...")
            session.send_packet(factory.ble_disconnect(src_addr, central_dst, session.proto.next_seq()))
            time.sleep(0.2)

    except KeyboardInterrupt:
        print(f"\n{COLOR_YELLOW}[*] Stopping...{COLOR_RESET}")
        return 0
    except SerialException as exc:
        print(f"{COLOR_RED}[ERROR] Serial error: {exc}{COLOR_RESET}")
        return 1
    except Exception as exc:
        print(f"{COLOR_RED}[ERROR] Unexpected error: {exc}{COLOR_RESET}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
