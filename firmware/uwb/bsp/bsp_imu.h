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
#include "main.h"

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
	#define BSP_IMU_CS_GPIO_PORT  GPIOA
	#define BSP_IMU_CS_GPIO_PIN   GPIO_PIN_4
#endif

/* =============================================================================
 * SENSOR CONFIGURATION
 * ============================================================================= */
/** Gyroscope full-scale range.  See icm42688_gyro_fs_t in icm42688.h. */
#ifndef BSP_IMU_GYRO_FS
	#define BSP_IMU_GYRO_FS         ICM42688_GYRO_FS_2000DPS
#endif

/** Accelerometer full-scale range.  See icm42688_accel_fs_t. */
#ifndef BSP_IMU_ACCEL_FS
	#define BSP_IMU_ACCEL_FS        ICM42688_ACCEL_FS_16G
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
	#define BSP_IMU_CALIB_DATA  (true)
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

/* =============================================================================
 * FILTER CONFIGURATION
 * ============================================================================= */
#define BSP_IMU_FILTER_COMPLEMENTARY
/* #define BSP_IMU_FILTER_MADGWICK */
/* #define BSP_IMU_FILTER_MAHONY   */

/* --- Complementary filter params --- */
#define BSP_IMU_COMP_ALPHA        (0.98f)   /* 0.0~1.0: càng gần 1 càng tin gyro */
#define BSP_IMU_DT_S              (0.05f)  /* dt tương ứng ODR 1kHz */

/* --- Madgwick filter params --- */
#define BSP_IMU_MADGWICK_BETA     (0.1f)    /* gradient descent step, thường 0.01~0.1 */

/* --- Mahony filter params --- */
#define BSP_IMU_MAHONY_KP         (2.0f)    /* proportional gain */
#define BSP_IMU_MAHONY_KI         (0.005f)  /* integral gain */

/* Public enumerate/structure ----------------------------------------- */
typedef icm42688_err_t          bsp_imu_err_t;

#define BSP_IMU_OK            ICM42688_OK
#define BSP_IMU_ERR           ICM42688_ERR
#define BSP_IMU_ERR_PARAM     ICM42688_ERR_PARAM
#define BSP_IMU_ERR_TIMEOUT   ICM42688_ERR_TIMEOUT
#define BSP_IMU_ERR_WHO_AM_I  ICM42688_ERR_WHO_AM_I

/**
 * @brief Euler angles (degrees)
 */
typedef struct
{
    float roll;   /* rotation around X axis, degrees */
    float pitch;  /* rotation around Y axis, degrees */
    float yaw;    /* rotation around Z axis, degrees (gyro integrated, drifts) */
    float temp;
} bsp_imu_data_t;

/**
 * @brief Filter internal state — khởi tạo zero trước khi dùng
 */
typedef struct
{
#if defined(BSP_IMU_FILTER_MAHONY)
    float integralFBx;  /* Mahony integral feedback */
    float integralFBy;
    float integralFBz;
#endif
    float q0, q1, q2, q3;  /* quaternion (Madgwick / Mahony) */
} bsp_imu_filter_state_t;

/* Public variables --------------------------------------------------- */

/* Public function prototypes ----------------------------------------- */
bsp_imu_err_t bsp_imu_init(void);

bsp_imu_err_t bsp_imu_get_data(bsp_imu_data_t *euler_data,
                                icm42688_sensor_data_t *sensor_data,
                                bsp_imu_filter_state_t *state);

bsp_imu_err_t bsp_imu_soft_reset();

bsp_imu_err_t bsp_imu_self_test();

bsp_imu_err_t bsp_imu_setup_interrupt();

bsp_imu_err_t bsp_imu_clear_interrupt();

bsp_imu_err_t bsp_imu_irq_handler();

bool bsp_imu_is_data_ready();

#endif /* __BSP_IMU_H */

/* End of file -------------------------------------------------------- */
