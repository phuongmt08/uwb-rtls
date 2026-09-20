#!/usr/bin/env python3
"""Generate fw_image_meta.h only when the content actually changes."""

from __future__ import annotations

import argparse
import re
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


def existing_identity(path: Path) -> tuple[str, str] | None:
    """Return the generated SHA/timestamp pair, or None for an unknown file."""
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    sha = re.search(r"^#define FW_VERSION_GITSHA_HEX 0x([0-9A-Fa-f]+)ULL$", text, re.MULTILINE)
    timestamp = re.search(r"^#define FW_IMAGE_TIMESTAMP ([0-9]+)U$", text, re.MULTILINE)
    if not sha or not timestamp:
        return None
    return sha.group(1).lower(), timestamp.group(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate fw_image_meta.h")
    parser.add_argument("--output", required=True, help="Output header path")
    parser.add_argument("--git-sha", required=True, help="Short git sha without 0x prefix")
    parser.add_argument("--timestamp", required=True, help="Image timestamp value")
    parser.add_argument("--image-length", required=True, help="Image length value")
    parser.add_argument("--image-crc", required=True, help="Image crc value")
    parser.add_argument(
        "--preserve-timestamp-if-same-sha",
        action="store_true",
        help="Keep the existing timestamp when the generated header already describes this Git SHA",
    )
    args = parser.parse_args()

    output = Path(args.output)
    git_sha = args.git_sha.lower()
    timestamp = args.timestamp
    identity = existing_identity(output)
    if args.preserve_timestamp_if_same_sha and identity and identity[0] == git_sha:
        timestamp = identity[1]

    content = render_header(
        git_sha=git_sha,
        timestamp=timestamp,
        image_length=args.image_length,
        image_crc=args.image_crc,
    )

    if output.exists():
        existing = output.read_text(encoding="utf-8")
        if existing == content:
            print(f"  [meta] {output.name} unchanged (sha={git_sha} ts={timestamp})")
            return 0

    output.write_text(content, encoding="utf-8")
    print(f"  [meta] {output.name} updated (sha={git_sha} ts={timestamp})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
