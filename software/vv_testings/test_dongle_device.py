from __future__ import annotations
import argparse
import os
import sys
import time
from typing import Optional

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from google.protobuf.json_format import MessageToDict
from serial import SerialException

from common import protocol_pb2 as pb
from common.commands import CommandFactory
from common.transport import HostTransport, VvAddress
from vv_test_session import VvTestSession


DEFAULT_BAUD = 115200
DEFAULT_TIMEOUT_S = 1.0
DEFAULT_SCAN_TIMEOUT_S = 5.0


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


def parse_addr(raw: str) -> int:
    text = str(raw).strip()
    if text.lower().startswith("0x"):
        return int(text, 16)
    if text.lstrip("-").isdigit():
        return int(text, 10)

    name = text.upper()
    if not name.startswith("PACKET_ADDR_"):
        name = f"PACKET_ADDR_{name}"
    if not hasattr(pb, name):
        choices = ", ".join(a.name for a in VvAddress)
        raise argparse.ArgumentTypeError(f"Unknown address '{raw}'. Use number or one of: {choices}")
    return int(getattr(pb, name))


def addr_name(value: int) -> str:
    try:
        return VvAddress(int(value)).name
    except ValueError:
        return f"UNKNOWN({value})"


def parse_mac(raw: str) -> bytes:
    parts = raw.strip().split(":")
    if len(parts) != 6:
        raise argparse.ArgumentTypeError("MAC must be AA:BB:CC:DD:EE:FF")
    try:
        return bytes(reversed([int(part, 16) for part in parts]))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("MAC must be AA:BB:CC:DD:EE:FF") from exc


def format_mac(mac_bytes: bytes) -> str:
    return ":".join(f"{b:02X}" for b in reversed(mac_bytes))


def first_param(packets: list[pb.packet_t], name: str) -> pb.packet_t | None:
    for pkt in packets:
        if packet_name(pkt) == name:
            return pkt
    return None


def send_case(
    session: VvTestSession,
    title: str,
    pkt: pb.packet_t,
    expected: str | None,
    timeout_s: float,
) -> tuple[bool, list[pb.packet_t]]:
    print(f"\n--- {title} ---")
    print_packet("TX", pkt)
    packets = session.send_and_wait(pkt, timeout_s=timeout_s)
    if not packets:
        print("RX <none>")
    else:
        for rx in packets:
            print_packet("RX", rx)

    ok = True if expected is None else first_param(packets, expected) is not None
    status = "PASS" if ok else "FAIL"
    expected_text = expected if expected is not None else "any response"
    print(f"[{status}] expected={expected_text}")
    return ok, packets


def listen_position_packets(session: VvTestSession, seconds: float) -> tuple[int, int, int]:
    print(f"\n--- listen position packets ({seconds:.1f}s) ---")
    deadline = time.time() + seconds
    ranging_count = 0
    fusion_count = 0
    vehicle_pos_count = 0

    while time.time() < deadline:
        for pkt in session.recv_packets(timeout_s=0.1):
            name = packet_name(pkt)
            if name == "ranging_result":
                ranging_count += 1
                r = pkt.ranging_result
                print(
                    f"RX ranging_result #{ranging_count}: "
                    f"x={r.pos_x_m:.3f} y={r.pos_y_m:.3f} z={r.pos_z_m:.3f} rms={r.rms_error_m:.3f}"
                )
            elif name == "sensor_fusion_result":
                fusion_count += 1
                f = pkt.sensor_fusion_result
                print(
                    f"RX sensor_fusion_result #{fusion_count}: "
                    f"ukf=({f.ukf_x_m},{f.ukf_y_m}) tril=({f.tril_x_m},{f.tril_y_m}) yaw={f.yaw_deg}"
                )
            elif name == "position_vehicle":
                vehicle_pos_count += 1
                p = pkt.position_vehicle
                print(f"RX position_vehicle #{vehicle_pos_count}: x={p.x:.3f} y={p.y:.3f}")

    print(
        "position summary: "
        f"ranging_result={ranging_count} "
        f"sensor_fusion_result={fusion_count} "
        f"position_vehicle={vehicle_pos_count}"
    )
    return ranging_count, fusion_count, vehicle_pos_count


def scan_and_connect(
    session: VvTestSession,
    factory: CommandFactory,
    src: int,
    central_dst: int,
    scan_timeout_s: float,
    target_name: str | None = None,
    target_mac: bytes | None = None,
) -> tuple[bytes, str] | None:
    print("\n--- scan and connect ---")
    session.send_packet(factory.ble_scan_start(src, central_dst, session.proto.next_seq()))
    deadline = time.time() + scan_timeout_s
    discovered: dict[bytes, tuple[str, int, int]] = {}

    while time.time() < deadline:
        for pkt in session.recv_packets(timeout_s=0.1):
            if packet_name(pkt) != "ble_scan_result":
                continue

            mac = bytes(pkt.ble_scan_result.mac_address)
            name = pkt.ble_scan_result.name
            rssi = int(pkt.ble_scan_result.rssi_dbm)
            serial_number = int(pkt.ble_scan_result.serial_number)

            if target_mac is not None and mac != target_mac:
                continue
            if target_name and target_name.upper() not in name.upper():
                continue
            if target_mac is None and target_name is None:
                name_upper = name.upper()
                if not any(token in name_upper for token in ("UWB", "TAG", "ANCHOR", "RTLS", "NODE")):
                    continue

            if mac not in discovered:
                discovered[mac] = (name, rssi, serial_number)
                print(f"found {format_mac(mac)} name='{name}' rssi={rssi} serial={serial_number}")

        if target_mac is not None and target_mac in discovered:
            break

    session.send_packet(factory.ble_scan_stop(src, central_dst, session.proto.next_seq()))
    time.sleep(0.5)

    if not discovered:
        print("[FAIL] no matching BLE device found")
        return None

    devices = list(discovered.items())
    if len(devices) == 1:
        selected_mac, (selected_name, _, _) = devices[0]
    else:
        print("\nDiscovered devices:")
        for idx, (mac, (name, rssi, serial_number)) in enumerate(devices, start=1):
            print(f"  {idx}. {format_mac(mac)} name='{name}' rssi={rssi} serial={serial_number}")
        raw = input(f"Select device [1-{len(devices)}] default 1: ").strip()
        index = int(raw) if raw else 1
        if index < 1 or index > len(devices):
            print("[FAIL] invalid device selection")
            return None
        selected_mac, (selected_name, _, _) = devices[index - 1]

    print(f"connecting {format_mac(selected_mac)} name='{selected_name}'")
    for attempt in range(1, 4):
        pkt = factory.ble_connect(src, central_dst, session.proto.next_seq())
        pkt.ble_connect.mac_address = selected_mac
        session.send_packet(pkt)

        deadline = time.time() + 15.0
        while time.time() < deadline:
            for rx in session.recv_packets(timeout_s=0.1):
                if packet_name(rx) != "ble_status_resp":
                    continue
                state = int(rx.ble_status_resp.state)
                reason = int(rx.ble_status_resp.disconnect_reason)
                print(f"ble_status state={state} reason=0x{reason:02X}")
                if state == int(pb.BLE_STATE_CONNECTED):
                    print("[PASS] BLE connected")
                    return selected_mac, selected_name
                if state == int(pb.BLE_STATE_IDLE) and reason != 0:
                    break
            else:
                continue
            break

        if attempt < 3:
            session.send_packet(factory.ble_disconnect(src, central_dst, session.proto.next_seq()))
            time.sleep(1.5)

    print("[FAIL] BLE connect failed")
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only device smoke test through a USB dongle/central serial link."
    )
    parser.add_argument("--port", default=None, help="Dongle serial port, example COM28 or /dev/ttyACM0")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD, help="Serial baud rate")
    parser.add_argument("--src", type=parse_addr, default=int(VvAddress.HOST), help="Source address, default HOST")
    parser.add_argument("--dst", type=parse_addr, default=int(VvAddress.MCU), help="Device destination, default MCU")
    parser.add_argument(
        "--central-dst",
        type=parse_addr,
        default=int(VvAddress.CENTRAL),
        help="Central dongle destination for BLE status checks, default CENTRAL",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S, help="Response timeout seconds")
    parser.add_argument("--skip-central", action="store_true", help="Skip ble_status_get to the central dongle")
    parser.add_argument("--connect", action="store_true", help="Scan and connect the dongle to a BLE device before MCU checks")
    parser.add_argument("--scan-timeout", type=float, default=DEFAULT_SCAN_TIMEOUT_S, help="BLE scan timeout seconds")
    parser.add_argument("--target-name", default=None, help="Only connect to a scanned device whose name contains this text")
    parser.add_argument("--mac", type=parse_mac, default=None, help="Only connect to this BLE MAC, format AA:BB:CC:DD:EE:FF")
    parser.add_argument(
        "--set-host-transport",
        choices=["usb", "uart"],
        default=None,
        help="Optionally send host_transport_set before device checks",
    )
    parser.add_argument(
        "--listen-position",
        type=float,
        default=0.0,
        help="Listen for ranging_result/sensor_fusion_result/position_vehicle packets for N seconds",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    factory = CommandFactory()

    port = args.port
    baud = args.baud
    if port is None:
        probe = VvTestSession.auto_probe(
            src=args.src,
            dst=args.dst,
            debug=False,
            expected_params=("device_information_resp", "ack"),
            response_timeout_s=args.timeout,
        )
        if probe is None:
            print("No dongle/device response found. Pass --port explicitly, e.g. --port COM28 or --port /dev/ttyACM0")
            return 2
        port = probe.port
        baud = probe.baud

    print(f"Connected: {port} @ {baud}")
    print(f"Route: src={addr_name(args.src)}({args.src}) dst={addr_name(args.dst)}({args.dst})")

    try:
        with VvTestSession(port, baud=baud, debug=True) as session:
            results: list[tuple[str, bool]] = []

            if not args.skip_central:
                pkt = factory.ble_status_get(args.src, args.central_dst, session.proto.next_seq())
                ok, _ = send_case(session, "central ble_status_get", pkt, "ble_status_resp", args.timeout)
                results.append(("central_ble_status", ok))

            if args.connect:
                connected = scan_and_connect(
                    session=session,
                    factory=factory,
                    src=args.src,
                    central_dst=args.central_dst,
                    scan_timeout_s=args.scan_timeout,
                    target_name=args.target_name,
                    target_mac=args.mac,
                )
                if connected is None:
                    return 2

            if args.set_host_transport is not None:
                transport = HostTransport.UART if args.set_host_transport == "uart" else HostTransport.USB
                pkt = factory.host_transport_set(args.src, args.dst, session.proto.next_seq(), transport=int(transport))
                ok, _ = send_case(session, f"host_transport_set {args.set_host_transport}", pkt, None, args.timeout)
                results.append(("host_transport_set", ok))

            pkt = factory.device_information_get(args.src, args.dst, session.proto.next_seq())
            ok, _ = send_case(session, "device_information_get", pkt, "device_information_resp", args.timeout)
            results.append(("device_information", ok))

            pkt = factory.device_type_get(args.src, args.dst, session.proto.next_seq())
            ok, _ = send_case(session, "device_type_get", pkt, "device_type_set", args.timeout)
            results.append(("device_type", ok))

            pkt = factory.battery_info_get(args.src, args.dst, session.proto.next_seq())
            ok, _ = send_case(session, "battery_info_get", pkt, "battery_info_resp", args.timeout)
            results.append(("battery_info", ok))

            pkt = factory.anchor_layout_get(args.src, args.dst, session.proto.next_seq())
            ok, _ = send_case(session, "anchor_layout_get", pkt, "anchor_layout_resp", args.timeout)
            results.append(("anchor_layout", ok))

            if args.listen_position > 0:
                listen_position_packets(session, args.listen_position)

    except SerialException as exc:
        print(f"Serial error: {exc}")
        return 1

    print("\n=== FINAL RESULT ===")
    fail_count = 0
    for name, ok in results:
        if not ok:
            fail_count += 1
        print(f"{name:<22} {'PASS' if ok else 'FAIL'}")
    print("OVERALL PASS" if fail_count == 0 else "OVERALL FAIL")
    return 0 if fail_count == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
