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
                                float T1, float T2, float anchor_R_base)
{
    if (!ctx) return;
    for (uint8_t i = 0; i < 8; i++) {
        ctx->anchor_medians[i].count = 0;
        ctx->anchor_medians[i].index = 0;
        for (uint8_t j = 0; j < 5; j++) ctx->anchor_medians[i].history[j] = 0.0f;
    }
    ctx->T1 = T1;
    ctx->T2 = T2;
    ctx->R_base = anchor_R_base;
    ctx->initialized = true;
}

bool mw_filter_mahalanobis_update(mahalanobis_prefilter_t *ctx,
                                  uint8_t anchor_id, float d_raw,
                                  float px, float py, float pz,
                                  float vx, float vy, float vz,
                                  float ax, float ay, float az,
                                  float *d_out, float *d2_score, float *R_adaptive)
{
    if (!ctx || !ctx->initialized || anchor_id >= 8) return false;

    /* 1. Median Filter */
    float d_meas = mw_filter_median_update(&ctx->anchor_medians[anchor_id], d_raw);

    /* 2. Predict Measurement */
    float dx = px - ax;
    float dy = py - ay;
    float dz = pz - az;
    float d_pred = sqrtf(dx * dx + dy * dy + dz * dz);
    if (d_pred < 0.1f) d_pred = 0.1f; /* Prevent dividing small S, over-trusting close distance */

    /* 3. Compute Innovation */
    float r = d_meas - d_pred;

    /* 4. Compute Mahalanobis Distance */
    float k_pos = 0.02f;
    float k_vel = 0.05f; /* Tuning parameter for IMU drift */
    float vel_mag = sqrtf(vx * vx + vy * vy + vz * vz);
    float S = ctx->R_base + (k_pos * d_pred * d_pred) + (k_vel * vel_mag);
    float d2 = (r * r) / S;

    if (d_out) *d_out = d_meas;
    if (d2_score) *d2_score = d2;

    /* 5. Decision Logic */
    if (d2 < ctx->T1) {
        if (R_adaptive) *R_adaptive = ctx->R_base;
        return true;
    } else if (d2 < ctx->T2) {
        float scale = (d2 / ctx->T1);
        scale = scale * scale; /* Quadratic penalty */
        if (R_adaptive) *R_adaptive = ctx->R_base * scale;
        return true;
    }
    
    /* d2 >= T2 -> Rejected */
    return false;
}

void mw_filter_distance_smoother_init(distance_smoother_t *ctx,
                                      bool enabled,
                                      float alpha,
                                      float jump_limit_m)
{
    if (!ctx) return;

    memset(ctx, 0, sizeof(*ctx));
    ctx->enabled = enabled;
    ctx->alpha = alpha;
    ctx->jump_limit_m = jump_limit_m;
}

void mw_filter_distance_smoother_reset(distance_smoother_t *ctx)
{
    if (!ctx) return;

    for (uint8_t i = 0; i < 8; i++) {
        ctx->anchors[i].initialized = false;
        ctx->anchors[i].filtered_m = 0.0f;
    }
}

float mw_filter_distance_smoother_apply(distance_smoother_t *ctx,
                                        uint8_t anchor_index,
                                        float raw_distance_m)
{
    if (!ctx || !ctx->enabled || anchor_index >= 8) {
        return raw_distance_m;
    }

    anchor_distance_smoother_t *flt = &ctx->anchors[anchor_index];
    if (!flt->initialized) {
        flt->filtered_m = raw_distance_m;
        flt->initialized = true;
        return raw_distance_m;
    }

    float delta = raw_distance_m - flt->filtered_m;
    float bounded_measurement = raw_distance_m;

    if (delta > ctx->jump_limit_m) {
        bounded_measurement = flt->filtered_m + ctx->jump_limit_m;
    } else if (delta < -ctx->jump_limit_m) {
        bounded_measurement = flt->filtered_m - ctx->jump_limit_m;
    }

    flt->filtered_m += ctx->alpha * (bounded_measurement - flt->filtered_m);
    return flt->filtered_m;
}