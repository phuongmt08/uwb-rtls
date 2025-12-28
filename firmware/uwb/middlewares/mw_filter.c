/**
 * @file       mw_filter.c
 * @copyright
 * @license
 * @version    2.0.0
 * @date       2025-12-21
 * @author     Phuong Mai
 * @brief      Simple filter algorithms
 * @note       None
 * @example    None
 */
#include "mw_filter.h"
#include <string.h>

/* Kalman 2D --------------------------------------------------------- */

void mw_filter_kalman2d_init(kalman_2d_t *kf, float x0, float y0, 
                             float dt, float Q, float R)
{
    if (!kf) return;
    memset(kf, 0, sizeof(kalman_2d_t));
    
    kf->state[0] = x0;
    kf->state[2] = y0;
    kf->P[0][0] = kf->P[1][1] = kf->P[2][2] = kf->P[3][3] = 1.0f;
    kf->dt = dt;
    kf->Q = Q;
    kf->R = R;
    kf->initialized = true;
}

bool mw_filter_kalman2d_update(kalman_2d_t *kf, float mx, float my, 
                               float R_scale, pos_vel_2d_t *out)
{
    if (!kf || !kf->initialized) return false;
    
    float dt = kf->dt;
    float Q = kf->Q;
    float R = kf->R * R_scale;
    
    /* Predict */
    float x = kf->state[0] + kf->state[1] * dt;
    float vx = kf->state[1];
    float y = kf->state[2] + kf->state[3] * dt;
    float vy = kf->state[3];
    
    float P00 = kf->P[0][0] + 2*dt*kf->P[0][1] + dt*dt*kf->P[1][1] + Q;
    float P01 = kf->P[0][1] + dt*kf->P[1][1];
    float P11 = kf->P[1][1] + Q;
    float P22 = kf->P[2][2] + 2*dt*kf->P[2][3] + dt*dt*kf->P[3][3] + Q;
    float P23 = kf->P[2][3] + dt*kf->P[3][3];
    float P33 = kf->P[3][3] + Q;
    
    /* Update X */
    float Sx = P00 + R;
    float Kx0 = P00 / Sx;
    float Kx1 = P01 / Sx;
    kf->state[0] = x + Kx0 * (mx - x);
    kf->state[1] = vx + Kx1 * (mx - x);
    kf->P[0][0] = P00 - Kx0*Sx*Kx0;
    kf->P[0][1] = kf->P[1][0] = P01 - Kx0*Sx*Kx1;
    kf->P[1][1] = P11 - Kx1*Sx*Kx1;
    
    /* Update Y */
    float Sy = P22 + R;
    float Ky2 = P22 / Sy;
    float Ky3 = P23 / Sy;
    kf->state[2] = y + Ky2 * (my - y);
    kf->state[3] = vy + Ky3 * (my - y);
    kf->P[2][2] = P22 - Ky2*Sy*Ky2;
    kf->P[2][3] = kf->P[3][2] = P23 - Ky2*Sy*Ky3;
    kf->P[3][3] = P33 - Ky3*Sy*Ky3;
    
    if (out) {
        out->x = kf->state[0];
        out->vx = kf->state[1];
        out->y = kf->state[2];
        out->vy = kf->state[3];
    }
    return true;
}

/* EMA --------------------------------------------------------------- */

void mw_filter_ema_init(ema_filter_t *f, float alpha)
{
    if (!f) return;
    f->alpha = alpha;
    f->initialized = false;
}

float mw_filter_ema_update(ema_filter_t *f, float input)
{
    if (!f) return input;
    
    if (!f->initialized) {
        f->value = input;
        f->initialized = true;
        return input;
    }
    
    f->value = f->alpha * input + (1.0f - f->alpha) * f->value;
    return f->value;
}