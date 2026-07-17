/**
 * @file       mw_filter.h
 * @version    4.1.0 
 * @date       2026-01-29
 * @author     Phuong Mai
 * @brief      
 */
#ifndef __MW_FILTER_H
#define __MW_FILTER_H

#include <stdint.h>
#include <stdbool.h>
#include "positioning_config.h"

typedef struct {
    float history[5];
    uint8_t count;
    uint8_t index;
} median_filter_1d_t;

#define UKF_INIT_SAMPLES 20
#define UKF_INIT_DISCARD_SAMPLES 10

typedef struct {
    bool rejected;
    uint16_t reject_streak;
} mahalanobis_anchor_state_t;

typedef struct {
    mahalanobis_anchor_state_t anchors[8];
    float T1;     /* Recover threshold */
    float T2;     /* Reject threshold */
    float R_base;
    float R_gate;
    float velocity_weight;
    float min_covariance;
    bool initialized;
} mahalanobis_prefilter_t;

void mw_filter_mahalanobis_init(mahalanobis_prefilter_t *ctx,
                                float T1, float T2, float anchor_R_base,
                                float R_gate, float velocity_weight,
                                float min_covariance);

float mw_filter_median_update(median_filter_1d_t *med, float new_val);

bool mw_filter_mahalanobis_update(mahalanobis_prefilter_t *ctx,
                                  uint8_t anchor_id, float d_raw,
                                  float px, float py, float pz,
                                  float pxx, float pxy, float pyy,
                                  float ax, float ay, float az,
                                  float *d_out, float *d2_score, float *R_adaptive);



typedef struct {
    float x_history[UKF_INIT_SAMPLES];
    float y_history[UKF_INIT_SAMPLES];
    uint8_t count;
} ukf_init_filter_t;

typedef struct {
    float d_history[3][UKF_INIT_SAMPLES];
    uint8_t count;
} ukf_init_distance_filter_t;

void mw_filter_ukf_init_reset(ukf_init_filter_t *ctx);
bool mw_filter_ukf_init_add(ukf_init_filter_t *ctx, float x, float y, float *out_x, float *out_y);

void mw_filter_ukf_init_distance_reset(ukf_init_distance_filter_t *ctx);
bool mw_filter_ukf_init_distance_add(ukf_init_distance_filter_t *ctx, float d0, float d1, float d2, float *out_d0, float *out_d1, float *out_d2);

#endif /* __MW_FILTER_H */
