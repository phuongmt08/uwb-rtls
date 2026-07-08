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
    double fp_snr;          /* Diagnostic only, never used in weighting */
    double rx_fp_delta_db;  /* APS006 RX-FP delta [dB]; log-only until calibrated */
    bool quality_valid;
    bool rescued;      /* Recovered by frame-level rescue: keep weight low */

    /* Per-anchor measurement weight (prefilter output) */
    double q_mahalanobis;      /* Soft confidence from innovation/d2 */
    double q_fp;               /* Soft confidence from first-path amplitude */
    double q_residual;         /* Frame residual confidence, needs >=4 anchors */
    double sigma_r2;           /* Estimated range variance */
    double measurement_weight; /* Final precision weight w = qM*qFP*qR/sigma_r2 */

    /* Layout selection / debug outputs (not the main estimator) */
    double layout_score;       /* WGDOP of the selected layout */
    double debug_residual;     /* Residual vs debug position */
    double debug_tril_rms;     /* Debug trilateration residual RMS */

    /* Legacy composite-score fields, kept for logging compatibility */
    double selection_score;
    double residual_rms;
    double gdop_penalty;
    double fp_penalty;
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
 * @brief Select the best anchors based on Mahalanobis distance (d2_score)
 * 
 * Sorts valid anchors by d2_score ascending and returns the top M anchors.
 * 
 * @param[in]  anchors        Input array of anchors
 * @param[in]  total_anchors  Number of anchors in input array
 * @param[out] best_out       Output array to store selected anchors
 * @param[in]  max_out        Maximum number of anchors to select (usually 3 or 4)
 * @return Number of anchors successfully selected and copied to best_out
 */
uint8_t mw_trilateration_select_best(const mw_tril_anchor_t *anchors,
                                     uint8_t total_anchors,
                                     mw_tril_anchor_t *best_out,
                                     uint8_t max_out,
                                     uint8_t prev_mask);

/**
 * @brief Huber influence weight q(u;c): 1 when |u|<=c, c/|u| otherwise.
 */
double mw_huber_weight(double u, double c);

/**
 * @brief Compute per-anchor measurement weights for one ranging frame.
 *
 * Fills q_mahalanobis, q_fp, q_residual, sigma_r2 and measurement_weight
 * of every valid anchor. q_residual is only informative when the frame has
 * at least 4 valid anchors and a reference position; with 3 anchors it is
 * forced to 1 (a 3-anchor 2D fit can absorb one bad range).
 *
 * @param[in,out] anchors      Anchor array (valid entries are updated)
 * @param[in]     count        Number of entries in the array
 * @param[in]     p_ref_valid  true if p_ref holds a usable reference position
 * @param[in]     p_ref        Reference position (UKF predicted/last state)
 */
void mw_anchor_compute_weights(mw_tril_anchor_t *anchors,
                               uint8_t count,
                               bool p_ref_valid,
                               vec2d_t p_ref);

/**
 * @brief Select the 3-anchor layout for the UKF update by weighted geometry.
 *
 * Scores every candidate triplet with WGDOP = sqrt(trace(inv(H^T W H)))
 * evaluated at a reference position (UKF predicted state when available,
 * otherwise the candidate debug trilateration, otherwise the anchor
 * centroid). Keeps the previous layout unless the challenger improves the
 * score beyond MW_LAYOUT_SWITCH_MARGIN (hysteresis).
 *
 * @param[in]  anchors      Valid anchors with measurement_weight computed
 * @param[in]  count        Number of anchors in the array
 * @param[in]  p_ref_valid  true if p_ref holds a usable reference position
 * @param[in]  p_ref        Reference position for the geometry matrix
 * @param[out] best_out     Exactly 3 selected anchors on success
 * @param[in]  prev_mask    Bitmask of the previously selected anchor IDs
 * @return 3 on success, 0 when no usable layout exists
 */
uint8_t mw_select_ukf_layout_3(const mw_tril_anchor_t *anchors,
                               uint8_t count,
                               bool p_ref_valid,
                               vec2d_t p_ref,
                               mw_tril_anchor_t *best_out,
                               uint8_t prev_mask);

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
