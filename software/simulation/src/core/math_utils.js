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

function selectBestTriplet(vAnchors, d2Reject, weights, options) {
    if (vAnchors.length < 3) return null;
    const candidates = [];
    let minGdop = Infinity;
    let maxGdop = 0;
    const healthById = options && options.healthById;

    for (let i = 0; i < vAnchors.length - 2; i++) {
        for (let j = i + 1; j < vAnchors.length - 1; j++) {
            for (let k = j + 1; k < vAnchors.length; k++) {
                const triplet = [vAnchors[i], vAnchors[j], vAnchors[k]];
                const pos = trilaterate(triplet);
                if (!pos) continue;
                const gdop = tripletGdop(pos, triplet);
                if (!Number.isFinite(gdop)) continue;
                const residual = residualRms(pos, triplet);
                const avgD2Raw = triplet.reduce((s, a) => s + (a.d2 || 0), 0) / 3;
                const avgFpAmpPenalty = triplet.reduce((s, a) => s + fpAmpPenalty(a.fp_amp), 0) / 3;
                const avgHealthPenalty = averageTripletHealthPenalty(triplet, healthById);
                const avgRange = triplet.reduce((s, a) => s + (a.r || 0), 0) / 3;
                const rangePenalty = clamp01(avgRange / 15.0);
                candidates.push({
                    triplet,
                    key: tripletKey(triplet),
                    pos,
                    gdop,
                    residual,
                    avgD2Raw,
                    avgFpAmpPenalty,
                    avgHealthPenalty,
                    rangePenalty
                });
                minGdop = Math.min(minGdop, gdop);
                maxGdop = Math.max(maxGdop, gdop);
            }
        }
    }
    if (!candidates.length) return null;

    let best = null;
    const w = normalizeTripletWeights(weights);
    const residualSumsById = {};
    const residualCountsById = {};
    candidates.forEach(c => {
        c.candidateCount = candidates.length;
    });

    for (const c of candidates) {
        const scored = scoreTripletCandidate(c, d2Reject, w, minGdop, maxGdop);
        c.triplet.forEach(a => {
            const id = String(a.id);
            residualSumsById[id] = (residualSumsById[id] || 0.0) + scored.residualPenalty;
            residualCountsById[id] = (residualCountsById[id] || 0) + 1;
        });

        if (!best || scored.score < best.score) {
            best = scored;
        }
    }

    const residualContributionById = {};
    Object.keys(residualSumsById).forEach(id => {
        residualContributionById[id] = residualSumsById[id] / Math.max(1, residualCountsById[id]);
    });
    if (best) {
        best.residualContributionById = residualContributionById;
    }

    const previousKey = options && options.previousKey;
    if (best && previousKey && best.key !== previousKey) {
        const challengerKey = best.key;
        const challengerScore = best.score;
        const challengerHealthPenalty = best.healthPenalty;
        const previous = candidates.find(c => c.key === previousKey);
        if (previous) {
            const scoredPrevious = scoreTripletCandidate(previous, d2Reject, w, minGdop, maxGdop);
            const switchMargin = options && Number.isFinite(options.switchMargin)
                ? Math.max(0, options.switchMargin)
                : (SIM_CONFIG.FILTER.TRIPLET_SWITCH_MARGIN || 0);
            const switchScoreEps = options && Number.isFinite(options.switchScoreEps)
                ? Math.max(0, options.switchScoreEps)
                : (SIM_CONFIG.FILTER.TRIPLET_SWITCH_SCORE_EPS || 0);
            const keepPrevious = scoredPrevious.score <= (best.score * (1.0 + switchMargin)) + switchScoreEps;

            if (keepPrevious) {
                best = Object.assign(scoredPrevious, {
                    residualContributionById,
                    keptPrevious: true,
                    challengerKey,
                    challengerScore,
                    challengerHealthPenalty
                });
            }
        }
    }
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

function calcErrorMetrics(values) {
    const valid = (values || [])
        .filter(v => v !== null && v !== undefined)
        .map(v => Number(v))
        .filter(v => Number.isFinite(v) && v >= 0)
        .sort((a, b) => a - b);

    if (!valid.length) {
        return {
            count: 0,
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
