function updatePlots(res, samples, rawData) {
    const { 
        simPathRuled, simPathWLS, simPathTriplet, simPathUKF, simPathUKF_lpf, simPathUKF_plot,
        wlsInfo, bestTripletInfo,
        plotData, gatedDist, d2Scores, rejectIdx, rescueIdx, rescueDist,
        pos_errors_fw, pos_errors, pos_errors_wls, pos_errors_triplet, pos_errors_ukf, pos_errors_ukf_lpf,
        x_axis, total_time 
    } = res;

    // 1. Trajectory
    Plotly.restyle('trajectory', { 
        x: [rawData.fw_path.x.slice(0, x_axis.length), simPathRuled.x, simPathWLS.x, simPathTriplet.x, simPathUKF.x, simPathUKF_lpf.x],
        y: [rawData.fw_path.y.slice(0, x_axis.length), simPathRuled.y, simPathWLS.y, simPathTriplet.y, simPathUKF.y, simPathUKF_lpf.y],
        text: [
            samples.slice(0, x_axis.length).map((_, i) => 'Idx: ' + i + '<br>Mask: ' + rawData.fw_path.mask[i] + ' (A' + decodeMask(rawData.fw_path.mask[i]) + ')'), 
            simPathRuled.x.map((_, i) => 'Idx: ' + i),
            simPathWLS.x.map((_, i) => 'Idx: ' + i + '<br>Multilateration: ' + wlsInfo[i]),
            simPathTriplet.x.map((_, i) => 'Idx: ' + i + '<br>Best Triplet: ' + bestTripletInfo[i]),
            simPathUKF.x.map((_, i) => 'Entry Idx: ' + i + '<br>UKF Fusion'),
            simPathUKF_lpf.x.map((_, i) => 'Entry Idx: ' + i + '<br>UKF Fusion + IMU LPF')
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
    Plotly.restyle('accel', { 
        x: [x_axis, x_axis, x_axis, x_axis, x_axis],
        y: [plotData.ax, plotData.ay, plotData.ax_lpf, plotData.ay_lpf, plotData.zupt],
        customdata: [plotData.times, plotData.times, plotData.times, plotData.times, plotData.times]
    }, [0, 1, 2, 3, 4]);

    const spectrum = plotData.accelSpectrum || {};
    const cutoff = parseFloat(document.getElementById('imu_lpf_cutoff_range').value);
    const maxSpecY = [spectrum.ax, spectrum.ay, spectrum.ax_lpf, spectrum.ay_lpf]
        .flatMap(s => s && s.mag ? s.mag : [])
        .filter(Number.isFinite)
        .reduce((m, v) => Math.max(m, v), 0);
    Plotly.restyle('accel_spectrum', {
        x: [
            spectrum.ax ? spectrum.ax.freq : [],
            spectrum.ay ? spectrum.ay.freq : [],
            spectrum.ax_lpf ? spectrum.ax_lpf.freq : [],
            spectrum.ay_lpf ? spectrum.ay_lpf.freq : [],
            [cutoff, cutoff]
        ],
        y: [
            spectrum.ax ? spectrum.ax.mag : [],
            spectrum.ay ? spectrum.ay.mag : [],
            spectrum.ax_lpf ? spectrum.ax_lpf.mag : [],
            spectrum.ay_lpf ? spectrum.ay_lpf.mag : [],
            [0, maxSpecY || 1]
        ]
    }, [0, 1, 2, 3, 4]);
    Plotly.restyle('velocity', {
        x: [x_axis, x_axis, x_axis, x_axis, x_axis, x_axis, x_axis],
        y: [plotData.vx_raw, plotData.vy_raw, plotData.vx, plotData.vy, plotData.vx_lpf, plotData.vy_lpf, plotData.zupt],
        customdata: [plotData.times, plotData.times, plotData.times, plotData.times, plotData.times, plotData.times, plotData.times]
    }, [0, 1, 2, 3, 4, 5, 6]);
    Plotly.restyle('yaw_plot', { 
        x: [x_axis, x_axis, x_axis, x_axis],
        y: [plotData.gz, plotData.gz_lpf, plotData.yaw, plotData.ukf_yaw],
        customdata: [plotData.times, plotData.times, plotData.times, plotData.times]
    }, [0, 1, 2, 3]);

    Plotly.restyle('pos_error', {
        x: [x_axis, x_axis, x_axis, x_axis, x_axis, x_axis],
        y: [pos_errors_fw, pos_errors, pos_errors_wls, pos_errors_triplet, pos_errors_ukf, pos_errors_ukf_lpf],
        name: [
            `Pos Error (Firmware) Mean: ${meanErr(pos_errors_fw)}m`,
            `Pos Error (Rules) Mean: ${meanErr(pos_errors)}m`,
            `Pos Error (Multilateration) Mean: ${meanErr(pos_errors_wls)}m`,
            `Pos Error (Best Triplet) Mean: ${meanErr(pos_errors_triplet)}m`,
            `Pos Error (UKF Fusion) Mean: ${meanErr(pos_errors_ukf)}m`,
            `Pos Error (UKF Fusion + IMU LPF) Mean: ${meanErr(pos_errors_ukf_lpf)}m`
        ],
        customdata: [plotData.times, plotData.times, plotData.times, plotData.times, plotData.times, plotData.times]
    }, [0, 1, 2, 3, 4, 5]);

    const csv_errors = samples.slice(0, x_axis.length).map(s => s.err);
    Plotly.restyle('error_frame', { x: [x_axis], y: [csv_errors], customdata: [plotData.times] }, [0]);

    function getStats(arr) {
        const valid = arr.filter(v => v !== null && v !== undefined && !isNaN(v));
        if (valid.length === 0) return { min: "N/A", max: "N/A", mean: "N/A" };
        const min = Math.min(...valid);
        const max = Math.max(...valid);
        const mean = valid.reduce((a, b) => a + b, 0) / valid.length;
        return {
            min: min.toFixed(1),
            max: max.toFixed(1),
            mean: mean.toFixed(1)
        };
    }

    const pathLossData = [0,1,2,3].map(i => {
        const x = [];
        const y = [];
        samples.slice(0, x_axis.length).forEach((s, idx) => {
            const dist = s.distances[i];
            const amp = rawData.fp_logs.amp[i] ? rawData.fp_logs.amp[i][idx] : 0;
            if (dist > 0.1 && amp > 0) {
                x.push(dist);
                y.push(amp);
            }
        });
        return { x, y };
    });

    const sliced_amp = [0,1,2,3].map(i => (rawData.fp_logs.amp[i] || []).slice(0, x_axis.length).map(v => (v <= 0 || v > 5000) ? null : v));
    const sliced_snr = [0,1,2,3].map(i => (rawData.fp_logs.snr[i] || []).slice(0, x_axis.length).map(v => (v <= 0 || v > 5000) ? null : v));
    for(let i=0; i<4; ++i) {
        const ampStats = getStats(sliced_amp[i]);
        Plotly.restyle('fp_amp', { 
            x: [x_axis], 
            y: [sliced_amp[i]], 
            name: [`A${i+1} (Min: ${ampStats.min}, Max: ${ampStats.max}, Mean: ${ampStats.mean})`],
            customdata: [plotData.times] 
        }, [i]);

        const snrStats = getStats(sliced_snr[i]);
        Plotly.restyle('fp_snr', { 
            x: [x_axis], 
            y: [sliced_snr[i]], 
            name: [`A${i+1} (Min: ${snrStats.min}, Max: ${snrStats.max}, Mean: ${snrStats.mean})`],
            customdata: [plotData.times] 
        }, [i]);

        Plotly.restyle('path_loss', { x: [pathLossData[i].x], y: [pathLossData[i].y] }, [i]);
    }
    Plotly.relayout('fp_amp', { 'yaxis.autorange': true });
    Plotly.relayout('fp_snr', { 'yaxis.autorange': true });
    Plotly.relayout('path_loss', { 'xaxis.autorange': true, 'yaxis.autorange': true });

    // 4. Sync layout
    setTimeAxisSyncData(x_axis, plotData.times, total_time);
    const syncLayout = { 'xaxis.range': [0, x_axis.length], 'xaxis2.range': [0, total_time], 'xaxis2.showticklabels': true, 'xaxis2.autorange': false };
    TIME_AXIS_PLOTS.forEach(id => Plotly.relayout(id, syncLayout));
}
