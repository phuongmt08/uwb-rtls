/**
 * @file       bsp_imu.h
 * @copyright  [Your Copyright]
 * @license    [Your License]
 * @version    1.0.0
 * @date       2026-03-04
 * @author     Dong Son
 *
 * @brief
 **/

/* Define to prevent recursive inclusion ------------------------------ */
#ifndef __BSP_IMU_H
#define __BSP_IMU_H

/* Includes ----------------------------------------------------------- */
#include "icm42688.h"
#include "stm32f4xx_hal.h"

/* Public defines ----------------------------------------------------- */
/* =============================================================================
 * HARDWARE CONFIGURATION
* ============================================================================= */

/* --- SPI peripheral ------------------------------------------------- */
#ifndef BSP_IMU_SPI_HANDLE
	#define BSP_IMU_SPI_HANDLE    hspi1
#endif

#ifndef BSP_IMU_SPI_TIMEOUT_MS
	#define BSP_IMU_SPI_TIMEOUT_MS  (100U)
#endif

/* --- Chip-select GPIO ----------------------------------------------- */
#ifndef BSP_IMU_CS_GPIO_PORT
	#define BSP_IMU_CS_GPIO_PORT  GPIOB
	#define BSP_IMU_CS_GPIO_PIN   GPIO_PIN_13
#endif

/* =============================================================================
 * SENSOR CONFIGURATION
 * ============================================================================= */
/** Gyroscope full-scale range.  See icm42688_gyro_fs_t in icm42688.h. */
#ifndef BSP_IMU_GYRO_FS
	#define BSP_IMU_GYRO_FS         ICM42688_GYRO_FS_250DPS
#endif

/** Accelerometer full-scale range.  See icm42688_accel_fs_t. */
#ifndef BSP_IMU_ACCEL_FS
	#define BSP_IMU_ACCEL_FS        ICM42688_ACCEL_FS_4G
#endif

/** Gyroscope output data rate.  See icm42688_odr_t. */
#ifndef BSP_IMU_GYRO_ODR
	#define BSP_IMU_GYRO_ODR        ICM42688_ODR_1KHZ
#endif

/** Accelerometer output data rate.  See icm42688_odr_t. */
#ifndef BSP_IMU_ACCEL_ODR
	#define BSP_IMU_ACCEL_ODR       ICM42688_ODR_1KHZ
#endif

/** Gyroscope anti-alias filter bandwidth.  See icm42688_aaf_t. */
#ifndef BSP_IMU_GYRO_AAF
	#define BSP_IMU_GYRO_AAF        ICM42688_AAF_258HZ
#endif

/** Accelerometer anti-alias filter bandwidth.  See icm42688_aaf_t. */
#ifndef BSP_IMU_ACCEL_AAF
	#define BSP_IMU_ACCEL_AAF       ICM42688_AAF_258HZ
#endif

#ifndef BSP_IMU_GYRO_UI_FILT_BW
    #define BSP_IMU_GYRO_UI_FILT_BW     ICM42688_UI_FILT_BW_ODR_4
#endif

#ifndef BSP_IMU_ACCEL_UI_FILT_BW
    #define BSP_IMU_ACCEL_UI_FILT_BW    ICM42688_UI_FILT_BW_ODR_4
#endif

#ifndef BSP_IMU_GYRO_UI_FILT_ORD
    #define BSP_IMU_GYRO_UI_FILT_ORD    ICM42688_UI_FILT_ORD_1ST
#endif

#ifndef BSP_IMU_ACCEL_UI_FILT_ORD
    #define BSP_IMU_ACCEL_UI_FILT_ORD   ICM42688_UI_FILT_ORD_1ST
#endif

#ifndef BSP_IMU_TEMP_FILT_BW
    #define BSP_IMU_TEMP_FILT_BW        ICM42688_TEMP_FILT_BW_4000HZ
#endif

/**
 * true  → Low-Noise mode (accel LN + gyro LN) — lower noise, higher current.
 * false → Low-Power mode (accel LP + gyro LN).
 */
#ifndef BSP_IMU_LOW_NOISE_MODE
	#define BSP_IMU_LOW_NOISE_MODE  (true)
#endif

#ifndef BSP_IMU_CALIB_DATA
	#define BSP_IMU_CALIB_DATA  (false)
#endif

#ifndef BSP_IMU_ENABLE_GYRO_FILTER
	#define BSP_IMU_ENABLE_GYRO_FILTER  (true)
#endif

#ifndef BSP_IMU_ENABLE_ACCEL_FILTER
	#define BSP_IMU_ENABLE_ACCEL_FILTER  (true)
#endif

#ifndef M_PI
	#define M_PI  3.1415f
#endif

/* Public enumerate/structure ----------------------------------------- */
typedef icm42688_err_t          bsp_imu_err_t;

#define BSP_IMU_OK            ICM42688_OK
#define BSP_IMU_ERR           ICM42688_ERR
#define BSP_IMU_ERR_PARAM     ICM42688_ERR_PARAM
#define BSP_IMU_ERR_TIMEOUT   ICM42688_ERR_TIMEOUT
#define BSP_IMU_ERR_WHO_AM_I  ICM42688_ERR_WHO_AM_I

typedef struct
{
    float ax;
    float ay;
    float gz;
} bsp_imu_data_t;

typedef struct
{
    float bias_ax;
    float bias_ay;
    float bias_gz;
} bsp_imu_bias_t;

/* Public variables --------------------------------------------------- */

/* Public function prototypes ----------------------------------------- */
bsp_imu_err_t bsp_imu_init(void);

bsp_imu_err_t bsp_imu_get_raw_data(bsp_imu_data_t *p_imu_data);

bsp_imu_err_t bsp_imu_get_bias_data(bsp_imu_bias_t *p_bias);

bsp_imu_err_t bsp_imu_soft_reset();

bsp_imu_err_t bsp_imu_self_test();

bsp_imu_err_t bsp_imu_setup_interrupt();

bsp_imu_err_t bsp_imu_clear_interrupt();

bsp_imu_err_t bsp_imu_irq_handler();

bool bsp_imu_is_data_ready();

/**
 * @brief Read internal temperature of ICM-42688 IMU chip.
 * @param[out] temp  Pointer to float to store temperature in C.
 * @return BSP_IMU_OK on success, BSP_IMU_ERR on failure.
 */
bsp_imu_err_t bsp_imu_get_temp(float *temp);

/**
 * @brief  Check if IMU driver was successfully initialized.
 * @retval true if initialized, false otherwise
 */
bool bsp_imu_is_initialized(void);

#endif /* __BSP_IMU_H */

/* End of file -------------------------------------------------------- */
