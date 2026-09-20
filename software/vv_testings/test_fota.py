from __future__ import annotations
"""
test_fota.py  —  FOTA over USB-CDC test: flash APP firmware via STM32 bootloader
==================================================================================

Mục tiêu:
  Kiểm tra toàn bộ luồng FOTA: bootloader chạy trên STM32F411CEU6 nhận
  firmware APP (uwb-rtls.hex) qua USB-CDC, ghi vào vùng application flash,
  xác minh CRC rồi reboot sang app mới.

  !!! QUAN TRỌNG !!!
    Phải chỉ rõ APP firmware bằng --hex và khóa SHA-256 bằng --expect-sha256.
  KHÔNG dùng cơ chế tự chọn file mới nhất. KHÔNG dùng bootloader hex —
  bootloader không thể FOTA chính nó.

Flow:
  1. Mở đúng cổng USB-CDC đã chỉ định; kiểm serial/type/role của bo.
  2. send enter_to_bootloader  →  fota_state_resp(IDLE)
  3. send flash_erase          →  fota_state_resp(ERASING)
                                   fota_state_resp(RECEIVING)
  4. Parse APP .hex → binary chunks (4-byte aligned, 0xFF padding)
  5. Stream flash_write × N    →  ACK per chunk
  6. send flash_verify         →  fota_state_resp(VERIFYING)
                                   fota_state_resp(FINISHED | ERROR)
  7. Report PASS / FAIL

Usage:
  python test_fota.py --port /dev/serial/by-id/<tag> --hex <uwb-rtls.hex> \
    --expect-sha256 <64-hex> --expect-serial 5071420 --expect-role tag --dry-run
  python test_fota.py --port /dev/serial/by-id/<tag> --hex <uwb-rtls.hex> \
    --expect-sha256 <64-hex> --expect-serial 5071420 --expect-role tag --yes

Memory layout (memorylayout.h):
  MEM_APP_START  = 0x0800_C000
  MEM_APP_END    = 0x0804_0000  (208 KB app region)
"""
import argparse
import hashlib
import os
import serial
import struct
import sys
import time
import zlib
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

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
BLOCK_SIZE        = 8      # max host ACK window; frame boundaries may end it earlier
MAX_BLOCK_RETRIES = 5
FOTA_FLAG_COMPRESSED = 0x80000000
FOTA_FLAG_ACK_REQ     = 0x40000000
FOTA_RAW_BLOCK_SIZE   = 4096
FOTA_COMP_BLOCK_MAX   = 4160
FOTA_FRAME_MAGIC      = b"FD"
FOTA_FRAME_VERSION    = 1

ERASE_TIMEOUT_S   = 8.0   # sector erase can take ~1–2 s on F411
WRITE_TIMEOUT_S   = 0.5    # per-window ACK timeout
TX_PACKET_GAP_S   = 0.005  # pace packets within each ACK window
VERIFY_TIMEOUT_S  = 5.0   # CRC calc + vector check
ENTER_TIMEOUT_S   = 0.5
POST_ENTER_WAIT_S = 2   # wait after enter_to_bootloader ACK for reset+bootloader init

DEFAULT_SRC  = int(VvAddress.DEBUG)
DEFAULT_APP_DST = int(VvAddress.MCU)
DEFAULT_BL_DST  = int(VvAddress.MCU)
DEFAULT_BAUD = 115200

assert CHUNK_SIZE % 4 == 0, "CHUNK_SIZE must be 4-byte aligned"

class HexParseError(Exception):
    pass


def _ts() -> str:
    """Human-readable timestamp for transfer logs."""
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def _parse_intel_hex(hex_path: str) -> bytes:
    """
    Parse an Intel HEX file and return a flat binary blob aligned to
    MEM_APP_START.  Only addresses inside [MEM_APP_START, MEM_APP_END)
    are accepted.  Gaps are filled with 0xFF.
    """
    raw: dict[int, int] = {}          # addr → byte value
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

            elif record_type == 0x05: # Start Linear Address (ignored)
                pass

    if not raw:
        raise HexParseError("HEX file contains no data records")

    # Filter to app region only
    app_bytes = {addr: val for addr, val in raw.items()
                 if MEM_APP_START <= addr < MEM_APP_END}

    if not app_bytes:
        raise HexParseError(
            f"No data found in app region "
            f"[0x{MEM_APP_START:08X}..0x{MEM_APP_END:08X})"
        )

    max_addr = max(app_bytes.keys())
    image_len = max_addr - MEM_APP_START + 1

    # Pad to 4-byte boundary with 0xFF
    padded_len = (image_len + 3) & ~3
    blob = bytearray(b"\xFF" * padded_len)
    for addr, val in app_bytes.items():
        blob[addr - MEM_APP_START] = val

    return bytes(blob)


def _compress_fota_blocks(data: bytes) -> Tuple[bytes, List[int]]:
    """Encode independent 4 KB raw-DEFLATE frames for the bootloader."""
    framed = bytearray()
    frame_end_offsets = []

    for offset in range(0, len(data), FOTA_RAW_BLOCK_SIZE):
        raw = data[offset : offset + FOTA_RAW_BLOCK_SIZE]
        compressor = zlib.compressobj(
            level=6,
            method=zlib.DEFLATED,
            wbits=-12,
        )
        compressed = compressor.compress(raw) + compressor.flush()
        if len(compressed) > FOTA_COMP_BLOCK_MAX:
            raise ValueError(
                f"compressed block too large: {len(compressed)} bytes"
            )

        framed.extend(
            struct.pack(
                "<2sBBHH",
                FOTA_FRAME_MAGIC,
                FOTA_FRAME_VERSION,
                0,
                len(raw),
                len(compressed),
            )
        )
        framed.extend(compressed)
        frame_end_offsets.append(len(framed))

    return bytes(framed), frame_end_offsets


def _split_chunks(
    data: bytes,
    chunk_size: int,
    pad_last: bool = True,
) -> List[Tuple[int, bytes]]:
    """Split binary into (flash_address, chunk_bytes) pairs."""
    chunks = []
    for offset in range(0, len(data), chunk_size):
        chunk = data[offset : offset + chunk_size]
        if pad_last and len(chunk) % 4 != 0:
            chunk = chunk + b"\xFF" * (4 - len(chunk) % 4)
        chunks.append((MEM_APP_START + offset, bytes(chunk)))
    return chunks


def _group_chunks_for_ack(
    chunks: List[Tuple[int, bytes]],
    max_window_size: int,
    frame_end_offsets: List[int] = None,
) -> List[List[Tuple[int, int, bytes]]]:
    """End an ACK window at both its size limit and each DEFLATE frame."""
    if max_window_size <= 0:
        raise ValueError("max_window_size must be positive")

    frame_ends = frame_end_offsets or []
    frame_index = 0
    windows = []
    window = []

    for idx, (addr, data) in enumerate(chunks):
        window.append((idx, addr, data))
        chunk_end = (addr - MEM_APP_START) + len(data)
        completes_frame = False

        while frame_index < len(frame_ends) and frame_ends[frame_index] <= chunk_end:
            completes_frame = True
            frame_index += 1

        is_last = idx == len(chunks) - 1
        if len(window) >= max_window_size or completes_frame or is_last:
            windows.append(window)
            window = []

    return windows


# ─── Protocol helpers ────────────────────────────────────────────────────────

def _wait_for_fota_state(
    session: VvTestSession,
    expected_state_value: int,
    timeout_s: float,
    label: str,
) -> Optional[pb.packet_t]:
    """Poll until a fota_state_resp with the expected state arrives."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        pkts = session.recv_packets(timeout_s=0.1)
        for pkt in pkts:
            if pkt.WhichOneof("params") == "fota_state_resp":
                state = pkt.fota_state_resp.state
                print(f"  [FOTA STATE] {label} → state={state}")
                if state == expected_state_value:
                    return pkt
                if state == pb.FOTA_STATE_ERROR:
                    print(f"  [ERROR] Device reported FOTA_STATE_ERROR during {label}")
                    return None
    print(f"  [TIMEOUT] No fota_state_resp({label}) within {timeout_s:.1f}s")
    return None


def step_enter_bootloader(session: VvTestSession, factory: CommandFactory,
                          src: int, dst: int) -> bool:
    print("\n── STEP 1: enter_to_bootloader ──────────────────────────────────")
    seq = session.proto.next_seq()
    pkt = factory.enter_to_bootloader(src, dst, seq)
    packets = send_and_print(
        session,
        "enter_to_bootloader",
        pkt,
        timeout_s=ENTER_TIMEOUT_S,
    )

    ack_ok = any(
        p.WhichOneof("params") == "ack" and p.ack.ack_seq == seq
        for p in packets
    )
    resp = first_param(packets, "fota_state_resp")

    if ack_ok:
        print(
            "  [INFO] enter_to_bootloader ACK received, wait "
            f"{POST_ENTER_WAIT_S:.1f}s for reboot..."
        )
        time.sleep(POST_ENTER_WAIT_S)
        _ = session.recv_packets(timeout_s=0.2)

    if resp is None:
        print(
            "  [WARN] No fota_state_resp received — "
            "assuming device is in bootloader"
        )
        return True

    print(f"  fota_state = {resp.fota_state_resp.state}")
    return True


def step_flash_erase(session: VvTestSession, factory: CommandFactory,
                     src: int, dst: int) -> bool:
    print("\n── STEP 2: flash_erase ──────────────────────────────────────────")
    seq = session.proto.next_seq()
    pkt = factory.flash_erase(src, dst, seq)
    pkt.flash_erase.partition_id = 1
    pkt.flash_erase.flash_addr_region = pb.FLASH_ADDR_REGION_APPLICATION
    packets = send_and_print(session, "flash_erase", pkt,
                             timeout_s=0.5)

    # Expect ERASING state, then RECEIVING (both arrive quickly if erase is fast,
    # or RECEIVING arrives after erase completes)
    erasing = None
    receiving = None
    for p in packets:
        if p.WhichOneof("params") == "fota_state_resp":
            if p.fota_state_resp.state == pb.FOTA_STATE_ERASING:
                erasing = p
            elif p.fota_state_resp.state == pb.FOTA_STATE_RECEIVING:
                receiving = p

    if erasing:
        print("  [OK] ERASING state received")

    # Wait for RECEIVING state (erase may take a few seconds)
    if not receiving:
        receiving = _wait_for_fota_state(
            session, pb.FOTA_STATE_RECEIVING, ERASE_TIMEOUT_S, "RECEIVING"
        )
        
    if not receiving:
        print("  [FAIL] Never reached RECEIVING state after erase")
        return False
    print("  [OK] RECEIVING state — ready for flash_write chunks")
    return True


def step_flash_write(session: VvTestSession, factory: CommandFactory,
                     src: int, dst: int, firmware_blob: bytes,
                     compress: bool = True, block_size: int = BLOCK_SIZE,
                     chunk_size: int = CHUNK_SIZE) -> bool:
    if compress:
        compressed_data, frame_end_offsets = _compress_fota_blocks(firmware_blob)
        frame_count = len(frame_end_offsets)
        ratio = len(compressed_data) / len(firmware_blob) * 100.0
        print(
            "\n── STEP 3: flash_write "
            f"(Block DEFLATE: {len(firmware_blob)}B -> "
            f"{len(compressed_data)}B in {frame_count} frames "
            f"[{ratio:.1f}%], {100.0 - ratio:.1f}% saved) ──"
        )
        raw_chunks = _split_chunks(
            compressed_data,
            chunk_size,
            pad_last=False,
        )
    else:
        print(f"\n── STEP 3: flash_write (Uncompressed: {len(firmware_blob)}B) ──")
        raw_chunks = _split_chunks(firmware_blob, chunk_size)
        frame_end_offsets = []

    total_chunks = len(raw_chunks)
    raw_windows = _group_chunks_for_ack(
        raw_chunks,
        block_size,
        frame_end_offsets,
    )
    sync_mode = ", DEFLATE-frame sync" if compress else ""
    print(
        f"Total chunks: {total_chunks} × {chunk_size}B "
        f"(ACK window<={block_size}{sync_mode})"
    )

    packet_windows = []
    for raw_window in raw_windows:
        packet_window = []
        for position, (idx, addr, data) in enumerate(raw_window):
            req_ack = position == len(raw_window) - 1

            flags = 0
            if compress:
                flags |= FOTA_FLAG_COMPRESSED
            if req_ack:
                flags |= FOTA_FLAG_ACK_REQ

            packet_addr = flags | (addr & 0x0FFFFFFF)

            seq = session.proto.next_seq()
            pkt = factory._base(src, dst, seq)
            pkt.flash_write.address = packet_addr
            pkt.flash_write.data = data
            packet_window.append((idx, seq, req_ack, pkt))
        packet_windows.append(packet_window)

    i = 0
    t_start = time.time()
    for block in packet_windows:
        last_in_block = block[-1]
        block_retry_count = 0

        while block_retry_count < MAX_BLOCK_RETRIES:
            # Send all packets in current block continuously. The bootloader
            # uses the compressed input address to skip already committed data.
            transport_error = None
            for idx, seq, req_ack, pkt in block:
                try:
                    session.send_packet(pkt)
                except (
                    serial.SerialTimeoutException,
                    serial.SerialException,
                ) as exc:
                    transport_error = exc
                    break
                time.sleep(TX_PACKET_GAP_S)

            ack_received = False
            if transport_error is None:
                deadline = time.time() + WRITE_TIMEOUT_S
                while time.time() < deadline:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        break
                    packets = session.recv_packets(
                        timeout_s=min(0.05, remaining)
                    )
                    for p in packets:
                        if p.WhichOneof("params") == "ack" and p.ack.ack_seq == last_in_block[1]:
                            if p.ack.response == pb.PACKET_ACK_RESPONSE_ACK:
                                ack_received = True
                            else:
                                print(f"[{_ts()}] [NACK] Received NACK for seq {last_in_block[1]}")
                                deadline = 0
                                break

                    if ack_received:
                        break
            else:
                print(
                    f"[{_ts()}] [TX BACKPRESSURE] chunk "
                    f"{idx + 1}/{total_chunks}: {transport_error}"
                )
                if not isinstance(
                    transport_error,
                    serial.SerialTimeoutException,
                ):
                    print("[FAIL] Dongle COM handle is no longer valid")
                    return False
                time.sleep(0.1)

            if ack_received:
                break

            block_retry_count += 1
            print(f"[{_ts()}] [RETRY] No ACK for block ending at chunk "
                  f"{last_in_block[0]+1}/{total_chunks} "
                  f"({block_retry_count}/{MAX_BLOCK_RETRIES})")

        if not ack_received:
            print(f"[{_ts()}] [FAIL] Block ending at chunk "
                  f"{last_in_block[0]+1}/{total_chunks} failed after "
                  f"{MAX_BLOCK_RETRIES} attempts")
            return False

        i += len(block)
        pct = (i * 100) // total_chunks
        print(f"  Written {i}/{total_chunks} chunks ({pct}%)...")

    t_elapsed = max(0.001, time.time() - t_start)
    print(f"  [OK] All chunks written in {t_elapsed:.2f}s! ({len(firmware_blob)/t_elapsed/1024.0:.1f} KB/s effective throughput)")
    return True


def step_flash_verify(session: VvTestSession, factory: CommandFactory,
                      src: int, dst: int) -> bool:
    print("\n── STEP 4: flash_verify ─────────────────────────────────────────")
    seq = session.proto.next_seq()
    pkt = factory.flash_verify(src, dst, seq)
    packets = send_and_print(session, "flash_verify", pkt,
                             timeout_s=0.5)

    # Expect VERIFYING → FINISHED (or ERROR)
    verifying = None
    finished = None
    for p in packets:
        if p.WhichOneof("params") == "fota_state_resp":
            if p.fota_state_resp.state == pb.FOTA_STATE_VERIFYING:
                verifying = p
            elif p.fota_state_resp.state == pb.FOTA_STATE_FINISHED:
                finished = p

    if verifying:
        print("  [OK] VERIFYING state received")

    if not finished:
        finished = _wait_for_fota_state(
            session, pb.FOTA_STATE_FINISHED, VERIFY_TIMEOUT_S, "FINISHED"
        )
        
    if not finished:
        print("  [FAIL] Image verification failed (no FINISHED state)")
        return False

    print("  [OK] FOTA_STATE_FINISHED — image verified successfully!")
    return True


# ─── Main ────────────────────────────────────────────────────────────────────

def _parse_int_auto(value: str) -> int:
    return int(value, 0)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_target_identity(info, expected_serial: int, expected_role: str) -> bool:
    role_map = {
        "tag": pb.DEVICE_ROLE_TAG,
        "anchor": pb.DEVICE_ROLE_ANCHOR,
    }
    type_map = {
        "tag": pb.DEVICE_TYPE_TAG,
        "anchor": pb.DEVICE_TYPE_ANCHOR,
    }
    actual_serial = int(info.serial_number)
    actual_role = int(info.role)
    actual_type = int(info.device_type)
    print(
        "Target    : "
        f"serial={actual_serial} device_type={actual_type} role={actual_role}"
    )
    if actual_serial != expected_serial:
        print(f"ERROR: target serial mismatch: expected {expected_serial}, got {actual_serial}")
        return False
    if actual_role != role_map[expected_role] or actual_type != type_map[expected_role]:
        print(
            f"ERROR: target identity mismatch: expected type/role={expected_role}, "
            f"got device_type={actual_type} role={actual_role}"
        )
        return False
    return True


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="FOTA over an explicitly selected USB-CDC port and verified APP HEX"
    )
    parser.add_argument("--port", required=True, help="Exact COM port or /dev/serial/by-id path")
    parser.add_argument("--hex", required=True, help="Exact APP firmware HEX path")
    parser.add_argument("--expect-sha256", required=True, help="Expected SHA-256 of the HEX file")
    parser.add_argument("--expect-serial", required=True, type=_parse_int_auto,
                        help="Expected board serial number (decimal or 0x...)")
    parser.add_argument("--expect-role", required=True, choices=("tag", "anchor"),
                        help="Expected device type and runtime role")
    parser.add_argument("--chunk-size", type=int, default=CHUNK_SIZE,
                        help=f"Flash payload bytes per chunk (4..{CHUNK_SIZE}, multiple of 4)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate arguments, SHA-256 and HEX without opening the serial port")
    parser.add_argument("--yes", action="store_true",
                        help="Required acknowledgement for erase/write/verify")
    args = parser.parse_args(argv)

    if args.chunk_size < 4 or args.chunk_size > CHUNK_SIZE or args.chunk_size % 4:
        parser.error(f"--chunk-size must be a multiple of 4 in range 4..{CHUNK_SIZE}")

    # ── Resolve HEX file ──────────────────────────────────────────────────────
    hex_file = Path(args.hex).expanduser().resolve()
    if not hex_file.is_file():
        print(f"ERROR: HEX file not found: {hex_file}")
        return 1
    hex_path = str(hex_file)
    expected_sha256 = args.expect_sha256.strip().lower()
    if len(expected_sha256) != 64 or any(c not in "0123456789abcdef" for c in expected_sha256):
        print("ERROR: --expect-sha256 must contain exactly 64 hexadecimal characters")
        return 1
    actual_sha256 = _sha256_file(hex_file)
    print(f"HEX file : {hex_file}  (APP firmware)")
    print(f"SHA-256  : {actual_sha256}")
    if actual_sha256 != expected_sha256:
        print(f"ERROR: HEX SHA-256 mismatch: expected {expected_sha256}")
        return 1
    if "bootloader" in hex_file.name.lower():
        print("ERROR: refusing a file whose name contains 'bootloader'")
        return 1

    # ── Parse HEX ─────────────────────────────────────────────────────────────
    try:
        firmware_blob = _parse_intel_hex(hex_path)
    except HexParseError as exc:
        print(f"ERROR parsing HEX: {exc}")
        return 1
    print(f"Image size: {len(firmware_blob)} bytes "
          f"(0x{MEM_APP_START:08X}..0x{MEM_APP_START + len(firmware_blob):08X})")

    chunks = _split_chunks(firmware_blob, args.chunk_size)
    print(f"Chunks    : {len(chunks)} x {args.chunk_size} B")

    if args.dry_run:
        print("DRY RUN PASSED: artifact verified; serial port was not opened")
        return 0
    if not args.yes:
        print("ERROR: refusing to erase/flash without explicit --yes (run --dry-run first)")
        return 1

    # ── Connect only to the explicitly selected target ───────────────────────
    port = args.port
    baud = DEFAULT_BAUD
    src = DEFAULT_SRC
    app_dst = DEFAULT_APP_DST
    bl_dst = DEFAULT_BL_DST
    print(f"Port      : {port} @ {baud}")
    print(f"Address   : src={src} app_dst={app_dst} bl_dst={bl_dst}")
    factory = CommandFactory()

    # ── Run FOTA steps ────────────────────────────────────────────────────────
    all_ok = True
    try:
        with VvTestSession(port, baud=baud, debug=True) as session:
            info_pkt = factory.device_information_get(src, app_dst, session.proto.next_seq())
            info, _packets = session.send_expect_param(
                info_pkt, "device_information_resp", timeout_s=0.8
            )
            if info is None:
                print("ERROR: device_information_get timed out; refusing to enter bootloader")
                return 1
            if not _verify_target_identity(info, args.expect_serial, args.expect_role):
                print("ERROR: target verification failed; refusing to enter bootloader")
                return 1

            all_ok &= step_enter_bootloader(session, factory, src, app_dst)
            if not all_ok:
                print("[ABORT] enter_to_bootloader failed")
            else:
                all_ok &= step_flash_erase(session, factory, src, bl_dst)

            if all_ok:
                all_ok &= step_flash_write(
                    session, factory, src, bl_dst, firmware_blob,
                    compress=True, chunk_size=args.chunk_size,
                )

            if all_ok:
                all_ok &= step_flash_verify(session, factory, src, bl_dst)
    except (serial.SerialException, OSError) as exc:
        print(f"ERROR: cannot use serial port {port}: {type(exc).__name__}: {exc}")
        return 1

    # ── Result ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 58)
    if all_ok:
        print("FOTA TEST PASSED")
    else:
        print("FOTA TEST FAILED")
    print("=" * 58)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
