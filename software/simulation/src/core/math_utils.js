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
        d2: SIM_CONFIG.FILTER.TRIPLET_W_D2,
        fp_amp: SIM_CONFIG.FILTER.TRIPLET_W_FP,
        gdop: SIM_CONFIG.FILTER.TRIPLET_W_GDOP,
        residual: SIM_CONFIG.FILTER.TRIPLET_W_RESIDUAL
    };

    const w = {
        d2: weights && Number.isFinite(weights.d2) ? weights.d2 : defaults.d2,
        fp_amp: weights && Number.isFinite(weights.fp_amp) ? weights.fp_amp : defaults.fp_amp,
        gdop: weights && Number.isFinite(weights.gdop) ? weights.gdop : defaults.gdop,
        residual: weights && Number.isFinite(weights.residual) ? weights.residual : defaults.residual
    };

    const sum = w.d2 + w.fp_amp + w.gdop + w.residual;
    if (!Number.isFinite(sum) || sum <= 0) return defaults;

    return {
        d2: w.d2 / sum,
        fp_amp: w.fp_amp / sum,
        gdop: w.gdop / sum,
        residual: w.residual / sum
    };
}

function selectBestTriplet(vAnchors, d2Reject, weights) {
    if (vAnchors.length < 3) return null;
    const candidates = [];
    let minGdop = Infinity;
    let maxGdop = 0;

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
                candidates.push({ triplet, pos, gdop, residual, avgD2Raw, avgFpAmpPenalty });
                minGdop = Math.min(minGdop, gdop);
                maxGdop = Math.max(maxGdop, gdop);
            }
        }
    }
    if (!candidates.length) return null;

    let best = null;
    const w = normalizeTripletWeights(weights);
    const gdopSpan = Math.max(0.001, maxGdop - minGdop);
    for (const c of candidates) {
        const avgD2Penalty = c.triplet.reduce((s, a) => s + d2Penalty(a.d2, d2Reject), 0) / 3;
        const gdopPenalty = clamp01((c.gdop - minGdop) / gdopSpan);
        const residualPenalty = clamp01(c.residual / 0.30);
        const fpAmpPenaltyAvg = c.avgFpAmpPenalty;
        const score =
            w.d2 * avgD2Penalty +
            w.fp_amp * fpAmpPenaltyAvg +
            w.gdop * gdopPenalty +
            w.residual * residualPenalty;

        if (!best || score < best.score) {
            best = {
                triplet: c.triplet,
                pos: c.pos,
                score,
                avgD2Raw: c.avgD2Raw,
                avgD2Penalty,
                gdopRaw: c.gdop,
                gdopPenalty,
                fpAmpPenalty: fpAmpPenaltyAvg,
                residual: c.residual,
                residualPenalty
            };
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
    const valid = arr.filter(v => v !== null);
    return valid.length ? (valid.reduce((s, v) => s + v, 0) / valid.length).toFixed(3) : "N/A";
}
