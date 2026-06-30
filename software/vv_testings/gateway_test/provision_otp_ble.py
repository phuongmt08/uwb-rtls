from __future__ import annotations
"""
provision_otp_ble.py - One-time factory OTP provisioning over BLE.

This is the BLE variant of software/vv_testings/provision_otp.py. It connects
to the BLE Central Dongle, scans/connects to the target peripheral like
test_ble_log.py, then sends factory_otp_write to the MCU through BLE.

Usage:
  python software/vv_testings/gateway_test/provision_otp_ble.py --field antenna_delay --tx 16436 --rx 16436 --yes
  python software/vv_testings/gateway_test/provision_otp_ble.py --field device_info --device-type anchor --mfg-date 23052026 --hw-rev 1 --yes
  python software/vv_testings/gateway_test/provision_otp_ble.py --port COM28 --mac AA:BB:CC:DD:EE:FF --field antenna_delay --tx 16436 --rx 16436 --yes
"""

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Optional

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
from test_ble_log import step_auto_scan_and_connect


OTP_CONFIRM_MAGIC = 0x4F545057  # "OTPW"

OTP_TYPE_DEVICE_INFO = 0x01
OTP_TYPE_ANTENNA_DELAY = 0x02

DEVICE_TYPES = {
    "tag": pb.DEVICE_TYPE_TAG,
    "anchor": pb.DEVICE_TYPE_ANCHOR,
    "gateway": pb.DEVICE_TYPE_GATEWAY,
    "debug": pb.DEVICE_TYPE_DEBUG_TOOL,
}

COLOR_GREEN = "\033[32m"
COLOR_RED = "\033[31m"
COLOR_CYAN = "\033[36m"
COLOR_YELLOW = "\033[33m"
COLOR_GRAY = "\033[90m"
COLOR_RESET = "\033[0m"


def _parse_u32(text: str) -> int:
    value = int(text, 0)
    if value < 0 or value > 0xFFFFFFFF:
        raise argparse.ArgumentTypeError("value must fit uint32")
    return value


def _parse_u8(text: str) -> int:
    value = int(text, 0)
    if value < 0 or value > 0xFF:
        raise argparse.ArgumentTypeError("value must fit uint8")
    return value


def _valid_mfg_date(date_ddmmyyyy: int) -> bool:
    day = date_ddmmyyyy // 1_000_000
    month = (date_ddmmyyyy // 10_000) % 100
    year = date_ddmmyyyy % 10_000
    return 1 <= day <= 31 and 1 <= month <= 12 and 2000 <= year <= 2255


def _parse_mac(mac_text: Optional[str]) -> Optional[bytes]:
    if not mac_text:
        return None
    parts = mac_text.split(":")
    if len(parts) != 6:
        raise argparse.ArgumentTypeError("MAC must be AA:BB:CC:DD:EE:FF")
    try:
        return bytes(reversed([int(part, 16) for part in parts]))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("MAC must be hex bytes") from exc


def _packet_name(pkt: pb.packet_t) -> str:
    return pkt.WhichOneof("params") or "<none>"


def _packet_to_dict(pkt: pb.packet_t) -> dict:
    return MessageToDict(
        pkt,
        preserving_proto_field_name=True,
        use_integers_for_enums=False,
    )


def _print_packet(prefix: str, pkt: pb.packet_t) -> None:
    print(f"{prefix} {_packet_name(pkt)}: {_packet_to_dict(pkt)}")


def _build_factory_otp_write(args: argparse.Namespace, src: int, dst: int, seq: int) -> pb.packet_t:
    pkt = pb.packet_t()
    pkt.hdr.addr.src = src
    pkt.hdr.addr.dst = dst
    pkt.hdr.seq = seq

    cmd = pkt.factory_otp_write
    cmd.confirm_magic = OTP_CONFIRM_MAGIC

    if args.field == "device_info":
        cmd.otp_type = OTP_TYPE_DEVICE_INFO
        cmd.device_type = DEVICE_TYPES[args.device_type]
        cmd.value_u32 = args.mfg_date
        cmd.value_u8 = args.hw_rev
    elif args.field == "antenna_delay":
        cmd.otp_type = OTP_TYPE_ANTENNA_DELAY
        cmd.tx_antenna_delay = args.tx
        cmd.rx_antenna_delay = args.rx
    else:
        raise ValueError(f"unsupported field: {args.field}")

    return pkt


def _ack_response_name(value: int) -> str:
    try:
        return pb.packet_ack_response_t.Name(value)
    except ValueError:
        return str(value)


def _build_ack(session: VvTestSession, src: int, pkt: pb.packet_t) -> pb.packet_t:
    ack = pb.packet_t()
    ack.hdr.addr.src = src
    ack.hdr.addr.dst = int(pkt.hdr.addr.src)
    ack.hdr.seq = session.proto.next_seq()
    ack.ack.ack_seq = int(pkt.hdr.seq)
    ack.ack.response = pb.PACKET_ACK_RESPONSE_ACK
    return ack


def _ack_mcu_packet_if_needed(session: VvTestSession, src: int, pkt: pb.packet_t) -> None:
    if _packet_name(pkt) == "ack":
        return
    try:
        if int(pkt.hdr.addr.src) != int(VvAddress.MCU):
            return
    except (AttributeError, TypeError, ValueError):
        return
    session.send_packet(_build_ack(session, src, pkt))


def _send_and_wait_for_ack(
    session: VvTestSession,
    src: int,
    pkt: pb.packet_t,
    timeout_s: float,
) -> tuple[bool, str]:
    session.send_packet(pkt)

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        packets = session.recv_packets(timeout_s=0.05)
        for rx in packets:
            _print_packet("RX", rx)
            name = _packet_name(rx)
            if name == "ack" and int(rx.ack.ack_seq) == int(pkt.hdr.seq):
                response_name = _ack_response_name(int(rx.ack.response))
                return rx.ack.response == pb.PACKET_ACK_RESPONSE_ACK, response_name
            _ack_mcu_packet_if_needed(session, src, rx)

    return False, "NO_ACK"


def _send_quiet(session: VvTestSession, pkt: pb.packet_t, settle_s: float = 0.2) -> None:
    session.send_packet(pkt)
    time.sleep(settle_s)
    session.recv_packets(timeout_s=0.05)


def _bootstrap_mcu_route(session: VvTestSession, factory: CommandFactory, src: int, mcu_dst: int) -> None:
    print("\n[*] Preparing MCU route over BLE...")

    none_pkt = pb.packet_t()
    none_pkt.hdr.addr.src = src
    none_pkt.hdr.addr.dst = mcu_dst
    none_pkt.hdr.seq = session.proto.next_seq()
    none_pkt.none.dummy = 0
    _send_quiet(session, none_pkt)

    transport_pkt = factory.host_transport_set(
        src,
        mcu_dst,
        session.proto.next_seq(),
        transport=int(HostTransport.USB),
    )
    _send_quiet(session, transport_pkt)


def _find_fallback_port() -> Optional[str]:
    for port_info in serial.tools.list_ports.comports():
        desc = port_info.description or ""
        if "USB" in desc or "JLink" in desc or "Serial" in desc:
            return port_info.device
    return None


def _validate_args(args: argparse.Namespace) -> Optional[str]:
    if not args.yes:
        return "Refusing to write OTP without --yes. This consumes one OTP record and cannot be erased on real OTP."
    if args.field == "antenna_delay" and (args.tx > 0xFFFF or args.rx > 0xFFFF):
        return "tx/rx antenna delay must fit uint16."
    if args.field == "device_info" and args.mfg_date == 0:
        return "device_info requires --mfg-date, e.g. --mfg-date 23052026."
    if args.field == "device_info" and not _valid_mfg_date(args.mfg_date):
        return "mfg-date must be DDMMYYYY with year 2000..2255, e.g. 23052026."
    if args.field == "device_info" and args.hw_rev is None:
        return "device_info requires --hw-rev, e.g. --hw-rev 1."
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="One-time factory OTP provisioning over BLE")
    parser.add_argument("--port", default=None, help="COM port of the Central Dongle (e.g. COM28)")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate")
    parser.add_argument("--mac", type=_parse_mac, default=None, help="Target BLE MAC, e.g. AA:BB:CC:DD:EE:FF")
    parser.add_argument("--name", type=str, default=None, help="Scan only for devices containing this string in their name")
    parser.add_argument("--field", choices=["device_info", "antenna_delay"], required=True)
    parser.add_argument("--device-type", choices=sorted(DEVICE_TYPES), default="anchor")
    parser.add_argument("--mfg-date", type=_parse_u32, default=0, help="Manufacturing date DDMMYYYY for device_info")
    parser.add_argument("--hw-rev", type=_parse_u8, default=None, help="Hardware revision for device_info")
    parser.add_argument("--tx", type=_parse_u32, default=16436, help="TX antenna delay for antenna_delay")
    parser.add_argument("--rx", type=_parse_u32, default=16436, help="RX antenna delay for antenna_delay")
    parser.add_argument("--yes", action="store_true", help="Required: confirm this irreversible OTP write")
    parser.add_argument("--timeout", type=float, default=2.0, help="ACK timeout after sending factory_otp_write")
    parser.add_argument("--verbose", action="store_true", help="Print low-level serial debug")
    parser.add_argument("--src", type=int, default=int(VvAddress.HOST), help="Source address (default: HOST=5)")
    parser.add_argument("--dst", type=int, default=int(VvAddress.MCU), help="MCU destination address (default: MCU=1)")
    parser.add_argument("--central-dst", type=int, default=int(VvAddress.CENTRAL), help="Central destination address (default: CENTRAL=3)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    validation_error = _validate_args(args)
    if validation_error:
        print(f"{COLOR_RED}{validation_error}{COLOR_RESET}")
        return 2

    port = args.port
    try:
        if not port:
            print("[*] Probing for Central Dongle COM port automatically...")
            probe = VvTestSession.auto_probe(src=args.src, debug=args.verbose)
            if probe is not None:
                port = probe.port
                args.baud = probe.baud
                print(f"[+] Found Central Dongle: {port} @ {probe.baud}")
            else:
                port = _find_fallback_port()
                if port:
                    print(f"[+] Found USB serial port via fallback scanning: {port}")
                else:
                    print(f"{COLOR_RED}[ERROR] No serial port found. Connect Central Dongle or use --port COMx.{COLOR_RESET}")
                    return 1

        factory = CommandFactory()
        with VvTestSession(port, args.baud, debug=args.verbose) as session:
            target = step_auto_scan_and_connect(
                session=session,
                factory=factory,
                src=args.src,
                central_dst=args.central_dst,
                expected_mac=args.mac,
                target_name_filter=args.name,
            )
            if not target:
                return 1

            _bootstrap_mcu_route(session, factory, args.src, args.dst)

            seq = session.proto.next_seq()
            pkt = _build_factory_otp_write(args, args.src, args.dst, seq)
            print(f"\n{COLOR_YELLOW}About to write OTP field={args.field} seq={seq}.{COLOR_RESET}")
            _print_packet("TX", pkt)

            ok, response = _send_and_wait_for_ack(session, args.src, pkt, timeout_s=args.timeout)
            print(f"\nACK response: {response}")

            print("\n[-] Disconnecting BLE...")
            session.send_packet(factory.ble_disconnect(args.src, args.central_dst, session.proto.next_seq()))
            time.sleep(0.2)

            if ok:
                print(f"{COLOR_GREEN}[OK] OTP write accepted by MCU.{COLOR_RESET}")
                return 0

            print(f"{COLOR_RED}[FAIL] OTP write was not accepted.{COLOR_RESET}")
            return 1

    except KeyboardInterrupt:
        print(f"\n{COLOR_YELLOW}[*] Stopping...{COLOR_RESET}")
        return 0
    except SerialException as exc:
        print(f"{COLOR_RED}[ERROR] Serial error: {exc}{COLOR_RESET}")
        return 1
    except Exception as exc:
        print(f"{COLOR_RED}[ERROR] Unexpected error: {exc}{COLOR_RESET}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
