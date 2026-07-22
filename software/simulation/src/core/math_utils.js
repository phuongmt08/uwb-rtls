function trilaterate(vAnchors) {
    if (vAnchors.length < 3) return null;
    const [a1, a2, a3] = vAnchors;
    const d = 4*((a1.x-a2.x)*(a1.y-a3.y)-(a1.x-a3.x)*(a1.y-a2.y));
    if (Math.abs(d) < 0.001) return null;
    const A = a2.r**2-a1.r**2-a2.x**2+a1.x**2-a2.y**2+a1.y**2;
    const B = a3.r**2-a1.r**2-a3.x**2+a1.x**2-a3.y**2+a1.y**2;
    return {
        x: (1/d)*(2*A*(a1.y-a3.y)-2*B*(a1.y-a2.y)),
        y: (1/d)*(2*B*(a1.x-a2.x)-2*A*(a1.x-a3.x))
    };
}

function multilaterate(vAnchors) {
    if (vAnchors.length < 3) return null;

    const ref = vAnchors[0];
    const others = vAnchors.slice(1);

    let hxx = 0, hxy = 0, hyy = 0, bx = 0, by = 0;
    for (const a of others) {
        const Ai = 2 * (ref.x - a.x);
        const Bi = 2 * (ref.y - a.y);
        const Ci = a.r*a.r - ref.r*ref.r
                 - a.x*a.x + ref.x*ref.x
                 - a.y*a.y + ref.y*ref.y;
        const w = anchorWeight(ref) * anchorWeight(a);
        hxx += w * Ai * Ai;
        hxy += w * Ai * Bi;
        hyy += w * Bi * Bi;
        bx  += w * Ai * Ci;
        by  += w * Bi * Ci;
    }

    const det = hxx * hyy - hxy * hxy;
    if (Math.abs(det) < 0.0001) return null;
    return {
        x: (hyy * bx - hxy * by) / det,
        y: (hxx * by - hxy * bx) / det
    };
}

function clamp01(v) {
    return Math.max(0, Math.min(1, v));
}

function fpAmpPenalty(fpAmp) {
    if (!Number.isFinite(fpAmp) || fpAmp <= 0) return 1.0;
    return clamp01(1.0 - (fpAmp / SIM_CONFIG.FILTER.FP_AMP_GOOD));
}

function fpAmpWeight(fpAmp) {
    return SIM_CONFIG.FILTER.FP_AMP_WEIGHT_FLOOR +
        (1.0 - SIM_CONFIG.FILTER.FP_AMP_WEIGHT_FLOOR) * (1.0 - fpAmpPenalty(fpAmp));
}

function anchorWeight(anchor) {
    const d2 = Number.isFinite(anchor.d2) ? Math.max(0, anchor.d2) : 0;
    const d2Weight = 1.0 / (1.0 + d2);
    const qualityWeight = fpAmpWeight(anchor.fp_amp);
    const rescueWeight = anchor.rescue ? SIM_CONFIG.FILTER.RESCUE_SORT_WEIGHT : 1.0;
    return d2Weight * qualityWeight * rescueWeight;
}

function residualRms(pos, anchorSet) {
    if (!pos) return Infinity;
    const errs = anchorSet.map(a => {
        const pred = Math.sqrt((pos.x-a.x)**2 + (pos.y-a.y)**2);
        return pred - a.r;
    });
    return Math.sqrt(errs.reduce((s, e) => s + e*e, 0) / errs.length);
}

function d2Penalty(d2, d2Reject) {
    return clamp01((d2 || 0) / Math.max(0.001, d2Reject));
}

function tripletGdop(pos, triplet) {
    if (!pos) return Infinity;
    let hxx = 0, hxy = 0, hyy = 0;
    for (const a of triplet) {
        const dx = pos.x - a.x;
        const dy = pos.y - a.y;
        const r = Math.sqrt(dx*dx + dy*dy);
        if (r < 0.001) return Infinity;
        const hx = dx / r;
        const hy = dy / r;
        hxx += hx * hx;
        hxy += hx * hy;
        hyy += hy * hy;
    }
    const det = hxx * hyy - hxy * hxy;
    if (det <= 0.000001) return Infinity;
    return Math.sqrt((hxx + hyy) / det);
}

function normalizeTripletWeights(weights) {
    const defaults = {
        d2: SIM_CONFIG.FILTER.TRIPLET_W_D2 || 0.35,
        fp_amp: SIM_CONFIG.FILTER.TRIPLET_W_FP || 0.15,
        residual: SIM_CONFIG.FILTER.TRIPLET_W_RESIDUAL || 0.30,
        dist: SIM_CONFIG.FILTER.TRIPLET_W_DIST || 0.25,
        health: SIM_CONFIG.FILTER.TRIPLET_W_HEALTH || 0.25
    };

    const w = {
        d2: weights && Number.isFinite(weights.d2) ? weights.d2 : defaults.d2,
        fp_amp: weights && Number.isFinite(weights.fp_amp) ? weights.fp_amp : defaults.fp_amp,
        residual: weights && Number.isFinite(weights.residual) ? weights.residual : defaults.residual,
        dist: weights && Number.isFinite(weights.dist) ? weights.dist : defaults.dist,
        health: weights && Number.isFinite(weights.health) ? weights.health : defaults.health
    };

    const callerUsesPercent = [w.d2, w.fp_amp, w.residual, w.dist].some(v => Math.abs(v) > 1.0);
    if ((!weights || !Number.isFinite(weights.health)) && callerUsesPercent) {
        w.health = defaults.health * 100.0;
    }

    const sum = w.d2 + w.fp_amp + w.residual + w.dist + w.health;
    if (!Number.isFinite(sum) || sum <= 0) return defaults;

    return {
        d2: w.d2 / sum,
        fp_amp: w.fp_amp / sum,
        residual: w.residual / sum,
        dist: w.dist / sum,
        health: w.health / sum
    };
}

function tripletKey(triplet) {
    return triplet.map(a => a.id).slice().sort((a, b) => a - b).join(',');
}

function anchorHealthPenalty(anchor, healthById) {
    if (!anchor || !healthById) return 0.0;
    const health = healthById[anchor.id] !== undefined ? healthById[anchor.id] : healthById[String(anchor.id)];
    if (Number.isFinite(health)) return clamp01(health);
    if (health && Number.isFinite(health.score)) return clamp01(health.score);
    return 0.0;
}

function averageTripletHealthPenalty(triplet, healthById) {
    if (!healthById) return 0.0;
    return triplet.reduce((s, a) => s + anchorHealthPenalty(a, healthById), 0.0) / triplet.length;
}

function scoreTripletCandidate(c, d2Reject, weights, minGdop, maxGdop) {
    const gdopSpan = Math.max(0.001, maxGdop - minGdop);
    const avgD2Penalty = c.triplet.reduce((s, a) => s + d2Penalty(a.d2, d2Reject), 0) / 3;
    const gdopPenalty = clamp01((c.gdop - minGdop) / gdopSpan);
    const residualPenalty = clamp01(c.residual / 0.30);
    const fpAmpPenaltyAvg = c.avgFpAmpPenalty;
    const distPenalty = c.rangePenalty;
    const healthPenaltyAvg = c.avgHealthPenalty;
    const score =
        weights.d2 * avgD2Penalty +
        weights.fp_amp * fpAmpPenaltyAvg +
        weights.residual * residualPenalty +
        weights.dist * distPenalty +
        weights.health * healthPenaltyAvg;

    return {
        triplet: c.triplet,
        key: c.key,
        candidateCount: c.candidateCount,
        pos: c.pos,
        score,
        avgD2Raw: c.avgD2Raw,
        avgD2Penalty,
        gdopRaw: c.gdop,
        gdopPenalty,
        fpAmpPenalty: fpAmpPenaltyAvg,
        residual: c.residual,
        residualPenalty,
        distPenalty,
        healthPenalty: healthPenaltyAvg
    };
}

function firmwareHuberWeight(value, delta) {
    const magnitude = Math.abs(value);
    if (!Number.isFinite(magnitude)) return SIM_CONFIG.FILTER.HUBER_WEIGHT_FLOOR;
    if (delta <= 1.0e-9 || magnitude <= delta) return 1.0;
    return Math.max(SIM_CONFIG.FILTER.HUBER_WEIGHT_FLOOR, delta / magnitude);
}

function firmwareRangeVariance(anchor) {
    const distance = Math.abs(anchor.r);
    const sigma = Math.min(
        SIM_CONFIG.FILTER.RANGE_SIGMA_MAX_M,
        Math.sqrt(
            SIM_CONFIG.FILTER.RANGE_SIGMA_BASE_M ** 2
            + (SIM_CONFIG.FILTER.RANGE_SIGMA_SLOPE * distance) ** 2
        )
    );
    return sigma * sigma * (anchor.rescue ? SIM_CONFIG.FILTER.DEFAULT_RESCUE_NOISE_SCALE_MIN : 1.0);
}

function firmwareFpWeight(anchor) {
    if (!anchor.quality_valid) {
        return firmwareHuberWeight(0.5, SIM_CONFIG.FILTER.HUBER_FP_DEFICIT_DELTA);
    }
    if (!Number.isFinite(anchor.fp_confidence) || anchor.fp_confidence < 0.0) {
        return SIM_CONFIG.FILTER.HUBER_WEIGHT_FLOOR;
    }
    const deficit = 1.0 - Math.min(1.0, anchor.fp_confidence);
    return deficit <= 0.0
        ? 1.0
        : firmwareHuberWeight(deficit, SIM_CONFIG.FILTER.HUBER_FP_DEFICIT_DELTA);
}

function median(values) {
    if (!values.length) return 0.0;
    const sorted = values.slice().sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    return (sorted.length % 2) ? sorted[mid] : 0.5 * (sorted[mid - 1] + sorted[mid]);
}

function computeFirmwareMeasurementWeights(anchors, referenceValid, referencePosition) {
    const useResidual = !!referenceValid && anchors.length >= 4;
    const residuals = useResidual
        ? anchors.map(a => a.r - Math.hypot(referencePosition.x - a.x, referencePosition.y - a.y))
        : anchors.map(() => 0.0);
    const residualMedian = useResidual ? median(residuals) : 0.0;
    const madScale = useResidual
        ? 1.4826 * median(residuals.map(r => Math.abs(r - residualMedian)))
        : 0.0;

    anchors.forEach((anchor, i) => {
        const variance = firmwareRangeVariance(anchor);
        const qD2 = (!(anchor.d2 > 0.0))
            ? 1.0
            : firmwareHuberWeight(
                Math.sqrt(anchor.d2),
                Math.sqrt(SIM_CONFIG.FILTER.DEFAULT_T2_LOW)
            );
        const qFp = firmwareFpWeight(anchor);
        let qResidual = 1.0;
        if (useResidual) {
            const scale = Math.max(madScale, Math.sqrt(variance));
            const normalized = Math.abs(residuals[i] - residualMedian) / scale;
            qResidual = firmwareHuberWeight(normalized, SIM_CONFIG.FILTER.HUBER_RESIDUAL_DELTA);
        }
        anchor.range_variance = variance;
        anchor.q_d2 = qD2;
        anchor.q_fp = qFp;
        anchor.q_residual = qResidual;
        anchor.frame_residual = useResidual ? residuals[i] : null;
        anchor.measurement_weight = (qD2 * qFp * qResidual) / variance;
    });
}

function firmwareTripletWgdop(pos, triplet) {
    let hxx = 0.0, hxy = 0.0, hyy = 0.0;
    for (const anchor of triplet) {
        const dx = pos.x - anchor.x;
        const dy = pos.y - anchor.y;
        const range = Math.hypot(dx, dy);
        if (range < 1.0e-9) return 1.0e9;
        const hx = dx / range;
        const hy = dy / range;
        const fallbackWeight = SIM_CONFIG.FILTER.HUBER_WEIGHT_FLOOR / firmwareRangeVariance(anchor);
        const weight = anchor.measurement_weight > 0.0 && Number.isFinite(anchor.measurement_weight)
            ? anchor.measurement_weight
            : fallbackWeight;
        hxx += weight * hx * hx;
        hxy += weight * hx * hy;
        hyy += weight * hy * hy;
    }
    const det = hxx * hyy - hxy * hxy;
    if (!Number.isFinite(det) || det <= SIM_CONFIG.FILTER.WGDOP_DET_MIN) return 1.0e9;
    return Math.sqrt((hxx + hyy) / det);
}

function selectBestTriplet(vAnchors, d2Reject, weights, options) {
    if (vAnchors.length < 3) return null;
    const referenceValid = !!(options && options.referenceValid);
    const referencePosition = options && options.referencePosition;
    computeFirmwareMeasurementWeights(vAnchors, referenceValid, referencePosition);

    const candidates = [];
    for (let i = 0; i < vAnchors.length - 2; i++) {
        for (let j = i + 1; j < vAnchors.length - 1; j++) {
            for (let k = j + 1; k < vAnchors.length; k++) {
                const triplet = [vAnchors[i], vAnchors[j], vAnchors[k]];
                const pos = trilaterate(triplet);
                if (!pos) continue;
                const scorePosition = referenceValid ? referencePosition : pos;
                const wgdop = firmwareTripletWgdop(scorePosition, triplet);
                if (wgdop >= 1.0e8) continue;
                candidates.push({
                    triplet,
                    key: tripletKey(triplet),
                    pos,
                    score: wgdop,
                    gdopRaw: wgdop,
                    gdopPenalty: wgdop,
                    residual: residualRms(pos, vAnchors),
                    residualPenalty: 0.0,
                    avgD2Raw: triplet.reduce((s, a) => s + (a.d2 || 0.0), 0.0) / 3.0,
                    avgD2Penalty: 0.0,
                    fpAmpPenalty: 1.0 - triplet.reduce((s, a) => s + a.q_fp, 0.0) / 3.0,
                    distPenalty: 0.0,
                    healthPenalty: 0.0
                });
            }
        }
    }
    if (!candidates.length) return null;
    candidates.forEach(c => {
        c.candidateCount = vAnchors.length;
        c.tripletCombinationCount = candidates.length;
    });

    let best = candidates.reduce((a, b) => b.score < a.score ? b : a);
    const previousKey = options && options.previousKey;
    if (previousKey && best.key !== previousKey) {
        const previous = candidates.find(c => c.key === previousKey);
        if (previous) {
            const switchMargin = options && Number.isFinite(options.switchMargin)
                ? Math.max(0.0, options.switchMargin)
                : SIM_CONFIG.FILTER.TRIPLET_SWITCH_MARGIN;
            const switchScoreEps = options && Number.isFinite(options.switchScoreEps)
                ? Math.max(0.0, options.switchScoreEps)
                : SIM_CONFIG.FILTER.TRIPLET_SWITCH_SCORE_EPS;
            if (previous.score <= best.score * (1.0 + switchMargin) + switchScoreEps) {
                const challenger = best;
                best = Object.assign(previous, {
                    keptPrevious: true,
                    challengerKey: challenger.key,
                    challengerScore: challenger.score,
                    challengerHealthPenalty: 0.0
                });
            }
        }
    }

    const residualContributionById = {};
    vAnchors.forEach(a => { residualContributionById[String(a.id)] = 1.0 - a.q_residual; });
    best.residualContributionById = residualContributionById;
    best.referenceValid = referenceValid;
    return best;
}

function meanFinite(values) {
    let sum = 0;
    let count = 0;
    values.forEach(v => {
        if (Number.isFinite(v)) {
            sum += v;
            count++;
        }
    });
    return count > 0 ? { mean: sum / count, count } : { mean: null, count: 0 };
}

function meanErr(arr) {
    const valid = arr.filter(v => Number.isFinite(v));
    return valid.length ? (valid.reduce((s, v) => s + v, 0) / valid.length).toFixed(3) : "N/A";
}

function buildGroundTruthSegmentProfiles(segments) {
    const angleThresholdRad = Math.max(0, Number(SIM_CONFIG.ENV.GROUND_TRUTH_CURVE_ANGLE_DEG) || 0) * Math.PI / 180;
    const endpointEpsilon = 1.0e-6;
    const samePoint = (ax, ay, bx, by) => Math.hypot(ax - bx, ay - by) <= endpointEpsilon;

    const profiles = segments.map(seg => {
        const dx = seg[2] - seg[0];
        const dy = seg[3] - seg[1];
        return { length: Math.hypot(dx, dy), startCurve: false, endCurve: false };
    });

    const endpointIsCurve = (segmentIndex, atStart) => {
        const seg = segments[segmentIndex];
        const ex = atStart ? seg[0] : seg[2];
        const ey = atStart ? seg[1] : seg[3];
        const vx = (atStart ? seg[2] : seg[0]) - ex;
        const vy = (atStart ? seg[3] : seg[1]) - ey;
        const vLen = Math.hypot(vx, vy);
        if (vLen <= endpointEpsilon) return false;

        for (let j = 0; j < segments.length; j++) {
            if (j === segmentIndex) continue;
            const other = segments[j];
            let ox;
            let oy;
            if (samePoint(ex, ey, other[0], other[1])) {
                ox = other[2] - ex;
                oy = other[3] - ey;
            } else if (samePoint(ex, ey, other[2], other[3])) {
                ox = other[0] - ex;
                oy = other[1] - ey;
            } else {
                continue;
            }

            const oLen = Math.hypot(ox, oy);
            if (oLen <= endpointEpsilon) continue;
            const alignment = Math.min(1, Math.max(-1, Math.abs((vx * ox + vy * oy) / (vLen * oLen))));
            if (Math.acos(alignment) >= angleThresholdRad) return true;
        }
        return false;
    };

    profiles.forEach((profile, index) => {
        profile.startCurve = endpointIsCurve(index, true);
        profile.endCurve = endpointIsCurve(index, false);
    });
    return profiles;
}

function groundTruthToleranceAtProjection(profile, projectionT) {
    const straightTolerance = Math.max(0, Number(SIM_CONFIG.ENV.GROUND_TRUTH_TOLERANCE_STRAIGHT_M) || 0);
    const curveTolerance = Math.max(straightTolerance, Number(SIM_CONFIG.ENV.GROUND_TRUTH_TOLERANCE_CURVE_M) || 0);
    const curveRadius = Math.max(0, Number(SIM_CONFIG.ENV.GROUND_TRUTH_CURVE_INFLUENCE_RADIUS_M) || 0);
    if (!profile || profile.length <= 0) return straightTolerance;

    const distanceFromStart = projectionT * profile.length;
    const distanceFromEnd = (1 - projectionT) * profile.length;
    const inCurve = (profile.startCurve && distanceFromStart <= curveRadius) ||
        (profile.endCurve && distanceFromEnd <= curveRadius);
    return inCurve ? curveTolerance : straightTolerance;
}

function calcErrorMetrics(values) {
    const valid = (values || [])
        .filter(v => v !== null && v !== undefined)
        .map(v => Number(v))
        .filter(v => Number.isFinite(v) && v >= 0)
        .sort((a, b) => a - b);

    if (!valid.length) {
        return {
            count: 0,
            euclidean: null,
            mae: null,
            rmse: null,
            p50: null,
            p90: null,
            p95: null,
            max: null
        };
    }

    const sum = valid.reduce((acc, v) => acc + v, 0);
    const sumSquares = valid.reduce((acc, v) => acc + v * v, 0);
    const percentile = (p) => {
        if (valid.length === 1) return valid[0];
        const rank = (p / 100) * (valid.length - 1);
        const lo = Math.floor(rank);
        const hi = Math.ceil(rank);
        if (lo === hi) return valid[lo];
        return valid[lo] + (valid[hi] - valid[lo]) * (rank - lo);
    };

    return {
        count: valid.length,
        // Each value is already sqrt(dx^2 + dy^2); report its mean as the
        // aggregate Euclidean position error for the selected trajectory.
        euclidean: sum / valid.length,
        mae: sum / valid.length,
        rmse: Math.sqrt(sumSquares / valid.length),
        p50: percentile(50),
        p90: percentile(90),
        p95: percentile(95),
        max: valid[valid.length - 1]
    };
}

function formatErrorMetric(value) {
    return Number.isFinite(value) ? `${value.toFixed(3)} m` : "N/A";
}

function computeTimeDomainSpectrum(values, times) {
    const clean = [];
    const cleanTimes = [];
    values.forEach((v, i) => {
        const t = times && times[i];
        if (Number.isFinite(v) && Number.isFinite(t)) {
            clean.push(v);
            cleanTimes.push(t);
        }
    });

    const n = clean.length;
    if (n < 4) return { freq: [], mag: [] };

    const maxFftSize = 16384;
    let nfft = 1;
    while ((nfft * 2) <= n && (nfft * 2) <= maxFftSize) nfft *= 2;
    if (nfft < 4) return { freq: [], mag: [] };

    const start = Math.max(0, n - nfft);
    const duration = cleanTimes[start + nfft - 1] - cleanTimes[start];
    const fs = duration > 0 ? (nfft - 1) / duration : 0;
    if (!Number.isFinite(fs) || fs <= 0) return { freq: [], mag: [] };

    let mean = 0;
    for (let i = 0; i < nfft; i++) mean += clean[start + i];
    mean /= nfft;

    const re = new Array(nfft);
    const im = new Array(nfft).fill(0);
    let windowSum = 0;
    for (let i = 0; i < nfft; i++) {
        const w = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / (nfft - 1));
        re[i] = (clean[start + i] - mean) * w;
        windowSum += w;
    }

    for (let i = 1, j = 0; i < nfft; i++) {
        let bit = nfft >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) {
            const tr = re[i]; re[i] = re[j]; re[j] = tr;
            const ti = im[i]; im[i] = im[j]; im[j] = ti;
        }
    }

    for (let len = 2; len <= nfft; len <<= 1) {
        const angle = -2 * Math.PI / len;
        const wLenRe = Math.cos(angle);
        const wLenIm = Math.sin(angle);
        for (let i = 0; i < nfft; i += len) {
            let wRe = 1;
            let wIm = 0;
            const half = len >> 1;
            for (let j = 0; j < half; j++) {
                const uRe = re[i + j];
                const uIm = im[i + j];
                const vRe = re[i + j + half] * wRe - im[i + j + half] * wIm;
                const vIm = re[i + j + half] * wIm + im[i + j + half] * wRe;
                re[i + j] = uRe + vRe;
                im[i + j] = uIm + vIm;
                re[i + j + half] = uRe - vRe;
                im[i + j + half] = uIm - vIm;

                const nextWRe = wRe * wLenRe - wIm * wLenIm;
                wIm = wRe * wLenIm + wIm * wLenRe;
                wRe = nextWRe;
            }
        }
    }

    const freq = [];
    const mag = [];
    const scale = windowSum > 0 ? 2 / windowSum : 2 / nfft;
    for (let k = 1; k <= Math.floor(nfft / 2); k++) {
        freq.push(k * fs / nfft);
        mag.push(scale * Math.sqrt(re[k] * re[k] + im[k] * im[k]));
    }

    return { freq, mag };
}
