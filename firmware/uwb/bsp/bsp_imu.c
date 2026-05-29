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

/* Private defines ---------------------------------------------------------- */
#define GRAVITY							9.80665f
#define DEG2RAD							3.14159265358979323846f / 180.0f

/* Public vriables --------------------------------------------------- */
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
static bool s_initialized = false;

bsp_imu_err_t bsp_imu_init(void)
{
	bsp_imu.bus.set_cs       = bsp_cs_set;
	bsp_imu.bus.spi_transfer = bsp_spi_transfer;
	bsp_imu.bus.delay_us     = bsp_delay_us;
	bsp_imu.bus.delay_ms     = bsp_delay_ms;

	bsp_imu_err_t ret = icm42688_init(&bsp_imu, &s_default_cfg);
	if (ret == BSP_IMU_OK)
	{
		s_initialized = true;
	}
	else
	{
		s_initialized = false;
	}
	return ret;
}

bsp_imu_err_t bsp_imu_get_raw_data(bsp_imu_data_t *p_imu_data)
{
	CHECK_ERR(p_imu_data != NULL, BSP_IMU_ERR);

	icm42688_sensor_data_t raw_data;

	CHECK_ERR(icm42688_get_raw_data(&bsp_imu, &raw_data) == ICM42688_OK, BSP_IMU_ERR);

	p_imu_data->ax = raw_data.accel.x * GRAVITY;
	p_imu_data->ay = raw_data.accel.y * GRAVITY;
	p_imu_data->gz = raw_data.gyro.z * DEG2RAD;

    return BSP_IMU_OK;
}

bsp_imu_err_t bsp_imu_get_bias_data(bsp_imu_bias_t *p_bias)
{
	CHECK_ERR(p_bias != NULL, BSP_IMU_ERR);

	icm42688_calibration_t calib_data;

	CHECK_ERR(icm42688_get_calibration(&bsp_imu, &calib_data) == ICM42688_OK, BSP_IMU_ERR);

	p_bias->bias_ax = calib_data.accel_offset.x * GRAVITY;
	p_bias->bias_ay = calib_data.accel_offset.y * GRAVITY;
	p_bias->bias_gz = calib_data.gyro_offset.z * DEG2RAD;

	return BSP_IMU_OK;
}

bsp_imu_err_t bsp_imu_setup_interrupt()
{
	CHECK_ERR(s_initialized, BSP_IMU_ERR);
	return icm42688_setup_interrupt(&bsp_imu);
}

bsp_imu_err_t bsp_imu_clear_interrupt()
{
	CHECK_ERR(s_initialized, BSP_IMU_ERR);
	return icm42688_clear_interrupt(&bsp_imu);
}

bsp_imu_err_t bsp_imu_irq_handler()
{
	CHECK_ERR(s_initialized, BSP_IMU_ERR);
	icm42688_irq_handler(&bsp_imu);

	return BSP_IMU_OK;
}

bool bsp_imu_is_data_ready()
{
	if (!s_initialized)
	{
		return false;
	}
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
	CHECK_ERR(s_initialized, BSP_IMU_ERR);
	return icm42688_soft_reset(&bsp_imu);
}

bsp_imu_err_t bsp_imu_self_test()
{
	CHECK_ERR(s_initialized, BSP_IMU_ERR);
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

bsp_imu_err_t bsp_imu_get_temp(float *temp)
{
	CHECK_ERR(s_initialized, BSP_IMU_ERR);
	CHECK_ERR(temp != NULL, BSP_IMU_ERR);
	icm42688_sensor_data_t raw_data;

	// Burst read raw sensor data (including temperature) from ICM-42688
	CHECK_ERR(icm42688_get_raw_data(&bsp_imu, &raw_data) == ICM42688_OK, BSP_IMU_ERR);

	*temp = raw_data.temp;
	return BSP_IMU_OK;
}

bool bsp_imu_is_initialized(void)
{
	return s_initialized;
}

/* End of file -------------------------------------------------------- */
