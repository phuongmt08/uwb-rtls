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

static void mahal_history_push(mahalanobis_anchor_state_t *state, float value)
{
    state->history[state->index] = value;
    state->index = (uint8_t)((state->index + 1U) % MW_FILTER_MAHAL_HISTORY_WINDOW);
    if (state->count < MW_FILTER_MAHAL_HISTORY_WINDOW) {
        state->count++;
    }
}

static float mahal_history_median(const mahalanobis_anchor_state_t *state)
{
    float sorted[MW_FILTER_MAHAL_HISTORY_WINDOW];
    for (uint8_t i = 0; i < state->count; i++) {
        sorted[i] = state->history[i];
    }

    for (uint8_t i = 1; i < state->count; i++) {
        float key = sorted[i];
        int j = i - 1;
        while (j >= 0 && sorted[j] > key) {
            sorted[j + 1] = sorted[j];
            j--;
        }
        sorted[j + 1] = key;
    }

    if (state->count == 0U) {
        return 0.0f;
    }
    if ((state->count & 1U) != 0U) {
        return sorted[state->count / 2U];
    }

    uint8_t mid = state->count / 2U;
    return 0.5f * (sorted[mid - 1U] + sorted[mid]);
}

static float mahal_history_variance(const mahalanobis_anchor_state_t *state)
{
    if (state->count == 0U) {
        return 0.0f;
    }

    float mean = 0.0f;
    for (uint8_t i = 0; i < state->count; i++) {
        mean += state->history[i];
    }
    mean /= (float)state->count;

    float variance = 0.0f;
    for (uint8_t i = 0; i < state->count; i++) {
        float err = state->history[i] - mean;
        variance += err * err;
    }
    return variance / (float)state->count;
}

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
        ctx->anchors[i].count = 0;
        ctx->anchors[i].index = 0;
        ctx->anchors[i].rejected = false;
        for (uint8_t j = 0; j < MW_FILTER_MAHAL_HISTORY_WINDOW; j++) {
            ctx->anchors[i].history[j] = 0.0f;
        }
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

    (void)px;
    (void)py;
    (void)pz;
    (void)ax;
    (void)ay;
    (void)az;

    mahalanobis_anchor_state_t *state = &ctx->anchors[anchor_id];

    /* Cold-start: seed clean history with valid raw readings before gating. */
    if (state->count < MW_FILTER_MAHAL_COLD_START) {
        mahal_history_push(state, d_raw);
        if (d_out) *d_out = d_raw;
        if (d2_score) *d2_score = 0.0f;
        if (R_adaptive) *R_adaptive = ctx->R_base;
        return true;
    }

    float d_pred = mahal_history_median(state);
    float variance = mahal_history_variance(state);
    float vel_mag = sqrtf(vx * vx + vy * vy + vz * vz);
    const float k_vel = MAHALANOBIS_PREFILTER_VELOCITY_WEIGHT;
    float S = fmaxf(variance, ctx->R_base) + (k_vel * vel_mag);
    if (S < MAHALANOBIS_PREFILTER_MIN_COVARIANCE) {
        S = MAHALANOBIS_PREFILTER_MIN_COVARIANCE;
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

    mahal_history_push(state, d_raw);

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
