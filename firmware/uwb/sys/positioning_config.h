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

/* Enable/Disable: Force antenna delay to default values on startup
 * 0 = Use calibrated values (if available)
 * 1 = Force antenna delay to factory defaults
 */
#define ENABLE_FORCE_DEFAULT_ANT_DLY    0

#define TAG_FACTORY_TX_ANT_DLY      16436
#define TAG_FACTORY_RX_ANT_DLY      16436

#define ANCHOR_DEFAULT_TX_ANT_DLY   16611
#define ANCHOR_DEFAULT_RX_ANT_DLY   16436

/* ===================================================================
 * HEIGHT CONFIGURATION
 * =================================================================== */

/**
 * @brief Tag height from ground (meters)
 */
#define TAG_HEIGHT_M            (0.24f)

/**
 * @brief Anchor height from ground (meters)
 */
#define ANCHOR_HEIGHT_M         (0.415f)

/**
 * @brief Height offset between Anchor and Tag (meters)
 */
#define HEIGHT_OFFSET_M         (ANCHOR_HEIGHT_M - TAG_HEIGHT_M)

/* ===================================================================
 * ANCHOR/TAG AUTO-CALIBRATION
 * =================================================================== */

#define ENABLE_ANCHOR_AUTO_CALIB    0
#define ENABLE_TAG_AUTO_CALIB       0

#define CALIB_REF_DISTANCE_XY_M   7.32f   /* Horizontal distance Tag-Anchor (m) */
#define CALIB_TAG_HEIGHT_M        TAG_HEIGHT_M
#define CALIB_ANCHOR_HEIGHT_M     ANCHOR_HEIGHT_M
#define CALIB_ANCHOR_ID           1       /* Anchor used for tag calibration */

#define CALIB_SAMPLES             30      /* Number of samples to collect */
#define CALIB_ERROR_THRESHOLD_M   0.02f   /* Stop if error < 2cm */
#define CALIB_MIN_DELTA_STEP      3       /* Stop if step < 3 */
#define CALIB_MAX_ROUNDS          12      /* Max 10 rounds */
#define CALIB_MAX_STD_M           0.05f   /* Max allowed std deviation (m) */
#define DW1000_M_PER_DLY_UNIT     0.004691764f  /* DW1000 time unit = ~4.69mm */

/* ===================================================================
 * ANCHOR LAYOUT
 * =================================================================== */

#define NUM_ANCHORS  4

#define ANCHOR_1_X   0.0f
#define ANCHOR_1_Y   0.0f

#define ANCHOR_2_X   9.76f
#define ANCHOR_2_Y   0.0f

#define ANCHOR_3_X   0.0f
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

/**
 * @brief Enable/Disable Mahalanobis pre-filter on raw 3D distances.
 *        0 = bypass Mahalanobis gate
 *        1 = apply Mahalanobis gate
 */
#ifndef ENABLE_MAHALANOBIS_PREFILTER
#define ENABLE_MAHALANOBIS_PREFILTER  0
#endif

/**
 * @brief Enable/Disable quality gating based on trilateration error
 *        0 = Accept all trilateration results
 *        1 = Reject results with error > MAX_ACCEPTABLE_ERROR_M
 */
#ifndef ENABLE_QUALITY_GATING
#define ENABLE_QUALITY_GATING       1
#endif

/* Quality gating parameter */
#ifndef MAX_ACCEPTABLE_ERROR_M
#define MAX_ACCEPTABLE_ERROR_M      1.0f    /* Max trilateration error (m) */
#endif

/* ===================================================================
 * ERROR HANDLING
 * =================================================================== */

#define MAX_CONSECUTIVE_ERR  10

/* ===================================================================
 * PARAMETER DESCRIPTIONS AND TUNING GUIDE
 * =================================================================== */

/*
 * TUNING WORKFLOW:
 * ----------------
 * - Tuning details for filters to be updated upon adding UKF/IMU integration.
 */

#endif /* __POSITIONING_CONFIG_H */
