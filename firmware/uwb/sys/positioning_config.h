/**
 * @file       positioning_config.h
 * @copyright
 * @license
 * @version    1.0.0
 * @date       2025-12-24
 * @author     Phuong Mai
 * @brief      Centralized configuration for positioning system
 * @note       None
 * @example    None
 */
#ifndef __POSITIONING_CONFIG_H
#define __POSITIONING_CONFIG_H

/* ===== ANTENNA DELAY (FACTORY CALIBRATION) ===== */

/**
 * TAG antenna delays - FIXED (manufacturer calibrated)
 */
#define TAG_FACTORY_TX_ANT_DLY      16436
#define TAG_FACTORY_RX_ANT_DLY      16436

/**
 * ANCHOR antenna delays - Default (can be auto-calibrated)
 */
#define ANCHOR_DEFAULT_TX_ANT_DLY   16436
#define ANCHOR_DEFAULT_RX_ANT_DLY   16436

/* ===== ANCHOR AUTO-CALIBRATION ===== */

/**
 * Enable anchor calibration build (0=disabled, 1=enabled)
 */
#define ENABLE_ANCHOR_AUTO_CALIB    0
#if ENABLE_ANCHOR_AUTO_CALIB
#define CALIB_REF_DISTANCE_M      5.6f  /* Physical distance Tag-Anchor (m) */
#define CALIB_SAMPLES             25      /* Number of samples to collect */
#define CALIB_ERROR_THRESHOLD_M   0.02f   // Stop if error < 2cm
#define CALIB_MIN_DELTA_STEP      3       // Stop if step < 3
#define CALIB_MAX_ROUNDS          10      // Max 10 rounds
#define CALIB_MAX_STD_M           0.05f   /* Max allowed std deviation (m) */
#define DW1000_M_PER_DLY_UNIT     0.004691764f  /* DW1000 time unit = ~4.69mm */
#endif

/* ===== PRESETS ===== */

#undef PRESET_WORST_CASE
#define PRESET_BEST_CASE

/* ===== HEIGHT CONFIGURATION (Z-AXIS) ===== */

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

/* ===== ANCHOR LAYOUT (2D COORDINATES) ===== */

#define NUM_ANCHORS  3  /* Total anchors (will use best 3 for trilateration) */

#define ANCHOR_1_X   0.0f
#define ANCHOR_1_Y   0.0f

#define ANCHOR_2_X   9.76f
#define ANCHOR_2_Y   0.0f

#define ANCHOR_3_X   4.88f
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
/* ===== DISTANCE VALIDATION ===== */

#define MAX_VALID_DISTANCE_M    (50.0f)

#define MIN_VALID_DISTANCE_M    (0.05f)

/* ===== FILTER CONFIGURATION ===== */
/**
 * Enable Adaptive Kalman Filter (AKF)
 * 1 = Smooth position with innovation-based adaptive AKF
 * 0 = Use raw trilateration output
 * 
 * Note: AKF automatically adapts measurement noise (R) based on
 *       innovation variance - no manual R tuning or RSSI needed!
 */
#ifndef MW_FILTER_ENABLE_KALMAN_2D
#define MW_FILTER_ENABLE_KALMAN_2D  0
#endif

/**
 * Enable quality gating
 * 1 = Reject trilateration if error_estimate too high
 * 0 = Accept all trilateration results
 */
#ifndef ENABLE_QUALITY_GATING
#define ENABLE_QUALITY_GATING  1
#endif

/* ===== PRESET IMPLEMENTATIONS ===== */

#ifdef PRESET_WORST_CASE
    /* Heavy filtering for noisy environment */
    #undef AKF_PROCESS_NOISE
    #undef AKF_R_BASE
    #undef AKF_INNOVATION_ALPHA
    #undef AKF_R_SCALE_MIN
    #undef AKF_R_SCALE_MAX
    #undef MAX_ACCEPTABLE_ERROR_M
    
    #define AKF_PROCESS_NOISE           0.01f /* Low process noise (stable) */
    #define AKF_R_BASE                  0.5f  /* Base measurement noise */
    #define AKF_INNOVATION_ALPHA        0.2f  /* Smooth adaptation */
    #define AKF_R_SCALE_MIN             0.5f  /* Trust more when stable */
    #define AKF_R_SCALE_MAX             5.0f  /* Increase R when unstable */
    #define MAX_ACCEPTABLE_ERROR_M      2.0f  /* Accept poor quality */
#endif

#ifdef PRESET_BEST_CASE
    /* Light filtering for clean environment */
    #undef AKF_PROCESS_NOISE
    #undef AKF_R_BASE
    #undef AKF_INNOVATION_ALPHA
    #undef AKF_R_SCALE_MIN
    #undef AKF_R_SCALE_MAX
    #undef MAX_ACCEPTABLE_ERROR_M
    
    #define AKF_PROCESS_NOISE           0.003f /* Higher Q for responsive tracking */
    #define AKF_R_BASE                  0.06f  /* Lower base R */
    #define AKF_INNOVATION_ALPHA        0.7f  /* Faster adaptation */
    #define AKF_R_SCALE_MIN             0.2f  /* More aggressive trust */
    #define AKF_R_SCALE_MAX             0.1f  /* Lower max R */
    #define MAX_ACCEPTABLE_ERROR_M      1.0f  /* Strict quality gating */
#endif

/* ===== MANUAL TUNING ===== */

/* ===== Adaptive Kalman Filter (AKF) Parameters ===== */

#ifndef AKF_PROCESS_NOISE
#define AKF_PROCESS_NOISE  0.01f  /* Process noise Q */
#endif

#ifndef AKF_R_BASE
#define AKF_R_BASE  0.3f  /* Base measurement noise */
#endif

#ifndef AKF_INNOVATION_ALPHA
#define AKF_INNOVATION_ALPHA  0.3f  /* EMA for innovation (0.2-0.5) */
#endif

#ifndef AKF_R_SCALE_MIN
#define AKF_R_SCALE_MIN  0.1f  /* Min R multiplier (high confidence) */
#endif

#ifndef AKF_R_SCALE_MAX
#define AKF_R_SCALE_MAX  4.0f  /* Max R multiplier (low confidence) */
#endif

#ifndef MAX_ACCEPTABLE_ERROR_M
#define MAX_ACCEPTABLE_ERROR_M  1.0f
#endif

#define MAX_CONSECUTIVE_ERR       10

#endif //__POSITIONING_CONFIG