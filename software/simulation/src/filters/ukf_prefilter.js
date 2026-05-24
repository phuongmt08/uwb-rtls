// Helper for angle normalization to [-PI, PI]
function normalizeAngle(angle) {
    let wrapped = (angle + Math.PI) % (2.0 * Math.PI);
    if (wrapped < 0) {
        wrapped += 2.0 * Math.PI;
    }
    return wrapped - Math.PI;
}

// 11x11 Cholesky Decomposition with Diagonal Safeguards
function cholesky11(A) {
    const n = 11;
    const L = Array.from({ length: n }, () => new Float64Array(n));
    for (let i = 0; i < n; i++) {
        for (let j = 0; j <= i; j++) {
            let sum = 0;
            for (let k = 0; k < j; k++) {
                sum += L[i][k] * L[j][k];
            }
            if (i === j) {
                let val = (Number.isFinite(A[i][i]) ? A[i][i] : 0.0) - sum;
                if (!Number.isFinite(val) || val < 1e-9) val = 1e-9; // Protect positive definiteness
                L[i][j] = Math.sqrt(val);
            } else {
                const numerator = (Number.isFinite(A[i][j]) ? A[i][j] : 0.0) - sum;
                L[i][j] = Number.isFinite(numerator) ? numerator / L[j][j] : 0.0;
            }
        }
    }
    return L;
}

// Matrix Inversion via Gaussian Elimination with Partial Pivoting (dynamic size)
function invertMatrix(M) {
    const n = M.length;
    for (let i = 0; i < n; i++) {
        for (let j = 0; j < n; j++) {
            if (!Number.isFinite(M[i][j])) {
                return null;
            }
        }
    }
    const A = Array.from({ length: n }, (_, i) => {
        const row = new Float64Array(2 * n);
        for (let j = 0; j < n; j++) row[j] = M[i][j];
        row[n + i] = 1.0;
        return row;
    });
    
    for (let i = 0; i < n; i++) {
        let maxRow = i;
        for (let j = i + 1; j < n; j++) {
            if (Math.abs(A[j][i]) > Math.abs(A[maxRow][i])) {
                maxRow = j;
            }
        }
        if (Math.abs(A[maxRow][i]) < 1e-12) {
            return null; // Singular matrix
        }
        const temp = A[i];
        A[i] = A[maxRow];
        A[maxRow] = temp;
        
        const pivot = A[i][i];
        for (let j = i; j < 2 * n; j++) {
            A[i][j] /= pivot;
        }
        
        for (let j = 0; j < n; j++) {
            if (j !== i) {
                const factor = A[j][i];
                for (let k = i; k < 2 * n; k++) {
                    A[j][k] -= factor * A[i][k];
                }
            }
        }
    }
    
    const inv = Array.from({ length: n }, () => new Float64Array(n));
    for (let i = 0; i < n; i++) {
        for (let j = 0; j < n; j++) {
            inv[i][j] = A[i][n + j];
        }
    }
    return inv;
}

class UnscentedKalmanFilter {
    constructor(config) {
        // State variables: [px, py, vx, vy, theta, bax, bay, bgz]
        this.L = 8;
        this.W = 3;
        this.L_aug = this.L + this.W; // 11
        this.num_sigmas = 2 * this.L_aug + 1; // 23

        this.x = new Float64Array(this.L);
        this.P = Array.from({ length: this.L }, () => new Float64Array(this.L));
        
        this.is_initialized = false;
        this.X_sigma_pred = Array.from({ length: this.num_sigmas }, () => new Float64Array(this.L));

        // Param config
        this.alpha = config.ukf_alpha !== undefined ? config.ukf_alpha : 0.1;
        this.beta = config.ukf_beta !== undefined ? config.ukf_beta : 2.0;
        this.kappa = config.ukf_kappa !== undefined ? config.ukf_kappa : 0.0;
        
        this.q_a = config.q_a !== undefined ? config.q_a : 0.04;
        this.q_g = config.q_g !== undefined ? config.q_g : 4.78e-7;
        this.r_uwb = config.r_uwb !== undefined ? config.r_uwb : 0.01;
        this.r_gate = config.r_gate !== undefined ? config.r_gate : this.r_uwb;
        this.use_planar_ranges = config.use_planar_ranges !== undefined
            ? config.use_planar_ranges
            : true;
        this.zupt_velocity_variance = config.zupt_velocity_variance !== undefined
            ? config.zupt_velocity_variance
            : 0.0004;
        this.position_bounds = config.position_bounds || null;
        this.max_update_position_step = config.max_update_position_step !== undefined
            ? config.max_update_position_step
            : 1.0;
        this.max_update_velocity_step = config.max_update_velocity_step !== undefined
            ? config.max_update_velocity_step
            : 1.0;

        // Weights
        this.lambda = this.alpha * this.alpha * (this.L_aug + this.kappa) - this.L_aug;
        this.gamma = Math.sqrt(this.L_aug + this.lambda);

        this.Wm = new Float64Array(this.num_sigmas);
        this.Wc = new Float64Array(this.num_sigmas);

        this.Wm[0] = this.lambda / (this.L_aug + this.lambda);
        this.Wc[0] = this.Wm[0] + (1.0 - this.alpha * this.alpha + this.beta);
        for (let i = 1; i < this.num_sigmas; i++) {
            this.Wm[i] = 1.0 / (2.0 * (this.L_aug + this.lambda));
            this.Wc[i] = this.Wm[i];
        }
    }

    seedSigmaPredictionFromState() {
        for (let m = 0; m < this.num_sigmas; m++) {
            for (let i = 0; i < this.L; i++) {
                this.X_sigma_pred[m][i] = this.x[i];
            }
        }

        for (let i = 0; i < this.L; i++) {
            const delta = this.gamma * Math.sqrt(Math.max(this.P[i][i], 1e-9));
            const plus = i + 1;
            const minus = i + 1 + this.L_aug;
            this.X_sigma_pred[plus][i] += delta;
            this.X_sigma_pred[minus][i] -= delta;
        }
    }

    cloneCovariance() {
        return this.P.map(row => Float64Array.from(row));
    }

    restoreState(x, P) {
        this.x = Float64Array.from(x);
        this.P = P.map(row => Float64Array.from(row));
        this.seedSigmaPredictionFromState();
    }

    isFiniteState() {
        for (let i = 0; i < this.L; i++) {
            if (!Number.isFinite(this.x[i])) return false;
            for (let j = 0; j < this.L; j++) {
                if (!Number.isFinite(this.P[i][j])) return false;
            }
        }
        return true;
    }

    isSaneState() {
        if (!this.isFiniteState()) return false;
        const limits = [100.0, 100.0, 20.0, 20.0, Math.PI + 1e-6, 20.0, 20.0, 20.0];
        for (let i = 0; i < this.L; i++) {
            if (Math.abs(this.x[i]) > limits[i]) return false;
        }
        if (this.position_bounds) {
            const b = this.position_bounds;
            if (this.x[0] < b.minX || this.x[0] > b.maxX ||
                this.x[1] < b.minY || this.x[1] > b.maxY) {
                return false;
            }
        }
        return true;
    }

    stabilizeCovariance() {
        const minDiag = 1e-9;
        for (let i = 0; i < this.L; i++) {
            for (let j = 0; j < i; j++) {
                const a = this.P[i][j];
                const b = this.P[j][i];
                let val = 0.0;
                if (Number.isFinite(a) && Number.isFinite(b)) {
                    val = 0.5 * (a + b);
                } else if (Number.isFinite(a)) {
                    val = a;
                } else if (Number.isFinite(b)) {
                    val = b;
                }
                this.P[i][j] = val;
                this.P[j][i] = val;
            }
            if (!Number.isFinite(this.P[i][i]) || this.P[i][i] < minDiag) {
                this.P[i][i] = minDiag;
            }
        }
    }

    init(init_x, init_y, bias_ax, bias_ay, bias_gz) {
        this.x[0] = init_x;
        this.x[1] = init_y;
        this.x[2] = 0.0;
        this.x[3] = 0.0;
        this.x[4] = 0.0; // theta
        this.x[5] = bias_ax;
        this.x[6] = bias_ay;
        this.x[7] = bias_gz;

        // Init covariance P
        for (let i = 0; i < this.L; i++) {
            for (let j = 0; j < this.L; j++) {
                this.P[i][j] = 0.0;
            }
        }
        this.P[0][0] = 1e-6; // px
        this.P[1][1] = 1e-6; // py
        this.P[2][2] = 1e-6; // vx
        this.P[3][3] = 1e-6; // vy
        this.P[4][4] = 1e-20; // theta
        this.P[5][5] = 1e-5; // bax
        this.P[6][6] = 1e-5; // bay
        this.P[7][7] = 1e-5; // bgz

        this.is_initialized = true;
        this.seedSigmaPredictionFromState();
    }

    predict(imu, dt) {
        if (!this.is_initialized) return;
        if (!Number.isFinite(dt) || dt <= 0) return;

        const prevX = Float64Array.from(this.x);
        const prevP = this.cloneCovariance();
        const imuAx = Number.isFinite(imu.ax) ? imu.ax : this.x[5];
        const imuAy = Number.isFinite(imu.ay) ? imu.ay : this.x[6];
        const imuGz = Number.isFinite(imu.gz) ? imu.gz : this.x[7];

        // 1. Generate augmented state and covariance
        const x_aug = new Float64Array(this.L_aug);
        for (let i = 0; i < this.L; i++) x_aug[i] = this.x[i];

        const P_aug = Array.from({ length: this.L_aug }, () => new Float64Array(this.L_aug));
        for (let i = 0; i < this.L; i++) {
            for (let j = 0; j < this.L; j++) {
                P_aug[i][j] = this.P[i][j];
            }
        }
        P_aug[8][8] = this.q_a;
        P_aug[9][9] = this.q_a;
        P_aug[10][10] = this.q_g;

        const sqrt_P_aug = cholesky11(P_aug);

        // 2. Generate sigma points (size 23x11)
        const sigma_pts = Array.from({ length: this.num_sigmas }, () => new Float64Array(this.L_aug));
        for (let i = 0; i < this.L_aug; i++) sigma_pts[0][i] = x_aug[i];
        for (let i = 0; i < this.L_aug; i++) {
            for (let j = 0; j < this.L_aug; j++) {
                const delta = this.gamma * sqrt_P_aug[j][i];
                sigma_pts[i + 1][j] = x_aug[j] + delta;
                sigma_pts[i + 1 + this.L_aug][j] = x_aug[j] - delta;
            }
        }

        // 3. Propagate sigma points (size 23x8)
        for (let m = 0; m < this.num_sigmas; m++) {
            const sp = sigma_pts[m];
            const px = sp[0], py = sp[1], vx = sp[2], vy = sp[3], theta = sp[4];
            const bax = sp[5], bay = sp[6], bgz = sp[7];
            const n_ax = sp[8], n_ay = sp[9], n_gz = sp[10];

            const corrected_ax = imuAx - bax + n_ax;
            const corrected_ay = imuAy - bay + n_ay;
            const corrected_gz = imuGz - bgz + n_gz;

            const theta_new = normalizeAngle(theta + corrected_gz * dt);
            const cos_t = Math.cos(theta);
            const sin_t = Math.sin(theta);
            
            const ax_world = corrected_ax * cos_t - corrected_ay * sin_t;
            const ay_world = corrected_ax * sin_t + corrected_ay * cos_t;

            this.X_sigma_pred[m][0] = px + vx * dt + 0.5 * ax_world * dt * dt;
            this.X_sigma_pred[m][1] = py + vy * dt + 0.5 * ay_world * dt * dt;
            this.X_sigma_pred[m][2] = vx + ax_world * dt;
            this.X_sigma_pred[m][3] = vy + ay_world * dt;
            this.X_sigma_pred[m][4] = theta_new;
            this.X_sigma_pred[m][5] = bax;
            this.X_sigma_pred[m][6] = bay;
            this.X_sigma_pred[m][7] = bgz;
        }

        // 4. Compute predicted state mean
        const x_new = new Float64Array(this.L);
        let sum_sin = 0.0, sum_cos = 0.0;
        for (let m = 0; m < this.num_sigmas; m++) {
            const t = this.X_sigma_pred[m][4];
            sum_sin += this.Wm[m] * Math.sin(t);
            sum_cos += this.Wm[m] * Math.cos(t);
        }
        x_new[4] = Math.atan2(sum_sin, sum_cos);

        for (let i = 0; i < this.L; i++) {
            if (i === 4) continue;
            let sum = 0.0;
            for (let m = 0; m < this.num_sigmas; m++) {
                sum += this.Wm[m] * this.X_sigma_pred[m][i];
            }
            x_new[i] = sum;
        }
        this.x = x_new;

        // 5. Compute predicted covariance P
        const P_new = Array.from({ length: this.L }, () => new Float64Array(this.L));
        for (let m = 0; m < this.num_sigmas; m++) {
            const diff = new Float64Array(this.L);
            for (let i = 0; i < this.L; i++) {
                diff[i] = this.X_sigma_pred[m][i] - this.x[i];
            }
            diff[4] = normalizeAngle(diff[4]);

            for (let i = 0; i < this.L; i++) {
                for (let j = 0; j < this.L; j++) {
                    P_new[i][j] += this.Wc[m] * diff[i] * diff[j];
                }
            }
        }

        // Numerical stabilizer
        const epsilon = 1e-9;
        for (let i = 0; i < this.L; i++) {
            P_new[i][i] += epsilon;
        }

        this.P = P_new;
        this.stabilizeCovariance();
        if (!this.isSaneState()) {
            this.restoreState(prevX, prevP);
        }
    }

    predictedRange(px, py, anchor, tagHeight) {
        const planar = Math.sqrt((px - anchor.x)**2 + (py - anchor.y)**2);
        if (this.use_planar_ranges) return planar;
        return Math.sqrt(planar * planar + (tagHeight - anchor.z)**2);
    }

    computeMahalanobis(d, anchor, tagHeight) {
        if (!this.is_initialized) return { d_pred: d, d2: 0 };
        if (!Number.isFinite(d)) return { d_pred: null, d2: Infinity };

        // Fusion logs normally carry planar ranges; app_tag projects raw UWB 3D
        // distance to 2D before logging d1..d4.
        const z_sigma = new Float64Array(this.num_sigmas);
        for (let m = 0; m < this.num_sigmas; m++) {
            const px = this.X_sigma_pred[m][0];
            const py = this.X_sigma_pred[m][1];
            z_sigma[m] = this.predictedRange(px, py, anchor, tagHeight);
        }

        // 2. Mean predicted measurement
        let z_mean = 0.0;
        for (let m = 0; m < this.num_sigmas; m++) {
            z_mean += this.Wm[m] * z_sigma[m];
        }

        // 3. Innovation covariance S
        let S = 0.0;
        for (let m = 0; m < this.num_sigmas; m++) {
            S += this.Wc[m] * (z_sigma[m] - z_mean) * (z_sigma[m] - z_mean);
        }
        S += this.r_gate; // Gate noise can be looser than update noise.
        if (!Number.isFinite(z_mean) || !Number.isFinite(S) || S <= 0) {
            return { d_pred: z_mean, d2: Infinity };
        }

        // 4. Mahalanobis distance squared
        const d2 = ((d - z_mean) ** 2) / Math.max(1e-6, S);

        return { d_pred: z_mean, d2: d2 };
    }

    update(acceptedMeasurements, tagHeight) {
        if (!this.is_initialized || acceptedMeasurements.length === 0) return;

        const measurements = acceptedMeasurements.filter(m =>
            m &&
            Number.isFinite(m.d) &&
            m.anchor &&
            Number.isFinite(m.anchor.x) &&
            Number.isFinite(m.anchor.y) &&
            Number.isFinite(m.anchor.z)
        );
        if (measurements.length === 0) return;

        const prevX = Float64Array.from(this.x);
        const prevP = this.cloneCovariance();
        const M = measurements.length;

        // 1. Measurements vector and predictions
        const z_sigma = Array.from({ length: M }, () => new Float64Array(this.num_sigmas));
        for (let j = 0; j < M; j++) {
            const anc = measurements[j].anchor;
            for (let m = 0; m < this.num_sigmas; m++) {
                const px = this.X_sigma_pred[m][0];
                const py = this.X_sigma_pred[m][1];
                z_sigma[j][m] = this.predictedRange(px, py, anc, tagHeight);
            }
        }

        const z_mean = new Float64Array(M);
        for (let j = 0; j < M; j++) {
            let sum = 0.0;
            for (let m = 0; m < this.num_sigmas; m++) {
                sum += this.Wm[m] * z_sigma[j][m];
            }
            z_mean[j] = sum;
        }

        // 2. Innovation covariance S (MxM) and Cross covariance Tc (LxM)
        const S = Array.from({ length: M }, () => new Float64Array(M));
        const Tc = Array.from({ length: this.L }, () => new Float64Array(M));

        for (let m = 0; m < this.num_sigmas; m++) {
            const z_diff = new Float64Array(M);
            for (let j = 0; j < M; j++) {
                z_diff[j] = z_sigma[j][m] - z_mean[j];
            }

            const x_diff = new Float64Array(this.L);
            for (let i = 0; i < this.L; i++) {
                x_diff[i] = this.X_sigma_pred[m][i] - this.x[i];
            }
            x_diff[4] = normalizeAngle(x_diff[4]);

            for (let j = 0; j < M; j++) {
                for (let k = 0; k < M; k++) {
                    S[j][k] += this.Wc[m] * z_diff[j] * z_diff[k];
                }
                for (let i = 0; i < this.L; i++) {
                    Tc[i][j] += this.Wc[m] * x_diff[i] * z_diff[j];
                }
            }
        }

        // Add range noise covariance to diagonal of S
        for (let j = 0; j < M; j++) {
            const noise = measurements[j].r_uwb !== undefined
                ? measurements[j].r_uwb
                : this.r_uwb;
            S[j][j] += Number.isFinite(noise) ? Math.max(1e-9, noise) : this.r_uwb;
        }

        // 3. Invert S
        const invS = invertMatrix(S);
        if (!invS) {
            this.stabilizeCovariance();
            return;
        }

        // 4. Kalman Gain K = Tc * invS (LxM)
        const K = Array.from({ length: this.L }, () => new Float64Array(M));
        for (let i = 0; i < this.L; i++) {
            for (let j = 0; j < M; j++) {
                let sum = 0.0;
                for (let k = 0; k < M; k++) {
                    sum += Tc[i][k] * invS[k][j];
                }
                K[i][j] = sum;
            }
        }

        // 5. Update state x
        const y = new Float64Array(M);
        for (let j = 0; j < M; j++) {
            y[j] = measurements[j].d - z_mean[j];
        }

        const dx = new Float64Array(this.L);
        for (let i = 0; i < this.L; i++) {
            let sum = 0.0;
            for (let j = 0; j < M; j++) {
                sum += K[i][j] * y[j];
            }
            dx[i] = sum;
        }

        // UWB ranges are good position anchors, but poor direct estimators for IMU biases.
        dx[4] = 0.0;
        dx[5] = 0.0;
        dx[6] = 0.0;
        dx[7] = 0.0;

        const posStep = Math.sqrt(dx[0] * dx[0] + dx[1] * dx[1]);
        if (!Number.isFinite(posStep)) {
            this.restoreState(prevX, prevP);
            return;
        }
        if (posStep > this.max_update_position_step) {
            const scale = this.max_update_position_step / posStep;
            dx[0] *= scale;
            dx[1] *= scale;
        }

        const velStep = Math.sqrt(dx[2] * dx[2] + dx[3] * dx[3]);
        if (!Number.isFinite(velStep)) {
            this.restoreState(prevX, prevP);
            return;
        }
        if (velStep > this.max_update_velocity_step) {
            const scale = this.max_update_velocity_step / velStep;
            dx[2] *= scale;
            dx[3] *= scale;
        }

        for (let i = 0; i < this.L; i++) {
            this.x[i] += dx[i];
        }
        this.x[4] = normalizeAngle(this.x[4]);

        // 6. Update covariance P = P - K * S * K^T
        const K_S = Array.from({ length: this.L }, () => new Float64Array(M));
        for (let i = 0; i < this.L; i++) {
            for (let j = 0; j < M; j++) {
                let sum = 0.0;
                for (let k = 0; k < M; k++) {
                    sum += K[i][k] * S[k][j];
                }
                K_S[i][j] = sum;
            }
        }

        const P_sub = Array.from({ length: this.L }, () => new Float64Array(this.L));
        for (let i = 0; i < this.L; i++) {
            for (let j = 0; j < this.L; j++) {
                let sum = 0.0;
                for (let k = 0; k < M; k++) {
                    sum += K_S[i][k] * K[j][k];
                }
                P_sub[i][j] = sum;
            }
        }

        for (let i = 0; i < this.L; i++) {
            for (let j = 0; j < this.L; j++) {
                this.P[i][j] -= P_sub[i][j];
            }
        }

        // Force symmetry
        for (let i = 0; i < this.L; i++) {
            for (let j = 0; j < i; j++) {
                const val = 0.5 * (this.P[i][j] + this.P[j][i]);
                this.P[i][j] = val;
                this.P[j][i] = val;
            }
        }

        this.stabilizeCovariance();
        if (!this.isSaneState()) {
            this.restoreState(prevX, prevP);
            return;
        }
        this.seedSigmaPredictionFromState();
    }

    applyZupt() {
        if (!this.is_initialized) return;

        this.x[2] = Number.isFinite(this.x[2]) ? this.x[2] * 0.1 : 0.0;
        this.x[3] = Number.isFinite(this.x[3]) ? this.x[3] * 0.1 : 0.0;

        const velVar = Math.max(1e-6, this.zupt_velocity_variance);
        for (let i = 0; i < this.L; i++) {
            if (i !== 2) {
                const v = Number.isFinite(this.P[2][i]) ? this.P[2][i] * 0.25 : 0.0;
                this.P[2][i] = v;
                this.P[i][2] = v;
            }
            if (i !== 3) {
                const v = Number.isFinite(this.P[3][i]) ? this.P[3][i] * 0.25 : 0.0;
                this.P[3][i] = v;
                this.P[i][3] = v;
            }
        }
        this.P[2][2] = Math.max(velVar, Math.min(Number.isFinite(this.P[2][2]) ? this.P[2][2] : velVar, velVar * 10.0));
        this.P[3][3] = Math.max(velVar, Math.min(Number.isFinite(this.P[3][3]) ? this.P[3][3] : velVar, velVar * 10.0));
        this.stabilizeCovariance();
        this.seedSigmaPredictionFromState();
    }
}

class MahalanobisPrefilter {
    constructor(config) {
        this.T2_high = config.T2_high;
        this.T2_low = config.T2_low;
        this.is_rejected = [false, false, false, false];
        this.reject_counts = [0, 0, 0, 0];
        this.min_frame_measurements = config.min_frame_measurements !== undefined ? config.min_frame_measurements : 3;
        this.rescue_noise_scale_min = config.rescue_noise_scale_min !== undefined ? config.rescue_noise_scale_min : 4.0;
        this.rescue_noise_max = config.rescue_noise_max !== undefined ? config.rescue_noise_max : 0.25;
        
        // Instantiate the UKF
        this.ukf = new UnscentedKalmanFilter(config);
        
        this.tagHeight = config.tagHeight !== undefined ? config.tagHeight : 0.465;
    }

    init(init_x, init_y, bias_ax, bias_ay, bias_gz) {
        this.ukf.init(init_x, init_y, bias_ax, bias_ay, bias_gz);
        this.is_rejected = [false, false, false, false];
        this.reject_counts = [0, 0, 0, 0];
    }

    predict(imu, dt) {
        this.ukf.predict(imu, dt);
    }

    applyZupt() {
        this.ukf.applyZupt();
    }

    process(i, d, anchor) {
        if (d <= 0.1) {
            return { index: i, d, anchor, pass: false, d2: null, d_pred: null, rescue: false };
        }

        // Query the UKF for predicted range and Mahalanobis d2.
        const res = this.ukf.computeMahalanobis(d, anchor, this.tagHeight);
        const d2 = Number.isFinite(res.d2) ? res.d2 : Infinity;

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
            this.reject_counts[i] = 0;
            return { index: i, d, anchor, pass: true, d2: d2, d_pred: res.d_pred, rescue: false };
        }

        this.reject_counts[i]++;
        return { index: i, d, anchor, pass: false, d2: d2, d_pred: res.d_pred, rescue: false };
    }

    rescueFrame(results, minCount) {
        const targetCount = minCount !== undefined ? minCount : this.min_frame_measurements;
        const acceptedCount = results.filter(r => r.pass).length;
        if (acceptedCount >= targetCount) return [];

        const needed = targetCount - acceptedCount;
        const rescue = results
            .filter(r => !r.pass && r.d2 !== null && Number.isFinite(r.d2) && r.d > 0.1)
            .sort((a, b) => a.d2 - b.d2)
            .slice(0, needed);

        rescue.forEach(r => {
            r.pass = true;
            r.rescue = true;
        });

        return rescue;
    }

    measurementNoiseFor(result) {
        if (!result || !result.rescue || !Number.isFinite(result.d2)) {
            return this.ukf.r_uwb;
        }
        const gate = Math.max(0.001, this.T2_high);
        const residual = Number.isFinite(result.d_pred) ? result.d - result.d_pred : 0.0;
        const residualNoise = Number.isFinite(residual) ? (residual * residual) / gate : 0.0;
        const scaledNoise = this.ukf.r_uwb * Math.max(this.rescue_noise_scale_min, result.d2 / gate);
        const noise = Math.max(this.ukf.r_uwb * this.rescue_noise_scale_min, residualNoise, scaledNoise);
        return Math.min(Math.max(this.ukf.r_uwb, this.rescue_noise_max), noise);
    }

    update(acceptedMeasurements) {
        this.ukf.update(acceptedMeasurements, this.tagHeight);
    }
}
