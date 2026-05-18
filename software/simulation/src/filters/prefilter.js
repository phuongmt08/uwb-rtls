class MahalanobisPrefilter {
    constructor(config) {
        this.T2_high = config.T2_high;
        this.T2_low = config.T2_low;
        this.R_base = config.R_base;
        this.WIN = config.WIN;
        this.k_vel = SIM_CONFIG.FILTER.K_VEL;
        this.histories = [[], [], [], []];
        this.is_rejected = [false, false, false, false];
    }

    process(i, d, v_clean) {
        if (d <= 0.1) return { pass: false, d2: null };

        const hist = this.histories[i];

        // Cold-start
        if (hist.length < 3) {
            hist.push(d);
            return { pass: true, d2: 0 };
        }

        const sorted = [...hist].sort((a, b) => a - b);
        const d_pred = sorted[Math.floor(sorted.length / 2)];
        const mean = hist.reduce((s, v) => s + v, 0) / hist.length;
        const variance = hist.reduce((s, v) => s + (v - mean)**2, 0) / hist.length;
        const vel_mag = Math.sqrt(v_clean.x**2 + v_clean.y**2);
        const S = Math.max(variance, this.R_base) + (this.k_vel * vel_mag);

        const d2 = ((d - d_pred)**2) / S;

        let pass = false;
        if (this.is_rejected[i]) {
            if (d2 < this.T2_low) {
                this.is_rejected[i] = false;
                pass = true;
            }
        } else {
            if (d2 > this.T2_high) {
                this.is_rejected[i] = true;
            } else {
                pass = true;
            }
        }

        if (pass) {
            hist.push(d);
            if (hist.length > this.WIN) hist.shift();
            return { pass: true, d2: d2 };
        } else {
            return { pass: false, d2: d2 };
        }
    }
}
