import os
import re
import json
import math
import sys
import datetime
import http.server
import socketserver
import webbrowser
import threading

# --- CONFIGURATION ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GT_SQUARE = {
    'x': [2.44, 7.32, 7.32, 2.44, 2.44],
    'y': [2.44, 2.44, 7.32, 7.32, 2.44]
}

def parse_log(filepath):
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
        (?:fp_amp_norm:\s*\[(?P<amp>[\d.,\s]+)\])? # Amplitude (Optional)
        \s*
        (?:fp_snr:\s*\[(?P<snr>[\d.,\s]+)\])?      # SNR (Optional)
        \s*
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

                    data.append({
                        'line_no': line_no,
                        'raw_line': raw_line,
                        'type': d['type'],
                        'ax': float(d['ax']), 'ay': float(d['ay']), 'gz': float(d['gz']),
                        'px_fw': float(d['px']), 'py_fw': float(d['py']), 'dt': float(d['dt']),
                        'fp_amp_norm': parse_float_list(d.get('amp')),
                        'fp_snr': parse_float_list(d.get('snr')),
                        'mask': int(d['mask']) if d.get('mask') else 15,
                        'distances': [float(d['d1']), float(d['d2']), float(d['d3']), float(d['d4'])],
                        'err': int(d['err'])
                    })
    except: pass
    return data

def run_gen(log_file):
    log_data = parse_log(log_file)
    if not log_data: return None
    bias = {'ax': 0.0, 'ay': 0.0, 'gz': 0.0}
    fw_path = {'x': [], 'y': [], 'mask': []}
    fp_logs = {'amp': [[], [], [], []], 'snr': [[], [], [], []]}
    for entry in log_data:
        if entry['type'] == 'Init':
            bias['ax'], bias['ay'], bias['gz'] = entry['ax'], entry['ay'], entry['gz']
        if entry['type'] == 'Update':
            fw_path['x'].append(entry['px_fw'])
            fw_path['y'].append(entry['py_fw'])
            fw_path['mask'].append(entry.get('mask', 15))
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
            <polyline points="{fw_pts}" fill="none" stroke="#2563eb" stroke-width="1.2" />
        """
    
    payload = {
        'fw_path': fw_path,
        'fp_logs': fp_logs,
        'all_entries': log_data,
        'biases': bias,
        'thumb_svg': f'<svg viewBox="0 0 60 60">{svg_content}</svg>'
    }
    return payload

def load_template():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    template_path = os.path.join(script_dir, 'template_prefilter.html')
    with open(template_path, 'r', encoding='utf-8') as f:
        return f.read()


def main():
    logs = [
        os.path.join(root, f)
        for root, _, files in os.walk(BASE_DIR)
        for f in files
        if f.endswith('.csv') and 'ukf_log' in f
    ]
    logs.sort(reverse=True)
    if not logs: return

    template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'template_prefilter.html')
    template_mtime = os.path.getmtime(template_path) if os.path.exists(template_path) else 0
    template_content = load_template()

    files_generated = 0
    sim_results = []
    for lp in logs:
        try:
            fn  = os.path.basename(lp)
            rn  = fn.replace('.csv', '_sim.html')
            rp  = os.path.join(os.path.dirname(lp), rn)
            
            # Check if we need to regenerate
            log_mtime = os.path.getmtime(lp)
            html_exists = os.path.exists(rp)
            html_mtime = os.path.getmtime(rp) if html_exists else 0
            
            needs_gen = not html_exists or log_mtime > html_mtime or template_mtime > html_mtime
            
            p = run_gen(lp)
            if not p: continue
            
            if needs_gen:
                with open(rp, 'w', encoding='utf-8') as f:
                    html = template_content.replace('__FILENAME__', fn)
                    html = html.replace('__DATA_JSON__', json.dumps(p))
                    f.write(html)
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
    for folder in sorted_folders:
        items = grouped_results[folder]
        items_html = "".join([
            f'<a href="{r["path"]}" class="log-item">'
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
    <title>UWB Pro-Tuning Simulation</title>
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
            color: #2563eb; 
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
        <h1>UWB Pro-Tuning Simulation</h1>
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
    Handler = http.server.SimpleHTTPRequestHandler
    
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
