/**
 * @file       mw_filter.h
 * @version    4.1.0 
 * @date       2026-01-29
 * @author     Phuong Mai
 * @brief      DES pre-smoothing + Adaptive Kalman filtering system
 */
#ifndef __MW_FILTER_H
#define __MW_FILTER_H

#include <stdint.h>
#include <stdbool.h>

#define AKF_STATE_SIZE (4)

typedef struct {
    float x, y;
    float vx, vy;
} pos_vel_2d_t;

typedef struct {
    float s_x, b_x;
    float s_y, b_y;
    
    float alpha_base;
    float alpha;
    float beta;
    
    float prev_mx;
    float prev_my;
    float change_ema;
    
    bool initialized;
} des_filter_2d_t;

typedef struct {
    float state[AKF_STATE_SIZE];
    float P[AKF_STATE_SIZE][AKF_STATE_SIZE];
    float dt;
    
    float Q;
    float R_base;
    
    float innovation_x;
    float innovation_y;
    float innovation_var;
    float innovation_alpha;
    
    float R_scale;
    float R_scale_min;
    float R_scale_max;
    
    bool initialized;
} adaptive_kalman_2d_t;

typedef struct {
    des_filter_2d_t des;
    adaptive_kalman_2d_t akf;
} mw_filter_cxt_t;

void mw_filter_des_init(des_filter_2d_t *des,
                        float x0, float y0,
                        float alpha_base,
                        float beta);

void mw_filter_des_update(des_filter_2d_t *des,
                          float mx_raw, float my_raw,
                          float *mx_smooth, float *my_smooth);

void mw_filter_des_reset(des_filter_2d_t *des, float x, float y);

void mw_filter_akf_init(adaptive_kalman_2d_t *akf,
                        float x0, float y0,
                        float dt,
                        float Q,
                        float R_base,
                        float innovation_alpha,
                        float R_scale_min,
                        float R_scale_max);

float mw_filter_akf_update(adaptive_kalman_2d_t *akf,
                           float mx, float my,
                           pos_vel_2d_t *out);

void mw_filter_akf_get_stats(const adaptive_kalman_2d_t *akf,
                             float *innovation_var,
                             float *R_scale);

void mw_filter_init(mw_filter_cxt_t *filter,
                    float x0, float y0,
                    float dt,
                    float des_alpha,
                    float des_beta,
                    float akf_Q,
                    float akf_R_base,
                    float akf_innovation_alpha,
                    float akf_R_scale_min,
                    float akf_R_scale_max);

float mw_filter_update(mw_filter_cxt_t *filter,
                       float mx_raw, float my_raw,
                       pos_vel_2d_t *out);

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