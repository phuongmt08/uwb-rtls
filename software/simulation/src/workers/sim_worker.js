// Import scripts relative to the worker location
importScripts('../core/config.js', '../core/math_utils.js', '../filters/prefilter.js');

self.onmessage = function(e) {
    const { 
        rawData, anchors, gt_square, tagHeight, 
        params, rules, max_samples 
    } = e.data;

    const samples = rawData.all_entries.filter(e => e.type === 'Update');
    const bias = rawData.biases;

    const filter = new MahalanobisPrefilter({
        T2_high: params.T2_high,
        T2_low: params.T2_low,
        R_base: params.R_base,
        WIN: params.WIN
    });

    const gatedDist = [[], [], [], []];
    const d2Scores  = [[], [], [], []];
    const rejectIdx = [[], [], [], []];

    let v_clean = { x: 0, y: 0 }, yaw = 0, zupt_cnt = 0;
    const simPath  = { x: [], y: [] };
    const simPathRuled = { x: [], y: [] };
    const simPathWLS     = { x: [], y: [] };
    const simPathTriplet = { x: [], y: [] };
    const wlsInfo         = [];
    const bestTripletInfo = [];

    const SMOOTHER_ALPHA = SIM_CONFIG.SMOOTHER.ALPHA;
    const SMOOTHER_JUMP_LIMIT = SIM_CONFIG.SMOOTHER.JUMP_LIMIT;
    const smoother_state = [
        { init: false, filtered: 0 }, { init: false, filtered: 0 },
        { init: false, filtered: 0 }, { init: false, filtered: 0 }
    ];

    const applySmoothing = (i, raw_d) => {
        if (!params.enable_smoother) return raw_d;
        if (!smoother_state[i].init) {
            smoother_state[i].filtered = raw_d;
            smoother_state[i].init = true;
            return raw_d;
        }
        let delta = raw_d - smoother_state[i].filtered;
        let bounded = raw_d;
        if (delta > SMOOTHER_JUMP_LIMIT) bounded = smoother_state[i].filtered + SMOOTHER_JUMP_LIMIT;
        else if (delta < -SMOOTHER_JUMP_LIMIT) bounded = smoother_state[i].filtered - SMOOTHER_JUMP_LIMIT;
        smoother_state[i].filtered += SMOOTHER_ALPHA * (bounded - smoother_state[i].filtered);
        return smoother_state[i].filtered;
    };

    const plotData = { vx: [], vy: [], zupt: [], ax: [], ay: [], gz: [], yaw: [], times: [] };
    let sampleIdx = 0, total_time = 0;
    let last_ax = 0, last_ay = 0, last_gz = 0;

    const entriesToProcess = rawData.all_entries.slice(0, max_samples);
    entriesToProcess.forEach((entry) => {
        if (entry.type === 'Init') {
            last_ax = entry.ax; last_ay = entry.ay; last_gz = entry.gz;
        }
        if (entry.dt > 0) total_time += entry.dt;

        if (entry.type === 'Predict' && entry.dt > 0) {
            last_ax = entry.ax; last_ay = entry.ay; last_gz = entry.gz;
            v_clean.x += (entry.ax - bias.ax) * entry.dt;
            v_clean.y += (entry.ay - bias.ay) * entry.dt;
            v_clean.x *= SIM_CONFIG.IMU.VELOCITY_DECAY; 
            v_clean.y *= SIM_CONFIG.IMU.VELOCITY_DECAY;
            yaw += (entry.gz - bias.gz) * entry.dt;

            const acc_mag = Math.sqrt((entry.ax - bias.ax)**2 + (entry.ay - bias.ay)**2);
            const gyr_mag = Math.abs(entry.gz - bias.gz);
            if (acc_mag < params.zupt_acc && gyr_mag < params.zupt_gyr) zupt_cnt++; else zupt_cnt = 0;
            if (zupt_cnt > SIM_CONFIG.IMU.ZUPT_COUNT_THRESHOLD) { v_clean.x = 0; v_clean.y = 0; }
        }

        if (entry.type === 'Update') {
            let v_anchors = [], v_anchors_ruled = [], v_anchors_best = [];
            let allowedAnchors = [0, 1, 2, 3], hasMatchingRule = false;
            for (const r of rules) {
                if (sampleIdx >= r.start && sampleIdx <= r.end) {
                    allowedAnchors = r.anchors;
                    hasMatchingRule = true;
                    break;
                }
            }
            if (rules.length > 0 && !hasMatchingRule) allowedAnchors = [];

            entry.distances.forEach((d, i) => {
                const anc = anchors[i];
                const res = filter.process(i, d, v_clean);
                d2Scores[i].push(res.d2);

                if (res.pass) {
                    let smoothed_d = applySmoothing(i, d);
                    gatedDist[i].push(smoothed_d);
                    const r2d = Math.sqrt(Math.max(0, smoothed_d**2 - (anc.z - tagHeight)**2));
                    v_anchors.push({ x: anc.x, y: anc.y, r: r2d });
                    v_anchors_best.push({ x: anc.x, y: anc.y, r: r2d, d2: res.d2, id: i+1 });
                    if (allowedAnchors.includes(i)) v_anchors_ruled.push({ x: anc.x, y: anc.y, r: r2d });
                } else {
                    gatedDist[i].push(null);
                    if (res.d2 !== null) rejectIdx[i].push(sampleIdx);
                }
            });

            const pos = multilaterate(v_anchors);
            simPath.x.push(pos ? pos.x : null);
            simPath.y.push(pos ? pos.y : null);

            const pos_ruled = multilaterate(v_anchors_ruled);
            simPathRuled.x.push(pos_ruled ? pos_ruled.x : null);
            simPathRuled.y.push(pos_ruled ? pos_ruled.y : null);

            const pos_wls = multilaterate(v_anchors_best);
            simPathWLS.x.push(pos_wls ? pos_wls.x : null);
            simPathWLS.y.push(pos_wls ? pos_wls.y : null);
            wlsInfo.push(pos_wls ? `N=${v_anchors_best.length}<br>${v_anchors_best.map(a => `A${a.id}(w=${(1/(1+(a.d2||0))).toFixed(2)})`).join(', ')}` : 'None');

            const bestTriplet = selectBestTriplet(v_anchors_best, params.T2_high);
            simPathTriplet.x.push(bestTriplet ? bestTriplet.pos.x : null);
            simPathTriplet.y.push(bestTriplet ? bestTriplet.pos.y : null);
            bestTripletInfo.push(bestTriplet ? `${bestTriplet.triplet.map(a => 'A'+a.id).join(',')}<br>score=${bestTriplet.score.toFixed(3)}` : 'None');

            plotData.vx.push(v_clean.x);
            plotData.vy.push(v_clean.y);
            plotData.zupt.push(zupt_cnt > 10 ? 0.1 : 0);
            plotData.ax.push(last_ax - bias.ax);
            plotData.ay.push(last_ay - bias.ay);
            plotData.gz.push(last_gz - bias.gz);
            plotData.yaw.push(yaw * 180 / Math.PI);
            plotData.times.push(total_time);
            sampleIdx++;
        }
    });

    const x_axis = simPath.x.map((_, i) => i);
    const pos_errors = [], pos_errors_wls = [], pos_errors_triplet = [];
    
    const calcErr = (pathX, pathY, out) => {
        pathX.forEach((px, i) => {
            if (px === null) { out.push(null); return; }
            let min_d = 999;
            for (let j = 0; j < gt_square.x.length - 1; j++) {
                const x1 = gt_square.x[j], y1 = gt_square.y[j], x2 = gt_square.x[j+1], y2 = gt_square.y[j+1];
                const l2 = (x2-x1)**2 + (y2-y1)**2;
                let t = Math.max(0, Math.min(1, ((px-x1)*(x2-x1) + (pathY[i]-y1)*(y2-y1)) / l2));
                min_d = Math.min(min_d, Math.sqrt((px - (x1 + t*(x2-x1)))**2 + (pathY[i] - (y1 + t*(y2-y1)))**2));
            }
            out.push(min_d);
        });
    };
    calcErr(simPathRuled.x, simPathRuled.y, pos_errors);
    calcErr(simPathWLS.x, simPathWLS.y, pos_errors_wls);
    calcErr(simPathTriplet.x, simPathTriplet.y, pos_errors_triplet);

    self.postMessage({
        simPath, simPathRuled, simPathWLS, simPathTriplet,
        wlsInfo, bestTripletInfo,
        plotData, gatedDist, d2Scores, rejectIdx,
        pos_errors, pos_errors_wls, pos_errors_triplet,
        x_axis, total_time
    });
};
