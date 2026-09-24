#!/usr/bin/env python3
"""Build and flash the Tag firmware from the vehicle's Orin.

Three build configurations, taken verbatim from BAN_GIAO_K1_RANGE_DIAG.docx §4:

    A  build              diagnostics off - pre-K1 behaviour, the H0 reference
    B  build_diag_scalar  SYS_RANGING_DIAG_STREAM_ENABLE=1
    C  build_diag         STREAM=1 + CIR_ENABLE=1            <- the K2 target

Each configuration has its own BUILD_DIR, so the three images never overwrite
one another, and both the build and the flash always use the BUILD_DIR *and*
the DEFS of the configuration you selected - building C and then flashing A by
accident is not possible.

Interactive menu:

    1  Build            ->  make -C firmware/uwb BUILD_DIR=... DEFS=...
    2  Flash            ->  put the Tag into DFU mode, then make ... flash
    3  Build + flash
    c  Change configuration (A/B/C)
    q  Quit

The bootloader-entry command is sent with **src = VEHICLE**, so the Tag sees the
request as coming from the vehicle controller rather than from a desktop host.

Every build leaves two files next to the image so a session can be traced back
to exactly what was flashed:

    <build_dir>/build.log         full compiler output, with the warning count
    <build_dir>/build_info.json   config, DEFS, toolchain, git SHA, bin sha256

Flashing reads build_info.json and refuses to write an image whose stamp says it
was built for a different configuration.

Run it with the repository virtualenv - the system python3 on the Orin has
neither pyserial nor protobuf:

    software/.venv/bin/python 'build&flash.py'

Non-interactive use, for scripting (--yes answers the flash confirmation):

    software/.venv/bin/python 'build&flash.py' --config C --build
    software/.venv/bin/python 'build&flash.py' --config C --flash --yes
    software/.venv/bin/python 'build&flash.py' --config C --all --yes

Note the quotes: '&' is a shell metacharacter, so the filename must be quoted or
escaped when running it from bash.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _find_repo_root(start: Path) -> Path:
    """Walk up until the repo layout is recognisable, so this file can be moved."""
    for candidate in (start, *start.parents):
        if (candidate / "firmware" / "uwb" / "Makefile").is_file():
            return candidate
    raise SystemExit(
        f"ERROR: could not locate the uwb-rtls repository above {start}.\n"
        "       Keep this script somewhere inside the repository."
    )


REPO_ROOT = _find_repo_root(HERE)
SOFTWARE_DIR = REPO_ROOT / "software"
FIRMWARE_DIR = REPO_ROOT / "firmware" / "uwb"
VENV_PYTHON = SOFTWARE_DIR / ".venv" / "bin" / "python"

# The base defines are the Makefile's own DEFS value. A configuration that
# overrides DEFS must repeat them, because a command-line assignment replaces
# the whole variable.
BASE_DEFS = "-DDEBUG -DUSE_HAL_DRIVER -DSTM32F411xE"

# The firmware stamps this git SHA into its own version, and device_info.py reads
# it back off the board. The Makefile regenerates the header only when the
# generator script changes (firmware/uwb/Makefile: the rule's only prerequisite),
# so a header left over from an older commit keeps stamping that old SHA into
# every image. `make clean` does not remove it either - it lives in firmware/uwb,
# not in BUILD_DIR. Hence: --clean removes it, and every build checks the image.
FW_IMAGE_META_HEADER = FIRMWARE_DIR / "fw_image_meta.h"

CONFIGS: dict[str, dict[str, str]] = {
    "A": {
        "build_dir": "build",
        "defs": "",  # the Makefile default; nothing to override
        "summary": "diagnostics off (pre-K1 behaviour, H0 reference)",
        "caution": "Config A sends no range_diag: a K2 recording made with it is unusable.",
    },
    "B": {
        "build_dir": "build_diag_scalar",
        "defs": f"{BASE_DEFS} -DSYS_RANGING_DIAG_STREAM_ENABLE=1",
        "summary": "DW1000 diagnostic registers, no CIR",
        "caution": "",
    },
    "C": {
        "build_dir": "build_diag",
        "defs": (
            f"{BASE_DEFS} -DSYS_RANGING_DIAG_STREAM_ENABLE=1"
            " -DSYS_RANGING_DIAG_CIR_ENABLE=1"
        ),
        "summary": "diagnostic registers + CIR window (K2 target)",
        "caution": "",
    },
}

DEFAULT_CONFIG = "A"

# Set from --port; empty means "there must be exactly one board plugged in".
SELECTED_PORT = ""

# The Tag enumerates as an ST virtual COM port while running the application,
# and as an ST DFU device once the bootloader takes over.
VCP_VID, VCP_PID = 0x0483, 0x5740
DFU_VID_PID = "0483:df11"

BOOTLOADER_SETTLE_S = 1.5
DFU_WAIT_TIMEOUT_S = 10.0

sys.path.insert(0, str(SOFTWARE_DIR))


# ---------------------------------------------------------------------------
# configuration helpers
# ---------------------------------------------------------------------------

def build_dir(config: str) -> Path:
    return FIRMWARE_DIR / CONFIGS[config]["build_dir"]


def bin_path(config: str) -> Path:
    return build_dir(config) / "uwb-rtls.bin"


def log_path(config: str) -> Path:
    return build_dir(config) / "build.log"


def stamp_path(config: str) -> Path:
    return build_dir(config) / "build_info.json"


def make_args(config: str) -> list[str]:
    """The make invocation for this configuration - identical for build and flash."""
    cfg = CONFIGS[config]
    args = ["make", "-C", str(FIRMWARE_DIR), f"BUILD_DIR={cfg['build_dir']}"]
    if cfg["defs"]:
        args.append(f"DEFS={cfg['defs']}")
    return args


def describe(config: str) -> str:
    cfg = CONFIGS[config]
    return f"{config} - {cfg['summary']}  [{cfg['build_dir']}]"


# ---------------------------------------------------------------------------
# generic helpers
# ---------------------------------------------------------------------------

def run(cmd: list[str], **kwargs) -> int:
    print(f"\n$ {' '.join(str(c) for c in cmd)}\n")
    return subprocess.run([str(c) for c in cmd], **kwargs).returncode


def run_logged(cmd: list[str], log_file: Path) -> tuple[int, int, int]:
    """Run a command, echoing output to the console and to log_file.

    Returns (exit code, compiler warnings seen, translation units compiled).
    The unit count is what tells an incremental build from a full one: a warning
    count of 0 means nothing at all was recompiled, not a clean build.
    """
    printable = " ".join(str(c) for c in cmd)
    print(f"\n$ {printable}\n")
    log_file.parent.mkdir(parents=True, exist_ok=True)

    warnings = 0
    compiled = 0
    with log_file.open("w") as log:
        log.write(f"$ {printable}\n\n")
        proc = subprocess.Popen(
            [str(c) for c in cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            log.write(line)
            if "warning:" in line:
                warnings += 1
            if "arm-none-eabi-gcc" in line and " -c " in line:
                compiled += 1
        rc = proc.wait()
        log.write(f"\n[exit {rc}]  warnings={warnings}  compiled={compiled}\n")
    return rc, warnings, compiled


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


def flash_runtime_import_error() -> ImportError | None:
    """Return the first missing host dependency needed to identify/flash a board."""
    try:
        import serial  # noqa: F401
        from common.commands import CommandFactory  # noqa: F401
        from common.transport import VvAddress, VvProtocol  # noqa: F401
    except ImportError as exc:
        return exc
    return None


def ensure_flash_runtime() -> bool:
    """Use the repository venv for flash operations, or fail without a traceback."""
    missing = flash_runtime_import_error()
    if missing is None:
        return True

    current_python = Path(sys.executable).absolute()
    venv_python = VENV_PYTHON.absolute()
    if VENV_PYTHON.is_file() and current_python != venv_python:
        print(f"\nMissing Python module {missing.name!r} in {sys.executable}.")
        print(f"Restarting with the repository environment: {VENV_PYTHON}")
        sys.stdout.flush()
        try:
            os.execv(
                str(VENV_PYTHON),
                [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]],
            )
        except OSError as exc:
            print(f"ERROR: could not start {VENV_PYTHON}: {exc}")

    print(f"\nERROR: Python module {missing.name!r} is required to flash the Tag.")
    print(f"       Run the script with: {VENV_PYTHON} {str(Path(__file__).resolve())!r}")
    print("       Or create the venv:   python3 software/install.py --profile orin")
    return False


def confirm(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        print(f"{question} -> yes (--yes)")
        return True
    try:
        return input(f"{question} [y/N]: ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print("\nNo answer - treating as no.")
        return False


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def gcc_path() -> str | None:
    """The Makefile requires GCC_PATH explicitly; it never guesses."""
    value = os.environ.get("GCC_PATH", "").strip()
    return value or None


def require_gcc_path() -> str | None:
    value = gcc_path()
    if value:
        return value
    print("\nERROR: GCC_PATH is not set - the firmware Makefile requires it.")
    print("\n  On this board the Ubuntu toolchain lives in /usr/bin:")
    print("      export GCC_PATH=/usr/bin")
    print("\n  To make it permanent:")
    print("      echo 'export GCC_PATH=/usr/bin' >> ~/.bashrc && source ~/.bashrc")
    return None


def gcc_version(path: str) -> str:
    """Resolve arm-none-eabi-gcc the same way the Makefile does, and ask its version."""
    for candidate in (Path(path) / "arm-none-eabi-gcc", Path(path) / "bin" / "arm-none-eabi-gcc"):
        if candidate.is_file():
            try:
                out = subprocess.run(
                    [str(candidate), "--version"],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                )
                return out.stdout.splitlines()[0].strip()
            except OSError:
                break
    return "unknown"


def git_state() -> tuple[str, bool]:
    """(short SHA, working tree dirty) - recorded so a session can be traced back."""
    try:
        sha = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short=12", "HEAD"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        ).stdout.strip() or "unknown"
        dirty = bool(subprocess.run(
            ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        ).stdout.strip())
    except OSError:
        return "unknown", False
    return sha, dirty


# ---------------------------------------------------------------------------
# build stamp
# ---------------------------------------------------------------------------

def write_stamp(config: str, toolchain: str, warnings: int, compiled: int) -> dict:
    image = bin_path(config)
    sha, dirty = git_state()
    info = {
        "config": config,
        "build_dir": CONFIGS[config]["build_dir"],
        "defs": CONFIGS[config]["defs"] or "(Makefile default)",
        "make": " ".join(make_args(config)),
        "gcc_path": toolchain,
        "gcc_version": gcc_version(toolchain),
        "git_sha": sha,
        "git_dirty": dirty,
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "warnings": warnings,
        "compiled_units": compiled,
        "bin": str(image.relative_to(REPO_ROOT)),
        "bin_size_bytes": image.stat().st_size if image.is_file() else None,
        "bin_sha256": sha256(image) if image.is_file() else None,
        "log": str(log_path(config).relative_to(REPO_ROOT)),
    }
    stamp_path(config).write_text(json.dumps(info, indent=2) + "\n")
    return info


def read_stamp(config: str) -> dict | None:
    path = stamp_path(config)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------
# device helpers
# ---------------------------------------------------------------------------

def list_vcps() -> list[str]:
    """Every board of ours currently enumerated as a virtual COM port."""
    try:
        from serial.tools import list_ports
    except ImportError:
        print("ERROR: pyserial is not installed for this interpreter.")
        print(f"       Run the script with: {VENV_PYTHON}")
        print("       Or create the venv:   python3 software/install.py --profile orin")
        return []
    return [p.device for p in list_ports.comports()
            if p.vid == VCP_VID and p.pid == VCP_PID]


def find_vcp() -> str | None:
    """The one board to talk to, or None if that is not unambiguous.

    Tag and Anchors run the same firmware and enumerate identically, so with two
    boards plugged in there is no way to tell from USB which is which. Guessing
    here would send the bootloader command - and then the image - to whichever
    enumerated first. So: refuse, and let the operator pick with --port.
    """
    if SELECTED_PORT:
        return SELECTED_PORT
    ports = list_vcps()
    if len(ports) > 1:
        print(f"\n{len(ports)} boards are plugged in: {', '.join(ports)}")
        print("Tag and Anchors look identical over USB, so this script will not")
        print("guess which one you meant. Unplug the others, or name one:")
        print("    --port /dev/ttyACM0")
        return None
    return ports[0] if ports else None


def dfu_present() -> bool:
    """True if a DFU device is currently enumerated."""
    if not have("dfu-util"):
        return False
    result = subprocess.run(
        ["dfu-util", "-l"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    return DFU_VID_PID in result.stdout


# ---------------------------------------------------------------------------
# actions
# ---------------------------------------------------------------------------

def do_build(config: str, clean: bool = False) -> bool:
    path = require_gcc_path()
    if path is None:
        return False

    if clean:
        # Scoped to this configuration's BUILD_DIR: the Makefile's clean target
        # removes that directory only.
        print(f"\nCleaning {CONFIGS[config]['build_dir']}/ before building")
        if run(make_args(config) + ["clean"]) != 0:
            print("\nClean FAILED.")
            return False
        if FW_IMAGE_META_HEADER.is_file():
            FW_IMAGE_META_HEADER.unlink()
            print(f"Removed {FW_IMAGE_META_HEADER.relative_to(REPO_ROOT)} "
                  "so the build re-stamps the current git SHA")

    jobs = str(os.cpu_count() or 1)
    print(f"\nBuilding config {describe(config)}")
    print(f"  GCC_PATH={path}  -j{jobs}")
    print(f"  DEFS={CONFIGS[config]['defs'] or '(Makefile default)'}")

    rc, warnings, compiled = run_logged(make_args(config) + [f"-j{jobs}"], log_path(config))
    if rc != 0:
        print(f"\nBuild FAILED (config {config}).  Full output: "
              f"{log_path(config).relative_to(REPO_ROOT)}")
        return False

    check_embedded_sha(config)
    info = write_stamp(config, path, warnings, compiled)
    print(f"\nBuild OK  (config {config}, {warnings} warnings, "
          f"{compiled} files compiled)")
    print(f"  image : {info['bin']}  ({info['bin_size_bytes'] / 1024:.1f} KiB)")
    print(f"  log   : {info['log']}")
    print(f"  stamp : {stamp_path(config).relative_to(REPO_ROOT)}")
    print(f"  git   : {info['git_sha']}{' (dirty)' if info['git_dirty'] else ''}")
    if compiled == 0:
        print("\n  Incremental build: nothing was recompiled, so the warning count "
              "says\n  nothing about this image. Use option 4 (clean rebuild) for a "
              "count you\n  can compare against the pre-merge build - checklist "
              "BAN_GIAO §9.")
    else:
        print("\n  Checklist BAN_GIAO §9: compare this warning count with the "
              "pre-merge\n  build; a clean merge adds none.")
    return True


def check_embedded_sha(config: str) -> bool:
    """Warn if the image does not carry HEAD's git SHA.

    The board reports this SHA in device_information_resp, and that is how a
    recording session is tied to a commit. A stale fw_image_meta.h silently
    stamps an older SHA, which would make two different images indistinguishable
    over the wire.
    """
    image = bin_path(config)
    # --short=16, matching FW_GIT_SHA in firmware/uwb/Makefile; git_state()'s
    # 12-digit SHA is for the human-readable record, not for this byte match.
    try:
        sha = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short=16", "HEAD"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        ).stdout.strip()
    except OSError:
        return True
    if not image.is_file() or len(sha) != 16:
        return True
    try:
        wanted = int(sha, 16).to_bytes(8, "little")
    except ValueError:
        return True
    if image.read_bytes().find(wanted) >= 0:
        print(f"  git SHA {sha} is embedded in the image")
        return True

    print(f"\n  WARNING: the image does NOT carry HEAD's git SHA ({sha}).")
    print(f"  The board would report an older SHA, and {FW_IMAGE_META_HEADER.name} is why.")
    print("  Fix with a clean rebuild (option 4, or --clean), which removes it.")
    return False


def enter_bootloader() -> bool:
    """Ask the Tag to reboot into its DFU bootloader, as the VEHICLE."""
    try:
        from common.commands import CommandFactory
        from common.transport import VvAddress, VvProtocol
        import serial
    except ImportError as exc:
        print(f"\nERROR: cannot enter the bootloader; missing Python module {exc.name!r}.")
        print(f"       Run the script with: {VENV_PYTHON} {str(Path(__file__).resolve())!r}")
        return False

    port = find_vcp()
    if port is None:
        print(f"\nNo Tag virtual COM port found ({VCP_VID:04X}:{VCP_PID:04X}).")
        print("The Tag may already be in DFU mode, or it is not connected.")
        return False

    proto = VvProtocol()
    packet = CommandFactory().enter_to_bootloader(
        int(VvAddress.VEHICLE),   # src: this vehicle controller
        int(VvAddress.MCU),       # dst: the Tag's MCU
        proto.next_seq(),
    )
    frame = proto.wrap_packet(packet)

    print(f"\nSending enter_to_bootloader on {port}")
    print(f"  src = VEHICLE (0x{int(VvAddress.VEHICLE):02X})   "
          f"dst = MCU (0x{int(VvAddress.MCU):02X})   seq = {packet.hdr.seq}")

    try:
        link = serial.Serial(port, 115200, timeout=1.0)
    except (serial.SerialException, OSError) as exc:
        print(f"\nERROR: could not open {port}: {exc}")
        print("Close anything else using the port (RTLS Studio, another script),")
        print("and check you are in the 'dialout' group.")
        return False

    # Once the Tag accepts the command it reboots into the bootloader, so the
    # CDC-ACM port disappears underneath us. Every error from here on is the
    # expected disconnect, not a failure - the DFU wait below is what decides.
    try:
        with link:
            link.write(frame)
            link.flush()
            # The device reboots immediately; an ACK may or may not arrive first.
            for pkt in proto.decode_from_frames(link.read(64)):
                if pkt.WhichOneof("params") == "ack":
                    print("  ACK received.")
                    break
    except (serial.SerialException, OSError):
        print("  Port dropped - the Tag is rebooting.")

    print(f"Waiting for the DFU device to enumerate (up to {DFU_WAIT_TIMEOUT_S:.0f}s)")
    deadline = time.time() + DFU_WAIT_TIMEOUT_S
    while time.time() < deadline:
        if dfu_present():
            print("  DFU device is up.")
            time.sleep(0.3)   # let the interface settle before dfu-util claims it
            return True
        time.sleep(0.3)

    print("\nThe DFU device did not appear.")
    return False


def read_board_identity(port: str) -> dict | None:
    """Who is on the other end - so a flash cannot silently hit the wrong board.

    Reuses device_info.py rather than duplicating the exchange; it sits in this
    same directory, which is on sys.path when this script runs.
    """
    try:
        import serial
        import device_info
        from common.commands import CommandFactory
        from common.transport import VvAddress, VvProtocol
    except ImportError:
        return None

    proto = VvProtocol()
    factory = CommandFactory()
    src, dst = int(VvAddress.VEHICLE), int(VvAddress.MCU)
    try:
        with serial.Serial(port, 115200, timeout=0.2) as link:
            info = device_info.read_device_info(link, proto, factory, src, dst, 2.0)
            if info is None:
                return None
            cfg = device_info.read_sys_config(link, proto, factory, src, dst, 2.0)
            if cfg is not None:
                info["device_id"] = cfg["device_id"]
            return info
    except (serial.SerialException, OSError):
        return None


def report_target_board() -> dict | None:
    """Print the board's identity before a flash, or say why it is unknown."""
    if dfu_present():
        print("\n  target : a board already in DFU mode - it cannot be identified")
        print("           (DFU exposes no identity; make sure it is the one you meant)")
        return None
    port = find_vcp()
    if port is None:
        return None
    info = read_board_identity(port)
    if info is None:
        print(f"\n  target : {port} - did not answer device_information_get")
        print("           (a board on older firmware accepts commands but does not")
        print("            reply over USB; it can still be flashed)")
        return None
    print(f"\n  target : {port}   {info['device_type']} / role {info['role']}"
          f"   device_id {info.get('device_id', '?')}"
          f"   serial {info['serial_number']}")
    print(f"           running fw {info['fw_gitsha']}")
    return info


def check_image(config: str) -> bool:
    """Report exactly what is about to be flashed; refuse a mismatched stamp."""
    image = bin_path(config)
    if not image.is_file():
        print(f"\nNo firmware image at {image.relative_to(REPO_ROOT)}.")
        print(f"Build config {config} first (option 1).")
        return False

    print("\nAbout to flash:")
    print(f"  config : {describe(config)}")
    print(f"  image  : {image.relative_to(REPO_ROOT)}  "
          f"({image.stat().st_size / 1024:.1f} KiB)")

    info = read_stamp(config)
    if info is None:
        print(f"  stamp  : MISSING ({stamp_path(config).name} not found)")
        print("           This image was not built by this script, so its "
              "configuration cannot be verified.")
        return True

    if info.get("config") != config:
        print(f"  stamp  : MISMATCH - the image in {CONFIGS[config]['build_dir']}/ "
              f"was built as config {info.get('config')}, not {config}.")
        print("\nRefusing to flash. Rebuild this configuration (option 1) first.")
        return False

    print(f"  built  : {info.get('built_at')}   "
          f"({info.get('warnings')} warnings, gcc {info.get('gcc_version')})")
    print(f"  git    : {info.get('git_sha')}"
          f"{' (dirty)' if info.get('git_dirty') else ''}")
    print(f"  defs   : {info.get('defs')}")

    if info.get("bin_sha256") and info["bin_sha256"] != sha256(image):
        print("  sha256 : CHANGED since the build - the image on disk is not the "
              "one that was stamped.")
        print("           Rebuild before flashing if you did not replace it on purpose.")
    else:
        print("  sha256 : matches the build stamp")

    return True


def do_flash(config: str, assume_yes: bool = False) -> bool:
    if not have("dfu-util"):
        print("\nERROR: dfu-util not found.")
        print("       sudo apt install dfu-util")
        return False

    if not check_image(config):
        return False

    target = report_target_board()

    caution = CONFIGS[config]["caution"]
    if caution:
        print(f"\n  NOTE: {caution}")

    if not confirm(f"\nFlash config {config} to this board?", assume_yes):
        print("Cancelled - nothing was written.")
        return False

    if dfu_present():
        print("\nA DFU device is already connected - skipping bootloader entry.")
    elif not enter_bootloader():
        print("\nCannot flash: the Tag is not in DFU mode.")
        print("Enter bootloader mode manually, then choose option 2 again.")
        return False

    # Delegate the actual transfer to the Makefile so the DFU VID/PID and the
    # application flash address stay defined in exactly one place. BUILD_DIR and
    # DEFS are passed again here: `flash` depends on the .bin, so if make decides
    # to rebuild it, it must rebuild it with this configuration's flags.
    #
    # patch_fota_header is asked for explicitly: the Makefile's `flash` target
    # depends on the .bin alone, while the FOTA header (length + CRC, 7 bytes in
    # the app header) is written by a separate target that only `all` pulls in.
    # Whenever make regenerates the .bin on its way to flashing, those bytes stay
    # zero and an unpatched image goes onto the board. Naming both targets keeps
    # the patch applied: patch_fota_header builds the .bin first, so flash then
    # finds it up to date and writes the patched bytes.
    path = require_gcc_path()
    if path is None:
        return False

    rc = run(make_args(config) + ["patch_fota_header", "flash"])
    if rc != 0:
        print("\nFlash FAILED.")
        return False

    record_flash(config, target)

    print(f"\nFlash OK - the Tag is running config {config} "
          f"({CONFIGS[config]['summary']}).")
    print("Record the config letter and the git SHA above in the session log.")
    print("Run H0 before any recording session (BAN_GIAO_K1_RANGE_DIAG §12, step 3).")
    return True


def do_enter_dfu() -> bool:
    """Put the Tag into DFU mode and stop there.

    For an image this script must not build - the pre-K1 H0 reference in
    build_prek1/ comes from another commit, so running `make flash` against it
    would rebuild it from the merged sources and destroy it. Enter DFU here,
    then write that image with dfu-util directly; build_prek1/build_info.json
    carries its sha256 to check against.
    """
    if dfu_present():
        print("\nA DFU device is already connected - nothing to do.")
        return True
    if not enter_bootloader():
        print("\nThe Tag is not in DFU mode.")
        return False
    print("\nThe Tag is in DFU mode. Nothing has been written.")
    print("To flash an externally built image (e.g. the pre-K1 H0 reference):")
    print("    dfu-util -d 0483:df11 -a 0 -s 0x0800C000:leave \\")
    print("             -D firmware/uwb/build_prek1/uwb-rtls.bin")
    return True


def record_flash(config: str, target: dict | None) -> None:
    """Append what was actually written, and re-stamp if make relinked the image.

    `make flash` depends on the .bin, so make is free to relink it first - and it
    does whenever the shared fw_image_meta.h is newer than this BUILD_DIR's
    objects. The bytes then differ from the ones write_stamp recorded, even
    though the configuration is the same. Recording the image as it was at the
    moment of the flash is what ties a board to an image afterwards.
    """
    image = bin_path(config)
    if not image.is_file():
        return

    actual = sha256(image)
    info = read_stamp(config)
    if info is not None and info.get("bin_sha256") != actual:
        print("\n  NOTE: the image on disk differs from the build stamp - re-stamping")
        print("        to the bytes that were actually flashed.")
        info["bin_sha256"] = actual
        info["bin_size_bytes"] = image.stat().st_size
        info["relinked_at_flash"] = datetime.now().isoformat(timespec="seconds")
        stamp_path(config).write_text(json.dumps(info, indent=2) + "\n")

    sha, dirty = git_state()
    entry = {
        "flashed_at": datetime.now().isoformat(timespec="seconds"),
        "config": config,
        "build_dir": CONFIGS[config]["build_dir"],
        "git_sha": sha,
        "git_dirty": dirty,
        "bin_sha256": actual,
        "bin_size_bytes": image.stat().st_size,
        "target_before": target,
    }
    log = build_dir(config) / "flash_log.jsonl"
    with log.open("a") as handle:
        handle.write(json.dumps(entry) + "\n")
    print(f"  flash recorded -> {log.relative_to(REPO_ROOT)}")


# ---------------------------------------------------------------------------
# menu
# ---------------------------------------------------------------------------

MENU = """
========================================
  UWB Tag - build & flash from the Orin
========================================
  1) Build firmware
  2) Flash firmware  (auto-enters DFU as VEHICLE)
  3) Build, then flash
  4) Clean rebuild  (true warning count for the §9 checklist)
  5) Enter DFU only (for an externally built image, e.g. pre-K1)
  c) Change configuration (A/B/C)
  q) Quit
"""


def status_block(config: str) -> str:
    lines = [f"  Selected config : {describe(config)}",
             f"  GCC_PATH        : {gcc_path() or 'NOT SET'}"]
    for letter in CONFIGS:
        marker = ">" if letter == config else " "
        image = bin_path(letter)
        if image.is_file():
            age_min = (time.time() - image.stat().st_mtime) / 60
            info = read_stamp(letter)
            state = f"{image.stat().st_size / 1024:6.1f} KiB, built {age_min:5.0f} min ago"
            if info is None:
                state += ", no stamp"
            elif info.get("config") != letter:
                state += f", STAMP SAYS {info.get('config')}"
            else:
                state += f", {info.get('warnings')} warnings"
        else:
            state = "no image"
        lines.append(f"   {marker} {letter}  {CONFIGS[letter]['build_dir']:<18} {state}")

    port = find_vcp()
    if dfu_present():
        tag = "DFU mode"
    elif port:
        tag = f"running ({port})"
    else:
        tag = "not detected"
    lines.append(f"  Tag             : {tag}")
    return "\n".join(lines)


def choose_config(current: str) -> str:
    print("\nConfigurations (BAN_GIAO_K1_RANGE_DIAG.docx §4):")
    for letter, cfg in CONFIGS.items():
        print(f"  {letter}  {cfg['summary']}")
        print(f"     BUILD_DIR={cfg['build_dir']}")
        print(f"     DEFS={cfg['defs'] or '(Makefile default)'}")
    try:
        choice = input(f"\nConfig [{current}]: ").strip().upper()
    except (EOFError, KeyboardInterrupt):
        return current
    if not choice:
        return current
    if choice not in CONFIGS:
        print(f"Unknown config: {choice!r} - keeping {current}.")
        return current
    return choice


def menu_loop(config: str) -> int:
    while True:
        print(MENU)
        print(status_block(config))
        try:
            choice = input("\nSelect: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            return 0

        if choice == "1":
            do_build(config)
        elif choice == "2":
            do_flash(config)
        elif choice == "3":
            if do_build(config):
                do_flash(config)
        elif choice == "4":
            do_build(config, clean=True)
        elif choice == "5":
            do_enter_dfu()
        elif choice == "c":
            config = choose_config(config)
        elif choice in ("q", "quit", "exit"):
            print("Bye.")
            return 0
        else:
            print(f"Unknown option: {choice!r}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build and flash the UWB Tag firmware from the vehicle's Orin.",
    )
    parser.add_argument(
        "--config", choices=sorted(CONFIGS), default=DEFAULT_CONFIG,
        help=f"build configuration (default: {DEFAULT_CONFIG}); "
             "A=diagnostics off, B=registers, C=registers+CIR",
    )
    parser.add_argument("--build", action="store_true", help="build, then exit")
    parser.add_argument(
        "--clean", action="store_true",
        help="remove this configuration's BUILD_DIR first, so the warning count "
             "covers the whole firmware",
    )
    parser.add_argument("--flash", action="store_true", help="flash, then exit")
    parser.add_argument("--all", action="store_true", help="build then flash, then exit")
    parser.add_argument(
        "--enter-dfu", action="store_true",
        help="put the Tag into DFU mode and exit, writing nothing",
    )
    parser.add_argument(
        "--port", default="",
        help="serial port of the board to act on, e.g. /dev/ttyACM0; required "
             "when more than one board is plugged in",
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="answer the flash confirmation with yes (for scripted runs)",
    )
    args = parser.parse_args()

    # Building only needs the toolchain. Every other mode either accesses the
    # serial/DFU device immediately or shows the interactive device status.
    needs_flash_runtime = args.enter_dfu or args.all or args.flash or not args.build
    if needs_flash_runtime and not ensure_flash_runtime():
        return 1

    global SELECTED_PORT
    SELECTED_PORT = args.port

    if args.enter_dfu:
        return 0 if do_enter_dfu() else 1
    if args.all:
        return 0 if (do_build(args.config, args.clean)
                     and do_flash(args.config, args.yes)) else 1
    if args.build:
        return 0 if do_build(args.config, args.clean) else 1
    if args.flash:
        return 0 if do_flash(args.config, args.yes) else 1

    return menu_loop(args.config)


if __name__ == "__main__":
    raise SystemExit(main())
