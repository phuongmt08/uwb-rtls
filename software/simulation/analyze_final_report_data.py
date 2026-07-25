#!/usr/bin/env python3
"""Build one aggregate positioning report from every CSV in final_report_data.

The script uses only Python's standard library. Plotly is loaded by the generated
HTML report from the same CDN already used by the simulation reports.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import importlib.util
import json
import math
import statistics
import webbrowser
from pathlib import Path
from typing import Iterable, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT_DIR = (SCRIPT_DIR.parent / "data" / "final_report_data").resolve()
DEFAULT_OUTPUT_NAME = "final_report_analysis.html"
DETOUR_SOURCE_CSV = "uwb_data_20260612_192531.csv"
DETOUR_TARGET_CSVS = (
    "uwb_data_20260612_192000.csv",
    "uwb_data_20260612_192531.csv",
)
DETOUR_START_INDEX = 2260
DETOUR_END_INDEX = 2600
METRIC_KEYS = ("mae", "rmse", "p95", "max")


def finite_float(value: object, default: float | None = None) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def finite_int(value: object, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def percentile(sorted_values: Sequence[float], percentile_value: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * percentile_value / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def error_metrics(values: Iterable[float | None]) -> dict[str, float | int | None]:
    valid = sorted(value for value in values if value is not None and math.isfinite(value))
    if not valid:
        return {
            "count": 0,
            "mae": None,
            "rmse": None,
            "p50": None,
            "p90": None,
            "p95": None,
            "max": None,
        }
    return {
        "count": len(valid),
        "mae": sum(valid) / len(valid),
        "rmse": math.sqrt(sum(value * value for value in valid) / len(valid)),
        "p50": percentile(valid, 50),
        "p90": percentile(valid, 90),
        "p95": percentile(valid, 95),
        "max": valid[-1],
    }


def reduction_percent(baseline: float | None, candidate: float | None) -> float | None:
    if baseline is None or candidate is None or not math.isfinite(baseline) or baseline <= 0:
        return None
    return 100.0 * (baseline - candidate) / baseline


def load_simulation_module():
    module_path = SCRIPT_DIR / "uwb-rtls_simulation.py"
    spec = importlib.util.spec_from_file_location("uwb_rtls_simulation", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load simulation helpers from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_ground_truth() -> tuple[str, list[list[float]], list[float | None], list[float | None]]:
    simulation = load_simulation_module()
    track = simulation.parse_graphml_groundtruth(str(SCRIPT_DIR / "custom_track_modified.xml"))
    track = simulation.apply_groundtruth_params(track)
    if not track or not track.get("segments"):
        raise RuntimeError("Custom ground truth could not be loaded")
    segments = [[float(value) for value in segment[:4]] for segment in track["segments"]]
    return track.get("name", "Custom Track"), segments, track.get("x", []), track.get("y", [])


def load_csv(path: Path) -> dict:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"tx_frame_cnt", "anchor_mask", "ukf_x", "ukf_y", "tril_x", "tril_y"}
        fields = {field.lower() for field in (reader.fieldnames or [])}
        missing = required - fields
        if missing:
            raise ValueError(f"{path.name}: missing columns {', '.join(sorted(missing))}")
        for row_index, raw in enumerate(reader):
            row = {str(key).lower(): value for key, value in raw.items() if key is not None}
            ukf_x = finite_float(row.get("ukf_x"))
            ukf_y = finite_float(row.get("ukf_y"))
            tril_x = finite_float(row.get("tril_x"))
            tril_y = finite_float(row.get("tril_y"))
            if None in (ukf_x, ukf_y, tril_x, tril_y):
                continue
            rows.append(
                {
                    "index": row_index,
                    "frame": finite_int(row.get("tx_frame_cnt"), row_index),
                    "mask": finite_int(row.get("anchor_mask"), 0),
                    "ukf_x": ukf_x,
                    "ukf_y": ukf_y,
                    "tril_x": tril_x,
                    "tril_y": tril_y,
                    "error_frames": finite_int(row.get("error_frame_cnt"), 0),
                }
            )
    if not rows:
        raise ValueError(f"{path.name}: no valid positioning rows")
    return {"path": path, "rows": rows}


def fitted_detour_segment(rows: Sequence[dict]) -> list[float] | None:
    points = [
        (row["ukf_x"], row["ukf_y"])
        for row in rows
        if DETOUR_START_INDEX <= row["index"] <= DETOUR_END_INDEX
    ]
    if len(points) < 2:
        return None
    mean_x = statistics.fmean(point[0] for point in points)
    mean_y = statistics.fmean(point[1] for point in points)
    sxx = sum((x - mean_x) ** 2 for x, _ in points)
    syy = sum((y - mean_y) ** 2 for _, y in points)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in points)
    theta = 0.5 * math.atan2(2.0 * sxy, sxx - syy)
    ux, uy = math.cos(theta), math.sin(theta)
    projections = [(x - mean_x) * ux + (y - mean_y) * uy for x, y in points]
    start = (mean_x + min(projections) * ux, mean_y + min(projections) * uy)
    end = (mean_x + max(projections) * ux, mean_y + max(projections) * uy)
    if math.dist(start, points[0]) > math.dist(end, points[0]):
        start, end = end, start
    return [start[0], start[1], end[0], end[1]]


def segment_profiles(segments: Sequence[Sequence[float]], curve_angle_deg: float) -> list[dict]:
    epsilon = 1.0e-6
    angle_threshold = max(0.0, curve_angle_deg) * math.pi / 180.0

    def same_point(ax: float, ay: float, bx: float, by: float) -> bool:
        return math.hypot(ax - bx, ay - by) <= epsilon

    profiles = []
    for x1, y1, x2, y2 in segments:
        profiles.append({"length": math.hypot(x2 - x1, y2 - y1), "start_curve": False, "end_curve": False})

    def endpoint_is_curve(index: int, at_start: bool) -> bool:
        x1, y1, x2, y2 = segments[index]
        ex, ey = (x1, y1) if at_start else (x2, y2)
        vx, vy = ((x2 - ex), (y2 - ey)) if at_start else ((x1 - ex), (y1 - ey))
        v_len = math.hypot(vx, vy)
        if v_len <= epsilon:
            return False
        for other_index, other in enumerate(segments):
            if other_index == index:
                continue
            ox1, oy1, ox2, oy2 = other
            if same_point(ex, ey, ox1, oy1):
                ox, oy = ox2 - ex, oy2 - ey
            elif same_point(ex, ey, ox2, oy2):
                ox, oy = ox1 - ex, oy1 - ey
            else:
                continue
            other_len = math.hypot(ox, oy)
            if other_len <= epsilon:
                continue
            alignment = min(1.0, max(-1.0, abs((vx * ox + vy * oy) / (v_len * other_len))))
            if math.acos(alignment) >= angle_threshold:
                return True
        return False

    for index, profile in enumerate(profiles):
        profile["start_curve"] = endpoint_is_curve(index, True)
        profile["end_curve"] = endpoint_is_curve(index, False)
    return profiles


def build_segment_grid(segments: Sequence[Sequence[float]], cell_size: float = 0.5) -> dict:
    cells: dict[tuple[int, int], list[int]] = {}
    for index, (x1, y1, x2, y2) in enumerate(segments):
        min_cell_x = math.floor(min(x1, x2) / cell_size)
        max_cell_x = math.floor(max(x1, x2) / cell_size)
        min_cell_y = math.floor(min(y1, y2) / cell_size)
        max_cell_y = math.floor(max(y1, y2) / cell_size)
        for cell_x in range(min_cell_x, max_cell_x + 1):
            for cell_y in range(min_cell_y, max_cell_y + 1):
                cells.setdefault((cell_x, cell_y), []).append(index)
    return {"cell_size": cell_size, "cells": cells}


def nearest_segment(
    x: float,
    y: float,
    segments: Sequence[Sequence[float]],
    segment_grid: dict,
) -> tuple[float, int, float]:
    cell_size = segment_grid["cell_size"]
    cells = segment_grid["cells"]
    center_x = math.floor(x / cell_size)
    center_y = math.floor(y / cell_size)
    nearest_distance = math.inf
    nearest_index = -1
    nearest_projection = 0.0
    checked: set[int] = set()
    max_ring = 1 + max(
        abs(cell_x - center_x) + abs(cell_y - center_y)
        for cell_x, cell_y in cells
    )

    for ring in range(max_ring + 1):
        if ring == 0:
            coordinates = [(center_x, center_y)]
        else:
            coordinates = []
            for offset in range(-ring, ring + 1):
                coordinates.append((center_x + offset, center_y - ring))
                coordinates.append((center_x + offset, center_y + ring))
            for offset in range(-ring + 1, ring):
                coordinates.append((center_x - ring, center_y + offset))
                coordinates.append((center_x + ring, center_y + offset))

        for coordinate in coordinates:
            for index in cells.get(coordinate, []):
                if index in checked:
                    continue
                checked.add(index)
                x1, y1, x2, y2 = segments[index]
                length_squared = (x2 - x1) ** 2 + (y2 - y1) ** 2
                if length_squared <= 1.0e-6:
                    continue
                projection = max(0.0, min(1.0, ((x - x1) * (x2 - x1) + (y - y1) * (y2 - y1)) / length_squared))
                projected_x = x1 + projection * (x2 - x1)
                projected_y = y1 + projection * (y2 - y1)
                distance = math.hypot(x - projected_x, y - projected_y)
                if distance < nearest_distance:
                    nearest_distance = distance
                    nearest_index = index
                    nearest_projection = projection

        # Every unvisited cell is now at least roughly (ring - 1) cells away.
        # The conservative extra cell accounts for points close to a cell edge.
        if nearest_index >= 0 and ring >= 2 and (ring - 1) * cell_size > nearest_distance:
            break
    return nearest_distance, nearest_index, nearest_projection


def positioning_error(
    x: float,
    y: float,
    segments: Sequence[Sequence[float]],
    profiles: Sequence[dict],
    segment_grid: dict,
    straight_tolerance: float,
    curve_tolerance: float,
    curve_radius: float,
) -> float | None:
    nearest_distance, nearest_index, nearest_projection = nearest_segment(x, y, segments, segment_grid)
    if nearest_index < 0:
        return None
    profile = profiles[nearest_index]
    distance_from_start = nearest_projection * profile["length"]
    distance_from_end = (1.0 - nearest_projection) * profile["length"]
    is_curve = (
        profile["start_curve"] and distance_from_start <= curve_radius
    ) or (
        profile["end_curve"] and distance_from_end <= curve_radius
    )
    tolerance = curve_tolerance if is_curve else straight_tolerance
    return max(0.0, nearest_distance - tolerance)


def downsample(values: Sequence, maximum: int) -> list:
    if len(values) <= maximum:
        return list(values)
    indices = sorted({round(index * (len(values) - 1) / (maximum - 1)) for index in range(maximum)})
    return [values[index] for index in indices]


def downsample_xy(x_values: Sequence[float], y_values: Sequence[float], maximum: int) -> tuple[list[float], list[float]]:
    if len(x_values) <= maximum:
        return list(x_values), list(y_values)
    indices = sorted({round(index * (len(x_values) - 1) / (maximum - 1)) for index in range(maximum)})
    return [x_values[index] for index in indices], [y_values[index] for index in indices]


def empirical_cdf(values: Sequence[float], maximum: int = 1800) -> dict[str, list[float]]:
    sorted_values = sorted(value for value in values if math.isfinite(value))
    if not sorted_values:
        return {"x": [], "y": []}
    if len(sorted_values) > maximum:
        indices = sorted({round(index * (len(sorted_values) - 1) / (maximum - 1)) for index in range(maximum)})
    else:
        indices = list(range(len(sorted_values)))
    return {
        "x": [round(sorted_values[index], 6) for index in indices],
        "y": [round(100.0 * (index + 1) / len(sorted_values), 4) for index in indices],
    }


def normalized_profile(values: Sequence[float], bins: int = 100) -> list[float | None]:
    result: list[float | None] = []
    for bin_index in range(bins):
        start = math.floor(bin_index * len(values) / bins)
        end = math.floor((bin_index + 1) * len(values) / bins)
        section = [value for value in values[start:end] if math.isfinite(value)]
        result.append(statistics.fmean(section) if section else None)
    return result


def aggregate_profiles(profiles: Sequence[Sequence[float | None]]) -> dict[str, list[float | None]]:
    p25: list[float | None] = []
    median: list[float | None] = []
    p75: list[float | None] = []
    for values in zip(*profiles):
        valid = sorted(value for value in values if value is not None and math.isfinite(value))
        p25.append(percentile(valid, 25))
        median.append(percentile(valid, 50))
        p75.append(percentile(valid, 75))
    return {"p25": p25, "median": median, "p75": p75}


def rounded(value: float | None, digits: int = 6) -> float | None:
    return None if value is None else round(value, digits)


def rounded_metrics(metrics: dict) -> dict:
    return {key: (value if key == "count" else rounded(value)) for key, value in metrics.items()}


def label_for_file(path: Path) -> str:
    stem = path.stem
    suffix = stem.rsplit("_", 1)[-1]
    if len(suffix) == 6 and suffix.isdigit():
        return f"{suffix[:2]}:{suffix[2:4]}:{suffix[4:]}"
    return stem


def mask_label(mask: int) -> str:
    anchors = [f"A{index + 1}" for index in range(4) if mask & (1 << index)]
    return "+".join(anchors) if anchors else "No anchors"


def build_report_data(args: argparse.Namespace) -> dict:
    input_dir = args.input.resolve()
    excluded = set(args.exclude)
    csv_paths = sorted(path for path in input_dir.glob(args.pattern) if path.name not in excluded)
    if not csv_paths:
        raise RuntimeError(f"No CSV files matching {args.pattern!r} in {input_dir}")

    ground_truth_name, base_segments, ground_truth_x, ground_truth_y = load_ground_truth()
    parsed_runs = [load_csv(path) for path in csv_paths]
    detour_targets = set(args.detour_file or DETOUR_TARGET_CSVS)
    detour_source = next(
        (parsed for parsed in parsed_runs if parsed["path"].name == args.detour_source),
        None,
    )
    if detour_targets and detour_source is None:
        raise RuntimeError(f"Detour source CSV not found: {args.detour_source}")
    shared_detour_segment = fitted_detour_segment(detour_source["rows"]) if detour_source else None
    if detour_targets and shared_detour_segment is None:
        raise RuntimeError(f"Could not fit the adjusted ground-truth segment from {args.detour_source}")
    runs = []
    all_ukf_errors: list[float] = []
    all_tril_errors: list[float] = []
    ukf_profiles = []
    tril_profiles = []
    mask_errors: dict[int, dict[str, list[float]]] = {}
    total_mask_counts: dict[int, int] = {}

    for parsed in parsed_runs:
        path = parsed["path"]
        rows = parsed["rows"]
        segments = list(base_segments)
        ground_truth_variant = "Standard route"
        detour_segment = None
        if path.name in detour_targets:
            detour_segment = shared_detour_segment
            segments.append(detour_segment)
            ground_truth_variant = "Standard route + adjusted segment"
        profiles = segment_profiles(segments, args.curve_angle)
        segment_grid = build_segment_grid(segments)

        ukf_errors = []
        tril_errors = []
        mask_counts: dict[int, int] = {}
        for row in rows:
            ukf_error = positioning_error(
                row["ukf_x"], row["ukf_y"], segments, profiles,
                segment_grid,
                args.straight_tolerance, args.curve_tolerance, args.curve_radius,
            )
            tril_error = positioning_error(
                row["tril_x"], row["tril_y"], segments, profiles,
                segment_grid,
                args.straight_tolerance, args.curve_tolerance, args.curve_radius,
            )
            if ukf_error is None or tril_error is None:
                continue
            ukf_errors.append(ukf_error)
            tril_errors.append(tril_error)
            mask = row["mask"] & 0x0F
            mask_errors.setdefault(mask, {"ukf": [], "tril": []})["ukf"].append(ukf_error)
            mask_errors[mask]["tril"].append(tril_error)
            mask_counts[mask] = mask_counts.get(mask, 0) + 1
            total_mask_counts[mask] = total_mask_counts.get(mask, 0) + 1

        ukf_metrics = error_metrics(ukf_errors)
        tril_metrics = error_metrics(tril_errors)
        changes = {key: reduction_percent(tril_metrics[key], ukf_metrics[key]) for key in METRIC_KEYS}
        frames = [row["frame"] for row in rows]
        missing_frames = sum(max(0, current - previous - 1) for previous, current in zip(frames, frames[1:]) if current >= previous)
        expected_frames = max(1, len(frames) + missing_frames)
        mask_total = max(1, sum(mask_counts.values()))
        ukf_x, ukf_y = downsample_xy([row["ukf_x"] for row in rows], [row["ukf_y"] for row in rows], args.max_trajectory_points)
        tril_x, tril_y = downsample_xy([row["tril_x"] for row in rows], [row["tril_y"] for row in rows], args.max_trajectory_points)
        trajectory_gt_x = list(ground_truth_x)
        trajectory_gt_y = list(ground_truth_y)
        if detour_segment:
            trajectory_gt_x.extend([None, detour_segment[0], detour_segment[2]])
            trajectory_gt_y.extend([None, detour_segment[1], detour_segment[3]])

        runs.append(
            {
                "filename": path.name,
                "label": label_for_file(path),
                "samples": len(ukf_errors),
                "ground_truth": ground_truth_variant,
                "metrics": {"ukf": rounded_metrics(ukf_metrics), "tril": rounded_metrics(tril_metrics)},
                "changes": {key: rounded(value, 4) for key, value in changes.items()},
                "frame_gap_pct": round(100.0 * missing_frames / expected_frames, 4),
                "mask_pct": {str(key): round(100.0 * value / mask_total, 4) for key, value in mask_counts.items()},
                "box": {
                    "ukf": [round(value, 5) for value in downsample(ukf_errors, args.max_box_points)],
                    "tril": [round(value, 5) for value in downsample(tril_errors, args.max_box_points)],
                },
                "trajectory": {
                    "ukf_x": [round(value, 5) for value in ukf_x],
                    "ukf_y": [round(value, 5) for value in ukf_y],
                    "tril_x": [round(value, 5) for value in tril_x],
                    "tril_y": [round(value, 5) for value in tril_y],
                    "gt_x": [None if value is None else round(value, 5) for value in trajectory_gt_x],
                    "gt_y": [None if value is None else round(value, 5) for value in trajectory_gt_y],
                },
            }
        )
        all_ukf_errors.extend(ukf_errors)
        all_tril_errors.extend(tril_errors)
        ukf_profiles.append(normalized_profile(ukf_errors))
        tril_profiles.append(normalized_profile(tril_errors))

    aggregate_ukf = error_metrics(all_ukf_errors)
    aggregate_tril = error_metrics(all_tril_errors)
    aggregate_changes = {key: reduction_percent(aggregate_tril[key], aggregate_ukf[key]) for key in METRIC_KEYS}
    mask_performance = []
    for mask in sorted(mask_errors):
        ukf_values = mask_errors[mask]["ukf"]
        tril_values = mask_errors[mask]["tril"]
        mask_performance.append(
            {
                "mask": str(mask),
                "label": mask_label(mask),
                "samples": len(ukf_values),
                "ukf": rounded_metrics(error_metrics(ukf_values)),
                "tril": rounded_metrics(error_metrics(tril_values)),
            }
        )

    return {
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "input_dir": str(input_dir),
        "run_count": len(runs),
        "sample_count": len(all_ukf_errors),
        "ground_truth_name": ground_truth_name,
        "tolerances": {
            "straight_m": args.straight_tolerance,
            "curve_m": args.curve_tolerance,
            "curve_radius_m": args.curve_radius,
            "curve_angle_deg": args.curve_angle,
        },
        "runs": runs,
        "aggregate": {
            "metrics": {"ukf": rounded_metrics(aggregate_ukf), "tril": rounded_metrics(aggregate_tril)},
            "changes": {key: rounded(value, 4) for key, value in aggregate_changes.items()},
            "cdf": {"ukf": empirical_cdf(all_ukf_errors), "tril": empirical_cdf(all_tril_errors)},
            "profile": {
                "progress": list(range(1, 101)),
                "ukf": {key: [rounded(value) for value in values] for key, values in aggregate_profiles(ukf_profiles).items()},
                "tril": {key: [rounded(value) for value in values] for key, values in aggregate_profiles(tril_profiles).items()},
            },
            "mask_usage": [
                {
                    "mask": str(mask),
                    "label": mask_label(mask),
                    "samples": total_mask_counts[mask],
                    "pct": round(100.0 * total_mask_counts[mask] / max(1, sum(total_mask_counts.values())), 4),
                }
                for mask in sorted(total_mask_counts)
            ],
            "mask_performance": mask_performance,
            "improved_runs": {
                key: sum(1 for run in runs if run["changes"][key] is not None and run["changes"][key] > 0)
                for key in METRIC_KEYS
            },
        },
    }


HTML_TEMPLATE = r'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Aggregate UWB-RTLS Positioning Analysis</title>
  <script src="https://cdn.plot.ly/plotly-2.32.0.min.js"></script>
  <style>
    :root{color-scheme:light;--bg:#ffffff;--panel:#ffffff;--panel2:#f8fafc;--text:#172033;--muted:#64748b;--border:#dbe3ee;--blue:#2563eb;--orange:#ea580c;--green:#059669;--red:#dc2626;--violet:#7c3aed}
    *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,sans-serif}
    main{width:min(1480px,calc(100% - 32px));margin:0 auto;padding:36px 0 56px}header{display:flex;justify-content:space-between;gap:24px;align-items:flex-end;margin-bottom:22px}
    h1{font-size:clamp(1.55rem,3vw,2.55rem);line-height:1.08;margin:0 0 10px;letter-spacing:-.04em}h2{font-size:1rem;margin:0 0 14px}h3{font-size:.9rem;margin:0;color:var(--text)}p{margin:0;color:var(--muted)}
    .eyebrow{font-size:.72rem;text-transform:uppercase;letter-spacing:.16em;color:var(--orange);font-weight:700;margin-bottom:8px}.meta{text-align:right;font-size:.78rem;line-height:1.65}
    .stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:18px}.stat,.panel{background:var(--panel);border:1px solid var(--border);border-radius:16px;box-shadow:0 12px 34px rgba(15,23,42,.07)}
    .stat{padding:17px 18px}.stat-label{color:var(--muted);font-size:.72rem;text-transform:uppercase;letter-spacing:.08em}.stat-value{font-size:1.55rem;font-weight:760;margin-top:6px}.stat-context{font-size:.75rem;color:var(--muted);margin-top:3px}
    .grid-2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-bottom:14px}.panel{padding:18px}.panel-head{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:4px}.panel-note{font-size:.72rem;color:var(--muted)}
    .plot{height:410px}.plot.tall{height:500px}.run-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-bottom:14px}.run-plot{height:390px}.run-panel{min-width:0}
    .table-wrap{overflow-x:auto}.report-table{width:100%;border-collapse:collapse;font-size:.78rem}.report-table th,.report-table td{padding:10px 9px;border-bottom:1px solid var(--border);text-align:right;white-space:nowrap}.report-table th{color:var(--muted);font-size:.68rem;text-transform:uppercase;letter-spacing:.06em}.report-table th:first-child,.report-table td:first-child{text-align:left}.positive{color:var(--green)}.negative{color:var(--red)}
    .method{display:inline-flex;align-items:center;gap:6px}.dot{width:8px;height:8px;border-radius:50%;display:inline-block}.dot.ukf{background:var(--orange)}.dot.tril{background:var(--blue)}
    footer{color:var(--muted);font-size:.75rem;padding:12px 2px 0;line-height:1.6}@media(max-width:900px){header{align-items:flex-start;flex-direction:column}.meta{text-align:left}.stats,.grid-2,.run-grid{grid-template-columns:1fr 1fr}.stats{grid-template-columns:1fr 1fr}}@media(max-width:640px){main{width:min(100% - 18px,1480px);padding-top:20px}.grid-2,.run-grid{grid-template-columns:1fr}.plot,.run-plot{height:360px}}
  </style>
</head>
<body>
<main>
  <header>
    <div><div class="eyebrow">Final report · UWB RTLS</div><h1>Positioning performance across all test data</h1><p>Accuracy, tail error, run-to-run repeatability, and anchor-triplet sensitivity.</p></div>
    <div class="meta" id="report-meta"></div>
  </header>
  <section class="stats" id="summary-stats"></section>
  <section class="grid-2">
    <article class="panel"><div class="panel-head"><h2>Cumulative distribution of trajectory error</h2><span class="panel-note">Pooled samples</span></div><div id="cdf" class="plot"></div></article>
    <article class="panel"><div class="panel-head"><h2>UKF error change vs Trilateration</h2><span class="panel-note">Positive = lower error</span></div><div id="delta" class="plot"></div></article>
  </section>
  <section class="grid-2">
    <article class="panel"><div class="panel-head"><h2>P95 by test run</h2><span class="panel-note">Tail-error comparison</span></div><div id="per-run-p95" class="plot tall"></div></article>
    <article class="panel"><div class="panel-head"><h2>Improvement consistency by test run</h2><span class="panel-note">Error reduction (%)</span></div><div id="run-heatmap" class="plot tall"></div></article>
  </section>
  <section class="grid-2">
    <article class="panel"><div class="panel-head"><h2>Error over normalized trajectory progress</h2><span class="panel-note">Median and IQR across runs</span></div><div id="progress-profile" class="plot"></div></article>
    <article class="panel"><div class="panel-head"><h2>P95 by anchor triplet</h2><span class="panel-note">Triplet-geometry sensitivity</span></div><div id="anchor-performance" class="plot"></div></article>
  </section>
  <section class="grid-2">
    <article class="panel"><div class="panel-head"><h2>Error distribution by test run</h2><span class="panel-note">Box plot · downsampled</span></div><div id="run-box" class="plot tall"></div></article>
    <article class="panel"><div class="panel-head"><h2>Anchor-triplet usage</h2><span class="panel-note">By CSV file</span></div><div id="anchor-availability" class="plot tall"></div></article>
  </section>
  <section class="panel" style="margin-bottom:14px"><div class="panel-head"><h2>Trajectory by test run</h2><span class="panel-note"><span class="method"><span class="dot tril"></span>Trilateration</span> · <span class="method"><span class="dot ukf"></span>UKF</span></span></div></section>
  <section class="run-grid" id="trajectory-grid"></section>
  <section class="panel"><div class="panel-head"><h2>Detailed metrics</h2><span class="panel-note">Error is measured to the nearest ground-truth segment</span></div><div class="table-wrap"><table class="report-table"><thead><tr><th>CSV</th><th>Samples</th><th>UKF MAE</th><th>Tril MAE</th><th>Δ MAE</th><th>UKF P95</th><th>Tril P95</th><th>Δ P95</th><th>Frame gap</th><th>Ground truth</th></tr></thead><tbody id="metric-rows"></tbody></table></div></section>
  <footer id="method-note"></footer>
</main>
<script>
const REPORT=__REPORT_DATA__;
const C={blue:'#2563eb',orange:'#ea580c',green:'#059669',red:'#dc2626',violet:'#7c3aed',grid:'#e2e8f0',text:'#334155',muted:'#64748b'};
const cfg={responsive:true,displaylogo:false,modeBarButtonsToRemove:['lasso2d','select2d']};
const layout=(extra={})=>Object.assign({paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)',font:{color:C.text,family:'Inter,system-ui,sans-serif',size:11},margin:{t:34,r:20,b:52,l:60},hoverlabel:{bgcolor:'#ffffff',bordercolor:C.grid,font:{color:'#172033'}},xaxis:{gridcolor:C.grid,zerolinecolor:C.grid},yaxis:{gridcolor:C.grid,zerolinecolor:C.grid}},extra);
const fmt=v=>Number.isFinite(v)?v.toFixed(3)+' m':'N/A';
const pct=v=>Number.isFinite(v)?(v>=0?'+':'')+v.toFixed(1)+'%':'N/A';
const cls=v=>Number.isFinite(v)?(v>=0?'positive':'negative'):'';
document.getElementById('report-meta').innerHTML=`${REPORT.run_count} CSV files · ${REPORT.sample_count.toLocaleString('en-US')} samples<br>${new Date(REPORT.generated_at).toLocaleString('en-US')}<br>${REPORT.ground_truth_name}`;
const am=REPORT.aggregate.metrics,ac=REPORT.aggregate.changes,im=REPORT.aggregate.improved_runs;
document.getElementById('summary-stats').innerHTML=`
  <div class="stat"><div class="stat-label">Aggregate UKF MAE</div><div class="stat-value">${fmt(am.ukf.mae)}</div><div class="stat-context">Trilateration: ${fmt(am.tril.mae)}</div></div>
  <div class="stat"><div class="stat-label">Aggregate UKF P95</div><div class="stat-value">${fmt(am.ukf.p95)}</div><div class="stat-context">Trilateration: ${fmt(am.tril.p95)}</div></div>
  <div class="stat"><div class="stat-label">MAE reduction</div><div class="stat-value ${cls(ac.mae)}">${pct(ac.mae)}</div><div class="stat-context">Across all pooled samples</div></div>
  <div class="stat"><div class="stat-label">Run consistency</div><div class="stat-value">${im.mae}/${REPORT.run_count}</div><div class="stat-context">Runs with lower UKF MAE</div></div>`;

const cdf=REPORT.aggregate.cdf;
Plotly.newPlot('cdf',[
  {x:cdf.tril.x,y:cdf.tril.y,name:'Trilateration',mode:'lines',line:{color:C.blue,width:2.5},hovertemplate:'%{x:.3f} m<br>%{y:.1f}%<extra>Trilateration</extra>'},
  {x:cdf.ukf.x,y:cdf.ukf.y,name:'UKF',mode:'lines',line:{color:C.orange,width:2.5},hovertemplate:'%{x:.3f} m<br>%{y:.1f}%<extra>UKF</extra>'}
],layout({legend:{orientation:'h',x:0,y:1.13},xaxis:{title:'Trajectory error (m)',gridcolor:C.grid,rangemode:'tozero'},yaxis:{title:'Cumulative probability',ticksuffix:'%',range:[0,100],gridcolor:C.grid},shapes:[{type:'line',x0:am.tril.p95,x1:am.tril.p95,y0:0,y1:95,line:{color:C.blue,dash:'dot'}},{type:'line',x0:am.ukf.p95,x1:am.ukf.p95,y0:0,y1:95,line:{color:C.orange,dash:'dot'}}],annotations:[{x:am.tril.p95,y:92,text:`Tril P95: ${am.tril.p95.toFixed(3)} m`,showarrow:false,xanchor:'right',font:{color:C.blue}},{x:am.ukf.p95,y:84,text:`UKF P95: ${am.ukf.p95.toFixed(3)} m`,showarrow:false,xanchor:'left',font:{color:C.orange}}]}),cfg);

const metricKeys=['mae','rmse','p95','max'],metricLabels=['MAE','RMSE','P95','Max'];
const deltaValues=metricKeys.map(k=>ac[k]);const deltaExtent=Math.max(10,...deltaValues.filter(Number.isFinite).map(Math.abs))*1.25;
Plotly.newPlot('delta',[{x:deltaValues,y:metricLabels,type:'bar',orientation:'h',marker:{color:deltaValues.map(v=>v>=0?C.green:C.red)},text:deltaValues.map(pct),textposition:'outside',cliponaxis:false,hovertemplate:'%{y}: %{x:+.1f}%<extra></extra>'}],layout({margin:{t:24,r:62,b:52,l:60},xaxis:{title:'UKF error reduction',ticksuffix:'%',range:[-deltaExtent,deltaExtent],gridcolor:C.grid,zeroline:true,zerolinecolor:C.muted},yaxis:{autorange:'reversed',gridcolor:C.grid},showlegend:false}),cfg);

const labels=REPORT.runs.map(r=>r.label);
Plotly.newPlot('per-run-p95',[{x:REPORT.runs.map(r=>r.metrics.tril.p95),y:labels,name:'Trilateration',type:'bar',orientation:'h',marker:{color:C.blue},hovertemplate:'%{y}<br>%{x:.3f} m<extra>Trilateration P95</extra>'},{x:REPORT.runs.map(r=>r.metrics.ukf.p95),y:labels,name:'UKF',type:'bar',orientation:'h',marker:{color:C.orange},hovertemplate:'%{y}<br>%{x:.3f} m<extra>UKF P95</extra>'}],layout({barmode:'group',legend:{orientation:'h',x:0,y:1.1},xaxis:{title:'P95 (m)',gridcolor:C.grid,rangemode:'tozero'},yaxis:{autorange:'reversed',gridcolor:C.grid}}),cfg);

const heatZ=REPORT.runs.map(r=>metricKeys.map(k=>r.changes[k]));
Plotly.newPlot('run-heatmap',[{z:heatZ,x:metricLabels,y:labels,type:'heatmap',zmid:0,colorscale:[[0,C.red],[.5,'#f1f5f9'],[1,C.green]],text:heatZ.map(row=>row.map(pct)),texttemplate:'%{text}',hovertemplate:'%{y}<br>%{x}: %{z:+.1f}%<extra></extra>',colorbar:{title:'Error<br>reduction',ticksuffix:'%'}}],layout({margin:{t:30,r:70,b:48,l:64},xaxis:{side:'bottom',gridcolor:C.grid},yaxis:{autorange:'reversed',gridcolor:C.grid}}),cfg);

const profile=REPORT.aggregate.profile,p=profile.progress;
Plotly.newPlot('progress-profile',[
  {x:p,y:profile.tril.p25,mode:'lines',line:{width:0},showlegend:false,hoverinfo:'skip'},
  {x:p,y:profile.tril.p75,mode:'lines',fill:'tonexty',fillcolor:'rgba(96,165,250,.14)',line:{width:0},name:'Tril IQR',hoverinfo:'skip'},
  {x:p,y:profile.tril.median,mode:'lines',line:{color:C.blue,width:2},name:'Tril median'},
  {x:p,y:profile.ukf.p25,mode:'lines',line:{width:0},showlegend:false,hoverinfo:'skip'},
  {x:p,y:profile.ukf.p75,mode:'lines',fill:'tonexty',fillcolor:'rgba(251,146,60,.14)',line:{width:0},name:'UKF IQR',hoverinfo:'skip'},
  {x:p,y:profile.ukf.median,mode:'lines',line:{color:C.orange,width:2},name:'UKF median'}
],layout({legend:{orientation:'h',x:0,y:1.13},xaxis:{title:'Normalized run progress',ticksuffix:'%',gridcolor:C.grid},yaxis:{title:'Mean segment error (m)',gridcolor:C.grid,rangemode:'tozero'},hovermode:'x unified'}),cfg);

const ap=REPORT.aggregate.mask_performance;
Plotly.newPlot('anchor-performance',[{x:ap.map(v=>v.label),y:ap.map(v=>v.tril.p95),name:'Trilateration',type:'bar',marker:{color:C.blue},customdata:ap.map(v=>v.samples),hovertemplate:'%{x}<br>P95: %{y:.3f} m<br>%{customdata} samples<extra>Trilateration</extra>'},{x:ap.map(v=>v.label),y:ap.map(v=>v.ukf.p95),name:'UKF',type:'bar',marker:{color:C.orange},customdata:ap.map(v=>v.samples),hovertemplate:'%{x}<br>P95: %{y:.3f} m<br>%{customdata} samples<extra>UKF</extra>'}],layout({barmode:'group',legend:{orientation:'h',x:0,y:1.13},xaxis:{gridcolor:C.grid},yaxis:{title:'P95 (m)',gridcolor:C.grid,rangemode:'tozero'}}),cfg);

const box=[];REPORT.runs.forEach(run=>{box.push({y:run.box.tril,name:run.label+' Tril',type:'box',marker:{color:C.blue},line:{color:C.blue},boxpoints:false,legendgroup:'tril',showlegend:false});box.push({y:run.box.ukf,name:run.label+' UKF',type:'box',marker:{color:C.orange},line:{color:C.orange},boxpoints:false,legendgroup:'ukf',showlegend:false})});
Plotly.newPlot('run-box',box,layout({margin:{t:28,r:18,b:76,l:58},xaxis:{tickangle:-42,gridcolor:C.grid},yaxis:{title:'Error (m)',gridcolor:C.grid,rangemode:'tozero'},boxmode:'group'}),cfg);

const maskColors=[C.blue,C.orange,C.violet,C.green,C.red,'#22d3ee'];
const maskTraces=REPORT.aggregate.mask_usage.map((entry,index)=>({x:labels,y:REPORT.runs.map(r=>r.mask_pct[entry.mask]||0),name:entry.label,type:'bar',marker:{color:maskColors[index%maskColors.length]},hovertemplate:'%{x}<br>%{y:.1f}%<extra>'+entry.label+'</extra>'}));
Plotly.newPlot('anchor-availability',maskTraces,layout({barmode:'stack',legend:{orientation:'h',x:0,y:1.1},xaxis:{gridcolor:C.grid},yaxis:{title:'Sample share',ticksuffix:'%',range:[0,100],gridcolor:C.grid}}),cfg);

const trajectoryGrid=document.getElementById('trajectory-grid');
REPORT.runs.forEach((run,index)=>{const panel=document.createElement('article');panel.className='panel run-panel';panel.innerHTML=`<div class="panel-head"><h3>${run.label} · ${run.samples.toLocaleString('en-US')} samples</h3><span class="panel-note">${run.ground_truth}</span></div><div class="run-plot" id="trajectory-${index}"></div>`;trajectoryGrid.appendChild(panel);const t=run.trajectory;Plotly.newPlot(`trajectory-${index}`,[{x:t.gt_x,y:t.gt_y,name:'Ground truth',mode:'lines',line:{color:C.muted,width:1.5,dash:'dot'},hoverinfo:'skip'},{x:t.tril_x,y:t.tril_y,name:'Trilateration',mode:'lines',line:{color:C.blue,width:1.2},hovertemplate:'(%{x:.3f}, %{y:.3f})<extra>Trilateration</extra>'},{x:t.ukf_x,y:t.ukf_y,name:'UKF',mode:'lines',line:{color:C.orange,width:1.6},hovertemplate:'(%{x:.3f}, %{y:.3f})<extra>UKF</extra>'}],layout({margin:{t:18,r:14,b:46,l:50},legend:{orientation:'h',x:0,y:1.1,font:{size:9}},xaxis:{title:'X (m)',gridcolor:C.grid},yaxis:{title:'Y (m)',gridcolor:C.grid,scaleanchor:'x',scaleratio:1},hovermode:'closest'}),cfg)});

document.getElementById('metric-rows').innerHTML=REPORT.runs.map(run=>`<tr><td title="${run.filename}">${run.label}</td><td>${run.samples.toLocaleString('en-US')}</td><td>${fmt(run.metrics.ukf.mae)}</td><td>${fmt(run.metrics.tril.mae)}</td><td class="${cls(run.changes.mae)}">${pct(run.changes.mae)}</td><td>${fmt(run.metrics.ukf.p95)}</td><td>${fmt(run.metrics.tril.p95)}</td><td class="${cls(run.changes.p95)}">${pct(run.changes.p95)}</td><td>${run.frame_gap_pct.toFixed(2)}%</td><td>${run.ground_truth}</td></tr>`).join('');
const tol=REPORT.tolerances;document.getElementById('method-note').textContent=`Error is the shortest Euclidean distance to the ground-truth trajectory. Tolerance is subtracted before metric calculation: ${tol.straight_m.toFixed(3)} m on straight segments and ${tol.curve_m.toFixed(3)} m near corners within a ${tol.curve_radius_m.toFixed(2)} m radius. Improvement = (Trilateration − UKF) / Trilateration × 100%.`;
</script>
</body>
</html>'''


def build_html(report_data: dict) -> str:
    payload = json.dumps(report_data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return HTML_TEMPLATE.replace("__REPORT_DATA__", payload)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze all UWB positioning CSV files and build one HTML report.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_DIR, help="Directory containing the CSV files")
    parser.add_argument("--output", type=Path, default=None, help="Output HTML path (default: <input>/final_report_analysis.html)")
    parser.add_argument("--pattern", default="*.csv", help="CSV glob pattern")
    parser.add_argument("--exclude", action="append", default=[], help="Filename to exclude; may be repeated")
    parser.add_argument("--detour-source", default=DETOUR_SOURCE_CSV, help="CSV used to fit the adjusted route segment")
    parser.add_argument("--detour-file", action="append", default=None, help="CSV that uses the adjusted route; may be repeated")
    parser.add_argument("--straight-tolerance", type=float, default=0.0)
    parser.add_argument("--curve-tolerance", type=float, default=0.01)
    parser.add_argument("--curve-radius", type=float, default=0.30)
    parser.add_argument("--curve-angle", type=float, default=5.0)
    parser.add_argument("--max-trajectory-points", type=int, default=1400)
    parser.add_argument("--max-box-points", type=int, default=1800)
    parser.add_argument("--open", action="store_true", help="Open the generated report in the default browser")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output = args.output.resolve() if args.output else args.input.resolve() / DEFAULT_OUTPUT_NAME
    report_data = build_report_data(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_html(report_data), encoding="utf-8")
    print(f"Generated: {args.output}")
    print(f"CSV files: {report_data['run_count']} | Valid samples: {report_data['sample_count']}")
    print(f"UKF MAE: {report_data['aggregate']['metrics']['ukf']['mae']:.3f} m | UKF P95: {report_data['aggregate']['metrics']['ukf']['p95']:.3f} m")
    if args.open:
        webbrowser.open(args.output.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
