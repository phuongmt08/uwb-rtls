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

#define ANCHOR_DEFAULT_TX_ANT_DLY   16342
#define ANCHOR_DEFAULT_RX_ANT_DLY   16342

/* During auto-calibration, only TX delay is adjusted.
 * RX delay is kept fixed at this value for both Tag and Anchor. */
#define CALIB_FIXED_RX_ANT_DLY      16436

/* ===================================================================
 * HEIGHT CONFIGURATION
 * =================================================================== */

/**
 * @brief Tag height from ground (meters)
 */
#define TAG_HEIGHT_M            (0.435f)

/**
 * @brief Anchor height from ground (meters)
 */
#define ANCHOR_HEIGHT_M         (0.405f)

/**
 * @brief Height offset between Anchor and Tag (meters)
 */
#define HEIGHT_OFFSET_M         (ANCHOR_HEIGHT_M - TAG_HEIGHT_M)

/* ===================================================================
 * ANCHOR/TAG AUTO-CALIBRATION
 * =================================================================== */

/* 0 = normal ranging mode
 * 1 = anchor-to-anchor gradient calibration mode (A2A)                      */
#define ENABLE_ANCHOR_AUTO_CALIB    0

/* A2A calibration mode:
 * 0 = mutual calibration: all anchors calibrate against each other
 * 1 = target calibration: only the target anchor is adjusted;
 *     other anchors keep their delay and act as references.
 *
 * CALIB_TARGET_ANCHOR_ID:
 * 0 = external target/initiator, not part of anchor layout or TDMA anchor slots
 * 1..NUM_ANCHORS = fixed target anchor ID. */
#define CALIB_A2A_TARGET_MODE       1U
#define CALIB_TARGET_ANCHOR_ID      4U

/* Physical position of the external calibration target when
 * CALIB_TARGET_ANCHOR_ID == 0. This target ranges as the TAG/initiator side
 * and does not consume any anchor ID or TDMA anchor slot. */
#define CALIB_TARGET_POS_X_M        0.0f
#define CALIB_TARGET_POS_Y_M        0.0f
#define CALIB_TARGET_POS_Z_M        0.0f

/* ------------------------------------------------------------------
 * A2A (Anchor-to-Anchor) Gradient Calibration
 *
 * Algorithm: each anchor ranges with every peer listed in
 * CALIB_PAIRWISE_LIST, collects CALIB_ANCHOR_SAMPLES per pair,
 * then applies a damped gradient step to its combined antenna delay.
 * This repeats for MW_CALIB_A2A_ITERATIONS (see mw_calibration.h).
 *
 * Binary-search constants (ERROR_THRESHOLD, MIN_DELTA_STEP,
 * MAX_ROUNDS) are intentionally removed — gradient needs none of them.
 * Gradient tuning (damping, m→DW factor, iteration count, ANT clamp)
 * lives in mw_calibration.h as MW_CALIB_A2A_* defines.
 * ------------------------------------------------------------------ */

/* Samples collected per anchor pair per iteration.
 * 20 is a good balance: enough to average out multipath,
 * fast enough for in-field calibration.                               */
#define CALIB_ANCHOR_SAMPLES     20

/* Reject a batch if std deviation exceeds this threshold.
 * Batch is discarded and re-collected automatically.                  */
#define CALIB_ANCHOR_MAX_STD_M   0.08f

/* ------------------------------------------------------------------
 * Gradient step tuning — passed into mw_calib_a2a_config_t
 * ------------------------------------------------------------------ */

/* DW1000: TWR combined delay → distance.
 * 1 DW unit in combined (TX+RX) = c × 15.65ps / 2 ≈ 2.345 mm.
 * Inverse: 1 m error → 1/0.002345 ≈ 426 DW units (combined).
 * Each anchor absorbs half → 213 units/m per anchor.                 */
#define CALIB_A2A_M_TO_DW_UNITS  213.0f

/* Damping factor 0.0–1.0.
 * 0.4 converges faster for target-mode calibration with ~1m initial error.
 * Reduce toward 0.2 if the error starts oscillating across zero.      */
#define CALIB_A2A_DAMPING        0.4f

/* Combined delay clamp range.
 * Default DW1000 combined ≈ 32872 (2 × 16436).
 * Allow ±~4% headroom around factory default.                        */
#define CALIB_A2A_ANT_MIN        30000U
#define CALIB_A2A_ANT_MAX        36000U

/* Number of full pair-sweep iterations.
 * 2 is sufficient for <5mm residual error with damping=0.4.          */
#define CALIB_A2A_ITERATIONS     12U

/* DW1000 physical constant.
 * 1 DW unit = 1/(499.2e6 × 128) ≈ 15.65 ps one-way.
 * TWR round-trip: 1 unit in combined (TX+RX) delay
 * → distance error = c × 15.65ps / 2 ≈ 2.345 mm.
 * Inverse used inside mw_calibration.c as MW_CALIB_A2A_M_TO_DW.      */
#define DW1000_M_PER_DLY_UNIT    0.002345f   /* meters per DW unit (TWR) */

/* Anchor-to-anchor pairwise calibration list.
 * Format: {source_anchor_id, target_anchor_id}
 * source_id actively ranges against target_id.
 * Every source accumulates errors from all its pairs, then
 * applies one gradient step — no dependency chain.
 * Anchor 1 has no source entries → it holds the initial delay
 * (natural gauge anchor, no explicit fix needed for 2 iterations).   */
/* ===================================================================
 * ANCHOR LAYOUT
 * =================================================================== */

#define MAX_ANCHORS_SUPPORTED  8
#define NUM_ANCHORS            4

#define ANCHOR_1_X   0.0f
#define ANCHOR_1_Y   0.0f

#define ANCHOR_2_X   9.76f
#define ANCHOR_2_Y   0.0f

#define ANCHOR_3_X   0.0f
#define ANCHOR_3_Y   9.76f

#define ANCHOR_4_X   9.76f
#define ANCHOR_4_Y   9.76f

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
#define ENABLE_MAHALANOBIS_PREFILTER  1
#endif

/**
 * @brief Enable/Disable quality gating based on trilateration error
 *        0 = Accept all trilateration results
 *        1 = Reject results with error > MAX_ACCEPTABLE_ERROR_M
 */
#ifndef ENABLE_QUALITY_GATING
#define ENABLE_QUALITY_GATING       0
#endif

/* Quality gating parameter */
#ifndef MAX_ACCEPTABLE_ERROR_M
#define MAX_ACCEPTABLE_ERROR_M      1.0f    /* Max trilateration error (m) */
#endif

/**
 * @brief Distance Smoother (EMA Filter) parameters
 *        ALPHA: 0.0 to 1.0 (lower = smoother/more lag, higher = jumpier/less lag)
 *        JUMP_LIMIT: Max allowed delta between consecutive samples (meters)
 */
#define SMOOTHER_ALPHA              0.25f
#define SMOOTHER_JUMP_LIMIT_M       0.30f

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
