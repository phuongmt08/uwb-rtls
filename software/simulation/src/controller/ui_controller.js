let simWorker = null;
let latestTrajectoryPaths = {};
let latestTotalTime = 0;
let debounceTimer = null;

function initSimulation() {
    if (window.Worker) {
        simWorker = new Worker('/src/workers/sim_worker.js');
        simWorker.onmessage = function(e) {
            const res = e.data;
            latestTrajectoryPaths = {
                firmware: { x: rawData.fw_path.x.slice(0, res.x_axis.length), y: rawData.fw_path.y.slice(0, res.x_axis.length) },
                all: res.simPath,
                rules: res.simPathRuled,
                wls: res.simPathWLS,
                triplet: res.simPathTriplet
            };
            latestTotalTime = res.total_time;
            updatePlots(res, samples, rawData);
            updatePositionRateDisplay(res.total_time);
            updateD2DistanceStats(res.d2Scores, res.gatedDist, res.rejectIdx, res.x_axis.length, samples);
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

    const params = {
        T2_high: parseFloat(document.getElementById('t2_high_range').value),
        T2_low: parseFloat(document.getElementById('t2_low_range').value),
        R_base: parseFloat(document.getElementById('r_range').value),
        WIN: parseInt(document.getElementById('win_range').value),
        zupt_acc: parseFloat(document.getElementById('zupt_acc_range').value),
        zupt_gyr: parseFloat(document.getElementById('zupt_gyr_range').value),
        enable_smoother: document.getElementById('enable_smoother').checked
    };

    let max_samples = parseInt(document.getElementById('max_samples_range').value);
    if (isNaN(max_samples)) max_samples = rawData.all_entries.length;

    // Update value labels
    document.getElementById('t2_high_val').innerText = params.T2_high;
    document.getElementById('t2_low_val').innerText  = params.T2_low;
    document.getElementById('r_val').innerText       = params.R_base;
    document.getElementById('win_val').innerText     = params.WIN;
    document.getElementById('zupt_acc_val').innerText = params.zupt_acc;
    document.getElementById('zupt_gyr_val').innerText = params.zupt_gyr;
    const maxRangeElem = document.getElementById('max_samples_range');
    document.getElementById('max_samples_val').innerText = (max_samples >= parseInt(maxRangeElem.max)) ? "All" : max_samples;

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
        rawData, anchors, gt_square, tagHeight,
        params, rules, max_samples
    });
}

// Override global update for compatibility with existing UI attributes (onchange="update()")
window.update = requestUpdate;
