import http.server
import csv
import importlib.util
import json
import math
import os
import re
import socketserver
import sys
import threading
import webbrowser


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FUSION_FILE_PATTERNS = ("fusion_frame_log_data",)


def _load_base_report_module():
    module_path = os.path.join(BASE_DIR, "uwb-rtls_simulation.py")
    spec = importlib.util.spec_from_file_location("uwb_rtls_simulation_report", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE_REPORT = _load_base_report_module()


def _safe_float(value, default=0.0):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _safe_int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _extract_number(raw_line, key, default=0.0):
    match = re.search(rf"{key}:\s*([-+]?\d+(?:\.\d+)?)", raw_line)
    return _safe_float(match.group(1), default) if match else default


def parse_fusion_frame_log(filepath):
    entries = []
    prev_step_tx = {}
    with open(filepath, "r", encoding="utf-8") as f:
        first_line = f.readline()
        f.seek(0)
        if "frame_counter" in first_line and "ukf_x_m" in first_line:
            reader = csv.DictReader(f)
            for line_no, row in enumerate(reader, 2):
                ukf_x = _safe_float(row.get("ukf_x_m", row.get("ukf_x", 0.0)))
                ukf_y = _safe_float(row.get("ukf_y_m", row.get("ukf_y", 0.0)))
                tril_x = _safe_float(row.get("tril_x_m", row.get("tril_x", ukf_x)), ukf_x)
                tril_y = _safe_float(row.get("tril_y_m", row.get("tril_y", ukf_y)), ukf_y)
                ukf_step = _safe_int(row.get("ukf_step", 0), 0)
                tx_frame_cnt = _safe_int(row.get("tx_frame_cnt", row.get("frame_counter", 0)), 0)

                dt = _safe_float(row.get("dt"), -1.0)
                if dt < 0:
                    prev_tx = prev_step_tx.get(ukf_step)
                    dt = 0.0 if prev_tx is None else max(1, tx_frame_cnt - prev_tx) * 0.02
                prev_step_tx[ukf_step] = tx_frame_cnt

                entries.append({
                    "line_no": line_no,
                    "raw_line": ",".join(str(row.get(key, "")) for key in (reader.fieldnames or [])),
                    "frame_counter": _safe_int(row.get("frame_counter", len(entries) + 1), len(entries) + 1),
                    "tx_frame_cnt": tx_frame_cnt,
                    "type": row.get("status") or ("Update" if ukf_step == 1 else "Predict"),
                    "source_format": row.get("frame_type") or "unified_csv",
                    "ukf_step": ukf_step,
                    "ax": _safe_float(row.get("ax")),
                    "ay": _safe_float(row.get("ay")),
                    "gz": _safe_float(row.get("gz")),
                    "px_fw": ukf_x,
                    "py_fw": ukf_y,
                    "dt": dt,
                    "ukf_x": ukf_x,
                    "ukf_y": ukf_y,
                    "tril_x": tril_x,
                    "tril_y": tril_y,
                    "yaw": _safe_float(row.get("yaw_deg"), _safe_float(row.get("ukf_yaw_deg"))),
                    "ukf_yaw": _safe_float(row.get("ukf_yaw_deg"), _safe_float(row.get("yaw_deg"))),
                    "fp_amp_norm": [_safe_float(row.get(f"fp_amp_norm{i}")) for i in range(1, 5)],
                    "fp_snr": [_safe_float(row.get(f"fp_snr{i}")) for i in range(1, 5)],
                    "fp_confidence": [_safe_float(row.get(f"fp_confidence{i}")) for i in range(1, 5)],
                    "quality_valid": [_safe_int(row.get(f"quality_valid{i}")) for i in range(1, 5)],
                    "mask": _safe_int(row.get("anchor_mask", row.get("mask", 15)), 15),
                    "distances": [_safe_float(row.get(f"d{i}")) for i in range(1, 5)],
                    "err": _safe_int(row.get("ranging_error_count", row.get("err", 0)), 0),
                })
            return entries

        for line_no, line in enumerate(f, 1):
            raw_line = line.rstrip("\r\n")
            if "ukf_x:" not in raw_line:
                continue

            counter_match = re.search(r"\(\s*(?P<rx>\d+)\s*/\s*(?P<tx>\d+)\s*\)", raw_line)
            frame_counter = int(counter_match.group("rx")) if counter_match else len(entries) + 1
            tx_frame_cnt = int(counter_match.group("tx")) if counter_match else frame_counter
            ukf_step = _safe_int(_extract_number(raw_line, "ukf_step", 0), 0)
            status_match = re.search(r"\)\s*(?P<type>Update|Init|Predict)\b", raw_line)

            dt = _extract_number(raw_line, "dt", -1.0)
            if dt < 0:
                update_dt = _extract_number(raw_line, "update_dt", -1.0)
                predict_dt = _extract_number(raw_line, "predict_dt", -1.0)
                dt = update_dt if update_dt >= 0 else predict_dt
            if dt < 0:
                prev_tx = prev_step_tx.get(ukf_step)
                dt = 0.0 if prev_tx is None else max(1, tx_frame_cnt - prev_tx) * 0.02
            prev_step_tx[ukf_step] = tx_frame_cnt

            ukf_x = _extract_number(raw_line, "ukf_x")
            ukf_y = _extract_number(raw_line, "ukf_y")
            tril_x = _extract_number(raw_line, "tril_x", ukf_x)
            tril_y = _extract_number(raw_line, "tril_y", ukf_y)
            yaw = _extract_number(raw_line, "yaw", _extract_number(raw_line, "ukf_yaw"))

            entries.append({
                "line_no": line_no,
                "raw_line": raw_line,
                "frame_counter": frame_counter,
                "tx_frame_cnt": tx_frame_cnt,
                "type": status_match.group("type") if status_match else ("Update" if ukf_step == 1 else "Predict"),
                "source_format": "path_csv",
                "ukf_step": ukf_step,
                "ax": 0.0,
                "ay": 0.0,
                "gz": 0.0,
                "px_fw": ukf_x,
                "py_fw": ukf_y,
                "dt": dt,
                "ukf_x": ukf_x,
                "ukf_y": ukf_y,
                "tril_x": tril_x,
                "tril_y": tril_y,
                "yaw": yaw,
                "ukf_yaw": _extract_number(raw_line, "ukf_yaw", yaw),
                "fp_amp_norm": [0, 0, 0, 0],
                "fp_snr": [0, 0, 0, 0],
                "fp_confidence": [0, 0, 0, 0],
                "quality_valid": [0, 0, 0, 0],
                "mask": _safe_int(_extract_number(raw_line, "mask", 15), 15),
                "distances": [0.0, 0.0, 0.0, 0.0],
                "err": _safe_int(_extract_number(raw_line, "err", 0), 0),
            })
    return entries


def _make_thumb(fw_path):
    xs = fw_path["x"]
    ys = fw_path["y"]
    if not xs:
        return '<svg viewBox="0 0 60 60"></svg>'

    all_x = xs + BASE_REPORT.GT_SQUARE["x"]
    all_y = ys + BASE_REPORT.GT_SQUARE["y"]
    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)
    dx, dy = max_x - min_x, max_y - min_y
    scale = 50 / max(dx, dy, 0.1)
    off_x = (50 - dx * scale) / 2
    off_y = (50 - dy * scale) / 2

    def to_svg(x_list, y_list):
        points = []
        for x, y in zip(x_list, y_list):
            px = 5 + off_x + (x - min_x) * scale
            py = 55 - off_y - (y - min_y) * scale
            points.append(f"{px:.1f},{py:.1f}")
        return " ".join(points)

    gt_pts = to_svg(BASE_REPORT.GT_SQUARE["x"], BASE_REPORT.GT_SQUARE["y"])
    fw_pts = to_svg(xs, ys)
    return (
        '<svg viewBox="0 0 60 60">'
        f'<polyline points="{gt_pts}" fill="none" stroke="#fecaca" stroke-width="1.5" stroke-dasharray="2,2" />'
        f'<polyline points="{fw_pts}" fill="none" stroke="#a78bfa" stroke-width="1.6" />'
        '</svg>'
    )


def run_gen(log_file):
    entries = parse_fusion_frame_log(log_file)
    if not entries:
        return None

    fw_path = {"x": [], "y": [], "mask": []}
    tril_path = {"x": [], "y": []}
    for entry in entries:
        fw_path["x"].append(entry["ukf_x"])
        fw_path["y"].append(entry["ukf_y"])
        fw_path["mask"].append(entry.get("mask", 15))
        tril_path["x"].append(entry["tril_x"])
        tril_path["y"].append(entry["tril_y"])

    return {
        "log_format": "path_csv",
        "source_file_type": "fusion_frame",
        "fw_path": fw_path,
        "tril_path": tril_path,
        "fp_logs": {"amp": [[], [], [], []], "snr": [[], [], [], []]},
        "all_entries": entries,
        "biases": {"ax": 0.0, "ay": 0.0, "gz": 0.0},
        "thumb_svg": _make_thumb(fw_path),
    }


def _is_fusion_csv(filename):
    return filename.endswith(".csv") and any(pattern in filename for pattern in FUSION_FILE_PATTERNS)


def _find_logs():
    data_dir = os.path.abspath(os.path.join(BASE_DIR, "..", "data"))
    search_dirs = [data_dir] if os.path.isdir(data_dir) else []

    logs = [
        os.path.join(root, filename)
        for search_dir in search_dirs
        for root, _, files in os.walk(search_dir)
        for filename in files
        if _is_fusion_csv(filename)
    ]
    logs.sort(reverse=True)
    return logs


def _report_path_for(log_path):
    report_name = os.path.basename(log_path).replace(".csv", "_sim.html")
    data_dir = os.path.abspath(os.path.join(BASE_DIR, "..", "data"))
    if (
        os.path.abspath(log_path).startswith(os.path.abspath(BASE_DIR))
        or os.path.abspath(log_path).startswith(data_dir)
    ):
        return os.path.join(os.path.dirname(log_path), report_name)
    return os.path.join(BASE_DIR, "logs_data_reports", report_name)


def _generate_reports():
    template_content = BASE_REPORT.load_template()
    app_js_bundle = BASE_REPORT.load_app_js_bundle()
    worker_js_bundle = BASE_REPORT.load_worker_js_bundle()
    report_source_mtime = BASE_REPORT.get_report_source_mtime()

    results = []
    files_generated = 0
    for log_path in _find_logs():
        try:
            payload = run_gen(log_path)
            if not payload:
                continue

            report_path = _report_path_for(log_path)
            log_mtime = os.path.getmtime(log_path)
            html_mtime = os.path.getmtime(report_path) if os.path.exists(report_path) else 0
            needs_gen = not os.path.exists(report_path) or log_mtime > html_mtime or report_source_mtime > html_mtime

            if needs_gen:
                os.makedirs(os.path.dirname(report_path), exist_ok=True)
                with open(report_path, "w", encoding="utf-8") as f:
                    f.write(BASE_REPORT.render_template(
                        template_content,
                        os.path.basename(log_path),
                        payload,
                        BASE_REPORT.load_ground_truths(payload),
                        app_js_bundle,
                        worker_js_bundle,
                    ))
                files_generated += 1

            results.append({
                "name": os.path.basename(log_path),
                "path": os.path.relpath(report_path, BASE_DIR).replace("\\", "/"),
                "samples": len(payload["all_entries"]),
                "thumb": payload["thumb_svg"],
            })
        except Exception:
            import traceback
            traceback.print_exc()

    return results, files_generated, str(int(report_source_mtime))


def _write_dashboard(results, files_generated, cache_token):
    grouped = {}
    for result in results:
        folder = os.path.dirname(result["path"]) or "Root"
        grouped.setdefault(folder, []).append(result)

    sections = []
    for folder in sorted(grouped.keys(), reverse=True):
        items = "".join([
            f'<a href="{item["path"]}?v={cache_token}" class="log-item">'
            f'  <div style="display:flex;align-items:center;gap:15px;">'
            f'    <div class="thumb">{item["thumb"]}</div>'
            f'    <span>{item["name"]}</span>'
            f'  </div>'
            f'  <span class="sample-count">{item["samples"]} samples</span>'
            f'</a>'
            for item in grouped[folder]
        ])
        sections.append(f"""
        <div class="date-group">
            <div class="date-header">{folder}</div>
            <div class="log-list">{items}</div>
        </div>
        """)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Fusion Frame Reports</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; background: #f8fafc; color: #0f172a; }}
    .header {{ margin-bottom: 18px; }}
    .date-group {{ background: white; border: 1px solid #e2e8f0; border-radius: 10px; margin-bottom: 16px; overflow: hidden; }}
    .date-header {{ background: #e2e8f0; padding: 10px 14px; font-weight: 700; }}
    .log-list {{ display: flex; flex-direction: column; }}
    .log-item {{ display: flex; justify-content: space-between; align-items: center; padding: 10px 14px; border-top: 1px solid #f1f5f9; color: #0f172a; text-decoration: none; }}
    .log-item:hover {{ background: #f8fafc; }}
    .thumb svg {{ width: 56px; height: 56px; }}
    .sample-count {{ color: #64748b; font-size: 0.9rem; }}
  </style>
</head>
<body>
  <div class="header">
    <h1>Fusion Frame Reports</h1>
    <p>Generated {files_generated} report(s). Showing {len(results)} fusion CSV file(s).</p>
  </div>
  {''.join(sections) if sections else '<p>No fusion_frame_log_data CSV files found.</p>'}
</body>
</html>"""

    dashboard_path = os.path.join(BASE_DIR, "fusion_frame_dashboard.html")
    with open(dashboard_path, "w", encoding="utf-8") as f:
        f.write(html)
    return dashboard_path


def main():
    results, files_generated, cache_token = _generate_reports()
    dashboard_path = _write_dashboard(results, files_generated, cache_token)

    port = 8000
    httpd = None
    while port < 8050:
        try:
            os.chdir(BASE_DIR)
            httpd = socketserver.TCPServer(("", port), http.server.SimpleHTTPRequestHandler)
            break
        except OSError:
            port += 1

    if not httpd:
        print("[ERROR] Could not find an available port.")
        sys.exit(1)

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    print(f"[SERVER] Running at http://localhost:{port}")
    webbrowser.open(f"http://localhost:{port}/{os.path.basename(dashboard_path)}")

    try:
        while True:
            import time
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[INFO] Server stopped by user.")


if __name__ == "__main__":
    main()
