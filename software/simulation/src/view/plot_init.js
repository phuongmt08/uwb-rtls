function initPlots(anchors, gt_square, rawData, samples) {
    const colors = SIM_CONFIG.VIEW.COLORS;
    // 1. Trajectory
    Plotly.newPlot('trajectory', [
        { x: anchors.map(a => a.x), y: anchors.map(a => a.y), mode: 'markers+text',
          name: 'Anchors', text: anchors.map(a => 'A'+a.id), textposition: 'top center',
          marker: { color: '#1e293b', size: 10, symbol: 'triangle-up' } },
        { x: gt_square.x, y: gt_square.y, mode: 'lines', name: 'Ground Truth',
          line: { color: '#f87171', dash: 'dot', width: 1 } },
        { x: samples.map(e => e.px_fw), y: samples.map(e => e.py_fw), mode: 'lines+markers',
          name: 'Firmware Path', type: 'scattergl', marker: { size: 2 }, line: { color: '#94a3b8', width: 1 } },
        { x: [], y: [], mode: 'lines', name: 'Simulated Path (All)',
           type: 'scattergl', line: { color: '#94a3b8', width: 1, dash: 'dot' } },
        { x: [], y: [], mode: 'lines+markers', name: 'Simulated Path (Rules)',
           type: 'scattergl', marker: { size: 3 }, line: { color: '#2563eb', width: 2 } },
        { x: [], y: [], mode: 'lines', name: 'Simulated Path (Multilateration)',
           type: 'scattergl', line: { color: '#d97706', width: 2, dash: 'dash' } },
        { x: [], y: [], mode: 'lines+markers', name: 'Simulated Path (Best Triplet)',
           type: 'scattergl', marker: { size: 3 }, line: { color: '#059669', width: 2 } }
    ], {
        margin: { t: 40, b: 40, l: 50, r: 50 },
        xaxis: { title: 'X (m)', gridcolor: '#f1f5f9' },
        yaxis: { title: 'Y (m)', gridcolor: '#f1f5f9', scaleanchor: 'x', scaleratio: 1 },
        hovermode: 'closest',
        height: 600
    });

    // 2. Distances (3 traces per anchor: Raw, Gated, Rejected)
    const distTraces = [];
    anchors.forEach((a, i) => {
        // i*3: Raw
        distTraces.push({
            x: samples.map((_, idx) => idx), y: samples.map(e => e.distances[i]),
            name: `A${a.id} Raw`, mode: 'lines', type: 'scattergl',
            line: { color: colors[i], width: 1, opacity: 0.3 }, visible: 'legendonly'
        });
        // i*3+1: Gated
        distTraces.push({
            x: [], y: [], name: `A${a.id} Gated`, mode: 'lines', type: 'scattergl',
            line: { color: colors[i], width: 2 }
        });
        // i*3+2: Rejected
        distTraces.push({
            x: [], y: [], name: `A${a.id} Rejected`, mode: 'markers', type: 'scattergl',
            marker: { color: 'red', symbol: 'x', size: 5 }, visible: 'legendonly'
        });
    });
    // Trace index 12: Dummy for axis
    distTraces.push({ x: [0, 100], y: [null], xaxis: 'x2', showlegend: false, hoverinfo: 'none' });

    Plotly.newPlot('distances', distTraces, {
        margin: { t: 40, b: 40, l: 50, r: 50 }, xaxis: { title: 'Sample Index' },
        xaxis2: { title: 'Time (s)', overlaying: 'x', side: 'top', showticklabels: true, showline: true, autorange: false, fixedrange: true },
        yaxis: { title: 'Distance (m)' }, hovermode: 'x unified',
        height: 600
    });

    // 3. D2 Scores
    const sTraces = [];
    for (let i = 0; i < 4; i++) sTraces.push({ x: [], y: [], name: 'A'+(i+1), mode: 'lines', type: 'scattergl', line: { color: colors[i] } });
    sTraces.push({ x: [], y: [], mode: 'lines', name: 'T2 Reject', line: { color: '#ef4444', dash: 'dash' } });
    sTraces.push({ x: [], y: [], mode: 'lines', name: 'T2 Recover', line: { color: '#f59e0b', dash: 'dash' } });
    sTraces.push({ x: [0, 100], y: [null], xaxis: 'x2', showlegend: false, hoverinfo: 'none' });
    Plotly.newPlot('scores', sTraces, {
        margin: { t: 40, b: 40, l: 50, r: 50 }, xaxis: { title: 'Sample Index' },
        xaxis2: { title: 'Time (s)', overlaying: 'x', side: 'top', showticklabels: true, showline: true, autorange: false, fixedrange: true },
        yaxis: { range: [0, 20], title: 'D2 Score' }, hovermode: 'x unified'
    });

    // 4. Accel
    Plotly.newPlot('accel', [
        { x: [], y: [], name: 'Ax', mode: 'lines', type: 'scatter', line: { color: '#2563eb' },
          hovertemplate: 'Ax: %{y:.3f} m/s²<extra></extra>' },
        { x: [], y: [], name: 'Ay', mode: 'lines', type: 'scatter', line: { color: '#16a34a' },
          hovertemplate: 'Ay: %{y:.3f} m/s²<extra></extra>' },
        { x: [0, 100], y: [null], xaxis: 'x2', showlegend: false, hoverinfo: 'none' }
    ], {
        margin: { t: 40, b: 40, l: 50, r: 50 }, xaxis: { title: 'Sample Index' },
        xaxis2: { title: 'Time (s)', overlaying: 'x', side: 'top', showticklabels: true, showline: true, autorange: false, fixedrange: true },
        yaxis: { title: 'Acceleration (m/s²)' }, hovermode: 'x unified'
    });

    // 5. Velocity
    Plotly.newPlot('velocity', [
        { x: [], y: [], name: 'Vx Raw', mode: 'lines', type: 'scatter', line: { color: '#ef4444', dash: 'dot', width: 1 }, visible: 'legendonly', hovertemplate: 'Vx Raw: %{y:.3f} m/s<extra></extra>' },
        { x: [], y: [], name: 'Vy Raw', mode: 'lines', type: 'scatter', line: { color: '#f87171', dash: 'dot', width: 1 }, visible: 'legendonly', hovertemplate: 'Vy Raw: %{y:.3f} m/s<extra></extra>' },
        { x: [], y: [], name: 'Vx Clean', mode: 'lines', type: 'scatter', line: { color: '#2563eb', width: 2 }, hovertemplate: 'Vx: %{y:.3f} m/s<extra></extra>' },
        { x: [], y: [], name: 'Vy Clean', mode: 'lines', type: 'scatter', line: { color: '#16a34a', width: 2 }, hovertemplate: 'Vy: %{y:.3f} m/s<extra></extra>' },
        { x: [], y: [], name: 'ZUPT Active', fill: 'tozeroy', mode: 'lines', line: { color: '#cbd5e1', width: 0 }, opacity: 0.3, hovertemplate: 'ZUPT Active<extra></extra>' },
        { x: [0, 100], y: [null], xaxis: 'x2', showlegend: false, hoverinfo: 'none' }
    ], {
        margin: { t: 40, b: 40, l: 50, r: 50 }, xaxis: { title: 'Sample Index' },
        xaxis2: { title: 'Time (s)', overlaying: 'x', side: 'top', showticklabels: true, showline: true, autorange: false, fixedrange: true },
        yaxis: { title: 'Velocity (m/s)' }, hovermode: 'x unified'
    });

    // 6. Yaw
    Plotly.newPlot('yaw_plot', [
        { x: [], y: [], name: 'Gyro Z', mode: 'lines', type: 'scatter', line: { color: '#94a3b8', width: 1 }, yaxis: 'y2',
          hovertemplate: 'Gz: %{y:.4f} rad/s<extra></extra>' },
        { x: [], y: [], name: 'Yaw Angle', mode: 'lines', type: 'scatter', line: { color: '#7c3aed', width: 2 },
          hovertemplate: 'Yaw: %{y:.2f} deg<extra></extra>' },
        { x: [0, 100], y: [null], xaxis: 'x2', showlegend: false, hoverinfo: 'none' }
    ], {
        margin: { t: 40, b: 40, l: 50, r: 50 }, xaxis: { title: 'Sample Index' },
        xaxis2: { title: 'Time (s)', overlaying: 'x', side: 'top', showticklabels: true, showline: true, autorange: false, fixedrange: true },
        yaxis: { title: 'Yaw (deg)', side: 'left' },
        yaxis2: { title: 'Gyro (rad/s)', overlaying: 'y', side: 'right', showgrid: false },
        hovermode: 'x unified'
    });

    // 7. FP Amp/SNR
    const fpTpl = { margin: { t: 40, b: 40, l: 50, r: 50 }, xaxis: { title: 'Sample Index' }, xaxis2: { title: 'Time (s)', overlaying: 'x', side: 'top', showticklabels: true, showline: true, autorange: false, fixedrange: true }, hovermode: 'x unified' };
    const fpTraces = anchors.map((a, i) => ({ x: [], y: [], name: `A${a.id}`, mode: 'lines+markers', type: 'scatter', line: { color: colors[i], width: 1 }, marker: { size: 4 } }));
    fpTraces.push({ x: [0, 100], y: [null], xaxis: 'x2', showlegend: false, hoverinfo: 'none' });
    Plotly.newPlot('fp_amp', JSON.parse(JSON.stringify(fpTraces)), Object.assign({}, fpTpl, { yaxis: { title: 'Amplitude Norm' } }));
    Plotly.newPlot('fp_snr', JSON.parse(JSON.stringify(fpTraces)), Object.assign({}, fpTpl, { yaxis: { title: 'SNR' } }));

    // 8. Pos Error
    const errTraces = [
        { x: [], y: [], name: 'Pos Error (Rules)', mode: 'lines', type: 'scatter', line: { color: '#ef4444', width: 2 } },
        { x: [], y: [], name: 'Pos Error (Multilateration)', mode: 'lines', type: 'scatter', line: { color: '#d97706', width: 2 } },
        { x: [], y: [], name: 'Pos Error (Best Triplet)', mode: 'lines', type: 'scatter', line: { color: '#059669', width: 2 } },
        { x: [0, 100], y: [null], xaxis: 'x2', showlegend: false, hoverinfo: 'none' }
    ];
    Plotly.newPlot('pos_error', errTraces, {
        margin: { t: 40, b: 40, l: 50, r: 50 }, xaxis: { title: 'Sample Index' },
        xaxis2: { title: 'Time (s)', overlaying: 'x', side: 'top', showticklabels: true, showline: true, autorange: false, fixedrange: true },
        yaxis: { title: 'Error (m)', range: [0, SIM_CONFIG.VIEW.MAX_ERROR_RANGE] }, hovermode: 'x unified'
    });

    // 9. Error Frame
    Plotly.newPlot('error_frame', [
        { x: [], y: [], name: 'Log Error Frames', mode: 'lines', type: 'scatter', line: { color: '#475569', width: 1 }, fill: 'tozeroy' },
        { x: [0, 100], y: [null], xaxis: 'x2', showlegend: false, hoverinfo: 'none' }
    ], {
        margin: { t: 40, b: 40, l: 50, r: 50 }, xaxis: { title: 'Sample Index' },
        xaxis2: { title: 'Time (s)', overlaying: 'x', side: 'top', showticklabels: true, showline: true, autorange: false, fixedrange: true },
        yaxis: { title: 'Frame Count' }, hovermode: 'x unified'
    });
}

// Helper for mean calculation
function meanErr(arr) {
    const valid = arr.filter(v => v !== null);
    return valid.length ? (valid.reduce((s, v) => s + v, 0) / valid.length).toFixed(3) : "N/A";
}
