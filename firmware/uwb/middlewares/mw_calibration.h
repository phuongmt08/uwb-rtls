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

#endif /* __MW_CALIBRATION_H */
