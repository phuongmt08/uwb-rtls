const SIM_CONFIG = {
    // --- Prefilter Parameters ---
    FILTER: {
        K_VEL: 0.5,             // Velocity uncertainty weight
        DEFAULT_T2_HIGH: 7.5,
        DEFAULT_T2_LOW: 5.0,
        DEFAULT_RESCUE_MIN_ANCHORS: 3,
        DEFAULT_RESCUE_MIN_REJECT_STREAK: 5,
        DEFAULT_RESCUE_NOISE_SCALE_MIN: 4.0,
        DEFAULT_RESCUE_NOISE_MAX: 0.25,
        DEFAULT_UKF_ALPHA: 0.1,
        DEFAULT_UKF_BETA: 2.0,
        DEFAULT_UKF_KAPPA: 0.0,
        DEFAULT_Q_A: 0.25,
        DEFAULT_Q_G: 1.0e-6,
        DEFAULT_R_UWB: 0.05,
        DEFAULT_R_GATE: 0.10,
        MIN_GATE_COVARIANCE: 1.0e-6,
        RANGE_SIGMA_BASE_M: 0.10,
        RANGE_SIGMA_SLOPE: 0.015,
        RANGE_SIGMA_MAX_M: 0.35,
        HUBER_FP_DEFICIT_DELTA: 0.35,
        HUBER_RESIDUAL_DELTA: 1.50,
        HUBER_WEIGHT_FLOOR: 0.10,
        WGDOP_DET_MIN: 1.0e-8,
        REFERENCE_MAX_STD_M: 0.50,
        ADAPTIVE_R_MIN: 0.0025,
        ADAPTIVE_R_MAX: 0.25,
        DEFAULT_YAW_MAP_OFFSET_DEG: 0.0,
        TRIPLET_W_D2: 0.35,
        TRIPLET_W_FP: 0.15,
        TRIPLET_W_RESIDUAL: 0.30,
        TRIPLET_W_DIST: 0.25,
        TRIPLET_W_HEALTH: 0.25,
        TRIPLET_SWITCH_MARGIN: 0.10,
        TRIPLET_SWITCH_SCORE_EPS: 0.02,
        ANCHOR_HEALTH_ALPHA: 0.18,
        FP_AMP_GOOD: 40.0,
        FP_AMP_WEIGHT_FLOOR: 0.25,
        // Old logs never stored fp_confidence/quality_valid. When true, derive
        // a proxy confidence = clamp(fp_amp / FP_AMP_GOOD) so the FP branch
        // still exercises on replay. Biased vs live firmware (which uses the
        // FP-index vs peak-index confidence) — do NOT tune FP params on it.
        FP_CONFIDENCE_PROXY_FROM_AMP: true,
        RESCUE_SORT_WEIGHT: 0.35,
        POSITION_BOUND_MARGIN: 3.0,
        MAX_UWB_POS_CORRECTION: 1.0,
        MAX_UWB_VEL_CORRECTION: 1.0
    },

    // --- Smoothing Parameters ---
    SMOOTHER: {
        ALPHA: 0.25,
        JUMP_LIMIT: 0.30        // meters
    },

    // --- IMU / Dead Reckoning ---
    IMU: {
        VELOCITY_DECAY: 0.98,   // Velocity damping factor
        DEFAULT_ENABLE_ZUPT_UKF: false,
        ZUPT_COUNT_THRESHOLD: 10,
        DEFAULT_ZUPT_ACC: 0.15,
        DEFAULT_ZUPT_GYR: 0.05,
        DEFAULT_ENABLE_LPF: true,
        DEFAULT_LPF_CUTOFF_HZ: 2.0,
        DEFAULT_FILTER_ORDER: 2,
        MIN_FILTER_ORDER: 1,
        MAX_FILTER_ORDER: 6,
        CUTOFF_NYQUIST_MARGIN: 0.95
    },

    // --- Visualization ---
    VIEW: {
        COLORS: ['#2563eb', '#16a34a', '#d97706', '#7c3aed'],
        MAX_ERROR_RANGE: 1.5    // meters for plot Y-axis
    },

    // --- Environment / Setup ---
    ENV: {
        TAG_HEIGHT: 0.465,
        LOG_DISTANCES_ARE_PLANAR: true,
        ANCHORS: [
            { id: 1, x: 0.0,  y: 0.0,  z: 0.895 },
            { id: 2, x: 9.76, y: 0.0,  z: 0.895 },
            { id: 3, x: 0.0,  y: 9.76, z: 0.895 },
            { id: 4, x: 9.76, y: 9.76, z: 0.895 }
        ],
        GT_SQUARE: {
            x: [2.44, 7.32, 7.32, 2.44, 2.44],
            y: [2.44, 2.44, 7.32, 7.32, 2.44]
        }
    }
};
