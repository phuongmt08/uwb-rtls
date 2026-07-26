const TIME_AXIS_PLOTS = ['distances', 'scores', 'triplet_selection', 'triplet_debug', 'x_position', 'y_position', 'accel', 'velocity', 'yaw_plot', 'pos_error', 'error_frame', 'fp_amp', 'fp_snr'];

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

function tripletAxisData(anchorList) {
    const keys = [];
    const labels = [];
    for (let i = 0; i < anchorList.length - 2; i++) {
        for (let j = i + 1; j < anchorList.length - 1; j++) {
            for (let k = j + 1; k < anchorList.length; k++) {
                const ids = [anchorList[i].id, anchorList[j].id, anchorList[k].id].sort((a, b) => a - b);
                keys.push(ids.join(','));
                labels.push(ids.map(id => 'A' + id).join(','));
            }
        }
    }
    const valueByKey = {};
    keys.forEach((key, i) => {
        valueByKey[key] = i;
    });
    return {
        keys,
        labels,
        tickvals: keys.map((_, i) => i),
        valueByKey
    };
}

function initPlots(anchors, gt_square, rawData, samples) {
    const colors = SIM_CONFIG.VIEW.COLORS;
    const isPathCsv = rawData.log_format === 'path_csv';
    const tripletAxis = tripletAxisData(anchors);
    const gtOverlay = gt_square && gt_square.overlay;
    const gtOverlayPoints = gtOverlay && Array.isArray(gtOverlay.points) ? gtOverlay.points : [];
    const hasGtOverlay = gtOverlayPoints.length >= 2;
    // 1. Trajectory
    Plotly.newPlot('trajectory', [
        { x: anchors.map(a => a.x), y: anchors.map(a => a.y), mode: 'markers+text',
          name: 'Anchors', text: anchors.map(a => 'A'+a.id), textposition: 'top center',
          marker: { color: '#1e293b', size: 10, symbol: 'triangle-up' } },
        { x: gt_square.x, y: gt_square.y, mode: 'lines', name: 'Ground Truth',
          line: { color: '#f87171', dash: 'dot', width: 1 } },
        { x: isPathCsv ? samples.map(e => e.tril_x) : samples.map(e => e.px_fw),
          y: isPathCsv ? samples.map(e => e.tril_y) : samples.map(e => e.py_fw), mode: 'lines+markers',
          name: 'Trilateration Path', type: 'scattergl',
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
           visible: isPathCsv ? false : true, type: 'scattergl', marker: { size: 3 }, line: { color: '#0ea5e9', width: 2 } },
        { x: gtOverlayPoints.map(point => point[0]), y: gtOverlayPoints.map(point => point[1]), mode: 'lines',
          name: 'Ground Truth Detour', visible: hasGtOverlay, showlegend: false, type: 'scattergl',
          line: { color: '#f87171', dash: 'dot', width: 1 } }
    ], {
        margin: { t: 40, b: 100, l: 50, r: 50 },
        xaxis: { title: 'X (m)', gridcolor: '#f1f5f9' },
        yaxis: { title: 'Y (m)', gridcolor: '#f1f5f9', scaleanchor: 'x', scaleratio: 1 },
        hovermode: 'closest',
        legend: {
            orientation: 'h',
            yanchor: 'top',
            y: -0.15,
            xanchor: 'center',
            x: 0.5
        }
    }, {
        responsive: true,
        scrollZoom: true
    });

    const positionTraceNames = isPathCsv
        ? ['Trilateration', 'Data UKF', '', '', '', '']
        : ['Trilateration', 'Rules', 'Multilateration', 'Best Triplet', 'UKF Fusion', 'UKF Fusion + IMU Butterworth'];
    const positionTraceColors = ['#64748b', '#ef4444', '#d97706', '#059669', '#8b5cf6', '#0ea5e9'];
    const makePositionTraces = (axisLabel) => positionTraceNames.map((name, i) => ({
        x: [],
        y: [],
        name,
        mode: 'lines',
        type: 'scattergl',
        visible: name ? true : false,
        line: { color: positionTraceColors[i], width: i === 0 ? 1.5 : 2 },
        hovertemplate: `${axisLabel}: %{y:.4f} m<extra></extra>`
    })).concat([{ x: [0, 100], y: [null], xaxis: 'x2', showlegend: false, hoverinfo: 'none' }]);
    const positionLayout = (axisLabel) => ({
        margin: { t: 40, b: 40, l: 50, r: 50 },
        xaxis: { title: 'Sample Index' },
        xaxis2: { title: 'Time (s)', overlaying: 'x', side: 'top', showticklabels: true, showline: true, autorange: false, fixedrange: true },
        yaxis: { title: `${axisLabel} Position (m)` },
        hovermode: 'x unified'
    });
    Plotly.newPlot('x_position', makePositionTraces('X'), positionLayout('X'));
    Plotly.newPlot('y_position', makePositionTraces('Y'), positionLayout('Y'));

    // 2. Distances (4 traces per anchor: Raw, Gated, Rejected, Rescue)
    const distTraces = [];
    anchors.forEach((a, i) => {
        // i*4: Raw
        distTraces.push({
            x: samples.map((_, idx) => idx), y: samples.map(e => e.distances[i]),
            name: `A${a.id} Raw`, mode: 'lines', type: 'scattergl',
            line: { color: colors[i], width: 1.5 },
            opacity: 0.45,
            connectgaps: true,
            visible: 'legendonly'
        });
        // i*4+1: Gated
        distTraces.push({
            x: [], y: [], name: `A${a.id} Gated`, mode: 'lines', type: 'scattergl',
            line: { color: colors[i], width: 2.5 },
            connectgaps: true
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
        yaxis: { title: 'Distance (m)' }, hovermode: 'x unified'
    }, {
        responsive: true
    });

    // 3. D2 Scores
    const sTraces = [];
    for (let i = 0; i < 4; i++) sTraces.push({ x: [], y: [], name: 'A'+(i+1), mode: 'lines', type: 'scattergl', line: { color: colors[i], width: 1.5 }, connectgaps: true });
    sTraces.push({ x: [], y: [], mode: 'lines', name: 'T2 Reject', line: { color: '#ef4444', dash: 'dash' } });
    sTraces.push({ x: [], y: [], mode: 'lines', name: 'T2 Recover', line: { color: '#f59e0b', dash: 'dash' } });
    sTraces.push({ x: [0, 100], y: [null], xaxis: 'x2', showlegend: false, hoverinfo: 'none' });
    Plotly.newPlot('scores', sTraces, {
        margin: { t: 40, b: 40, l: 50, r: 50 }, xaxis: { title: 'Sample Index' },
        xaxis2: { title: 'Time (s)', overlaying: 'x', side: 'top', showticklabels: true, showline: true, autorange: false, fixedrange: true },
        yaxis: { title: 'D2 Score' }, hovermode: 'x unified'
    });

    // 4. Triplet selection timeline
    Plotly.newPlot('triplet_selection', [
        { x: [], y: [], name: 'Selected Triplet', mode: 'markers', type: 'scattergl',
          marker: { color: '#2563eb', size: 7, symbol: 'square' },
          hovertemplate: 'Sample: %{x}<br>Time: %{customdata[0]:.4f}s<br>Selected: %{customdata[1]}<br>UKF used: %{customdata[10]}<br>Score: %{customdata[12]:.4f}<br>Health: %{customdata[8]:.4f}<br>Candidates: %{customdata[4]}<extra></extra>' },
        { x: [], y: [], name: 'UKF Used Selected', mode: 'markers', type: 'scattergl',
          marker: { color: '#16a34a', size: 10, symbol: 'circle-open', line: { width: 2, color: '#16a34a' } },
          hovertemplate: 'Sample: %{x}<br>Time: %{customdata[0]:.4f}s<br>UKF update used: %{customdata[11]}<br>Selected: %{customdata[1]}<extra></extra>' },
        { x: [], y: [], name: 'No UKF Update', mode: 'markers', type: 'scattergl',
          marker: { color: '#dc2626', size: 9, symbol: 'x' },
          hovertemplate: 'Sample: %{x}<br>Time: %{customdata[0]:.4f}s<br>No UKF update<br>Selected: %{customdata[1]}<extra></extra>' },
        { x: [], y: [], name: 'Held Previous', mode: 'markers', type: 'scattergl',
          marker: { color: '#f97316', size: 9, symbol: 'triangle-up' },
          hovertemplate: 'Sample: %{x}<br>Time: %{customdata[0]:.4f}s<br>Held: %{customdata[1]}<br>Challenger: %{customdata[3]}<br>Challenger score: %{customdata[7]:.4f}<extra></extra>' },
        { x: [0, 100], y: [null], xaxis: 'x2', showlegend: false, hoverinfo: 'none' }
    ], {
        margin: { t: 40, b: 40, l: 90, r: 40 },
        xaxis: { title: 'Sample Index' },
        xaxis2: { title: 'Time (s)', overlaying: 'x', side: 'top', showticklabels: true, showline: true, autorange: false, fixedrange: true },
        yaxis: {
            title: 'Triplet',
            tickvals: tripletAxis.tickvals,
            ticktext: tripletAxis.labels,
            range: [-0.75, Math.max(0.75, tripletAxis.keys.length - 0.25)],
            gridcolor: '#f1f5f9'
        },
        hovermode: 'closest'
    });

    // 5. Triplet debug. GDOP is diagnostic only and does not enter the score.
    Plotly.newPlot('triplet_debug', [
        { x: [], y: [], name: 'Selected GDOP', mode: 'lines+markers', type: 'scatter',
          line: { color: '#0ea5e9', width: 2 }, marker: { size: 4 },
          hovertemplate: 'Time: %{customdata[0]:.4f}s<br>Triplet: %{customdata[1]}<br>Candidates: %{customdata[4]}<br>GDOP: %{y:.4f}<br>Avg D2: %{customdata[6]:.4f}<br>Residual: %{customdata[5]:.4f}m<extra></extra>' },
        { x: [], y: [], name: 'Triplet Score', mode: 'lines', type: 'scatter', yaxis: 'y2',
          line: { color: '#8b5cf6', width: 2 },
          hovertemplate: 'Time: %{customdata[0]:.4f}s<br>Triplet: %{customdata[1]}<br>Status: %{customdata[2]}<br>Challenger: %{customdata[3]}<br>Challenger score: %{customdata[7]:.4f}<br>Challenger health: %{customdata[9]:.4f}<br>Score: %{y:.4f}<extra></extra>' },
        { x: [], y: [], name: 'Health Penalty', mode: 'lines', type: 'scatter', yaxis: 'y2',
          line: { color: '#db2777', width: 2 },
          hovertemplate: 'Time: %{customdata[0]:.4f}s<br>Triplet: %{customdata[1]}<br>Health penalty: %{y:.4f}<extra></extra>' },
        { x: [], y: [], name: 'D2 Penalty', mode: 'lines', type: 'scatter', yaxis: 'y2',
          line: { color: '#ef4444', width: 1.5 }, visible: 'legendonly',
          hovertemplate: 'Time: %{customdata[0]:.4f}s<br>Triplet: %{customdata[1]}<br>D2 penalty: %{y:.4f}<extra></extra>' },
        { x: [], y: [], name: 'FP Penalty', mode: 'lines', type: 'scatter', yaxis: 'y2',
          line: { color: '#10b981', width: 1.5 }, visible: 'legendonly',
          hovertemplate: 'Time: %{customdata[0]:.4f}s<br>Triplet: %{customdata[1]}<br>FP penalty: %{y:.4f}<extra></extra>' },
        { x: [], y: [], name: 'Residual Penalty', mode: 'lines', type: 'scatter', yaxis: 'y2',
          line: { color: '#f59e0b', width: 1.5 }, visible: 'legendonly',
          hovertemplate: 'Time: %{customdata[0]:.4f}s<br>Triplet: %{customdata[1]}<br>Residual penalty: %{y:.4f}<extra></extra>' },
        { x: [], y: [], name: 'Distance Penalty', mode: 'lines', type: 'scatter', yaxis: 'y2',
          line: { color: '#64748b', width: 1.5 }, visible: 'legendonly',
          hovertemplate: 'Time: %{customdata[0]:.4f}s<br>Triplet: %{customdata[1]}<br>Distance penalty: %{y:.4f}<extra></extra>' },
        { x: [], y: [], name: 'GDOP Penalty', mode: 'lines', type: 'scatter', yaxis: 'y2',
          line: { color: '#38bdf8', width: 1, dash: 'dot' }, visible: 'legendonly',
          hovertemplate: 'Time: %{customdata[0]:.4f}s<br>Triplet: %{customdata[1]}<br>GDOP penalty: %{y:.4f}<extra></extra>' },
        { x: [], y: [], name: 'A1 Health', mode: 'lines', type: 'scatter', yaxis: 'y2',
          line: { color: colors[0], width: 1, dash: 'dot' }, visible: 'legendonly',
          hovertemplate: 'Time: %{customdata[0]:.4f}s<br>A1 health: %{y:.4f}<br>Reject streak: %{customdata[1]}<br>Rescue streak: %{customdata[2]}<br>Reject EWMA: %{customdata[3]:.4f}<br>Rescue EWMA: %{customdata[4]:.4f}<extra></extra>' },
        { x: [], y: [], name: 'A2 Health', mode: 'lines', type: 'scatter', yaxis: 'y2',
          line: { color: colors[1], width: 1, dash: 'dot' }, visible: 'legendonly',
          hovertemplate: 'Time: %{customdata[0]:.4f}s<br>A2 health: %{y:.4f}<br>Reject streak: %{customdata[1]}<br>Rescue streak: %{customdata[2]}<br>Reject EWMA: %{customdata[3]:.4f}<br>Rescue EWMA: %{customdata[4]:.4f}<extra></extra>' },
        { x: [], y: [], name: 'A3 Health', mode: 'lines', type: 'scatter', yaxis: 'y2',
          line: { color: colors[2], width: 1, dash: 'dot' }, visible: 'legendonly',
          hovertemplate: 'Time: %{customdata[0]:.4f}s<br>A3 health: %{y:.4f}<br>Reject streak: %{customdata[1]}<br>Rescue streak: %{customdata[2]}<br>Reject EWMA: %{customdata[3]:.4f}<br>Rescue EWMA: %{customdata[4]:.4f}<extra></extra>' },
        { x: [], y: [], name: 'A4 Health', mode: 'lines', type: 'scatter', yaxis: 'y2',
          line: { color: colors[3], width: 1, dash: 'dot' }, visible: 'legendonly',
          hovertemplate: 'Time: %{customdata[0]:.4f}s<br>A4 health: %{y:.4f}<br>Reject streak: %{customdata[1]}<br>Rescue streak: %{customdata[2]}<br>Reject EWMA: %{customdata[3]:.4f}<br>Rescue EWMA: %{customdata[4]:.4f}<extra></extra>' },
        { x: [], y: [], name: 'Held Previous', mode: 'markers', type: 'scatter', yaxis: 'y2',
          marker: { color: '#dc2626', symbol: 'triangle-up', size: 9 },
          hovertemplate: 'Time: %{customdata[0]:.4f}s<br>Held: %{customdata[1]}<br>Challenger: %{customdata[3]}<br>Challenger score: %{customdata[7]:.4f}<br>Challenger health: %{customdata[9]:.4f}<br>Held score: %{y:.4f}<extra></extra>' },
        { x: [0, 100], y: [null], xaxis: 'x2', showlegend: false, hoverinfo: 'none' }
    ], {
        margin: { t: 40, b: 40, l: 60, r: 70 },
        xaxis: { title: 'Sample Index' },
        xaxis2: { title: 'Time (s)', overlaying: 'x', side: 'top', showticklabels: true, showline: true, autorange: false, fixedrange: true },
        yaxis: { title: 'GDOP', side: 'left', gridcolor: '#f1f5f9' },
        yaxis2: { title: 'Score / Penalty', overlaying: 'y', side: 'right', range: [0, 1], showgrid: false },
        hovermode: 'x unified'
    });

    // 6. Accel
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
        { x: [], y: [], name: 'Pos Error (Trilateration)', mode: 'lines', type: 'scatter', line: { color: '#64748b', width: 1.5 } },
        { x: [], y: [], name: 'Pos Error (Rules)', mode: 'lines', type: 'scatter', line: { color: '#ef4444', width: 2 } },
        { x: [], y: [], name: 'Pos Error (Multilateration)', mode: 'lines', type: 'scatter', line: { color: '#d97706', width: 2 } },
        { x: [], y: [], name: 'Pos Error (Best Triplet)', mode: 'lines', type: 'scatter', line: { color: '#059669', width: 2 } },
        { x: [], y: [], name: 'Pos Error (UKF Fusion)', mode: 'lines', type: 'scatter', line: { color: '#8b5cf6', width: 2 } },
        { x: [], y: [], name: 'Pos Error (UKF Fusion + IMU Butterworth)', mode: 'lines', type: 'scatter', line: { color: '#0ea5e9', width: 2 } },
        { x: [0, 100], y: [null], xaxis: 'x2', showlegend: false, hoverinfo: 'none' }
    ];
    Plotly.newPlot('pos_error', errTraces, {
        margin: { t: 58, b: 42, l: 52, r: 18 },
        legend: { orientation: 'h', x: 0, y: 1.18, xanchor: 'left', yanchor: 'bottom', font: { size: 10 } },
        xaxis: { title: 'Sample Index' },
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
    const valid = arr.filter(v => Number.isFinite(v));
    return valid.length ? (valid.reduce((s, v) => s + v, 0) / valid.length).toFixed(3) : "N/A";
}
