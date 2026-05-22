#!/usr/bin/env python3
"""Patch FOTA header fields (image_length, image_crc) in HEX/BIN artifacts.

- image_length: highest used app byte - MEM_APP_START + 1, aligned to 4
- image_crc   : STM32 CRC32 over [MEM_APP_START, MEM_APP_START + image_length),
                with image_crc field treated as 0 during computation.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

APP_START = 0x0800C000
APP_END = 0x08040000
HEADER_ADDR = APP_START + 0x200
OFF_IMAGE_LENGTH = 44
OFF_IMAGE_CRC = 48


def stm32_crc32(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for i in range(0, len(data), 4):
        chunk = data[i:i + 4]
        if len(chunk) < 4:
            chunk = chunk + (b"\xFF" * (4 - len(chunk)))
        word = struct.unpack("<I", chunk)[0]
        crc ^= word
        for _ in range(32):
            if crc & 0x80000000:
                crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF
            else:
                crc = (crc << 1) & 0xFFFFFFFF
    return crc


def parse_hex(path: Path) -> dict[int, int]:
    raw: dict[int, int] = {}
    upper_linear = 0
    upper_segment = 0

    for line in path.read_text().splitlines():
        row = line.strip()
        if not row or not row.startswith(":"):
            continue
        rec = bytes.fromhex(row[1:])
        count = rec[0]
        addr = (rec[1] << 8) | rec[2]
        rectype = rec[3]
        payload = rec[4:4 + count]

        if rectype == 0x00:
            base = upper_linear + upper_segment + addr
            for i, b in enumerate(payload):
                raw[base + i] = b
        elif rectype == 0x01:
            break
        elif rectype == 0x02:
            upper_segment = int.from_bytes(payload, "big") << 4
            upper_linear = 0
        elif rectype == 0x04:
            upper_linear = int.from_bytes(payload, "big") << 16
            upper_segment = 0

    return raw


def build_image_bytes(raw: dict[int, int], image_len: int) -> bytearray:
    img = bytearray([0xFF] * image_len)
    for addr, val in raw.items():
        if APP_START <= addr < APP_START + image_len:
            img[addr - APP_START] = val
    return img


def compute_image_len(raw: dict[int, int]) -> int:
    used = [a for a, v in raw.items() if APP_START <= a < APP_END and v != 0xFF]
    if not used:
        raise RuntimeError("No app data in HEX")
    max_addr = max(used)
    image_len = max_addr - APP_START + 1
    return (image_len + 3) & ~3


def patch_raw(raw: dict[int, int], image_len: int, image_crc: int) -> None:
    len_addr = HEADER_ADDR + OFF_IMAGE_LENGTH
    crc_addr = HEADER_ADDR + OFF_IMAGE_CRC
    len_bytes = struct.pack("<I", image_len)
    crc_bytes = struct.pack("<I", image_crc)
    for i in range(4):
        raw[len_addr + i] = len_bytes[i]
        raw[crc_addr + i] = crc_bytes[i]


def patch_raw_length_only(raw: dict[int, int], image_len: int) -> None:
    len_addr = HEADER_ADDR + OFF_IMAGE_LENGTH
    len_bytes = struct.pack("<I", image_len)
    for i in range(4):
        raw[len_addr + i] = len_bytes[i]


def write_hex(raw: dict[int, int], path: Path) -> None:
    def rec(addr16: int, rectype: int, payload: bytes) -> str:
        body = bytes([len(payload), (addr16 >> 8) & 0xFF, addr16 & 0xFF, rectype]) + payload
        checksum = ((-sum(body)) & 0xFF)
        return ":" + body.hex().upper() + f"{checksum:02X}"

    lines: list[str] = []
    current_upper = None
    addrs = sorted(raw.keys())
    i = 0
    while i < len(addrs):
        addr = addrs[i]
        upper = (addr >> 16) & 0xFFFF
        if upper != current_upper:
            current_upper = upper
            lines.append(rec(0, 0x04, current_upper.to_bytes(2, "big")))

        base = addr
        payload = bytearray([raw[base]])
        i += 1
        while i < len(addrs) and len(payload) < 16:
            nxt = addrs[i]
            if ((nxt >> 16) & 0xFFFF) != current_upper or nxt != (base + len(payload)):
                break
            payload.append(raw[nxt])
            i += 1

        lines.append(rec(base & 0xFFFF, 0x00, bytes(payload)))

    lines.append(":00000001FF")
    path.write_text("\n".join(lines) + "\n")


def patch_bin(path: Path, image_len: int, image_crc: int) -> None:
    if not path.exists():
        return
    data = bytearray(path.read_bytes())
    len_off = (HEADER_ADDR - APP_START) + OFF_IMAGE_LENGTH
    crc_off = (HEADER_ADDR - APP_START) + OFF_IMAGE_CRC
    if len(data) < (crc_off + 4):
        return
    data[len_off:len_off + 4] = struct.pack("<I", image_len)
    data[crc_off:crc_off + 4] = struct.pack("<I", image_crc)
    path.write_bytes(data)


def main() -> int:
    parser = argparse.ArgumentParser(description="Patch FOTA header (length+crc) into HEX/BIN")
    parser.add_argument("--hex", required=True, help="Path to output hex")
    parser.add_argument("--bin", required=False, help="Path to output bin")
    args = parser.parse_args()

    hex_path = Path(args.hex)
    raw = parse_hex(hex_path)

    image_len = compute_image_len(raw)

    # Length must be present in the header before CRC is calculated.
    patch_raw_length_only(raw, image_len)

    img = build_image_bytes(raw, image_len)

    crc_off = (HEADER_ADDR - APP_START) + OFF_IMAGE_CRC
    if crc_off + 4 <= len(img):
        img[crc_off:crc_off + 4] = b"\x00\x00\x00\x00"

    image_crc = stm32_crc32(bytes(img))

    patch_raw(raw, image_len, image_crc)
    write_hex(raw, hex_path)

    if args.bin:
        patch_bin(Path(args.bin), image_len, image_crc)

    print(f"[patch_fota_header] len={image_len} crc=0x{image_crc:08X}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
