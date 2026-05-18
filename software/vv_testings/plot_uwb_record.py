import csv
import sys
import os
import json
from datetime import datetime

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>UWB Record Analysis - {filename}</title>
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 20px; background-color: #f5f5f5; }}
        .container {{ max-width: 1200px; margin: auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
        h1 {{ color: #2c3e50; text-align: center; }}
        .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-bottom: 30px; }}
        .stat-card {{ background: #ecf0f1; padding: 15px; border-radius: 6px; text-align: center; border-left: 5px solid #3498db; }}
        .stat-card h3 {{ margin: 0; color: #7f8c8d; font-size: 0.9em; text-transform: uppercase; }}
        .stat-card p {{ margin: 10px 0 0; font-size: 1.5em; font-weight: bold; color: #2c3e50; }}
        .chart-container {{ margin-bottom: 40px; height: 500px; border: 1px solid #eee; border-radius: 8px; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
        th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }}
        th {{ background-color: #3498db; color: white; }}
        tr:hover {{ background-color: #f9f9f9; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>UWB Record Analysis: {filename}</h1>
        
        <div class="stats-grid">
            <div class="stat-card"><h3>Duration</h3><p>{duration:.2f} s</p></div>
            <div class="stat-card"><h3>Total Samples</h3><p>{total_samples}</p></div>
            <div class="stat-card"><h3>Frame Errors</h3><p>{frame_errors}</p></div>
            <div class="stat-card"><h3>Avg Error Estimate</h3><p>{avg_pos_error:.3f} m</p></div>
        </div>

        <div id="distance_chart" class="chart-container"></div>
        <div id="position_chart" class="chart-container"></div>
        <div id="error_chart" class="chart-container"></div>

        <h2>Anchor Statistics</h2>
        <table>
            <thead>
                <tr>
                    <th>Anchor ID</th>
                    <th>Samples</th>
                    <th>Mean Distance (m)</th>
                    <th>Std Dev (m)</th>
                    <th>Min (m)</th>
                    <th>Max (m)</th>
                </tr>
            </thead>
            <tbody>
                {anchor_table_rows}
            </tbody>
        </table>
    </div>

    <script>
        const distanceData = {distance_data_json};
        const positionData = {position_data_json};
        
        // 1. Distance Over Time
        const distTraces = [];
        for (const aid in distanceData) {{
            distTraces.push({{
                x: distanceData[aid].t,
                y: distanceData[aid].d,
                text: distanceData[aid].text,
                name: 'Anchor ' + aid,
                mode: 'lines+markers',
                marker: {{ size: 4 }},
                line: {{ shape: 'linear' }},
                hoverinfo: 'text+y'
            }});
        }}
        const layoutCommon = {{
            hovermode: 'closest',
            xaxis: {{ 
                showspikes: true, 
                spikemode: 'across', 
                spikedash: 'dash', 
                spikecolor: '#999' 
            }}
        }};

        Plotly.newPlot('distance_chart', distTraces, {{
            ...layoutCommon,
            title: 'Distances over Time',
            xaxis: {{ ...layoutCommon.xaxis, title: 'Time (s)' }},
            yaxis: {{ title: 'Distance (m)' }}
        }});

        // 2. Position XY Scatter
        if (positionData.x.length > 0) {{
            const anchorData = [
                {{ x: 0.0,  y: 0.0,  name: 'Anchor 1' }},
                {{ x: 9.76, y: 0.0,  name: 'Anchor 2' }},
                {{ x: 0.0,  y: 9.76, name: 'Anchor 3' }},
                {{ x: 9.76, y: 9.76, name: 'Anchor 4' }}
            ];

            const posTraces = [
                // Anchor Perimeter
                {{
                    x: [0, 9.76, 9.76, 0, 0],
                    y: [0, 0, 9.76, 9.76, 0],
                    mode: 'lines',
                    name: 'Anchor Perimeter',
                    line: {{ color: '#ccc', dash: 'dash', width: 1 }},
                    hoverinfo: 'none'
                }},
                // Anchors
                {{
                    x: anchorData.map(a => a.x),
                    y: anchorData.map(a => a.y),
                    text: anchorData.map(a => a.name),
                    mode: 'markers+text',
                    type: 'scatter',
                    name: 'Anchors',
                    marker: {{ color: '#27ae60', size: 10, symbol: 'triangle-up' }},
                    textposition: 'top center',
                    hoverinfo: 'text'
                }},
                // Ground Truth (4.88m square)
                {{
                    x: [2.44, 7.32, 7.32, 2.44, 2.44],
                    y: [2.50, 2.50, 7.38, 7.38, 2.50],
                    mode: 'lines',
                    name: 'Ground Truth (Ideal)',
                    line: {{ color: 'rgba(231, 76, 60, 0.8)', dash: 'dot', width: 2 }},
                    hoverinfo: 'name'
                }},
                // Tag Path
                {{
                    x: positionData.x,
                    y: positionData.y,
                    text: positionData.text,
                    mode: 'lines+markers',
                    type: 'scatter',
                    name: 'Tag Path',
                    line: {{ color: 'rgba(52, 152, 219, 0.3)', width: 1 }},
                    marker: {{ color: 'rgba(52, 152, 219, 0.8)', size: 4 }},
                    hoverinfo: 'text+x+y'
                }}
            ];

            Plotly.newPlot('position_chart', posTraces, {{
                title: 'Tag Movement Trajectory & Anchor Layout',
                xaxis: {{ title: 'X (m)', scaleanchor: 'y' }},
                yaxis: {{ title: 'Y (m)' }},
                hovermode: 'closest'
            }});

            // 3. Position Error over Time
            Plotly.newPlot('error_chart', [{{
                x: positionData.t,
                y: positionData.err,
                type: 'scatter',
                mode: 'lines',
                name: 'Error Estimate',
                line: {{ color: '#e74c3c' }},
                hoverinfo: 'x+y'
            }}], {{
                ...layoutCommon,
                title: 'Position Error Estimate over Time',
                xaxis: {{ ...layoutCommon.xaxis, title: 'Time (s)' }},
                yaxis: {{ title: 'Error (m)' }}
            }});
        }}
    </script>
</body>
</html>
"""

def generate_plot(filename):
    if not os.path.exists(filename):
        print(f"Error: {filename} not found.")
        return

    # Data collection
    dist_data = {} # aid -> {t: [], d: [], text: []}
    pos_data = {'t': [], 'x': [], 'y': [], 'err': [], 'text': []}
    
    start_ts = None
    total_samples = 0
    max_frame_error = 0
    
    with open(filename, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            total_samples += 1
            ts = datetime.strptime(row['timestamp'], "%Y-%m-%d %H:%M:%S.%f")
            if start_ts is None: start_ts = ts
            
            t_rel = (ts - start_ts).total_seconds()
            fe = int(row['frame_error'] or 0)
            if fe > max_frame_error: max_frame_error = fe
            
            if row['type'] == 'distance':
                aid = row['anchor_id']
                if aid not in dist_data: dist_data[aid] = {'t': [], 'd': [], 'text': []}
                dist_data[aid]['t'].append(t_rel)
                dist_data[aid]['d'].append(float(row['distance']))
                dist_data[aid]['text'].append(f"Time: {t_rel:.2f}s<br>TS: {row['timestamp'][-12:]}<br>RSSI: {row['rssi']}dBm")
            elif row['type'] == 'position':
                pos_data['t'].append(t_rel)
                pos_data['x'].append(float(row['x']))
                pos_data['y'].append(float(row['y']))
                pos_data['err'].append(float(row['error_m']))
                pos_data['text'].append(f"Time: {t_rel:.2f}s<br>TS: {row['timestamp'][-12:]}<br>Error: {row['error_m']}m")

    duration = t_rel if start_ts else 0
    avg_pos_error = sum(pos_data['err'])/len(pos_data['err']) if pos_data['err'] else 0

    # Build anchor table
    table_rows = ""
    for aid in sorted(dist_data.keys()):
        vals = dist_data[aid]['d']
        n = len(vals)
        mean = sum(vals)/n
        std = (sum((v-mean)**2 for v in vals)/n)**0.5
        table_rows += f"<tr><td>{aid}</td><td>{n}</td><td>{mean:.3f}</td><td>{std:.3f}</td><td>{min(vals):.3f}</td><td>{max(vals):.3f}</td></tr>"

    # Fill template
    html = HTML_TEMPLATE.format(
        filename=os.path.basename(filename),
        duration=duration,
        total_samples=total_samples,
        frame_errors=max_frame_error,
        avg_pos_error=avg_pos_error,
        anchor_table_rows=table_rows,
        distance_data_json=json.dumps(dist_data),
        position_data_json=json.dumps(pos_data)
    )

    output_file = filename.replace('.csv', '_plot.html')
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f"Interactive plot generated: {output_file}")
    return output_file

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    if not target:
        files = [f for f in os.listdir('.') if f.startswith('uwb_record_') and f.endswith('.csv')]
        if files: target = max(files)
    
    if target:
        generate_plot(target)
    else:
        print("No uwb_record file found.")
