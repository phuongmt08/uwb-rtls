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

/* ====================================================================
 * UWB Mahalanobis Pre-Filter
 * ==================================================================== */

float mw_filter_median_update(median_filter_1d_t *med, float new_val)
{
    med->history[med->index] = new_val;
    med->index = (med->index + 1) % 5;
    if (med->count < 5) med->count++;

    float sorted[5];
    for (uint8_t i = 0; i < med->count; i++) sorted[i] = med->history[i];

    /* Insertion sort */
    for (uint8_t i = 1; i < med->count; i++) {
        float key = sorted[i];
        int j = i - 1;
        while (j >= 0 && sorted[j] > key) {
            sorted[j + 1] = sorted[j];
            j--;
        }
        sorted[j + 1] = key;
    }

    if (med->count == 0) return new_val;
    if (med->count % 2 == 1) {
        return sorted[med->count / 2];
    } else {
        int mid = med->count / 2;
        return 0.5f * (sorted[mid - 1] + sorted[mid]);
    }
}

void mw_filter_mahalanobis_init(mahalanobis_prefilter_t *ctx,
                                float T1, float T2, float anchor_R_base,
                                float R_gate, float velocity_weight,
                                float min_covariance)
{
    if (!ctx) return;
    for (uint8_t i = 0; i < 8; i++) {
        ctx->anchors[i].rejected = false;
    }
    ctx->T1 = T1;
    ctx->T2 = T2;
    ctx->R_base = anchor_R_base;
    ctx->R_gate = R_gate;
    ctx->velocity_weight = velocity_weight;
    ctx->min_covariance = min_covariance;
    ctx->initialized = true;
}

bool mw_filter_mahalanobis_update(mahalanobis_prefilter_t *ctx,
                                  uint8_t anchor_id, float d_raw,
                                  float px, float py, float pz,
                                  float pxx, float pxy, float pyy,
                                  float ax, float ay, float az,
                                  float *d_out, float *d2_score, float *R_adaptive)
{
    if (!ctx || !ctx->initialized || anchor_id >= 8) return false;

    mahalanobis_anchor_state_t *state = &ctx->anchors[anchor_id];

    if (!isfinite(d_raw) ||
        !isfinite(px) || !isfinite(py) || !isfinite(pz) ||
        !isfinite(pxx) || !isfinite(pxy) || !isfinite(pyy) ||
        !isfinite(ax) || !isfinite(ay) || !isfinite(az)) {
        if (d_out) *d_out = d_raw;
        if (d2_score) *d2_score = INFINITY;
        if (R_adaptive) *R_adaptive = ctx->R_base;
        state->rejected = true;
        return false;
    }

    float dx = px - ax;
    float dy = py - ay;
#if SYS_FUSION_USE_PLANAR_RANGES
    float d_pred = sqrtf((dx * dx) + (dy * dy));
#else
    float dz = pz - az;
    float d_pred = sqrtf((dx * dx) + (dy * dy) + (dz * dz));
#endif
    float dxy = sqrtf((dx * dx) + (dy * dy));
    if (!isfinite(dxy) || dxy < 1.0e-6f) {
        if (d_out) *d_out = d_raw;
        if (d2_score) *d2_score = INFINITY;
        if (R_adaptive) *R_adaptive = ctx->R_base;
        state->rejected = true;
        return false;
    }
    float hx = dx / dxy;
    float hy = dy / dxy;
    /* Scalar range innovation covariance: S = H P H' + R. Position is
     * planar here, matching the distance passed by the fusion task. */
    float projected_cov = (hx * hx * pxx) + (2.0f * hx * hy * pxy)
                        + (hy * hy * pyy);
    float S = projected_cov + ctx->R_gate;
    if (!isfinite(d_pred) || !isfinite(S)) {
        if (d_out) *d_out = d_raw;
        if (d2_score) *d2_score = INFINITY;
        if (R_adaptive) *R_adaptive = ctx->R_base;
        state->rejected = true;
        return false;
    }
    if (S < ctx->min_covariance) {
        S = ctx->min_covariance;
    }

    float r = d_raw - d_pred;
    float d2 = (r * r) / S;

    if (d_out) *d_out = d_raw;
    if (d2_score) *d2_score = d2;

    bool accepted = false;
    if (state->rejected) {
        if (d2 < ctx->T1) {
            state->rejected = false;
            accepted = true;
        }
    } else if (d2 > ctx->T2) {
        state->rejected = true;
    } else {
        accepted = true;
    }

    if (!accepted) {
        return false;
    }

    if (R_adaptive) {
        float scale = 1.0f;
        if (d2 > ctx->T1) {
            scale = d2 / ctx->T1;
            scale = scale * scale;
        }
        *R_adaptive = ctx->R_base * scale;
    }

    return true;
}



/* ====================================================================
 * UKF Initialization Filter (Median over N samples)
 * ==================================================================== */

void mw_filter_ukf_init_reset(ukf_init_filter_t *ctx)
{
    if (!ctx) return;
    ctx->count = 0;
    for (int i = 0; i < UKF_INIT_SAMPLES; i++) {
        ctx->x_history[i] = 0.0f;
        ctx->y_history[i] = 0.0f;
    }
}

static void insertion_sort(float arr[], uint8_t n)
{
    for (uint8_t i = 1; i < n; i++) {
        float key = arr[i];
        int j = i - 1;
        while (j >= 0 && arr[j] > key) {
            arr[j + 1] = arr[j];
            j--;
        }
        arr[j + 1] = key;
    }
}

bool mw_filter_ukf_init_add(ukf_init_filter_t *ctx, float x, float y, float *out_x, float *out_y)
{
    if (!ctx) return false;

    if (ctx->count < UKF_INIT_DISCARD_SAMPLES + UKF_INIT_SAMPLES) {
        if (ctx->count >= UKF_INIT_DISCARD_SAMPLES) {
            ctx->x_history[ctx->count - UKF_INIT_DISCARD_SAMPLES] = x;
            ctx->y_history[ctx->count - UKF_INIT_DISCARD_SAMPLES] = y;
        }
        ctx->count++;
    }

    if (ctx->count < UKF_INIT_DISCARD_SAMPLES + UKF_INIT_SAMPLES) {
        return false;
    }

    /* Compute Median */
    float sorted_x[UKF_INIT_SAMPLES];
    float sorted_y[UKF_INIT_SAMPLES];
    
    for (int i = 0; i < UKF_INIT_SAMPLES; i++) {
        sorted_x[i] = ctx->x_history[i];
        sorted_y[i] = ctx->y_history[i];
    }

    insertion_sort(sorted_x, UKF_INIT_SAMPLES);
    insertion_sort(sorted_y, UKF_INIT_SAMPLES);

    if (out_x) {
        if (UKF_INIT_SAMPLES % 2 == 1) {
            *out_x = sorted_x[UKF_INIT_SAMPLES / 2];
        } else {
            *out_x = 0.5f * (sorted_x[UKF_INIT_SAMPLES / 2 - 1] + sorted_x[UKF_INIT_SAMPLES / 2]);
        }
    }
    
    if (out_y) {
        if (UKF_INIT_SAMPLES % 2 == 1) {
            *out_y = sorted_y[UKF_INIT_SAMPLES / 2];
        } else {
            *out_y = 0.5f * (sorted_y[UKF_INIT_SAMPLES / 2 - 1] + sorted_y[UKF_INIT_SAMPLES / 2]);
        }
    }

    return true; /* Filter completed */
}

void mw_filter_ukf_init_distance_reset(ukf_init_distance_filter_t *ctx)
{
    if (!ctx) return;
    ctx->count = 0;
    for (int i = 0; i < UKF_INIT_SAMPLES; i++) {
        ctx->d_history[0][i] = 0.0f;
        ctx->d_history[1][i] = 0.0f;
        ctx->d_history[2][i] = 0.0f;
    }
}

bool mw_filter_ukf_init_distance_add(ukf_init_distance_filter_t *ctx, float d0, float d1, float d2, float *out_d0, float *out_d1, float *out_d2)
{
    if (!ctx) return false;

    if (ctx->count < UKF_INIT_DISCARD_SAMPLES + UKF_INIT_SAMPLES) {
        if (ctx->count >= UKF_INIT_DISCARD_SAMPLES) {
            ctx->d_history[0][ctx->count - UKF_INIT_DISCARD_SAMPLES] = d0;
            ctx->d_history[1][ctx->count - UKF_INIT_DISCARD_SAMPLES] = d1;
            ctx->d_history[2][ctx->count - UKF_INIT_DISCARD_SAMPLES] = d2;
        }
        ctx->count++;
    }

    if (ctx->count < UKF_INIT_DISCARD_SAMPLES + UKF_INIT_SAMPLES) {
        return false;
    }

    /* Compute Median */
    if (out_d0 && out_d1 && out_d2) {
        float sorted_d0[UKF_INIT_SAMPLES];
        float sorted_d1[UKF_INIT_SAMPLES];
        float sorted_d2[UKF_INIT_SAMPLES];

        for (int i = 0; i < UKF_INIT_SAMPLES; i++) {
            sorted_d0[i] = ctx->d_history[0][i];
            sorted_d1[i] = ctx->d_history[1][i];
            sorted_d2[i] = ctx->d_history[2][i];
        }

        insertion_sort(sorted_d0, UKF_INIT_SAMPLES);
        insertion_sort(sorted_d1, UKF_INIT_SAMPLES);
        insertion_sort(sorted_d2, UKF_INIT_SAMPLES);

        if (UKF_INIT_SAMPLES % 2 == 1) {
            *out_d0 = sorted_d0[UKF_INIT_SAMPLES / 2];
            *out_d1 = sorted_d1[UKF_INIT_SAMPLES / 2];
            *out_d2 = sorted_d2[UKF_INIT_SAMPLES / 2];
        } else {
            *out_d0 = 0.5f * (sorted_d0[UKF_INIT_SAMPLES / 2 - 1] + sorted_d0[UKF_INIT_SAMPLES / 2]);
            *out_d1 = 0.5f * (sorted_d1[UKF_INIT_SAMPLES / 2 - 1] + sorted_d1[UKF_INIT_SAMPLES / 2]);
            *out_d2 = 0.5f * (sorted_d2[UKF_INIT_SAMPLES / 2 - 1] + sorted_d2[UKF_INIT_SAMPLES / 2]);
        }
    }

    return true; /* Filter completed */
}
