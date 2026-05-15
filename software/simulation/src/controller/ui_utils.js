let ruleCounter = 0;

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
    div.style.gap = '10px';
    div.style.alignItems = 'center';
    div.style.background = '#f8fafc';
    div.style.padding = '8px 12px';
    div.style.borderRadius = '6px';
    div.style.border = '1px solid #e2e8f0';
    div.style.fontSize = '0.85rem';

    let checksHTML = '';
    for (let i = 0; i < 4; i++) {
        const checked = activeAnchors.includes(i) ? 'checked' : '';
        checksHTML += `<label style="display:flex; align-items:center; gap:4px; margin:0;"><input type="checkbox" class="rule-anchor" value="${i}" ${checked} onchange="update()"> A${i+1}</label>`;
    }

    div.innerHTML = `
        <div style="display:flex; align-items:center; gap:5px;">
            <span style="font-weight:bold; color:#64748b;">Range:</span>
            <input type="number" class="rule-start" value="${start}" style="width: 70px; padding: 4px; border:1px solid #cbd5e1; border-radius:4px;" onchange="update()">
            <span>-</span>
            <input type="number" class="rule-end" value="${end}" style="width: 70px; padding: 4px; border:1px solid #cbd5e1; border-radius:4px;" onchange="update()">
        </div>
        <div style="display:flex; gap: 12px; margin-left: 15px; flex-grow: 1;">
            ${checksHTML}
        </div>
        <button onclick="removeRule(${id})" style="cursor:pointer; color: #ef4444; border:none; background:none; font-weight:bold; font-size:1.2rem; padding:0 5px;" title="Remove Rule">&times;</button>
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

        if (entry.type === 'Update') {
            const nextPx = path.x ? path.x[updateIdx] : null;
            const nextPy = path.y ? path.y[updateIdx] : null;
            if (Number.isFinite(nextPx) && Number.isFinite(nextPy)) {
                px = nextPx;
                py = nextPy;
            }
            updateIdx++;
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

function updatePositionRateDisplay(totalTimeOverride) {
    if (Number.isFinite(totalTimeOverride)) latestTotalTime = totalTimeOverride;
    const select = document.getElementById('export_path_select');
    const selected = select ? select.value : 'rules';
    const path = latestTrajectoryPaths[selected] || latestTrajectoryPaths.firmware;
    const validCount = countValidPositions(path);
    const totalCount = path && path.x ? path.x.length : 0;
    const hz = latestTotalTime > 0 ? validCount / latestTotalTime : 0;
    const label = select ? select.options[select.selectedIndex].text : 'Trajectory';
    const elem = document.getElementById('position_rate_info');
    if (elem) {
        elem.textContent = `${label}: ${hz.toFixed(2)} Hz (${validCount}/${totalCount} points, ${latestTotalTime.toFixed(2)}s)`;
    }
}

function updateD2DistanceStats(d2Scores, gatedDist, rejectIdx, xAxisLength, samples) {
    const elem = document.getElementById('d2_distance_stats');
    if (!elem) return;

    const rawCounts = [0, 0, 0, 0];
    samples.slice(0, xAxisLength).forEach(s => {
        for (let i = 0; i < 4; i++) {
            if (s.distances[i] > 0.1) rawCounts[i]++;
        }
    });

    elem.innerHTML = [0, 1, 2, 3].map(i => {
        const d2 = meanFinite(d2Scores[i]);
        const gatedCount = gatedDist[i].filter(v => Number.isFinite(v)).length;
        const rejectCount = rejectIdx[i].length;
        const meanText = d2.mean === null ? '--' : d2.mean.toFixed(3);
        return `
            <div class="stat-box">
                <strong>A${i + 1}</strong>
                <div class="stat-row"><span>Mean D2</span><span>${meanText}</span></div>
                <div class="stat-row"><span>D2 Count</span><span>${d2.count}</span></div>
                <div class="stat-row"><span>Raw Dist</span><span>${rawCounts[i]}</span></div>
                <div class="stat-row"><span>Gated Dist</span><span>${gatedCount}</span></div>
                <div class="stat-row"><span>Rejected</span><span>${rejectCount}</span></div>
            </div>
        `;
    }).join('');
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
        t2_high: document.getElementById('t2_high_input').value,
        t2_low: document.getElementById('t2_low_input').value,
        r_base: document.getElementById('r_input').value,
        zupt_acc: document.getElementById('zupt_acc_input').value,
        zupt_gyr: document.getElementById('zupt_gyr_input').value,
        max_samples: document.getElementById('max_samples_input').value,
        enable_smoother: document.getElementById('enable_smoother').checked,
        rules: []
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
            if (config.t2_high) {
                document.getElementById('t2_high_input').value = config.t2_high;
                document.getElementById('t2_high_range').value = config.t2_high;
                document.getElementById('t2_high_val').innerText = config.t2_high;
            }
            if (config.t2_low) {
                document.getElementById('t2_low_input').value = config.t2_low;
                document.getElementById('t2_low_range').value = config.t2_low;
                document.getElementById('t2_low_val').innerText = config.t2_low;
            }
            if (config.r_base) {
                document.getElementById('r_input').value = config.r_base;
                document.getElementById('r_range').value = config.r_base;
                document.getElementById('r_val').innerText = config.r_base;
            }
            if (config.zupt_acc) {
                document.getElementById('zupt_acc_input').value = config.zupt_acc;
                document.getElementById('zupt_acc_range').value = config.zupt_acc;
                document.getElementById('zupt_acc_val').innerText = config.zupt_acc;
            }
            if (config.zupt_gyr) {
                document.getElementById('zupt_gyr_input').value = config.zupt_gyr;
                document.getElementById('zupt_gyr_range').value = config.zupt_gyr;
                document.getElementById('zupt_gyr_val').innerText = config.zupt_gyr;
            }
            if (config.max_samples) {
                document.getElementById('max_samples_input').value = config.max_samples;
                document.getElementById('max_samples_range').value = config.max_samples;
                document.getElementById('max_samples_val').innerText = config.max_samples;
            }
            if (config.enable_smoother !== undefined) {
                document.getElementById('enable_smoother').checked = config.enable_smoother;
            }
            
            if (config.rules && config.rules.length > 0) {
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
