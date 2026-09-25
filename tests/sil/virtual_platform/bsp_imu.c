#include "bsp_imu.h"
#include <string.h>

static bool s_imu_init = true;
static bsp_imu_data_t s_current_sample = {0};
static bsp_imu_bias_t s_current_bias = {0};

void v_imu_init(void)
{
    s_imu_init = true;
    memset(&s_current_sample, 0, sizeof(s_current_sample));
    memset(&s_current_bias, 0, sizeof(s_current_bias));
}

void v_imu_set_sample(float ax, float ay, float az, float gx, float gy, float gz)
{
    s_current_sample.ax = ax;
    s_current_sample.ay = ay;
    s_current_sample.az = az;
    s_current_sample.gx = gx;
    s_current_sample.gy = gy;
    s_current_sample.gz = gz;
}

void v_imu_set_bias(float b_ax, float b_ay, float b_az, float b_gx, float b_gy, float b_gz)
{
    s_current_bias.bias_ax = b_ax;
    s_current_bias.bias_ay = b_ay;
    s_current_bias.bias_az = b_az;
    s_current_bias.bias_gx = b_gx;
    s_current_bias.bias_gy = b_gy;
    s_current_bias.bias_gz = b_gz;
}

bool bsp_imu_is_initialized(void)
{
    return s_imu_init;
}

bsp_imu_err_t bsp_imu_get_bias_data(bsp_imu_bias_t *p_bias)
{
    if (p_bias == NULL) return BSP_IMU_ERR;
    *p_bias = s_current_bias;
    return BSP_IMU_OK;
}

bsp_imu_err_t bsp_imu_get_raw_data(bsp_imu_data_t *p_data)
{
    if (p_data == NULL) return BSP_IMU_ERR;
    *p_data = s_current_sample;
    return BSP_IMU_OK;
}
