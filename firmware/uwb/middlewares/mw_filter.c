/**
 * @file       mw_filter.c
 * @copyright
 * @license
 * @version    3.0.0
 * @date       2026-01-10
 * @author     Phuong Mai
 * @brief      Adaptive Kalman Filter with Innovation-based R tuning
 * @note       Replaced old KF2D + RSSI with innovation-based AKF
 * @example    None
 */
#include "mw_filter.h"
#include <string.h>
#include <math.h>

/* Adaptive Kalman Filter -------------------------------------------- */

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
    
    /* Initial state: [x, vx, y, vy] */
    akf->state[0] = x0;
    akf->state[1] = 0.0f;  /* vx = 0 */
    akf->state[2] = y0;
    akf->state[3] = 0.0f;  /* vy = 0 */
    
    /* Initial covariance (diagonal) */
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
    akf->innovation_var = R_base;  /* Initialize to base R */
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
    
    /* ===== PREDICT STEP ===== */
    
    /* State prediction: x_k = F * x_{k-1}
     * F = [1  dt  0   0 ]
     *     [0  1   0   0 ]
     *     [0  0   1  dt ]
     *     [0  0   0   1 ]
     */
    float x_pred = akf->state[0] + akf->state[1] * dt;
    float vx_pred = akf->state[1];
    float y_pred = akf->state[2] + akf->state[3] * dt;
    float vy_pred = akf->state[3];
    
    /* Covariance prediction: P_k = F * P_{k-1} * F^T + Q
     * Simplified for constant velocity model
     */
    float P00 = akf->P[0][0] + 2.0f*dt*akf->P[0][1] + dt*dt*akf->P[1][1] + Q;
    float P01 = akf->P[0][1] + dt*akf->P[1][1];
    float P11 = akf->P[1][1] + Q;
    float P22 = akf->P[2][2] + 2.0f*dt*akf->P[2][3] + dt*dt*akf->P[3][3] + Q;
    float P23 = akf->P[2][3] + dt*akf->P[3][3];
    float P33 = akf->P[3][3] + Q;
    
    /* ===== INNOVATION CALCULATION ===== */
    
    /* Innovation (measurement residual): y = z - H*x_pred
     * Where H = [1 0 0 0; 0 0 1 0] (we measure position only)
     */
    float innov_x = mx - x_pred;
    float innov_y = my - y_pred;
    
    /* Innovation magnitude squared (Euclidean distance^2) */
    float innov_magnitude_sq = innov_x*innov_x + innov_y*innov_y;
    
    /* Update innovation variance estimate using EMA
     * This tracks how well predictions match measurements
     */
    if (akf->innovation_var == 0.0f || akf->innovation_var == akf->R_base) {
        /* First meaningful measurement - initialize with actual innovation */
        akf->innovation_var = innov_magnitude_sq > 1e-6f ? innov_magnitude_sq : akf->R_base;
    } else {
        /* EMA update: var = alpha*new + (1-alpha)*old */
        akf->innovation_var = akf->innovation_alpha * innov_magnitude_sq +
                             (1.0f - akf->innovation_alpha) * akf->innovation_var;
    }
    
    /* Store current innovation for debugging */
    akf->innovation_x = innov_x;
    akf->innovation_y = innov_y;
    
    /* ===== ADAPTIVE R CALCULATION ===== */
    
    /* Calculate adaptive R scale based on innovation variance
     * 
     * Logic:
     * - Low innovation → predictions accurate → trust measurement more (lower R)
     * - High innovation → predictions poor → trust measurement less (higher R)
     * 
     * R_scale = sqrt(innovation_var / R_base)
     * Using sqrt because innovation_var is squared distance
     */
    float innovation_ratio = sqrtf(akf->innovation_var / akf->R_base);
    
    /* Apply R_scale with bounds */
    akf->R_scale = innovation_ratio;
    if (akf->R_scale < akf->R_scale_min) {
        akf->R_scale = akf->R_scale_min;
    }
    if (akf->R_scale > akf->R_scale_max) {
        akf->R_scale = akf->R_scale_max;
    }
    
    /* Compute actual R for this update */
    float R = akf->R_base * akf->R_scale;
    
    /* ===== UPDATE STEP (X dimension) ===== */
    
    /* Innovation covariance: S = H*P*H^T + R */
    float Sx = P00 + R;
    
    /* Kalman gain: K = P*H^T / S */
    float Kx0 = P00 / Sx;
    float Kx1 = P01 / Sx;
    
    /* State update: x = x_pred + K*innovation */
    akf->state[0] = x_pred + Kx0 * innov_x;
    akf->state[1] = vx_pred + Kx1 * innov_x;
    
    /* Covariance update: P = (I - K*H)*P */
    akf->P[0][0] = P00 - Kx0*Sx*Kx0;
    akf->P[0][1] = akf->P[1][0] = P01 - Kx0*Sx*Kx1;
    akf->P[1][1] = P11 - Kx1*Sx*Kx1;
    
    /* ===== UPDATE STEP (Y dimension) ===== */
    
    float Sy = P22 + R;
    float Ky2 = P22 / Sy;
    float Ky3 = P23 / Sy;
    
    akf->state[2] = y_pred + Ky2 * innov_y;
    akf->state[3] = vy_pred + Ky3 * innov_y;
    
    akf->P[2][2] = P22 - Ky2*Sy*Ky2;
    akf->P[2][3] = akf->P[3][2] = P23 - Ky2*Sy*Ky3;
    akf->P[3][3] = P33 - Ky3*Sy*Ky3;
    
    /* Output */
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

/* End of file -------------------------------------------------------- */