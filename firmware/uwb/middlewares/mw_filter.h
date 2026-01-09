/**
 * @file       mw_filter.h
 * @copyright
 * @license
 * @version    3.0.0
 * @date       2026-01-10
 * @author     Phuong Mai
 * @brief      Adaptive Kalman Filter with Innovation & Velocity-based tuning
 * @example    None
 */
#ifndef __MW_FILTER_H
#define __MW_FILTER_H

#include <stdint.h>
#include <stdbool.h>

/* ===== Common Types ===== */

#define AKF_STATE_SIZE (4)  /* [x, vx, y, vy] */

typedef struct {
    float x, y;      /* Position (m) */
    float vx, vy;    /* Velocity (m/s) */
} pos_vel_2d_t;

/* ===== Adaptive Kalman Filter (AKF) - Innovation-based Adaptive R ===== */

typedef struct {
    float state[AKF_STATE_SIZE];  /* [x, vx, y, vy] */
    float P[AKF_STATE_SIZE][AKF_STATE_SIZE];  /* State covariance */
    float dt;  /* Time step (seconds) */
    
    /* Fixed noise parameters */
    float Q;  /* Process noise (fixed) */
    float R_base;  /* Base measurement noise */
    
    /* Innovation-based adaptive R */
    float innovation_x;     /* Last innovation for X */
    float innovation_y;     /* Last innovation for Y */
    float innovation_var;   /* Innovation variance (EMA filtered) */
    float innovation_alpha; /* EMA smoothing for innovation (0.2-0.5) */
    
    float R_scale;      /* Current adaptive R multiplier */
    float R_scale_min;  /* Min R scale (high confidence, e.g., 0.3) */
    float R_scale_max;  /* Max R scale (low confidence, e.g., 5.0) */
    
    bool initialized;
} adaptive_kalman_2d_t;

/**
 * @brief Initialize Adaptive Kalman Filter
 * @param akf Adaptive Kalman filter structure
 * @param x0, y0 Initial position (meters)
 * @param dt Time step (seconds, e.g., 0.1 for 10Hz)
 * @param Q Process noise (fixed, e.g., 0.001-0.01)
 * @param R_base Base measurement noise (e.g., 0.1-0.5)
 * @param innovation_alpha EMA for innovation variance (0.2=smooth, 0.5=responsive)
 * @param R_scale_min Min R scale (0.3 = trust more when stable)
 * @param R_scale_max Max R scale (5.0 = trust less when unstable)
 */
void mw_filter_akf_init(adaptive_kalman_2d_t *akf,
                        float x0, float y0,
                        float dt,
                        float Q,
                        float R_base,
                        float innovation_alpha,
                        float R_scale_min,
                        float R_scale_max);

/**
 * @brief Update AKF with measurement
 * @details Automatically adapts R based on innovation variance
 * @param akf Adaptive Kalman filter
 * @param mx, my Measurement position (meters)
 * @param out Output position and velocity (can be NULL)
 * @return Current R_scale for debugging
 */
float mw_filter_akf_update(adaptive_kalman_2d_t *akf,
                           float mx, float my,
                           pos_vel_2d_t *out);

/**
 * @brief Get adaptive filter stats for debugging
 */
void mw_filter_akf_get_stats(const adaptive_kalman_2d_t *akf,
                             float *innovation_var,
                             float *R_scale);

#endif /* __MW_FILTER_H */