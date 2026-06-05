from __future__ import annotations

import argparse
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from common import protocol_pb2 as pb
from vv_test_session import VvTestSession


OTP_CONFIRM_MAGIC = 0x4F545057  # "OTPW"

OTP_TYPE_DEVICE_INFO = 0x01
OTP_TYPE_ANTENNA_DELAY = 0x02

DEVICE_TYPES = {
    "tag": pb.DEVICE_TYPE_TAG,
    "anchor": pb.DEVICE_TYPE_ANCHOR,
    "gateway": pb.DEVICE_TYPE_GATEWAY,
    "debug": pb.DEVICE_TYPE_DEBUG_TOOL,
}

DSTS = {
    "mcu": pb.PACKET_ADDR_MCU,
    "bcast": pb.PACKET_ADDR_BCAST,
    "central": pb.PACKET_ADDR_CENTRAL,
    "peripheral": pb.PACKET_ADDR_PERIPHERAL,
}


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


def _build_packet(args: argparse.Namespace, seq: int) -> pb.packet_t:
    pkt = pb.packet_t()
    pkt.hdr.addr.src = pb.PACKET_ADDR_DEBUG
    pkt.hdr.addr.dst = DSTS[args.dst]
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


def _has_positive_ack(packets: list[pb.packet_t], seq: int) -> tuple[bool, str]:
    for pkt in packets:
        if pkt.WhichOneof("params") == "ack" and pkt.ack.ack_seq == seq:
            return pkt.ack.response == pb.PACKET_ACK_RESPONSE_ACK, _ack_response_name(pkt.ack.response)
    return False, "NO_ACK"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="One-time factory OTP provisioning command")
    parser.add_argument("--port", default=None, help="COM port, e.g. COM7. Auto-probes when omitted.")
    parser.add_argument("--dst", choices=sorted(DSTS), default="bcast")
    parser.add_argument("--field", choices=["device_info", "antenna_delay"], required=True)
    parser.add_argument("--device-type", choices=sorted(DEVICE_TYPES), default="anchor")
    parser.add_argument("--mfg-date", type=_parse_u32, default=0, help="Manufacturing date DDMMYYYY for device_info, e.g. 23052026")
    parser.add_argument("--hw-rev", type=_parse_u8, default=None, help="Hardware revision for device_info")
    parser.add_argument("--tx", type=_parse_u32, default=16436)
    parser.add_argument("--rx", type=_parse_u32, default=16436)
    parser.add_argument("--yes", action="store_true", help="Required: confirm this irreversible OTP write")
    parser.add_argument("--timeout", type=float, default=0.8)
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.yes:
        print("Refusing to write OTP without --yes.")
        print("This consumes one OTP record and cannot be erased on real OTP.")
        return 2

    if args.field == "antenna_delay" and (args.tx > 0xFFFF or args.rx > 0xFFFF):
        print("tx/rx antenna delay must fit uint16.")
        return 2
    if args.field == "device_info" and args.mfg_date == 0:
        print("device_info requires --mfg-date, e.g. --mfg-date 23052026.")
        return 2
    if args.field == "device_info" and not _valid_mfg_date(args.mfg_date):
        print("mfg-date must be DDMMYYYY with year 2000..2255, e.g. 23052026.")
        return 2
    if args.field == "device_info" and args.hw_rev is None:
        print("device_info requires --hw-rev, e.g. --hw-rev 1.")
        return 2

    port = args.port
    if port is None:
        probe = VvTestSession.auto_probe(debug=False)
        if probe is None:
            print("No device found. Pass --port COMx.")
            return 1
        port = probe.port
        print(f"Auto-probed {port} serial={probe.serial_number}")

    with VvTestSession(port, debug=True) as session:
        seq = session.proto.next_seq()
        pkt = _build_packet(args, seq)
        print(f"Writing OTP field={args.field} dst={args.dst} seq={seq}")
        packets = session.send_and_wait(pkt, timeout_s=args.timeout)

    ok, response = _has_positive_ack(packets, seq)
    print(f"ACK response: {response}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
