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
    'y': [2.50, 2.50, 7.38, 7.38, 2.50]
}

def parse_log(filepath):
    data = []
    pattern = re.compile(r"(Update|Init|Predict)\s+ax:\s*([\d.-]+)\s+ay:\s*([\d.-]+)\s+gz:\s*([\d.-]+)\s+px:\s*([\d.-]+)\s+py:\s*([\d.-]+)\s+dt:\s*([\d.-]+)\s+d1:\s*([\d.-]+)\s+d2:\s*([\d.-]+)\s+d3:\s*([\d.-]+)\s+d4:\s*([\d.-]+)\s+err:\s*(\d+)")
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                m = pattern.search(line)
                if m:
                    v = [float(x) for x in m.groups()[1:]]
                    data.append({
                        'type': m.group(1),
                        'ax': v[0], 'ay': v[1], 'gz': v[2],
                        'dt': v[5], 'px_fw': v[3], 'py_fw': v[4],
                        'distances': [v[6], v[7], v[8], v[9]],
                        'err': int(m.group(12))
                    })
    except: pass
    return data

def run_gen(log_file):
    log_data = parse_log(log_file)
    if not log_data: return None
    bias = {'ax': 0.0, 'ay': 0.0, 'gz': 0.0}
    fw_path = {'x': [], 'y': []}
    for entry in log_data:
        if entry['type'] == 'Init':
            bias['ax'], bias['ay'], bias['gz'] = entry['ax'], entry['ay'], entry['gz']
        if entry['type'] == 'Update':
            fw_path['x'].append(entry['px_fw'])
            fw_path['y'].append(entry['py_fw'])

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

HTML_V49_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>UWB Debug v4.9 (Fixed Gate): {filename}</title>
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <style>
        body {{ font-family: -apple-system, sans-serif; margin: 0; background: #f1f5f9; color: #1e293b; padding: 20px; }}
        .card {{ background: white; padding: 20px; border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); margin-bottom: 20px; }}
        .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }}
        .controls {{ display: flex; gap: 20px; background: white; padding: 15px 30px; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); flex-wrap: wrap; }}
        .control-item {{ display: flex; flex-direction: column; gap: 5px; }}
        .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
        label {{ font-size: 0.75rem; font-weight: bold; color: #64748b; text-transform: uppercase; }}
        input[type="range"] {{ width: 180px; }}
        .val {{ color: #2563eb; font-weight: bold; }}
        .plot-lg {{ height: 500px; }}
        .plot-md {{ height: 400px; }}
        .plot-title {{ font-size: 1rem; color: #1e293b; margin: 0 0 10px 0; border-left: 4px solid #2563eb; padding-left: 10px; }}
        .badge {{ font-size: 0.7rem; background: #dcfce7; color: #166534; padding: 2px 8px; border-radius: 99px; font-weight: bold; margin-left: 8px; }}
    </style>
</head>
<body>
    <div class="header">
        <div>
            <h2 style="margin:0;">{filename} <span class="badge">v4.9 Fixed Gate</span></h2>
            <p style="margin:5px 0 0; font-size:0.8rem; color:#64748b;">Init Bias - X: {bias_x}, Y: {bias_y}</p>
        </div>
        <div class="controls">
            <div class="control-item">
                <label>T2 Reject (High): <span id="t2_high_val" class="val">9.0</span></label>
                <div style="display:flex; gap:10px; align-items:center;">
                    <input type="range" id="t2_high_range" min="1" max="40" step="0.5" value="9.0">
                    <input type="number" id="t2_high_input" step="0.5" style="width:60px; padding:4px; border:1px solid #cbd5e1; border-radius:8px;" value="9.0">
                </div>
            </div>
            <div class="control-item">
                <label>T2 Recover (Low): <span id="t2_low_val" class="val">4.0</span></label>
                <div style="display:flex; gap:10px; align-items:center;">
                    <input type="range" id="t2_low_range" min="1" max="40" step="0.5" value="4.0">
                    <input type="number" id="t2_low_input" step="0.5" style="width:60px; padding:4px; border:1px solid #cbd5e1; border-radius:8px;" value="4.0">
                </div>
            </div>
            <div class="control-item">
                <label>R_base (Noise Floor): <span id="r_val" class="val">0.05</span></label>
                <div style="display:flex; gap:10px; align-items:center;">
                    <input type="range" id="r_range" min="0.01" max="0.5" step="0.01" value="0.05">
                    <input type="number" id="r_input" step="0.01" style="width:60px; padding:4px; border:1px solid #cbd5e1; border-radius:8px;" value="0.05">
                </div>
            </div>
            <div class="control-item">
                <label>History Window: <span id="win_val" class="val">15</span></label>
                <div style="display:flex; gap:10px; align-items:center;">
                    <input type="range" id="win_range" min="3" max="30" step="1" value="15">
                    <input type="number" id="win_input" step="1" style="width:60px; padding:4px; border:1px solid #cbd5e1; border-radius:8px;" value="15">
                </div>
            </div>
            <div class="control-item">
                <label>ZUPT Accel: <span id="zupt_acc_val" class="val">0.15</span></label>
                <div style="display:flex; gap:10px; align-items:center;">
                    <input type="range" id="zupt_acc_range" min="0.01" max="1.0" step="0.01" value="0.15">
                    <input type="number" id="zupt_acc_input" step="0.01" style="width:60px; padding:4px; border:1px solid #cbd5e1; border-radius:8px;" value="0.15">
                </div>
            </div>
            <div class="control-item">
                <label>ZUPT Gyro: <span id="zupt_gyr_val" class="val">0.05</span></label>
                <div style="display:flex; gap:10px; align-items:center;">
                    <input type="range" id="zupt_gyr_range" min="0.01" max="0.5" step="0.01" value="0.05">
                    <input type="number" id="zupt_gyr_input" step="0.01" style="width:60px; padding:4px; border:1px solid #cbd5e1; border-radius:8px;" value="0.05">
                </div>
            </div>
            <div class="control-item">
                <label>Max Samples: <span id="max_samples_val" class="val">All</span></label>
                <div style="display:flex; gap:10px; align-items:center;">
                    <input type="range" id="max_samples_range" min="10" max="100000" step="10" value="10000">
                    <input type="number" id="max_samples_input" step="10" style="width:80px; padding:4px; border:1px solid #cbd5e1; border-radius:8px;" value="10000">
                </div>
            </div>
            <a href="../simulation_dashboard.html" style="padding: 10px 20px; background: #e2e8f0; border-radius: 8px; text-decoration: none; color: #475569; font-weight: bold; font-size: 0.8rem; height: fit-content; align-self: center;">&larr; Dashboard</a>
        </div>
    </div>

    <div class="grid-2">
        <div class="card"><h3 class="plot-title">Trajectory Comparison</h3><div id="trajectory" class="plot-lg"></div></div>
        <div class="card"><h3 class="plot-title">Distance Tracking</h3><div id="distances" class="plot-lg"></div></div>
    </div>
    <div class="card"><h3 class="plot-title">Mahalanobis D2 Scores (gate on raw d vs clean-history median)</h3><div id="scores" class="plot-md"></div></div>
    <div class="card"><h3 class="plot-title">IMU Acceleration</h3><div id="accel" class="plot-md"></div></div>
    <div class="card"><h3 class="plot-title">Integrated Velocity</h3><div id="velocity" class="plot-md"></div></div>
    <div class="card"><h3 class="plot-title">IMU Rotation (Gyro & Yaw)</h3><div id="yaw_plot" class="plot-md"></div></div>
    <div class="card"><h3 class="plot-title">Positioning Error (vs Ground Truth)</h3><div id="pos_error" class="plot-md"></div></div>
    <div class="card"><h3 class="plot-title">Log Error Frames (CSV)</h3><div id="error_frame" class="plot-md"></div></div>

    <script>
        const rawData = {data_json}, anchors = {anchors_json}, gt_square = {gt_square_json}, tagHeight = {tag_height};
        const samples = rawData.all_entries.filter(e => e.type === 'Update');
        
        document.getElementById('max_samples_range').max = rawData.all_entries.length;
        document.getElementById('max_samples_range').value = rawData.all_entries.length;
        document.getElementById('max_samples_input').value = rawData.all_entries.length;

        function trilaterate(vAnchors) {{
            if (vAnchors.length < 3) return null;
            const [a1, a2, a3] = vAnchors;
            const d = 4*((a1.x-a2.x)*(a1.y-a3.y)-(a1.x-a3.x)*(a1.y-a2.y));
            if (Math.abs(d) < 0.001) return null;
            const A = a2.r**2-a1.r**2-a2.x**2+a1.x**2-a2.y**2+a1.y**2,
                  B = a3.r**2-a1.r**2-a3.x**2+a1.x**2-a3.y**2+a1.y**2;
            return {{ x: (1/d)*(2*A*(a1.y-a3.y)-2*B*(a1.y-a2.y)),
                      y: (1/d)*(2*B*(a1.x-a2.x)-2*A*(a1.x-a3.x)) }};
        }}

        function update() {{
            const T2_high    = parseFloat(document.getElementById('t2_high_range').value);
            const T2_low     = parseFloat(document.getElementById('t2_low_range').value);
            const R_base     = parseFloat(document.getElementById('r_range').value);
            const WIN        = parseInt(document.getElementById('win_range').value);
            const zupt_acc   = parseFloat(document.getElementById('zupt_acc_range').value);
            const zupt_gyr   = parseFloat(document.getElementById('zupt_gyr_range').value);
            let max_samples  = parseInt(document.getElementById('max_samples_range').value);

            if (isNaN(max_samples)) max_samples = rawData.all_entries.length;

            document.getElementById('t2_high_val').innerText = T2_high;
            document.getElementById('t2_low_val').innerText  = T2_low;
            document.getElementById('r_val').innerText       = R_base;
            document.getElementById('win_val').innerText     = WIN;
            document.getElementById('zupt_acc_val').innerText = zupt_acc;
            document.getElementById('zupt_gyr_val').innerText = zupt_gyr;
            const maxRangeElem = document.getElementById('max_samples_range');
            document.getElementById('max_samples_val').innerText = (max_samples >= parseInt(maxRangeElem.max)) ? "All" : max_samples;

            const bias = rawData.biases;

            // histories[i] = CLEAN accepted-only sliding window per anchor
            const histories = [[], [], [], []];
            const gatedDist = [[], [], [], []];
            const d2Scores  = [[], [], [], []];
            const rejectIdx = [[], [], [], []];
            const is_rejected = [false, false, false, false];

            let v_clean = {{ x: 0, y: 0 }}, yaw = 0, zupt_cnt = 0;
            const simPath  = {{ x: [], y: [] }};

            // k_vel: velocity uncertainty (m/s → variance contribution)
            // k_pos REMOVED — was the root bug (depended on firmware position)
            const k_vel = 0.5;

            const plotData = {{ vx: [], vy: [], zupt: [], ax: [], ay: [], gz: [], yaw: [], times: [] }};
            let sampleIdx = 0, total_time = 0;
            let last_ax = 0, last_ay = 0, last_gz = 0;

            const entriesToProcess = rawData.all_entries.slice(0, max_samples);
            entriesToProcess.forEach((entry) => {{
                if (entry.type === 'Init') {{
                    last_ax = entry.ax; last_ay = entry.ay; last_gz = entry.gz;
                }}
                if (entry.dt > 0) total_time += entry.dt;

                // ── IMU predict step ─────────────────────────────────────
                if (entry.type === 'Predict' && entry.dt > 0) {{
                    last_ax = entry.ax; last_ay = entry.ay; last_gz = entry.gz;
                    v_clean.x += (entry.ax - bias.ax) * entry.dt;
                    v_clean.y += (entry.ay - bias.ay) * entry.dt;
                    v_clean.x *= 0.98; v_clean.y *= 0.98;
                    yaw += (entry.gz - bias.gz) * entry.dt;

                    const acc_mag = Math.sqrt((entry.ax - bias.ax)**2 + (entry.ay - bias.ay)**2);
                    const gyr_mag = Math.abs(entry.gz - bias.gz);
                    if (acc_mag < zupt_acc && gyr_mag < zupt_gyr) zupt_cnt++; else zupt_cnt = 0;
                    if (zupt_cnt > 10) {{ v_clean.x = 0; v_clean.y = 0; }}
                }}

                // ── UWB update step ──────────────────────────────────────
                if (entry.type === 'Update') {{
                    let v_anchors = [];

                    entry.distances.forEach((d, i) => {{
                        const anc = anchors[i];

                        // Skip clearly invalid readings
                        if (d <= 0.1) {{
                            gatedDist[i].push(null);
                            d2Scores[i].push(0);
                            return;
                        }}

                        const hist = histories[i];

                        // ── Cold-start: warm up history without gating ────
                        if (hist.length < 3) {{
                            hist.push(d);
                            gatedDist[i].push(d);
                            d2Scores[i].push(0);
                            const r2d = Math.sqrt(Math.max(0, d**2 - (anc.z - tagHeight)**2));
                            v_anchors.push({{ x: anc.x, y: anc.y, r: r2d }});
                            return;
                        }}

                        const sorted  = [...hist].sort((a, b) => a - b);
                        const d_pred  = sorted[Math.floor(sorted.length / 2)];
                        const mean    = hist.reduce((s, v) => s + v, 0) / hist.length;
                        const variance= hist.reduce((s, v) => s + (v - mean)**2, 0) / hist.length;
                        const vel_mag = Math.sqrt(v_clean.x**2 + v_clean.y**2);
                        const S       = Math.max(variance, R_base) + (k_vel * vel_mag);

                        const d2 = ((d - d_pred)**2) / S;
                        d2Scores[i].push(d2);

                        let pass = false;
                        if (is_rejected[i]) {{
                            if (d2 < T2_low) {{
                                is_rejected[i] = false;
                                pass = true;
                            }}
                        }} else {{
                            if (d2 > T2_high) {{
                                is_rejected[i] = true;
                            }} else {{
                                pass = true;
                            }}
                        }}

                        if (pass) {{
                            hist.push(d);
                            if (hist.length > WIN) hist.shift();
                            gatedDist[i].push(d);
                            const r2d = Math.sqrt(Math.max(0, d**2 - (anc.z - tagHeight)**2));
                            v_anchors.push({{ x: anc.x, y: anc.y, r: r2d }});
                        }} else {{
                            gatedDist[i].push(null);
                            rejectIdx[i].push(sampleIdx);
                        }}
                    }});

                    const pos = trilaterate(v_anchors);
                    simPath.x.push(pos ? pos.x : null);
                    simPath.y.push(pos ? pos.y : null);

                    plotData.vx.push(v_clean.x);
                    plotData.vy.push(v_clean.y);
                    plotData.zupt.push(zupt_cnt > 10 ? 0.1 : 0);
                    plotData.ax.push(last_ax - bias.ax);
                    plotData.ay.push(last_ay - bias.ay);
                    plotData.gz.push(last_gz - bias.gz);
                    plotData.yaw.push(yaw * 180 / Math.PI);
                    plotData.times.push(total_time);
                    sampleIdx++;
                }}
            }});

            const x_axis    = simPath.x.map((_, i) => i);
            const rej_indices = (i) => rejectIdx[i];
            const times     = plotData.times;
            const pos_errors = [];

            Plotly.restyle('trajectory', {{ 
                x: [rawData.fw_path.x.slice(0, x_axis.length), simPath.x], 
                y: [rawData.fw_path.y.slice(0, x_axis.length), simPath.y] 
            }}, [2, 3]);

            for (let i = 0; i < 4; i++) {{
                Plotly.restyle('distances', {{
                    x: [x_axis, x_axis, rej_indices(i)],
                    y: [samples.slice(0, x_axis.length).map(s => s.distances[i]), gatedDist[i], rejectIdx[i].map(idx => samples[idx].distances[i])],
                    customdata: [times, times, rejectIdx[i].map(idx => times[idx])],
                    hovertemplate: 'Time: %{{customdata:.4f}}s | %{{y:.6f}}m<extra></extra>'
                }}, [i*3, i*3+1, i*3+2]);

                Plotly.restyle('scores', {{
                    x: [x_axis], y: [d2Scores[i]],
                    customdata: [times],
                    hovertemplate: 'Time: %{{customdata:.4f}}s | D2: %{{y:.6f}}<extra></extra>'
                }}, [i]);
            }}
            Plotly.restyle('scores', {{ 
                x: [[0, x_axis.length], [0, x_axis.length]], 
                y: [[T2_high, T2_high], [T2_low, T2_low]] 
            }}, [4, 5]);

            Plotly.restyle('accel', {{
                x: [x_axis, x_axis], y: [plotData.ax, plotData.ay],
                customdata: [times, times],
                hovertemplate: 'Time: %{{customdata:.4f}}s | %{{y:.6f}}<extra></extra>'
            }}, [0, 1]);

            Plotly.restyle('velocity', {{
                x: [x_axis, x_axis, x_axis, x_axis, x_axis],
                y: [[], [], plotData.vx, plotData.vy, plotData.zupt],
                customdata: [[], [], times, times, times],
                hovertemplate: 'Time: %{{customdata:.4f}}s | %{{y:.6f}}<extra></extra>'
            }}, [0, 1, 2, 3, 4]);

            Plotly.restyle('yaw_plot', {{
                x: [x_axis, x_axis], y: [plotData.gz, plotData.yaw],
                customdata: [times, times],
                hovertemplate: 'Time: %{{customdata:.4f}}s | %{{y:.6f}}<extra></extra>'
            }}, [0, 1]);

            simPath.x.forEach((px, i) => {{
                if (px === null) {{ pos_errors.push(null); return; }}
                const py = simPath.y[i];
                let min_d = 999;
                for (let j = 0; j < gt_square.x.length - 1; j++) {{
                    const x1 = gt_square.x[j], y1 = gt_square.y[j],
                          x2 = gt_square.x[j+1], y2 = gt_square.y[j+1];
                    const l2 = (x2-x1)**2 + (y2-y1)**2;
                    let t = ((px-x1)*(x2-x1) + (py-y1)*(y2-y1)) / l2;
                    t = Math.max(0, Math.min(1, t));
                    const dx = px - (x1 + t*(x2-x1)), dy = py - (y1 + t*(y2-y1));
                    min_d = Math.min(min_d, Math.sqrt(dx*dx + dy*dy));
                }}
                pos_errors.push(min_d);
            }});
            Plotly.restyle('pos_error', {{
                x: [x_axis], y: [pos_errors],
                customdata: [times],
                hovertemplate: 'Time: %{{customdata:.4f}}s | Err: %{{y:.6f}}m<extra></extra>'
            }}, [0]);

            const csv_errors = samples.slice(0, x_axis.length).map(s => s.err);
            Plotly.restyle('error_frame', {{
                x: [x_axis], y: [csv_errors],
                customdata: [times],
                hovertemplate: 'Time: %{{customdata:.4f}}s | Frames: %{{y:.0f}}<extra></extra>'
            }}, [0]);

            const syncLayout = {{
                'xaxis.range':  [0, x_axis.length],
                'xaxis2.range': [0, total_time],
                'xaxis2.showticklabels': true,
                'xaxis2.autorange': false
            }};
            ['distances', 'scores', 'accel', 'velocity', 'yaw_plot', 'pos_error', 'error_frame']
                .forEach(id => Plotly.relayout(id, syncLayout));
        }}

        const colors = ['#2563eb', '#16a34a', '#d97706', '#7c3aed'];

        Plotly.newPlot('trajectory', [
            {{ x: anchors.map(a => a.x), y: anchors.map(a => a.y), mode: 'markers+text',
               name: 'Anchors', text: anchors.map(a => 'A'+a.id),
               marker: {{ symbol: 'triangle-up', size: 12, color: '#1e293b' }} }},
            {{ x: gt_square.x, y: gt_square.y, mode: 'lines', name: 'Ground Truth',
               line: {{ dash: 'dot', color: '#ef4444', width: 2 }}, opacity: 0.4 }},
            {{ x: rawData.fw_path.x, y: rawData.fw_path.y, mode: 'lines',
               name: 'Firmware Path', line: {{ color: '#94a3b8', width: 1 }},
               text: samples.map((_, i) => i) }},
            {{ x: [], y: [], mode: 'lines+markers', name: 'Simulated Path',
               marker: {{ size: 4, color: '#2563eb' }}, line: {{ width: 2 }} }}
        ], {{
            xaxis: {{ scaleanchor: 'y', title: 'X (m)' }},
            yaxis: {{ title: 'Y (m)' }},
            hovermode: 'closest', margin: {{ t: 20 }}
        }});

        const dTraces = [];
        for (let i = 0; i < 4; i++) {{
            dTraces.push({{ x: samples.map((_, idx) => idx),
                            y: samples.map(s => s.distances[i]),
                            name: 'A'+(i+1)+' Raw', mode: 'lines',
                            line: {{ width: 1, color: colors[i], opacity: 0.15 }} }});
            dTraces.push({{ x: [], y: [], name: 'A'+(i+1)+' Gated', mode: 'lines',
                            line: {{ width: 2, color: colors[i] }} }});
            dTraces.push({{ x: [], y: [], name: 'REJECTED', legendgroup: 'rej',
                            showlegend: (i === 0), mode: 'markers',
                            marker: {{ symbol: 'x', color: '#ef4444', size: 6 }} }});
        }}
        dTraces.push({{ x: [0, 100], y: [null], xaxis: 'x2', showlegend: false, hoverinfo: 'none' }});
        Plotly.newPlot('distances', dTraces, {{
            margin: {{ t: 40, b: 40 }},
            xaxis:  {{ title: 'Sample Index', side: 'bottom' }},
            xaxis2: {{ title: 'Time (s)', overlaying: 'x', side: 'top',
                       showticklabels: true, showline: true }},
            yaxis:  {{ title: 'Distance (m)' }},
            hovermode: 'x unified'
        }});

        const sTraces = [];
        for (let i = 0; i < 4; i++) {{
            sTraces.push({{ x: [], y: [], name: 'A'+(i+1), mode: 'lines',
                            line: {{ color: colors[i] }} }});
        }}
        sTraces.push({{ x: [], y: [], mode: 'lines', name: 'T2 Reject',
                        line: {{ color: '#ef4444', dash: 'dash' }} }});
        sTraces.push({{ x: [], y: [], mode: 'lines', name: 'T2 Recover',
                        line: {{ color: '#f59e0b', dash: 'dash' }} }});
        sTraces.push({{ x: [0, 100], y: [null], xaxis: 'x2', showlegend: false, hoverinfo: 'none' }});
        Plotly.newPlot('scores', sTraces, {{
            margin: {{ t: 40, b: 40 }},
            xaxis:  {{ title: 'Sample Index' }},
            xaxis2: {{ title: 'Time (s)', overlaying: 'x', side: 'top',
                       showticklabels: true, showline: true }},
            yaxis:  {{ range: [0, 20], title: 'D2 Score' }},
            hovermode: 'x unified'
        }});

        Plotly.newPlot('accel', [
            {{ x: [], y: [], name: 'Ax', mode: 'lines', line: {{ color: '#2563eb' }} }},
            {{ x: [], y: [], name: 'Ay', mode: 'lines', line: {{ color: '#16a34a' }} }},
            {{ x: [0, 100], y: [null], xaxis: 'x2', showlegend: false, hoverinfo: 'none' }}
        ], {{
            margin: {{ t: 40, b: 40 }},
            xaxis:  {{ title: 'Sample Index' }},
            xaxis2: {{ title: 'Time (s)', overlaying: 'x', side: 'top',
                       showticklabels: true, showline: true }},
            yaxis:  {{ title: 'm/s²' }},
            hovermode: 'x unified'
        }});

        Plotly.newPlot('velocity', [
            {{ x: [], y: [], name: 'Vx Raw', mode: 'lines',
               line: {{ color: '#ef4444', dash: 'dot', width: 1 }}, visible: 'legendonly' }},
            {{ x: [], y: [], name: 'Vy Raw', mode: 'lines',
               line: {{ color: '#f87171', dash: 'dot', width: 1 }}, visible: 'legendonly' }},
            {{ x: [], y: [], name: 'Vx Clean', mode: 'lines', line: {{ color: '#2563eb', width: 2 }} }},
            {{ x: [], y: [], name: 'Vy Clean', mode: 'lines', line: {{ color: '#16a34a', width: 2 }} }},
            {{ x: [], y: [], name: 'ZUPT Active', fill: 'tozeroy', mode: 'lines',
               line: {{ color: '#cbd5e1', width: 0 }}, opacity: 0.3 }},
            {{ x: [0, 100], y: [null], xaxis: 'x2', showlegend: false, hoverinfo: 'none' }}
        ], {{
            margin: {{ t: 40, b: 40 }},
            xaxis:  {{ title: 'Sample Index' }},
            xaxis2: {{ title: 'Time (s)', overlaying: 'x', side: 'top',
                       showticklabels: true, showline: true }},
            yaxis:  {{ title: 'm/s' }},
            hovermode: 'x unified'
        }});

        Plotly.newPlot('yaw_plot', [
            {{ x: [], y: [], name: 'Gyro Z', mode: 'lines',
               line: {{ color: '#94a3b8', width: 1 }}, yaxis: 'y2' }},
            {{ x: [], y: [], name: 'Yaw Angle', mode: 'lines', line: {{ color: '#7c3aed', width: 2 }} }},
            {{ x: [0, 100], y: [null], xaxis: 'x2', showlegend: false, hoverinfo: 'none' }}
        ], {{
            margin: {{ t: 40, b: 40 }},
            xaxis:  {{ title: 'Sample Index' }},
            xaxis2: {{ title: 'Time (s)', overlaying: 'x', side: 'top',
                       showticklabels: true, showline: true }},
            yaxis:  {{ title: 'Yaw (deg)', side: 'left' }},
            yaxis2: {{ title: 'Gyro (rad/s)', overlaying: 'y', side: 'right', showgrid: false }},
            hovermode: 'x unified'
        }});

        Plotly.newPlot('pos_error', [
            {{ x: [], y: [], name: 'Pos Error', mode: 'lines',
               line: {{ color: '#ef4444', width: 2 }}, fill: 'tozeroy' }},
            {{ x: [0, 100], y: [null], xaxis: 'x2', showlegend: false, hoverinfo: 'none' }}
        ], {{
            margin: {{ t: 40, b: 40 }},
            xaxis:  {{ title: 'Sample Index' }},
            xaxis2: {{ title: 'Time (s)', overlaying: 'x', side: 'top',
                       showticklabels: true, showline: true }},
            yaxis:  {{ title: 'Error (m)', range: [0, 1.5] }},
            hovermode: 'x unified'
        }});

        Plotly.newPlot('error_frame', [
            {{ x: [], y: [], name: 'Log Error Frames', mode: 'lines',
               line: {{ color: '#475569', width: 1 }}, fill: 'tozeroy' }},
            {{ x: [0, 100], y: [null], xaxis: 'x2', showlegend: false, hoverinfo: 'none' }}
        ], {{
            margin: {{ t: 40, b: 40 }},
            xaxis:  {{ title: 'Sample Index' }},
            xaxis2: {{ title: 'Time (s)', overlaying: 'x', side: 'top',
                       showticklabels: true, showline: true }},
            yaxis:  {{ title: 'Frame Count' }},
            hovermode: 'x unified'
        }});

        function syncInput(rangeId, inputId) {{
            document.getElementById(rangeId).addEventListener('input', (e) => {{
                document.getElementById(inputId).value = e.target.value;
                update();
            }});
            document.getElementById(inputId).addEventListener('input', (e) => {{
                document.getElementById(rangeId).value = e.target.value;
                update();
            }});
        }}

        syncInput('t2_high_range', 't2_high_input');
        syncInput('t2_low_range', 't2_low_input');
        syncInput('r_range', 'r_input');
        syncInput('win_range', 'win_input');
        syncInput('zupt_acc_range', 'zupt_acc_input');
        syncInput('zupt_gyr_range', 'zupt_gyr_input');
        syncInput('max_samples_range', 'max_samples_input');
        update();
    </script>
</body>
</html>
"""

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
                html = HTML_V49_TEMPLATE.format(
                    filename=fn,
                    data_json=json.dumps(p),
                    anchors_json=json.dumps(ANCHORS),
                    gt_square_json=json.dumps(GT_SQUARE),
                    tag_height=TAG_HEIGHT,
                    bias_x=p['biases']['ax'],
                    bias_y=p['biases']['ay']
                )
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