#ifndef BSP_IMU_H_
#define BSP_IMU_H_

#include <stdint.h>
#include <stdbool.h>
#include "stm32f4xx_hal.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    BSP_IMU_OK = 0,
    BSP_IMU_ERR = 1
} bsp_imu_err_t;

typedef struct {
    float ax;
    float ay;
    float az;
    float gx;
    float gy;
    float gz;
} bsp_imu_data_t;

typedef struct {
    float bias_ax;
    float bias_ay;
    float bias_az;
    float bias_gx;
    float bias_gy;
    float bias_gz;
} bsp_imu_bias_t;

bool bsp_imu_is_initialized(void);
bsp_imu_err_t bsp_imu_get_bias_data(bsp_imu_bias_t *p_bias);
bsp_imu_err_t bsp_imu_get_raw_data(bsp_imu_data_t *p_data);

/* SIL test injection functions */
void v_imu_init(void);
void v_imu_set_sample(float ax, float ay, float az, float gx, float gy, float gz);
void v_imu_set_bias(float b_ax, float b_ay, float b_az, float b_gx, float b_gy, float b_gz);

#ifdef __cplusplus
}
#endif

#endif /* BSP_IMU_H_ */
