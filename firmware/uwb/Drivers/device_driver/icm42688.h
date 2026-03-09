/**
 * @file       icm42688.h
 * @copyright
 * @license
 * @version    1.0.0
 * @date       2026-03-04
 * @author     Dong Son
 * @brief      ICM-42688-P 6-axis IMU driver (SPI mode)
 * @note       SPI mode 0/3, max 24MHz, MSB first
 *             Based on ICM-42688-P datasheet Rev 1.8
 * @example    None
 */

#ifndef __ICM42688_H
#define __ICM42688_H

/* Includes ----------------------------------------------------------- */
#include <stdbool.h>
#include <stdint.h>

/* Public defines ----------------------------------------------------------- */
/* Bank 0 Registers (User Bank) */
#define ICM42688_REG_DEVICE_CONFIG        0x11
#define ICM42688_REG_DRIVE_CONFIG         0x13
#define ICM42688_REG_INT_CONFIG           0x14
#define ICM42688_REG_FIFO_CONFIG          0x16
#define ICM42688_REG_TEMP_DATA1           0x1D
#define ICM42688_REG_TEMP_DATA0           0x1E
#define ICM42688_REG_ACCEL_DATA_X1        0x1F
#define ICM42688_REG_ACCEL_DATA_X0        0x20
#define ICM42688_REG_ACCEL_DATA_Y1        0x21
#define ICM42688_REG_ACCEL_DATA_Y0        0x22
#define ICM42688_REG_ACCEL_DATA_Z1        0x23
#define ICM42688_REG_ACCEL_DATA_Z0        0x24
#define ICM42688_REG_GYRO_DATA_X1         0x25
#define ICM42688_REG_GYRO_DATA_X0         0x26
#define ICM42688_REG_GYRO_DATA_Y1         0x27
#define ICM42688_REG_GYRO_DATA_Y0         0x28
#define ICM42688_REG_GYRO_DATA_Z1         0x29
#define ICM42688_REG_GYRO_DATA_Z0         0x2A
#define ICM42688_REG_TMST_FSYNCH          0x2B
#define ICM42688_REG_TMST_FSYNCL          0x2C
#define ICM42688_REG_INT_STATUS           0x2D
#define ICM42688_REG_FIFO_COUNTH          0x2E
#define ICM42688_REG_FIFO_COUNTL          0x2F
#define ICM42688_REG_FIFO_DATA            0x30
#define ICM42688_REG_APEX_DATA0           0x31
#define ICM42688_REG_INT_STATUS2          0x37
#define ICM42688_REG_INT_STATUS3          0x38
#define ICM42688_REG_SIGNAL_PATH_RESET    0x4B
#define ICM42688_REG_INTF_CONFIG0         0x4C
#define ICM42688_REG_INTF_CONFIG1         0x4D
#define ICM42688_REG_PWR_MGMT0            0x4E
#define ICM42688_REG_GYRO_CONFIG0         0x4F
#define ICM42688_REG_ACCEL_CONFIG0        0x50
#define ICM42688_REG_GYRO_CONFIG1         0x51
#define ICM42688_REG_GYRO_ACCEL_CONFIG0   0x52
#define ICM42688_REG_ACCEL_CONFIG1        0x53
#define ICM42688_REG_TMST_CONFIG          0x54
#define ICM42688_REG_FIFO_CONFIG1         0x5F
#define ICM42688_REG_FIFO_CONFIG2         0x60
#define ICM42688_REG_FIFO_CONFIG3         0x61
#define ICM42688_REG_FSYNC_CONFIG         0x62
#define ICM42688_REG_INT_CONFIG0          0x63
#define ICM42688_REG_INT_CONFIG1          0x64
#define ICM42688_REG_INT_SOURCE0          0x65
#define ICM42688_REG_INT_SOURCE1          0x66
#define ICM42688_REG_INT_SOURCE3          0x68
#define ICM42688_REG_INT_SOURCE4          0x69
#define ICM42688_REG_SELF_TEST_CONFIG     0x70
#define ICM42688_REG_WHO_AM_I             0x75
#define ICM42688_REG_REG_BANK_SEL         0x76

/* Bank 1 Registers */
#define ICM42688_REG_SENSOR_CONFIG0       0x03  /* Bank 1 */
#define ICM42688_REG_GYRO_CONFIG_STATIC2  0x0B  /* Bank 1 */
#define ICM42688_REG_GYRO_CONFIG_STATIC3  0x0C  /* Bank 1 */
#define ICM42688_REG_GYRO_CONFIG_STATIC4  0x0D  /* Bank 1 */
#define ICM42688_REG_GYRO_CONFIG_STATIC5  0x0E  /* Bank 1 */
#define ICM42688_REG_GYRO_CONFIG_STATIC6  0x0F  /* Bank 1 */
#define ICM42688_REG_GYRO_CONFIG_STATIC7  0x10  /* Bank 1 */
#define ICM42688_REG_GYRO_CONFIG_STATIC8  0x11  /* Bank 1 */
#define ICM42688_REG_GYRO_CONFIG_STATIC9  0x12  /* Bank 1 */
#define ICM42688_REG_GYRO_CONFIG_STATIC10 0x13  /* Bank 1 */

/* Bank 2 Registers */
#define ICM42688_REG_ACCEL_CONFIG_STATIC2 0x03  /* Bank 2 */
#define ICM42688_REG_ACCEL_CONFIG_STATIC3 0x04  /* Bank 2 */
#define ICM42688_REG_ACCEL_CONFIG_STATIC4 0x05  /* Bank 2 */

/* Bank 3 */
#define ICM42688_REG_CLKDIV   0x2A

/* Bank 4 — Hardware offset registers */
#define ICM42688_REG_OFFSET_USER0   0x77  /* Bank 4 */
#define ICM42688_REG_OFFSET_USER1   0x78  /* Bank 4 */
#define ICM42688_REG_OFFSET_USER2   0x79  /* Bank 4 */
#define ICM42688_REG_OFFSET_USER3   0x7A  /* Bank 4 */
#define ICM42688_REG_OFFSET_USER4   0x7B  /* Bank 4 */
#define ICM42688_REG_OFFSET_USER5   0x7C  /* Bank 4 */
#define ICM42688_REG_OFFSET_USER6   0x7D  /* Bank 4 */
#define ICM42688_REG_OFFSET_USER7   0x7E  /* Bank 4 */
#define ICM42688_REG_OFFSET_USER8   0x7F  /* Bank 4 */

/* WHO_AM_I value for ICM-42688-P */
#define ICM42688_WHO_AM_I_VALUE           	0x47

/* PWR_MGMT0 bits */
#define ICM42688_PWR_MGMT0_GYRO_MODE_OFF  	0x00
#define ICM42688_PWR_MGMT0_GYRO_MODE_STDBY 	0x04
#define ICM42688_PWR_MGMT0_GYRO_MODE_LN   	0x0C
#define ICM42688_PWR_MGMT0_ACCEL_MODE_OFF 	0x00
#define ICM42688_PWR_MGMT0_ACCEL_MODE_LP  	0x02
#define ICM42688_PWR_MGMT0_ACCEL_MODE_LN  	0x03
#define ICM42688_PWR_MGMT0_TEMP_DIS       	0x20

/* Sensitivity scale factors */
#define ICM42688_GYRO_SENSITIVITY_2000DPS   16.4f    /* LSB/(dps) */
#define ICM42688_GYRO_SENSITIVITY_1000DPS   32.8f
#define ICM42688_GYRO_SENSITIVITY_500DPS    65.5f
#define ICM42688_GYRO_SENSITIVITY_250DPS    131.0f
#define ICM42688_GYRO_SENSITIVITY_125DPS    262.0f
#define ICM42688_GYRO_SENSITIVITY_62_5DPS   524.3f
#define ICM42688_GYRO_SENSITIVITY_31_25DPS  1048.6f
#define ICM42688_GYRO_SENSITIVITY_15_625DPS 2097.2f

#define ICM42688_ACCEL_SENSITIVITY_16G      2048.0f  /* LSB/g */
#define ICM42688_ACCEL_SENSITIVITY_8G       4096.0f
#define ICM42688_ACCEL_SENSITIVITY_4G       8192.0f
#define ICM42688_ACCEL_SENSITIVITY_2G       16384.0f

/* Temperature sensitivity: 132.48 LSB/°C, offset = 25°C */
#define ICM42688_TEMP_SENSITIVITY           132.48f
#define ICM42688_TEMP_OFFSET                25.0f

#define ICM42688_CALIB_SAMPLES 1000

/* Public enumerate/structure ----------------------------------------------- */
typedef enum
{
  ICM42688_OK = 0,
  ICM42688_ERR,
  ICM42688_ERR_PARAM,
  ICM42688_ERR_TIMEOUT,
  ICM42688_ERR_WHO_AM_I,
} icm42688_err_t;

/**
 * @brief Gyro Full Scale Range
 * bits[7:5] of GYRO_CONFIG0
 */
typedef enum
{
  ICM42688_GYRO_FS_2000DPS  	= 0,  /* ±2000 dps */
  ICM42688_GYRO_FS_1000DPS  	= 1,  /* ±1000 dps */
  ICM42688_GYRO_FS_500DPS   	= 2,  /* ±500 dps */
  ICM42688_GYRO_FS_250DPS   	= 3,  /* ±250 dps */
  ICM42688_GYRO_FS_125DPS   	= 4,  /* ±125 dps */
  ICM42688_GYRO_FS_62_5DPS  	= 5,  /* ±62.5 dps */
  ICM42688_GYRO_FS_31_25DPS 	= 6,  /* ±31.25 dps */
  ICM42688_GYRO_FS_15_625DPS 	= 7, /* ±15.625 dps */
} icm42688_gyro_fs_t;

/**
 * @brief Accel Full Scale Range
 * bits[7:5] of ACCEL_CONFIG0
 */
typedef enum
{
  ICM42688_ACCEL_FS_16G = 0,  /* ±16g */
  ICM42688_ACCEL_FS_8G  = 1,  /* ±8g */
  ICM42688_ACCEL_FS_4G  = 2,  /* ±4g */
  ICM42688_ACCEL_FS_2G  = 3,  /* ±2g */
} icm42688_accel_fs_t;

/**
 * @brief Output Data Rate (ODR)
 * bits[3:0] of GYRO_CONFIG0 / ACCEL_CONFIG0
 */
typedef enum
{
  ICM42688_ODR_32KHZ   		= 1,   /* 32 kHz (LN mode only) */
  ICM42688_ODR_16KHZ   		= 2,   /* 16 kHz (LN mode only) */
  ICM42688_ODR_8KHZ    		= 3,   /* 8 kHz (LN mode only) */
  ICM42688_ODR_4KHZ    		= 4,   /* 4 kHz (LN mode only) */
  ICM42688_ODR_2KHZ    		= 5,   /* 2 kHz (LN mode only) */
  ICM42688_ODR_1KHZ    		= 6,   /* 1 kHz (LN mode) */
  ICM42688_ODR_200HZ   		= 8,   /* 200 Hz */
  ICM42688_ODR_100HZ   		= 9,   /* 100 Hz */
  ICM42688_ODR_50HZ    		= 10,  /* 50 Hz */
  ICM42688_ODR_25HZ    		= 11,  /* 25 Hz */
  ICM42688_ODR_12_5HZ  		= 12,  /* 12.5 Hz */
  ICM42688_ODR_6_25HZ  		= 13,  /* 6.25 Hz (LP mode) */
  ICM42688_ODR_3_125HZ 		= 14,  /* 3.125 Hz (LP mode) */
  ICM42688_ODR_1_5625HZ 	= 15, /* 1.5625 Hz (LP mode) */
} icm42688_odr_t;

/**
 * @brief Gyro Notch Filter bandwidth selection
 * GYRO_CONFIG_STATIC10 (Bank 1) bits[2:0]
 */
typedef enum
{
  ICM42688_GYRO_NF_BW_1449HZ = 0,
  ICM42688_GYRO_NF_BW_680HZ  = 1,
  ICM42688_GYRO_NF_BW_329HZ  = 2,
  ICM42688_GYRO_NF_BW_162HZ  = 3,
  ICM42688_GYRO_NF_BW_80HZ   = 4,
  ICM42688_GYRO_NF_BW_40HZ   = 5,
  ICM42688_GYRO_NF_BW_20HZ   = 6,
  ICM42688_GYRO_NF_BW_10HZ   = 7,
} icm42688_gyro_nf_bw_t;

typedef enum
{
  ICM42688_AAF_258HZ   = 1,  /* BW ≈ 258 Hz  (DELT=63)         */
  ICM42688_AAF_536HZ   = 2,  /* BW ≈ 536 Hz  (DELT=6)          */
  ICM42688_AAF_997HZ   = 3,  /* BW ≈ 997 Hz  (DELT=1)          */
  ICM42688_AAF_1962HZ  = 4,  /* BW ≈ 1962 Hz (DELT=1, max)     */
} icm42688_aaf_t;

/**
 * @brief UI Low-Pass Filter bandwidth
 * Used in GYRO_ACCEL_CONFIG0 bits[7:4] (accel) and bits[3:0] (gyro)
 * LN mode values (see datasheet 14.40)
 */
typedef enum
{
  ICM42688_UI_FILT_BW_ODR_2      = 0,  /* BW = ODR/2                              */
  ICM42688_UI_FILT_BW_ODR_4      = 1,  /* BW = max(400Hz, ODR)/4  (default)       */
  ICM42688_UI_FILT_BW_ODR_5      = 2,  /* BW = max(400Hz, ODR)/5                  */
  ICM42688_UI_FILT_BW_ODR_8      = 3,  /* BW = max(400Hz, ODR)/8                  */
  ICM42688_UI_FILT_BW_ODR_10     = 4,  /* BW = max(400Hz, ODR)/10                 */
  ICM42688_UI_FILT_BW_ODR_16     = 5,  /* BW = max(400Hz, ODR)/16                 */
  ICM42688_UI_FILT_BW_ODR_20     = 6,  /* BW = max(400Hz, ODR)/20                 */
  ICM42688_UI_FILT_BW_ODR_40     = 7,  /* BW = max(400Hz, ODR)/40                 */
  ICM42688_UI_FILT_BW_LOW_LAT_1  = 14, /* Low latency, Dec2 @ max(400Hz, ODR)     */
  ICM42688_UI_FILT_BW_LOW_LAT_2  = 15, /* Low latency, Dec2 @ max(200Hz, 8*ODR)   */
} icm42688_ui_filt_bw_t;

/**
 * @brief UI filter order
 * Gyro  → GYRO_CONFIG1  (0x51) bits[3:2]
 * Accel → ACCEL_CONFIG1 (0x53) bits[4:3]
 */
typedef enum
{
  ICM42688_UI_FILT_ORD_1ST = 0,  /* 1st order */
  ICM42688_UI_FILT_ORD_2ND = 1,  /* 2nd order */
  ICM42688_UI_FILT_ORD_3RD = 2,  /* 3rd order */
} icm42688_ui_filt_ord_t;

/**
 * @brief Temperature DLPF bandwidth
 * GYRO_CONFIG1 (0x51) bits[7:5]
 */
typedef enum
{
  ICM42688_TEMP_FILT_BW_4000HZ = 0,  /* BW = 4000Hz, latency = 0.125ms (default) */
  ICM42688_TEMP_FILT_BW_170HZ  = 1,  /* BW = 170Hz,  latency = 1ms               */
  ICM42688_TEMP_FILT_BW_82HZ   = 2,  /* BW = 82Hz,   latency = 2ms               */
  ICM42688_TEMP_FILT_BW_40HZ   = 3,  /* BW = 40Hz,   latency = 4ms               */
  ICM42688_TEMP_FILT_BW_20HZ   = 4,  /* BW = 20Hz,   latency = 8ms               */
  ICM42688_TEMP_FILT_BW_10HZ   = 5,  /* BW = 10Hz,   latency = 16ms              */
  ICM42688_TEMP_FILT_BW_5HZ    = 6,  /* BW = 5Hz,    latency = 32ms              */
} icm42688_temp_filt_bw_t;

/**
 * @brief 3-axis float data
 */
typedef struct
{
	float x;
	float y;
	float z;
} icm42688_axis_float_t;

/**
 * @brief All sensor data
 */
typedef struct
{
	icm42688_axis_float_t 	gyro;   	/* dps  */
	icm42688_axis_float_t 	accel;  	/* g    */
	float          			temp;   	/* °C   */
} icm42688_sensor_data_t;

/**
 * @brief Calibration data (zero-rate offset)
 */
typedef struct
{
	icm42688_axis_float_t gyro_offset;
	icm42688_axis_float_t accel_offset;
} icm42688_calibration_t;

/**
 * @brief Configuration structure
 */
typedef struct
{
	icm42688_gyro_fs_t      	gyro_fs;
	icm42688_accel_fs_t     	accel_fs;
	icm42688_odr_t          	gyro_odr;
	icm42688_odr_t          	accel_odr;
	icm42688_aaf_t           	gyro_aaf;
	icm42688_aaf_t           	accel_aaf;
	bool						gyro_filter_enable;
	bool						accel_filter_enable;
	icm42688_ui_filt_bw_t    	gyro_ui_filt_bw;
	icm42688_ui_filt_bw_t    	accel_ui_filt_bw;
	icm42688_ui_filt_ord_t   	gyro_ui_filt_ord;
	icm42688_ui_filt_ord_t   	accel_ui_filt_ord;
	icm42688_temp_filt_bw_t  	temp_filt_bw;
	bool                 		use_low_noise_mode;
	bool             			use_calibrated;
} icm42688_config_t;

/**
 * @brief SPI/GPIO interface binding
 */
typedef struct
{
  void (*set_cs)(bool select);
  bool (*spi_transfer)(const uint8_t *tx, uint8_t *rx, uint16_t length);
  void (*delay_us)(uint32_t us);
  void (*delay_ms)(uint32_t ms);
} icm42688_bus_if_t;

/**
 * @brief Driver instance
 */
typedef struct
{
  icm42688_bus_if_t       	bus;
  icm42688_config_t       	config;
  icm42688_calibration_t  	calib;
  volatile bool        		data_ready;  /* Set by interrupt handler */
} icm42688_dev_t;

/* Public function prototypes ----------------------------------------------- */
icm42688_err_t icm42688_init(icm42688_dev_t *dev, const icm42688_config_t *config);

icm42688_err_t icm42688_get_all_data(icm42688_dev_t *dev, icm42688_sensor_data_t *data);

icm42688_err_t icm42688_compute_hardware_offsets(icm42688_dev_t *dev, uint16_t num_samples);

icm42688_err_t icm42688_set_hardware_offsets(icm42688_dev_t *dev);

icm42688_err_t icm42688_get_calibration(const icm42688_dev_t *dev, icm42688_calibration_t *calib);

icm42688_err_t icm42688_calibrate_gyro_offset(icm42688_dev_t *dev, uint16_t num_samples);

icm42688_err_t icm42688_calibrate_accel_offset(icm42688_dev_t *dev, uint16_t num_samples);

icm42688_err_t icm42688_set_filter(icm42688_dev_t *dev, bool gyro_filter, bool accel_filter);

icm42688_err_t icm42688_set_gyro_fs(icm42688_dev_t *dev, icm42688_gyro_fs_t fs);

icm42688_err_t icm42688_set_accel_fs(icm42688_dev_t *dev, icm42688_accel_fs_t fs);

icm42688_err_t icm42688_set_gyro_odr(icm42688_dev_t *dev, icm42688_odr_t odr);

icm42688_err_t icm42688_set_accel_odr(icm42688_dev_t *dev, icm42688_odr_t odr);

icm42688_err_t icm42688_set_aaf_filter(icm42688_dev_t *dev, icm42688_aaf_t gyro_aaf, icm42688_aaf_t accel_aaf);

icm42688_err_t icm42688_set_ui_filter_order(icm42688_dev_t *dev, icm42688_ui_filt_ord_t gyro_ord, icm42688_ui_filt_ord_t accel_ord);

icm42688_err_t icm42688_set_ui_filter_bw(icm42688_dev_t *dev, icm42688_ui_filt_bw_t gyro_bw, icm42688_ui_filt_bw_t accel_bw);

icm42688_err_t icm42688_set_temp_filter_bw(icm42688_dev_t *dev, icm42688_temp_filt_bw_t temp_bw);

icm42688_err_t icm42688_enable_data_ready_interrupt(icm42688_dev_t *dev);

icm42688_err_t icm42688_disable_data_ready_interrupt(icm42688_dev_t *dev);

icm42688_err_t icm42688_set_gyro_notch_filter(icm42688_dev_t *dev, float freq_x, float freq_y, float freq_z, icm42688_gyro_nf_bw_t bw);

icm42688_err_t icm42688_set_hw_offsets(icm42688_dev_t *dev);

icm42688_err_t icm42688_setup_interrupt(icm42688_dev_t *dev);

icm42688_err_t icm42688_clear_interrupt(icm42688_dev_t *dev);

void icm42688_irq_handler(icm42688_dev_t *dev);

icm42688_err_t icm42688_soft_reset(icm42688_dev_t *dev);

icm42688_err_t icm42688_self_test(icm42688_dev_t *dev);

#endif /* __ICM42688_H */

/* End of file -------------------------------------------------------- */
