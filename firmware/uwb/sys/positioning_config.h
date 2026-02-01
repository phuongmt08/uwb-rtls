/**
 * @file       positioning_config.h
 * @version    2.0.0
 * @date       2026-01-29
 * @author     Phuong Mai
 * @brief      Centralized configuration for positioning system
 *             All filter parameters and thresholds controlled from here
 */
#ifndef __POSITIONING_CONFIG_H
#define __POSITIONING_CONFIG_H

/* ===================================================================
 * ANTENNA DELAY CONFIGURATION
 * =================================================================== */

#define TAG_FACTORY_TX_ANT_DLY      16436
#define TAG_FACTORY_RX_ANT_DLY      16436

#define ANCHOR_DEFAULT_TX_ANT_DLY   16436
#define ANCHOR_DEFAULT_RX_ANT_DLY   16436

/* ===================================================================
 * ANCHOR AUTO-CALIBRATION
 * =================================================================== */

#define ENABLE_ANCHOR_AUTO_CALIB    0

#if ENABLE_ANCHOR_AUTO_CALIB
#define CALIB_REF_DISTANCE_M      7.32f    /* Physical distance Tag-Anchor (m) */
#define CALIB_SAMPLES             30      /* Number of samples to collect */
#define CALIB_ERROR_THRESHOLD_M   0.02f   /* Stop if error < 2cm */
#define CALIB_MIN_DELTA_STEP      3       /* Stop if step < 3 */
#define CALIB_MAX_ROUNDS          12      /* Max 10 rounds */
#define CALIB_MAX_STD_M           0.05f   /* Max allowed std deviation (m) */
#define DW1000_M_PER_DLY_UNIT     0.004691764f  /* DW1000 time unit = ~4.69mm */
#endif

/* ===================================================================
 * HEIGHT CONFIGURATION
 * =================================================================== */

/**
 * @brief Tag height from ground (meters)
 */
#define TAG_HEIGHT_M            (0.148f)

/**
 * @brief Anchor height from ground (meters)
 */
#define ANCHOR_HEIGHT_M         (0.415f)

/**
 * @brief Height offset between Anchor and Tag (meters)
 */
#define HEIGHT_OFFSET_M         (ANCHOR_HEIGHT_M - TAG_HEIGHT_M)

/* ===================================================================
 * ANCHOR LAYOUT
 * =================================================================== */

#define NUM_ANCHORS  4

#define ANCHOR_1_X   -1.0f
#define ANCHOR_1_Y   0.0f

#define ANCHOR_2_X   9.76f
#define ANCHOR_2_Y   0.0f

#define ANCHOR_3_X   -1.0f
#define ANCHOR_3_Y   14.64f

#define ANCHOR_4_X   9.76f
#define ANCHOR_4_Y   14.64f

#ifndef ANCHOR_1_Z
#define ANCHOR_1_Z ANCHOR_HEIGHT_M
#endif
#ifndef ANCHOR_2_Z
#define ANCHOR_2_Z ANCHOR_HEIGHT_M
#endif
#ifndef ANCHOR_3_Z
#define ANCHOR_3_Z ANCHOR_HEIGHT_M
#endif
#ifndef ANCHOR_4_Z
#define ANCHOR_4_Z ANCHOR_HEIGHT_M
#endif

/* ===================================================================
 * DISTANCE VALIDATION
 * =================================================================== */

#define MAX_VALID_DISTANCE_M    (50.0f)
#define MIN_VALID_DISTANCE_M    (0.05f)

/* ===================================================================
 * FILTER ENABLE/DISABLE CONTROL
 * =================================================================== */


#ifndef MW_FILTER_ENABLE_DES
#define MW_FILTER_ENABLE_DES        0
#endif

#ifndef MW_FILTER_ENABLE_AKF
#define MW_FILTER_ENABLE_AKF        1
#endif

/**
 * @brief Legacy macro for backward compatibility
 *        Maps to AKF enable/disable
 */
#ifndef MW_FILTER_ENABLE_KALMAN_2D
#define MW_FILTER_ENABLE_KALMAN_2D  MW_FILTER_ENABLE_AKF
#endif

/**
 * @brief Enable/Disable quality gating based on trilateration error
 *        0 = Accept all trilateration results
 *        1 = Reject results with error > MAX_ACCEPTABLE_ERROR_M
 */
#ifndef ENABLE_QUALITY_GATING
#define ENABLE_QUALITY_GATING       1
#endif

/* ===================================================================
 * FILTER PRESETS
 * =================================================================== */

/* Uncomment ONE preset or use MANUAL tuning below */

#undef PRESET_WORST_CASE
#define PRESET_BEST_CASE
/* #define PRESET_MANUAL */

/* ===================================================================
 * DES (DOUBLE EXPONENTIAL SMOOTHING) PARAMETERS
 * =================================================================== */

#ifdef PRESET_WORST_CASE
    /* Heavy smoothing for noisy environments */
    #define DES_ALPHA_BASE              0.15f   /* Base smoothing factor (0-1) */
    #define DES_BETA                    0.10f   /* Trend smoothing factor (0-1) */
    #define DES_MOTION_THRESHOLD        0.05f    /* Motion detection threshold (m) */
    #define DES_CHANGE_ALPHA            0.3f    /* Change EMA smoothing (0-1) */
    #define DES_MOTION_SCALE_HIGH       1.5f    /* Alpha multiplier for high motion */
    #define DES_MOTION_SCALE_LOW        0.7f    /* Alpha multiplier for low motion */
    #define DES_ALPHA_MIN               0.1f    /* Minimum alpha value */
    #define DES_ALPHA_MAX               0.7f    /* Maximum alpha value */

#elif defined(PRESET_BEST_CASE)
    /* Light smoothing for clean environments */
    #define DES_ALPHA_BASE              0.35f   /* Base smoothing factor (0-1) */
    #define DES_BETA                    0.15f   /* Trend smoothing factor (0-1) */
    #define DES_MOTION_THRESHOLD        0.05f    /* Motion detection threshold (m) */
    #define DES_CHANGE_ALPHA            0.3f    /* Change EMA smoothing (0-1) */
    #define DES_MOTION_SCALE_HIGH       1.5f    /* Alpha multiplier for high motion */
    #define DES_MOTION_SCALE_LOW        0.7f    /* Alpha multiplier for low motion */
    #define DES_ALPHA_MIN               0.1f    /* Minimum alpha value */
    #define DES_ALPHA_MAX               0.7f    /* Maximum alpha value */

#else
    /* Manual tuning - adjust these values as needed */
    #ifndef DES_ALPHA_BASE
    #define DES_ALPHA_BASE              0.30f   /* Base smoothing factor (0-1) */
    #endif
    #ifndef DES_BETA
    #define DES_BETA                    0.15f   /* Trend smoothing factor (0-1) */
    #endif
    #ifndef DES_MOTION_THRESHOLD
    #define DES_MOTION_THRESHOLD        0.05f    /* Motion detection threshold (m) */
    #endif
    #ifndef DES_CHANGE_ALPHA
    #define DES_CHANGE_ALPHA            0.3f    /* Change EMA smoothing (0-1) */
    #endif
    #ifndef DES_MOTION_SCALE_HIGH
    #define DES_MOTION_SCALE_HIGH       1.5f    /* Alpha multiplier for high motion */
    #endif
    #ifndef DES_MOTION_SCALE_LOW
    #define DES_MOTION_SCALE_LOW        0.7f    /* Alpha multiplier for low motion */
    #endif
    #ifndef DES_ALPHA_MIN
    #define DES_ALPHA_MIN               0.1f    /* Minimum alpha value */
    #endif
    #ifndef DES_ALPHA_MAX
    #define DES_ALPHA_MAX               0.7f    /* Maximum alpha value */
    #endif
#endif

/* ===================================================================
 * AKF (ADAPTIVE KALMAN FILTER) PARAMETERS
 * =================================================================== */

#ifdef PRESET_WORST_CASE
    /* Conservative filtering for high noise */
    #define AKF_PROCESS_NOISE           0.01f   /* Process noise Q (acceleration variance) */
    #define AKF_R_BASE                  0.5f    /* Base measurement noise R */
    #define AKF_INNOVATION_ALPHA        0.2f    /* Innovation variance EMA (0-1) */
    #define AKF_R_SCALE_MIN             0.5f    /* Min R scale (trust measurements more) */
    #define AKF_R_SCALE_MAX             5.0f    /* Max R scale (trust model more) */
    
    /* Motion detection thresholds */
    #define AKF_STOP_THRESHOLD          0.1f    /* Velocity threshold for "stopped" (m/s) */
    #define AKF_STOP_VELOCITY_DAMPING   0.5f    /* Velocity damping factor when stopped */
    #define AKF_STOP_GAIN_REDUCTION     0.3f    /* Kalman gain reduction when stopped */
    
    /* Process noise scaling */
    #define AKF_Q_SCALE_STOPPED         0.3f    /* Q scale when stopped */
    #define AKF_Q_SCALE_VELOCITY_K      0.1f    /* Q velocity coefficient */
    
    /* Quality gating */
    #define MAX_ACCEPTABLE_ERROR_M      2.0f    /* Max trilateration error (m) */

#elif defined(PRESET_BEST_CASE)
    /* Aggressive filtering for low noise */
    #define AKF_PROCESS_NOISE           0.05f  /* Process noise Q (acceleration variance) */
    #define AKF_R_BASE                  0.08f   /* Base measurement noise R */
    #define AKF_INNOVATION_ALPHA        0.6f    /* Innovation variance EMA (0-1) */
    #define AKF_R_SCALE_MIN             0.05f    /* Min R scale (trust measurements more) */
    #define AKF_R_SCALE_MAX             2.0f    /* Max R scale (trust model more) */
    
    /* Motion detection thresholds */
    #define AKF_STOP_THRESHOLD          0.10f    /* Velocity threshold for "stopped" (m/s) */
    #define AKF_STOP_VELOCITY_DAMPING   0.2f    /* Velocity damping factor when stopped */
    #define AKF_STOP_GAIN_REDUCTION     0.2f    /* Kalman gain reduction when stopped */
    
    /* Process noise scaling */
    #define AKF_Q_SCALE_STOPPED         0.15f    /* Q scale when stopped */
    #define AKF_Q_SCALE_VELOCITY_K      0.1f    /* Q velocity coefficient */
    
    /* Quality gating */
    #define MAX_ACCEPTABLE_ERROR_M      1.0f    /* Max trilateration error (m) */

#else
    /* Manual tuning - adjust these values as needed */
    
    #ifndef AKF_PROCESS_NOISE
    #define AKF_PROCESS_NOISE           0.003f  /* Process noise Q (acceleration variance) */
    #endif
    
    #ifndef AKF_R_BASE
    #define AKF_R_BASE                  0.06f   /* Base measurement noise R */
    #endif
    
    #ifndef AKF_INNOVATION_ALPHA
    #define AKF_INNOVATION_ALPHA        0.7f    /* Innovation variance EMA (0-1) */
    #endif
    
    #ifndef AKF_R_SCALE_MIN
    #define AKF_R_SCALE_MIN             0.05f    /* Min R scale (trust measurements more) */
    #endif
    
    #ifndef AKF_R_SCALE_MAX
    #define AKF_R_SCALE_MAX             2.0f    /* Max R scale (trust model more) */
    #endif
    
    /* Motion detection thresholds */
    #ifndef AKF_STOP_THRESHOLD
    #define AKF_STOP_THRESHOLD          0.1f    /* Velocity threshold for "stopped" (m/s) */
    #endif
    
    #ifndef AKF_STOP_VELOCITY_DAMPING
    #define AKF_STOP_VELOCITY_DAMPING   0.5f    /* Velocity damping factor when stopped */
    #endif
    
    #ifndef AKF_STOP_GAIN_REDUCTION
    #define AKF_STOP_GAIN_REDUCTION     0.3f    /* Kalman gain reduction when stopped */
    #endif
    
    /* Process noise scaling */
    #ifndef AKF_Q_SCALE_STOPPED
    #define AKF_Q_SCALE_STOPPED         0.3f    /* Q scale when stopped */
    #endif
    
    #ifndef AKF_Q_SCALE_VELOCITY_K
    #define AKF_Q_SCALE_VELOCITY_K      0.1f    /* Q velocity coefficient */
    #endif
    
    /* Quality gating */
    #ifndef MAX_ACCEPTABLE_ERROR_M
    #define MAX_ACCEPTABLE_ERROR_M      1.0f    /* Max trilateration error (m) */
    #endif
#endif

/* ===================================================================
 * ERROR HANDLING
 * =================================================================== */

#define MAX_CONSECUTIVE_ERR  10

/* ===================================================================
 * PARAMETER DESCRIPTIONS AND TUNING GUIDE
 * =================================================================== */

/*
 * DES PARAMETERS GUIDE:
 * ---------------------
 * DES_ALPHA_BASE: Controls smoothing vs responsiveness
 *   - Lower (0.1-0.2): Heavier smoothing, slower response to changes
 *   - Higher (0.3-0.5): Less smoothing, faster response
 *   - Typical range: 0.15 - 0.35
 *
 * DES_BETA: Controls trend tracking
 *   - Lower (0.05-0.1): Slower trend adaptation
 *   - Higher (0.15-0.25): Faster trend adaptation
 *   - Typical range: 0.1 - 0.2
 *
 * DES_MOTION_THRESHOLD: Velocity threshold for motion detection
 *   - Lower: More sensitive to motion (switches to high-motion mode earlier)
 *   - Higher: Less sensitive (stays in smooth mode longer)
 *   - Typical range: 0.05 - 0.2 m
 *
 * AKF PARAMETERS GUIDE:
 * ---------------------
 * AKF_PROCESS_NOISE (Q): Expected acceleration variance
 *   - Lower (0.001-0.005): Assumes smooth motion, slower adaptation
 *   - Higher (0.01-0.05): Assumes erratic motion, faster adaptation
 *   - Typical range: 0.003 - 0.01
 *
 * AKF_R_BASE: Base measurement noise covariance
 *   - Lower (0.03-0.08): Trusts measurements more (faster tracking)
 *   - Higher (0.3-0.8): Trusts model more (heavier smoothing)
 *   - Typical range: 0.05 - 0.5
 *
 * AKF_INNOVATION_ALPHA: How fast to adapt to changing measurement quality
 *   - Lower (0.1-0.3): Slower adaptation (stable but slow to react)
 *   - Higher (0.5-0.8): Faster adaptation (reactive but may oscillate)
 *   - Typical range: 0.3 - 0.7
 *
 * AKF_R_SCALE_MIN/MAX: Measurement trust adjustment range
 *   - Smaller range (0.5-2.0): Conservative adaptation
 *   - Larger range (0.2-5.0): Aggressive adaptation
 *   - MIN controls max trust in measurements
 *   - MAX controls max trust in model
 *
 * AKF_STOP_THRESHOLD: Velocity below which tag is considered stopped
 *   - Lower (0.05): More sensitive, enters stop mode earlier
 *   - Higher (0.2): Less sensitive, stays in motion mode longer
 *   - Typical range: 0.08 - 0.15 m/s
 *
 * TUNING WORKFLOW:
 * ----------------
 * 1. Start with a preset (BEST_CASE or WORST_CASE)
 * 2. Test with stationary tag - should have minimal jitter
 * 3. Test with moving tag - should track smoothly without lag
 * 4. Adjust DES_ALPHA_BASE if too smooth/laggy or too noisy
 * 5. Adjust AKF_R_BASE for overall smoothness vs responsiveness
 * 6. Adjust AKF_INNOVATION_ALPHA for measurement quality adaptation speed
 * 7. Fine-tune stop detection with AKF_STOP_THRESHOLD
 */

#endif /* __POSITIONING_CONFIG_H */
