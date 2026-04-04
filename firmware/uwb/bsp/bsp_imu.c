/**
 * @file       bsp_imu.c
 * @copyright  [Your Copyright]
 * @license    [Your License]
 * @version    1.0.0
 * @date       2026-03-04
 * @author     Dong Son
 *
 * @brief
 **/

/* Includes ----------------------------------------------------------- */
#include "bsp_imu.h"
#include "bsp_util.h"
#include "err.h"
#include <math.h>

/* Public variables --------------------------------------------------- */
extern SPI_HandleTypeDef BSP_IMU_SPI_HANDLE;
icm42688_dev_t bsp_imu;

/* Private variables -------------------------------------------------- */
static const icm42688_config_t s_default_cfg =
{
	.gyro_fs             	= BSP_IMU_GYRO_FS,
	.accel_fs            	= BSP_IMU_ACCEL_FS,
	.gyro_odr            	= BSP_IMU_GYRO_ODR,
	.accel_odr           	= BSP_IMU_ACCEL_ODR,
	.gyro_aaf   			= BSP_IMU_GYRO_AAF,
	.accel_aaf  			= BSP_IMU_ACCEL_AAF,
	.gyro_filter_enable		= BSP_IMU_ENABLE_GYRO_FILTER,
	.accel_filter_enable	= BSP_IMU_ENABLE_ACCEL_FILTER,
	.gyro_ui_filt_bw     	= BSP_IMU_GYRO_UI_FILT_BW,
	.accel_ui_filt_bw    	= BSP_IMU_ACCEL_UI_FILT_BW,
	.gyro_ui_filt_ord    	= BSP_IMU_GYRO_UI_FILT_ORD,
	.accel_ui_filt_ord   	= BSP_IMU_ACCEL_UI_FILT_ORD,
	.temp_filt_bw        	= BSP_IMU_TEMP_FILT_BW,
	.use_low_noise_mode  	= BSP_IMU_LOW_NOISE_MODE,
	.use_calibrated      	= BSP_IMU_CALIB_DATA,
};

/* Private function prototypes ---------------------------------------- */
static void bsp_cs_set(bool select);
static bool bsp_spi_transfer(const uint8_t *tx, uint8_t *rx, uint16_t length);

/* Private function prototypes ---------------------------------------- */
/* Function definitions ----------------------------------------------- */
bsp_imu_err_t bsp_imu_init(void)
{

	bsp_imu.bus.set_cs       = bsp_cs_set;
	bsp_imu.bus.spi_transfer = bsp_spi_transfer;
	bsp_imu.bus.delay_us     = bsp_delay_us;
	bsp_imu.bus.delay_ms     = bsp_delay_ms;

	return icm42688_init(&bsp_imu, &s_default_cfg);
}

bsp_imu_err_t bsp_imu_get_data(bsp_imu_data_t *euler_data,
                                icm42688_sensor_data_t *sensor_data,
                                bsp_imu_filter_state_t *state)
{
    CHECK_ERR((euler_data != NULL && sensor_data != NULL && state != NULL), BSP_IMU_ERR_PARAM);
    CHECK_ERR(icm42688_get_all_data(&bsp_imu, sensor_data) == ICM42688_OK, BSP_IMU_ERR);

    float gx = sensor_data->gyro.x * ((float)M_PI / 180.0f);  /* dps → rad/s */
    float gy = sensor_data->gyro.y * ((float)M_PI / 180.0f);
    float gz = sensor_data->gyro.z * ((float)M_PI / 180.0f);
    float ax = sensor_data->accel.x;
    float ay = sensor_data->accel.y;
    float az = sensor_data->accel.z;

    euler_data->temp = sensor_data->temp;

#if defined(BSP_IMU_FILTER_COMPLEMENTARY)
    float accel_roll  =  atan2f(ay, az) * (180.0f / (float)M_PI);
    float accel_pitch = -atan2f(ax, sqrtf(ay * ay + az * az)) * (180.0f / (float)M_PI);

    euler_data->roll  = BSP_IMU_COMP_ALPHA * (euler_data->roll  + sensor_data->gyro.x * BSP_IMU_DT_S)
                      + (1.0f - BSP_IMU_COMP_ALPHA) * accel_roll;
    euler_data->pitch = BSP_IMU_COMP_ALPHA * (euler_data->pitch + sensor_data->gyro.y * BSP_IMU_DT_S)
                      + (1.0f - BSP_IMU_COMP_ALPHA) * accel_pitch;
    euler_data->yaw  += sensor_data->gyro.z * BSP_IMU_DT_S;
    if (euler_data->yaw >  180.0f) euler_data->yaw -= 360.0f;
    if (euler_data->yaw < -180.0f) euler_data->yaw += 360.0f;

#elif defined(BSP_IMU_FILTER_MADGWICK)
    float q0 = state->q0, q1 = state->q1, q2 = state->q2, q3 = state->q3;

    /* Normalize accel */
    float norm = sqrtf(ax*ax + ay*ay + az*az);
    if (norm < 1e-6f) return BSP_IMU_ERR;
    ax /= norm; ay /= norm; az /= norm;

    float _2q0 = 2.0f*q0, _2q1 = 2.0f*q1, _2q2 = 2.0f*q2, _2q3 = 2.0f*q3;
    float _4q0 = 4.0f*q0, _4q1 = 4.0f*q1, _4q2 = 4.0f*q2;
    float _8q1 = 8.0f*q1, _8q2 = 8.0f*q2;
    float q0q0 = q0*q0, q1q1 = q1*q1, q2q2 = q2*q2, q3q3 = q3*q3;

    /* Gradient descent */
    float s0 = _4q0*q2q2 + _2q2*ax + _4q0*q1q1 - _2q1*ay;
    float s1 = _4q1*q3q3 - _2q3*ax + 4.0f*q0q0*q1 - _2q0*ay - _4q1 + _8q1*q1q1 + _8q1*q2q2 + _4q1*az;
    float s2 = 4.0f*q0q0*q2 + _2q0*ax + _4q2*q3q3 - _2q3*ay - _4q2 + _8q2*q1q1 + _8q2*q2q2 + _4q2*az;
    float s3 = 4.0f*q1q1*q3 - _2q1*ax + 4.0f*q2q2*q3 - _2q2*ay;

    norm = sqrtf(s0*s0 + s1*s1 + s2*s2 + s3*s3);
    if (norm < 1e-6f) return BSP_IMU_ERR;
    s0 /= norm; s1 /= norm; s2 /= norm; s3 /= norm;

    float qDot0 = 0.5f*(-q1*gx - q2*gy - q3*gz) - BSP_IMU_MADGWICK_BETA*s0;
    float qDot1 = 0.5f*( q0*gx + q2*gz - q3*gy) - BSP_IMU_MADGWICK_BETA*s1;
    float qDot2 = 0.5f*( q0*gy - q1*gz + q3*gx) - BSP_IMU_MADGWICK_BETA*s2;
    float qDot3 = 0.5f*( q0*gz + q1*gy - q2*gx) - BSP_IMU_MADGWICK_BETA*s3;

    q0 += qDot0 * BSP_IMU_DT_S;
    q1 += qDot1 * BSP_IMU_DT_S;
    q2 += qDot2 * BSP_IMU_DT_S;
    q3 += qDot3 * BSP_IMU_DT_S;

    norm = sqrtf(q0*q0 + q1*q1 + q2*q2 + q3*q3);
    state->q0 = q0/norm; state->q1 = q1/norm;
    state->q2 = q2/norm; state->q3 = q3/norm;

    euler_data->roll  =  atan2f(2.0f*(state->q0*state->q1 + state->q2*state->q3),
                                1.0f - 2.0f*(state->q1*state->q1 + state->q2*state->q2))
                         * (180.0f / (float)M_PI);
    euler_data->pitch =  asinf(2.0f*(state->q0*state->q2 - state->q3*state->q1))
                         * (180.0f / (float)M_PI);
    euler_data->yaw   =  atan2f(2.0f*(state->q0*state->q3 + state->q1*state->q2),
                                1.0f - 2.0f*(state->q2*state->q2 + state->q3*state->q3))
                         * (180.0f / (float)M_PI);

#elif defined(BSP_IMU_FILTER_MAHONY)
    float q0 = state->q0, q1 = state->q1, q2 = state->q2, q3 = state->q3;

    float norm = sqrtf(ax*ax + ay*ay + az*az);
    if (norm < 1e-6f) return BSP_IMU_ERR;
    ax /= norm; ay /= norm; az /= norm;

    /* Estimated gravity direction from quaternion */
    float vx = 2.0f*(q1*q3 - q0*q2);
    float vy = 2.0f*(q0*q1 + q2*q3);
    float vz = q0*q0 - q1*q1 - q2*q2 + q3*q3;

    /* Error = cross(accel, estimated_gravity) */
    float ex = ay*vz - az*vy;
    float ey = az*vx - ax*vz;
    float ez = ax*vy - ay*vx;

    state->integralFBx += BSP_IMU_MAHONY_KI * ex * BSP_IMU_DT_S;
    state->integralFBy += BSP_IMU_MAHONY_KI * ey * BSP_IMU_DT_S;
    state->integralFBz += BSP_IMU_MAHONY_KI * ez * BSP_IMU_DT_S;

    gx += BSP_IMU_MAHONY_KP * ex + state->integralFBx;
    gy += BSP_IMU_MAHONY_KP * ey + state->integralFBy;
    gz += BSP_IMU_MAHONY_KP * ez + state->integralFBz;

    float qDot0 = 0.5f*(-q1*gx - q2*gy - q3*gz);
    float qDot1 = 0.5f*( q0*gx + q2*gz - q3*gy);
    float qDot2 = 0.5f*( q0*gy - q1*gz + q3*gx);
    float qDot3 = 0.5f*( q0*gz + q1*gy - q2*gx);

    q0 += qDot0 * BSP_IMU_DT_S;
    q1 += qDot1 * BSP_IMU_DT_S;
    q2 += qDot2 * BSP_IMU_DT_S;
    q3 += qDot3 * BSP_IMU_DT_S;

    norm = sqrtf(q0*q0 + q1*q1 + q2*q2 + q3*q3);
    state->q0 = q0/norm; state->q1 = q1/norm;
    state->q2 = q2/norm; state->q3 = q3/norm;

    euler_data->roll  =  atan2f(2.0f*(state->q0*state->q1 + state->q2*state->q3),
                                1.0f - 2.0f*(state->q1*state->q1 + state->q2*state->q2))
                         * (180.0f / (float)M_PI);
    euler_data->pitch =  asinf(2.0f*(state->q0*state->q2 - state->q3*state->q1))
                         * (180.0f / (float)M_PI);
    euler_data->yaw   =  atan2f(2.0f*(state->q0*state->q3 + state->q1*state->q2),
                                1.0f - 2.0f*(state->q2*state->q2 + state->q3*state->q3))
                         * (180.0f / (float)M_PI);

#else
    #error "BSP_IMU: No filter selected. Define one of: BSP_IMU_FILTER_COMPLEMENTARY / MADGWICK / MAHONY"
#endif

    return BSP_IMU_OK;
}

bsp_imu_err_t bsp_imu_setup_interrupt()
{
	return icm42688_setup_interrupt(&bsp_imu);
}

bsp_imu_err_t bsp_imu_clear_interrupt()
{
	return icm42688_clear_interrupt(&bsp_imu);
}

bsp_imu_err_t bsp_imu_irq_handler()
{
	icm42688_irq_handler(&bsp_imu);

	return BSP_IMU_OK;
}

bool bsp_imu_is_data_ready()
{
	/* Atomically read and clear the flag set by bsp_imu_irq_handler() */
	bool ready = bsp_imu.data_ready;

	if (ready)
	{
		bsp_imu.data_ready = false;
	}

	return ready;
}

bsp_imu_err_t bsp_imu_soft_reset()
{
	return icm42688_soft_reset(&bsp_imu);
}

bsp_imu_err_t bsp_imu_self_test()
{
	return icm42688_self_test(&bsp_imu);
}

/* Private definitions ------------------------------------------------ */
static void bsp_cs_set(bool select)
{
	if (select)
	{
		HAL_GPIO_WritePin(BSP_IMU_CS_GPIO_PORT, BSP_IMU_CS_GPIO_PIN, GPIO_PIN_RESET);
	}
	else
	{
		HAL_GPIO_WritePin(BSP_IMU_CS_GPIO_PORT, BSP_IMU_CS_GPIO_PIN, GPIO_PIN_SET);
	}
}

static bool bsp_spi_transfer(const uint8_t *tx, uint8_t *rx, uint16_t length)
{

	/* Temporary buffers for the NULL side of the transaction */
	uint8_t tx_dummy[64] = {0};
	uint8_t rx_dummy[64];

	const uint8_t *tx_ptr = (tx != NULL) ? tx : tx_dummy;
	uint8_t       *rx_ptr = (rx != NULL) ? rx : rx_dummy;

	/* Guard: driver must not exceed the dummy buffer size */
	if (length > sizeof(tx_dummy))
	{
		return false;
	}

	CHECK_ERR((HAL_SPI_TransmitReceive(&BSP_IMU_SPI_HANDLE,
			(uint8_t *)tx_ptr,
			rx_ptr,
			length,
			BSP_IMU_SPI_TIMEOUT_MS) == HAL_OK), false);

	return true;
}

/* End of file -------------------------------------------------------- */
