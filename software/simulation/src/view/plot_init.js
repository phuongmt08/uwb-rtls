const TIME_AXIS_PLOTS = ['distances', 'scores', 'accel', 'velocity', 'yaw_plot', 'pos_error', 'error_frame', 'fp_amp', 'fp_snr'];

function sampleToTime(sampleIndex, times, totalTime) {
    if (!times || times.length === 0 || !Number.isFinite(sampleIndex)) return 0;
    if (sampleIndex <= 0) return 0;
    if (sampleIndex >= times.length) return totalTime;

    const lo = Math.floor(sampleIndex);
    const hi = Math.ceil(sampleIndex);
    const tLo = lo <= 0 ? 0 : times[Math.min(lo, times.length - 1)];
    const tHi = hi >= times.length ? totalTime : times[hi];
    if (lo === hi) return tLo;

    return tLo + (tHi - tLo) * (sampleIndex - lo);
}

function timeRangeForSampleRange(sampleRange) {
    const sync = window.__uwbTimeAxisSync;
    if (!sync || !sampleRange || sampleRange.length < 2) return null;

    return [
        sampleToTime(sampleRange[0], sync.times, sync.totalTime),
        sampleToTime(sampleRange[1], sync.times, sync.totalTime)
    ];
}

function setTimeAxisSyncData(xAxis, times, totalTime) {
    window.__uwbTimeAxisSync = {
        sampleCount: xAxis ? xAxis.length : 0,
        times: times || [],
        totalTime: Number.isFinite(totalTime) ? totalTime : 0
    };
}

function syncTimeAxisToSampleRange(plotId, sampleRange) {
    const timeRange = timeRangeForSampleRange(sampleRange);
    if (!timeRange) return;

    Plotly.relayout(plotId, {
        'xaxis2.range': timeRange,
        'xaxis2.autorange': false,
        'xaxis2.showticklabels': true
    });
}

function attachTimeAxisZoomSync(plotId) {
    const plot = document.getElementById(plotId);
    if (!plot || plot.__timeAxisZoomSyncAttached) return;

    plot.__timeAxisZoomSyncAttached = true;
    plot.on('plotly_relayout', (eventData) => {
        const sync = window.__uwbTimeAxisSync;
        if (!sync) return;

        if (eventData['xaxis.autorange']) {
            syncTimeAxisToSampleRange(plotId, [0, sync.sampleCount]);
            return;
        }

        const x0 = eventData['xaxis.range[0]'];
        const x1 = eventData['xaxis.range[1]'];
        if (Number.isFinite(x0) && Number.isFinite(x1)) {
            syncTimeAxisToSampleRange(plotId, [x0, x1]);
        }
    });
}

function attachAllTimeAxisZoomSync() {
    TIME_AXIS_PLOTS.forEach(attachTimeAxisZoomSync);
}

function initPlots(anchors, gt_square, rawData, samples) {
    const colors = SIM_CONFIG.VIEW.COLORS;
    const isPathCsv = rawData.log_format === 'path_csv';
    // 1. Trajectory
    Plotly.newPlot('trajectory', [
        { x: anchors.map(a => a.x), y: anchors.map(a => a.y), mode: 'markers+text',
          name: 'Anchors', text: anchors.map(a => 'A'+a.id), textposition: 'top center',
          marker: { color: '#1e293b', size: 10, symbol: 'triangle-up' } },
        { x: gt_square.x, y: gt_square.y, mode: 'lines', name: `Ground Truth (${gt_square.name || 'Original Square'})`,
          line: { color: '#f87171', dash: 'dot', width: 1 } },
        { x: isPathCsv ? samples.map(e => e.tril_x) : samples.map(e => e.px_fw),
          y: isPathCsv ? samples.map(e => e.tril_y) : samples.map(e => e.py_fw), mode: 'lines+markers',
          name: isPathCsv ? 'Trilateration Path' : 'Firmware Path', type: 'scattergl',
          marker: { size: 2 }, line: { color: '#94a3b8', width: 1 } },
        { x: isPathCsv ? samples.map(e => e.ukf_x) : [], y: isPathCsv ? samples.map(e => e.ukf_y) : [],
          mode: 'lines+markers', name: isPathCsv ? 'UKF Path' : 'Simulated Path (Rules)',
           type: 'scattergl', marker: { size: 3 }, line: { color: '#2563eb', width: 2 } },
        { x: [], y: [], mode: 'lines', name: 'Simulated Path (Multilateration)',
           visible: isPathCsv ? false : true, type: 'scattergl', line: { color: '#d97706', width: 2, dash: 'dash' } },
        { x: [], y: [], mode: 'lines+markers', name: 'Simulated Path (Best Triplet)',
           visible: isPathCsv ? false : true, type: 'scattergl', marker: { size: 3 }, line: { color: '#059669', width: 2 } },
        { x: [], y: [], mode: 'lines+markers', name: 'Simulated Path (UKF Fusion)',
           visible: isPathCsv ? false : true, type: 'scattergl', marker: { size: 3 }, line: { color: '#8b5cf6', width: 2 } },
        { x: [], y: [], mode: 'lines+markers', name: 'Simulated Path (UKF Fusion + IMU Butterworth)',
           visible: isPathCsv ? false : true, type: 'scattergl', marker: { size: 3 }, line: { color: '#0ea5e9', width: 2 } }
    ], {
        margin: { t: 40, b: 100, l: 50, r: 50 },
        xaxis: { title: 'X (m)', gridcolor: '#f1f5f9' },
        yaxis: { title: 'Y (m)', gridcolor: '#f1f5f9' },
        hovermode: 'closest',
        legend: {
            orientation: 'h',
            yanchor: 'top',
            y: -0.15,
            xanchor: 'center',
            x: 0.5
        },
        width: 800,
        height: 800
    }, {
        responsive: true,
        scrollZoom: true
    });

    // 2. Distances (4 traces per anchor: Raw, Gated, Rejected, Rescue)
    const distTraces = [];
    anchors.forEach((a, i) => {
        // i*4: Raw
        distTraces.push({
            x: samples.map((_, idx) => idx), y: samples.map(e => e.distances[i]),
            name: `A${a.id} Raw`, mode: 'lines', type: 'scattergl',
            line: { color: colors[i], width: 1, opacity: 0.3 }, visible: 'legendonly'
        });
        // i*4+1: Gated
        distTraces.push({
            x: [], y: [], name: `A${a.id} Gated`, mode: 'lines', type: 'scattergl',
            line: { color: colors[i], width: 2 }
        });
        // i*4+2: Rejected
        distTraces.push({
            x: [], y: [], name: `A${a.id} Rejected`, mode: 'markers', type: 'scattergl',
            marker: { color: 'red', symbol: 'x', size: 5 }, visible: 'legendonly'
        });
        // i*4+3: Rescue
        distTraces.push({
            x: [], y: [], name: `A${a.id} Rescue`, mode: 'markers', type: 'scattergl',
            marker: { color: '#f59e0b', symbol: 'circle-open', size: 7 }
        });
    });

    // Dummy for axis
    distTraces.push({ x: [0, 100], y: [null], xaxis: 'x2', showlegend: false, hoverinfo: 'none' });

    Plotly.newPlot('distances', distTraces, {
        margin: { t: 40, b: 40, l: 50, r: 50 }, xaxis: { title: 'Sample Index' },
        xaxis2: { title: 'Time (s)', overlaying: 'x', side: 'top', showticklabels: true, showline: true, autorange: false, fixedrange: true },
        yaxis: { title: 'Distance (m)' }, hovermode: 'x unified',
        height: 800
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
        yaxis: { title: 'D2 Score' }, hovermode: 'x unified'
    });

    // 4. Accel
    Plotly.newPlot('accel', [
        { x: [], y: [], name: 'Ax', mode: 'lines', type: 'scatter', line: { color: '#2564eb8f' },
          hovertemplate: 'Ax: %{y:.3f} m/s²<extra></extra>' },
        { x: [], y: [], name: 'Ay', mode: 'lines', type: 'scatter', line: { color: '#53b577ba' },
          hovertemplate: 'Ay: %{y:.3f} m/s²<extra></extra>' },
        { x: [], y: [], name: 'Ax Butterworth', mode: 'lines', type: 'scatter', line: { color: '#eb0808', dash: 'dash', width: 2 },
          hovertemplate: 'Ax Butterworth: %{y:.3f} m/s^2<extra></extra>' },
        { x: [], y: [], name: 'Ay Butterworth', mode: 'lines', type: 'scatter', line: { color: '#e30bff', dash: 'dash', width: 2 },
          hovertemplate: 'Ay Butterworth: %{y:.3f} m/s^2<extra></extra>' },
        { x: [], y: [], name: 'ZUPT Active', fill: 'tozeroy', yaxis: 'y2', mode: 'lines', line: { color: '#cbd5e1', width: 0 }, opacity: 0.3, hovertemplate: 'ZUPT Active<extra></extra>' },
        { x: [0, 100], y: [null], xaxis: 'x2', showlegend: false, hoverinfo: 'none' }
    ], {
        margin: { t: 40, b: 40, l: 50, r: 50 }, xaxis: { title: 'Sample Index' },
        xaxis2: { title: 'Time (s)', overlaying: 'x', side: 'top', showticklabels: true, showline: true, autorange: false, fixedrange: true },
        yaxis: { title: 'Acceleration (m/s²)' },
        yaxis2: { overlaying: 'y', side: 'right', range: [0, 1], showgrid: false, zeroline: false, showticklabels: false },
        hovermode: 'x unified'
    });

    Plotly.newPlot('accel_spectrum', [
        { x: [], y: [], name: 'Ax Spectrum', mode: 'lines', type: 'scatter', line: { color: '#2564eb6c', width: 2 },
          hovertemplate: 'Ax %{x:.3f} Hz: %{y:.6f}<extra></extra>' },
        { x: [], y: [], name: 'Ay Spectrum', mode: 'lines', type: 'scatter', line: { color: '#16a34a6c', width: 2 },
          hovertemplate: 'Ay %{x:.3f} Hz: %{y:.6f}<extra></extra>' },
        { x: [], y: [], name: 'Ax Butterworth Spectrum', mode: 'lines', type: 'scatter', line: { color: 'rgb(234, 9, 159)', dash: 'dash', width: 2 },
          hovertemplate: 'Ax Butterworth %{x:.3f} Hz: %{y:.6f}<extra></extra>' },
        { x: [], y: [], name: 'Ay Butterworth Spectrum', mode: 'lines', type: 'scatter', line: { color: 'rgb(226, 11, 11)6c', dash: 'dash', width: 2 },
          hovertemplate: 'Ay Butterworth %{x:.3f} Hz: %{y:.6f}<extra></extra>' },
        { x: [], y: [], name: 'Butterworth Cutoff', mode: 'lines', type: 'scatter', line: { color: '#ef4444', dash: 'dot', width: 2 },
          hovertemplate: 'Cutoff: %{x:.3f} Hz<extra></extra>' }
    ], {
        margin: { t: 40, b: 40, l: 60, r: 40 },
        xaxis: { title: 'Frequency (Hz)', rangemode: 'tozero' },
        yaxis: { title: 'Amplitude' },
        hovermode: 'x unified'
    });

    // 5. Velocity
    Plotly.newPlot('velocity', [
        { x: [], y: [], name: 'Vx Raw', mode: 'lines', type: 'scatter', line: { color: '#ef4444', dash: 'dot', width: 1 }, visible: 'legendonly', hovertemplate: 'Vx Raw: %{y:.3f} m/s<extra></extra>' },
        { x: [], y: [], name: 'Vy Raw', mode: 'lines', type: 'scatter', line: { color: '#f87171', dash: 'dot', width: 1 }, visible: 'legendonly', hovertemplate: 'Vy Raw: %{y:.3f} m/s<extra></extra>' },
        { x: [], y: [], name: 'Vx Clean', mode: 'lines', type: 'scatter', line: { color: '#2563eb', width: 2 }, hovertemplate: 'Vx: %{y:.3f} m/s<extra></extra>' },
        { x: [], y: [], name: 'Vy Clean', mode: 'lines', type: 'scatter', line: { color: '#16a34a', width: 2 }, hovertemplate: 'Vy: %{y:.3f} m/s<extra></extra>' },
        { x: [], y: [], name: 'Vx Butterworth', mode: 'lines', type: 'scatter', line: { color: '#60a5fa', dash: 'dash', width: 2 }, hovertemplate: 'Vx Butterworth: %{y:.3f} m/s<extra></extra>' },
        { x: [], y: [], name: 'Vy Butterworth', mode: 'lines', type: 'scatter', line: { color: '#86efac', dash: 'dash', width: 2 }, hovertemplate: 'Vy Butterworth: %{y:.3f} m/s<extra></extra>' },
        { x: [], y: [], name: 'ZUPT Active', fill: 'tozeroy', yaxis: 'y2', mode: 'lines', line: { color: '#cbd5e1', width: 0 }, opacity: 0.3, hovertemplate: 'ZUPT Active<extra></extra>' },
        { x: [0, 100], y: [null], xaxis: 'x2', showlegend: false, hoverinfo: 'none' }
    ], {
        margin: { t: 40, b: 40, l: 50, r: 50 }, xaxis: { title: 'Sample Index' },
        xaxis2: { title: 'Time (s)', overlaying: 'x', side: 'top', showticklabels: true, showline: true, autorange: false, fixedrange: true },
        yaxis: { title: 'Velocity (m/s)' },
        yaxis2: { overlaying: 'y', side: 'right', range: [0, 1], showgrid: false, zeroline: false, showticklabels: false },
        hovermode: 'x unified'
    });

    // 6. Yaw
    Plotly.newPlot('yaw_plot', [
        { x: [], y: [], name: 'Gyro Z', mode: 'lines', type: 'scatter', visible: isPathCsv ? false : true, line: { color: '#94a3b8', width: 1 }, yaxis: 'y2',
          hovertemplate: 'Gz: %{y:.4f} rad/s<extra></extra>' },
        { x: [], y: [], name: 'Gyro Z Butterworth', mode: 'lines', type: 'scatter', visible: isPathCsv ? false : true, line: { color: '#38bdf8', dash: 'dash', width: 1.5 }, yaxis: 'y2',
          hovertemplate: 'Gz Butterworth: %{y:.4f} rad/s<extra></extra>' },
        { x: [], y: [], name: 'Yaw Angle', mode: 'lines', type: 'scatter', line: { color: '#7c3aed', width: 2 },
          hovertemplate: 'Yaw: %{y:.2f} deg<extra></extra>' },
        { x: [], y: [], name: 'UKF Yaw', mode: 'lines', type: 'scatter', line: { color: '#10b981', width: 2 },
          hovertemplate: 'UKF Yaw: %{y:.2f} deg<extra></extra>' },
        { x: [0, 100], y: [null], xaxis: 'x2', showlegend: false, hoverinfo: 'none' }
    ], {
        margin: { t: 40, b: 40, l: 60, r: 70 },
        xaxis: { title: 'Sample Index' },
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

    // 7b. Path Loss (FP Amp vs. Distance Scatter)
    const pathLossTraces = anchors.map((a, i) => ({
        x: [], y: [], name: `A${a.id}`, mode: 'markers', type: 'scatter',
        marker: { color: colors[i], size: 5, opacity: 0.7 }
    }));
    Plotly.newPlot('path_loss', pathLossTraces, {
        margin: { t: 40, b: 40, l: 50, r: 50 },
        xaxis: { title: 'Distance (m)', gridcolor: '#f1f5f9' },
        yaxis: { title: 'First Path Amplitude', gridcolor: '#f1f5f9' },
        hovermode: 'closest'
    });

    // 8. Pos Error
    const errTraces = [
        { x: [], y: [], name: 'Pos Error (Firmware)', mode: 'lines', type: 'scatter', line: { color: '#64748b', width: 1.5 } },
        { x: [], y: [], name: 'Pos Error (Rules)', mode: 'lines', type: 'scatter', line: { color: '#ef4444', width: 2 } },
        { x: [], y: [], name: 'Pos Error (Multilateration)', mode: 'lines', type: 'scatter', line: { color: '#d97706', width: 2 } },
        { x: [], y: [], name: 'Pos Error (Best Triplet)', mode: 'lines', type: 'scatter', line: { color: '#059669', width: 2 } },
        { x: [], y: [], name: 'Pos Error (UKF Fusion)', mode: 'lines', type: 'scatter', line: { color: '#8b5cf6', width: 2 } },
        { x: [], y: [], name: 'Pos Error (UKF Fusion + IMU Butterworth)', mode: 'lines', type: 'scatter', line: { color: '#0ea5e9', width: 2 } },
        { x: [0, 100], y: [null], xaxis: 'x2', showlegend: false, hoverinfo: 'none' }
    ];
    Plotly.newPlot('pos_error', errTraces, {
        margin: { t: 40, b: 40, l: 50, r: 50 }, xaxis: { title: 'Sample Index' },
        xaxis2: { title: 'Time (s)', overlaying: 'x', side: 'top', showticklabels: true, showline: true, autorange: false, fixedrange: true },
        yaxis: { title: 'Error (m)', range: [0, SIM_CONFIG.VIEW.MAX_ERROR_RANGE] }, hovermode: 'x unified'
    });

    // 9. Error Frame
    Plotly.newPlot('error_frame', [
        { x: [], y: [], name: isPathCsv ? 'error_cnt' : 'Log Error Frames', mode: 'lines', type: 'scatter', line: { color: '#475569', width: 1 }, fill: 'tozeroy' },
        { x: [0, 100], y: [null], xaxis: 'x2', showlegend: false, hoverinfo: 'none' }
    ], {
        margin: { t: 40, b: 40, l: 50, r: 50 }, xaxis: { title: 'Sample Index' },
        xaxis2: { title: 'Time (s)', overlaying: 'x', side: 'top', showticklabels: true, showline: true, autorange: false, fixedrange: true },
        yaxis: { title: isPathCsv ? 'error_cnt' : 'Frame Count' }, hovermode: 'x unified'
    });

    attachAllTimeAxisZoomSync();
}

// Helper for mean calculation
function meanErr(arr) {
    const valid = arr.filter(v => v !== null);
    return valid.length ? (valid.reduce((s, v) => s + v, 0) / valid.length).toFixed(3) : "N/A";
}
