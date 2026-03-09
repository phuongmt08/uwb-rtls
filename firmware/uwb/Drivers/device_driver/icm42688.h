/**
 * @file       icm42688.h
 * @copyright
 * @license
 * @version    1.0.0
 * @date       2026-01-10
 * @author     Phuong Mai
 * @brief      ICM-42688-P 6-axis IMU driver (SPI mode)
 * @note       SPI mode 0/3, max 24MHz, MSB first
 *             Based on ICM-42688-P datasheet Rev 1.8
 * @example    None
 */

#ifndef __ICM42688_H
#define __ICM42688_H

/* Public includes ---------------------------------------------------------- */
#include <stdbool.h>
#include <stdint.h>

/* Public defines ----------------------------------------------------------- */
/* Bank 0 Registers (User Bank) */
#define ICM42_REG_DEVICE_CONFIG        0x11
#define ICM42_REG_DRIVE_CONFIG         0x13
#define ICM42_REG_INT_CONFIG           0x14
#define ICM42_REG_FIFO_CONFIG          0x16
#define ICM42_REG_TEMP_DATA1           0x1D
#define ICM42_REG_TEMP_DATA0           0x1E
#define ICM42_REG_ACCEL_DATA_X1        0x1F
#define ICM42_REG_ACCEL_DATA_X0        0x20
#define ICM42_REG_ACCEL_DATA_Y1        0x21
#define ICM42_REG_ACCEL_DATA_Y0        0x22
#define ICM42_REG_ACCEL_DATA_Z1        0x23
#define ICM42_REG_ACCEL_DATA_Z0        0x24
#define ICM42_REG_GYRO_DATA_X1         0x25
#define ICM42_REG_GYRO_DATA_X0         0x26
#define ICM42_REG_GYRO_DATA_Y1         0x27
#define ICM42_REG_GYRO_DATA_Y0         0x28
#define ICM42_REG_GYRO_DATA_Z1         0x29
#define ICM42_REG_GYRO_DATA_Z0         0x2A
#define ICM42_REG_TMST_FSYNCH          0x2B
#define ICM42_REG_TMST_FSYNCL          0x2C
#define ICM42_REG_INT_STATUS           0x2D
#define ICM42_REG_FIFO_COUNTH          0x2E
#define ICM42_REG_FIFO_COUNTL          0x2F
#define ICM42_REG_FIFO_DATA            0x30
#define ICM42_REG_APEX_DATA0           0x31
#define ICM42_REG_INT_STATUS2          0x37
#define ICM42_REG_INT_STATUS3          0x38
#define ICM42_REG_SIGNAL_PATH_RESET    0x4B
#define ICM42_REG_INTF_CONFIG0         0x4C
#define ICM42_REG_INTF_CONFIG1         0x4D
#define ICM42_REG_PWR_MGMT0            0x4E
#define ICM42_REG_GYRO_CONFIG0         0x4F
#define ICM42_REG_ACCEL_CONFIG0        0x50
#define ICM42_REG_GYRO_CONFIG1         0x51
#define ICM42_REG_GYRO_ACCEL_CONFIG0   0x52
#define ICM42_REG_ACCEL_CONFIG1        0x53
#define ICM42_REG_TMST_CONFIG          0x54
#define ICM42_REG_FIFO_CONFIG1         0x5F
#define ICM42_REG_FIFO_CONFIG2         0x60
#define ICM42_REG_FIFO_CONFIG3         0x61
#define ICM42_REG_FSYNC_CONFIG         0x62
#define ICM42_REG_INT_CONFIG0          0x63
#define ICM42_REG_INT_CONFIG1          0x64
#define ICM42_REG_INT_SOURCE0          0x65
#define ICM42_REG_INT_SOURCE1          0x66
#define ICM42_REG_INT_SOURCE3          0x68
#define ICM42_REG_INT_SOURCE4          0x69
#define ICM42_REG_SELF_TEST_CONFIG     0x70
#define ICM42_REG_WHO_AM_I             0x75
#define ICM42_REG_REG_BANK_SEL         0x76

/* Bank 1 Registers */
#define ICM42_REG_SENSOR_CONFIG0       0x03  /* Bank 1 */
#define ICM42_REG_GYRO_CONFIG_STATIC2  0x0B  /* Bank 1 */
#define ICM42_REG_GYRO_CONFIG_STATIC3  0x0C  /* Bank 1 */
#define ICM42_REG_GYRO_CONFIG_STATIC4  0x0D  /* Bank 1 */
#define ICM42_REG_GYRO_CONFIG_STATIC5  0x0E  /* Bank 1 */
#define ICM42_REG_GYRO_CONFIG_STATIC6  0x0F  /* Bank 1 */
#define ICM42_REG_GYRO_CONFIG_STATIC7  0x10  /* Bank 1 */
#define ICM42_REG_GYRO_CONFIG_STATIC8  0x11  /* Bank 1 */
#define ICM42_REG_GYRO_CONFIG_STATIC9  0x12  /* Bank 1 */
#define ICM42_REG_GYRO_CONFIG_STATIC10 0x13  /* Bank 1 */

/* Bank 2 Registers */
#define ICM42_REG_ACCEL_CONFIG_STATIC2 0x03  /* Bank 2 */
#define ICM42_REG_ACCEL_CONFIG_STATIC3 0x04  /* Bank 2 */
#define ICM42_REG_ACCEL_CONFIG_STATIC4 0x05  /* Bank 2 */

/* Bank 4 Registers */
#define ICM42_REG_APEX_CONFIG0         0x56  /* Bank 4 */
#define ICM42_REG_INT_SOURCE6          0x4D  /* Bank 4 */

/* WHO_AM_I value for ICM-42688-P */
#define ICM42_WHO_AM_I_VALUE           0x47

/* PWR_MGMT0 bits */
#define ICM42_PWR_MGMT0_GYRO_MODE_OFF  0x00
#define ICM42_PWR_MGMT0_GYRO_MODE_STDBY 0x04
#define ICM42_PWR_MGMT0_GYRO_MODE_LN   0x0C
#define ICM42_PWR_MGMT0_ACCEL_MODE_OFF 0x00
#define ICM42_PWR_MGMT0_ACCEL_MODE_LP  0x02
#define ICM42_PWR_MGMT0_ACCEL_MODE_LN  0x03
#define ICM42_PWR_MGMT0_TEMP_DIS       0x20

/* Public enumerate/structure ----------------------------------------------- */
typedef enum
{
  ICM42_OK = 0,
  ICM42_ERR,
  ICM42_ERR_PARAM,
  ICM42_ERR_TIMEOUT,
  ICM42_ERR_WHO_AM_I,
} icm42_err_t;

/**
 * @brief Gyro Full Scale Range
 * bits[7:5] of GYRO_CONFIG0
 */
typedef enum
{
  ICM42_GYRO_FS_2000DPS  = 0,  /* ±2000 dps */
  ICM42_GYRO_FS_1000DPS  = 1,  /* ±1000 dps */
  ICM42_GYRO_FS_500DPS   = 2,  /* ±500 dps */
  ICM42_GYRO_FS_250DPS   = 3,  /* ±250 dps */
  ICM42_GYRO_FS_125DPS   = 4,  /* ±125 dps */
  ICM42_GYRO_FS_62_5DPS  = 5,  /* ±62.5 dps */
  ICM42_GYRO_FS_31_25DPS = 6,  /* ±31.25 dps */
  ICM42_GYRO_FS_15_625DPS = 7, /* ±15.625 dps */
} icm42_gyro_fs_t;

/**
 * @brief Accel Full Scale Range
 * bits[7:5] of ACCEL_CONFIG0
 */
typedef enum
{
  ICM42_ACCEL_FS_16G = 0,  /* ±16g */
  ICM42_ACCEL_FS_8G  = 1,  /* ±8g */
  ICM42_ACCEL_FS_4G  = 2,  /* ±4g */
  ICM42_ACCEL_FS_2G  = 3,  /* ±2g */
} icm42_accel_fs_t;

/**
 * @brief Output Data Rate (ODR)
 * bits[3:0] of GYRO_CONFIG0 / ACCEL_CONFIG0
 */
typedef enum
{
  ICM42_ODR_32KHZ   = 1,   /* 32 kHz (LN mode only) */
  ICM42_ODR_16KHZ   = 2,   /* 16 kHz (LN mode only) */
  ICM42_ODR_8KHZ    = 3,   /* 8 kHz (LN mode only) */
  ICM42_ODR_4KHZ    = 4,   /* 4 kHz (LN mode only) */
  ICM42_ODR_2KHZ    = 5,   /* 2 kHz (LN mode only) */
  ICM42_ODR_1KHZ    = 6,   /* 1 kHz (LN mode) */
  ICM42_ODR_200HZ   = 8,   /* 200 Hz */
  ICM42_ODR_100HZ   = 9,   /* 100 Hz */
  ICM42_ODR_50HZ    = 10,  /* 50 Hz */
  ICM42_ODR_25HZ    = 11,  /* 25 Hz */
  ICM42_ODR_12_5HZ  = 12,  /* 12.5 Hz */
  ICM42_ODR_6_25HZ  = 13,  /* 6.25 Hz (LP mode) */
  ICM42_ODR_3_125HZ = 14,  /* 3.125 Hz (LP mode) */
  ICM42_ODR_1_5625HZ = 15, /* 1.5625 Hz (LP mode) */
} icm42_odr_t;

/**
 * @brief Anti-Alias Filter (AAF) bandwidth
 * bits[1:0] of GYRO_ACCEL_CONFIG0
 */
typedef enum
{
  ICM42_AAF_DISABLE = 0,
  ICM42_AAF_258HZ   = 0,
  ICM42_AAF_536HZ   = 1,
  ICM42_AAF_997HZ   = 2,
  ICM42_AAF_1962HZ  = 3,
} icm42_aaf_t;

/**
 * @brief UI Filter order
 * bits[3:2] of GYRO_ACCEL_CONFIG0
 */
typedef enum
{
  ICM42_UI_FILT_ORD_1ST = 0,
  ICM42_UI_FILT_ORD_2ND = 1,
  ICM42_UI_FILT_ORD_3RD = 2,
} icm42_ui_filt_ord_t;

/**
 * @brief 3-axis raw data
 */
typedef struct
{
  int16_t x;
  int16_t y;
  int16_t z;
} icm42_axis_raw_t;

/**
 * @brief 3-axis float data
 */
typedef struct
{
  float x;
  float y;
  float z;
} icm42_axis_float_t;

/**
 * @brief All sensor data
 */
typedef struct
{
  icm42_axis_raw_t gyro;   /* Gyroscope raw data */
  icm42_axis_raw_t accel;  /* Accelerometer raw data */
  int16_t          temp;   /* Temperature raw data */
} icm42_sensor_data_t;

/**
 * @brief Calibration data (zero-rate offset)
 */
typedef struct
{
  icm42_axis_raw_t gyro_offset;
  icm42_axis_raw_t accel_offset;
  bool             is_calibrated;
} icm42_calibration_t;

/**
 * @brief Configuration structure
 */
typedef struct
{
  icm42_gyro_fs_t      gyro_fs;
  icm42_accel_fs_t     accel_fs;
  icm42_odr_t          gyro_odr;
  icm42_odr_t          accel_odr;
  icm42_aaf_t          gyro_aaf;
  icm42_aaf_t          accel_aaf;
  icm42_ui_filt_ord_t  ui_filt_ord;
  bool                 use_low_noise_mode;  /* true = LN mode, false = LP mode */
} icm42_config_t;

/**
 * @brief SPI/GPIO interface binding
 */
typedef struct
{
  void (*set_cs)(bool select);
  bool (*spi_transfer)(const uint8_t *tx, uint8_t *rx, uint16_t length);
  void (*delay_us)(uint32_t us);
  void (*delay_ms)(uint32_t ms);
} icm42_bus_if_t;

/**
 * @brief Driver instance
 */
typedef struct
{
  icm42_bus_if_t       bus;
  icm42_config_t       config;
  icm42_calibration_t  calib;
  volatile bool        data_ready;  /* Set by interrupt handler */
} icm42_dev_t;

/* Public function prototypes ----------------------------------------------- */

/**
 * @brief Initialize ICM-42688
 * @param dev Device instance
 * @param config Configuration parameters
 * @return ICM42_OK on success
 */
icm42_err_t icm42_init(icm42_dev_t *dev, const icm42_config_t *config);

/**
 * @brief Read register(s)
 * @param dev Device instance
 * @param reg Register address
 * @param data Buffer to store read data
 * @param len Number of bytes to read
 * @return ICM42_OK on success
 */
icm42_err_t icm42_read_reg(icm42_dev_t *dev, uint8_t reg, uint8_t *data, uint16_t len);

/**
 * @brief Write register(s)
 * @param dev Device instance
 * @param reg Register address
 * @param data Data to write
 * @param len Number of bytes to write
 * @return ICM42_OK on success
 */
icm42_err_t icm42_write_reg(icm42_dev_t *dev, uint8_t reg, const uint8_t *data, uint16_t len);

/**
 * @brief Get all sensor data (burst read)
 * @param dev Device instance
 * @param data Pointer to store sensor data
 * @return ICM42_OK on success
 */
icm42_err_t icm42_get_all_data(icm42_dev_t *dev, icm42_sensor_data_t *data);

/**
 * @brief Get gyroscope data
 * @param dev Device instance
 * @param gyro Pointer to store gyro data
 * @return ICM42_OK on success
 */
icm42_err_t icm42_get_gyro(icm42_dev_t *dev, icm42_axis_raw_t *gyro);

/**
 * @brief Get accelerometer data
 * @param dev Device instance
 * @param accel Pointer to store accel data
 * @return ICM42_OK on success
 */
icm42_err_t icm42_get_accel(icm42_dev_t *dev, icm42_axis_raw_t *accel);

/**
 * @brief Get temperature data
 * @param dev Device instance
 * @param temp Pointer to store temperature data
 * @return ICM42_OK on success
 */
icm42_err_t icm42_get_temp(icm42_dev_t *dev, int16_t *temp);

/**
 * @brief Convert raw gyro data to dps (degrees per second)
 * @param dev Device instance
 * @param raw Raw gyro data
 * @param dps Output in dps
 */
void icm42_convert_gyro_to_dps(const icm42_dev_t *dev, const icm42_axis_raw_t *raw, icm42_axis_float_t *dps);

/**
 * @brief Convert raw accel data to g (gravity)
 * @param dev Device instance
 * @param raw Raw accel data
 * @param g Output in g
 */
void icm42_convert_accel_to_g(const icm42_dev_t *dev, const icm42_axis_raw_t *raw, icm42_axis_float_t *g);

/**
 * @brief Convert raw temperature to Celsius
 * @param raw_temp Raw temperature data
 * @return Temperature in Celsius
 */
float icm42_convert_temp_to_celsius(int16_t raw_temp);

/**
 * @brief Calibrate gyroscope zero-rate offset
 * @param dev Device instance
 * @param num_samples Number of samples to average (100-500 recommended)
 * @return ICM42_OK on success
 * @note Keep sensor stationary during calibration
 */
icm42_err_t icm42_calibrate_gyro_offset(icm42_dev_t *dev, uint16_t num_samples);

/**
 * @brief Calibrate accelerometer offset
 * @param dev Device instance
 * @param num_samples Number of samples to average
 * @return ICM42_OK on success
 * @note Place sensor flat (Z-axis up) during calibration
 */
icm42_err_t icm42_calibrate_accel_offset(icm42_dev_t *dev, uint16_t num_samples);

/**
 * @brief Set calibration data manually
 * @param dev Device instance
 * @param calib Calibration data
 * @return ICM42_OK on success
 */
icm42_err_t icm42_set_calibration(icm42_dev_t *dev, const icm42_calibration_t *calib);

/**
 * @brief Get current calibration data
 * @param dev Device instance
 * @param calib Pointer to store calibration data
 * @return ICM42_OK on success
 */
icm42_err_t icm42_get_calibration(const icm42_dev_t *dev, icm42_calibration_t *calib);

/**
 * @brief Setup data ready interrupt on INT1 pin
 * @param dev Device instance
 * @return ICM42_OK on success
 */
icm42_err_t icm42_setup_interrupt(icm42_dev_t *dev);

/**
 * @brief Clear interrupt status
 * @param dev Device instance
 * @return ICM42_OK on success
 */
icm42_err_t icm42_clear_interrupt(icm42_dev_t *dev);

/**
 * @brief Interrupt handler - call from EXTI callback
 * @param dev Device instance
 */
void icm42_irq_handler(icm42_dev_t *dev);

/**
 * @brief Soft reset the device
 * @param dev Device instance
 * @return ICM42_OK on success
 */
icm42_err_t icm42_soft_reset(icm42_dev_t *dev);

/**
 * @brief Run self-test
 * @param dev Device instance
 * @return ICM42_OK on success
 */
icm42_err_t icm42_self_test(icm42_dev_t *dev);

#endif /* __ICM42688_H */

/* End of file -------------------------------------------------------- */
