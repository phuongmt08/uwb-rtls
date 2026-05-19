#!/usr/bin/env python3
"""Generate fw_image_meta.h only when the content actually changes."""

from __future__ import annotations

import argparse
from pathlib import Path


def render_header(git_sha: str, timestamp: str, image_length: str, image_crc: str) -> str:
    return (
        "/*\n"
        " * AUTO-GENERATED FILE - Do not edit manually\n"
        " */\n\n"
        "#ifndef FW_IMAGE_META_H\n"
        "#define FW_IMAGE_META_H\n"
        f"#define FW_VERSION_GITSHA_HEX 0x{git_sha}ULL\n"
        f"#define FW_VERSION_GITSHA_NOQUOTE {git_sha}\n"
        f"#define FW_IMAGE_TIMESTAMP {timestamp}U\n"
        f"#define FW_IMAGE_LENGTH {image_length}U\n"
        f"#define FW_IMAGE_CRC {image_crc}U\n"
        "#endif\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate fw_image_meta.h")
    parser.add_argument("--output", required=True, help="Output header path")
    parser.add_argument("--git-sha", required=True, help="Short git sha without 0x prefix")
    parser.add_argument("--timestamp", required=True, help="Image timestamp value")
    parser.add_argument("--image-length", required=True, help="Image length value")
    parser.add_argument("--image-crc", required=True, help="Image crc value")
    args = parser.parse_args()

    output = Path(args.output)
    content = render_header(
        git_sha=args.git_sha,
        timestamp=args.timestamp,
        image_length=args.image_length,
        image_crc=args.image_crc,
    )

    if output.exists():
        existing = output.read_text(encoding="utf-8")
        if existing == content:
            print(f"  [meta] {output.name} unchanged (sha={args.git_sha} ts={args.timestamp})")
            return 0

    output.write_text(content, encoding="utf-8")
    print(f"  [meta] {output.name} updated (sha={args.git_sha} ts={args.timestamp})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
