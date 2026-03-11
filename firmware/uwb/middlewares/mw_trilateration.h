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
    int8_t rssi;       /* RSSI in dBm (e.g., -70) */
    bool valid;        /* true if measurement is valid */
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
 * @brief Calculate 3D position (automatic anchor selection)
 * 
 * Strategy:
 * - If 3 anchors: Use all 3
 * - If 4+ anchors: Select 3 best anchors by RSSI + distance
 * - Calculate position using 3-sphere algorithm
 * 
 * @param[in]  anchors     Array of anchor measurements
 * @param[in]  num_anchors Number of anchors (3-8)
 * @param[out] position    Calculated 3D position
 * @param[out] result      Optional quality info (can be NULL)
 * @return MW_TRIL_OK on success
 * 
 * @note Simple API - just call this function
 */
mw_tril_err_t mw_trilateration_3d(const mw_tril_anchor_t *anchors,
                                  uint8_t num_anchors,
                                  vec3d_t *position,
                                  mw_tril_result_t *result);

/**
 * @brief Calculate 2D position (automatic anchor selection)
 * 
 * Same as 3D but ignores Z coordinate (faster calculation)
 * 
 * @param[in]  anchors     Array of anchor measurements
 * @param[in]  num_anchors Number of anchors (3-8)
 * @param[out] position    Calculated 2D position
 * @param[out] result      Optional quality info (can be NULL)
 * @return MW_TRIL_OK on success
 */
mw_tril_err_t mw_trilateration_2d(const mw_tril_anchor_t *anchors,
                                  uint8_t num_anchors,
                                  vec2d_t *position,
                                  mw_tril_result_t *result);

#endif /* __MW_TRILATERATION_H */

/* End of file -------------------------------------------------------- */