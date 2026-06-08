import os
import re
import json
import math
import sys
import csv
import datetime
import http.server
import socketserver
import webbrowser
import threading
import xml.etree.ElementTree as ET

# --- CONFIGURATION ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GT_SQUARE = {
    'x': [2.44, 7.32, 7.32, 2.44, 2.44],
    'y': [2.44, 2.44, 7.32, 7.32, 2.44]
}
DEFAULT_ANCHORS = [
    {'id': 1, 'x': 0.0,  'y': 0.0,  'z': 0.895},
    {'id': 2, 'x': 9.76, 'y': 0.0,  'z': 0.895},
    {'id': 3, 'x': 0.0,  'y': 9.76, 'z': 0.895},
    {'id': 4, 'x': 9.76, 'y': 9.76, 'z': 0.895},
]
GROUND_TRUTH_PARAMS = {
    'custom_track': {
        # world: ground truth is fixed in room/world coordinates and does not follow A1.
        # anchor_relative: ground truth coordinates are relative to the selected anchor.
        'coordinate_frame': 'world',
        'anchor_id': 1,
        'offset_x': 0.5,
        'offset_y': 0.5,
    }
}

def _anchor_origin(anchor_id):
    for anchor in DEFAULT_ANCHORS:
        if anchor['id'] == anchor_id:
            return anchor['x'], anchor['y']
    return 0.0, 0.0

def apply_groundtruth_params(track):
    if not track:
        return None

    params = GROUND_TRUTH_PARAMS.get(track.get('id'), {})
    frame = params.get('coordinate_frame', 'world')
    offset_x = float(params.get('offset_x', 0.0) or 0.0)
    offset_y = float(params.get('offset_y', 0.0) or 0.0)

    if frame == 'anchor_relative':
        ax, ay = _anchor_origin(int(params.get('anchor_id', 1) or 1))
        offset_x += ax
        offset_y += ay

    def shift_x(v):
        return None if v is None else v + offset_x

    def shift_y(v):
        return None if v is None else v + offset_y

    transformed = dict(track)
    transformed['x'] = [shift_x(v) for v in track.get('x', [])]
    transformed['y'] = [shift_y(v) for v in track.get('y', [])]
    transformed['segments'] = [
        [seg[0] + offset_x, seg[1] + offset_y, seg[2] + offset_x, seg[3] + offset_y, *seg[4:]]
        for seg in track.get('segments', [])
    ]
    transformed['coordinate_frame'] = frame
    transformed['groundtruth_offset'] = {'x': offset_x, 'y': offset_y}
    return transformed

def parse_graphml_groundtruth(filepath):
    if not os.path.exists(filepath):
        return None

    ns = {'g': 'http://graphml.graphdrawing.org/xmlns'}
    tree = ET.parse(filepath)
    root = tree.getroot()

    key_names = {}
    for key in root.findall('g:key', ns):
        key_id = key.get('id')
        attr_name = key.get('attr.name')
        if key_id and attr_name:
            key_names[key_id] = attr_name

    nodes = {}
    graph = root.find('g:graph', ns)
    if graph is None:
        return None

    for node in graph.findall('g:node', ns):
        values = {}
        for data in node.findall('g:data', ns):
            values[key_names.get(data.get('key'), data.get('key'))] = data.text
        try:
            nodes[node.get('id')] = {
                'x': float(values['x']),
                'y': float(values['y'])
            }
        except (KeyError, TypeError, ValueError):
            continue

    segments = []
    for edge in graph.findall('g:edge', ns):
        src = nodes.get(edge.get('source'))
        dst = nodes.get(edge.get('target'))
        if src and dst:
            # Read the 'dotted' attribute (d2 key): True = single lane (22cm), False = double lane (44cm)
            is_dotted = False
            for data in edge.findall('g:data', ns):
                attr_name = key_names.get(data.get('key'), data.get('key'))
                if attr_name == 'dotted' and data.text:
                    is_dotted = data.text.strip().lower() == 'true'
            segments.append([src['x'], src['y'], dst['x'], dst['y'], is_dotted])

    if not segments:
        return None

    x = []
    y = []
    for seg in segments:
        x.extend([seg[0], seg[2], None])
        y.extend([seg[1], seg[3], None])

    return {
        'id': 'custom_track',
        'name': os.path.basename(filepath),
        'x': x,
        'y': y,
        'segments': segments
    }

def load_ground_truths():
    square_segments = [
        [GT_SQUARE['x'][i], GT_SQUARE['y'][i], GT_SQUARE['x'][i + 1], GT_SQUARE['y'][i + 1], False]
        for i in range(len(GT_SQUARE['x']) - 1)
    ]
    tracks = [
        {
            'id': 'square',
            'name': 'Original Square',
            'x': GT_SQUARE['x'],
            'y': GT_SQUARE['y'],
            'segments': square_segments
        }
    ]

    custom = parse_graphml_groundtruth(os.path.join(BASE_DIR, 'custom_track_modified.xml'))
    if custom:
        tracks.append(apply_groundtruth_params(custom))

    return tracks

def parse_log(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        first_line = f.readline().strip()
    if 'ukf_x' in first_line and 'tril_x' in first_line:
        return parse_path_csv_log(filepath)

    data = []
    pattern = re.compile(r"""
        (?P<type>Update|Init|Predict)          # Loại log
        \s+ax:\s*(?P<ax>[\d.-]+)               # Accel X
        \s+ay:\s*(?P<ay>[\d.-]+)               # Accel Y
        \s+gz:\s*(?P<gz>[\d.-]+)               # Gyro Z
        \s+px:\s*(?P<px>[\d.-]+)               # Pos X (Firmware)
        \s+py:\s*(?P<py>[\d.-]+)               # Pos Y (Firmware)
        \s+dt:\s*(?P<dt>[\d.-]+)               # Delta Time
        .*?                                    # Skip unknown content
        (?:mask:\s*(?P<mask>\d+)\s+)?              # Anchor Mask (Optional)
        d1:\s*(?P<d1>[\d.-]+)\s+                   # Distance 1
        d2:\s*(?P<d2>[\d.-]+)\s+                   # Distance 2
        d3:\s*(?P<d3>[\d.-]+)\s+                   # Distance 3
        d4:\s*(?P<d4>[\d.-]+)                      # Distance 4
        \s+err:\s*(?P<err>\d+)                     # Error Code
    """, re.VERBOSE)
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line_no, line in enumerate(f, 1):
                raw_line = line.rstrip('\r\n')
                m = pattern.search(line)
                if m:
                    d = m.groupdict()
                    
                    def parse_float_list(s):
                        return [float(x.strip()) for x in (s or "").split(',') if x.strip()] or [0,0,0,0]

                    def parse_quality(prefix):
                        bracket = re.search(rf"{prefix}:\s*\[([\d.,\s-]+)\]", raw_line)
                        if bracket:
                            return parse_float_list(bracket.group(1))

                        values = []
                        for anchor_idx in range(1, 5):
                            scalar = re.search(rf"{prefix}{anchor_idx}:\s*([\d.-]+)", raw_line)
                            values.append(float(scalar.group(1)) if scalar else 0)
                        return values

                    amp_vals = parse_quality('amp') if 'amp' in raw_line or 'fp_amp_norm' in raw_line else [0,0,0,0]
                    snr_vals = parse_quality('snr') if 'snr' in raw_line or 'fp_snr' in raw_line else [0,0,0,0]

                    # Sanitize extreme outliers (values > 5000 or non-finite)
                    amp_vals = [v if (math.isfinite(v) and -5000.0 < v < 5000.0) else 0.0 for v in amp_vals]
                    snr_vals = [v if (math.isfinite(v) and -5000.0 < v < 5000.0) else 0.0 for v in snr_vals]

                    data.append({
                        'line_no': line_no,
                        'raw_line': raw_line,
                        'type': d['type'],
                        'ax': float(d['ax']), 'ay': float(d['ay']), 'gz': float(d['gz']),
                        'px_fw': float(d['px']), 'py_fw': float(d['py']), 'dt': float(d['dt']),
                        'fp_amp_norm': amp_vals,
                        'fp_snr': snr_vals,
                        'mask': int(d['mask']) if d.get('mask') else 15,
                        'distances': [float(d['d1']), float(d['d2']), float(d['d3']), float(d['d4'])],
                        'err': int(d['err'])
                    })
    except: pass
    return data

def safe_float(value, default=0.0):
    try:
        v = float(value)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default

def safe_int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default

def parse_path_csv_log(filepath):
    data = []
    prev_frame = None
    try:
        with open(filepath, 'r', encoding='utf-8', newline='') as f:
            reader = csv.DictReader(f)
            for line_no, row in enumerate(reader, 2):
                frame = safe_int(row.get('tx_frame_cnt'), len(data))
                if row.get('dt') not in (None, ''):
                    dt = safe_float(row.get('dt'))
                elif prev_frame is None:
                    dt = 0.0
                else:
                    frame_delta = max(1, frame - prev_frame)
                    dt = frame_delta / 100.0
                prev_frame = frame

                ukf_x = safe_float(row.get('ukf_x'))
                ukf_y = safe_float(row.get('ukf_y'))
                tril_x = safe_float(row.get('tril_x'), ukf_x)
                tril_y = safe_float(row.get('tril_y'), ukf_y)
                yaw = safe_float(row.get('yaw'), safe_float(row.get('ukf_yaw')))

                data.append({
                    'line_no': line_no,
                    'raw_line': ','.join(row.get(k, '') for k in (reader.fieldnames or [])),
                    'type': 'Update',
                    'source_format': 'path_csv',
                    'tx_frame_cnt': frame,
                    'ax': 0.0, 'ay': 0.0, 'gz': 0.0,
                    'px_fw': ukf_x, 'py_fw': ukf_y, 'dt': dt,
                    'ukf_x': ukf_x, 'ukf_y': ukf_y,
                    'tril_x': tril_x, 'tril_y': tril_y,
                    'yaw': yaw,
                    'ukf_yaw': safe_float(row.get('ukf_yaw'), yaw),
                    'fp_amp_norm': [0, 0, 0, 0],
                    'fp_snr': [0, 0, 0, 0],
                    'mask': safe_int(row.get('anchor_mask'), 15),
                    'distances': [0.0, 0.0, 0.0, 0.0],
                    'err': safe_int(row.get('error_cnt'), safe_int(row.get('error_frame_cnt'), 0))
                })
    except Exception:
        pass
    return data

def run_gen(log_file):
    log_data = parse_log(log_file)
    if not log_data: return None
    log_format = log_data[0].get('source_format', 'ukf_log')
    bias = {'ax': 0.0, 'ay': 0.0, 'gz': 0.0}
    fw_path = {'x': [], 'y': [], 'mask': []}
    tril_path = {'x': [], 'y': []}
    fp_logs = {'amp': [[], [], [], []], 'snr': [[], [], [], []]}
    for entry in log_data:
        if entry['type'] == 'Init':
            bias['ax'], bias['ay'], bias['gz'] = entry['ax'], entry['ay'], entry['gz']
        if entry['type'] == 'Update':
            fw_path['x'].append(entry['px_fw'])
            fw_path['y'].append(entry['py_fw'])
            fw_path['mask'].append(entry.get('mask', 15))
            if log_format == 'path_csv':
                tril_path['x'].append(entry.get('tril_x'))
                tril_path['y'].append(entry.get('tril_y'))
            for i in range(4):
                val_amp = entry['fp_amp_norm'][i] if len(entry.get('fp_amp_norm', [])) > i else 0
                val_snr = entry['fp_snr'][i] if len(entry.get('fp_snr', [])) > i else 0
                fp_logs['amp'][i].append(val_amp)
                fp_logs['snr'][i].append(val_snr)

    # --- GENERATE MINI THUMBNAIL (SVG) ---
    svg_content = ""
    if fw_path['x']:
        # Combine path and GT to find global bounds
        all_x = fw_path['x'] + GT_SQUARE['x']
        all_y = fw_path['y'] + GT_SQUARE['y']
        min_x, max_x = min(all_x), max(all_x)
        min_y, max_y = min(all_y), max(all_y)
        dx, dy = max_x - min_x, max_y - min_y
        
        # Scale to fit 50x50 box (1:1 Aspect Ratio)
        max_dim = max(dx, dy, 0.1)
        scale = 50 / max_dim
        
        # Centering offsets
        off_x = (50 - dx * scale) / 2
        off_y = (50 - dy * scale) / 2

        def to_svg(x_list, y_list):
            pts = []
            for i in range(len(x_list)):
                px = 5 + off_x + (x_list[i] - min_x) * scale
                py = 55 - off_y - (y_list[i] - min_y) * scale
                pts.append(f"{px:.1f},{py:.1f}")
            return " ".join(pts)

        gt_pts = to_svg(GT_SQUARE['x'], GT_SQUARE['y'])
        fw_pts = to_svg(fw_path['x'], fw_path['y'])
        
        svg_content = f"""
            <polyline points="{gt_pts}" fill="none" stroke="#fecaca" stroke-width="1.5" stroke-dasharray="2,2" />
            <polyline points="{fw_pts}" fill="none" stroke="#a78bfa" stroke-width="1.6" />
        """
    
    payload = {
        'log_format': log_format,
        'fw_path': fw_path,
        'tril_path': tril_path,
        'fp_logs': fp_logs,
        'all_entries': log_data,
        'biases': bias,
        'thumb_svg': f'<svg viewBox="0 0 60 60">{svg_content}</svg>'
    }
    return payload

def load_template():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    template_path = os.path.join(script_dir, 'template_ukf_prefilter.html')
    with open(template_path, 'r', encoding='utf-8') as f:
        return f.read()

def read_text_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()

def load_app_js_bundle():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parts = [
        'src/core/config.js',
        'src/core/math_utils.js',
        'src/view/plot_init.js',
        'src/view/plot_updater.js',
        'src/controller/ui_utils.js',
        'src/controller/ui_controller.js',
    ]
    return "\n\n".join(
        f"// ---- {part} ----\n" + read_text_file(os.path.join(script_dir, part))
        for part in parts
    )

def load_worker_js_bundle():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    worker = read_text_file(os.path.join(script_dir, 'src/workers/sim_worker.js'))
    worker = re.sub(r"^\s*importScripts\([^\n]+\);\s*\n", "", worker, count=1, flags=re.MULTILINE)
    parts = [
        'src/core/config.js',
        'src/core/math_utils.js',
        'src/filters/ukf_prefilter.js',
    ]
    prefix = "\n\n".join(
        f"// ---- {part} ----\n" + read_text_file(os.path.join(script_dir, part))
        for part in parts
    )
    return prefix + "\n\n// ---- src/workers/sim_worker.js ----\n" + worker

def get_report_source_mtime():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    paths = [
        'uwb-rtls_simulation.py',
        'custom_track_modified.xml',
        'template_ukf_prefilter.html',
        'src/core/config.js',
        'src/core/math_utils.js',
        'src/view/plot_init.js',
        'src/view/plot_updater.js',
        'src/controller/ui_utils.js',
        'src/controller/ui_controller.js',
        'src/workers/sim_worker.js',
        'src/filters/ukf_prefilter.js',
    ]
    return max(
        os.path.getmtime(os.path.join(script_dir, path))
        for path in paths
        if os.path.exists(os.path.join(script_dir, path))
    )

def render_template(template_content, filename, payload, ground_truths, app_js_bundle, worker_js_bundle):
    html = template_content.replace('__FILENAME__', filename)
    html = html.replace('__DATA_JSON__', json.dumps(payload))
    html = html.replace('__GROUND_TRUTHS_JSON__', json.dumps(ground_truths))
    html = html.replace('__APP_JS_BUNDLE__', app_js_bundle)
    html = html.replace('__SIM_WORKER_SOURCE_JSON__', json.dumps(worker_js_bundle))
    # Replace biases specifically
    html = html.replace('__BIAS_X__', f"{payload['biases']['ax']:.4f}")
    html = html.replace('__BIAS_Y__', f"{payload['biases']['ay']:.4f}")
    return html

def main():
    logs_data_dir = os.path.abspath(os.path.join(BASE_DIR, '..', 'logs_data'))
    search_dirs = [BASE_DIR]
    if os.path.isdir(logs_data_dir):
        search_dirs.append(logs_data_dir)

    logs = [
        os.path.join(root, f)
        for search_dir in search_dirs
        for root, _, files in os.walk(search_dir)
        for f in files
        if f.endswith('.csv') and ('ukf_log' in f or f.startswith('uwb_data_'))
    ]
    logs.sort(reverse=True)
    if not logs: return

    template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'template_ukf_prefilter.html')
    report_source_mtime = get_report_source_mtime() if os.path.exists(template_path) else 0
    template_content = load_template()
    ground_truths = load_ground_truths()
    app_js_bundle = load_app_js_bundle()
    worker_js_bundle = load_worker_js_bundle()

    files_generated = 0
    sim_results = []
    for lp in logs:
        try:
            fn  = os.path.basename(lp)
            rn  = fn.replace('.csv', '_sim.html')
            if os.path.abspath(lp).startswith(os.path.abspath(BASE_DIR)):
                rp = os.path.join(os.path.dirname(lp), rn)
            else:
                rp = os.path.join(BASE_DIR, 'logs_data_reports', rn)
            
            # Check if we need to regenerate
            log_mtime = os.path.getmtime(lp)
            html_exists = os.path.exists(rp)
            html_mtime = os.path.getmtime(rp) if html_exists else 0
            
            needs_gen = not html_exists or log_mtime > html_mtime or report_source_mtime > html_mtime
            
            p = run_gen(lp)
            if not p: continue
            
            if needs_gen:
                os.makedirs(os.path.dirname(rp), exist_ok=True)
                with open(rp, 'w', encoding='utf-8') as f:
                    f.write(render_template(template_content, fn, p, ground_truths, app_js_bundle, worker_js_bundle))
                files_generated += 1
                
            num_updates = len([e for e in p['all_entries'] if e['type'] == 'Update'])
            sim_results.append({
                'name': fn,
                'path': os.path.relpath(rp, BASE_DIR).replace('\\', '/'),
                'samples': num_updates,
                'thumb': p['thumb_svg']
            })
        except Exception as e:
            import traceback
            traceback.print_exc()


    # Group results by folder (date)
    grouped_results = {}
    for r in sim_results:
        folder = os.path.dirname(r['path'])
        if not folder or folder == ".": folder = "Root"
        if folder not in grouped_results:
            grouped_results[folder] = []
        grouped_results[folder].append(r)

    # Sort groups by name (date) descending
    sorted_folders = sorted(grouped_results.keys(), reverse=True)

    html_sections = []
    cache_token = str(int(report_source_mtime))
    for folder in sorted_folders:
        items = grouped_results[folder]
        items_html = "".join([
            f'<a href="{r["path"]}?v={cache_token}" class="log-item">'
            f'  <div style="display:flex;align-items:center;gap:15px;">'
            f'    <div class="thumb">{r["thumb"]}</div>'
            f'    <span>{r["name"]}</span>'
            f'  </div>'
            f'  <span class="sample-count">{r["samples"]} samples</span>'
            f'</a>'
            for r in items
        ])
        section = f"""
        <div class="date-group">
            <div class="date-header">{folder}</div>
            <div class="log-list">
                {items_html}
            </div>
        </div>
        """
        html_sections.append(section)

    list_html = "\n".join(html_sections)

    dashboard_html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>UWB-RTLS Simulation</title>
    <style>
        body {{ 
            font-family: 'Inter', -apple-system, sans-serif; 
            padding: 60px; 
            background: #f8fafc; 
            color: #1e293b;
            line-height: 1.5;
        }}
        .container {{ 
            max-width: 900px; 
            margin: 0 auto; 
            background: white; 
            padding: 40px; 
            border-radius: 16px; 
            box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1); 
        }}
        h1 {{ margin-top: 0; color: #1e293b; font-size: 2.25rem; }}
        p {{ color: #64748b; margin-bottom: 30px; }}
        .date-group {{ margin-bottom: 30px; }}
        .date-header {{ 
            background: #f1f5f9; 
            padding: 10px 20px; 
            font-weight: bold; 
            color: #475569; 
            border-radius: 8px 8px 0 0;
            border: 1px solid #e2e8f0;
            border-bottom: none;
            text-transform: uppercase;
            font-size: 0.85rem;
            letter-spacing: 0.05em;
        }}
        .log-list {{ 
            border: 1px solid #e2e8f0; 
            border-radius: 0 0 8px 8px; 
            overflow: hidden; 
        }}
        .log-item {{ 
            display: flex; 
            justify-content: space-between; 
            padding: 15px 20px; 
            border-bottom: 1px solid #f1f5f9; 
            text-decoration: none; 
            color: #7c3aed; 
            font-weight: 500;
            transition: background 0.2s;
            background: white;
        }}
        .log-item:hover {{ background: #f8fafc; }}
        .log-item:last-child {{ border-bottom: none; }}
        .thumb {{ 
            width: 50px; 
            height: 50px; 
            background: #f8fafc; 
            border: 1px solid #f1f5f9;
            border-radius: 6px; 
            display: flex; 
            align-items: center; 
            justify-content: center;
        }}
        .thumb svg {{ width: 100%; height: 100%; }}
        .sample-count {{ color: #64748b; font-weight: normal; font-size: 0.9rem; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>UWB-RTLS Simulation</h1>
        <p>Select a simulation log file to start real-time tuning and analysis.</p>
        {list_html}
    </div>
</body>
</html>
"""
    with open(os.path.join(BASE_DIR, "simulation_dashboard.html"), 'w', encoding='utf-8') as f:
        f.write(dashboard_html)
    
    if files_generated > 0:
        print(f"\n[SUCCESS] Generated {files_generated} new simulation files.")
    else:
        print(f"\n[INFO] All {len(sim_results)} simulation files are up to date.")
    print(f"[INFO] Dashboard: {os.path.join(BASE_DIR, 'simulation_dashboard.html')}")

    # --- AUTO SERVER & BROWSER ---
    PORT = 8000
    MAX_TRIES = 10

    class NoCacheHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
        def end_headers(self):
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            super().end_headers()

    Handler = NoCacheHTTPRequestHandler
    
    # Change directory to BASE_DIR to serve files correctly
    os.chdir(BASE_DIR)

    httpd = None
    for attempt in range(MAX_TRIES):
        try:
            # Set allow_reuse_address to True to help with quick restarts
            socketserver.TCPServer.allow_reuse_address = True
            httpd = socketserver.TCPServer(("", PORT), Handler)
            break
        except OSError:
            print(f"[WARNING] Port {PORT} is busy, trying next...")
            PORT += 1
    
    if not httpd:
        print("[ERROR] Could not find an available port. Please close some applications.")
        sys.exit(1)

    def start_server(server):
        print(f"[SERVER] Running at http://localhost:{PORT}")
        print("[INFO] Press Ctrl+C to stop the server.")
        server.serve_forever()

    # Start server in a separate thread
    thread = threading.Thread(target=start_server, args=(httpd,))
    thread.daemon = True
    thread.start()

    # Open the dashboard in the default browser
    webbrowser.open(f"http://localhost:{PORT}/simulation_dashboard.html")
    
    # Keep the main thread alive so the server continues to run
    try:
        while True:
            import time
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[INFO] Server stopped by user.")
        sys.exit(0)

if __name__ == "__main__":
    main()
