from __future__ import annotations

import argparse
import msvcrt
import sys
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

sys.path.append(str(Path(__file__).resolve().parent.parent))
sys.path.append(str(Path(__file__).resolve().parent.parent.parent))

from common import protocol_pb2 as pb
from common.commands import CommandFactory
from common.transport import VvAddress
from vv_test_session import VvTestSession

from serial.tools import list_ports


DEFAULT_BAUD = 115200
DEFAULT_SCAN_TIMEOUT_S = 6.0
DEFAULT_CONNECT_TIMEOUT_S = 15.0
DEFAULT_SRC = int(VvAddress.HOST)
DEFAULT_MCU_DST = int(VvAddress.MCU)
DEFAULT_CENTRAL_DST = int(VvAddress.CENTRAL)
TARGET_NAME_PREFIXES = ("UWB", "TAG", "ANCHOR", "NUS", "RTLS", "NODE")
DEFAULT_EXPECTED_HZ = 100.0
STREAM_RECV_TIMEOUT_S = 0.05
STREAM_STAT_PERIOD_S = 1.0
STOP_SESSION_DELAY_S = 0.2


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
        if _score_dongle_port(port_info) > 0:
            return port_info.device
    return None


def _ble_state_name(session: VvTestSession, state: int) -> str:
    enum = session.proto.pb.DESCRIPTOR.enum_types_by_name.get("ble_state_t")
    value = enum.values_by_number.get(state) if enum is not None else None
    return value.name if value is not None else f"BLE_STATE_UNKNOWN({state})"


def _device_uuid_text(pkt) -> str:
    scan = pkt.ble_scan_result
    if hasattr(scan, "uuid") and scan.uuid:
        return bytes(scan.uuid).hex().upper()
    return ""


def _drain_stale_packets(session: VvTestSession, verbose: bool) -> None:
    packets = session.recv_packets(timeout_s=0.15)
    if verbose and packets:
        print(f"drained {len(packets)} stale packet(s)")


def _build_device_information_get(session: VvTestSession) -> pb.packet_t:
    factory = CommandFactory()
    return factory.device_information_get(DEFAULT_SRC, DEFAULT_MCU_DST, session.proto.next_seq())


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


def _scan_for_peripheral(
    session: VvTestSession,
    factory: CommandFactory,
    scan_timeout_s: float,
    target_mac: bytes | None,
    name_filter: str | None,
    verbose: bool,
) -> Optional[Tuple[bytes, str]]:
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

    discovered: Dict[bytes, Tuple[str, int, str]] = {}
    deadline = time.time() + scan_timeout_s
    name_filter_lower = name_filter.lower() if name_filter else None

    while time.time() < deadline:
        for pkt in session.recv_packets(timeout_s=0.1):
            msg = pkt.WhichOneof("params")
            if msg == "ble_scan_result":
                scan_result = pkt.ble_scan_result
                mac = bytes(scan_result.mac_address)
                dev_name = scan_result.name or ""
                rssi = scan_result.rssi_dbm
                uuid_text = _device_uuid_text(pkt)

                if mac not in discovered:
                    matched = False
                    if target_mac is not None:
                        matched = (mac == target_mac)
                    else:
                        name_upper = dev_name.upper()
                        uuid_upper = uuid_text.upper()
                        if name_filter_lower:
                            matched = name_filter_lower in dev_name.lower() or name_filter_lower in uuid_upper.lower()
                        else:
                            matched = any(prefix in name_upper for prefix in TARGET_NAME_PREFIXES) or bool(uuid_text)

                    if matched:
                        discovered[mac] = (dev_name, rssi, uuid_text)
                        uuid_part = f" | UUID: {uuid_text}" if uuid_text else ""
                        print(f"  [Scan] Found: {_mac_to_str(mac)} ('{dev_name}'){uuid_part} | RSSI: {rssi} dBm")
            elif msg == "ble_status_resp" and verbose:
                print(f"  [Status] {_ble_state_name(session, pkt.ble_status_resp.state)}")
            elif verbose:
                print(f"  [RX] {msg} src={pkt.hdr.addr.src} dst={pkt.hdr.addr.dst} seq={pkt.hdr.seq}")

    print("[-] Stopping BLE Scan...")
    session.send_packet(factory.ble_scan_stop(DEFAULT_SRC, DEFAULT_CENTRAL_DST, session.proto.next_seq()))
    time.sleep(0.5)

    if not discovered:
        print("\n[FAIL] No target UWB Peripheral device found within scan timeout.")
        return None

    device_list = list(discovered.items())
    if len(device_list) == 1:
        selected_mac, (selected_name, _, _) = device_list[0]
        print(f"\n[+] Automatically selecting the only found device: '{selected_name}' ({_mac_to_str(selected_mac)})")
        return selected_mac, selected_name

    print("\n--- DISCOVERED BLE DEVICES ---")
    for idx, (mac_bytes, (name, rssi, uuid_text)) in enumerate(device_list, 1):
        uuid_part = f" | UUID: {uuid_text}" if uuid_text else ""
        print(f"  [{idx}] {_mac_to_str(mac_bytes)} | Name: '{name}'{uuid_part} | RSSI: {rssi:4d} dBm")
    print("------------------------------")

    while True:
        try:
            choice = input(f"Enter device index (1-{len(device_list)}) or MAC [default first device]: ").strip()
            if not choice:
                selected_mac, (selected_name, _, _) = device_list[0]
                return selected_mac, selected_name

            if choice.isdigit():
                choice_idx = int(choice)
                if 1 <= choice_idx <= len(device_list):
                    selected_mac, (selected_name, _, _) = device_list[choice_idx - 1]
                    return selected_mac, selected_name
                print(f"Invalid index. Please enter a number between 1 and {len(device_list)}.")
                continue

            if ":" in choice:
                mac_input = choice.upper()
                normalized = bytes(reversed([int(x, 16) for x in mac_input.split(":")]))
                if normalized in discovered:
                    selected_name = discovered[normalized][0]
                    return normalized, selected_name
                print("Invalid MAC. Please enter one of the discovered devices.")
                continue

            print("Invalid input. Please enter a device index or MAC address in format AA:BB:CC:DD:EE:FF.")
        except ValueError:
            print("Invalid input. Please enter a device index or MAC address in format AA:BB:CC:DD:EE:FF.")
        except KeyboardInterrupt:
            print("\n[ABORT] User cancelled device selection.")
            return None


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
            for resp in session.recv_packets(timeout_s=0.1):
                msg = resp.WhichOneof("params")
                if msg == "ble_status_resp":
                    state = resp.ble_status_resp.state
                    if verbose:
                        print(f"  [Status] {_ble_state_name(session, state)}")
                    if state == pb.BLE_STATE_CONNECTED:
                        print("[OK] BLE CONNECTION ESTABLISHED!")
                        return True
                    if state == pb.BLE_STATE_IDLE and resp.ble_status_resp.disconnect_reason != 0:
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

    selected_mac, _ = target
    if not _connect_selected_peripheral(session, factory, selected_mac, connect_timeout_s, verbose):
        return False

    session.send_packet(factory.ble_scan_stop(DEFAULT_SRC, DEFAULT_CENTRAL_DST, session.proto.next_seq()))
    time.sleep(0.2)
    return True


def _poll_stop_hotkey() -> bool:
    while msvcrt.kbhit():
        ch = msvcrt.getwch()
        if ch in ("q", "Q"):
            return True
    return False


def _run_stream(session: VvTestSession, seconds: float, verbose: bool, control_ranging: bool) -> bool:
    success = False
    for attempt in range(1, 4):
        pkt = _build_device_information_get(session)
        session.send_packet(pkt)
        if verbose:
            print(f"TX device_information_get seq={pkt.hdr.seq}")

        deadline = time.time() + 2.0
        while time.time() < deadline:
            for resp in session.recv_packets(timeout_s=0.1):
                msg = resp.WhichOneof("params")
                if msg == "device_information_resp":
                    info = resp.device_information_resp
                    print(
                        "MCU device_information_resp "
                        f"serial={info.serial_number} type={info.device_type} role={info.role} hw={info.hw_version}"
                    )
                    success = True
                    break
                if verbose:
                    print(f"RX {msg} src={resp.hdr.addr.src} dst={resp.hdr.addr.dst} seq={resp.hdr.seq}")
            if success:
                break
        if success:
            break
        if attempt < 3:
            print(f"Retrying device_information_get (attempt {attempt + 1}/3)...")
            time.sleep(0.5)

    if not success:
        print("ERROR: Failed to get MCU device information and set ble_connection_active flag.")
        return False

    if control_ranging:
        print("\n-- STEP 3: Start MCU ranging --")
        _send_ranging_cmd(session, start=True, verbose=verbose)
        time.sleep(0.2)

    print("\n-- STEP 4: Listen fusion stream from dongle USB --")
    print("[HOTKEY] Press 'q' to stop stream.")

    pkt_count = 0
    stream_start = time.time()
    last_stat = stream_start
    last_rx_time = None
    deadline = stream_start + seconds

    try:
        while time.time() < deadline:
            if _poll_stop_hotkey():
                print("[HOTKEY] 'q' pressed, sending ranging_stop...")
                if control_ranging:
                    _send_ranging_cmd(session, start=False, verbose=verbose)
                    time.sleep(STOP_SESSION_DELAY_S)
                    control_ranging = False
                break

            now = time.time()
            for pkt in session.recv_packets(timeout_s=STREAM_RECV_TIMEOUT_S):
                name = pkt.WhichOneof("params")
                if name == "sensor_fusion_result":
                    pkt_count += 1
                    dt_s = (now - last_rx_time) if last_rx_time is not None else 0.0
                    last_rx_time = now
                    s = pkt.sensor_fusion_result
                    print(
                        f"[RX] count={pkt_count} dt={dt_s:.3f}s "
                        f"t={s.timestamp_ms} "
                        f"ukf=({s.ukf_x_m:.3f},{s.ukf_y_m:.3f},{s.ukf_yaw_deg:.2f}) "
                        f"tril=({s.tril_x_m:.3f},{s.tril_y_m:.3f}) "
                        f"yaw={s.yaw_deg:.2f} err={s.ranging_error_count}",
                        flush=True,
                    )
                elif verbose:
                    print(f"RX {name} src={pkt.hdr.addr.src} dst={pkt.hdr.addr.dst} seq={pkt.hdr.seq}")

            if now - last_stat >= STREAM_STAT_PERIOD_S:
                elapsed = max(now - stream_start, 0.001)
                hz = pkt_count / elapsed
                print(f"[STAT] received={pkt_count} dt={elapsed:.2f}s rate={hz:.2f}Hz")
                last_stat = now
    except KeyboardInterrupt:
        print("\n[!] User interrupted with Ctrl+C")
    finally:
        if control_ranging:
            _send_ranging_cmd(session, start=False, verbose=verbose)
            time.sleep(STOP_SESSION_DELAY_S)

    elapsed = max(time.time() - stream_start, 0.001)
    print(f"[DONE] received={pkt_count} dt={elapsed:.2f}s rate={(pkt_count / elapsed):.2f}Hz")
    return pkt_count > 0


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

    return _run_stream(session, seconds, verbose, control_ranging)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Listen for MCU sensor_fusion_result packets forwarded by the BLE central dongle over USB CDC."
    )
    parser.add_argument("--port", help="Central dongle COM port, e.g. COM28. If omitted, a USB/J-Link-like port is selected.")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument("--seconds", type=float, default=3600.0, help="Stream duration; q stops earlier.")
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
