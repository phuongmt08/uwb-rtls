#!/usr/bin/env python3
"""Read sensor_fusion_result from the Tag and print the position, on the Orin.

Opens the Tag's virtual COM port, starts ranging, then streams the UKF pose out
of every sensor_fusion_result packet the MCU sends.

Packets are addressed with src = VEHICLE, so the Tag sees the vehicle
controller asking, not a desktop host.

Usage
-----
    python3 test_position.py                    # autodetect port, print live
    python3 test_position.py --port /dev/ttyACM0
    python3 test_position.py --csv run1.csv     # also log to CSV
    python3 test_position.py --seconds 30       # stop after 30 s
    python3 test_position.py --no-start         # just listen, do not send ranging_start
    python3 test_position.py --quiet            # CSV only, no console spam
    python3 test_position.py --lpf-hz 1.0       # smooth tril_x/tril_y for display

Needs only the headless dependency set:
    python3 software/install.py --profile orin
"""

from __future__ import annotations

import argparse
import csv
import math
import signal
import statistics
import sys
import time
from pathlib import Path


def _find_software_dir(start: Path) -> Path:
    """Locate software/ so this file keeps working if it is moved."""
    for candidate in (start, *start.parents):
        if (candidate / "common" / "protocol_pb2.py").is_file():
            return candidate
        if (candidate / "software" / "common" / "protocol_pb2.py").is_file():
            return candidate / "software"
    raise SystemExit(
        f"ERROR: could not find software/common/protocol_pb2.py above {start}.\n"
        "       Keep this script inside the repository."
    )


SOFTWARE_DIR = _find_software_dir(Path(__file__).resolve().parent)
sys.path.insert(0, str(SOFTWARE_DIR))

import serial                                              # noqa: E402
from serial.tools import list_ports                        # noqa: E402

from common import protocol_pb2 as pb                      # noqa: E402
from common.commands import CommandFactory                 # noqa: E402
from common.filters import PositionSmoother                # noqa: E402
from common.transport import VvAddress, VvProtocol         # noqa: E402

VCP_VID, VCP_PID = 0x0483, 0x5740

# The MCU sends positions as fixed-point hundredths: 123 means 1.23 m / 1.23 deg.
FIXED_SCALE = 100.0

CSV_COLUMNS = [
    "host_time", "timestamp_ms", "ukf_step",
    "ukf_x_m", "ukf_y_m", "ukf_yaw_deg",
    "tril_x_m", "tril_y_m", "yaw_deg",
    "tril_x_lpf_m", "tril_y_lpf_m",
    "zone_id", "anchor_mask", "n_anchors",
    "ranging_error_count", "prefilter_reject_count",
    "cov_xx_m2", "cov_xy_m2", "cov_yy_m2", "cov_valid",
    "anchors",
]


def find_port() -> str | None:
    for p in list_ports.comports():
        if (p.vid, p.pid) == (VCP_VID, VCP_PID):
            return p.device
    return None


def decode(result) -> dict:
    """Turn a sensor_fusion_result_t into plain SI units."""
    anchors = [
        {"id": a.anchor_id, "distance_mm": a.distance_mm, "weight": a.weight}
        for a in result.anchors
    ]
    return {
        "timestamp_ms": result.timestamp_ms,
        "ukf_step": result.ukf_step,
        "ukf_x_m": result.ukf_x_m / FIXED_SCALE,
        "ukf_y_m": result.ukf_y_m / FIXED_SCALE,
        "ukf_yaw_deg": result.ukf_yaw_deg / FIXED_SCALE,
        "tril_x_m": result.tril_x_m / FIXED_SCALE,
        "tril_y_m": result.tril_y_m / FIXED_SCALE,
        "yaw_deg": result.yaw_deg / FIXED_SCALE,
        "tril_x_lpf_m": result.tril_x_m / FIXED_SCALE,
        "tril_y_lpf_m": result.tril_y_m / FIXED_SCALE,
        "zone_id": result.zone_id,
        "anchor_mask": result.anchor_mask,
        "n_anchors": len(anchors),
        "ranging_error_count": result.ranging_error_count,
        "prefilter_reject_count": result.prefilter_reject_count,
        "cov_xx_m2": result.position_cov_xx_m2,
        "cov_xy_m2": result.position_cov_xy_m2,
        "cov_yy_m2": result.position_cov_yy_m2,
        "cov_valid": bool(result.position_cov_valid),
        "anchors": anchors,
    }


def uncertainty_m(s: dict) -> float | None:
    """1-sigma radius from the position covariance, if the MCU marked it valid."""
    if not s["cov_valid"]:
        return None
    trace = s["cov_xx_m2"] + s["cov_yy_m2"]
    return math.sqrt(trace) if trace > 0 else 0.0


def format_line(s: dict, n: int, lpf: bool = False) -> str:
    sigma = uncertainty_m(s)
    sigma_txt = f"±{sigma:5.2f}m" if sigma is not None else "  --   "
    dists = " ".join(f"A{a['id']}:{a['distance_mm'] / 1000:5.2f}" for a in s["anchors"])
    return (
        f"[{n:5d}] t={s['timestamp_ms'] / 1000:8.2f}s  "
        f"UKF x={s['ukf_x_m']:7.3f} y={s['ukf_y_m']:7.3f} yaw={s['ukf_yaw_deg']:7.2f}°  "
        f"{sigma_txt}  "
        f"tril x={s['tril_x_m']:7.3f} y={s['tril_y_m']:7.3f}  "
        + (f"lpf x={s['tril_x_lpf_m']:7.3f} y={s['tril_y_lpf_m']:7.3f}  " if lpf else "")
        + f"zone={s['zone_id']} anch={s['n_anchors']}  {dists}"
    )


def send(link: serial.Serial, proto: VvProtocol, packet) -> None:
    link.write(proto.wrap_packet(packet))
    link.flush()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stream fused position (sensor_fusion_result) from the Tag.",
    )
    parser.add_argument("--port", help="serial port (default: autodetect 0483:5740)")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--csv", type=Path, help="append samples to this CSV file")
    parser.add_argument("--seconds", type=float, help="stop after this many seconds")
    parser.add_argument("--samples", type=int, help="stop after this many samples")
    parser.add_argument("--yaw", type=float, default=0.0, help="initial yaw for ranging_start")
    parser.add_argument("--reinit", action="store_true", help="ask the UKF to reinitialise")
    parser.add_argument("--no-start", action="store_true",
                        help="do not send ranging_start; only listen")
    parser.add_argument("--no-stop", action="store_true",
                        help="leave ranging running when this script exits")
    parser.add_argument("--quiet", action="store_true", help="do not print each sample")
    parser.add_argument("--lpf-hz", type=float, default=0.0,
                        help="low-pass cutoff for tril_x/tril_y in Hz "
                             "(0 = off; 1.0 is a good starting point, lower = smoother)")
    parser.add_argument("--lpf-median", type=int, default=5,
                        help="median window that blocks outliers before the low-pass "
                             "(1 = off)")
    parser.add_argument("--lpf-jump-m", type=float, default=0.0,
                        help="restart the low-pass when a fix moves more than this "
                             "many metres in one sample (0 = never restart)")
    args = parser.parse_args()

    port = args.port or find_port()
    if port is None:
        print("No Tag serial port found (0483:5740).")
        print("Diagnose with:  python3 check_serial.py")
        return 1

    proto = VvProtocol()
    factory = CommandFactory()
    src, dst = int(VvAddress.VEHICLE), int(VvAddress.MCU)

    writer = None
    csv_file = None
    if args.csv:
        new = not args.csv.exists()
        csv_file = args.csv.open("a", newline="")
        writer = csv.DictWriter(csv_file, fieldnames=CSV_COLUMNS)
        if new:
            writer.writeheader()

    stopping = False

    def on_sigint(_sig, _frm):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, on_sigint)

    lpf = (PositionSmoother(args.lpf_hz, args.lpf_median, args.lpf_jump_m)
           if args.lpf_hz > 0.0 else None)

    count = 0
    started = time.time()
    # Cho mo cong + cho ranging ra nghiem dau tien co the mat vai chuc giay.
    # Gop quang cho do vao tan so thi con so bi keo xuong rat thap va gay hieu
    # nham la duong truyen cham, nen do rieng nhip thuc va quang cho.
    first_host = None            # luc mau dau tien ve toi Orin
    first_ms = last_ms = None    # gio MCU cua mau dau va mau cuoi
    max_gap_ms = 0.0             # khoang lang dai nhat giua hai mau
    gaps_ms: list[float] = []    # khoang cach giua cac mau, de lay trung vi
    # Goi ra o nhip UKF predict, nhanh hon nhieu so voi nhip ranging. Cai quyet
    # dinh chat luong la nhip tril doi gia tri, tuc mot vong ranging thanh cong.
    # Do rieng no, va do luon xem anchor nao chiu tra loi.
    fix_gaps_ms: list[float] = []
    last_tril = None
    last_fix_ms = None
    anchor_hits: dict[int, int] = {}
    rounds = 0                   # so goi co it nhat mot anchor
    err_first = err_last = None
    rej_first = rej_last = None

    try:
        with serial.Serial(port, args.baud, timeout=0.2) as link:
            print(f"Port {port} @ {args.baud}   src=VEHICLE dst=MCU")

            if not args.no_start:
                send(link, proto, factory.ranging_start(
                    src, dst, proto.next_seq(), yaw_deg=args.yaw, is_ukf_reinit=args.reinit))
                print(f"ranging_start sent (yaw={args.yaw}°, reinit={args.reinit})")

            if lpf:
                print(f"tril smoothing on: median {args.lpf_median} -> "
                      f"fc={args.lpf_hz} Hz, jump reset={args.lpf_jump_m or 'off'} m")

            print("Waiting for sensor_fusion_result...  Ctrl-C to stop\n")

            while not stopping:
                if args.seconds and time.time() - started >= args.seconds:
                    break
                if args.samples and count >= args.samples:
                    break

                # read(4096) cho cho du 4096 byte hoac het timeout 0.2 s. Goi
                # sensor_fusion_result chi ~65 byte va ra ~26 Hz, tuc ~1.7 kB/s,
                # nen khong lan nao du 4096 -> lan nao cung cho het 0.2 s roi moi
                # tra ra ca cum ~5 mau. Vi tri vi the nhay theo tung cum 200 ms
                # mot, moi mau bi om lai trung binh ~100 ms.
                # Lay dung so byte dang cho san; chua co byte nao thi read(1) nam
                # doi byte dau tien roi lay tiep phan con lai. Bo giai ma HDLC la
                # may trang thai chay tung byte, giu trang thai qua cac lan goi,
                # nen chia goi nho vo tu. live_server.py doc y het kieu nay.
                n = link.in_waiting
                chunk = link.read(n if n else 1)
                if not chunk:
                    continue

                for packet in proto.decode_from_frames(chunk):
                    if packet.WhichOneof("params") != "sensor_fusion_result":
                        continue

                    sample = decode(packet.sensor_fusion_result)
                    ms = sample["timestamp_ms"]
                    tril = (sample["tril_x_m"], sample["tril_y_m"])
                    if tril != last_tril:
                        if last_fix_ms is not None:
                            fix_gaps_ms.append(ms - last_fix_ms)
                        last_tril, last_fix_ms = tril, ms
                    if sample["anchors"]:
                        rounds += 1
                        for a in sample["anchors"]:
                            anchor_hits[a["id"]] = anchor_hits.get(a["id"], 0) + 1
                    if err_first is None:
                        err_first = sample["ranging_error_count"]
                        rej_first = sample["prefilter_reject_count"]
                    err_last = sample["ranging_error_count"]
                    rej_last = sample["prefilter_reject_count"]
                    if first_ms is None:
                        first_host, first_ms = time.time(), ms
                    else:
                        gaps_ms.append(ms - last_ms)
                        max_gap_ms = max(max_gap_ms, ms - last_ms)
                    last_ms = ms
                    if lpf:
                        sample["tril_x_lpf_m"], sample["tril_y_lpf_m"] = lpf.update_if_new(
                            sample["tril_x_m"], sample["tril_y_m"],
                            sample["timestamp_ms"] / 1000.0)
                    count += 1

                    if not args.quiet:
                        print(format_line(sample, count, lpf is not None))

                    if writer:
                        row = dict(sample)
                        row["host_time"] = f"{time.time():.3f}"
                        row["anchors"] = ";".join(
                            f"{a['id']}:{a['distance_mm']}:{a['weight']}"
                            for a in sample["anchors"]
                        )
                        writer.writerow(row)
                        csv_file.flush()

            if not args.no_start and not args.no_stop:
                send(link, proto, factory.ranging_stop(src, dst, proto.next_seq()))
                print("\nranging_stop sent")

    except serial.SerialException as exc:
        print(f"\nERROR: {exc}")
        print("Is another program holding the port? ModemManager can also grab it.")
        return 1
    finally:
        if csv_file:
            csv_file.close()

    elapsed = time.time() - started
    print(f"\n{count} samples in {elapsed:.1f}s of wall time")
    if count >= 2 and last_ms > first_ms:
        span = (last_ms - first_ms) / 1000.0
        typ = statistics.median(gaps_ms)
        print(f"  typical spacing {typ:.0f} ms = {1000 / typ:.1f} Hz "
              f"(median; this is the real stream rate)")
        print(f"  average {(count - 1) / span:.1f} Hz "
              f"over {span:.1f}s between first and last sample")
        print(f"  waited {first_host - started:.1f}s for the first sample; "
              f"longest gap after that {max_gap_ms / 1000:.1f}s")
        if fix_gaps_ms:
            fg = statistics.median(fix_gaps_ms)
            print(f"  new tril fix every {fg:.0f} ms = {1000 / fg:.1f} Hz "
                  f"({len(fix_gaps_ms) + 1} fixes) <- the rate that matters")
        else:
            print("  no new tril fix at all: trilateration never converged")
        if rounds:
            seen = "  ".join(f"A{i}:{100 * anchor_hits.get(i, 0) / rounds:.0f}%"
                             for i in sorted(set(anchor_hits) | {1, 2, 3, 4}))
            print(f"  anchors answering, over {rounds} rounds:  {seen}")
            missing = [i for i in (1, 2, 3, 4) if not anchor_hits.get(i)]
            if missing:
                print("  never answered: "
                      + ", ".join(f"A{i}" for i in missing)
                      + "  (trilateration needs 3)")
        if err_first is not None:
            print(f"  ranging errors +{err_last - err_first}, "
                  f"prefilter rejects +{rej_last - rej_first} during the run")
    elif count:
        print(f"  ({count / elapsed if elapsed else 0:.1f} Hz counting the wait)")
    if args.csv:
        print(f"CSV: {args.csv}")
    if count == 0:
        print("\nNothing received. Things to check:")
        print("  - the Tag is configured as a TAG, not an ANCHOR")
        print("  - anchors are powered and in range")
        print("  - the Tag's host_transport is USB, not UART")
        print("  - with --no-start, ranging must already be running AND the Tag")
        print("    must have seen a VEHICLE/DEBUG packet since boot; otherwise")
        print("    drop --no-start so ranging_start opens the session")
        print("  - try --reinit if the UKF looks stuck")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
