/**
 * @file       icm45686.h
 * @copyright
 * @license
 * @version    0.1.0
 * @date       2025
 * @author
 * @brief      ICM-45686 6-axis IMU driver với low-noise configuration
 * @note       SPI mode 0/3, max 24MHz, MSB first
 * @example    None
 */

/* Define to prevent recursive inclusion ------------------------------------ */
#ifndef __ICM45686_H
#define __ICM45686_H

/* Public includes ---------------------------------------------------------- */
#include <stdbool.h>
#include <stdint.h>

/* Public defines ----------------------------------------------------------- */
/* Key registers - Bank 0 */
#define ICM_REG_WHO_AM_I           0x75
#define ICM_REG_PWR_MGMT0          0x4E
#define ICM_REG_GYRO_CONFIG0       0x4F
#define ICM_REG_ACCEL_CONFIG0      0x50
#define ICM_REG_GYRO_CONFIG1       0x51
#define ICM_REG_ACCEL_CONFIG1      0x53
#define ICM_REG_INT_CONFIG         0x14
#define ICM_REG_INT_CONFIG0        0x63
#define ICM_REG_INT_CONFIG1        0x64
#define ICM_REG_INT_SOURCE0        0x65
#define ICM_REG_INT_STATUS         0x2D
#define ICM_REG_TEMP_DATA1         0x1D
#define ICM_REG_ACCEL_DATA_X1      0x1F
#define ICM_REG_ACCEL_DATA_X0      0x20
#define ICM_REG_GYRO_DATA_X1       0x25
#define ICM_REG_GYRO_DATA_X0       0x26
#define ICM_REG_SIGNAL_PATH_RESET  0x4B
#define ICM_REG_INTF_CONFIG1       0x4D
#define ICM_REG_REG_BANK_SEL       0x76

/* Bank 1 registers - High resolution mode */
#define ICM_REG_INTF_CONFIG5       0x7B  /* Bank 1 */
#define ICM_REG_GYRO_CONFIG_STATIC2 0x0B /* Bank 1 - UI filter order */
#define ICM_REG_GYRO_CONFIG_STATIC3 0x0C /* Bank 1 - Notch filter */
#define ICM_REG_GYRO_CONFIG_STATIC4 0x0D /* Bank 1 - Notch filter */
#define ICM_REG_GYRO_CONFIG_STATIC5 0x0E /* Bank 1 - Notch filter */

/* Bank 2 registers - Accel notch filter */
#define ICM_REG_ACCEL_CONFIG_STATIC2 0x03 /* Bank 2 - Notch filter */
#define ICM_REG_ACCEL_CONFIG_STATIC3 0x04 /* Bank 2 - Notch filter */
#define ICM_REG_ACCEL_CONFIG_STATIC4 0x05 /* Bank 2 - Notch filter */

#define ICM_WHO_AM_I_VALUE         0xE9

/* High resolution mode definitions */
#define ICM_ACCEL_UI_FS_MAX_4G     /* Max ±4g for 20-bit mode */
#define ICM_GYRO_UI_FS_MAX_250DPS  /* Max ±250dps for 20-bit mode */

/* Public enumerate/structure ----------------------------------------------- */
typedef enum
{
  ICM_OK = 0,
  ICM_ERR,
  ICM_ERR_PARAM,
  ICM_ERR_TIMEOUT,
} icm_err_t;

/* Gyro full scale: bits[7:5] of GYRO_CONFIG0
 * 0=±2000dps, 1=±1000dps, 2=±500dps, 3=±250dps
 */
typedef enum
{
  ICM_GYRO_FS_2000 = 0,
  ICM_GYRO_FS_1000 = 1,
  ICM_GYRO_FS_500  = 2,
  ICM_GYRO_FS_250  = 3,
} icm_gyro_fs_t;

/* Accel full scale: bits[7:6] of ACCEL_CONFIG0
 * 0=±16g, 1=±8g, 2=±4g, 3=±2g
 */
typedef enum
{
  ICM_ACCEL_FS_16G = 0,
  ICM_ACCEL_FS_8G  = 1,
  ICM_ACCEL_FS_4G  = 2,
  ICM_ACCEL_FS_2G  = 3,
} icm_accel_fs_t;

/* ODR: bits[3:0] of GYRO_CONFIG0/ACCEL_CONFIG0
 * 6=1kHz, 7=500Hz, 8=200Hz, 9=100Hz, etc.
 */
typedef enum
{
  ICM_ODR_1000HZ = 6,
  ICM_ODR_500HZ  = 7,
  ICM_ODR_200HZ  = 8,
  ICM_ODR_100HZ  = 9,
} icm_odr_t;

/**
 * @brief  SPI/GPIO binding
 */
typedef struct
{
  void (*set_cs)(bool select);
  bool (*spi_transfer)(const uint8_t *tx, uint8_t *rx, uint16_t length);
  void (*delay_us)(uint32_t us);
} icm_bus_if_t;

typedef struct
{
  int16_t x;
  int16_t y;
  int16_t z;
} icm_axis_raw_t;

typedef struct
{
  icm_axis_raw_t gyro;
  icm_axis_raw_t accel;
  int16_t        temp;
} icm_sensor_data_t;

typedef struct
{
  icm_gyro_fs_t  gyro_fs;
  icm_accel_fs_t accel_fs;
  icm_odr_t      gyro_odr;
  icm_odr_t      accel_odr;
  bool           use_low_noise_mode;  /* true = low noise, false = low power */
  bool           use_high_resolution; /* true = 20-bit mode, false = 16-bit */
} icm_config_t;

/**
 * @brief  Driver instance
 */
typedef struct
{
  icm_bus_if_t      bus;
  icm_config_t      config;
  icm_calibration_t calib;
  volatile bool     data_ready; /* Set by interrupt handler */
} icm45686_t;

/* Public function prototypes ----------------------------------------------- */
icm_err_t icm_read_reg(icm45686_t *dev, uint8_t reg, uint8_t *data, uint16_t len);
icm_err_t icm_write_reg(icm45686_t *dev, uint8_t reg, const uint8_t *data, uint16_t len);

/**
 * @brief  Initialize ICM-45686
 * @note   Configure low-noise mode and high-resolution mode for minimum noise
 */
icm_err_t icm_init(icm45686_t *dev, const icm_config_t *config);

/**
 * @brief  Configure gyro full-scale and ODR
 */
icm_err_t icm_set_gyro_config(icm45686_t *dev, icm_gyro_fs_t fs, icm_odr_t odr);

/**
 * @brief  Configure accel full-scale and ODR
 */
icm_err_t icm_set_accel_config(icm45686_t *dev, icm_accel_fs_t fs, icm_odr_t odr);

/**
 * @brief  Configure all filters (low-noise, high-res, notch, UI filter)
 * @note   Single function to configure all filter settings
 */
icm_err_t icm_set_filter_config(icm45686_t *dev, const icm_filter_config_t *filter);

/**
 * @brief  Run self-test
 * @note   Self-test verifies sensor is working properly
 *         Should run at startup or periodically
 */
icm_err_t icm_run_selftest(icm45686_t *dev, icm_selftest_result_t *result);

/**
 * @brief  Calibrate gyroscope zero-rate offset
 * @note   Keep sensor stationary during calibration
 * @param  num_samples  Number of samples to average (recommended: 100-500)
 */
icm_err_t icm_calibrate_gyro_offset(icm45686_t *dev, uint16_t num_samples);

/**
 * @brief  Calibrate accelerometer offset
 * @note   Place sensor flat (Z-axis pointing up) during calibration
 *         This calibrates X/Y to 0g and Z to 1g
 * @param  num_samples  Number of samples to average
 */
icm_err_t icm_calibrate_accel_offset(icm45686_t *dev, uint16_t num_samples);

/**
 * @brief  Set calibration data manually
 * @note   Use this to restore calibration from non-volatile memory
 */
icm_err_t icm_set_calibration(icm45686_t *dev, const icm_calibration_t *calib);

/**
 * @brief  Get current calibration data
 * @note   Use this to save calibration to non-volatile memory
 */
icm_err_t icm_get_calibration(icm45686_t *dev, icm_calibration_t *calib);

/**
 * @brief  Get sensor status flags
 */
icm_err_t icm_get_status(icm45686_t *dev, icm_status_t *status);

/**
 * @brief  Convert raw gyro data to degrees per second (dps)
 */
void      icm_convert_gyro_to_dps(const icm45686_t *dev, const icm_axis_raw_t *raw, icm_axis_float_t *dps);

/**
 * @brief  Convert raw accel data to g (gravity units)
 */
void      icm_convert_accel_to_g(const icm45686_t *dev, const icm_axis_raw_t *raw, icm_axis_float_t *g);

/**
 * @brief  Convert raw temperature to Celsius
 */
float     icm_convert_temp_to_celsius(int16_t raw_temp);

/**
 * @brief  Get gyroscope data
 */
icm_err_t icm_get_gyro(icm45686_t *dev, icm_axis_raw_t *gyro);

/**
 * @brief  Get accelerometer data
 */
icm_err_t icm_get_accel(icm45686_t *dev, icm_axis_raw_t *accel);

/**
 * @brief  Get temperature data
 */
icm_err_t icm_get_temp(icm45686_t *dev, int16_t *temp);

/**
 * @brief  Get all sensor data (accel, gyro, temp) in one read
 * @note   Burst read from 0x1F to reduce SPI overhead
 *         Supports both 16-bit and 20-bit mode
 *         Calibration is automatically applied
 */
icm_err_t icm_get_all_data(icm45686_t *dev, icm_sensor_data_t *data);

/**
 * @brief  Setup interrupt for data ready
 */
icm_err_t icm_setup_interrupt(icm45686_t *dev);

/**
 * @brief  Clear interrupt status
 */
icm_err_t icm_clear_interrupt(icm45686_t *dev);

/**
 * @brief  Interrupt handler - call from EXTI callback
 * @note   Set data_ready flag for main loop processing
 */
void      icm_irq_handler(icm45686_t *dev);

/* -------------------------------------------------------------------------- */

#endif /* __ICM45686_H */

/* End of file -------------------------------------------------------------- */