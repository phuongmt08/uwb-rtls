/* ============================== mw_trilateration.h =========================
 * @file       mw_trilateration.h
 * @brief      Middleware - Simple trilateration algorithms
 * @version    3.0.0
 * @date       2025-12-20
 * @note       Keep it simple - production ready
 */

#ifndef __MW_TRILATERATION_H
#define __MW_TRILATERATION_H

/* Includes ----------------------------------------------------------- */
#include <stdint.h>
#include <stdbool.h>

/* Public enumerate/structure ----------------------------------------- */

/**
 * @brief 3D position vector
 */
typedef struct {
    double x;
    double y;
    double z;
} vec3d_t;

/**
 * @brief 2D position vector
 */
typedef struct {
    double x;
    double y;
} vec2d_t;

/**
 * @brief Anchor information
 */
typedef struct {
    vec3d_t position;  /* Anchor position (x, y, z) in meters */
    double distance;   /* Measured distance to tag in meters */
    uint8_t id;        /* Anchor ID */
    bool valid;        /* true if measurement is valid */
    double d2_score;   /* Mahalanobis distance squared */
    double r_adaptive; /* Adaptive covariance */
    double fp_amp_norm;
    double fp_snr;
    double fp_confidence;
    bool quality_valid;
    bool rescued;       /* Reintroduced after persistent prefilter rejection */
    double wgdop;
    double residual_rms;
    double triplet_fp_weight; /* Mean FP-only confidence of the selected triplet */
    double measurement_weight; /* Final per-anchor precision used by WGDOP */
} mw_tril_anchor_t;

/**
 * @brief Trilateration result
 */
typedef struct {
    vec3d_t position;       /* Calculated position */
    double error_estimate;  /* RMS error in meters */
    uint8_t num_anchors;    /* Number of anchors used */
    bool valid;             /* true if calculation successful */
} mw_tril_result_t;

/**
 * @brief Error codes
 */
typedef enum {
    MW_TRIL_OK = 0,
    MW_TRIL_ERR = -1,
    MW_TRIL_ERR_PARAM = -2,
    MW_TRIL_ERR_NO_SOLUTION = -3
} mw_tril_err_t;

/* Public function prototypes ----------------------------------------- */

/**
 * @brief Select the best three-anchor layout using Huber-weighted WGDOP.
 * 
 * For the production 2D path, combines temporal consistency, DW1000 first-path
 * confidence, frame residual, and distance-dependent variance as measurement
 * precision before evaluating each candidate layout.
 * 
 * @param[in]  candidates       Dense array of valid candidate anchors
 * @param[in]  candidate_count  Number of entries in candidates
 * @param[out] selected_out     Exact three-anchor selection
 * @param[in]  prev_mask        Previously selected triplet for hysteresis
 * @param[in]  reference_valid  true when the UKF reference is trusted
 * @param[in]  reference_position Common WGDOP evaluation position
 * @return 3 on success, otherwise 0
 */
uint8_t mw_trilateration_select_best_3(const mw_tril_anchor_t *candidates,
                                       uint8_t candidate_count,
                                       mw_tril_anchor_t selected_out[3],
                                       uint8_t prev_mask,
                                       bool reference_valid,
                                       vec2d_t reference_position);

/**
 * @brief Compute one precision weight per candidate at a common reference.
 *
 * Residual confidence is enabled only with at least four anchors and a trusted
 * reference. With three anchors, or while the UKF reference is uncertain,
 * residual confidence stays neutral to avoid self-fitting a triplet.
 */
void mw_trilateration_compute_weights(mw_tril_anchor_t *candidates,
                                      uint8_t candidate_count,
                                      bool reference_valid,
                                      vec2d_t reference_position);

/**
 * @brief Calculate 3D position (Mathematical core)
 * 
 * Requires EXACTLY 3 pre-selected valid anchors.
 * 
 * @param[in]  anchors_exact_3 Array of exactly 3 previously selected anchors
 * @param[out] position        Calculated 3D position
 * @param[out] result          Optional quality info (can be NULL)
 * @return MW_TRIL_OK on success
 */
mw_tril_err_t mw_trilateration_3d(const mw_tril_anchor_t *anchors_exact_3,
                                  vec3d_t *position,
                                  mw_tril_result_t *result);

/**
 * @brief Calculate 2D position (Mathematical core)
 * 
 * Requires EXACTLY 3 pre-selected valid anchors. Ignored Z coordinate.
 * 
 * @param[in]  anchors_exact_3 Array of exactly 3 previously selected anchors
 * @param[out] position        Calculated 2D position
 * @param[out] result          Optional quality info (can be NULL)
 * @return MW_TRIL_OK on success
 */
mw_tril_err_t mw_trilateration_2d(const mw_tril_anchor_t *anchors_exact_3,
                                  vec2d_t *position,
                                  mw_tril_result_t *result);

#endif /* __MW_TRILATERATION_H */

/* End of file -------------------------------------------------------- */
