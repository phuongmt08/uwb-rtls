#!/usr/bin/env python3
"""Update app/version_build.h for firmware builds.

This is the single source of truth for build-version bumping, used by both
CLI workflows and the programmer app.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Callable


LogCallback = Callable[[str], None]


def _emit(log_callback: LogCallback | None, message: str) -> None:
    if log_callback is not None:
        log_callback(message)


def update_version_file(project_dir: str | Path, auto_inc: bool, log_callback: LogCallback | None = None) -> tuple[bool, str]:
    try:
        root = Path(project_dir)
        version_file = root / "app" / "version.h"
        version_build_file = root / "app" / "version_build.h"

        if not version_file.exists():
            _emit(log_callback, f"[VERSION] ERROR: Missing {version_file}")
            return False, ""

        version_content = version_file.read_text(encoding="utf-8")
        major = int(re.search(r"#define\s+FW_VERSION_MAJOR\s+(\d+)", version_content).group(1))
        minor = int(re.search(r"#define\s+FW_VERSION_MINOR\s+(\d+)", version_content).group(1))
        patch = int(re.search(r"#define\s+FW_VERSION_PATCH\s+(\d+)", version_content).group(1))

        _emit(log_callback, f"[VERSION] Current: {major}.{minor}.{patch}")

        old_major = old_minor = old_patch = old_build = 0
        if version_build_file.exists():
            try:
                build_content = version_build_file.read_text(encoding="utf-8")
                m = re.search(r"#define\s+FW_VERSION_MAJOR_OLD\s+(\d+)", build_content)
                if m:
                    old_major = int(m.group(1))
                m = re.search(r"#define\s+FW_VERSION_MINOR_OLD\s+(\d+)", build_content)
                if m:
                    old_minor = int(m.group(1))
                m = re.search(r"#define\s+FW_VERSION_PATCH_OLD\s+(\d+)", build_content)
                if m:
                    old_patch = int(m.group(1))
                m = re.search(r"#define\s+FW_VERSION_BUILD\s+(\d+)", build_content)
                if m:
                    old_build = int(m.group(1))
            except Exception as exc:
                _emit(log_callback, f"[VERSION] Build file read error: {exc}")

        if f"{major}.{minor}.{patch}" == f"{old_major}.{old_minor}.{old_patch}":
            new_build = (old_build + 1) if auto_inc else old_build
            _emit(log_callback, f"[VERSION] Version unchanged -> INCREMENT to {new_build}")
        else:
            new_build = 0
            _emit(log_callback, "[VERSION] Version changed -> RESET to 0")

        new_content = (
            "/*\n"
            " * version_build.h - AUTO-GENERATED\n"
            " * Do not commit this file to git\n"
            " * Updated by firmware/uwb/tools/update_version.py before each build\n"
            " */\n\n"
            "#ifndef APPLICATION_VERSION_BUILD_H_\n"
            "#define APPLICATION_VERSION_BUILD_H_\n\n"
            f"#define FW_VERSION_MAJOR_OLD {major}\n"
            f"#define FW_VERSION_MINOR_OLD {minor}\n"
            f"#define FW_VERSION_PATCH_OLD {patch}\n"
            f"#define FW_VERSION_BUILD {new_build}\n\n"
            "#endif /* APPLICATION_VERSION_BUILD_H_ */\n"
        )

        if version_build_file.exists() and version_build_file.read_text(encoding="utf-8") == new_content:
            _emit(log_callback, "[VERSION] version_build.h unchanged. Ready for fast incremental build!")
        else:
            version_build_file.write_text(new_content, encoding="utf-8")
            _emit(log_callback, "[VERSION] version_build.h updated.")

        version_str = f"{major}.{minor}.{patch}.{new_build}"
        _emit(log_callback, f"[VERSION] {version_str}")
        return True, version_str
    except Exception as exc:
        _emit(log_callback, f"[VERSION] ERROR: {exc}")
        return False, ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Update firmware build version header")
    parser.add_argument("--project-dir", default=".", help="Path to firmware/uwb project")
    parser.add_argument("--auto-inc", action="store_true", help="Increment build when version triplet is unchanged")
    args = parser.parse_args()

    success, version_str = update_version_file(args.project_dir, args.auto_inc, print)
    if success:
        print(version_str)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
