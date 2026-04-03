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

typedef struct {
    float history[5];
    uint8_t count;
    uint8_t index;
} median_filter_1d_t;

typedef struct {
    median_filter_1d_t anchor_medians[8];
    float T1;
    float T2;
    float R_base;
    bool initialized;
} mahalanobis_prefilter_t;

void mw_filter_mahalanobis_init(mahalanobis_prefilter_t *ctx,
                                float T1, float T2, float anchor_R_base);

bool mw_filter_mahalanobis_update(mahalanobis_prefilter_t *ctx,
                                  uint8_t anchor_id, float d_raw,
                                  float px, float py, float pz,
                                  float vx, float vy, float vz,
                                  float ax, float ay, float az,
                                  float *d_out, float *d2_score, float *R_adaptive);

#endif /* __MW_FILTER_H */