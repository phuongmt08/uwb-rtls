/**
 * @file       mw_filter.c
 * @copyright
 * @license
 * @version    3.1.0
 * @date       2026-01-10
 * @author     Phuong Mai
 * @brief      Adaptive Kalman Filter with Innovation-based R tuning
 * @example    None
 */
#include "mw_filter.h"
#include "positioning_config.h"
#include <string.h>
#include <math.h>

/* ========== DES FILTER ========== */

#if MW_FILTER_ENABLE_DES

void mw_filter_des_init(des_filter_2d_t *des,
                        float x0, float y0,
                        float alpha_base,
                        float beta)
{
    if (!des) return;
    memset(des, 0, sizeof(des_filter_2d_t));
    
    des->s_x = x0;
    des->s_y = y0;
    des->b_x = 0.0f;
    des->b_y = 0.0f;
    
    des->alpha_base = alpha_base;
    des->alpha = alpha_base;
    des->beta = beta;
    
    des->prev_mx = x0;
    des->prev_my = y0;
    des->change_ema = 0.0f;
    
    des->initialized = true;
}

void mw_filter_des_update(des_filter_2d_t *des,
                          float mx_raw, float my_raw,
                          float *mx_smooth, float *my_smooth)
{
    if (!des || !des->initialized || !mx_smooth || !my_smooth) return;
    
    /* Calculate movement change */
    float dx = mx_raw - des->prev_mx;
    float dy = my_raw - des->prev_my;
    float change = sqrtf(dx*dx + dy*dy);
    
    /* Exponential moving average of change */
    if (des->change_ema == 0.0f) {
        des->change_ema = change;
    } else {
        des->change_ema = DES_CHANGE_ALPHA * change + 
                         (1.0f - DES_CHANGE_ALPHA) * des->change_ema;
    }
    
    /* Adaptive alpha based on motion */
    if (des->change_ema > DES_MOTION_THRESHOLD) {
        /* High motion: increase responsiveness */
        des->alpha = des->alpha_base * DES_MOTION_SCALE_HIGH;
        if (des->alpha > DES_ALPHA_MAX) des->alpha = DES_ALPHA_MAX;
    } else {
        /* Low motion: increase smoothing */
        des->alpha = des->alpha_base * DES_MOTION_SCALE_LOW;
        if (des->alpha < DES_ALPHA_MIN) des->alpha = DES_ALPHA_MIN;
    }
    
    /* Double Exponential Smoothing update */
    float s_x_prev = des->s_x;
    float b_x_prev = des->b_x;
    des->s_x = des->alpha * mx_raw + (1.0f - des->alpha) * (s_x_prev + b_x_prev);
    des->b_x = des->beta * (des->s_x - s_x_prev) + (1.0f - des->beta) * b_x_prev;
    
    float s_y_prev = des->s_y;
    float b_y_prev = des->b_y;
    des->s_y = des->alpha * my_raw + (1.0f - des->alpha) * (s_y_prev + b_y_prev);
    des->b_y = des->beta * (des->s_y - s_y_prev) + (1.0f - des->beta) * b_y_prev;
    
    /* Output: level + trend */
    *mx_smooth = des->s_x + des->b_x;
    *my_smooth = des->s_y + des->b_y;
    
    /* Update history */
    des->prev_mx = mx_raw;
    des->prev_my = my_raw;
}

void mw_filter_des_reset(des_filter_2d_t *des, float x, float y)
{
    if (!des) return;
    
    des->s_x = x;
    des->s_y = y;
    des->b_x = 0.0f;
    des->b_y = 0.0f;
    des->prev_mx = x;
    des->prev_my = y;
    des->change_ema = 0.0f;
}

#endif /* MW_FILTER_ENABLE_DES */

/* ========== ADAPTIVE KALMAN FILTER ========== */

#if MW_FILTER_ENABLE_AKF

void mw_filter_akf_init(adaptive_kalman_2d_t *akf,
                        float x0, float y0,
                        float dt,
                        float Q,
                        float R_base,
                        float innovation_alpha,
                        float R_scale_min,
                        float R_scale_max)
{
    if (!akf) return;
    memset(akf, 0, sizeof(adaptive_kalman_2d_t));
    
    /* State: [x, vx, y, vy] */
    akf->state[0] = x0;
    akf->state[1] = 0.0f;
    akf->state[2] = y0;
    akf->state[3] = 0.0f;
    
    /* Initial covariance */
    akf->P[0][0] = 1.0f;
    akf->P[1][1] = 1.0f;
    akf->P[2][2] = 1.0f;
    akf->P[3][3] = 1.0f;
    
    /* Parameters */
    akf->dt = dt;
    akf->Q = Q;
    akf->R_base = R_base;
    
    /* Innovation tracking */
    akf->innovation_x = 0.0f;
    akf->innovation_y = 0.0f;
    akf->innovation_var = R_base;
    akf->innovation_alpha = innovation_alpha;
    
    /* Adaptive R scaling */
    akf->R_scale = 1.0f;
    akf->R_scale_min = R_scale_min;
    akf->R_scale_max = R_scale_max;
    
    akf->initialized = true;
}

float mw_filter_akf_update(adaptive_kalman_2d_t *akf,
                           float mx, float my,
                           pos_vel_2d_t *out)
{
    if (!akf || !akf->initialized) return 1.0f;
    
    float dt = akf->dt;
    float Q = akf->Q;
    
    /* Calculate current velocity magnitude */
    float velocity = sqrtf(akf->state[1]*akf->state[1] + 
                          akf->state[3]*akf->state[3]);
    
    /* Detect stopped state */
    bool is_stopped = (velocity < AKF_STOP_THRESHOLD);
    
    /* Dampen velocity when stopped */
    if (is_stopped) {
        akf->state[1] *= AKF_STOP_VELOCITY_DAMPING;
        akf->state[3] *= AKF_STOP_VELOCITY_DAMPING;
        velocity = sqrtf(akf->state[1]*akf->state[1] + 
                        akf->state[3]*akf->state[3]);
    }
    
    /* ===== PREDICT ===== */
    float x_pred = akf->state[0] + akf->state[1] * dt;
    float vx_pred = akf->state[1];
    float y_pred = akf->state[2] + akf->state[3] * dt;
    float vy_pred = akf->state[3];
    
    /* Innovation */
    float innov_x = mx - x_pred;
    float innov_y = my - y_pred;
    
    /* Velocity-adaptive process noise */
    float q_scale = is_stopped ? AKF_Q_SCALE_STOPPED : 
                    (1.0f + AKF_Q_SCALE_VELOCITY_K * velocity);
    float sigma_a2 = Q * q_scale;
    
    /* Process noise covariance matrix */
    float dt2 = dt * dt;
    float dt3 = dt2 * dt;
    float dt4 = dt3 * dt;
    
    float Q_pos_pos = 0.25f * sigma_a2 * dt4;
    float Q_pos_vel = 0.5f  * sigma_a2 * dt3;
    float Q_vel_vel = sigma_a2 * dt2;
    
    /* Predict covariance (X dimension) */
    float P00 = akf->P[0][0] + 2.0f*dt*akf->P[0][1] + dt2*akf->P[1][1] + Q_pos_pos;
    float P01 = akf->P[0][1] + dt*akf->P[1][1] + Q_pos_vel;
    float P11 = akf->P[1][1] + Q_vel_vel;
    
    /* Predict covariance (Y dimension) */
    float P22 = akf->P[2][2] + 2.0f*dt*akf->P[2][3] + dt2*akf->P[3][3] + Q_pos_pos;
    float P23 = akf->P[2][3] + dt*akf->P[3][3] + Q_pos_vel;
    float P33 = akf->P[3][3] + Q_vel_vel;
    
    /* ===== ADAPTIVE R CALCULATION ===== */
    float innov_magnitude_sq = innov_x*innov_x + innov_y*innov_y;
    
    /* Initialize or update innovation variance estimate */
    if (akf->innovation_var == 0.0f || akf->innovation_var == akf->R_base) {
        akf->innovation_var = innov_magnitude_sq > 1e-6f ? 
                             innov_magnitude_sq : akf->R_base;
    } else {
        akf->innovation_var = akf->innovation_alpha * innov_magnitude_sq +
                             (1.0f - akf->innovation_alpha) * akf->innovation_var;
    }
    
    akf->innovation_x = innov_x;
    akf->innovation_y = innov_y;
    
    /* Calculate R scale based on innovation */
    float innovation_ratio = sqrtf(akf->innovation_var / akf->R_base);
    akf->R_scale = 1.0f / innovation_ratio;
    
    /* Apply constraints on R_scale */
    if (is_stopped) {
        /* When stopped, trust measurements more (lower R) */
        akf->R_scale = akf->R_scale_max;
    } else {
        if (akf->R_scale < akf->R_scale_min) akf->R_scale = akf->R_scale_min;
        if (akf->R_scale > akf->R_scale_max) akf->R_scale = akf->R_scale_max;
    }
    
    float R = akf->R_base * akf->R_scale;
    
    /* ===== UPDATE (X dimension) ===== */
    float Sx = P00 + R;
    float Kx0 = P00 / Sx;
    float Kx1 = P01 / Sx;
    
    /* Reduce Kalman gain when stopped */
    if (is_stopped) {
        Kx0 *= AKF_STOP_GAIN_REDUCTION;
        Kx1 *= AKF_STOP_GAIN_REDUCTION;
    }
    
    akf->state[0] = x_pred + Kx0 * innov_x;
    akf->state[1] = vx_pred + Kx1 * innov_x;
    
    akf->P[0][0] = P00 - Kx0*Sx*Kx0;
    akf->P[0][1] = akf->P[1][0] = P01 - Kx0*Sx*Kx1;
    akf->P[1][1] = P11 - Kx1*Sx*Kx1;
    
    /* ===== UPDATE (Y dimension) ===== */
    float Sy = P22 + R;
    float Ky2 = P22 / Sy;
    float Ky3 = P23 / Sy;
    
    /* Reduce Kalman gain when stopped */
    if (is_stopped) {
        Ky2 *= AKF_STOP_GAIN_REDUCTION;
        Ky3 *= AKF_STOP_GAIN_REDUCTION;
    }
    
    akf->state[2] = y_pred + Ky2 * innov_y;
    akf->state[3] = vy_pred + Ky3 * innov_y;
    
    akf->P[2][2] = P22 - Ky2*Sy*Ky2;
    akf->P[2][3] = akf->P[3][2] = P23 - Ky2*Sy*Ky3;
    akf->P[3][3] = P33 - Ky3*Sy*Ky3;
    
    /* Output state */
    if (out) {
        out->x = akf->state[0];
        out->vx = akf->state[1];
        out->y = akf->state[2];
        out->vy = akf->state[3];
    }
    
    return akf->R_scale;
}

void mw_filter_akf_get_stats(const adaptive_kalman_2d_t *akf,
                             float *innovation_var,
                             float *R_scale)
{
    if (!akf) return;
    
    if (innovation_var) *innovation_var = akf->innovation_var;
    if (R_scale) *R_scale = akf->R_scale;
}

#endif /* MW_FILTER_ENABLE_AKF */

/* ========== COMBINED FILTER ========== */

void mw_filter_init(mw_filter_cxt_t *filter,
                    float x0, float y0,
                    float dt,
                    float des_alpha,
                    float des_beta,
                    float akf_Q,
                    float akf_R_base,
                    float akf_innovation_alpha,
                    float akf_R_scale_min,
                    float akf_R_scale_max)
{
    if (!filter) return;
    
#if MW_FILTER_ENABLE_DES
    mw_filter_des_init(&filter->des, x0, y0, des_alpha, des_beta);
#endif
    
#if MW_FILTER_ENABLE_AKF
    mw_filter_akf_init(&filter->akf, x0, y0, dt,
                       akf_Q, akf_R_base, akf_innovation_alpha,
                       akf_R_scale_min, akf_R_scale_max);
#endif
}

float mw_filter_update(mw_filter_cxt_t *filter,
                       float mx_raw, float my_raw,
                       pos_vel_2d_t *out)
{
    if (!filter || !out) return 1.0f;
    
    float mx_input = mx_raw;
    float my_input = my_raw;
    
#if MW_FILTER_ENABLE_DES
    mw_filter_des_update(&filter->des, mx_raw, my_raw, &mx_input, &my_input);
#endif
    
#if MW_FILTER_ENABLE_AKF
    float R_scale = mw_filter_akf_update(&filter->akf, mx_input, my_input, out);
    return R_scale;
#else
    out->x = mx_input;
    out->y = my_input;
    out->vx = 0.0f;
    out->vy = 0.0f;
    return 1.0f;
#endif
}