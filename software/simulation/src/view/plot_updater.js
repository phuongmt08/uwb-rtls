function updatePlots(res, samples, rawData) {
    const { 
        simPath, simPathRuled, simPathWLS, simPathTriplet, simPathUKF, simPathUKF_plot,
        wlsInfo, bestTripletInfo,
        plotData, gatedDist, d2Scores, rejectIdx, rescueIdx, rescueDist,
        pos_errors_fw, pos_errors, pos_errors_wls, pos_errors_triplet, pos_errors_ukf,
        x_axis, total_time 
    } = res;

    // 1. Trajectory
    Plotly.restyle('trajectory', { 
        x: [rawData.fw_path.x.slice(0, x_axis.length), simPath.x, simPathRuled.x, simPathWLS.x, simPathTriplet.x, simPathUKF.x],
        y: [rawData.fw_path.y.slice(0, x_axis.length), simPath.y, simPathRuled.y, simPathWLS.y, simPathTriplet.y, simPathUKF.y],
        text: [
            samples.slice(0, x_axis.length).map((_, i) => 'Idx: ' + i + '<br>Mask: ' + rawData.fw_path.mask[i] + ' (A' + decodeMask(rawData.fw_path.mask[i]) + ')'), 
            simPath.x.map((_, i) => 'Idx: ' + i),
            simPathRuled.x.map((_, i) => 'Idx: ' + i),
            simPathWLS.x.map((_, i) => 'Idx: ' + i + '<br>Multilateration: ' + wlsInfo[i]),
            simPathTriplet.x.map((_, i) => 'Idx: ' + i + '<br>Best Triplet: ' + bestTripletInfo[i]),
            simPathUKF.x.map((_, i) => 'Entry Idx: ' + i + '<br>UKF Fusion')
        ]
    }, [2, 3, 4, 5, 6, 7]);

    // 2. Distances & Scores
    for (let i = 0; i < 4; i++) {
        Plotly.restyle('distances', {
            x: [x_axis, x_axis, rejectIdx[i], rescueIdx[i]],
            y: [
                samples.slice(0, x_axis.length).map(s => s.distances[i] <= 0.1 ? null : s.distances[i]),
                gatedDist[i],
                rejectIdx[i].map(idx => samples[idx].distances[i]),
                rescueDist[i]
            ],
            customdata: [plotData.times, plotData.times, rejectIdx[i].map(idx => plotData.times[idx]), rescueIdx[i].map(idx => plotData.times[idx])],
            hovertemplate: 'Time: %{customdata:.4f}s | %{y:.6f}m<extra></extra>'
        }, [i*4, i*4+1, i*4+2, i*4+3]);

        Plotly.restyle('scores', {
            x: [x_axis], y: [d2Scores[i]],
            customdata: [plotData.times],
            hovertemplate: 'Time: %{customdata:.4f}s | D2: %{y:.6f}<extra></extra>'
        }, [i]);
    }
    const T2_high = parseFloat(document.getElementById('t2_high_range').value);
    const T2_low  = parseFloat(document.getElementById('t2_low_range').value);
    Plotly.restyle('scores', { x: [[0, x_axis.length], [0, x_axis.length]], y: [[T2_high, T2_high], [T2_low, T2_low]] }, [4, 5]);
    Plotly.relayout('scores', { 'yaxis.autorange': true });

    // 3. Other plots
    Plotly.restyle('accel', { x: [x_axis, x_axis], y: [plotData.ax, plotData.ay], customdata: [plotData.times, plotData.times] }, [0, 1]);
    Plotly.restyle('velocity', { x: [x_axis, x_axis, x_axis, x_axis, x_axis], y: [[], [], plotData.vx, plotData.vy, plotData.zupt], customdata: [[], [], plotData.times, plotData.times, plotData.times] }, [0, 1, 2, 3, 4]);
    Plotly.restyle('yaw_plot', { x: [x_axis, x_axis], y: [plotData.gz, plotData.yaw], customdata: [plotData.times, plotData.times] }, [0, 1]);

    Plotly.restyle('pos_error', {
        x: [x_axis, x_axis, x_axis, x_axis, x_axis],
        y: [pos_errors_fw, pos_errors, pos_errors_wls, pos_errors_triplet, pos_errors_ukf],
        name: [
            `Pos Error (Firmware) Mean: ${meanErr(pos_errors_fw)}m`,
            `Pos Error (Rules) Mean: ${meanErr(pos_errors)}m`,
            `Pos Error (Multilateration) Mean: ${meanErr(pos_errors_wls)}m`,
            `Pos Error (Best Triplet) Mean: ${meanErr(pos_errors_triplet)}m`,
            `Pos Error (UKF Fusion) Mean: ${meanErr(pos_errors_ukf)}m`
        ],
        customdata: [plotData.times, plotData.times, plotData.times, plotData.times, plotData.times]
    }, [0, 1, 2, 3, 4]);

    const csv_errors = samples.slice(0, x_axis.length).map(s => s.err);
    Plotly.restyle('error_frame', { x: [x_axis], y: [csv_errors], customdata: [plotData.times] }, [0]);

    const sliced_amp = [0,1,2,3].map(i => (rawData.fp_logs.amp[i] || []).slice(0, x_axis.length).map(v => v === 0 ? null : v));
    const sliced_snr = [0,1,2,3].map(i => (rawData.fp_logs.snr[i] || []).slice(0, x_axis.length).map(v => v === 0 ? null : v));
    for(let i=0; i<4; ++i) {
        Plotly.restyle('fp_amp', { x: [x_axis], y: [sliced_amp[i]], customdata: [plotData.times] }, [i]);
        Plotly.restyle('fp_snr', { x: [x_axis], y: [sliced_snr[i]], customdata: [plotData.times] }, [i]);
    }
    Plotly.relayout('fp_amp', { 'yaxis.autorange': true });
    Plotly.relayout('fp_snr', { 'yaxis.autorange': true });

    // 4. Sync layout
    const syncLayout = { 'xaxis.range': [0, x_axis.length], 'xaxis2.range': [0, total_time], 'xaxis2.showticklabels': true, 'xaxis2.autorange': false };
    ['distances', 'scores', 'accel', 'velocity', 'yaw_plot', 'pos_error', 'error_frame', 'fp_amp', 'fp_snr'].forEach(id => Plotly.relayout(id, syncLayout));
}
