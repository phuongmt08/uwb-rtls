from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
sys.path.append(str(Path(__file__).resolve().parent.parent.parent))

from common.commands import CommandFactory
from common.transport import VvAddress
from vv_test_session import VvTestSession

from serial.tools import list_ports


DEFAULT_BAUD = 115200
DEFAULT_SCAN_TIMEOUT_S = 12.0
DEFAULT_CONNECT_TIMEOUT_S = 15.0
DEFAULT_SRC = int(VvAddress.HOST)
DEFAULT_MCU_DST = int(VvAddress.MCU)
DEFAULT_CENTRAL_DST = int(VvAddress.CENTRAL)
TARGET_NAME_PREFIXES = ("UWB", "TAG", "ANCHOR", "NUS", "RTLS")


def _mac_to_str(mac: bytes) -> str:
    return ":".join(f"{b:02X}" for b in reversed(mac))


def _parse_mac(mac: str) -> bytes:
    parts = mac.replace("-", ":").split(":")
    if len(parts) != 6:
        raise argparse.ArgumentTypeError("MAC must look like AA:BB:CC:DD:EE:FF")
    try:
        return bytes(reversed([int(part, 16) for part in parts]))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("MAC contains non-hex byte") from exc


def _print_stream(pkt) -> None:
    s = pkt.sensor_fusion_result
    print(
        "sensor_fusion_result "
        f"t={s.timestamp_ms} "
        f"ukf=({s.ukf_x_m:.3f},{s.ukf_y_m:.3f},{s.ukf_yaw_deg:.2f}) "
        f"tril=({s.tril_x_m:.3f},{s.tril_y_m:.3f}) "
        f"yaw={s.yaw_deg:.2f} err={s.error_count}"
    )


def _score_dongle_port(port_info: list_ports.ListPortInfo) -> int:
    desc = (port_info.description or "").lower()
    manu = (port_info.manufacturer or "").lower()
    hwid = (port_info.hwid or "").lower()

    score = 0
    if "j-link" in desc or "jlink" in desc or "segger" in manu:
        score += 8
    if "nordic" in desc or "nordic" in manu or "nrf" in desc:
        score += 6
    if "usb" in desc or "usb" in manu:
        score += 3
    if "serial" in desc or "com" in desc:
        score += 2
    if "bluetooth" in desc:
        score -= 6
    if "vid:pid=1366" in hwid:
        score += 8
    return score


def _auto_select_dongle_port() -> str | None:
    ports = list(list_ports.comports())
    ports.sort(key=_score_dongle_port, reverse=True)

    for port_info in ports:
        if _score_dongle_port(port_info) <= 0:
            continue
        return port_info.device

    return None


def _drain_stale_packets(session: VvTestSession, verbose: bool) -> None:
    packets = session.recv_packets(timeout_s=0.15)
    if verbose and packets:
        print(f"drained {len(packets)} stale packet(s)")


def _send_ranging_cmd(session: VvTestSession, start: bool, verbose: bool) -> None:
    factory = CommandFactory()
    pkt = (
        factory.ranging_start(DEFAULT_SRC, DEFAULT_MCU_DST, session.proto.next_seq())
        if start
        else factory.ranging_stop(DEFAULT_SRC, DEFAULT_MCU_DST, session.proto.next_seq())
    )
    session.send_packet(pkt)
    if verbose:
        print("TX ranging_start" if start else "TX ranging_stop")


def _send_device_information_get(session: VvTestSession, verbose: bool) -> bool:
    factory = CommandFactory()
    pkt = factory.device_information_get(DEFAULT_SRC, DEFAULT_MCU_DST, session.proto.next_seq())

    print("\n-- STEP 2: Get MCU device information --")
    print(
        "TX device_information_get "
        f"src={pkt.hdr.addr.src} dst={pkt.hdr.addr.dst} seq={pkt.hdr.seq}"
    )
    session.send_packet(pkt)

    deadline = time.time() + 2.0
    while time.time() < deadline:
        for resp in session.recv_packets(timeout_s=0.1):
            msg = resp.WhichOneof("params")
            if msg == "device_information_resp":
                info = resp.device_information_resp
                print(
                    "MCU device_information_resp "
                    f"serial={info.serial_number} "
                    f"type={info.device_type} role={info.role} hw={info.hw_version}"
                )
                return True
            if msg == "ack":
                print(
                    "RX ack "
                    f"src={resp.hdr.addr.src} dst={resp.hdr.addr.dst} "
                    f"seq={resp.hdr.seq} ack_seq={resp.ack.ack_seq} "
                    f"response={resp.ack.response}"
                )
            else:
                print(
                    f"RX {msg} "
                    f"src={resp.hdr.addr.src} dst={resp.hdr.addr.dst} seq={resp.hdr.seq}"
                )

    print("WARNING: no device_information_resp from MCU")
    return False


def _ble_state_name(session: VvTestSession, state: int) -> str:
    enum = session.proto.pb.DESCRIPTOR.enum_types_by_name.get("ble_state_t")
    value = enum.values_by_number.get(state) if enum is not None else None
    return value.name if value is not None else f"BLE_STATE_UNKNOWN({state})"


def _scan_for_peripheral(
    session: VvTestSession,
    factory: CommandFactory,
    scan_timeout_s: float,
    target_mac: bytes | None,
    name_filter: str | None,
    verbose: bool,
) -> bytes | None:
    print("\n-- STEP 1A: BLE scan peripheral --")
    print("[+] Sending BLE Scan Start command...")
    scan = factory.ble_scan_start(DEFAULT_SRC, DEFAULT_CENTRAL_DST, session.proto.next_seq())
    scan.ble_scan_start.duration_ms = 0
    scan.ble_scan_start.interval_ms = 100
    scan.ble_scan_start.window_ms = 50
    scan.ble_scan_start.active_scanning = True
    session.send_packet(scan)

    if target_mac:
        print(f"[+] Scanning for device with MAC: {_mac_to_str(target_mac)}...")
    elif name_filter:
        print(f"[+] Scanning for device name containing: '{name_filter}'...")
    else:
        print("[+] Scanning for target UWB Peripheral devices...")

    target: bytes | None = None
    deadline = time.time() + scan_timeout_s
    name_filter_lower = name_filter.lower() if name_filter else None

    while time.time() < deadline:
        pkts = session.recv_packets(timeout_s=0.1)
        for pkt in pkts:
            msg = pkt.WhichOneof("params")
            if msg == "ble_scan_result":
                scan_result = pkt.ble_scan_result
                mac = bytes(scan_result.mac_address)
                mac_str = _mac_to_str(mac)
                dev_name = scan_result.name or ""
                rssi = scan_result.rssi_dbm
                print(f"  [Scan] Found: {mac_str} ('{dev_name}') | RSSI: {rssi} dBm")

                if target_mac is not None:
                    if mac == target_mac:
                        target = mac
                        print(f"\n[+] FOUND TARGET MAC: '{dev_name}' ({mac_str})")
                        break
                elif name_filter_lower:
                    if name_filter_lower in dev_name.lower():
                        target = mac
                        print(f"\n[+] FOUND TARGET NAME: '{dev_name}' ({mac_str})")
                        break
                else:
                    if any(prefix in dev_name.upper() for prefix in TARGET_NAME_PREFIXES):
                        target = mac
                        print(f"\n[+] FOUND TARGET DEVICE: '{dev_name}' ({mac_str})")
                        break
                    target = mac
                    print(f"\n[+] FOUND UUID-MATCHED DEVICE: '{dev_name}' ({mac_str})")
                    break
            elif msg == "ble_status_resp" and verbose:
                print(f"  [Status] {_ble_state_name(session, pkt.ble_status_resp.state)}")
            elif verbose:
                print(f"  [RX] {msg} src={pkt.hdr.addr.src} dst={pkt.hdr.addr.dst} seq={pkt.hdr.seq}")

        if target:
            break

    if not target:
        print("\n[FAIL] No target UWB Peripheral device found within scan timeout.")
        session.send_packet(factory.ble_scan_stop(DEFAULT_SRC, DEFAULT_CENTRAL_DST, session.proto.next_seq()))
        return None

    print("[-] Stopping BLE Scan...")
    session.send_packet(factory.ble_scan_stop(DEFAULT_SRC, DEFAULT_CENTRAL_DST, session.proto.next_seq()))
    time.sleep(0.5)
    return target


def _connect_selected_peripheral(
    session: VvTestSession,
    factory: CommandFactory,
    target: bytes,
    connect_timeout_s: float,
    verbose: bool,
) -> bool:
    print("\n-- STEP 1B: BLE connect peripheral --")
    mac_str = _mac_to_str(target)
    max_attempts = 3

    for attempt in range(1, max_attempts + 1):
        print(f"[+] Sending connect command to {mac_str} (attempt {attempt}/{max_attempts})...")
        pkt = factory.ble_connect(DEFAULT_SRC, DEFAULT_CENTRAL_DST, session.proto.next_seq())
        pkt.ble_connect.mac_address = target
        session.send_packet(pkt)

        print("[+] Waiting for BLE connection...")
        deadline = time.time() + connect_timeout_s
        retry_needed = False

        while time.time() < deadline:
            pkts = session.recv_packets(timeout_s=0.1)
            for resp in pkts:
                msg = resp.WhichOneof("params")
                if msg == "ble_status_resp":
                    state = resp.ble_status_resp.state
                    state_name = _ble_state_name(session, state)
                    if verbose:
                        print(f"  [Status] {state_name}")
                    if state == session.proto.pb.BLE_STATE_CONNECTED:
                        print("[OK] BLE CONNECTION ESTABLISHED!")
                        return True
                    if state == session.proto.pb.BLE_STATE_IDLE and resp.ble_status_resp.HasField("disconnect_reason"):
                        reason = resp.ble_status_resp.disconnect_reason
                        print(f"  [WARNING] Connection attempt {attempt} failed. Reason: 0x{reason:02X}")
                        retry_needed = True
                        break
                elif verbose:
                    print(f"  [RX] {msg} src={resp.hdr.addr.src} dst={resp.hdr.addr.dst} seq={resp.hdr.seq}")

            if retry_needed:
                break

        if attempt < max_attempts:
            time.sleep(1.0)

    print("[FAIL] Could not establish BLE connection.")
    return False


def _connect_peripheral(
    session: VvTestSession,
    scan_timeout_s: float,
    connect_timeout_s: float,
    target_mac: bytes | None,
    name_filter: str | None,
    verbose: bool,
) -> bool:
    factory = CommandFactory()

    _drain_stale_packets(session, verbose)
    target = _scan_for_peripheral(session, factory, scan_timeout_s, target_mac, name_filter, verbose)
    if target is None:
        return False

    return _connect_selected_peripheral(session, factory, target, connect_timeout_s, verbose)


def run(
    session: VvTestSession,
    seconds: float,
    verbose: bool,
    control_ranging: bool,
    scan_timeout_s: float,
    connect_timeout_s: float,
    target_mac: bytes | None,
    name_filter: str | None,
    skip_connect: bool,
) -> bool:
    _drain_stale_packets(session, verbose)

    if not skip_connect:
        print("\n-- STEP 1: BLE central scan/connect peripheral --")
        if not _connect_peripheral(
            session,
            scan_timeout_s=scan_timeout_s,
            connect_timeout_s=connect_timeout_s,
            target_mac=target_mac,
            name_filter=name_filter,
            verbose=verbose,
        ):
            return False

    _send_device_information_get(session, verbose)

    if control_ranging:
        print("\n-- STEP 3: Start MCU ranging --")
        _send_ranging_cmd(session, start=True, verbose=verbose)
        time.sleep(0.2)

    print("\n-- STEP 4: Listen fusion stream from dongle USB --")
    print(f"Listening on dongle USB for sensor_fusion_result for {seconds:.1f}s...")
    deadline = time.time() + seconds
    stream_packets = []

    while time.time() < deadline:
        for pkt in session.recv_packets(timeout_s=0.1):
            name = pkt.WhichOneof("params")
            if name == "sensor_fusion_result":
                stream_packets.append(pkt)
                if verbose:
                    _print_stream(pkt)
            elif verbose:
                print(f"RX {name} src={pkt.hdr.addr.src} dst={pkt.hdr.addr.dst} seq={pkt.hdr.seq}")

    if control_ranging:
        _send_ranging_cmd(session, start=False, verbose=verbose)

    if not stream_packets:
        print("ERROR: no sensor_fusion_result packets received from dongle USB")
        print("Check: central connected to peripheral, MCU ranging enabled, and MCU stream dst=HOST.")
        return False

    timestamps = [p.sensor_fusion_result.timestamp_ms for p in stream_packets]
    monotonic = all(b >= a for a, b in zip(timestamps, timestamps[1:]))
    duration_s = max(seconds, 0.001)
    observed_hz = len(stream_packets) / duration_s

    first = stream_packets[0].sensor_fusion_result
    last = stream_packets[-1].sensor_fusion_result
    print(
        f"received={len(stream_packets)} rate={observed_hz:.2f}Hz "
        f"first_t={first.timestamp_ms} last_t={last.timestamp_ms}"
    )

    if not monotonic:
        print("ERROR: sensor_fusion_result timestamp_ms is not monotonic")
        return False

    if observed_hz > 15.0:
        print("ERROR: sensor_fusion_result rate is higher than expected low-rate app telemetry")
        return False

    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Listen for MCU sensor_fusion_result packets forwarded by the BLE central dongle over USB CDC.")
    parser.add_argument("--port", help="Central dongle COM port, e.g. COM28. If omitted, a USB/J-Link-like port is selected.")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--scan-timeout", type=float, default=DEFAULT_SCAN_TIMEOUT_S)
    parser.add_argument("--connect-timeout", type=float, default=DEFAULT_CONNECT_TIMEOUT_S)
    parser.add_argument("--mac", type=_parse_mac, help="Peripheral MAC to connect, printed as AA:BB:CC:DD:EE:FF.")
    parser.add_argument("--name", help="Connect first scan result whose advertised name contains this text.")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--skip-connect", action="store_true", help="Assume central is already connected to peripheral.")
    parser.add_argument("--no-control", action="store_true", help="Only listen; do not send ranging_start/ranging_stop through the dongle.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    port = args.port
    baud = args.baud
    if port is None:
        port = _auto_select_dongle_port()
        if port is None:
            print("ERROR: no central dongle USB serial port found")
            print("Pass the port explicitly, for example: py ./test_fusion_stream.py --port COM28")
            return 1
        print(f"Selected dongle: {port} @ {baud}")
    else:
        print(f"Using dongle: {port} @ {baud}")

    with VvTestSession(port, baud=baud, debug=args.verbose) as session:
        ok = run(
            session,
            args.seconds,
            args.verbose,
            control_ranging=not args.no_control,
            scan_timeout_s=args.scan_timeout,
            connect_timeout_s=args.connect_timeout,
            target_mac=args.mac,
            name_filter=args.name,
            skip_connect=args.skip_connect,
        )

    print("FUSION STREAM OK" if ok else "FUSION STREAM FAILED")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
