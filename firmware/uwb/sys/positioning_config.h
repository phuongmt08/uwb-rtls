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

#define ANCHOR_DEFAULT_TX_ANT_DLY   16187
#define ANCHOR_DEFAULT_RX_ANT_DLY   16187

/* Legacy fixed RX delay used by older calibration paths. V1 A2A summary
 * solver only logs candidate delays and does not apply this value. */
#define CALIB_FIXED_RX_ANT_DLY      16436

/* ===================================================================
 * HEIGHT CONFIGURATION
 * =================================================================== */

/**
 * @brief Tag height from ground (meters)
 */
#define TAG_HEIGHT_M            (0.45f)

/**
 * @brief Anchor height from ground (meters)
 */
#define ANCHOR_HEIGHT_M         (0.895f)

/**
 * @brief Height offset between Anchor and Tag (meters)
 */
#define HEIGHT_OFFSET_M         (ANCHOR_HEIGHT_M - TAG_HEIGHT_M)

/* ===================================================================
 * ANCHOR/TAG AUTO-CALIBRATION
 * =================================================================== */

/* 0 = normal ranging mode
 * 1 = mutual anchor-to-anchor calibration summary mode (A2A V1)             */
#define ENABLE_ANCHOR_AUTO_CALIB    0

/* ------------------------------------------------------------------
 * A2A (Anchor-to-Anchor) Mutual Calibration V1
 *
 * Mutual-only V1: each anchor ranges with every other anchor, collects
 * CALIB_ANCHOR_SAMPLES per pair, and sends CALIB_PAIR_SUMMARY to A4.
 * A4 runs the full-matrix solver and logs candidate delays only.
 * ------------------------------------------------------------------ */

/* Samples collected per anchor pair per calibration epoch.
 * 20 is a good balance: enough to average out multipath,
 * fast enough for in-field calibration.                               */
#define CALIB_ANCHOR_SAMPLES     20

/* Reject a batch if std deviation exceeds this threshold.
 * Batch is discarded and re-collected automatically.                  */
#define CALIB_ANCHOR_MAX_STD_M   0.08f

/* DW1000: TWR combined delay -> distance.
 * 1 DW unit in combined (TX+RX) = c × 15.65ps / 2 ≈ 2.345 mm.
 * V1 uses this only to log candidate delay deltas.                    */
#define CALIB_A2A_M_TO_DW_UNITS  213.0f

/* Legacy gradient damping kept for compatibility with mw_calibration. */
#define CALIB_A2A_DAMPING        0.4f

/* Reject a calibration batch when per-pair residuals disagree by more
 * than this. A single antenna-delay scalar cannot fix link/angle bias. */
#define CALIB_A2A_MAX_PAIR_ERROR_SPREAD_M  0.20f

/* Reject a pair when too many TDMA rounds missed that peer while collecting.
 * timeout_rate = timeout_count / (valid_count + timeout_count). */
#define CALIB_A2A_MAX_TIMEOUT_RATE         0.25f

/* Final success gate. A calibration run is successful only when every
 * usable pair's absolute residual is below this value. */
#define CALIB_A2A_CONVERGENCE_MAX_ABS_M    0.05f

/* Combined delay clamp range.
 * Default DW1000 combined ≈ 32872 (2 × 16436).
 * Allow ±~4% headroom around factory default.                        */
#define CALIB_A2A_ANT_MIN        30000U
#define CALIB_A2A_ANT_MAX        36000U

/* Legacy gradient iteration count kept for compatibility. V1 summary
 * calibration completes after one mutual collection epoch.            */
#define CALIB_A2A_ITERATIONS     3U

/* DW1000 physical constant.
 * 1 DW unit = 1/(499.2e6 × 128) ≈ 15.65 ps one-way.
 * TWR round-trip: 1 unit in combined (TX+RX) delay
 * → distance error = c × 15.65ps / 2 ≈ 2.345 mm.
 * Inverse is used when converting solved bias to logged candidate
 * delay deltas.                                                       */
#define DW1000_M_PER_DLY_UNIT    0.002345f   /* meters per DW unit (TWR) */

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
 * @brief Enable/Disable the mw_filter Mahalanobis pre-filter.
 *        0 = bypass Mahalanobis gate
 *        1 = apply Mahalanobis gate before anchor selection
 */
#ifndef ENABLE_MAHALANOBIS_PREFILTER
#define ENABLE_MAHALANOBIS_PREFILTER  1
#endif

/**
 * @brief Mahalanobis pre-filter parameters.
 *
 * The firmware prefilter state lives in mw_filter. It keeps a clean per-anchor
 * rejected/accepted state. d2 is computed from the current sensor-fusion
 * predicted range to the anchor; T2_REJECT enters rejected state, and
 * T2_RECOVER exits that state. Rescue is a separate frame-level policy
 * controlled by RESCUE_MIN_ANCHORS.
 */
#ifndef MAHALANOBIS_PREFILTER_D2_RECOVER
#define MAHALANOBIS_PREFILTER_D2_RECOVER           5.0f
#endif

#ifndef MAHALANOBIS_PREFILTER_D2_REJECT
#define MAHALANOBIS_PREFILTER_D2_REJECT            7.5f
#endif

#ifndef MAHALANOBIS_PREFILTER_R_BASE
#define MAHALANOBIS_PREFILTER_R_BASE               0.05f
#endif

#ifndef MAHALANOBIS_PREFILTER_R_GATE
#define MAHALANOBIS_PREFILTER_R_GATE               0.10f
#endif

#ifndef MAHALANOBIS_PREFILTER_RESCUE_MIN_ANCHORS
#define MAHALANOBIS_PREFILTER_RESCUE_MIN_ANCHORS   1U
#endif

#ifndef MAHALANOBIS_PREFILTER_RESCUE_NOISE_SCALE_MIN
#define MAHALANOBIS_PREFILTER_RESCUE_NOISE_SCALE_MIN 4.0f
#endif

#ifndef MAHALANOBIS_PREFILTER_RESCUE_NOISE_MAX
#define MAHALANOBIS_PREFILTER_RESCUE_NOISE_MAX     0.25f
#endif

#ifndef MAHALANOBIS_PREFILTER_VELOCITY_WEIGHT
#define MAHALANOBIS_PREFILTER_VELOCITY_WEIGHT      0.5f
#endif

#ifndef MAHALANOBIS_PREFILTER_MIN_COVARIANCE
#define MAHALANOBIS_PREFILTER_MIN_COVARIANCE       1.0e-6f
#endif

/* Keep downstream D2 scoring tied to the same Mahalanobis thresholds. */
#ifndef MW_TRIL_D2_RECOVER
#define MW_TRIL_D2_RECOVER                         MAHALANOBIS_PREFILTER_D2_RECOVER
#endif

#ifndef MW_TRIL_D2_REJECT
#define MW_TRIL_D2_REJECT                          MAHALANOBIS_PREFILTER_D2_REJECT
#endif

#ifndef MW_TRIL_RESIDUAL_SCALE_M
#define MW_TRIL_RESIDUAL_SCALE_M                   0.30
#endif

#ifndef MW_TRIL_FP_AMP_GOOD
#define MW_TRIL_FP_AMP_GOOD                        40.0
#endif

#ifndef MW_TRIL_WEIGHT_D2
#define MW_TRIL_WEIGHT_D2                          0.35
#endif

#ifndef MW_TRIL_WEIGHT_FP_AMP
#define MW_TRIL_WEIGHT_FP_AMP                      0.15
#endif

#ifndef MW_TRIL_WEIGHT_GDOP
#define MW_TRIL_WEIGHT_GDOP                        0.20
#endif

#ifndef MW_TRIL_WEIGHT_RESIDUAL
#define MW_TRIL_WEIGHT_RESIDUAL                    0.30
#endif

#ifndef MW_TRIL_WEIGHT_DIST
#define MW_TRIL_WEIGHT_DIST                        0.25
#endif

#ifndef MW_TRIL_SWITCH_MARGIN
#define MW_TRIL_SWITCH_MARGIN                      0.12
#endif

#ifndef MW_TRIL_SWITCH_SCORE_EPS
#define MW_TRIL_SWITCH_SCORE_EPS                  0.02
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

#ifndef ENABLE_SYS_FUSION
#define ENABLE_SYS_FUSION  1
#endif

#ifndef SYS_FUSION_PREFILTER_ENABLED
#define SYS_FUSION_PREFILTER_ENABLED (ENABLE_SYS_FUSION && ENABLE_MAHALANOBIS_PREFILTER)
#endif

#ifndef SYS_FUSION_USE_PLANAR_RANGES
#define SYS_FUSION_USE_PLANAR_RANGES 1
#endif

#ifndef SYS_FUSION_UKF_ALPHA
#define SYS_FUSION_UKF_ALPHA   1.0f
#endif

#ifndef SYS_FUSION_UKF_KAPPA
#define SYS_FUSION_UKF_KAPPA   0.0f
#endif

#ifndef SYS_FUSION_UKF_BETA
#define SYS_FUSION_UKF_BETA    2.0f
#endif

#ifndef SYS_FUSION_UKF_QA
#define SYS_FUSION_UKF_QA      0.25f
#endif

#ifndef SYS_FUSION_UKF_QG
#define SYS_FUSION_UKF_QG      1.0e-6f
#endif

#ifndef SYS_FUSION_UKF_R_UWB
#define SYS_FUSION_UKF_R_UWB   0.05f
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
