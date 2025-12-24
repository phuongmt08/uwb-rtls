/* ============================== positioning_config.h =======================
 * @file       positioning_config.h
 * @brief      Centralized configuration for positioning system
 * @version    2.0.0
 * @date       2025-12-21
 */

#ifndef __POSITIONING_CONFIG_H
#define __POSITIONING_CONFIG_H

/* ===== PRESETS ===== */

/**
 * Uncomment ONE preset:
 * 
 * PRESET_TEST_WORST_CASE:
 *   - 1Hz update (1000ms interval)
 *   - Low accuracy, heavy filtering
 *   - For testing other systems under poor positioning
 * 
 * PRESET_HIGH_SPEED_VEHICLE:
 *   - 5-8Hz update (125-200ms interval)
 *   - High accuracy, fast response
 *   - For vehicle moving >20mm/s (0.02m/s)
 */

#undef PRESET_TEST_WORST_CASE
#define PRESET_HIGH_SPEED_VEHICLE

/* ===== ANCHOR LAYOUT (2D only, Z ignored) ===== */

#define NUM_ANCHORS  4  /* Total anchors (will use best 3 for trilateration) */

/* Anchor positions (X, Y in meters - Z ignored) */
#define ANCHOR_1_X   0.0f
#define ANCHOR_1_Y   0.0f

#define ANCHOR_2_X   5.0f
#define ANCHOR_2_Y   0.0f

#define ANCHOR_3_X   5.0f
#define ANCHOR_3_Y   5.0f

#define ANCHOR_4_X   0.0f
#define ANCHOR_4_Y   5.0f

/* ===== FILTER CONFIGURATION ===== */

/**
 * Enable distance EMA filter
 * 1 = Apply EMA smoothing to raw distances
 * 0 = Use raw distances directly
 */
#ifndef ENABLE_DISTANCE_FILTER
#define ENABLE_DISTANCE_FILTER  1
#endif

/**
 * Enable RSSI EMA filter
 * 1 = Apply EMA smoothing to RSSI (required if ENABLE_RSSI_ADAPTIVE=1)
 * 0 = Use raw RSSI
 */
#ifndef ENABLE_RSSI_FILTER
#define ENABLE_RSSI_FILTER  1
#endif

/**
 * Enable Kalman 2D filter
 * 1 = Smooth position with Kalman (HIGHLY RECOMMENDED)
 * 0 = Use raw trilateration output
 */
#ifndef MW_FILTER_ENABLE_KALMAN_2D
#define MW_FILTER_ENABLE_KALMAN_2D  1
#endif

/**
 * Kalman R tuning method
 * 0 = Fixed R (manual tuning)
 * 1 = Adaptive R from RSSI (auto adjust based on signal quality)
 */
#ifndef ENABLE_RSSI_ADAPTIVE
#define ENABLE_RSSI_ADAPTIVE  0
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

#ifdef PRESET_TEST_WORST_CASE
    /* 1Hz update, low accuracy, heavy filtering */
    #undef RANGING_INTERVAL_MS
    #undef DISTANCE_EMA_ALPHA
    #undef RSSI_EMA_ALPHA
    #undef KALMAN_PROCESS_NOISE
    #undef KALMAN_MEASURE_NOISE
    #undef MAX_ACCEPTABLE_ERROR_M
    #undef ENABLE_RSSI_ADAPTIVE
    
    #define RANGING_INTERVAL_MS     1000  /* 1Hz */
    #define DISTANCE_EMA_ALPHA      0.2f  /* Heavy smoothing */
    #define RSSI_EMA_ALPHA          0.15f /* Very smooth */
    #define KALMAN_PROCESS_NOISE    0.05f /* Slow changes */
    #define KALMAN_MEASURE_NOISE    5.0f  /* Low trust in measurements */
    #define MAX_ACCEPTABLE_ERROR_M  2.0f  /* Accept poor quality */
    #define ENABLE_RSSI_ADAPTIVE    0     /* Fixed R */
#endif

#ifdef PRESET_HIGH_SPEED_VEHICLE
    /* 5-8Hz update, high accuracy, fast tracking for >20mm/s vehicle */
    #undef RANGING_INTERVAL_MS
    #undef DISTANCE_EMA_ALPHA
    #undef RSSI_EMA_ALPHA
    #undef KALMAN_PROCESS_NOISE
    #undef KALMAN_MEASURE_NOISE
    #undef MAX_ACCEPTABLE_ERROR_M
    #undef ENABLE_RSSI_ADAPTIVE
    
    #define RANGING_INTERVAL_MS     125   /* 8Hz (can adjust 125-200ms for 5-8Hz) */
    #define DISTANCE_EMA_ALPHA      0.5f  /* Fast response */
    #define RSSI_EMA_ALPHA          0.3f  /* Moderate smoothing */
    #define KALMAN_PROCESS_NOISE    0.8f  /* Allow fast changes (vehicle speed) */
    #define KALMAN_MEASURE_NOISE    0.8f  /* Trust measurements */
    #define MAX_ACCEPTABLE_ERROR_M  0.8f  /* Strict quality */
    #define ENABLE_RSSI_ADAPTIVE    1     /* Adaptive R for robustness */
#endif

/* ===== MANUAL TUNING (if no preset selected) ===== */

#ifndef RANGING_INTERVAL_MS
#define RANGING_INTERVAL_MS  125  /* 8Hz default */
#endif

#ifndef DISTANCE_EMA_ALPHA
#define DISTANCE_EMA_ALPHA  0.4f
#endif

#ifndef RSSI_EMA_ALPHA
#define RSSI_EMA_ALPHA  0.3f
#endif

#ifndef KALMAN_PROCESS_NOISE
#define KALMAN_PROCESS_NOISE  0.5f
#endif

#ifndef KALMAN_MEASURE_NOISE
#define KALMAN_MEASURE_NOISE  1.0f
#endif

#ifndef MAX_ACCEPTABLE_ERROR_M
#define MAX_ACCEPTABLE_ERROR_M  1.0f
#endif

/* ===== RSSI ADAPTIVE THRESHOLDS ===== */

/**
 * RSSI-to-R_scale mapping (if ENABLE_RSSI_ADAPTIVE = 1)
 */
#define RSSI_THRESHOLD_EXCELLENT  -40   /* dBm */
#define RSSI_THRESHOLD_GOOD       -60   /* dBm */
#define RSSI_THRESHOLD_MODERATE   -80   /* dBm */
#define RSSI_THRESHOLD_POOR       -100  /* dBm */

#define MAX_CONSECUTIVE_ERR  10

#if NUM_ANCHORS < 3
    #error "Need at least 3 anchors for 2D positioning"
#endif

#if RANGING_INTERVAL_MS < 20
    #error "Ranging too fast, UWB can't keep up"
#endif

#if ENABLE_RSSI_ADAPTIVE && !ENABLE_RSSI_FILTER
    #warning "ENABLE_RSSI_ADAPTIVE requires ENABLE_RSSI_FILTER, enabling RSSI filter"
    #undef ENABLE_RSSI_FILTER
    #define ENABLE_RSSI_FILTER 1
#endif

#endif /* __POSITIONING_CONFIG_H */