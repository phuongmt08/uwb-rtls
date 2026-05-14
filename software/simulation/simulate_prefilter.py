import os
import re
import json
import math
import sys
import datetime

# --- CONFIGURATION ---
BASE_DIR = r"c:\Users\USER\Desktop\final_project\uwb-rtls\software\simulation"
ANCHORS = [
    {'id': 1, 'x': 0.0,  'y': 0.0,  'z': 0.405},
    {'id': 2, 'x': 9.76, 'y': 0.0,  'z': 0.405},
    {'id': 3, 'x': 0.0,  'y': 9.76, 'z': 0.405},
    {'id': 4, 'x': 9.76, 'y': 9.76, 'z': 0.405},
]
TAG_HEIGHT = 0.435
GT_SQUARE = {
    'x': [2.44, 7.32, 7.32, 2.44, 2.44],
    'y': [2.44, 2.44, 7.32, 7.32, 2.44]
}

def parse_log(filepath):
    data = []
    pattern = re.compile(r"(Update|Init|Predict)\s+ax:\s*([\d.-]+)\s+ay:\s*([\d.-]+)\s+gz:\s*([\d.-]+)\s+px:\s*([\d.-]+)\s+py:\s*([\d.-]+)\s+dt:\s*([\d.-]+)\s+(?:mask:\s*(\d+)\s+)?d1:\s*([\d.-]+)\s+d2:\s*([\d.-]+)\s+d3:\s*([\d.-]+)\s+d4:\s*([\d.-]+)\s+err:\s*(\d+)")
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line_no, line in enumerate(f, 1):
                raw_line = line.rstrip('\r\n')
                m = pattern.search(line)
                if m:
                    v = m.groups()[1:]
                    data.append({
                        'line_no': line_no,
                        'raw_line': raw_line,
                        'type': m.group(1),
                        'ax': float(v[0]), 'ay': float(v[1]), 'gz': float(v[2]),
                        'px_fw': float(v[3]), 'py_fw': float(v[4]), 'dt': float(v[5]),
                        'mask': int(v[6]) if v[6] is not None else 15,
                        'distances': [float(v[7]), float(v[8]), float(v[9]), float(v[10])],
                        'err': int(v[11])
                    })
    except: pass
    return data

def run_gen(log_file):
    log_data = parse_log(log_file)
    if not log_data: return None
    bias = {'ax': 0.0, 'ay': 0.0, 'gz': 0.0}
    fw_path = {'x': [], 'y': [], 'mask': []}
    for entry in log_data:
        if entry['type'] == 'Init':
            bias['ax'], bias['ay'], bias['gz'] = entry['ax'], entry['ay'], entry['gz']
        if entry['type'] == 'Update':
            fw_path['x'].append(entry['px_fw'])
            fw_path['y'].append(entry['py_fw'])
            fw_path['mask'].append(entry.get('mask', 15))

    payload = {
        'fw_path': fw_path,
        'all_entries': log_data,
        'biases': bias
    }
    return payload

# ─────────────────────────────────────────────────────────────────────────────
# v4.9 — Fixed Mahalanobis pre-filter
#
# Root-cause fixes vs v4.8:
#   BUG 1 — d_pred was computed from firmware UKF position (curr_px/curr_py).
#            Firmware position is tainted by unfiltered distances → circular
#            dependency.  Fix: d_pred = median of the CLEAN accepted-history
#            for each anchor (no position involved at all).
#
#   BUG 2 — Gate compared d_med (median of raw window) vs d_pred.  A single
#            spike is absorbed by the median → spike survives; stable region
#            vs drifted d_pred → stable region gets rejected.
#            Fix: gate on RAW d vs clean-history median.
#
#   BUG 3 — Outlier was pushed into histories[] BEFORE gating, so it
#            contaminated the next 5 predictions even after being rejected.
#            Fix: push to histories[] ONLY when the reading passes the gate.
#
#   REMOVED — k_pos * d_pred² term (amplified position error, no longer needed
#              because d_pred is now self-consistent with the history).
# ─────────────────────────────────────────────────────────────────────────────

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

    sim_results = []
    for lp in logs:
        try:
            p = run_gen(lp)
            if not p: continue
            fn  = os.path.basename(lp)
            rn  = fn.replace('.csv', '_sim.html')
            rp  = os.path.join(os.path.dirname(lp), rn)
            with open(rp, 'w', encoding='utf-8') as f:
                template = load_template()
                html = template.replace('__FILENAME__', fn)
                html = html.replace('__DATA_JSON__', json.dumps(p))
                html = html.replace('__ANCHORS_JSON__', json.dumps(ANCHORS))
                html = html.replace('__GT_SQUARE_JSON__', json.dumps(GT_SQUARE))
                html = html.replace('__TAG_HEIGHT__', str(TAG_HEIGHT))
                html = html.replace('__BIAS_X__', str(p['biases']['ax']))
                html = html.replace('__BIAS_Y__', str(p['biases']['ay']))
                f.write(html)
            num_updates = len([e for e in p['all_entries'] if e['type'] == 'Update'])
            sim_results.append({
                'name': fn,
                'path': os.path.relpath(rp, BASE_DIR).replace('\\', '/'),
                'samples': num_updates
            })
        except Exception as e:
            import traceback
            traceback.print_exc()

    list_items = "".join([
        f'<a href="{r["path"]}" style="display:flex;justify-content:space-between;'
        f'padding:15px;border-bottom:1px solid #f1f5f9;text-decoration:none;'
        f'color:#2563eb;font-weight:500;">'
        f'<span>{r["name"]}</span>'
        f'<span style="color:#64748b;font-weight:normal;">{r["samples"]} samples</span></a>'
        for r in sim_results
    ])

    dashboard_html = (
        "<html><body style='font-family:sans-serif;padding:60px;background:#f8fafc;'>"
        "<div style='max-width:900px;margin:0 auto;background:white;padding:40px;"
        "border-radius:16px;box-shadow:0 10px 15px -3px rgba(0,0,0,0.1);'>"
        "<h1 style='margin-top:0; color:#1e293b;'>UWB Pro-Tuning Dashboard</h1>"
        "<p style='color:#64748b; margin-bottom:30px;'>Select a log file to start real-time pre-filter tuning.</p>"
        f"{list_items}"
        "</div></body></html>"
    )
    with open(os.path.join(BASE_DIR, "simulation_dashboard.html"), 'w', encoding='utf-8') as f:
        f.write(dashboard_html)

if __name__ == "__main__":
    main()
