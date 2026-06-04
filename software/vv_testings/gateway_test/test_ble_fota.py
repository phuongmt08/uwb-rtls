from __future__ import annotations
"""
test_ble_fota.py  —  Automated FOTA over BLE (in gateway_test)
==================================================================================

Objective:
  Fully automate 100% of the FOTA process over BLE:
    1. Automatically find and parse the latest compiled APP hex file.
    2. Connect to the BLE Central Dongle (COM port).
    3. Automatically Scan and locate the BLE Peripheral (TAG/ANCHOR).
    4. Automatically connect to BLE and wait for the CONNECTED state.
    5. Perform all FOTA stages (enter bootloader, erase, write chunks with retries, verify)
       over BLE to the STM32 MCU.
    6. Gracefully disconnect and report the final result in pristine, professional logs!

Usage:
  python software/vv_testings/gateway_test/test_ble_fota.py
  python software/vv_testings/gateway_test/test_ble_fota.py --port COM28
"""
import sys
import os
import time
import struct
import argparse
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

# Add the parent directories to sys.path
sys.path.append(str(Path(__file__).resolve().parent.parent))
sys.path.append(str(Path(__file__).resolve().parent.parent.parent))

from common import protocol_pb2 as pb
from common.commands import CommandFactory
from vv_test_session import VvTestSession
from common.transport import VvAddress
from test_common import first_param, send_and_print

# ─── Memory layout (must match memorylayout.h) ────────────────────────────────
MEM_APP_START = 0x0800_C000
MEM_APP_END   = 0x0804_0000
MEM_APP_LEN   = MEM_APP_END - MEM_APP_START   # 208 KB

# ─── FOTA tuning ─────────────────────────────────────────────────────────────
CHUNK_SIZE        = 200    # bytes per flash_write packet (must be multiple of 4)
ERASE_TIMEOUT_S   = 10.0   # sector erase over BLE
WRITE_TIMEOUT_S   = 1.0    # per-chunk ACK timeout over BLE
VERIFY_TIMEOUT_S  = 6.0    # CRC verification timeout
ENTER_TIMEOUT_S   = 1.0
POST_ENTER_WAIT_S = 1.0    # wait after enter_to_bootloader ACK for reset+bootloader init

DEFAULT_SRC  = int(VvAddress.HOST)       # 5
DEFAULT_MCU_DST = int(VvAddress.MCU)      # 1
DEFAULT_CENTRAL_DST = int(VvAddress.CENTRAL) # 3
DEFAULT_BAUD = 115200

# ─── Intel HEX parser ────────────────────────────────────────────────────────

class HexParseError(Exception):
    pass


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def _parse_intel_hex(hex_path: str) -> bytes:
    raw: dict[int, int] = {}
    base_addr = 0
    upper_linear = 0

    with open(hex_path, "r") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line.startswith(":"):
                continue
            try:
                rec = bytes.fromhex(line[1:])
            except ValueError as exc:
                raise HexParseError(f"Line {lineno}: {exc}") from exc

            byte_count   = rec[0]
            address      = (rec[1] << 8) | rec[2]
            record_type  = rec[3]
            data         = rec[4 : 4 + byte_count]
            checksum     = rec[4 + byte_count]

            calc_sum = (sum(rec[: 4 + byte_count]) & 0xFF)
            expected = ((~calc_sum + 1) & 0xFF)
            if checksum != expected:
                raise HexParseError(
                    f"Line {lineno}: checksum mismatch "
                    f"(got 0x{checksum:02X}, expected 0x{expected:02X})"
                )

            if record_type == 0x00:   # Data
                abs_addr = upper_linear + base_addr + address
                for i, b in enumerate(data):
                    raw[abs_addr + i] = b

            elif record_type == 0x01: # EOF
                break

            elif record_type == 0x02: # Extended Segment Address
                base_addr = ((data[0] << 8) | data[1]) << 4

            elif record_type == 0x04: # Extended Linear Address
                upper_linear = ((data[0] << 8) | data[1]) << 16

    if not raw:
        raise HexParseError("HEX file contains no data records")

    app_bytes = {addr: val for addr, val in raw.items()
                 if MEM_APP_START <= addr < MEM_APP_END}

    if not app_bytes:
        raise HexParseError(
            f"No data found in app region "
            f"[0x{MEM_APP_START:08X}..0x{MEM_APP_END:08X})"
        )

    max_addr = max(app_bytes.keys())
    image_len = max_addr - MEM_APP_START + 1

    padded_len = (image_len + 3) & ~3
    blob = bytearray(b"\xFF" * padded_len)
    for addr, val in app_bytes.items():
        blob[addr - MEM_APP_START] = val

    return bytes(blob)


def _split_chunks(data: bytes, chunk_size: int) -> List[Tuple[int, bytes]]:
    chunks = []
    for offset in range(0, len(data), chunk_size):
        chunk = data[offset : offset + chunk_size]
        if len(chunk) % 4 != 0:
            chunk = chunk + b"\xFF" * (4 - len(chunk) % 4)
        chunks.append((MEM_APP_START + offset, bytes(chunk)))
    return chunks


# ─── Protocol helpers ────────────────────────────────────────────────────────

def _wait_for_fota_state(
    session: VvTestSession,
    expected_state_value: int,
    timeout_s: float,
    label: str,
) -> Optional[pb.packet_t]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        pkts = session.recv_packets(timeout_s=0.1)
        for pkt in pkts:
            if pkt.WhichOneof("params") == "fota_state_resp":
                state = pkt.fota_state_resp.state
                print(f"  [FOTA STATE] {label} -> state={state}")
                if state == expected_state_value:
                    return pkt
                if state == pb.FOTA_STATE_ERROR:
                    print(f"  [ERROR] Device reported FOTA_STATE_ERROR during {label}")
                    return None
    print(f"  [TIMEOUT] No fota_state_resp({label}) within {timeout_s:.1f}s")
    return None


# ─── FOTA test steps ─────────────────────────────────────────────────────────

def is_bootloader_name(name: str) -> bool:
    import re
    # Match default bootloader names like RTLS-BL-XXXX or UWB-BL-XXXX (where XXXX is exactly 4 hex digits)
    return bool(re.match(r"^(RTLS|UWB)-BL-[0-9A-F]{4}$", name.upper()))


def step_auto_scan_and_connect(session: VvTestSession, factory: CommandFactory,
                               src: int, central_dst: int, scan_timeout_s: float = 12.0,
                               expected_mac: Optional[bytes] = None) -> Optional[Tuple[bytes, bool, str]]:
    print("\n── STEP 0: Auto-Scan & BLE Connect ──────────────────────────────")
    print("[+] Sending BLE Scan Start command...")
    pkt = factory.ble_scan_start(src, central_dst, session.proto.next_seq())
    session.send_packet(pkt)
    
    target_mac = None
    target_name = None
    is_bootloader = False
    
    if expected_mac:
        mac_str_target = ":".join(f"{b:02X}" for b in reversed(expected_mac))
        print(f"[+] Scanning for device with MAC: {mac_str_target}...")
    else:
        print("[+] Scanning for target UWB Peripheral devices...")
        
    deadline = time.time() + scan_timeout_s
    while time.time() < deadline:
        pkts = session.recv_packets(timeout_s=0.1)
        for p in pkts:
            if p.WhichOneof("params") == "ble_scan_result":
                mac_bytes = p.ble_scan_result.mac_address
                mac_str = ":".join(f"{b:02X}" for b in reversed(mac_bytes))
                name = p.ble_scan_result.name
                rssi = p.ble_scan_result.rssi_dbm
                print(f"  [Scan] Found: {mac_str} ('{name}') | RSSI: {rssi} dBm")
                
                if expected_mac:
                    if mac_bytes == expected_mac:
                        target_mac = mac_bytes
                        target_name = name
                        is_bootloader = is_bootloader_name(name)
                        print(f"\n[+] FOUND PREVIOUS DEVICE: '{name}' ({mac_str})")
                        break
                else:
                    name_upper = name.upper()
                    if any(prefix in name_upper for prefix in ["UWB", "TAG", "ANCHOR", "NUS", "RTLS"]):
                        target_mac = mac_bytes
                        target_name = name
                        is_bootloader = is_bootloader_name(name)
                        print(f"\n[+] FOUND TARGET DEVICE: '{name}' ({mac_str})")
                        break
        if target_mac:
            break
            
    if not target_mac:
        print("\n[FAIL] No target UWB Peripheral device found within scan timeout.")
        session.send_packet(factory.ble_scan_stop(src, central_dst, session.proto.next_seq()))
        return None
        
    print("[-] Stopping BLE Scan...")
    session.send_packet(factory.ble_scan_stop(src, central_dst, session.proto.next_seq()))
    time.sleep(0.5)
    
    mac_str = ":".join(f"{b:02X}" for b in reversed(target_mac))
    
    max_conn_attempts = 3
    connected = False
    
    for attempt in range(1, max_conn_attempts + 1):
        print(f"[+] Sending connect command to {mac_str} (attempt {attempt}/{max_conn_attempts})...")
        pkt = factory.ble_connect(src, central_dst, session.proto.next_seq())
        pkt.ble_connect.mac_address = target_mac
        session.send_packet(pkt)
        
        print("[+] Waiting for BLE connection...")
        connect_deadline = time.time() + 15.0
        retry_needed = False
        
        while time.time() < connect_deadline:
            pkts = session.recv_packets(timeout_s=0.1)
            for p in pkts:
                if p.WhichOneof("params") == "ble_status_resp":
                    state = p.ble_status_resp.state
                    if state == pb.BLE_STATE_CONNECTED:
                        print("\033[32m[OK] BLE CONNECTION ESTABLISHED!\033[0m")
                        connected = True
                        break
                    elif state == pb.BLE_STATE_IDLE and p.ble_status_resp.HasField("disconnect_reason"):
                        reason = p.ble_status_resp.disconnect_reason
                        print(f"  [WARNING] Connection attempt {attempt} failed. Reason: 0x{reason:02X}")
                        retry_needed = True
                        break
            if connected or retry_needed:
                break
                
        if connected:
            break
            
        if attempt < max_conn_attempts:
            print(f"  [INFO] Sending disconnect to clean up Central connection state...")
            session.send_packet(factory.ble_disconnect(src, central_dst, session.proto.next_seq()))
            print(f"[+] Waiting 1.5s before retrying connection...")
            time.sleep(1.5)
            
    if not connected:
        print("[FAIL] BLE connection timed out or failed permanently after all retries.")
        return None
        
    time.sleep(1.0)
    return target_mac, is_bootloader, target_name


def step_enter_bootloader(session: VvTestSession, factory: CommandFactory,
                          src: int, dst: int) -> bool:
    print("\n── STEP 1: enter_to_bootloader ──────────────────────────────────")
    seq = session.proto.next_seq()
    pkt = factory.enter_to_bootloader(src, dst, seq)
    packets = send_and_print(session, "enter_to_bootloader", pkt,
                             timeout_s=ENTER_TIMEOUT_S)

    ack_ok = False
    for p in packets:
        if p.WhichOneof("params") == "ack" and p.ack.ack_seq == seq:
            ack_ok = True
            break

    if ack_ok:
        print(f"  [INFO] ACK received. Waiting 0.5s for packet transmission...")
        time.sleep(0.5)
        _ = session.recv_packets(timeout_s=0.2)

    return True


def step_flash_erase(session: VvTestSession, factory: CommandFactory,
                     src: int, dst: int) -> bool:
    print("\n── STEP 2: flash_erase ──────────────────────────────────────────")
    
    print("[+] Flushing stale packets from reception queue...")
    _ = session.recv_packets(timeout_s=0.5)
    
    seq = session.proto.next_seq()
    pkt = factory.flash_erase(src, dst, seq)
    pkt.flash_erase.partition_id = 1
    pkt.flash_erase.flash_addr_region = pb.FLASH_ADDR_REGION_APPLICATION
    packets = send_and_print(session, "flash_erase", pkt,
                             timeout_s=0.5)

    erasing = None
    receiving = None
    for p in packets:
        if p.WhichOneof("params") == "fota_state_resp":
            if p.fota_state_resp.state == pb.FOTA_STATE_ERASING:
                erasing = p
            elif p.fota_state_resp.state == pb.FOTA_STATE_RECEIVING:
                receiving = p

    if erasing:
        print("  [OK] Flash erase operation started...")

    if not receiving:
        receiving = _wait_for_fota_state(
            session, pb.FOTA_STATE_RECEIVING, ERASE_TIMEOUT_S, "RECEIVING"
        )
        
    if not receiving:
        print("  [FAIL] Failed to transition to RECEIVING state after erase.")
        return False
    print("  [OK] Ready for FOTA data transfer.")
    return True


def step_flash_write(session: VvTestSession, factory: CommandFactory,
                     src: int, dst: int, chunks: List[Tuple[int, bytes]]) -> bool:
    print(f"\n── STEP 3: flash_write ({len(chunks)} chunks × {CHUNK_SIZE}B) ──")
    total = len(chunks)
    max_retries = 3
    
    start_time = time.time()
    
    for idx, (addr, data) in enumerate(chunks, 1):
        seq = session.proto.next_seq()
        
        pkt = factory._base(src, dst, seq)
        pkt.flash_write.address = addr
        pkt.flash_write.data = data

        ack_received = False
        
        for attempt in range(1, max_retries + 1):
            if attempt > 1:
                print(f"\n[{_ts()}] [WARNING] Chunk {idx}/{total} (addr 0x{addr:08X}) timed out (attempt {attempt - 1}). Retrying attempt {attempt}/{max_retries}...")
                _ = session.recv_packets(timeout_s=0.05)
                time.sleep(0.1)

            session.send_packet(pkt)
            
            deadline = time.time() + WRITE_TIMEOUT_S
            while time.time() < deadline:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break

                packets = session.recv_packets(timeout_s=min(0.05, remaining), break_on_recv=True)
                for p in packets:
                    if p.WhichOneof("params") == "ack" and p.ack.ack_seq == seq:
                        ack_received = True

                if ack_received:
                    break
            
            if ack_received:
                break
                
        if not ack_received:
            print(f"\n[{_ts()}] [FAIL] No ACK received for chunk {idx}/{total} at address 0x{addr:08X} after {max_retries} attempts.")
            return False

        if idx % 10 == 0 or idx == total:
            pct = idx * 100 // total
            print(f"  Written {idx}/{total} chunks ({pct}%)...", end="\r", flush=True)

    duration = time.time() - start_time
    total_bytes = total * CHUNK_SIZE
    speed_kbps = (total_bytes / 1024.0) / duration if duration > 0 else 0.0
    ms_per_chunk = (duration / total) * 1000.0 if total > 0 else 0.0
    pps = total / duration if duration > 0 else 0.0

    print("\n  \033[32m[OK] All chunks successfully written and acknowledged!\033[0m")
    print("  \033[36m┌─────────────────────────────────────────────────────────────┐\033[0m")
    print("  \033[36m│                 FOTA FLASH WRITE STATISTICS                 │\033[0m")
    print("  \033[36m├──────────────────────────────┬──────────────────────────────┤\033[0m")
    print(f"  \033[36m│\033[0m Total Chunks Transmitted     \033[36m│\033[0m {total:<28} \033[36m│\033[0m")
    print(f"  \033[36m│\033[0m Chunk Payload Size          \033[36m│\033[0m {CHUNK_SIZE:<26} B \033[36m│\033[0m")
    print(f"  \033[36m│\033[0m Total Data Bytes            \033[36m│\033[0m {total_bytes:<26} B \033[36m│\033[0m")
    print("  \033[36m├──────────────────────────────┼──────────────────────────────┤\033[0m")
    print(f"  \033[36m│\033[0m Total Write Duration        \033[36m│\033[0m {duration:<26.2f} s \033[36m│\033[0m")
    print(f"  \033[36m│\033[0m Average Time per Chunk      \033[36m│\033[0m {ms_per_chunk:<26.2f} ms\033[36m│\033[0m")
    print(f"  \033[36m│\033[0m Transmission Speed          \033[36m│\033[0m {speed_kbps:<26.2f} KB/s\033[36m│\033[0m")
    print(f"  \033[36m│\033[0m Packets per Second (PPS)     \033[36m│\033[0m {pps:<26.2f} pps \033[36m│\033[0m")
    print("  \033[36m└──────────────────────────────┴──────────────────────────────┘\033[0m")
    print("  \033[90m* Compare 'Average Time per Chunk' with your BLE Connection Interval.\033[0m")
    print("  \033[90m  e.g., 15.0ms avg chunk time corresponds to a ~7.5ms/15.0ms BLE connection interval.\033[0m\n")
    return True


def step_flash_verify(session: VvTestSession, factory: CommandFactory,
                       src: int, dst: int) -> bool:
    print("\n── STEP 4: flash_verify ─────────────────────────────────────────")
    seq = session.proto.next_seq()
    pkt = factory.flash_verify(src, dst, seq)
    packets = send_and_print(session, "flash_verify", pkt,
                             timeout_s=0.5)

    verifying = False
    finished = False
    
    for p in packets:
        if p.WhichOneof("params") == "fota_state_resp":
            if p.fota_state_resp.state == pb.FOTA_STATE_VERIFYING:
                verifying = True
            elif p.fota_state_resp.state == pb.FOTA_STATE_FINISHED:
                finished = True
        elif p.WhichOneof("params") == "ble_status_resp":
            if p.ble_status_resp.state == pb.BLE_STATE_IDLE:
                print("  [INFO] BLE connection closed. Device has verified and jumped to the application!")
                finished = True

    if verifying and not finished:
        print("  [OK] Verifying software integrity (VERIFYING)...")

    if not finished:
        deadline = time.time() + VERIFY_TIMEOUT_S
        while time.time() < deadline:
            pkts = session.recv_packets(timeout_s=0.1)
            for p in pkts:
                if p.WhichOneof("params") == "fota_state_resp":
                    state = p.fota_state_resp.state
                    print(f"  [FOTA STATE] VERIFY -> state={state}")
                    if state == pb.FOTA_STATE_FINISHED:
                        finished = True
                        break
                    elif state == pb.FOTA_STATE_ERROR:
                        print("  [ERROR] Device reported FOTA_STATE_ERROR.")
                        return False
                elif p.WhichOneof("params") == "ble_status_resp":
                    if p.ble_status_resp.state == pb.BLE_STATE_IDLE:
                        print("  [INFO] BLE disconnected. Device successfully jumped into the application!")
                        finished = True
                        break
            if finished:
                break

    if not finished:
        print("  [FAIL] Software verification failed (No FINISHED state or jump detected).")
        return False

    print("\033[32m  [OK] CRC VERIFICATION SUCCESSFUL! Device jumped successfully into the new application!\033[0m")
    return True


def _find_default_hex() -> Optional[str]:
    script_dir = Path(__file__).resolve().parent
    build_version_dir = (script_dir.parent.parent.parent
                         / "firmware" / "uwb" / "build_version")
    if build_version_dir.is_dir():
        hexes = sorted(build_version_dir.glob("*.hex"), key=lambda p: p.stat().st_mtime,
                       reverse=True)
        if hexes:
            return str(hexes[0])

    candidate_build = (script_dir.parent.parent.parent
                       / "firmware" / "uwb" / "build" / "uwb-rtls.hex")
    if candidate_build.exists():
        return str(candidate_build)

    candidate_debug = (script_dir.parent.parent.parent
                       / "firmware" / "uwb" / "Debug" / "uwb-rtls.hex")
    if candidate_debug.exists():
        return str(candidate_debug)

    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Automated FOTA over BLE tool")
    parser.add_argument("--port", type=str, default=None, help="COM port, example COM28")
    parser.add_argument("--hex", type=str, default=None, help="Path to target hex file")
    args = parser.parse_args()

    # ── Resolve HEX file ──────────────────────────────────────────────────────
    hex_path = args.hex or _find_default_hex()
    if hex_path is None:
        print("\033[31m[ERROR] No target firmware hex file found.\033[0m")
        return 1
    if not os.path.isfile(hex_path):
        print(f"\033[31m[ERROR] Hex file does not exist: {hex_path}\033[0m")
        return 1
    print(f"Firmware Hex: {hex_path}")

    # ── Parse HEX ─────────────────────────────────────────────────────────────
    try:
        firmware_blob = _parse_intel_hex(hex_path)
    except HexParseError as exc:
        print(f"\033[31m[ERROR] HEX parse error: {exc}\033[0m")
        return 1
    print(f"Image Size  : {len(firmware_blob)} bytes (0x{MEM_APP_START:08X}..0x{MEM_APP_START + len(firmware_blob):08X})")

    chunks = _split_chunks(firmware_blob, CHUNK_SIZE)
    print(f"FOTA Chunks : {len(chunks)} chunks × {CHUNK_SIZE} B")

    # ── Resolve Port ──────────────────────────────────────────────────────────
    port = args.port
    if not port:
        print("[!] Probing for Central Dongle COM port automatically...")
        try:
            probe = VvTestSession.auto_probe(src=DEFAULT_SRC, debug=False)
            if probe:
                port = probe.port
                print(f"[+] Found Central Dongle at: {port}")
        except Exception:
            pass

    if not port:
        import serial.tools.list_ports
        ports = serial.tools.list_ports.comports()
        for p in ports:
            if 'USB' in p.description or 'JLink' in p.description or 'Serial' in p.description:
                port = p.device
                print(f"[+] Found matching USB serial port: {port}")
                break

    if not port:
        print("\033[31m[ERROR] No active COM port found on this machine!\033[0m")
        return 1

    factory = CommandFactory()
    all_ok = True
    fota_start_time = time.time()

    # ── Run Automated BLE FOTA steps ──────────────────────────────────────────
    with VvTestSession(port, baud=DEFAULT_BAUD, debug=False) as session:
        # Step 0: Auto Scan & Connect BLE target
        res = step_auto_scan_and_connect(session, factory, DEFAULT_SRC, DEFAULT_CENTRAL_DST)
        if not res:
            print("\033[31m[ABORT] Auto scan & connect failed.\033[0m")
            return 1
        target_mac, is_bootloader, target_name = res
        
        # Step 1: Switch STM32 to Bootloader (if not already in Bootloader)
        if not is_bootloader:
            all_ok &= step_enter_bootloader(session, factory, DEFAULT_SRC, DEFAULT_MCU_DST)
            if all_ok:
                print("\n[+] BLE connection dropped due to device reboot.")
                print("[+] Reconnecting automatically to the Bootloader...")
                # Send disconnect command on Central to clean up status
                session.send_packet(factory.ble_disconnect(DEFAULT_SRC, DEFAULT_CENTRAL_DST, session.proto.next_seq()))
                print(f"[+] Waiting {POST_ENTER_WAIT_S + 1.0:.1f}s for Central and Peripheral to settle...")
                time.sleep(POST_ENTER_WAIT_S + 1.0)
                
                # Scan and reconnect to the same MAC address (now running Bootloader)
                res_reconnect = step_auto_scan_and_connect(session, factory, DEFAULT_SRC, DEFAULT_CENTRAL_DST, expected_mac=target_mac)
                if not res_reconnect:
                    print("\033[31m[ABORT] Failed to reconnect to the Bootloader.\033[0m")
                    return 1
                _, is_bootloader_now, _ = res_reconnect
                if not is_bootloader_now:
                    print("\033[33m[WARNING] Reconnected device name does not contain 'BL'. Continuing...\033[0m")
        else:
            print("\n[+] Device is already in Bootloader mode, skipping bootloader enter step.")
            
        # Step 2: Erase target application flash partition
        if all_ok:
            all_ok &= step_flash_erase(session, factory, DEFAULT_SRC, DEFAULT_MCU_DST)
            
        # Step 3: Stream and write firmware image
        if all_ok:
            all_ok &= step_flash_write(session, factory, DEFAULT_SRC, DEFAULT_MCU_DST, chunks)
            
        # Step 4: Verify software integrity
        if all_ok:
            all_ok &= step_flash_verify(session, factory, DEFAULT_SRC, DEFAULT_MCU_DST)

        # Disconnect BLE to leave target in a clean state
        print("\n[-] Disconnecting BLE and cleaning session...")
        session.send_packet(factory.ble_disconnect(DEFAULT_SRC, DEFAULT_CENTRAL_DST, session.proto.next_seq()))
        time.sleep(0.5)

    # ── Result ────────────────────────────────────────────────────────────────
    fota_duration = time.time() - fota_start_time
    print("\n" + "═" * 58)
    if all_ok:
        print("\033[32;1m      FIRMWARE UPDATE OVER BLE  ✓  SUCCESSFULLY COMPLETED (PASSED)\033[0m")
    else:
        print("\033[31;1m      FIRMWARE UPDATE OVER BLE  ✗  FAILED\033[0m")
    print(f"      Total Duration : {fota_duration:.2f}s")
    print("═" * 58)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
