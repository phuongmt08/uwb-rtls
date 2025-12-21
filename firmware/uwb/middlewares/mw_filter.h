/* ============================== mw_filter.h ================================
 * @file       mw_filter.h
 * @brief      Simple filter algorithms (technology-agnostic)
 * @version    2.0.0
 * @date       2025-12-21
 */

#ifndef __MW_FILTER_H
#define __MW_FILTER_H

#include <stdint.h>
#include <stdbool.h>

/* ===== Kalman 2D Filter ===== */

#define KALMAN_2D_STATE_SIZE (4)  /* [x, vx, y, vy] */

typedef struct {
    float x, y;      /* Position */
    float vx, vy;    /* Velocity */
} pos_vel_2d_t;

typedef struct {
    float state[KALMAN_2D_STATE_SIZE];
    float P[KALMAN_2D_STATE_SIZE][KALMAN_2D_STATE_SIZE];
    float dt;
    float Q;  /* Process noise */
    float R;  /* Measurement noise */
    bool initialized;
} kalman_2d_t;

/**
 * @brief Initialize Kalman 2D filter
 */
void mw_filter_kalman2d_init(kalman_2d_t *kf, float x0, float y0, 
                             float dt, float Q, float R);

/**
 * @brief Update with measurement
 * @param R_scale Multiplier for R (1.0 = normal, >1 = less trust)
 */
bool mw_filter_kalman2d_update(kalman_2d_t *kf, float mx, float my, 
                               float R_scale, pos_vel_2d_t *out);

  
typedef struct {
    float value;
    float alpha;
    bool initialized;
} ema_filter_t;

/**
 * @brief Initialize EMA filter
 * @param alpha Smoothing factor (0.1=smooth, 0.9=fast)
 */
void mw_filter_ema_init(ema_filter_t *f, float alpha);

/**
 * @brief Update EMA
 */
float mw_filter_ema_update(ema_filter_t *f, float input);

#endif /* __MW_FILTER_H */