const SIM_CONFIG = {
    // --- Prefilter Parameters ---
    FILTER: {
        K_VEL: 0.5,             // Velocity uncertainty weight
        DEFAULT_T2_HIGH: 40.0,
        DEFAULT_T2_LOW: 20.0,
        DEFAULT_RESCUE_MIN_ANCHORS: 3,
        DEFAULT_RESCUE_NOISE_MAX: 0.25,
        DEFAULT_UKF_ALPHA: 0.1,
        DEFAULT_UKF_BETA: 2.0,
        DEFAULT_UKF_KAPPA: 0.0,
        DEFAULT_Q_A: 0.25,
        DEFAULT_Q_G: 1.0e-6,
        DEFAULT_R_UWB: 0.05,
        DEFAULT_R_GATE: 0.10,
        TRIPLET_W_D2: 0.35,
        TRIPLET_W_FP: 0.15,
        TRIPLET_W_GDOP: 0.20,
        TRIPLET_W_RESIDUAL: 0.30,
        FP_AMP_GOOD: 40.0,
        FP_AMP_WEIGHT_FLOOR: 0.25,
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
        DEFAULT_LPF_CUTOFF_HZ: 2.0
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
