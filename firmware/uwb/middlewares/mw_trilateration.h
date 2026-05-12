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
    bool quality_valid;
    double selection_score;
    double residual_rms;
    double gdop_penalty;
    double fp_penalty;
    double fp_snr_penalty;
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
                                     uint8_t max_out);

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
