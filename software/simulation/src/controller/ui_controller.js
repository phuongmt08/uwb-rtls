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
            latestTrajectoryPaths = {
                firmware: { x: rawData.fw_path.x.slice(0, res.x_axis.length), y: rawData.fw_path.y.slice(0, res.x_axis.length) },
                all: res.simPath,
                rules: res.simPathRuled,
                wls: res.simPathWLS,
                triplet: res.simPathTriplet,
                ukf: res.simPathUKF
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

    const params = {
        T2_high: parseFloat(document.getElementById('t2_high_range').value),
        T2_low: parseFloat(document.getElementById('t2_low_range').value),
        rescue_noise_max: rescueNoiseMax,
        rescue_min_anchors: parseInt(document.getElementById('win_range').value),
        zupt_acc: parseFloat(document.getElementById('zupt_acc_range').value),
        zupt_gyr: parseFloat(document.getElementById('zupt_gyr_range').value),
        enable_zupt_ukf: document.getElementById('enable_zupt_ukf').checked,
        enable_smoother: document.getElementById('enable_smoother').checked,
        enable_mahalanobis: document.getElementById('enable_mahalanobis').checked,

        // Add UKF Parameters
        ukf_alpha: parseFloat(document.getElementById('ukf_alpha_range').value),
        ukf_beta: parseFloat(document.getElementById('ukf_beta_range').value),
        ukf_kappa: parseFloat(document.getElementById('ukf_kappa_range').value),
        q_a: parseFloat(document.getElementById('ukf_qa_range').value),
        q_g: parseFloat(document.getElementById('ukf_qg_range').value),
        r_uwb: parseFloat(document.getElementById('ukf_ruwb_range').value),
        r_gate: parseFloat(document.getElementById('ukf_rgate_range').value)
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

    // Update UKF value labels
    document.getElementById('ukf_alpha_val').innerText = params.ukf_alpha;
    document.getElementById('ukf_beta_val').innerText  = params.ukf_beta.toFixed(1);
    document.getElementById('ukf_kappa_val').innerText = params.ukf_kappa.toFixed(1);
    document.getElementById('ukf_qa_val').innerText    = params.q_a.toFixed(3);
    document.getElementById('ukf_qg_val').innerText    = params.q_g.toExponential(3);
    document.getElementById('ukf_ruwb_val').innerText  = params.r_uwb.toFixed(3);
    document.getElementById('ukf_rgate_val').innerText = params.r_gate.toFixed(3);

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
}

function openReplayPage() {
    if (!latestSimulationResult) {
        alert('Vui lòng đợi mô phỏng chạy xong trước khi Replay!');
        return;
    }
    // Package data for replay
    const replayData = {
        anchors: anchors,
        groundTruth: activeGroundTruth,
        firmwarePath: latestTrajectoryPaths.firmware,
        ukfPath: latestSimulationResult.simPathUKF_plot, // 6Hz aligned
        ukfModes: latestSimulationResult.simPathUKF_modes, // 6Hz predict vs update modes
        // 20Hz UKF data for predict/update breadcrumb visualization
        ukfPath20Hz: latestSimulationResult.simPathUKF, // 20Hz full resolution
        ukfModes20Hz: latestSimulationResult.simPathUKF_allModes, // 20Hz: 0=Predict, 1=Update
        ukfTimes20Hz: latestSimulationResult.simPathUKF_allTimes, // 20Hz timestamps
        tripletPath: latestTrajectoryPaths.triplet,
        wlsPath: latestTrajectoryPaths.wls,
        yaw: latestSimulationResult.plotData.yaw,
        times: latestSimulationResult.plotData.times,
        x_axis: latestSimulationResult.x_axis,
        filename: sourceFilename,
        simPageUrl: window.location.pathname
    };
    localStorage.setItem('uwb_replay_data', JSON.stringify(replayData));
    window.open('/trajectory_replay.html', '_blank');
}

// Override global update for compatibility with existing UI attributes (onchange="update()")
window.update = requestUpdate;
