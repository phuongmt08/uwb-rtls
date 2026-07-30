#!/usr/bin/env python3
"""Build and run the SMF middleware semantics tests on the host."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


TEST_DIR = Path(__file__).resolve().parent
UWB_DIR = TEST_DIR.parents[1]
SMF_DIR = UWB_DIR / "middlewares" / "smf"
SMF_SOURCE = SMF_DIR / "src" / "smf.c"
SMF_INCLUDE = SMF_DIR / "include"
TEST_SOURCE = TEST_DIR / "test_smf.c"


def find_compiler() -> str:
    compiler = shutil.which("clang")
    if compiler:
        return compiler

    windows_clang = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "LLVM" / "bin" / "clang.exe"
    if windows_clang.is_file():
        return str(windows_clang)

    raise RuntimeError("clang was not found")


def build_and_run(compiler: str, output_dir: Path, instrumented: bool) -> None:
    suffix = ".dll" if os.name == "nt" else ".so"
    label = "instrumented" if instrumented else "production"
    output = output_dir / f"smf_test_{label}{suffix}"

    command = [
        compiler,
        "-std=c11",
        "-Wall",
        "-Wextra",
        "-Werror",
        f"-I{SMF_INCLUDE}",
        str(SMF_SOURCE),
        str(TEST_SOURCE),
        "-shared",
        "-o",
        str(output),
    ]

    if instrumented:
        command.append("-DSMF_INSTRUMENTATION=1")

    if os.name == "nt":
        command[1:1] = [
            "--target=x86_64-pc-windows-msvc",
            "-nostdlib",
            "-fuse-ld=lld",
            "-Wl,/noentry",
        ]
    else:
        command.insert(1, "-fPIC")

    subprocess.run(command, check=True)

    library = ctypes.CDLL(str(output))
    self_test = library.smf_self_test
    self_test.restype = ctypes.c_int
    result = self_test()
    if result != 0:
        raise RuntimeError(f"{label} SMF test failed at check {result}")
    print(f"smf {label} tests: PASS")

    if os.name == "nt":
        handle = library._handle
        del self_test
        del library
        free_library = ctypes.windll.kernel32.FreeLibrary
        free_library.argtypes = [ctypes.c_void_p]
        free_library.restype = ctypes.c_int
        if free_library(ctypes.c_void_p(handle)) == 0:
            raise RuntimeError(f"failed to unload {output}")


def main() -> None:
    compiler = find_compiler()
    with tempfile.TemporaryDirectory(prefix="smf_test_") as temp_dir:
        output_dir = Path(temp_dir)
        build_and_run(compiler, output_dir, instrumented=False)
        build_and_run(compiler, output_dir, instrumented=True)


if __name__ == "__main__":
    main()
