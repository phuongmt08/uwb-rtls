let ruleCounter = 0;
const UWB_SIM_DEFAULTS_SCHEMA_VERSION = 5;

function cloneAnchors(source) {
    return source.map(a => ({
        id: a.id,
        x: Number(a.x),
        y: Number(a.y),
        z: Number(a.z)
    }));
}

function initAnchorEditor(anchorList) {
    const container = document.getElementById('anchor_editor');
    if (!container) return;

    container.innerHTML = anchorList.map(a => `
        <div class="anchor-row" data-anchor-id="${a.id}">
            <strong>A${a.id}</strong>
            <label for="anchor_${a.id}_x">X</label>
            <input id="anchor_${a.id}_x" class="anchor-pos" data-anchor-index="${a.id - 1}" data-axis="x" type="number" step="0.001" value="${a.x}" oninput="updateAnchorsFromInputs()">
            <label for="anchor_${a.id}_y">Y</label>
            <input id="anchor_${a.id}_y" class="anchor-pos" data-anchor-index="${a.id - 1}" data-axis="y" type="number" step="0.001" value="${a.y}" oninput="updateAnchorsFromInputs()">
            <label for="anchor_${a.id}_z">Z</label>
            <input id="anchor_${a.id}_z" class="anchor-pos" data-anchor-index="${a.id - 1}" data-axis="z" type="number" step="0.001" value="${a.z}" oninput="updateAnchorsFromInputs()">
        </div>
    `).join('');
}

function readAnchorsFromInputs() {
    const next = cloneAnchors(anchors);
    document.querySelectorAll('.anchor-pos').forEach(input => {
        const idx = parseInt(input.dataset.anchorIndex);
        const axis = input.dataset.axis;
        const value = parseFloat(input.value);
        if (next[idx] && Number.isFinite(value)) {
            next[idx][axis] = value;
        }
    });
    return next;
}

function setAnchorInputs(anchorList) {
    anchorList.forEach((a, idx) => {
        ['x', 'y', 'z'].forEach(axis => {
            const input = document.querySelector(`.anchor-pos[data-anchor-index="${idx}"][data-axis="${axis}"]`);
            if (input) input.value = a[axis];
        });
    });
}

function updateAnchorPlot(anchorList) {
    const plot = document.getElementById('trajectory');
    if (!plot || !plot.data) return;
    Plotly.restyle('trajectory', {
        x: [anchorList.map(a => a.x)],
        y: [anchorList.map(a => a.y)],
        text: [anchorList.map(a => 'A' + a.id)]
    }, [0]);
}

function updateAnchorsFromInputs() {
    anchors = readAnchorsFromInputs();
    updateAnchorPlot(anchors);
    if (typeof update === 'function') update();
}

function resetAnchorsToDefault() {
    anchors = cloneAnchors(SIM_CONFIG.ENV.ANCHORS);
    setAnchorInputs(anchors);
    updateAnchorPlot(anchors);
    if (typeof update === 'function') update();
}

function normalizeGroundTruth(track) {
    if (!track) return { id: 'empty', name: 'Empty', x: [], y: [], segments: [] };
    if (Array.isArray(track.segments) && track.segments.length > 0) return track;

    const segments = [];
    const xs = track.x || [];
    const ys = track.y || [];
    for (let i = 0; i < Math.min(xs.length, ys.length) - 1; i++) {
        if ([xs[i], ys[i], xs[i + 1], ys[i + 1]].every(Number.isFinite)) {
            segments.push([xs[i], ys[i], xs[i + 1], ys[i + 1]]);
        }
    }
    return Object.assign({}, track, { segments });
}

function initGroundTruthSelector() {
    const select = document.getElementById('groundtruth_select');
    if (!select) return;

    select.innerHTML = groundTruths.map((gt, idx) => {
        const id = gt.id || `gt_${idx}`;
        return `<option value="${id}">Ground Truth: ${gt.name || id}</option>`;
    }).join('');

    const saved = localStorage.getItem('uwb_sim_groundtruth');
    if (saved && groundTruths.some(gt => gt.id === saved)) {
        select.value = saved;
    }
    activeGroundTruth = normalizeGroundTruth(groundTruths.find(gt => gt.id === select.value) || groundTruths[0]);
}

function updateGroundTruthPlot(track) {
    const plot = document.getElementById('trajectory');
    if (!plot || !plot.data) return;
    Plotly.restyle('trajectory', {
        x: [track.x],
        y: [track.y],
        name: [`Ground Truth (${track.name || track.id})`]
    }, [1]);
}

function onGroundTruthChanged() {
    const select = document.getElementById('groundtruth_select');
    const selected = select ? select.value : null;
    activeGroundTruth = normalizeGroundTruth(groundTruths.find(gt => gt.id === selected) || groundTruths[0]);
    if (selected) localStorage.setItem('uwb_sim_groundtruth', selected);
    updateGroundTruthPlot(activeGroundTruth);
    if (typeof update === 'function') update();
}

function decodeMask(mask) {
    let a = [];
    if (mask & 1) a.push(1);
    if (mask & 2) a.push(2);
    if (mask & 4) a.push(3);
    if (mask & 8) a.push(4);
    return a.length > 0 ? a.join(',') : 'None';
}

function addRule(start, end, activeAnchors) {
    start = start !== undefined ? start : 0;
    end = end !== undefined ? end : 100000;
    activeAnchors = activeAnchors || [0, 1, 2, 3];

    const id = ruleCounter++;
    const div = document.createElement('div');
    div.id = 'rule_' + id;
    div.style.display = 'flex';
    div.style.gap = '8px';
    div.style.alignItems = 'center';
    div.style.background = '#f8fafc';
    div.style.padding = '6px 10px';
    div.style.borderRadius = '6px';
    div.style.border = '1px solid #e2e8f0';
    div.style.fontSize = '0.8rem';

    let checksHTML = '';
    for (let i = 0; i < 4; i++) {
        const checked = activeAnchors.includes(i) ? 'checked' : '';
        checksHTML += `<label style="display:flex; align-items:center; gap:4px; margin:0;"><input type="checkbox" class="rule-anchor" value="${i}" ${checked} onchange="update()"> A${i+1}</label>`;
    }

    div.innerHTML = `
        <div style="display:flex; align-items:center; gap:5px;">
            <span style="font-weight:bold; color:#64748b;">Range:</span>
            <input type="number" class="rule-start" value="${start}" style="width: 60px; padding: 3px; border:1px solid #cbd5e1; border-radius:4px;" onchange="update()">
            <span>-</span>
            <input type="number" class="rule-end" value="${end}" style="width: 60px; padding: 3px; border:1px solid #cbd5e1; border-radius:4px;" onchange="update()">
        </div>
        <div style="display:flex; gap: 8px; margin-left: 10px; flex-grow: 1;">
            ${checksHTML}
        </div>
        <button onclick="removeRule(${id})" style="cursor:pointer; color: #ef4444; border:none; background:none; font-weight:bold; font-size:1rem; padding:0 4px;" title="Remove Rule">&times;</button>
    `;

    document.getElementById('rules_container').appendChild(div);
    if (typeof update === 'function') update();
}

function removeRule(id) {
    const el = document.getElementById('rule_' + id);
    if (el) el.remove();
    if (typeof update === 'function') update();
}

function formatLogNumber(value) {
    if (!Number.isFinite(value)) return "  0.000000";
    const fixed = Number(value).toFixed(6);
    return value >= 0 ? "  " + fixed : " " + fixed;
}

function replacePxPyInLine(line, px, py) {
    let out = line;
    out = out.replace(/px:\s*[-+]?\d+(?:\.\d+)?/, "px:" + formatLogNumber(px));
    out = out.replace(/py:\s*[-+]?\d+(?:\.\d+)?/, "py:" + formatLogNumber(py));
    return out;
}

function formatFallbackLogLine(entry, px, py, index) {
    const idx = String(index).padStart(4, ' ');
    const type = String(entry.type || '').padEnd(7, ' ');
    const d = entry.distances || [0, 0, 0, 0];
    const mask = Number.isFinite(entry.mask) ? entry.mask : 15;
    const err = Number.isFinite(entry.err) ? entry.err : 0;
    return `(${idx}/${idx}) ${type} ` +
        `ax:${formatLogNumber(entry.ax)} ay:${formatLogNumber(entry.ay)} gz:${formatLogNumber(entry.gz)} ` +
        `px:${formatLogNumber(px)} py:${formatLogNumber(py)} dt:${formatLogNumber(entry.dt)} ` +
        `mask: ${mask} d1:${formatLogNumber(d[0])} d2:${formatLogNumber(d[1])} ` +
        `d3:${formatLogNumber(d[2])} d4:${formatLogNumber(d[3])} err: ${err}`;
}

function exportTrajectoryCsv() {
    const select = document.getElementById('export_path_select');
    const selected = select ? select.value : 'rules';
    const path = latestTrajectoryPaths[selected] || latestTrajectoryPaths.firmware;
    let updateIdx = 0;

    const lines = rawData.all_entries.map((entry, index) => {
        let px = entry.px_fw;
        let py = entry.py_fw;

        if (selected === 'ukf' || selected === 'ukf_lpf') {
            // UKF has a position for EVERY log entry (20Hz)
            const nextPx = path.x ? path.x[index] : null;
            const nextPy = path.y ? path.y[index] : null;
            if (Number.isFinite(nextPx) && Number.isFinite(nextPy)) {
                px = nextPx;
                py = nextPy;
            }
        } else {
            // Other paths are only aligned with Update events (6Hz)
            if (entry.type === 'Update') {
                const nextPx = path.x ? path.x[updateIdx] : null;
                const nextPy = path.y ? path.y[updateIdx] : null;
                if (Number.isFinite(nextPx) && Number.isFinite(nextPy)) {
                    px = nextPx;
                    py = nextPy;
                }
                updateIdx++;
            }
        }

        const sourceLine = entry.raw_line || formatFallbackLogLine(entry, entry.px_fw, entry.py_fw, index + 1);
        return replacePxPyInLine(sourceLine, px, py);
    });

    const blob = new Blob([lines.join('\r\n') + '\r\n'], { type: 'text/csv;charset=utf-8' });
    const link = document.createElement('a');
    const label = selected.replace(/[^a-z0-9]+/gi, '_').toLowerCase();
    const base = sourceFilename.replace(/\.csv$/i, '');
    const url = URL.createObjectURL(blob);
    link.href = url;
    link.download = `${base}_${label}_trajectory.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
}

function countValidPositions(path) {
    if (!path || !path.x || !path.y) return 0;
    let count = 0;
    for (let i = 0; i < path.x.length; i++) {
        if (Number.isFinite(path.x[i]) && Number.isFinite(path.y[i])) count++;
    }
    return count;
}

function getRateStatsFromDt(entries, types) {
    const allowedTypes = new Set(types);
    let count = 0;
    let dtSum = 0;

    entries.forEach(entry => {
        const dt = Number(entry.dt);
        if (allowedTypes.has(entry.type) && Number.isFinite(dt) && dt > 0) {
            count++;
            dtSum += dt;
        }
    });

    const meanDt = count > 0 ? dtSum / count : 0;
    return {
        count,
        dtSum,
        meanDt,
        hz: meanDt > 0 ? 1 / meanDt : 0
    };
}

function updatePositionRateDisplay(totalTimeOverride) {
    if (Number.isFinite(totalTimeOverride)) latestTotalTime = totalTimeOverride;
    const select = document.getElementById('export_path_select');
    const selected = select ? select.value : 'rules';
    const path = latestTrajectoryPaths[selected] || latestTrajectoryPaths.firmware;
    const validCount = countValidPositions(path);
    const totalCount = path && path.x ? path.x.length : 0;
    const processedCount = latestSimulationResult && latestSimulationResult.simPathUKF_allTimes
        ? latestSimulationResult.simPathUKF_allTimes.length
        : rawData.all_entries.length;
    const entriesForRate = rawData.all_entries.slice(0, processedCount);
    const predictRate = getRateStatsFromDt(entriesForRate, ['Predict']);
    const updateRate = getRateStatsFromDt(entriesForRate, ['Update']);
    const allRate = getRateStatsFromDt(entriesForRate, ['Predict', 'Update']);
    const exportRate = selected === 'firmware' || selected === 'rules' || selected === 'wls' || selected === 'triplet'
        ? updateRate
        : allRate;
    const label = select ? select.options[select.selectedIndex].text : 'Trajectory';
    const elem = document.getElementById('position_rate_info');
    if (elem) {
        elem.textContent = `${label}: ${exportRate.hz.toFixed(2)} Hz (${validCount}/${totalCount} points) | Predict: ${predictRate.hz.toFixed(2)} Hz | Update: ${updateRate.hz.toFixed(2)} Hz`;
    }
}

function updateD2DistanceStats(d2Scores, gatedDist, rejectIdx, rescueIdx, ambiguityEvents, xAxisLength, samples) {
    const elem = document.getElementById('d2_distance_stats');
    if (!elem) return;

    const rawCounts = [0, 0, 0, 0];
    samples.slice(0, xAxisLength).forEach(s => {
        for (let i = 0; i < 4; i++) {
            if (s.distances[i] > 0.1) rawCounts[i]++;
        }
    });

    const anchorStats = [0, 1, 2, 3].map(i => {
        const d2 = meanFinite(d2Scores[i]);
        const gatedCount = gatedDist[i].filter(v => Number.isFinite(v)).length;
        const rejectCount = rejectIdx[i].length;
        const rescueCount = rescueIdx && rescueIdx[i] ? rescueIdx[i].length : 0;
        const meanText = d2.mean === null ? '--' : d2.mean.toFixed(3);
        return `
            <div class="stat-box">
                <strong>A${i + 1}</strong>
                <div class="stat-row"><span>Mean D2</span><span>${meanText}</span></div>
                <div class="stat-row"><span>D2 Count</span><span>${d2.count}</span></div>
                <div class="stat-row"><span>Raw Dist</span><span>${rawCounts[i]}</span></div>
                <div class="stat-row"><span>Gated Dist</span><span>${gatedCount}</span></div>
                <div class="stat-row"><span>Rescued</span><span>${rescueCount}</span></div>
                <div class="stat-row"><span>Rejected</span><span>${rejectCount}</span></div>
            </div>
        `;
    }).join('');

    const ambiguityCount = Array.isArray(ambiguityEvents) ? ambiguityEvents.length : 0;
    elem.innerHTML = anchorStats + `
        <div class="stat-box">
            <strong>Raw Geometry</strong>
            <div class="stat-row"><span>Ambiguous</span><span>${ambiguityCount}</span></div>
        </div>
    `;
}

function syncInput(rangeId, inputId) {
    document.getElementById(rangeId).addEventListener('input', (e) => {
        document.getElementById(inputId).value = e.target.value;
        if (typeof update === 'function') update();
    });
    document.getElementById(inputId).addEventListener('input', (e) => {
        document.getElementById(rangeId).value = e.target.value;
        if (typeof update === 'function') update();
    });
}

function saveDefaults() {
    const config = {
        schema_version: UWB_SIM_DEFAULTS_SCHEMA_VERSION,
        t2_high: document.getElementById('t2_high_input').value,
        t2_low: document.getElementById('t2_low_input').value,
        rescue_noise_max: document.getElementById('r_input').value,
        rescue_min_anchors: document.getElementById('win_input').value,
        zupt_acc: document.getElementById('zupt_acc_input').value,
        zupt_gyr: document.getElementById('zupt_gyr_input').value,
        max_samples: document.getElementById('max_samples_input').value,
        enable_smoother: document.getElementById('enable_smoother').checked,
        enable_mahalanobis: document.getElementById('enable_mahalanobis').checked,
        enable_imu_lpf: document.getElementById('enable_imu_lpf').checked,
        imu_lpf_cutoff_hz: document.getElementById('imu_lpf_cutoff_input').value,
        groundtruth: document.getElementById('groundtruth_select') ? document.getElementById('groundtruth_select').value : null,
        anchors: readAnchorsFromInputs(),
        rules: [],
        tag_height: document.getElementById('tag_height_input').value,

        // Save UKF parameters
        ukf_alpha: document.getElementById('ukf_alpha_input').value,
        ukf_beta: document.getElementById('ukf_beta_input').value,
        ukf_kappa: document.getElementById('ukf_kappa_input').value,
        q_a: document.getElementById('ukf_qa_input').value,
        q_g: document.getElementById('ukf_qg_input').value,
        r_uwb: document.getElementById('ukf_ruwb_input').value,
        r_gate: document.getElementById('ukf_rgate_input').value,
        triplet_w_d2: document.getElementById('triplet_w_d2_input').value,
        triplet_w_fp: document.getElementById('triplet_w_fp_input').value,
        triplet_w_gdop: document.getElementById('triplet_w_gdop_input').value,
        triplet_w_resid: document.getElementById('triplet_w_resid_input').value
    };
    const ruleDivs = document.getElementById('rules_container').children;
    for (let i = 0; i < ruleDivs.length; i++) {
        const div = ruleDivs[i];
        config.rules.push({
            start: div.querySelector('.rule-start').value,
            end: div.querySelector('.rule-end').value,
            anchors: Array.from(div.querySelectorAll('.rule-anchor:checked')).map(cb => parseInt(cb.value))
        });
    }
    localStorage.setItem('uwb_sim_defaults', JSON.stringify(config));
    if (config.groundtruth) localStorage.setItem('uwb_sim_groundtruth', config.groundtruth);
    alert('Defaults saved!');
}

function clearDefaults() {
    localStorage.removeItem('uwb_sim_defaults');
    alert('Defaults cleared! Reload the page to see original settings.');
}

function loadDefaults() {
    const saved = localStorage.getItem('uwb_sim_defaults');
    if (saved) {
        try {
            const config = JSON.parse(saved);
            const loadTuning = config.schema_version === UWB_SIM_DEFAULTS_SCHEMA_VERSION;
            if (!loadTuning) {
                console.warn('Ignoring stale simulation tuning defaults. Ground truth and anchors are still restored.');
            }

            if (loadTuning && config.t2_high) {
                document.getElementById('t2_high_input').value = config.t2_high;
                document.getElementById('t2_high_range').value = config.t2_high;
                document.getElementById('t2_high_val').innerText = config.t2_high;
            }
            if (loadTuning && config.t2_low) {
                document.getElementById('t2_low_input').value = config.t2_low;
                document.getElementById('t2_low_range').value = config.t2_low;
                document.getElementById('t2_low_val').innerText = config.t2_low;
            }
            if (loadTuning && config.rescue_noise_max) {
                const rescueNoiseMax = Math.min(5.0, Math.max(0.01, parseFloat(config.rescue_noise_max)));
                document.getElementById('r_input').value = rescueNoiseMax;
                document.getElementById('r_range').value = rescueNoiseMax;
                document.getElementById('r_val').innerText = rescueNoiseMax;
            }
            if (loadTuning && config.rescue_min_anchors) {
                document.getElementById('win_input').value = config.rescue_min_anchors;
                document.getElementById('win_range').value = config.rescue_min_anchors;
                document.getElementById('win_val').innerText = config.rescue_min_anchors;
            }
            if (loadTuning && config.zupt_acc) {
                document.getElementById('zupt_acc_input').value = config.zupt_acc;
                document.getElementById('zupt_acc_range').value = config.zupt_acc;
                document.getElementById('zupt_acc_val').innerText = config.zupt_acc;
            }
            if (loadTuning && config.zupt_gyr) {
                document.getElementById('zupt_gyr_input').value = config.zupt_gyr;
                document.getElementById('zupt_gyr_range').value = config.zupt_gyr;
                document.getElementById('zupt_gyr_val').innerText = config.zupt_gyr;
            }
            if (loadTuning && config.max_samples) {
                document.getElementById('max_samples_input').value = config.max_samples;
                document.getElementById('max_samples_range').value = config.max_samples;
                document.getElementById('max_samples_val').innerText = config.max_samples;
            }
            if (loadTuning && config.enable_smoother !== undefined) {
                document.getElementById('enable_smoother').checked = config.enable_smoother;
            }
            if (loadTuning && config.enable_mahalanobis !== undefined) {
                document.getElementById('enable_mahalanobis').checked = config.enable_mahalanobis;
            }
            if (loadTuning && config.enable_imu_lpf !== undefined) {
                document.getElementById('enable_imu_lpf').checked = config.enable_imu_lpf;
            }
            if (loadTuning && config.imu_lpf_cutoff_hz) {
                document.getElementById('imu_lpf_cutoff_input').value = config.imu_lpf_cutoff_hz;
                document.getElementById('imu_lpf_cutoff_range').value = config.imu_lpf_cutoff_hz;
                document.getElementById('imu_lpf_cutoff_val').innerText = parseFloat(config.imu_lpf_cutoff_hz).toFixed(2);
            }
            if (loadTuning && config.tag_height) {
                document.getElementById('tag_height_input').value = config.tag_height;
                document.getElementById('tag_height_range').value = config.tag_height;
                document.getElementById('tag_height_val').innerText = config.tag_height;
                tagHeight = parseFloat(config.tag_height);
            }
            if (config.groundtruth) {
                localStorage.setItem('uwb_sim_groundtruth', config.groundtruth);
            }
            if (Array.isArray(config.anchors) && config.anchors.length === anchors.length) {
                anchors = cloneAnchors(config.anchors);
                setAnchorInputs(anchors);
            }
            
            // Load UKF parameters
            if (loadTuning && config.ukf_alpha) {
                document.getElementById('ukf_alpha_input').value = config.ukf_alpha;
                document.getElementById('ukf_alpha_range').value = config.ukf_alpha;
                document.getElementById('ukf_alpha_val').innerText = config.ukf_alpha;
            }
            if (loadTuning && config.ukf_beta) {
                document.getElementById('ukf_beta_input').value = config.ukf_beta;
                document.getElementById('ukf_beta_range').value = config.ukf_beta;
                document.getElementById('ukf_beta_val').innerText = config.ukf_beta;
            }
            if (loadTuning && config.ukf_kappa) {
                document.getElementById('ukf_kappa_input').value = config.ukf_kappa;
                document.getElementById('ukf_kappa_range').value = config.ukf_kappa;
                document.getElementById('ukf_kappa_val').innerText = config.ukf_kappa;
            }
            if (loadTuning && config.q_a) {
                document.getElementById('ukf_qa_input').value = config.q_a;
                document.getElementById('ukf_qa_range').value = config.q_a;
                document.getElementById('ukf_qa_val').innerText = config.q_a;
            }
            if (loadTuning && config.q_g) {
                document.getElementById('ukf_qg_input').value = config.q_g;
                document.getElementById('ukf_qg_range').value = config.q_g;
                document.getElementById('ukf_qg_val').innerText = parseFloat(config.q_g).toExponential(3);
            }
            if (loadTuning && config.r_uwb) {
                document.getElementById('ukf_ruwb_input').value = config.r_uwb;
                document.getElementById('ukf_ruwb_range').value = config.r_uwb;
                document.getElementById('ukf_ruwb_val').innerText = config.r_uwb;
            }
            if (loadTuning && config.r_gate) {
                document.getElementById('ukf_rgate_input').value = config.r_gate;
                document.getElementById('ukf_rgate_range').value = config.r_gate;
                document.getElementById('ukf_rgate_val').innerText = config.r_gate;
            }
            if (loadTuning && config.triplet_w_d2 !== undefined) {
                document.getElementById('triplet_w_d2_input').value = config.triplet_w_d2;
                document.getElementById('triplet_w_d2_range').value = config.triplet_w_d2;
                document.getElementById('triplet_w_d2_val').innerText = config.triplet_w_d2;
            }
            if (loadTuning && config.triplet_w_fp !== undefined) {
                document.getElementById('triplet_w_fp_input').value = config.triplet_w_fp;
                document.getElementById('triplet_w_fp_range').value = config.triplet_w_fp;
                document.getElementById('triplet_w_fp_val').innerText = config.triplet_w_fp;
            }
            if (loadTuning && config.triplet_w_gdop !== undefined) {
                document.getElementById('triplet_w_gdop_input').value = config.triplet_w_gdop;
                document.getElementById('triplet_w_gdop_range').value = config.triplet_w_gdop;
                document.getElementById('triplet_w_gdop_val').innerText = config.triplet_w_gdop;
            }
            if (loadTuning && config.triplet_w_resid !== undefined) {
                document.getElementById('triplet_w_resid_input').value = config.triplet_w_resid;
                document.getElementById('triplet_w_resid_range').value = config.triplet_w_resid;
                document.getElementById('triplet_w_resid_val').innerText = config.triplet_w_resid;
            }

            if (loadTuning && config.rules && config.rules.length > 0) {
                document.getElementById('rules_container').innerHTML = '';
                config.rules.forEach(r => {
                    addRule(r.start, r.end, r.anchors);
                });
            }
        } catch (e) {
            console.error('Failed to load defaults', e);
        }
    }
}
