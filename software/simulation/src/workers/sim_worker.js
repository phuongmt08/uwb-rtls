// Import scripts relative to the worker location
importScripts('../core/config.js', '../core/math_utils.js', '../filters/ukf_prefilter.js');

self.onmessage = function(e) {
    const { 
        rawData, anchors, groundTruth, tagHeight,
        params, rules, max_samples 
    } = e.data;

    const samples = rawData.all_entries.filter(e => e.type === 'Update');
    const bias = rawData.biases;

    const bounds = anchors.reduce((b, a) => ({
        minX: Math.min(b.minX, a.x), maxX: Math.max(b.maxX, a.x),
        minY: Math.min(b.minY, a.y), maxY: Math.max(b.maxY, a.y)
    }), { minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity });
    const boundMargin = SIM_CONFIG.FILTER.POSITION_BOUND_MARGIN;

    const filter = new MahalanobisPrefilter({
        T2_high: params.T2_high,
        T2_low: params.T2_low,
        rescue_noise_max: params.rescue_noise_max,
        ukf_alpha: params.ukf_alpha,
        ukf_beta: params.ukf_beta,
        ukf_kappa: params.ukf_kappa,
        q_a: params.q_a,
        q_g: params.q_g,
        r_uwb: params.r_uwb,
        r_gate: params.r_gate,
        min_frame_measurements: params.rescue_min_anchors || 3,
        tagHeight: tagHeight,
        max_update_position_step: SIM_CONFIG.FILTER.MAX_UWB_POS_CORRECTION,
        max_update_velocity_step: SIM_CONFIG.FILTER.MAX_UWB_VEL_CORRECTION,
        use_planar_ranges: SIM_CONFIG.ENV.LOG_DISTANCES_ARE_PLANAR,
        position_bounds: {
            minX: bounds.minX - boundMargin,
            maxX: bounds.maxX + boundMargin,
            minY: bounds.minY - boundMargin,
            maxY: bounds.maxY + boundMargin
        }
    });

    const gatedDist = [[], [], [], []];
    const d2Scores  = [[], [], [], []];
    const rejectIdx = [[], [], [], []];
    const rescueIdx = [[], [], [], []];
    const rescueDist = [[], [], [], []];
    const ambiguityEvents = [];

    const insideAnchorBounds = (pos, margin) => {
        if (!pos || !Number.isFinite(pos.x) || !Number.isFinite(pos.y)) return false;
        const m = margin !== undefined ? margin : 0.25;
        return pos.x >= bounds.minX - m && pos.x <= bounds.maxX + m &&
               pos.y >= bounds.minY - m && pos.y <= bounds.maxY + m;
    };

    const toPlanarRange = (d, anchor) => {
        if (SIM_CONFIG.ENV.LOG_DISTANCES_ARE_PLANAR) return d;
        return Math.sqrt(Math.max(0, d**2 - (anchor.z - tagHeight)**2));
    };

    let v_clean = { x: 0, y: 0 }, yaw = 0, zupt_cnt = 0;
    const simPath  = { x: [], y: [] };
    const simPathRuled = { x: [], y: [] };
    const simPathWLS     = { x: [], y: [] };
    const simPathTriplet = { x: [], y: [] };

    // High-frequency UKF trajectory (20Hz - length equal to all entries processed) for CSV exporting
    const simPathUKF     = { x: [], y: [] };
    const simPathUKF_allModes = []; // 20Hz: 0=Predict (IMU only), 1=Update (with UWB)
    const simPathUKF_allTimes = []; // 20Hz timestamps
    // Low-frequency UKF trajectory (6Hz - aligned with Update events) for UI plotting
    const simPathUKF_plot = { x: [], y: [] };
    const simPathUKF_modes = [];

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
            filter.init(entry.px_fw, entry.py_fw, bias.ax, bias.ay, bias.gz);
        }
        if (entry.dt > 0) total_time += entry.dt;

        if (entry.type === 'Predict' && entry.dt > 0) {
            last_ax = entry.ax; last_ay = entry.ay; last_gz = entry.gz;

            const acc_mag = Math.sqrt((entry.ax - bias.ax)**2 + (entry.ay - bias.ay)**2);
            const gyr_mag = Math.abs(entry.gz - bias.gz);
            if (acc_mag < params.zupt_acc && gyr_mag < params.zupt_gyr) zupt_cnt++; else zupt_cnt = 0;
            const zuptActive = zupt_cnt > SIM_CONFIG.IMU.ZUPT_COUNT_THRESHOLD;
            const applyZuptToUkf = params.enable_zupt_ukf === true;
            const zeroImu = {
                ax: filter.ukf.is_initialized ? filter.ukf.x[5] : bias.ax,
                ay: filter.ukf.is_initialized ? filter.ukf.x[6] : bias.ay,
                gz: filter.ukf.is_initialized ? filter.ukf.x[7] : bias.gz
            };
            const imuForUkf = (applyZuptToUkf && zuptActive) ? zeroImu : { ax: entry.ax, ay: entry.ay, gz: entry.gz };

            // UKF Predict
            filter.predict(imuForUkf, entry.dt);
            if (applyZuptToUkf && zuptActive) {
                filter.applyZupt();
            }

            if (zuptActive) {
                v_clean.x = 0;
                v_clean.y = 0;
            } else {
                v_clean.x += (entry.ax - bias.ax) * entry.dt;
                v_clean.y += (entry.ay - bias.ay) * entry.dt;
                v_clean.x *= SIM_CONFIG.IMU.VELOCITY_DECAY;
                v_clean.y *= SIM_CONFIG.IMU.VELOCITY_DECAY;
            }
            yaw += (imuForUkf.gz - bias.gz) * entry.dt;
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

            const acceptedMeasurements = [];
            const frameResults = [];
            const rawRangeAnchors = [];

            entry.distances.forEach((d, i) => {
                const anc = anchors[i];
                const fpAmp = Array.isArray(entry.fp_amp_norm) ? entry.fp_amp_norm[i] : 0;
                if (d > 0.1) {
                    rawRangeAnchors.push({
                        x: anc.x,
                        y: anc.y,
                        r: toPlanarRange(d, anc),
                        id: i + 1,
                        fp_amp: fpAmp
                    });
                }

                // Process range measurements through our UKF-based Mahalanobis Prefilter
                const res = filter.process(i, d, anc);
                d2Scores[i].push(res.d2);

                res.pass = params.enable_mahalanobis ? res.pass : true;
                if (d <= 0.1) res.pass = false; // Always reject near-zero distance
                frameResults.push(res);
            });

            const acceptedBeforeRescue = frameResults.filter(r => r.pass).length;
            let rescued = [];
            if (params.enable_mahalanobis) {
                rescued = filter.rescueFrame(frameResults, params.rescue_min_anchors || 3);
            }

            const rawPos = multilaterate(rawRangeAnchors);
            const rawRms = residualRms(rawPos, rawRangeAnchors);
            if (rawRangeAnchors.length >= 3 &&
                rawRms <= 0.75 &&
                rawPos &&
                !insideAnchorBounds(rawPos, 0.25)) {
                ambiguityEvents.push({
                    index: sampleIdx,
                    acceptedBeforeRescue,
                    rescued: rescued.length,
                    rawResidual: rawRms,
                    x: rawPos.x,
                    y: rawPos.y,
                    anchors: rawRangeAnchors.map(a => a.id)
                });
            }

            frameResults.forEach((res) => {
                const i = res.index;
                const d = res.d;
                const anc = res.anchor;

                if (res.pass) {
                    let smoothed_d = applySmoothing(i, d);
                    if (res.rescue) {
                        gatedDist[i].push(null);
                        rescueIdx[i].push(sampleIdx);
                        rescueDist[i].push(smoothed_d);
                    } else {
                        gatedDist[i].push(smoothed_d);
                    }
                    const r2d = toPlanarRange(smoothed_d, anc);
                    const anchorMeasurement = {
                        x: anc.x,
                        y: anc.y,
                        r: r2d,
                        d2: res.d2,
                        id: i + 1,
                        fp_amp: Array.isArray(entry.fp_amp_norm) ? entry.fp_amp_norm[i] : 0,
                        rescue: res.rescue
                    };
                    v_anchors.push(anchorMeasurement);
                    v_anchors_best.push(anchorMeasurement);
                    if (allowedAnchors.includes(i)) v_anchors_ruled.push(anchorMeasurement);

                    acceptedMeasurements.push({
                        index: i,
                        d: smoothed_d,
                        anchor: anc,
                        r_uwb: filter.measurementNoiseFor(res)
                    });
                } else {
                    gatedDist[i].push(null);
                    if (res.d2 !== null) rejectIdx[i].push(sampleIdx);
                }
            });

            // UKF Update
            filter.update(acceptedMeasurements);

            const pos = multilaterate(v_anchors);
            simPath.x.push(pos ? pos.x : null);
            simPath.y.push(pos ? pos.y : null);

            const pos_ruled = multilaterate(v_anchors_ruled);
            simPathRuled.x.push(pos_ruled ? pos_ruled.x : null);
            simPathRuled.y.push(pos_ruled ? pos_ruled.y : null);

            const pos_wls = multilaterate(v_anchors_best);
            simPathWLS.x.push(pos_wls ? pos_wls.x : null);
            simPathWLS.y.push(pos_wls ? pos_wls.y : null);
            wlsInfo.push(pos_wls ? `N=${v_anchors_best.length}<br>${v_anchors_best.map(a => `A${a.id}(w=${anchorWeight(a).toFixed(2)},amp=${(a.fp_amp || 0).toFixed(1)})`).join(', ')}` : 'None');

            const bestTriplet = selectBestTriplet(v_anchors_best, params.T2_high);
            simPathTriplet.x.push(bestTriplet ? bestTriplet.pos.x : null);
            simPathTriplet.y.push(bestTriplet ? bestTriplet.pos.y : null);
            bestTripletInfo.push(bestTriplet ? `${bestTriplet.triplet.map(a => 'A'+a.id).join(',')}<br>score=${bestTriplet.score.toFixed(3)} fp=${bestTriplet.fpAmpPenalty.toFixed(2)}` : 'None');

            // Record UKF Fusion trajectory for plotting (Update rate = 6Hz)
            simPathUKF_plot.x.push(filter.ukf.is_initialized ? filter.ukf.x[0] : null);
            simPathUKF_plot.y.push(filter.ukf.is_initialized ? filter.ukf.x[1] : null);
            simPathUKF_modes.push(acceptedMeasurements.length > 0 ? 1 : 0);

            plotData.vx.push(v_clean.x);
            plotData.vy.push(v_clean.y);
            plotData.zupt.push(zupt_cnt > SIM_CONFIG.IMU.ZUPT_COUNT_THRESHOLD ? 0.1 : 0);
            plotData.ax.push(last_ax - bias.ax);
            plotData.ay.push(last_ay - bias.ay);
            plotData.gz.push(last_gz - bias.gz);
            plotData.yaw.push(yaw * 180 / Math.PI);
            plotData.times.push(total_time);
            sampleIdx++;
        }

        // Record UKF Fusion trajectory at every processed entry (Predict & Update rate = 20Hz)
        simPathUKF.x.push(filter.ukf.is_initialized ? filter.ukf.x[0] : null);
        simPathUKF.y.push(filter.ukf.is_initialized ? filter.ukf.x[1] : null);
        simPathUKF_allModes.push(entry.type === 'Update' ? 1 : 0); // 1=Update, 0=Predict/Init
        simPathUKF_allTimes.push(total_time);
    });

    const x_axis = simPath.x.map((_, i) => i);
    const pos_errors_fw = [], pos_errors = [], pos_errors_wls = [], pos_errors_triplet = [], pos_errors_ukf = [];
    
    const gtSegments = (groundTruth && groundTruth.segments) || [];
    const calcErr = (pathX, pathY, out) => {
        pathX.forEach((px, i) => {
            if (px === null) { out.push(null); return; }
            let min_d = 999;
            for (const seg of gtSegments) {
                const [x1, y1, x2, y2] = seg;
                const l2 = (x2-x1)**2 + (y2-y1)**2;
                if (l2 <= 0.000001) continue;
                let t = Math.max(0, Math.min(1, ((px-x1)*(x2-x1) + (pathY[i]-y1)*(y2-y1)) / l2));
                min_d = Math.min(min_d, Math.sqrt((px - (x1 + t*(x2-x1)))**2 + (pathY[i] - (y1 + t*(y2-y1)))**2));
            }
            out.push(min_d);
        });
    };
    calcErr(rawData.fw_path.x.slice(0, x_axis.length), rawData.fw_path.y.slice(0, x_axis.length), pos_errors_fw);
    calcErr(simPathRuled.x, simPathRuled.y, pos_errors);
    calcErr(simPathWLS.x, simPathWLS.y, pos_errors_wls);
    calcErr(simPathTriplet.x, simPathTriplet.y, pos_errors_triplet);
    calcErr(simPathUKF_plot.x, simPathUKF_plot.y, pos_errors_ukf);

    self.postMessage({
        simPath, simPathRuled, simPathWLS, simPathTriplet, simPathUKF, simPathUKF_plot, simPathUKF_modes, simPathUKF_allModes, simPathUKF_allTimes,
        wlsInfo, bestTripletInfo,
        plotData, gatedDist, d2Scores, rejectIdx, rescueIdx, rescueDist, ambiguityEvents,
        pos_errors_fw, pos_errors, pos_errors_wls, pos_errors_triplet, pos_errors_ukf,
        x_axis, total_time
    });
};
