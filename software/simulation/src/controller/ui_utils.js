let ruleCounter = 0;
const UWB_SIM_DEFAULTS_SCHEMA_VERSION = 10;
const UWB_SIM_GEOMETRY_STORAGE_KEY = 'uwb_sim_geometry';
const ANCHOR_LAYOUT_IDS = ['layout_1', 'layout_2'];
let activeAnchorLayoutId = 'layout_1';
let anchorLayouts = null;
let groundTruthOffset = { x: 0, y: 0 };

function cloneAnchors(source) {
    return source.map(a => ({
        id: a.id,
        x: Number(a.x),
        y: Number(a.y),
        z: Number(a.z)
    }));
}

function defaultAnchorLayouts() {
    return {
        layout_1: cloneAnchors(SIM_CONFIG.ENV.ANCHORS),
        layout_2: cloneAnchors(SIM_CONFIG.ENV.ANCHORS)
    };
}

function ensureAnchorLayouts() {
    if (!anchorLayouts) anchorLayouts = defaultAnchorLayouts();
    ANCHOR_LAYOUT_IDS.forEach(id => {
        if (!Array.isArray(anchorLayouts[id])) {
            anchorLayouts[id] = cloneAnchors(SIM_CONFIG.ENV.ANCHORS);
        }
    });
}

function saveActiveAnchorLayout() {
    ensureAnchorLayouts();
    anchorLayouts[activeAnchorLayoutId] = readAnchorsFromInputs();
}

function setAnchorLayoutSelectValue(layoutId) {
    const select = document.getElementById('anchor_layout_select');
    if (select) select.value = layoutId;
}

function initAnchorLayoutSelector() {
    ensureAnchorLayouts();
    setAnchorLayoutSelectValue(activeAnchorLayoutId);
}

function onAnchorLayoutChanged() {
    const select = document.getElementById('anchor_layout_select');
    const nextLayoutId = select && ANCHOR_LAYOUT_IDS.includes(select.value) ? select.value : 'layout_1';
    saveActiveAnchorLayout();
    activeAnchorLayoutId = nextLayoutId;
    ensureAnchorLayouts();
    anchors = cloneAnchors(anchorLayouts[activeAnchorLayoutId]);
    setAnchorInputs(anchors);
    updateAnchorPlot(anchors);
    if (typeof update === 'function') update();
}

function initAnchorEditor(anchorList) {
    const container = document.getElementById('anchor_editor');
    if (!container) return;

    let rowsHTML = (anchorList || []).map((a, idx) => `
        <tr class="anchor-row-tr" data-anchor-id="${a.id}">
            <td style="padding:6px 10px; font-weight:bold; color:#1e293b;">A${a.id}</td>
            <td style="padding:4px 6px;">
                <input class="anchor-pos" data-anchor-index="${idx}" data-axis="x" type="number" step="0.001" value="${a.x}" oninput="updateAnchorsFromInputs()" style="width:100%; box-sizing:border-box; padding:4px 6px; border:1px solid #cbd5e1; border-radius:4px; font-size:0.8rem;">
            </td>
            <td style="padding:4px 6px;">
                <input class="anchor-pos" data-anchor-index="${idx}" data-axis="y" type="number" step="0.001" value="${a.y}" oninput="updateAnchorsFromInputs()" style="width:100%; box-sizing:border-box; padding:4px 6px; border:1px solid #cbd5e1; border-radius:4px; font-size:0.8rem;">
            </td>
            <td style="padding:4px 6px;">
                <input class="anchor-pos" data-anchor-index="${idx}" data-axis="z" type="number" step="0.001" value="${a.z}" oninput="updateAnchorsFromInputs()" style="width:100%; box-sizing:border-box; padding:4px 6px; border:1px solid #cbd5e1; border-radius:4px; font-size:0.8rem;">
            </td>
            <td style="padding:4px 6px; text-align:center;">
                <button type="button" onclick="removeAnchor(${a.id})" style="cursor:pointer; background:#fee2e2; color:#ef4444; border:1px solid #fca5a5; border-radius:4px; padding:3px 8px; font-size:0.75rem; font-weight:bold;" title="Xóa Anchor A${a.id}">🗑 Xóa</button>
            </td>
        </tr>
    `).join('');

    container.innerHTML = `
        <table style="width:100%; border-collapse:collapse; font-size:0.8rem; border:1px solid #e2e8f0; border-radius:6px; overflow:hidden; background:white;">
            <thead>
                <tr style="background:#f1f5f9; text-align:left; color:#475569;">
                    <th style="padding:6px 10px; border-bottom:1px solid #cbd5e1;">Anchor</th>
                    <th style="padding:6px 10px; border-bottom:1px solid #cbd5e1;">X (m)</th>
                    <th style="padding:6px 10px; border-bottom:1px solid #cbd5e1;">Y (m)</th>
                    <th style="padding:6px 10px; border-bottom:1px solid #cbd5e1;">Z (m)</th>
                    <th style="padding:6px 10px; border-bottom:1px solid #cbd5e1; text-align:center;">Thao tác</th>
                </tr>
            </thead>
            <tbody>
                ${rowsHTML}
            </tbody>
        </table>
    `;
}

function addNewAnchor() {
    saveActiveAnchorLayout();
    const maxId = anchors.reduce((max, a) => Math.max(max, a.id), 0);
    const nextId = maxId + 1;
    anchors.push({ id: nextId, x: 0.0, y: 0.0, z: 2.495 });
    ensureAnchorLayouts();
    anchorLayouts[activeAnchorLayoutId] = cloneAnchors(anchors);
    initAnchorEditor(anchors);
    updateAnchorPlot(anchors);
    refreshRuleAnchorCheckboxes();
    if (typeof update === 'function') update();
}

function removeAnchor(anchorId) {
    if (anchors.length <= 3) {
        alert('Cần tối thiểu 3 Anchor để thực hiện thuật toán định vị!');
        return;
    }
    saveActiveAnchorLayout();
    anchors = anchors.filter(a => a.id !== anchorId);
    ensureAnchorLayouts();
    anchorLayouts[activeAnchorLayoutId] = cloneAnchors(anchors);
    initAnchorEditor(anchors);
    updateAnchorPlot(anchors);
    refreshRuleAnchorCheckboxes();
    if (typeof update === 'function') update();
}

function readAnchorsFromInputs() {
    const next = [];
    document.querySelectorAll('.anchor-row-tr').forEach(tr => {
        const id = parseInt(tr.dataset.anchorId);
        const xInput = tr.querySelector('.anchor-pos[data-axis="x"]');
        const yInput = tr.querySelector('.anchor-pos[data-axis="y"]');
        const zInput = tr.querySelector('.anchor-pos[data-axis="z"]');
        const x = xInput ? parseFloat(xInput.value) : 0;
        const y = yInput ? parseFloat(yInput.value) : 0;
        const z = zInput ? parseFloat(zInput.value) : 0;
        if (Number.isFinite(id)) {
            next.push({
                id: id,
                x: Number.isFinite(x) ? x : 0,
                y: Number.isFinite(y) ? y : 0,
                z: Number.isFinite(z) ? z : 0
            });
        }
    });
    return next.length > 0 ? next : cloneAnchors(anchors);
}

function setAnchorInputs(anchorList) {
    anchors = cloneAnchors(anchorList);
    initAnchorEditor(anchors);
    refreshRuleAnchorCheckboxes();
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
    ensureAnchorLayouts();
    anchorLayouts[activeAnchorLayoutId] = cloneAnchors(anchors);
    updateAnchorPlot(anchors);
    if (typeof update === 'function') update();
}

function resetAnchorsToDefault() {
    anchors = cloneAnchors(SIM_CONFIG.ENV.ANCHORS);
    ensureAnchorLayouts();
    anchorLayouts[activeAnchorLayoutId] = cloneAnchors(anchors);
    initAnchorEditor(anchors);
    updateAnchorPlot(anchors);
    refreshRuleAnchorCheckboxes();
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

function readGroundTruthOffsetInputs() {
    const offsetXInput = document.getElementById('gt_offset_x_input');
    const offsetYInput = document.getElementById('gt_offset_y_input');
    const x = offsetXInput ? parseFloat(offsetXInput.value) : 0;
    const y = offsetYInput ? parseFloat(offsetYInput.value) : 0;
    return {
        x: Number.isFinite(x) ? x : 0,
        y: Number.isFinite(y) ? y : 0
    };
}

function setGroundTruthOffsetInputs(offset) {
    const offsetX = parseFloat(offset && offset.x);
    const offsetY = parseFloat(offset && offset.y);
    const next = {
        x: Number.isFinite(offsetX) ? offsetX : 0,
        y: Number.isFinite(offsetY) ? offsetY : 0
    };
    groundTruthOffset = next;
    const offsetXInput = document.getElementById('gt_offset_x_input');
    const offsetYInput = document.getElementById('gt_offset_y_input');
    if (offsetXInput) offsetXInput.value = next.x;
    if (offsetYInput) offsetYInput.value = next.y;
}

function applyGroundTruthOffset(track) {
    const normalized = normalizeGroundTruth(track);
    const offset = groundTruthOffset || { x: 0, y: 0 };
    if (!offset.x && !offset.y) return normalized;

    const shiftX = (value) => Number.isFinite(value) ? value + offset.x : value;
    const shiftY = (value) => Number.isFinite(value) ? value + offset.y : value;
    return Object.assign({}, normalized, {
        x: (normalized.x || []).map(shiftX),
        y: (normalized.y || []).map(shiftY),
        segments: (normalized.segments || []).map(seg => [
            shiftX(seg[0]),
            shiftY(seg[1]),
            shiftX(seg[2]),
            shiftY(seg[3]),
            ...seg.slice(4)
        ]),
        ui_offset: { x: offset.x, y: offset.y }
    });
}

function selectedGroundTruthBase() {
    const select = document.getElementById('groundtruth_select');
    const selected = select ? select.value : null;
    return groundTruths.find((gt, idx) => (gt.id || `gt_${idx}`) === selected) || groundTruths[0];
}

function refreshActiveGroundTruth() {
    groundTruthOffset = readGroundTruthOffsetInputs();
    activeGroundTruth = applyGroundTruthOffset(selectedGroundTruthBase());
}

function initGroundTruthSelector() {
    const select = document.getElementById('groundtruth_select');
    if (!select) return;

    select.innerHTML = groundTruths.map((gt, idx) => {
        const id = gt.id || `gt_${idx}`;
        return `<option value="${id}">Ground Truth: ${gt.name || id}</option>`;
    }).join('');
    const fileMatchedOverlay = (typeof sourceFilename !== 'undefined')
        ? groundTruths.find(gt => gt.overlay && gt.overlay.reference_csv === sourceFilename)
        : null;
    const saved = localStorage.getItem('uwb_sim_groundtruth');
    if (fileMatchedOverlay) {
        select.value = fileMatchedOverlay.id;
    } else if (saved && groundTruths.some((gt, idx) => (gt.id || `gt_${idx}`) === saved)) {
        select.value = saved;
    }
    refreshActiveGroundTruth();
}

function updateGroundTruthPlot(track) {
    const plot = document.getElementById('trajectory');
    if (!plot || !plot.data) return;
    const overlay = track && track.overlay;
    const overlayPoints = overlay && Array.isArray(overlay.points) ? overlay.points : [];
    const hasOverlay = overlayPoints.length >= 2;
    Plotly.restyle('trajectory', {
        x: [track.x],
        y: [track.y],
        name: ['Ground Truth'],
        line: [{ color: '#f87171', dash: 'dot', width: 1 }]
    }, [1]);
    Plotly.restyle('trajectory', {
        x: [overlayPoints.map(point => point[0])],
        y: [overlayPoints.map(point => point[1])],
        name: ['Ground Truth Detour'],
        visible: [hasOverlay],
        showlegend: [false],
        line: [{ color: '#f87171', dash: 'dot', width: 1 }]
    }, [8]);
}

function onGroundTruthChanged() {
    const select = document.getElementById('groundtruth_select');
    const selected = select ? select.value : null;
    refreshActiveGroundTruth();
    if (selected) localStorage.setItem('uwb_sim_groundtruth', selected);
    updateGroundTruthPlot(activeGroundTruth);
    if (typeof update === 'function') update();
}

function onGroundTruthOffsetChanged() {
    refreshActiveGroundTruth();
    updateGroundTruthPlot(activeGroundTruth);
    if (typeof update === 'function') update();
}

function resetGroundTruthOffset() {
    setGroundTruthOffsetInputs({ x: 0, y: 0 });
    onGroundTruthOffsetChanged();
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
    activeAnchors = activeAnchors || anchors.map((_, idx) => idx);

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

    let checksHTML = anchors.map((anc, i) => {
        const checked = activeAnchors.includes(i) ? 'checked' : '';
        return `<label style="display:flex; align-items:center; gap:4px; margin:0;"><input type="checkbox" class="rule-anchor" value="${i}" ${checked} onchange="update()"> A${anc.id}</label>`;
    }).join('');

    div.innerHTML = `
        <div style="display:flex; align-items:center; gap:5px;">
            <span style="font-weight:bold; color:#64748b;">Range:</span>
            <input type="number" class="rule-start" value="${start}" style="width: 60px; padding: 3px; border:1px solid #cbd5e1; border-radius:4px;" onchange="update()">
            <span>-</span>
            <input type="number" class="rule-end" value="${end}" style="width: 60px; padding: 3px; border:1px solid #cbd5e1; border-radius:4px;" onchange="update()">
        </div>
        <div class="rule-anchors-container" style="display:flex; gap: 8px; margin-left: 10px; flex-grow: 1; flex-wrap: wrap;">
            ${checksHTML}
        </div>
        <button onclick="removeRule(${id})" style="cursor:pointer; color: #ef4444; border:none; background:none; font-weight:bold; font-size:1rem; padding:0 4px;" title="Remove Rule">&times;</button>
    `;

    document.getElementById('rules_container').appendChild(div);
    if (typeof update === 'function') update();
}

function refreshRuleAnchorCheckboxes() {
    document.querySelectorAll('#rules_container > div').forEach(div => {
        const checkboxes = Array.from(div.querySelectorAll('.rule-anchor'));
        const activeIdxs = checkboxes.filter(cb => cb.checked).map(cb => parseInt(cb.value));
        const container = div.querySelector('.rule-anchors-container');
        if (container) {
            container.innerHTML = anchors.map((anc, i) => {
                const checked = activeIdxs.includes(i) || activeIdxs.length === 0 ? 'checked' : '';
                return `<label style="display:flex; align-items:center; gap:4px; margin:0;"><input type="checkbox" class="rule-anchor" value="${i}" ${checked} onchange="update()"> A${anc.id}</label>`;
            }).join('');
        }
    });
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

function countTrilaterationUpdates(entries, isPathCsv) {
    let count = 0;
    let prevX = null;
    let prevY = null;
    
    entries.forEach(entry => {
        if (isPathCsv || entry.type === 'Update') {
            const x = isPathCsv ? entry.tril_x : entry.px_fw;
            const y = isPathCsv ? entry.tril_y : entry.py_fw;
            if (x !== undefined && y !== undefined && (x !== prevX || y !== prevY)) {
                count++;
                prevX = x;
                prevY = y;
            }
        }
    });
    return count;
}

function isRecordedPathLog(rawData) {
    return rawData && (rawData.log_format === 'path_csv' || rawData.log_format === 'fusion_frame_csv');
}

function countRangingErrorDelta(entries) {
    let total = 0;
    let prev = null;

    entries.forEach(entry => {
        const err = Number(entry.err);
        if (!Number.isFinite(err) || err < 0) return;

        if (prev !== null) {
            total += err >= prev ? (err - prev) : err;
        }
        prev = err;
    });

    return total;
}

function updatePositionRateDisplay(totalTimeOverride) {
    if (Number.isFinite(totalTimeOverride)) latestTotalTime = totalTimeOverride;
    const isPathCsv = isRecordedPathLog(rawData);
    const processedCount = latestSimulationResult && latestSimulationResult.simPathUKF_allTimes
        ? latestSimulationResult.simPathUKF_allTimes.length
        : rawData.all_entries.length;
        
    const duration = Number.isFinite(totalTimeOverride) ? totalTimeOverride : latestTotalTime;
    const entries = rawData.all_entries.slice(0, processedCount);
    
    const trilUpdates = countTrilaterationUpdates(entries, isPathCsv);
    const predictCount = entries.filter(e => e.type === 'Predict').length;
    const updateCount = entries.filter(e => e.type === 'Update').length;
    const errorCount = countRangingErrorDelta(entries);
    const rangingAttempts = updateCount + errorCount;
    
    const trilHz = duration > 0 ? (trilUpdates / duration).toFixed(2) : "0.00";
    const predictHz = duration > 0 ? (predictCount / duration).toFixed(2) : "0.00";
    const updateHz = duration > 0 ? (updateCount / duration).toFixed(2) : "0.00";
    const attemptHz = duration > 0 ? (rangingAttempts / duration).toFixed(2) : "0.00";
    const errorPct = rangingAttempts > 0 ? (100 * errorCount / rangingAttempts).toFixed(2) : "0.00";
    
    const elem = document.getElementById('position_rate_info');
    if (elem) {
        elem.textContent = `Duration: ${duration.toFixed(2)}s | Trilateration: ${trilUpdates} (${trilHz} Hz) | Predict: ${predictCount} (${predictHz} Hz) | Update: ${updateCount} (${updateHz} Hz) | Attempt: ${rangingAttempts} (${attemptHz} Hz) | Error: ${errorCount} (${errorPct}%)`;
    }
}

function updateD2DistanceStats(d2Scores, gatedDist, rejectIdx, rescueIdx, ambiguityEvents, xAxisLength, samples) {
    const elem = document.getElementById('d2_distance_stats');
    if (!elem) return;

    const rawCounts = anchors.map(() => 0);
    samples.slice(0, xAxisLength).forEach(s => {
        for (let i = 0; i < anchors.length; i++) {
            if (s.distances && s.distances[i] > 0.1) rawCounts[i]++;
        }
    });

    const anchorStats = anchors.map((anc, i) => {
        const d2 = meanFinite(d2Scores[i]);
        const gatedCount = gatedDist[i] ? gatedDist[i].filter(v => Number.isFinite(v)).length : 0;
        const rejectCount = rejectIdx[i] ? rejectIdx[i].length : 0;
        const rescueCount = rescueIdx && rescueIdx[i] ? rescueIdx[i].length : 0;
        const meanText = d2.mean === null ? '--' : d2.mean.toFixed(3);
        return `
            <div class="stat-box">
                <strong>A${anc.id}</strong>
                <div class="stat-row"><span>Mean D2</span><span>${meanText}</span></div>
                <div class="stat-row"><span>D2 Count</span><span>${d2.count}</span></div>
                <div class="stat-row"><span>Raw Dist</span><span>${rawCounts[i] || 0}</span></div>
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
        imu_filter_order: document.getElementById('imu_filter_order_input').value,
        groundtruth: document.getElementById('groundtruth_select') ? document.getElementById('groundtruth_select').value : null,
        groundtruth_offset: readGroundTruthOffsetInputs(),
        active_anchor_layout: activeAnchorLayoutId,
        anchor_layouts: (() => {
            saveActiveAnchorLayout();
            return {
                layout_1: cloneAnchors(anchorLayouts.layout_1),
                layout_2: cloneAnchors(anchorLayouts.layout_2)
            };
        })(),
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
        yaw_map_offset_deg: document.getElementById('ukf_yaw_map_offset_input').value,
        triplet_w_d2: document.getElementById('triplet_w_d2_input').value,
        triplet_w_fp: document.getElementById('triplet_w_fp_input').value,
        triplet_w_resid: document.getElementById('triplet_w_resid_input').value,
        triplet_w_dist: document.getElementById('triplet_w_dist_input').value,
        triplet_switch_margin: document.getElementById('triplet_switch_margin_input').value,
        triplet_switch_eps: document.getElementById('triplet_switch_eps_input').value
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
    localStorage.setItem(UWB_SIM_GEOMETRY_STORAGE_KEY, JSON.stringify({
        groundtruth_offset: config.groundtruth_offset,
        active_anchor_layout: config.active_anchor_layout,
        anchor_layouts: config.anchor_layouts
    }));
    if (config.groundtruth) localStorage.setItem('uwb_sim_groundtruth', config.groundtruth);
    alert('Defaults saved!');
}

function clearDefaults() {
    localStorage.removeItem('uwb_sim_defaults');
    localStorage.removeItem(UWB_SIM_GEOMETRY_STORAGE_KEY);
    alert('Defaults cleared! Reload the page to see original settings.');
}

function loadDefaults() {
    const saved = localStorage.getItem('uwb_sim_defaults');
    const savedGeometry = localStorage.getItem(UWB_SIM_GEOMETRY_STORAGE_KEY);
    if (saved || savedGeometry) {
        try {
            const config = saved ? JSON.parse(saved) : {};
            if (savedGeometry) {
                const geometry = JSON.parse(savedGeometry);
                config.groundtruth_offset = geometry.groundtruth_offset;
                config.active_anchor_layout = geometry.active_anchor_layout;
                config.anchor_layouts = geometry.anchor_layouts;
            }
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

            if (loadTuning && config.enable_mahalanobis !== undefined) {
                document.getElementById('enable_mahalanobis').checked = config.enable_mahalanobis;
            }
            if (loadTuning && config.enable_smoother !== undefined) {
                document.getElementById('enable_smoother').checked = config.enable_smoother;
            }
            if (loadTuning && config.enable_imu_lpf !== undefined) {
                document.getElementById('enable_imu_lpf').checked = config.enable_imu_lpf;
            }
            if (loadTuning && config.imu_lpf_cutoff_hz) {
                document.getElementById('imu_lpf_cutoff_input').value = config.imu_lpf_cutoff_hz;
                document.getElementById('imu_lpf_cutoff_range').value = config.imu_lpf_cutoff_hz;
                document.getElementById('imu_lpf_cutoff_val').innerText = parseFloat(config.imu_lpf_cutoff_hz).toFixed(2);
            }
            if (loadTuning && config.imu_filter_order) {
                const order = Math.min(SIM_CONFIG.IMU.MAX_FILTER_ORDER, Math.max(SIM_CONFIG.IMU.MIN_FILTER_ORDER, parseInt(config.imu_filter_order)));
                document.getElementById('imu_filter_order_input').value = order;
                document.getElementById('imu_filter_order_range').value = order;
                document.getElementById('imu_filter_order_val').innerText = order;
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
            if (config.groundtruth_offset) {
                setGroundTruthOffsetInputs(config.groundtruth_offset);
            }
            if (Array.isArray(config.anchors) && config.anchors.length === anchors.length) {
                anchors = cloneAnchors(config.anchors);
                setAnchorInputs(anchors);
            }
            if (config.anchor_layouts) {
                ensureAnchorLayouts();
                ANCHOR_LAYOUT_IDS.forEach(id => {
                    if (Array.isArray(config.anchor_layouts[id]) && config.anchor_layouts[id].length === anchors.length) {
                        anchorLayouts[id] = cloneAnchors(config.anchor_layouts[id]);
                    }
                });
                activeAnchorLayoutId = ANCHOR_LAYOUT_IDS.includes(config.active_anchor_layout)
                    ? config.active_anchor_layout
                    : 'layout_1';
                anchors = cloneAnchors(anchorLayouts[activeAnchorLayoutId]);
                setAnchorInputs(anchors);
                setAnchorLayoutSelectValue(activeAnchorLayoutId);
            } else {
                ensureAnchorLayouts();
                anchorLayouts[activeAnchorLayoutId] = cloneAnchors(anchors);
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
            if (loadTuning && config.yaw_map_offset_deg !== undefined) {
                document.getElementById('ukf_yaw_map_offset_input').value = config.yaw_map_offset_deg;
                document.getElementById('ukf_yaw_map_offset_range').value = config.yaw_map_offset_deg;
                document.getElementById('ukf_yaw_map_offset_val').innerText = parseFloat(config.yaw_map_offset_deg).toFixed(1);
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
            if (loadTuning && config.triplet_w_resid !== undefined) {
                document.getElementById('triplet_w_resid_input').value = config.triplet_w_resid;
                document.getElementById('triplet_w_resid_range').value = config.triplet_w_resid;
                document.getElementById('triplet_w_resid_val').innerText = config.triplet_w_resid;
            }
            if (loadTuning && config.triplet_w_dist !== undefined) {
                document.getElementById('triplet_w_dist_input').value = config.triplet_w_dist;
                document.getElementById('triplet_w_dist_range').value = config.triplet_w_dist;
                document.getElementById('triplet_w_dist_val').innerText = config.triplet_w_dist;
            }
            if (loadTuning && config.triplet_switch_margin !== undefined) {
                document.getElementById('triplet_switch_margin_input').value = config.triplet_switch_margin;
                document.getElementById('triplet_switch_margin_range').value = config.triplet_switch_margin;
                document.getElementById('triplet_switch_margin_val').innerText = parseFloat(config.triplet_switch_margin).toFixed(2);
            }
            if (loadTuning && config.triplet_switch_eps !== undefined) {
                document.getElementById('triplet_switch_eps_input').value = config.triplet_switch_eps;
                document.getElementById('triplet_switch_eps_range').value = config.triplet_switch_eps;
                document.getElementById('triplet_switch_eps_val').innerText = parseFloat(config.triplet_switch_eps).toFixed(3);
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
