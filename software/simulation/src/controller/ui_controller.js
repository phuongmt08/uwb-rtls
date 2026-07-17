let simWorker = null;
let latestTrajectoryPaths = {};
let latestTotalTime = 0;
let latestSimulationResult = null;
let debounceTimer = null;

function initSimulation() {
    if (window.Worker) {
        if (typeof SIM_WORKER_SOURCE === 'string' && SIM_WORKER_SOURCE.length > 0) {
            const workerBlob = new Blob([SIM_WORKER_SOURCE], { type: 'application/javascript' });
            simWorker = new Worker(URL.createObjectURL(workerBlob));
        } else {
            simWorker = new Worker('../src/workers/sim_worker.js');
        }
        simWorker.onmessage = function(e) {
            const res = e.data;
            latestSimulationResult = res;
            const isPathCsv = isRecordedPathLog(rawData);
            latestTrajectoryPaths = {
                firmware: isPathCsv
                    ? { x: rawData.tril_path.x.slice(0, res.x_axis.length), y: rawData.tril_path.y.slice(0, res.x_axis.length) }
                    : { x: rawData.fw_path.x.slice(0, res.x_axis.length), y: rawData.fw_path.y.slice(0, res.x_axis.length) },
                rules: res.simPathRuled,
                wls: res.simPathWLS,
                triplet: res.simPathTriplet,
                ukf: isPathCsv
                    ? { x: rawData.fw_path.x.slice(0, res.x_axis.length), y: rawData.fw_path.y.slice(0, res.x_axis.length) }
                    : res.simPathUKF,
                ukf_lpf: res.simPathUKF_lpf
            };
            latestTotalTime = res.total_time;
            updatePlots(res, samples, rawData);
            updatePositionRateDisplay(res.total_time);
            updateD2DistanceStats(res.d2Scores, res.gatedDist, res.rejectIdx, res.rescueIdx, res.ambiguityEvents, res.x_axis.length, samples);
        };
    } else {
        alert('Your browser does not support Web Workers. Performance will be poor.');
    }
}

function requestUpdate() {
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
        runSimulation();
    }, 50); // 50ms debounce
}

function runSimulation() {
    if (!simWorker) return;
    anchors = readAnchorsFromInputs();
    const rescueNoiseMax = Math.min(5.0, Math.max(0.01, parseFloat(document.getElementById('r_range').value)));
    const imuTiming = estimateImuTiming(rawData.all_entries);
    const cutoffLimit = Math.max(0.05, imuTiming.nyquist_hz * SIM_CONFIG.IMU.CUTOFF_NYQUIST_MARGIN);
    const cutoffInputValue = parseFloat(document.getElementById('imu_lpf_cutoff_range').value);
    const requestedCutoff = Number.isFinite(cutoffInputValue) ? cutoffInputValue : SIM_CONFIG.IMU.DEFAULT_LPF_CUTOFF_HZ;
    const imuCutoffHz = Math.min(Math.max(0.05, requestedCutoff), cutoffLimit);
    const imuFilterOrder = Math.min(
        SIM_CONFIG.IMU.MAX_FILTER_ORDER,
        Math.max(SIM_CONFIG.IMU.MIN_FILTER_ORDER, parseInt(document.getElementById('imu_filter_order_range').value) || SIM_CONFIG.IMU.DEFAULT_FILTER_ORDER)
    );

    const params = {
        T2_high: parseFloat(document.getElementById('t2_high_range').value),
        T2_low: parseFloat(document.getElementById('t2_low_range').value),
        rescue_noise_max: rescueNoiseMax,
        rescue_min_anchors: parseInt(document.getElementById('win_range').value),
        zupt_acc: parseFloat(document.getElementById('zupt_acc_range').value),
        zupt_gyr: parseFloat(document.getElementById('zupt_gyr_range').value),

        enable_mahalanobis: document.getElementById('enable_mahalanobis').checked,
        enable_imu_lpf: document.getElementById('enable_imu_lpf').checked,
        imu_lpf_cutoff_hz: imuCutoffHz,
        imu_filter_order: imuFilterOrder,
        imu_sample_rate_hz: imuTiming.sample_rate_hz,
        imu_nyquist_hz: imuTiming.nyquist_hz,

        // Add UKF Parameters
        ukf_alpha: parseFloat(document.getElementById('ukf_alpha_range').value),
        ukf_beta: parseFloat(document.getElementById('ukf_beta_range').value),
        ukf_kappa: parseFloat(document.getElementById('ukf_kappa_range').value),
        q_a: parseFloat(document.getElementById('ukf_qa_range').value),
        q_g: parseFloat(document.getElementById('ukf_qg_range').value),
        r_uwb: parseFloat(document.getElementById('ukf_ruwb_range').value),
        r_gate: parseFloat(document.getElementById('ukf_rgate_range').value),
        yaw_map_offset_deg: parseFloat(document.getElementById('ukf_yaw_map_offset_range').value),
        triplet_weights: {
            d2: parseFloat(document.getElementById('triplet_w_d2_range').value),
            fp_amp: parseFloat(document.getElementById('triplet_w_fp_range').value),
            residual: parseFloat(document.getElementById('triplet_w_resid_range').value),
            dist: parseFloat(document.getElementById('triplet_w_dist_range').value)
        },
        triplet_switch_margin: parseFloat(document.getElementById('triplet_switch_margin_range').value),
        triplet_switch_score_eps: parseFloat(document.getElementById('triplet_switch_eps_range').value)
    };

    let max_samples = parseInt(document.getElementById('max_samples_range').value);
    if (isNaN(max_samples)) max_samples = rawData.all_entries.length;

    // Update value labels
    document.getElementById('t2_high_val').innerText = params.T2_high;
    document.getElementById('t2_low_val').innerText  = params.T2_low;
    document.getElementById('r_val').innerText       = params.rescue_noise_max;
    document.getElementById('win_val').innerText     = params.rescue_min_anchors;
    document.getElementById('zupt_acc_val').innerText = params.zupt_acc;
    document.getElementById('zupt_gyr_val').innerText = params.zupt_gyr;
    document.getElementById('imu_lpf_cutoff_val').innerText = params.imu_lpf_cutoff_hz.toFixed(2);
    document.getElementById('imu_filter_order_val').innerText = params.imu_filter_order;
    document.getElementById('imu_filter_nyquist_val').innerText = Number.isFinite(params.imu_nyquist_hz) ? params.imu_nyquist_hz.toFixed(2) : '--';
    document.getElementById('imu_lpf_cutoff_range').max = cutoffLimit.toFixed(2);
    document.getElementById('imu_lpf_cutoff_input').max = cutoffLimit.toFixed(2);
    if (requestedCutoff !== imuCutoffHz) {
        document.getElementById('imu_lpf_cutoff_range').value = imuCutoffHz;
        document.getElementById('imu_lpf_cutoff_input').value = imuCutoffHz.toFixed(2);
    }

    // Update UKF value labels
    document.getElementById('ukf_alpha_val').innerText = params.ukf_alpha;
    document.getElementById('ukf_beta_val').innerText  = params.ukf_beta.toFixed(1);
    document.getElementById('ukf_kappa_val').innerText = params.ukf_kappa.toFixed(1);
    document.getElementById('ukf_qa_val').innerText    = params.q_a.toFixed(3);
    document.getElementById('ukf_qg_val').innerText    = params.q_g.toExponential(3);
    document.getElementById('ukf_ruwb_val').innerText  = params.r_uwb.toFixed(3);
    document.getElementById('ukf_rgate_val').innerText = params.r_gate.toFixed(3);
    document.getElementById('ukf_yaw_map_offset_val').innerText = params.yaw_map_offset_deg.toFixed(1);
    document.getElementById('triplet_w_d2_val').innerText = params.triplet_weights.d2.toFixed(0);
    document.getElementById('triplet_w_fp_val').innerText = params.triplet_weights.fp_amp.toFixed(0);
    document.getElementById('triplet_w_resid_val').innerText = params.triplet_weights.residual.toFixed(0);
    document.getElementById('triplet_w_dist_val').innerText = params.triplet_weights.dist.toFixed(0);
    document.getElementById('triplet_switch_margin_val').innerText = params.triplet_switch_margin.toFixed(2);
    document.getElementById('triplet_switch_eps_val').innerText = params.triplet_switch_score_eps.toFixed(3);

    const maxRangeElem = document.getElementById('max_samples_range');
    document.getElementById('max_samples_val').innerText = (max_samples >= parseInt(maxRangeElem.max)) ? "All" : max_samples;

    // Read tagHeight dynamically from the slider and update global let tagHeight
    tagHeight = parseFloat(document.getElementById('tag_height_range').value);
    document.getElementById('tag_height_val').innerText = tagHeight.toFixed(3);

    const rules = [];
    const ruleDivs = document.getElementById('rules_container').children;
    for (let i = 0; i < ruleDivs.length; i++) {
        const div = ruleDivs[i];
        rules.push({
            start: parseInt(div.querySelector('.rule-start').value) || 0,
            end: parseInt(div.querySelector('.rule-end').value) || 100000,
            anchors: Array.from(div.querySelectorAll('.rule-anchor:checked')).map(cb => parseInt(cb.value))
        });
    }

    simWorker.postMessage({
        rawData, anchors, groundTruth: activeGroundTruth, tagHeight,
        params, rules, max_samples
    });

    hasChanges = false;
    updateApplyButtonState();
}

function openReplayPage() {
    if (!latestSimulationResult) {
        alert('Vui lòng đợi mô phỏng chạy xong trước khi Replay!');
        return;
    }
    const imuLpfCutoffInput = document.getElementById('imu_lpf_cutoff_range');
    const enableImuLpfInput = document.getElementById('enable_imu_lpf');
    const imuFilterOrderInput = document.getElementById('imu_filter_order_range');
    const isPathCsv = isRecordedPathLog(rawData);

    const compactNumber = (value) => Number.isFinite(value)
        ? Math.round(value * 1e6) / 1e6
        : null;
    const imuDebugSamples = latestSimulationResult.imuDebug20Hz || [];
    const compactImuDebug = {
        format: 'compact-v1',
        biases: imuDebugSamples.length > 0
            ? [
                compactNumber(imuDebugSamples[0].bias_ax),
                compactNumber(imuDebugSamples[0].bias_ay),
                compactNumber(imuDebugSamples[0].bias_gz)
            ]
            : [0, 0, 0],
        // time, frame code, mode, dt, raw ax/ay/gz, global ax/ay,
        // raw yaw, UKF yaw, vx, vy
        rows: imuDebugSamples.map(sample => [
            compactNumber(sample.time),
            sample.frame_type === 'Predict' ? 0 : (sample.frame_type === 'Update' ? 1 : 2),
            sample.mode === 1 ? 1 : 0,
            compactNumber(sample.dt),
            compactNumber(sample.ax_raw),
            compactNumber(sample.ay_raw),
            compactNumber(sample.gz_raw),
            compactNumber(sample.ax_global),
            compactNumber(sample.ay_global),
            compactNumber(sample.yaw_deg),
            compactNumber(sample.ukf_yaw_deg),
            compactNumber(sample.vx),
            compactNumber(sample.vy)
        ])
    };

    // Package data for replay
    const replayData = {
        anchors: anchors,
        groundTruth: activeGroundTruth,
        firmwarePath: latestTrajectoryPaths.firmware,
        ukfPath: isPathCsv ? latestTrajectoryPaths.ukf : latestSimulationResult.simPathUKF_plot, // 6Hz aligned
        ukfLpfPath: latestSimulationResult.simPathUKF_lpf_plot,
        ukfModes: latestSimulationResult.simPathUKF_modes, // 6Hz predict vs update modes
        ukfLpfModes: latestSimulationResult.simPathUKF_lpf_modes,
        // 20Hz UKF data for predict/update breadcrumb visualization
        ukfPath20Hz: isPathCsv ? latestTrajectoryPaths.ukf : latestSimulationResult.simPathUKF, // 20Hz full resolution
        ukfLpfPath20Hz: latestSimulationResult.simPathUKF_lpf,
        ukfModes20Hz: latestSimulationResult.simPathUKF_allModes, // 20Hz: 0=Predict, 1=Update
        ukfTimes20Hz: latestSimulationResult.simPathUKF_allTimes, // 20Hz timestamps
        imuDebug20Hz: compactImuDebug,
        imuLpfConfig: {
            enabled: enableImuLpfInput ? enableImuLpfInput.checked : true,
            cutoff_hz: imuLpfCutoffInput ? parseFloat(imuLpfCutoffInput.value) : null,
            order: imuFilterOrderInput ? parseInt(imuFilterOrderInput.value) : SIM_CONFIG.IMU.DEFAULT_FILTER_ORDER,
            type: 'butterworth'
        },
        tripletPath: latestTrajectoryPaths.triplet,
        wlsPath: latestTrajectoryPaths.wls,
        yaw: latestSimulationResult.plotData.yaw,
        times: latestSimulationResult.plotData.times,
        x_axis: latestSimulationResult.x_axis,
        filename: sourceFilename,
        simPageUrl: window.location.pathname
    };
    // Long logs can exceed localStorage quota. The same-origin opener handoff
    // is the primary path; localStorage remains useful for refresh/manual open.
    window.__uwbReplayData = replayData;
    const serializedReplayData = JSON.stringify(replayData);
    try {
        sessionStorage.setItem('uwb_replay_data', serializedReplayData);
    } catch (error) {
        console.warn('Replay payload exceeds sessionStorage quota; using window handoff.', error);
    }
    try {
        localStorage.setItem('uwb_replay_data', serializedReplayData);
    } catch (error) {
        console.warn('Replay payload exceeds localStorage quota; using opener handoff.', error);
    }

    if (typeof BroadcastChannel !== 'undefined') {
        if (window.__uwbReplayChannel) window.__uwbReplayChannel.close();
        window.__uwbReplayChannel = new BroadcastChannel('uwb_replay_handoff_v1');
        window.__uwbReplayChannel.onmessage = (event) => {
            if (event.data && event.data.type === 'request') {
                window.__uwbReplayChannel.postMessage({ type: 'payload', payload: replayData });
            }
        };
    }

    const replayWindow = window.open('/simulation/trajectory_replay.html', '_blank');
    if (replayWindow) {
        let handoffAttempts = 0;
        const handoffTimer = window.setInterval(() => {
            handoffAttempts++;
            try {
                if (replayWindow.closed) {
                    window.clearInterval(handoffTimer);
                } else if (typeof replayWindow.receiveReplayData === 'function') {
                    replayWindow.receiveReplayData(replayData);
                    window.clearInterval(handoffTimer);
                } else if (handoffAttempts >= 100) {
                    window.clearInterval(handoffTimer);
                }
            } catch (error) {
                if (handoffAttempts >= 100) window.clearInterval(handoffTimer);
            }
        }, 100);
    }
}

let hasChanges = false;

function updateLabels() {
    if (!rawData) return;
    const t2_high = parseFloat(document.getElementById('t2_high_range').value);
    const t2_low = parseFloat(document.getElementById('t2_low_range').value);
    const rescueNoiseMax = Math.min(5.0, Math.max(0.01, parseFloat(document.getElementById('r_range').value)));
    const win_range = parseInt(document.getElementById('win_range').value);
    const zupt_acc = parseFloat(document.getElementById('zupt_acc_range').value);
    const zupt_gyr = parseFloat(document.getElementById('zupt_gyr_range').value);

    const imuTiming = estimateImuTiming(rawData.all_entries);
    const cutoffLimit = Math.max(0.05, imuTiming.nyquist_hz * SIM_CONFIG.IMU.CUTOFF_NYQUIST_MARGIN);
    const cutoffInputValue = parseFloat(document.getElementById('imu_lpf_cutoff_range').value);
    const requestedCutoff = Number.isFinite(cutoffInputValue) ? cutoffInputValue : SIM_CONFIG.IMU.DEFAULT_LPF_CUTOFF_HZ;
    const imuCutoffHz = Math.min(Math.max(0.05, requestedCutoff), cutoffLimit);
    const imuFilterOrder = Math.min(
        SIM_CONFIG.IMU.MAX_FILTER_ORDER,
        Math.max(SIM_CONFIG.IMU.MIN_FILTER_ORDER, parseInt(document.getElementById('imu_filter_order_range').value) || SIM_CONFIG.IMU.DEFAULT_FILTER_ORDER)
    );

    const ukf_alpha = parseFloat(document.getElementById('ukf_alpha_range').value);
    const ukf_beta = parseFloat(document.getElementById('ukf_beta_range').value);
    const ukf_kappa = parseFloat(document.getElementById('ukf_kappa_range').value);
    const q_a = parseFloat(document.getElementById('ukf_qa_range').value);
    const q_g = parseFloat(document.getElementById('ukf_qg_range').value);
    const r_uwb = parseFloat(document.getElementById('ukf_ruwb_range').value);
    const r_gate = parseFloat(document.getElementById('ukf_rgate_range').value);
    const yaw_map_offset_deg = parseFloat(document.getElementById('ukf_yaw_map_offset_range').value);

    const triplet_w_d2 = parseFloat(document.getElementById('triplet_w_d2_range').value);
    const triplet_w_fp = parseFloat(document.getElementById('triplet_w_fp_range').value);
    const triplet_w_resid = parseFloat(document.getElementById('triplet_w_resid_range').value);
    const triplet_w_dist = parseFloat(document.getElementById('triplet_w_dist_range').value);
    const triplet_switch_margin = parseFloat(document.getElementById('triplet_switch_margin_range').value);
    const triplet_switch_score_eps = parseFloat(document.getElementById('triplet_switch_eps_range').value);

    let max_samples = parseInt(document.getElementById('max_samples_range').value);
    if (isNaN(max_samples)) max_samples = rawData.all_entries.length;

    const t_height = parseFloat(document.getElementById('tag_height_range').value);

    // Update UI elements
    document.getElementById('t2_high_val').innerText = t2_high;
    document.getElementById('t2_low_val').innerText  = t2_low;
    document.getElementById('r_val').innerText       = rescueNoiseMax;
    document.getElementById('win_val').innerText     = win_range;
    document.getElementById('zupt_acc_val').innerText = zupt_acc;
    document.getElementById('zupt_gyr_val').innerText = zupt_gyr;
    document.getElementById('imu_lpf_cutoff_val').innerText = imuCutoffHz.toFixed(2);
    document.getElementById('imu_filter_order_val').innerText = imuFilterOrder;
    document.getElementById('imu_filter_nyquist_val').innerText = Number.isFinite(imuTiming.nyquist_hz) ? imuTiming.nyquist_hz.toFixed(2) : '--';
    
    document.getElementById('imu_lpf_cutoff_range').max = cutoffLimit.toFixed(2);
    document.getElementById('imu_lpf_cutoff_input').max = cutoffLimit.toFixed(2);
    if (requestedCutoff !== imuCutoffHz) {
        document.getElementById('imu_lpf_cutoff_range').value = imuCutoffHz;
        document.getElementById('imu_lpf_cutoff_input').value = imuCutoffHz.toFixed(2);
    }

    document.getElementById('ukf_alpha_val').innerText = ukf_alpha;
    document.getElementById('ukf_beta_val').innerText  = ukf_beta.toFixed(1);
    document.getElementById('ukf_kappa_val').innerText = ukf_kappa.toFixed(1);
    document.getElementById('ukf_qa_val').innerText    = q_a.toFixed(3);
    document.getElementById('ukf_qg_val').innerText    = q_g.toExponential(3);
    document.getElementById('ukf_ruwb_val').innerText  = r_uwb.toFixed(3);
    document.getElementById('ukf_rgate_val').innerText = r_gate.toFixed(3);
    document.getElementById('ukf_yaw_map_offset_val').innerText = yaw_map_offset_deg.toFixed(1);
    document.getElementById('triplet_w_d2_val').innerText = triplet_w_d2.toFixed(0);
    document.getElementById('triplet_w_fp_val').innerText = triplet_w_fp.toFixed(0);
    document.getElementById('triplet_w_resid_val').innerText = triplet_w_resid.toFixed(0);
    document.getElementById('triplet_w_dist_val').innerText = triplet_w_dist.toFixed(0);
    document.getElementById('triplet_switch_margin_val').innerText = triplet_switch_margin.toFixed(2);
    document.getElementById('triplet_switch_eps_val').innerText = triplet_switch_score_eps.toFixed(3);

    const maxRangeElem = document.getElementById('max_samples_range');
    document.getElementById('max_samples_val').innerText = (max_samples >= parseInt(maxRangeElem.max)) ? "All" : max_samples;
    document.getElementById('tag_height_val').innerText = t_height.toFixed(3);
}

function updateApplyButtonState() {
    const btn = document.getElementById('apply_btn');
    if (!btn) return;
    if (hasChanges) {
        btn.classList.add('pending');
        btn.innerText = 'Apply Changes (Pending)';
    } else {
        btn.classList.remove('pending');
        btn.innerText = 'Apply Changes';
    }
}

function onParameterChange() {
    updateLabels();
    hasChanges = true;
    updateApplyButtonState();
}

function applyChanges() {
    runSimulation();
}

// Override global update for compatibility with existing UI attributes (onchange="update()")
window.update = onParameterChange;
window.applyChanges = applyChanges;

function estimateImuTiming(entries) {
    const dts = [];
    (entries || []).forEach(entry => {
        if (entry && entry.type === 'Predict' && Number.isFinite(entry.dt) && entry.dt > 0) {
            dts.push(entry.dt);
        }
    });
    if (!dts.length) {
        const fallbackFs = 2 * (SIM_CONFIG.IMU.DEFAULT_LPF_CUTOFF_HZ / SIM_CONFIG.IMU.CUTOFF_NYQUIST_MARGIN);
        return { sample_rate_hz: fallbackFs, nyquist_hz: fallbackFs / 2 };
    }
    dts.sort((a, b) => a - b);
    const medianDt = dts[Math.floor(dts.length / 2)];
    const sampleRateHz = medianDt > 0 ? 1 / medianDt : 0;
    return {
        sample_rate_hz: sampleRateHz,
        nyquist_hz: sampleRateHz / 2
    };
}
