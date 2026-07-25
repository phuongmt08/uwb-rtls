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

#include "config.h"

/* ===================================================================
 * ANTENNA DELAY CONFIGURATION
 * =================================================================== */

 /* Enable/Disable: Force antenna delay to otp values on startup
 * 0 = Use calibrated values (if available)
 * 1 = Force antenna delay to otp
 */
#define ENABLE_OTP_ANTENNA_DELAY    0

/* Enable/Disable: Force antenna delay to default values on startup
 * 0 = Use calibrated values (if available)
 * 1 = Force antenna delay to factory defaults
 */
#define ENABLE_FORCE_DEFAULT_ANT_DLY    0

#define TAG_FACTORY_TX_ANT_DLY      16436
#define TAG_FACTORY_RX_ANT_DLY      16436

#define ANCHOR_DEFAULT_TX_ANT_DLY   16187
#define ANCHOR_DEFAULT_RX_ANT_DLY   16187

/* ===================================================================
 * HEIGHT CONFIGURATION
 * =================================================================== */

/**
 * @brief Tag height from ground (meters)
 */
#define TAG_HEIGHT_M            (0.585f)

/**
 * @brief Anchor height from ground (meters)
 */
#define ANCHOR_HEIGHT_M         (2.495f)

/**
 * @brief Height offset between Anchor and Tag (meters)
 */
#define HEIGHT_OFFSET_M         (ANCHOR_HEIGHT_M - TAG_HEIGHT_M)

/* ===================================================================
 * ANCHOR LAYOUT
 * =================================================================== */

#define MAX_ANCHORS_SUPPORTED  8
/* Maximum number of anchors participating in one zone/ranging cycle. */
#define NUM_ANCHORS            6
#if NUM_ANCHORS > MAX_ZONE_ANCHORS
#error "NUM_ANCHORS exceeds protobuf zone-profile capacity"
#endif

#if NUM_ANCHORS > MAX_ANCHORS_SUPPORTED
#error "NUM_ANCHORS exceeds ranging anchor-ID capacity"
#endif

/* Default active zone ID (1 or 2) */
#ifndef DEFAULT_ZONE_ID
#define DEFAULT_ZONE_ID        1
#endif

/* When enabled, a ranging cycle is aborted unless at least three anchors
 * respond and return results. Keep disabled so survey and degraded-layout
 * operation can consume partial ranging results. */
#define SYS_RANGING_REQUIRE_MIN_ANCHOR_SAMPLES  0

/* ===================================================================
 * ZONE SWITCH STRESS TEST
 * =================================================================== */

/* 0 = normal runtime
 * 1 = repeatedly switch between valid zone profiles to measure radio
 *     reconfiguration latency and ranging timing disturbance. Keep this
 *     disabled outside bench testing. */
#ifndef SYS_ZONE_SWITCH_STRESS_TEST_ENABLE
#define SYS_ZONE_SWITCH_STRESS_TEST_ENABLE       0
#endif

#ifndef SYS_ZONE_SWITCH_STRESS_INTERVAL_MS
#define SYS_ZONE_SWITCH_STRESS_INTERVAL_MS       500U
#endif

/* Zone 1 Defaults */
#define ZONE_1_ANCHOR_1_ID   1
#define ZONE_1_ANCHOR_1_X    0.7f
#define ZONE_1_ANCHOR_1_Y    0.03f
#define ZONE_1_ANCHOR_1_Z    ANCHOR_HEIGHT_M

#define ZONE_1_ANCHOR_2_ID   2
#define ZONE_1_ANCHOR_2_X    2.70f
#define ZONE_1_ANCHOR_2_Y    8.37f
#define ZONE_1_ANCHOR_2_Z    ANCHOR_HEIGHT_M

#define ZONE_1_ANCHOR_3_ID   3
#define ZONE_1_ANCHOR_3_X    7.5f
#define ZONE_1_ANCHOR_3_Y    8.37f
#define ZONE_1_ANCHOR_3_Z    ANCHOR_HEIGHT_M

#define ZONE_1_ANCHOR_4_ID   4
#define ZONE_1_ANCHOR_4_X    7.5f
#define ZONE_1_ANCHOR_4_Y    0.03f
#define ZONE_1_ANCHOR_4_Z    ANCHOR_HEIGHT_M

#define ZONE_1_ANCHOR_5_ID   5
#define ZONE_1_ANCHOR_5_X    4.3f
#define ZONE_1_ANCHOR_5_Y    0.8f
#define ZONE_1_ANCHOR_5_Z    0.88f

#define ZONE_1_ANCHOR_6_ID   6
#define ZONE_1_ANCHOR_6_X    4.3f
#define ZONE_1_ANCHOR_6_Y    7.88f
#define ZONE_1_ANCHOR_6_Z    1.44f

/* Zone 2 is intentionally left unconfigured for now. */

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
#define MAHALANOBIS_PREFILTER_D2_REJECT            6.0f
#endif

#ifndef MAHALANOBIS_PREFILTER_R_BASE
#define MAHALANOBIS_PREFILTER_R_BASE               0.05f
#endif

#ifndef MAHALANOBIS_PREFILTER_R_GATE
#define MAHALANOBIS_PREFILTER_R_GATE               0.10f
#endif

#ifndef MAHALANOBIS_PREFILTER_RESCUE_MIN_ANCHORS
#define MAHALANOBIS_PREFILTER_RESCUE_MIN_ANCHORS   3U
#endif

/* Transient rejects produce a predict-only frame. Rescue is allowed only
 * after the same anchor has failed this many consecutive gate evaluations. */
#ifndef MAHALANOBIS_PREFILTER_RESCUE_MIN_REJECT_STREAK
#define MAHALANOBIS_PREFILTER_RESCUE_MIN_REJECT_STREAK  2U
#endif

/* Never rescue an anchor whose spatial innovation is catastrophically far
 * from the UKF prior.  This keeps controlled rescue from becoming an
 * unconditional "always force three anchors" policy. */
#ifndef MAHALANOBIS_PREFILTER_RESCUE_D2_MAX
#define MAHALANOBIS_PREFILTER_RESCUE_D2_MAX        25.0f
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

#ifndef MW_TRIL_RANGE_SIGMA_BASE_M
/* LOS range standard-deviation model: sigma(d)^2 = base^2 + (slope*d)^2. */
#define MW_TRIL_RANGE_SIGMA_BASE_M                 0.10
#endif

#ifndef MW_TRIL_RANGE_SIGMA_SLOPE
#define MW_TRIL_RANGE_SIGMA_SLOPE                  0.015
#endif

#ifndef MW_TRIL_RANGE_SIGMA_MAX_M
#define MW_TRIL_RANGE_SIGMA_MAX_M                  0.35
#endif

#ifndef MW_TRIL_HUBER_FP_DEFICIT_DELTA
/* Huber transition for (1 - DW1000 register-based FP confidence). */
#define MW_TRIL_HUBER_FP_DEFICIT_DELTA             0.35
#endif

#ifndef MW_TRIL_HUBER_RESIDUAL_DELTA
/* Huber transition in normalized residual standard deviations. */
#define MW_TRIL_HUBER_RESIDUAL_DELTA               1.50
#endif

#ifndef MW_TRIL_HUBER_WEIGHT_FLOOR
#define MW_TRIL_HUBER_WEIGHT_FLOOR                 0.10
#endif

#ifndef MW_TRIL_WGDOP_DET_MIN
#define MW_TRIL_WGDOP_DET_MIN                      1.0e-8
#endif

/* Reject nearly-collinear triplets before WGDOP ranking.
 * quality = |cross(B-A, C-A)| / max_edge^2; equilateral ~= 0.866. */
#ifndef MW_TRIL_MIN_GEOMETRY_QUALITY
#define MW_TRIL_MIN_GEOMETRY_QUALITY                0.15
#endif

/* Penalize a triplet whose trilaterated probe disagrees with all ranges that
 * survived the frame prefilter. score = WGDOP + weight * residual_rms. */
#ifndef MW_TRIL_RESIDUAL_SCORE_WEIGHT
#define MW_TRIL_RESIDUAL_SCORE_WEIGHT               0.50
#endif

/* Trust the common UKF reference only while its radial 1-sigma uncertainty,
 * sqrt(Pxx + Pyy), stays below this limit. */
#ifndef MW_TRIL_REFERENCE_MAX_STD_M
#define MW_TRIL_REFERENCE_MAX_STD_M                0.50f
#endif

#ifndef MW_TRIL_SWITCH_MARGIN
/* New triplet must improve the composite selection score by 10 percent. */
#define MW_TRIL_SWITCH_MARGIN                      0.10
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

#ifndef SYS_FUSION_RAW_DEBUG_STREAM_ENABLE
#define SYS_FUSION_RAW_DEBUG_STREAM_ENABLE  1
#endif

#ifndef SYS_FUSION_PROTOBUF_STREAM_ENABLE
#define SYS_FUSION_PROTOBUF_STREAM_ENABLE   1
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
#define SYS_FUSION_UKF_QA      0.04f
#endif

#ifndef SYS_FUSION_UKF_QG
#define SYS_FUSION_UKF_QG      1.0e-10f
#endif

#ifndef SYS_FUSION_UKF_R_UWB
#define SYS_FUSION_UKF_R_UWB   0.01f
#endif

#ifndef MW_UKF_R_MIN
#define MW_UKF_R_MIN            0.0025f
#endif

#ifndef MW_UKF_R_MAX
#define MW_UKF_R_MAX            0.25f
#endif

/* Numerical floors for the small single-precision UKF matrices. */
#ifndef MW_UKF_INNOVATION_JITTER_MAX
#define MW_UKF_INNOVATION_JITTER_MAX  1.0e-3f
#endif

#ifndef MW_UKF_P_DIAGONAL_FLOOR
#define MW_UKF_P_DIAGONAL_FLOOR      1.0e-8f
#endif

#ifndef SYS_FUSION_UKF_INIT_P_PX
#define SYS_FUSION_UKF_INIT_P_PX        0.1f
#endif

#ifndef SYS_FUSION_UKF_INIT_P_PY
#define SYS_FUSION_UKF_INIT_P_PY        0.1f
#endif

#ifndef SYS_FUSION_UKF_INIT_P_VX
#define SYS_FUSION_UKF_INIT_P_VX        0.1f
#endif

#ifndef SYS_FUSION_UKF_INIT_P_VY
#define SYS_FUSION_UKF_INIT_P_VY        0.1f
#endif

#ifndef SYS_FUSION_UKF_INIT_P_THETA
#define SYS_FUSION_UKF_INIT_P_THETA     1.0e-10f
#endif

#ifndef SYS_FUSION_UKF_INIT_P_BIAS_AX
#define SYS_FUSION_UKF_INIT_P_BIAS_AX   0.001f
#endif

#ifndef SYS_FUSION_UKF_INIT_P_BIAS_AY
#define SYS_FUSION_UKF_INIT_P_BIAS_AY   0.001f
#endif

#ifndef SYS_FUSION_UKF_INIT_P_BIAS_GZ
#define SYS_FUSION_UKF_INIT_P_BIAS_GZ   1.0e-10f
#endif

#ifndef SYS_FUSION_IMU_BUTTERWORTH_ENABLE
/* Diagnostic bypass: keep raw IMU samples unchanged while isolating the
 * UKF-to-trilateration position offset. */
#define SYS_FUSION_IMU_BUTTERWORTH_ENABLE       1
#endif

/* Estimate roll/pitch from the complete 6-axis IMU and remove gravity before
 * the planar UKF. The output remains in the yaw-free, levelled body frame;
 * sys_sensor_fusion_predict() still performs the body-to-world yaw rotation. */
#ifndef SYS_FUSION_IMU_PREFILTER_ENABLE
#define SYS_FUSION_IMU_PREFILTER_ENABLE          1
#endif

#ifndef SYS_FUSION_IMU_GRAVITY_MPS2
#define SYS_FUSION_IMU_GRAVITY_MPS2              9.80665f
#endif

/* Larger values trust gyro integration for longer and reject sustained
 * translational acceleration more strongly. */
#ifndef SYS_FUSION_IMU_ATTITUDE_TIME_CONSTANT_S
#define SYS_FUSION_IMU_ATTITUDE_TIME_CONSTANT_S  1.5f
#endif

/* Accelerometer attitude correction fades to zero when |norm(a)-g| reaches
 * this value. */
#ifndef SYS_FUSION_IMU_ATTITUDE_ACC_TOLERANCE
#define SYS_FUSION_IMU_ATTITUDE_ACC_TOLERANCE    1.5f
#endif

#ifndef SYS_FUSION_IMU_BUTTERWORTH_CUTOFF_HZ
#define SYS_FUSION_IMU_BUTTERWORTH_CUTOFF_HZ    0.5f
#endif

#ifndef SYS_FUSION_IMU_SAMPLE_RATE_HZ
#define SYS_FUSION_IMU_SAMPLE_RATE_HZ           50.0f
#endif

#ifndef SYS_FUSION_IMU_CUTOFF_NYQUIST_MARGIN
#define SYS_FUSION_IMU_CUTOFF_NYQUIST_MARGIN    0.95f
#endif

#ifndef SYS_FUSION_IMU_ZUPT_ENABLE
/* Diagnostic bypass: do not force velocity/acceleration to zero while
 * comparing the UKF prediction and UWB correction paths. */
#define SYS_FUSION_IMU_ZUPT_ENABLE              1
#endif

#ifndef SYS_FUSION_IMU_ZUPT_ACC_THRESHOLD
#define SYS_FUSION_IMU_ZUPT_ACC_THRESHOLD       0.15f
#endif

#ifndef SYS_FUSION_IMU_ZUPT_GYR_THRESHOLD
#define SYS_FUSION_IMU_ZUPT_GYR_THRESHOLD       0.05f
#endif

#ifndef SYS_FUSION_IMU_ZUPT_COUNT_THRESHOLD
#define SYS_FUSION_IMU_ZUPT_COUNT_THRESHOLD     10U
#endif

#ifndef SYS_FUSION_IMU_ZUPT_USE_FILTERED_SAMPLE
#define SYS_FUSION_IMU_ZUPT_USE_FILTERED_SAMPLE 1
#endif

#ifndef SYS_FUSION_IMU_ZUPT_VEL_VARIANCE
#define SYS_FUSION_IMU_ZUPT_VEL_VARIANCE        1.0e-4f
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
