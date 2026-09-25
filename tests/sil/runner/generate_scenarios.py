#!/usr/bin/env python3
"""
Scenario generator for UWB-RTLS Software-in-the-Loop (SIL) Sandbox.
Generates deterministic test scenarios for:
1. 50Hz Cadence Verification (01_timing_50hz)
2. Sudden Motion Deadlock Recovery (02_sudden_jump)
3. NLOS Outlier Rejection (03_nlos_spike)
"""

import math
import os
import json

# Anchor layout (4 anchors in a 5m x 5m area at height 2.495m)
ANCHORS = [
    {"id": 1, "x": 0.0, "y": 0.0, "z": 2.495},
    {"id": 2, "x": 5.0, "y": 0.0, "z": 2.495},
    {"id": 3, "x": 5.0, "y": 5.0, "z": 2.495},
    {"id": 4, "x": 0.0, "y": 5.0, "z": 2.495},
]
TAG_HEIGHT = 0.585

def calc_range_3d(tag_x, tag_y, anchor):
    dx = anchor["x"] - tag_x
    dy = anchor["y"] - tag_y
    dz = anchor["z"] - TAG_HEIGHT
    return math.sqrt(dx * dx + dy * dy + dz * dz)

def write_scenario(filename_csv, filename_json, init_pos, steps, description):
    # Write JSON metadata
    json_data = {
        "description": description,
        "init_pos": init_pos,
        "anchors": ANCHORS,
        "step_count": len(steps)
    }
    with open(filename_json, "w", encoding="utf-8") as fj:
        json.dump(json_data, fj, indent=2)

    # Write CSV scenario format for direct C harness ingestion
    with open(filename_csv, "w", encoding="utf-8") as f:
        f.write(f"# Description: {description}\n")
        f.write(f"INIT_POS {init_pos[0]:.4f} {init_pos[1]:.4f}\n")
        for a in ANCHORS:
            f.write(f"ANCHOR {a['id']} {a['x']:.4f} {a['y']:.4f} {a['z']:.4f}\n")
        for s in steps:
            ranges_str = " ".join(f"{r:.4f}" for r in s["ranges"])
            f.write(
                f"STEP {s['cycle']} {s['time_ms']} {s['gt_x']:.4f} {s['gt_y']:.4f} "
                f"{s['ax']:.4f} {s['ay']:.4f} {s['gz']:.4f} 0x{s['mask']:02X} {ranges_str}\n"
            )

def generate_all(output_dir):
    os.makedirs(output_dir, exist_ok=True)

    # =========================================================================
    # Scenario 1: 50Hz Cadence Verification (10 seconds, 500 cycles at 20ms)
    # =========================================================================
    steps1 = []
    tag_x, tag_y = 2.5, 2.5
    for cycle in range(500):
        time_ms = cycle * 20
        # True 3D ranges to each anchor
        ranges = [calc_range_3d(tag_x, tag_y, a) for a in ANCHORS]
        steps1.append({
            "cycle": cycle,
            "time_ms": time_ms,
            "gt_x": tag_x,
            "gt_y": tag_y,
            "ax": 0.0,
            "ay": 0.0,
            "gz": 0.0,
            "mask": 0x0F, # Anchors 1, 2, 3, 4 valid
            "ranges": ranges
        })
    write_scenario(
        os.path.join(output_dir, "01_timing_50hz.csv"),
        os.path.join(output_dir, "01_timing_50hz.json"),
        (tag_x, tag_y),
        steps1,
        "50Hz Cadence Verification: Static tag at (2.5, 2.5) for 500 cycles"
    )

    # =========================================================================
    # Scenario 2: Sudden Motion Deadlock Recovery
    # Tag sits at (1.5, 1.5) for 50 cycles, jumps +2.0m to (3.5, 1.5) at cycle 50,
    # and stays at (3.5, 1.5) for 100 cycles.
    # =========================================================================
    steps2 = []
    init_x, init_y = 1.5, 1.5
    for cycle in range(150):
        time_ms = cycle * 20
        if cycle < 50:
            cur_x, cur_y = 1.5, 1.5
            ax = 0.0
        else:
            cur_x, cur_y = 3.5, 1.5
            # One cycle of large acceleration impulse at transition
            ax = 100.0 if cycle == 50 else 0.0

        ranges = [calc_range_3d(cur_x, cur_y, a) for a in ANCHORS]
        steps2.append({
            "cycle": cycle,
            "time_ms": time_ms,
            "gt_x": cur_x,
            "gt_y": cur_y,
            "ax": ax,
            "ay": 0.0,
            "gz": 0.0,
            "mask": 0x0F,
            "ranges": ranges
        })
    write_scenario(
        os.path.join(output_dir, "02_sudden_jump.csv"),
        os.path.join(output_dir, "02_sudden_jump.json"),
        (init_x, init_y),
        steps2,
        "Sudden Motion Deadlock Recovery: Jump from (1.5, 1.5) to (3.5, 1.5) at cycle 50"
    )

    # =========================================================================
    # Scenario 3: NLOS Outlier Rejection
    # Tag moves steadily from x=1.0 to x=4.0 at y=2.0 (200 cycles, 4.0s).
    # Between cycle 60 and cycle 140 (1.6 seconds), Anchor 2 range has +3.0m bias.
    # =========================================================================
    steps3 = []
    start_x, end_x = 1.0, 4.0
    tag_y = 2.0
    total_cycles = 200
    for cycle in range(total_cycles):
        time_ms = cycle * 20
        alpha = cycle / (total_cycles - 1)
        cur_x = start_x + alpha * (end_x - start_x)
        vx = (end_x - start_x) / (total_cycles * 0.02) # ~0.75 m/s

        ranges = [calc_range_3d(cur_x, tag_y, a) for a in ANCHORS]
        # Inject +3.0m NLOS multipath error on Anchor 2 (index 1) between cycles 60..140
        if 60 <= cycle <= 140:
            ranges[1] += 3.0

        steps3.append({
            "cycle": cycle,
            "time_ms": time_ms,
            "gt_x": cur_x,
            "gt_y": tag_y,
            "ax": 0.0,
            "ay": 0.0,
            "gz": 0.0,
            "mask": 0x0F,
            "ranges": ranges
        })
    write_scenario(
        os.path.join(output_dir, "03_nlos_spike.csv"),
        os.path.join(output_dir, "03_nlos_spike.json"),
        (start_x, tag_y),
        steps3,
        "NLOS Outlier Rejection: Tag moves 1.0m -> 4.0m, Anchor 2 injected with +3m error (cycles 60..140)"
    )
    print(f"[SIL] Successfully generated all 3 test scenarios in {output_dir}")

if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "tests/sil/scenarios"
    generate_all(out)
