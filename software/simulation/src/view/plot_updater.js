function updatePlots(res, samples, rawData) {
    const { 
        simPathRuled, simPathWLS, simPathTriplet, simPathUKF, simPathUKF_lpf, simPathUKF_plot,
        simPathUKF_lpf_plot,
        wlsInfo, bestTripletInfo,
        tripletDebug,
        plotData, gatedDist, d2Scores, rejectIdx, rescueIdx, rescueDist,
        pos_errors_fw, pos_errors, pos_errors_wls, pos_errors_triplet, pos_errors_ukf, pos_errors_ukf_lpf,
        x_axis, total_time 
    } = res;
    const isPathCsv = isRecordedPathLog(rawData);
    const positionSamples = samples.slice(0, x_axis.length);
    const anchorCount = gatedDist.length;
    const selectedAnchorsText = (mask) => {
        const numericMask = Number(mask) || 0;
        const selected = anchors
            .filter(anchor => (numericMask & (1 << (anchor.id - 1))) !== 0)
            .map(anchor => `A${anchor.id}`);
        return selected.length ? selected.join(', ') : 'None';
    };

    // 1. Trajectory
    if (isPathCsv) {
        const pathSamples = positionSamples;
        Plotly.restyle('trajectory', {
            x: [
                pathSamples.map(s => s.tril_x),
                pathSamples.map(s => s.ukf_x),
                [], [], [], []
            ],
            y: [
                pathSamples.map(s => s.tril_y),
                pathSamples.map(s => s.ukf_y),
                [], [], [], []
            ],
            text: [
                pathSamples.map((s, i) => `Idx: ${i}<br>Tril: ${s.tril_x}, ${s.tril_y}`),
                pathSamples.map((s, i) =>
                    `Idx: ${i}` +
                    `<br>UKF: ${s.ukf_x}, ${s.ukf_y}` +
                    `<br>Selected anchors: ${selectedAnchorsText(s.mask)}` +
                    `<br>Mask: 0x${(Number(s.mask) || 0).toString(16).toUpperCase()}`
                ),
                [], [], [], []
            ],
            name: ['Trilateration Path', 'UKF Path', '', '', '', ''],
            visible: [true, true, false, false, false, false]
        }, [2, 3, 4, 5, 6, 7]);
    } else {
        Plotly.restyle('trajectory', { 
            x: [rawData.fw_path.x.slice(0, x_axis.length), simPathRuled.x, simPathWLS.x, simPathTriplet.x, simPathUKF.x, simPathUKF_lpf.x],
            y: [rawData.fw_path.y.slice(0, x_axis.length), simPathRuled.y, simPathWLS.y, simPathTriplet.y, simPathUKF.y, simPathUKF_lpf.y],
            text: [
                samples.slice(0, x_axis.length).map((_, i) => 'Idx: ' + i + '<br>Mask: ' + rawData.fw_path.mask[i] + ' (A' + decodeMask(rawData.fw_path.mask[i]) + ')'), 
                simPathRuled.x.map((_, i) => 'Idx: ' + i),
                simPathWLS.x.map((_, i) => 'Idx: ' + i + '<br>Multilateration: ' + wlsInfo[i]),
                simPathTriplet.x.map((_, i) => 'Idx: ' + i + '<br>Best Triplet: ' + bestTripletInfo[i]),
                simPathUKF.x.map((_, i) => 'Entry Idx: ' + i + '<br>UKF Fusion'),
            simPathUKF_lpf.x.map((_, i) => 'Entry Idx: ' + i + '<br>UKF Fusion + IMU Butterworth')
            ]
        }, [2, 3, 4, 5, 6, 7]);
    }

    if (isPathCsv) {
        Plotly.restyle('x_position', {
            x: [x_axis, x_axis, [], [], [], []],
            y: [
                positionSamples.map(s => s.tril_x),
                positionSamples.map(s => s.ukf_x),
                [], [], [], []
            ],
            name: ['Trilateration X', 'Data UKF X', '', '', '', ''],
            visible: [true, true, false, false, false, false],
            customdata: [plotData.times, plotData.times, [], [], [], []]
        }, [0, 1, 2, 3, 4, 5]);
        Plotly.restyle('y_position', {
            x: [x_axis, x_axis, [], [], [], []],
            y: [
                positionSamples.map(s => s.tril_y),
                positionSamples.map(s => s.ukf_y),
                [], [], [], []
            ],
            name: ['Trilateration Y', 'Data UKF Y', '', '', '', ''],
            visible: [true, true, false, false, false, false],
            customdata: [plotData.times, plotData.times, [], [], [], []]
        }, [0, 1, 2, 3, 4, 5]);
    } else {
        Plotly.restyle('x_position', {
            x: [x_axis, x_axis, x_axis, x_axis, x_axis, x_axis],
            y: [
                rawData.fw_path.x.slice(0, x_axis.length),
                simPathRuled.x,
                simPathWLS.x,
                simPathTriplet.x,
                simPathUKF_plot.x,
                simPathUKF_lpf_plot.x
            ],
            name: ['Trilateration X', 'Rules X', 'Multilateration X', 'Best Triplet X', 'UKF Fusion X', 'UKF Fusion + IMU Butterworth X'],
            visible: [true, true, true, true, true, true],
            customdata: [plotData.times, plotData.times, plotData.times, plotData.times, plotData.times, plotData.times]
        }, [0, 1, 2, 3, 4, 5]);
        Plotly.restyle('y_position', {
            x: [x_axis, x_axis, x_axis, x_axis, x_axis, x_axis],
            y: [
                rawData.fw_path.y.slice(0, x_axis.length),
                simPathRuled.y,
                simPathWLS.y,
                simPathTriplet.y,
                simPathUKF_plot.y,
                simPathUKF_lpf_plot.y
            ],
            name: ['Trilateration Y', 'Rules Y', 'Multilateration Y', 'Best Triplet Y', 'UKF Fusion Y', 'UKF Fusion + IMU Butterworth Y'],
            visible: [true, true, true, true, true, true],
            customdata: [plotData.times, plotData.times, plotData.times, plotData.times, plotData.times, plotData.times]
        }, [0, 1, 2, 3, 4, 5]);
    }

    // 2. Distances & Scores
    for (let i = 0; i < anchorCount; i++) {
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
    Plotly.restyle('scores', {
        x: [[0, x_axis.length], [0, x_axis.length]],
        y: [[T2_high, T2_high], [T2_low, T2_low]]
    }, [anchorCount, anchorCount + 1]);
    Plotly.relayout('scores', { 'yaxis.autorange': true });

    const debug = tripletDebug || {};
    const debugArr = (name) => Array.isArray(debug[name]) ? debug[name] : x_axis.map(() => null);
    const tripletName = (key) => key ? key.split(',').map(id => 'A' + id).join(',') : 'None';
    const selectedKeys = debugArr('key');
    const challengerKeys = debugArr('challengerKey');
    const debugCustom = x_axis.map((_, i) => [
        plotData.times[i],
        tripletName(selectedKeys[i]),
        debug.held && debug.held[i] ? 'held previous' : 'selected best',
        tripletName(challengerKeys[i]),
        debug.candidateCount && debug.candidateCount[i] ? debug.candidateCount[i] : 0,
        debug.residual && Number.isFinite(debug.residual[i]) ? debug.residual[i] : null,
        debug.avgD2 && Number.isFinite(debug.avgD2[i]) ? debug.avgD2[i] : null,
        debug.challengerScore && Number.isFinite(debug.challengerScore[i]) ? debug.challengerScore[i] : null,
        debug.healthPenalty && Number.isFinite(debug.healthPenalty[i]) ? debug.healthPenalty[i] : null,
        debug.challengerHealthPenalty && Number.isFinite(debug.challengerHealthPenalty[i]) ? debug.challengerHealthPenalty[i] : null,
        debug.ukfUsed && debug.ukfUsed[i] ? 'yes' : 'no',
        debug.ukfKey && debug.ukfKey[i] ? tripletName(debug.ukfKey[i]) : 'None',
        debug.score && Number.isFinite(debug.score[i]) ? debug.score[i] : null
    ]);
    const nestedDebugArr = (name, anchorIndex) => {
        return Array.isArray(debug[name]) && Array.isArray(debug[name][anchorIndex])
            ? debug[name][anchorIndex]
            : x_axis.map(() => null);
    };
    const healthCustom = (anchorIndex) => x_axis.map((_, i) => [
        plotData.times[i],
        nestedDebugArr('rejectStreakByAnchor', anchorIndex)[i] || 0,
        nestedDebugArr('rescueStreakByAnchor', anchorIndex)[i] || 0,
        nestedDebugArr('rejectRateByAnchor', anchorIndex)[i],
        nestedDebugArr('rescueRateByAnchor', anchorIndex)[i]
    ]);
    const heldX = [];
    const heldY = [];
    const heldCustom = [];
    const heldFlags = debugArr('held');
    const scores = debugArr('score');
    const tripletAxis = tripletAxisData(anchors);
    const noTripletY = -0.5;
    const tripletValue = (key) => {
        return Object.prototype.hasOwnProperty.call(tripletAxis.valueByKey, key)
            ? tripletAxis.valueByKey[key]
            : null;
    };
    const selectedY = selectedKeys.map(tripletValue);
    const ukfUsedFlags = debugArr('ukfUsed');
    const ukfY = selectedY.map((y, i) => ukfUsedFlags[i] && Number.isFinite(y) ? y : null);
    const noUpdateX = [];
    const noUpdateY = [];
    const noUpdateCustom = [];
    const heldTripletX = [];
    const heldTripletY = [];
    const heldTripletCustom = [];
    selectedY.forEach((y, i) => {
        if (!ukfUsedFlags[i]) {
            noUpdateX.push(x_axis[i]);
            noUpdateY.push(Number.isFinite(y) ? y : noTripletY);
            noUpdateCustom.push(debugCustom[i]);
        }
        if (heldFlags[i] && Number.isFinite(y)) {
            heldTripletX.push(x_axis[i]);
            heldTripletY.push(y);
            heldTripletCustom.push(debugCustom[i]);
        }
    });
    heldFlags.forEach((held, i) => {
        if (held && Number.isFinite(scores[i])) {
            heldX.push(x_axis[i]);
            heldY.push(scores[i]);
            heldCustom.push(debugCustom[i]);
        }
    });
    Plotly.restyle('triplet_selection', {
        x: [x_axis, x_axis, noUpdateX, heldTripletX],
        y: [selectedY, ukfY, noUpdateY, heldTripletY],
        customdata: [debugCustom, debugCustom, noUpdateCustom, heldTripletCustom]
    }, [0, 1, 2, 3]);
    Plotly.relayout('triplet_selection', {
        'yaxis.tickvals': [noTripletY].concat(tripletAxis.tickvals),
        'yaxis.ticktext': ['None'].concat(tripletAxis.labels),
        'yaxis.range': [-0.75, Math.max(0.75, tripletAxis.keys.length - 0.25)]
    });

    Plotly.restyle('triplet_debug', {
        x: [x_axis, x_axis, x_axis, x_axis, x_axis, x_axis, x_axis, x_axis, x_axis, x_axis, x_axis, x_axis, heldX],
        y: [
            debugArr('gdop'),
            scores,
            debugArr('healthPenalty'),
            debugArr('d2Penalty'),
            debugArr('fpPenalty'),
            debugArr('residualPenalty'),
            debugArr('distPenalty'),
            debugArr('gdopPenalty'),
            nestedDebugArr('healthByAnchor', 0),
            nestedDebugArr('healthByAnchor', 1),
            nestedDebugArr('healthByAnchor', 2),
            nestedDebugArr('healthByAnchor', 3),
            heldY
        ],
        customdata: [
            debugCustom,
            debugCustom,
            debugCustom,
            debugCustom,
            debugCustom,
            debugCustom,
            debugCustom,
            debugCustom,
            healthCustom(0),
            healthCustom(1),
            healthCustom(2),
            healthCustom(3),
            heldCustom
        ]
    }, [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]);
    Plotly.relayout('triplet_debug', { 'yaxis.autorange': true });

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
    const csvYaw = isPathCsv ? samples.slice(0, x_axis.length).map(s => s.yaw) : plotData.yaw;
    const csvUkfYaw = isPathCsv ? samples.slice(0, x_axis.length).map(s => s.ukf_yaw) : plotData.ukf_yaw;
    Plotly.restyle('yaw_plot', { 
        x: [x_axis, x_axis, x_axis, x_axis],
        y: [isPathCsv ? [] : plotData.gz, isPathCsv ? [] : plotData.gz_lpf, csvYaw, csvUkfYaw],
        customdata: [plotData.times, plotData.times, plotData.times, plotData.times],
        visible: [!isPathCsv, !isPathCsv, true, true]
    }, [0, 1, 2, 3]);

    if (isPathCsv) {
        const pathSamples = samples.slice(0, x_axis.length);
        const trilErrors = calcPathErrors(pathSamples.map(s => s.tril_x), pathSamples.map(s => s.tril_y));
        const dataUkfErrors = calcPathErrors(pathSamples.map(s => s.ukf_x), pathSamples.map(s => s.ukf_y));
        Plotly.restyle('pos_error', {
            x: [x_axis, x_axis, [], [], [], []],
            y: [trilErrors, dataUkfErrors, [], [], [], []],
            name: [
                `Pos Error (Trilateration) MAE: ${meanErr(trilErrors)}m`,
                `Pos Error (Data UKF) MAE: ${meanErr(dataUkfErrors)}m`,
                '', '', '', ''
            ],
            visible: [true, true, false, false, false, false],
            customdata: [plotData.times, plotData.times, [], [], [], []]
        }, [0, 1, 2, 3, 4, 5]);
        updatePositionErrorMetrics([
            { label: 'Trilateration', errors: trilErrors },
            { label: 'Data UKF', errors: dataUkfErrors }
        ]);
    } else {
        Plotly.restyle('pos_error', {
            x: [x_axis, x_axis, x_axis, x_axis, x_axis, x_axis],
            y: [pos_errors_fw, pos_errors, pos_errors_wls, pos_errors_triplet, pos_errors_ukf, pos_errors_ukf_lpf],
            name: [
                `Pos Error (Trilateration) MAE: ${meanErr(pos_errors_fw)}m`,
                `Pos Error (Rules) MAE: ${meanErr(pos_errors)}m`,
                `Pos Error (Multilateration) MAE: ${meanErr(pos_errors_wls)}m`,
                `Pos Error (Best Triplet) MAE: ${meanErr(pos_errors_triplet)}m`,
                `Pos Error (UKF Fusion) MAE: ${meanErr(pos_errors_ukf)}m`,
                `Pos Error (UKF Fusion + IMU Butterworth) MAE: ${meanErr(pos_errors_ukf_lpf)}m`
            ],
            customdata: [plotData.times, plotData.times, plotData.times, plotData.times, plotData.times, plotData.times]
        }, [0, 1, 2, 3, 4, 5]);
        updatePositionErrorMetrics([
            { label: 'Trilateration', errors: pos_errors_fw },
            { label: 'Rules', errors: pos_errors },
            { label: 'Multilateration', errors: pos_errors_wls },
            { label: 'Best Triplet', errors: pos_errors_triplet },
            { label: 'UKF Fusion', errors: pos_errors_ukf },
            { label: 'UKF + Butterworth', errors: pos_errors_ukf_lpf }
        ]);
    }

    const csv_errors = samples.slice(0, x_axis.length).map(s => s.err);
    Plotly.restyle('error_frame', { x: [x_axis], y: [csv_errors], customdata: [plotData.times] }, [0]);

    function calcPathErrors(pathX, pathY) {
        const gtSegments = (typeof activeGroundTruth !== 'undefined' && activeGroundTruth && activeGroundTruth.segments) || [];
        const gtProfiles = buildGroundTruthSegmentProfiles(gtSegments);
        return pathX.map((px, i) => {
            const py = pathY[i];
            if (!Number.isFinite(px) || !Number.isFinite(py) || gtSegments.length === 0) return null;

            let minDist = Infinity;
            let nearestSegmentIndex = -1;
            let nearestProjectionT = 0;
            for (let segmentIndex = 0; segmentIndex < gtSegments.length; segmentIndex++) {
                const seg = gtSegments[segmentIndex];
                const [x1, y1, x2, y2] = seg;
                const l2 = (x2 - x1) ** 2 + (y2 - y1) ** 2;
                if (l2 <= 0.000001) continue;
                const t = Math.max(0, Math.min(1, ((px - x1) * (x2 - x1) + (py - y1) * (y2 - y1)) / l2));
                const projX = x1 + t * (x2 - x1);
                const projY = y1 + t * (y2 - y1);
                const distance = Math.hypot(px - projX, py - projY);
                if (distance < minDist) {
                    minDist = distance;
                    nearestSegmentIndex = segmentIndex;
                    nearestProjectionT = t;
                }
            }
            if (!Number.isFinite(minDist) || nearestSegmentIndex < 0) return null;
            const tolerance = groundTruthToleranceAtProjection(gtProfiles[nearestSegmentIndex], nearestProjectionT);
            return Math.max(0, minDist - tolerance);
        });
    }

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

    function updatePositionErrorMetrics(series) {
        const elem = document.getElementById('position_error_metrics');
        if (!elem) return;

        elem.innerHTML = series.map(item => {
            const metrics = calcErrorMetrics(item.errors);
            return `
                <div class="stat-box">
                    <strong>${item.label}</strong>
                    <div class="position-metric-grid">
                        <div class="position-metric euclidean" title="Mean of sqrt(dx^2 + dy^2) over valid samples"><span class="position-metric-label">Euclidean Position Error</span><span class="position-metric-value">${formatErrorMetric(metrics.euclidean)}</span></div>
                        <div class="position-metric"><span class="position-metric-label">MAE</span><span class="position-metric-value">${formatErrorMetric(metrics.mae)}</span></div>
                        <div class="position-metric"><span class="position-metric-label">RMSE</span><span class="position-metric-value">${formatErrorMetric(metrics.rmse)}</span></div>
                        <div class="position-metric"><span class="position-metric-label">P50</span><span class="position-metric-value">${formatErrorMetric(metrics.p50)}</span></div>
                        <div class="position-metric"><span class="position-metric-label">P90</span><span class="position-metric-value">${formatErrorMetric(metrics.p90)}</span></div>
                        <div class="position-metric"><span class="position-metric-label">P95</span><span class="position-metric-value">${formatErrorMetric(metrics.p95)}</span></div>
                        <div class="position-metric"><span class="position-metric-label">Max Error</span><span class="position-metric-value">${formatErrorMetric(metrics.max)}</span></div>
                        <div class="position-metric"><span class="position-metric-label">Samples</span><span class="position-metric-value">${metrics.count}</span></div>
                    </div>
                </div>
            `;
        }).join('');
    }

    const pathLossData = Array.from({ length: anchorCount }, (_, i) => {
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

    const anchorIndices = Array.from({ length: anchorCount }, (_, i) => i);
    const sliced_amp = anchorIndices.map(i => (rawData.fp_logs.amp[i] || []).slice(0, x_axis.length).map(v => (v <= 0 || v > 5000) ? null : v));
    const sliced_snr = anchorIndices.map(i => (rawData.fp_logs.snr[i] || []).slice(0, x_axis.length).map(v => (v <= 0 || v > 5000) ? null : v));
    for(let i=0; i<anchorCount; ++i) {
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
