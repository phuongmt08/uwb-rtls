/**
 * @file       mw_calibration.h
 * @version    1.0.0
 * @date       2026-04-27
 * @author     Phuong Mai
 * @brief      Shared binary-search based distance auto-calibration core.
 */
#ifndef __MW_CALIBRATION_H
#define __MW_CALIBRATION_H

#include <stdbool.h>
#include <stdint.h>

#define MW_CALIB_MAX_SAMPLES 64U

typedef struct {
    uint16_t samples_per_round;
    float min_valid_distance_m;
    float max_valid_distance_m;
    float error_threshold_m;
    uint16_t min_delta_step;
    uint16_t max_rounds;
    float max_std_m;
    uint16_t initial_delta_step;
    float initial_last_error;
} mw_calib_config_t;

typedef enum {
    MW_CALIB_STEP_NOT_READY = 0,
    MW_CALIB_STEP_REJECTED_STD,
    MW_CALIB_STEP_ADJUSTED,
    MW_CALIB_STEP_DONE
} mw_calib_step_result_t;

typedef struct {
    float distances[MW_CALIB_MAX_SAMPLES];
    uint16_t samples_per_round;
    float min_valid_distance_m;
    float max_valid_distance_m;
    float error_threshold_m;
    uint16_t min_delta_step;
    uint16_t max_rounds;
    float max_std_m;

    uint16_t count;
    float mean;
    float std_dev;
    float error;
    float last_error;
    uint16_t current_delay;
    uint16_t delta_step;
    uint16_t round;
    bool converged;
    bool done_by_threshold;
} mw_calib_ctx_t;

void mw_calib_reset(mw_calib_ctx_t *ctx,
                    const mw_calib_config_t *cfg,
                    uint16_t initial_delay);

bool mw_calib_add_sample(mw_calib_ctx_t *ctx, float distance_m);

mw_calib_step_result_t mw_calib_calculate_and_adjust(mw_calib_ctx_t *ctx,
                                                      float ref_distance_m);

/* Compute mean + std_dev from collected samples without touching delay.
 * Resets count and returns false if buffer not full or std > max_std_m.
 * mean_out / std_out may be NULL.                                           */
bool mw_calib_compute_stats(mw_calib_ctx_t *ctx,
                             float *mean_out, float *std_out);

/* --------------------------------------------------------------------- */
/* A2A Gradient Calibration                                                */
/* Anchor-to-anchor: each anchor ranges with all peers, collects per-pair  */
/* mean errors, then applies one damped gradient step per iteration.        */
/* --------------------------------------------------------------------- */

/* DW1000: 1 unit ≈ 2.345 mm → 1 m error ≈ 426 units total (2 anchors).
 * Each anchor absorbs half → 213 units/m.                                  */
#define MW_CALIB_A2A_M_TO_DW     213.0f
#define MW_CALIB_A2A_DAMPING     0.4f    /* 0.3-0.5: stable, 2 iters enough */
#define MW_CALIB_A2A_ANT_MIN     14000U
#define MW_CALIB_A2A_ANT_MAX     18000U
#define MW_CALIB_A2A_ITERATIONS  2U

typedef struct {
    uint16_t combined_delay;     /* current ANT_TX + ANT_RX total            */
    float    pair_error_sum;     /* Σ (mean_meas - d_known) for this iter    */
    uint8_t  pair_error_count;   /* number of valid pairs accumulated        */
    uint8_t  iter;               /* completed iterations (0-based)           */
    float    last_avg_error;     /* avg error of last gradient step (log)    */
    bool     done;               /* true after all iterations complete       */
} mw_calib_a2a_ctx_t;

void mw_calib_a2a_init(mw_calib_a2a_ctx_t *ctx,
                        uint16_t initial_combined_delay);

/* Accumulate one pair's measured mean and known distance into the context. */
void mw_calib_a2a_accum_pair(mw_calib_a2a_ctx_t *ctx,
                               float meas_mean_m, float d_known_m);

/* Apply gradient step after all pairs are accumulated.
 * Updates combined_delay, resets accumulator for next iteration.
 * Returns true when all iterations are done.                                */
bool mw_calib_a2a_apply_gradient(mw_calib_a2a_ctx_t *ctx);

#endif /* __MW_CALIBRATION_H */