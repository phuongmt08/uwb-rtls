/**
 * @file       mw_calibration.c
 * @version    1.0.0
 * @date       2026-04-27
 * @author     Phuong Mai
 * @brief      Shared binary-search based distance auto-calibration core.
 */

#include "mw_calibration.h"

#include <math.h>
#include <string.h>

static uint16_t mw_calib_clamp_u16_from_i32(int32_t value)
{
    if (value < 0) {
        return 0U;
    }
    if (value > 65535) {
        return 65535U;
    }
    return (uint16_t)value;
}

void mw_calib_reset(mw_calib_ctx_t *ctx,
                    const mw_calib_config_t *cfg,
                    uint16_t initial_delay)
{
    if (!ctx || !cfg) {
        return;
    }

    memset(ctx, 0, sizeof(*ctx));

    ctx->samples_per_round = cfg->samples_per_round;
    if (ctx->samples_per_round == 0U) {
        ctx->samples_per_round = 1U;
    }
    if (ctx->samples_per_round > MW_CALIB_MAX_SAMPLES) {
        ctx->samples_per_round = MW_CALIB_MAX_SAMPLES;
    }

    ctx->min_valid_distance_m = cfg->min_valid_distance_m;
    ctx->max_valid_distance_m = cfg->max_valid_distance_m;
    ctx->error_threshold_m = cfg->error_threshold_m;
    ctx->min_delta_step = cfg->min_delta_step;
    ctx->max_rounds = cfg->max_rounds;
    ctx->max_std_m = cfg->max_std_m;

    ctx->current_delay = initial_delay;
    ctx->delta_step = cfg->initial_delta_step;
    ctx->last_error = cfg->initial_last_error;
    ctx->converged = false;
    ctx->done_by_threshold = false;
}

bool mw_calib_add_sample(mw_calib_ctx_t *ctx, float distance_m)
{
    if (!ctx) {
        return false;
    }

    if (ctx->count >= ctx->samples_per_round) {
        return true;
    }

    if (distance_m < ctx->min_valid_distance_m ||
        distance_m > ctx->max_valid_distance_m) {
        return false;
    }

    ctx->distances[ctx->count++] = distance_m;
    return (ctx->count >= ctx->samples_per_round);
}

mw_calib_step_result_t mw_calib_calculate_and_adjust(mw_calib_ctx_t *ctx,
                                                      float ref_distance_m)
{
    if (!ctx) {
        return MW_CALIB_STEP_NOT_READY;
    }

    if (ctx->count < ctx->samples_per_round) {
        return MW_CALIB_STEP_NOT_READY;
    }

    float sum = 0.0f;
    for (uint16_t i = 0; i < ctx->count; i++) {
        sum += ctx->distances[i];
    }
    ctx->mean = sum / ctx->count;

    float variance = 0.0f;
    for (uint16_t i = 0; i < ctx->count; i++) {
        float diff = ctx->distances[i] - ctx->mean;
        variance += diff * diff;
    }
    ctx->std_dev = sqrtf(variance / ctx->count);

    if (ctx->std_dev > ctx->max_std_m) {
        ctx->count = 0;
        return MW_CALIB_STEP_REJECTED_STD;
    }

    ctx->error = ctx->mean - ref_distance_m;
    ctx->round++;

    if (fabsf(ctx->error) < ctx->error_threshold_m) {
        ctx->converged = true;
        ctx->done_by_threshold = true;
        return MW_CALIB_STEP_DONE;
    }

    if (ctx->round >= ctx->max_rounds ||
        ctx->delta_step < ctx->min_delta_step) {
        ctx->converged = true;
        ctx->done_by_threshold = false;
        return MW_CALIB_STEP_DONE;
    }

    if ((ctx->error * ctx->last_error) < 0.0f) {
        ctx->delta_step = (uint16_t)(ctx->delta_step / 2U);
    }

    int32_t new_delay;
    if (ctx->error > 0.0f) {
        new_delay = (int32_t)ctx->current_delay + (int32_t)ctx->delta_step;
    } else {
        new_delay = (int32_t)ctx->current_delay - (int32_t)ctx->delta_step;
    }

    ctx->last_error = ctx->error;
    ctx->current_delay = mw_calib_clamp_u16_from_i32(new_delay);
    ctx->count = 0;

    return MW_CALIB_STEP_ADJUSTED;
}

bool mw_calib_compute_stats(mw_calib_ctx_t *ctx,
                             float *mean_out, float *std_out)
{
    if (!ctx || ctx->count < ctx->samples_per_round) {
        return false;
    }

    float sum = 0.0f;
    for (uint16_t i = 0; i < ctx->count; i++) {
        sum += ctx->distances[i];
    }
    ctx->mean = sum / (float)ctx->count;

    float var = 0.0f;
    for (uint16_t i = 0; i < ctx->count; i++) {
        float d = ctx->distances[i] - ctx->mean;
        var += d * d;
    }
    ctx->std_dev = sqrtf(var / (float)ctx->count);

    if (mean_out) { *mean_out = ctx->mean; }
    if (std_out)  { *std_out  = ctx->std_dev; }

    if (ctx->std_dev > ctx->max_std_m) {
        ctx->count = 0; /* discard noisy batch, caller retries */
        return false;
    }

    return true;
}

/* ------------------------------------------------------------------ */
/* A2A Gradient Calibration                                             */
/* ------------------------------------------------------------------ */

void mw_calib_a2a_init(mw_calib_a2a_ctx_t *ctx,
                        uint16_t initial_combined_delay)
{
    if (!ctx) { return; }
    memset(ctx, 0, sizeof(*ctx));
    ctx->combined_delay = initial_combined_delay;
}

void mw_calib_a2a_accum_pair(mw_calib_a2a_ctx_t *ctx,
                               float meas_mean_m, float d_known_m)
{
    if (!ctx) { return; }
    ctx->pair_error_sum += (meas_mean_m - d_known_m);
    ctx->pair_error_count++;
}

bool mw_calib_a2a_apply_gradient(mw_calib_a2a_ctx_t *ctx)
{
    if (!ctx || ctx->pair_error_count == 0U) { return false; }

    float avg_error = ctx->pair_error_sum / (float)ctx->pair_error_count;
    ctx->last_avg_error = avg_error;

    /* delta = damping x avg_error x (DW_units/m) x 0.5
     * x0.5: this anchor carries half the TWR combined delay;
     * peer anchor delay is NOT updated here.                           */
    int32_t delta = (int32_t)(MW_CALIB_A2A_DAMPING
                               * avg_error
                               * MW_CALIB_A2A_M_TO_DW
                               * 0.5f);

    int32_t new_delay = (int32_t)ctx->combined_delay - delta;
    if (new_delay < (int32_t)MW_CALIB_A2A_ANT_MIN) {
        new_delay = (int32_t)MW_CALIB_A2A_ANT_MIN;
    }
    if (new_delay > (int32_t)MW_CALIB_A2A_ANT_MAX) {
        new_delay = (int32_t)MW_CALIB_A2A_ANT_MAX;
    }

    ctx->combined_delay   = (uint16_t)new_delay;
    ctx->pair_error_sum   = 0.0f;
    ctx->pair_error_count = 0U;
    ctx->iter++;

    ctx->done = (ctx->iter >= MW_CALIB_A2A_ITERATIONS);
    return ctx->done;
}