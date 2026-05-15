const SIM_CONFIG = {
    // --- Prefilter Parameters ---
    FILTER: {
        K_VEL: 0.5,             // Velocity uncertainty weight
        DEFAULT_T2_HIGH: 9.0,
        DEFAULT_T2_LOW: 4.0,
        DEFAULT_R_BASE: 0.05,
        DEFAULT_WIN: 15
    },

    // --- Smoothing Parameters ---
    SMOOTHER: {
        ALPHA: 0.25,
        JUMP_LIMIT: 0.30        // meters
    },

    // --- IMU / Dead Reckoning ---
    IMU: {
        VELOCITY_DECAY: 0.98,   // Velocity damping factor
        ZUPT_COUNT_THRESHOLD: 10,
        DEFAULT_ZUPT_ACC: 0.15,
        DEFAULT_ZUPT_GYR: 0.05
    },

    // --- Visualization ---
    VIEW: {
        COLORS: ['#2563eb', '#16a34a', '#d97706', '#7c3aed'],
        MAX_ERROR_RANGE: 1.5    // meters for plot Y-axis
    },

    // --- Environment / Setup ---
    ENV: {
        TAG_HEIGHT: 0.435,
        ANCHORS: [
            { id: 1, x: 0.0,  y: 0.0,  z: 0.405 },
            { id: 2, x: 9.76, y: 0.0,  z: 0.405 },
            { id: 3, x: 0.0,  y: 9.76, z: 0.405 },
            { id: 4, x: 9.76, y: 9.76, z: 0.405 }
        ],
        GT_SQUARE: {
            x: [2.44, 7.32, 7.32, 2.44, 2.44],
            y: [2.44, 2.44, 7.32, 7.32, 2.44]
        }
    }
};
