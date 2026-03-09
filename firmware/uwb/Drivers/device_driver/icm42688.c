/**
 * @file       icm42688.c
 * @copyright
 * @license
 * @version    1.0.0
 * @date       2026-03-04
 * @author     Dong Son
 * @brief      ICM-42688-P driver implementation
 * @note       Based on ICM-42688-P datasheet Rev 1.8
 * @example    None
 */

/* Includes ----------------------------------------------------------- */
#include "icm42688.h"
#include <string.h>
#include <math.h>
#include <stddef.h>
#include "err.h"

/* Private defines ---------------------------------------------------------- */
#define ICM42688_SPI_READ  0x80
#define ICM42688_SPI_WRITE 0x00
/* INTF_CONFIG0 bits */
#define ICM42688_SENSOR_DATA_ENDIAN		0x01	/* Big Endian */
#define ICM42688_DISABLE_I2C			0x03

#define ICM42688_ENABLE_SOFT_RESET		0x01
/* Private enumerate/structure ---------------------------------------- */
/* AAF parameters per icm42688_aaf_t index (datasheet Table 5-5) */
typedef struct
{
  uint8_t  delt;
  uint16_t deltsqr;
  uint8_t  bitshift;
} icm42688_aaf_param_t;

/* Private function prototypes ---------------------------------------------- */
static icm42688_err_t 	icm42688_select_bank(icm42688_dev_t *dev, uint8_t bank);
static icm42688_err_t 	icm42688_read_reg(icm42688_dev_t *dev, uint8_t reg, uint8_t *data, uint16_t len);
static icm42688_err_t 	icm42688_write_reg(icm42688_dev_t *dev, uint8_t reg, const uint8_t *data, uint16_t len);
static inline uint8_t 	icm42688_who_am_i(icm42688_dev_t *dev);

/* Private variables -------------------------------------------------- */
static const float ICM42688_GYRO_SENS_LUT[8] =
{
  ICM42688_GYRO_SENSITIVITY_2000DPS,    /* ICM42688_GYRO_FS_2000DPS   */
  ICM42688_GYRO_SENSITIVITY_1000DPS,    /* ICM42688_GYRO_FS_1000DPS   */
  ICM42688_GYRO_SENSITIVITY_500DPS,     /* ICM42688_GYRO_FS_500DPS    */
  ICM42688_GYRO_SENSITIVITY_250DPS,     /* ICM42688_GYRO_FS_250DPS    */
  ICM42688_GYRO_SENSITIVITY_125DPS,     /* ICM42688_GYRO_FS_125DPS    */
  ICM42688_GYRO_SENSITIVITY_62_5DPS,    /* ICM42688_GYRO_FS_62_5DPS   */
  ICM42688_GYRO_SENSITIVITY_31_25DPS,   /* ICM42688_GYRO_FS_31_25DPS  */
  ICM42688_GYRO_SENSITIVITY_15_625DPS,  /* ICM42688_GYRO_FS_15_625DPS */
};

static const float ICM42688_ACCEL_SENS_LUT[4] =
{
  ICM42688_ACCEL_SENSITIVITY_16G,  /* ICM42688_ACCEL_FS_16G */
  ICM42688_ACCEL_SENSITIVITY_8G,   /* ICM42688_ACCEL_FS_8G  */
  ICM42688_ACCEL_SENSITIVITY_4G,   /* ICM42688_ACCEL_FS_4G  */
  ICM42688_ACCEL_SENSITIVITY_2G,   /* ICM42688_ACCEL_FS_2G  */
};

static const icm42688_aaf_param_t s_aaf_lut[] =
{
  /* ICM42688_AAF_258HZ   */ { 63, 3968, 15 },
  /* ICM42688_AAF_536HZ   */ {  6,   36, 10 },
  /* ICM42688_AAF_997HZ   */ {  1,    1,  1 },
  /* ICM42688_AAF_1962HZ  */ {  1,    1,  1 },
};

/* Public function implementation ------------------------------------------- */
icm42688_err_t icm42688_init(icm42688_dev_t *dev, const icm42688_config_t *config)
{
	CHECK_ERR((dev != NULL && config != NULL), ICM42688_ERR_PARAM);

	uint8_t data;

	/* Initialize calibration to zero */
	memset(&dev->calib, 0, sizeof(icm42688_calibration_t));
	dev->data_ready = false;

	/* Soft reset */
	CHECK_ERR(icm42688_soft_reset(dev) == ICM42688_OK, ICM42688_ERR);

	/* Verify WHO_AM_I */
	data = icm42688_who_am_i(dev);

	if (data != ICM42688_WHO_AM_I_VALUE)
	{
		return ICM42688_ERR_WHO_AM_I;
	}

	/* PWR_MGMT0: select operating mode */
	if (config->use_low_noise_mode)
	{
		data = ICM42688_PWR_MGMT0_GYRO_MODE_LN | ICM42688_PWR_MGMT0_ACCEL_MODE_LN;
	}
	else
	{
		data = ICM42688_PWR_MGMT0_GYRO_MODE_LN | ICM42688_PWR_MGMT0_ACCEL_MODE_LP;
	}
	CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_PWR_MGMT0, &data, 1) == ICM42688_OK, ICM42688_ERR);

	/* Wait for sensors to start up (200us typ, max 500us per datasheet) */
	dev->bus.delay_ms(1);

	/* Configure Gyro: FS and ODR */
	CHECK_ERR(icm42688_set_gyro_fs(dev, config->gyro_fs) == ICM42688_OK, ICM42688_ERR);
	CHECK_ERR(icm42688_set_gyro_odr(dev, config->gyro_odr) == ICM42688_OK, ICM42688_ERR);

	/* Configure Accel: FS and ODR */
	CHECK_ERR(icm42688_set_accel_fs(dev, config->accel_fs) == ICM42688_OK, ICM42688_ERR);
	CHECK_ERR(icm42688_set_accel_odr(dev, config->accel_odr) == ICM42688_OK, ICM42688_ERR);

	/* UI filter */
	CHECK_ERR(icm42688_set_ui_filter_order(dev, config->gyro_ui_filt_ord, config->accel_ui_filt_ord) == ICM42688_OK, ICM42688_ERR);
	CHECK_ERR(icm42688_set_ui_filter_bw(dev, config->gyro_ui_filt_bw, config->accel_ui_filt_bw) == ICM42688_OK, ICM42688_ERR);

	/* AAF filter */

	/* Enable filter - Notch & AAF */
	CHECK_ERR(icm42688_set_filter(dev, config->gyro_filter_enable, config->accel_filter_enable) == ICM42688_OK, ICM42688_ERR);

	/* Save config */
	dev->config = *config;

	/* Wait for filter to settle */
	dev->bus.delay_ms(50);

	if (config->use_calibrated)
	{
		CHECK_ERR(icm42688_compute_hardware_offsets(dev, ICM42688_CALIB_SAMPLES) == ICM42688_OK, ICM42688_ERR);
		CHECK_ERR(icm42688_set_hardware_offsets(dev) == ICM42688_OK, ICM42688_ERR);
	}

	return ICM42688_OK;
}

icm42688_err_t icm42688_get_all_data(icm42688_dev_t *dev, icm42688_sensor_data_t *data)
{
	CHECK_ERR((dev != NULL && data != NULL), ICM42688_ERR_PARAM);

	uint8_t  buf[14];
	int16_t  raw_temp;
	int16_t  raw_ax, raw_ay, raw_az;
	int16_t  raw_gx, raw_gy, raw_gz;

	/* Burst read from TEMP_DATA1 (0x1D) to GYRO_DATA_Z0 (0x2A) = 14 bytes */
	CHECK_ERR(icm42688_read_reg(dev, ICM42688_REG_TEMP_DATA1, buf, 14) == ICM42688_OK, ICM42688_ERR);

	/* Big-endian (default): high byte first */
	raw_temp = (int16_t)((buf[0]  << 8) | buf[1]);
	raw_ax   = (int16_t)((buf[2]  << 8) | buf[3]);
	raw_ay   = (int16_t)((buf[4]  << 8) | buf[5]);
	raw_az   = (int16_t)((buf[6]  << 8) | buf[7]);
	raw_gx   = (int16_t)((buf[8]  << 8) | buf[9]);
	raw_gy   = (int16_t)((buf[10] << 8) | buf[11]);
	raw_gz   = (int16_t)((buf[12] << 8) | buf[13]);

	/* Convert to physical units, then assign to output struct */
	float gyro_sens  = ICM42688_GYRO_SENS_LUT[(uint8_t)dev->config.gyro_fs  & 0x07];
	float accel_sens = ICM42688_ACCEL_SENS_LUT[(uint8_t)dev->config.accel_fs & 0x03];

	data->temp    = ((float)raw_temp / ICM42688_TEMP_SENSITIVITY) + ICM42688_TEMP_OFFSET;
	data->accel.x = (float)raw_ax / accel_sens;
	data->accel.y = (float)raw_ay / accel_sens;
	data->accel.z = (float)raw_az / accel_sens;
	float gx_phys = (float)raw_gx / gyro_sens;
	float gy_phys = (float)raw_gy / gyro_sens;
	float gz_phys = (float)raw_gz / gyro_sens;

	if (dev->config.use_calibrated)
	{
	    data->gyro.x = gx_phys - dev->calib.gyro_offset.x;
	    data->gyro.y = gy_phys - dev->calib.gyro_offset.y;
	    data->gyro.z = gz_phys - dev->calib.gyro_offset.z;
	}
	else
	{
	    data->gyro.x = gx_phys;
	    data->gyro.y = gy_phys;
	    data->gyro.z = gz_phys;
	}

	return ICM42688_OK;
}

icm42688_err_t icm42688_calibrate_gyro_offset(icm42688_dev_t *dev, uint16_t num_samples)
{
    CHECK_ERR((dev != NULL && num_samples != 0), ICM42688_ERR_PARAM);

    uint8_t  	buf[6];
    int16_t  	raw_gx, raw_gy, raw_gz;
    int32_t 	sum_x = 0, sum_y = 0, sum_z = 0;

    for (uint16_t i = 0; i < num_samples; i++)
    {
    	CHECK_ERR(icm42688_read_reg(dev, ICM42688_REG_GYRO_DATA_X1, buf, 6) == ICM42688_OK, ICM42688_ERR);

    	raw_gx 	= (int16_t)((buf[0]  << 8) | buf[1]);
		raw_gy  = (int16_t)((buf[2]  << 8) | buf[3]);
		raw_gz  = (int16_t)((buf[4]  << 8) | buf[5]);

    	sum_x += (int32_t)raw_gx;
        sum_y += (int32_t)raw_gy;
        sum_z += (int32_t)raw_gz;
        dev->bus.delay_ms(2);
    }

    float gyro_sens  = ICM42688_GYRO_SENS_LUT[(uint8_t)dev->config.gyro_fs  & 0x07];

    dev->calib.gyro_offset.x = (float)sum_x / (float)num_samples / gyro_sens;
    dev->calib.gyro_offset.y = (float)sum_y / (float)num_samples / gyro_sens;
    dev->calib.gyro_offset.z = (float)sum_z / (float)num_samples / gyro_sens;

    return ICM42688_OK;
}

icm42688_err_t icm42688_calibrate_accel_offset(icm42688_dev_t *dev, uint16_t num_samples)
{
	CHECK_ERR((dev != NULL && num_samples != 0), ICM42688_ERR_PARAM);

	uint8_t  	buf[6];
	int16_t  	raw_ax, raw_ay, raw_az;
	int32_t 	sum_x = 0, sum_y = 0, sum_z = 0;

	for (uint16_t i = 0; i < num_samples; i++)
	{
		CHECK_ERR(icm42688_read_reg(dev, ICM42688_REG_ACCEL_DATA_X1, buf, 6) == ICM42688_OK, ICM42688_ERR);

		raw_ax 	= (int16_t)((buf[0]  << 8) | buf[1]);
		raw_ay  = (int16_t)((buf[2]  << 8) | buf[3]);
		raw_az  = (int16_t)((buf[4]  << 8) | buf[5]);

		sum_x += (int32_t)raw_ax;
		sum_y += (int32_t)raw_ay;
		sum_z += (int32_t)raw_az;
		dev->bus.delay_ms(2);
	}

	float accel_sens  = ICM42688_ACCEL_SENS_LUT[(uint8_t)dev->config.accel_fs  & 0x03];

	dev->calib.accel_offset.x = (float)sum_x / (float)num_samples / accel_sens;
	dev->calib.accel_offset.y = (float)sum_y / (float)num_samples / accel_sens;
	dev->calib.accel_offset.z = (float)sum_z / (float)num_samples / accel_sens;

	return ICM42688_OK;
}

icm42688_err_t icm42688_set_gyro_fs(icm42688_dev_t *dev, icm42688_gyro_fs_t fs)
{
    CHECK_ERR(dev != NULL, ICM42688_ERR_PARAM);

    uint8_t data;
    CHECK_ERR(icm42688_select_bank(dev, 0) == ICM42688_OK, ICM42688_ERR);
    CHECK_ERR(icm42688_read_reg(dev, ICM42688_REG_GYRO_CONFIG0, &data, 1) == ICM42688_OK, ICM42688_ERR);

    data = ((fs & 0x07) << 5) | (data & 0x1F);
    CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_GYRO_CONFIG0, &data, 1) == ICM42688_OK, ICM42688_ERR);

    dev->config.gyro_fs = fs;
    return ICM42688_OK;
}

icm42688_err_t icm42688_set_accel_fs(icm42688_dev_t *dev, icm42688_accel_fs_t fs)
{
    CHECK_ERR(dev != NULL, ICM42688_ERR_PARAM);

    uint8_t data;
    CHECK_ERR(icm42688_select_bank(dev, 0) == ICM42688_OK, ICM42688_ERR);
    CHECK_ERR(icm42688_read_reg(dev, ICM42688_REG_ACCEL_CONFIG0, &data, 1) == ICM42688_OK, ICM42688_ERR);

    data = ((fs & 0x07) << 5) | (data & 0x1F);
    CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_ACCEL_CONFIG0, &data, 1) == ICM42688_OK, ICM42688_ERR);

    dev->config.accel_fs = fs;
    return ICM42688_OK;
}

icm42688_err_t icm42688_set_gyro_odr(icm42688_dev_t *dev, icm42688_odr_t odr)
{
    CHECK_ERR(dev != NULL, ICM42688_ERR_PARAM);

    uint8_t data;
    CHECK_ERR(icm42688_select_bank(dev, 0) == ICM42688_OK, ICM42688_ERR);
    CHECK_ERR(icm42688_read_reg(dev, ICM42688_REG_GYRO_CONFIG0, &data, 1) == ICM42688_OK, ICM42688_ERR);

    data = (odr & 0x0F) | (data & 0xF0);
    CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_GYRO_CONFIG0, &data, 1) == ICM42688_OK, ICM42688_ERR);

    dev->config.gyro_odr = odr;
    return ICM42688_OK;
}

icm42688_err_t icm42688_set_accel_odr(icm42688_dev_t *dev, icm42688_odr_t odr)
{
    CHECK_ERR(dev != NULL, ICM42688_ERR_PARAM);

    uint8_t data;
    CHECK_ERR(icm42688_select_bank(dev, 0) == ICM42688_OK, ICM42688_ERR);
    CHECK_ERR(icm42688_read_reg(dev, ICM42688_REG_ACCEL_CONFIG0, &data, 1) == ICM42688_OK, ICM42688_ERR);

    data = (odr & 0x0F) | (data & 0xF0);
    CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_ACCEL_CONFIG0, &data, 1) == ICM42688_OK, ICM42688_ERR);

    dev->config.accel_odr = odr;
    return ICM42688_OK;
}

icm42688_err_t icm42688_set_aaf_filter(icm42688_dev_t *dev, icm42688_aaf_t gyro_aaf, icm42688_aaf_t accel_aaf)
{
	/* Configure Gyro AAF (Bank 1)
	 * GYRO_CONFIG_STATIC2 bit[0]: GYRO_AAF_DIS (1=disable)
	 * GYRO_CONFIG_STATIC3 [7:0]:  DELT
	 * GYRO_CONFIG_STATIC4 [7:0]:  DELTSQR low byte
	 * GYRO_CONFIG_STATIC5 [3:0]:  DELTSQR high nibble | [7:4]: BITSHIFT
	 */
	const icm42688_aaf_param_t *p = &s_aaf_lut[gyro_aaf];
	uint8_t data;

	CHECK_ERR(icm42688_select_bank(dev, 1) == ICM42688_OK, ICM42688_ERR);
	// DELT
	CHECK_ERR(icm42688_read_reg(dev, ICM42688_REG_GYRO_CONFIG_STATIC3, &data, 1) == ICM42688_OK, ICM42688_ERR);
	data = (data & 0xC0) | p->delt;
	CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_GYRO_CONFIG_STATIC3, &data, 1) == ICM42688_OK, ICM42688_ERR);

	// DELTSQR
	data = (uint8_t)(p->deltsqr & 0xFF);
	CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_GYRO_CONFIG_STATIC4, &data, 1) == ICM42688_OK, ICM42688_ERR);
	CHECK_ERR(icm42688_read_reg(dev, ICM42688_REG_GYRO_CONFIG_STATIC5, &data, 1) == ICM42688_OK, ICM42688_ERR);
	data = (data & 0xF0) | (uint8_t)((p->deltsqr >> 8) & 0xFF);
	CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_GYRO_CONFIG_STATIC5, &data, 1) == ICM42688_OK, ICM42688_ERR);

	// BIT SHIFT
	CHECK_ERR(icm42688_read_reg(dev, ICM42688_REG_GYRO_CONFIG_STATIC5, &data, 1) == ICM42688_OK, ICM42688_ERR);
	data = (data & 0x0F) | (uint8_t)((p->bitshift & 0x0F) << 4);
	CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_GYRO_CONFIG_STATIC5, &data, 1) == ICM42688_OK, ICM42688_ERR);

	/* Configure Accel AAF (Bank 2)
	 * ACCEL_CONFIG_STATIC2 [7:1]: DELT | [0]: ACCEL_AAF_DIS (1=disable)
	 * ACCEL_CONFIG_STATIC3 [7:0]: DELTSQR low byte
	 * ACCEL_CONFIG_STATIC4 [3:0]: DELTSQR high nibble | [7:4]: BITSHIFT
	 */
	p = &s_aaf_lut[accel_aaf];
	CHECK_ERR(icm42688_select_bank(dev, 2) == ICM42688_OK, ICM42688_ERR);

	// DELT
	CHECK_ERR(icm42688_read_reg(dev, ICM42688_REG_ACCEL_CONFIG_STATIC2, &data, 1) == ICM42688_OK, ICM42688_ERR);
	data = (data & 0x81) | (p->delt << 1);
	CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_ACCEL_CONFIG_STATIC2, &data, 1) == ICM42688_OK, ICM42688_ERR);

	// DELTSQR
	data =  (uint8_t)(p->deltsqr & 0xFF);
	CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_ACCEL_CONFIG_STATIC3, &data, 1) == ICM42688_OK, ICM42688_ERR);
	CHECK_ERR(icm42688_read_reg(dev, ICM42688_REG_ACCEL_CONFIG_STATIC4, &data, 1) == ICM42688_OK, ICM42688_ERR);
	data = (data & 0xF0) | (uint8_t)((p->deltsqr >> 8) & 0xFF);
	CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_ACCEL_CONFIG_STATIC4, &data, 1) == ICM42688_OK, ICM42688_ERR);

	// BIT SHIFT
	CHECK_ERR(icm42688_read_reg(dev, ICM42688_REG_ACCEL_CONFIG_STATIC4, &data, 1) == ICM42688_OK, ICM42688_ERR);
	data = (data & 0x0F) | (uint8_t)((p->bitshift & 0x0F) << 4);
	CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_ACCEL_CONFIG_STATIC4, &data, 1) == ICM42688_OK, ICM42688_ERR);

	return ICM42688_OK;
}

icm42688_err_t icm42688_set_ui_filter_order(icm42688_dev_t        *dev,
                                             icm42688_ui_filt_ord_t gyro_ord,
                                             icm42688_ui_filt_ord_t accel_ord)
{
    CHECK_ERR(dev != NULL, ICM42688_ERR_PARAM);

    uint8_t data;

    /* GYRO_CONFIG1: [3:2]=GYRO_UI_FILT_ORD, [1:0]=DEC2_M2_ORD(fixed=0x02) */
    CHECK_ERR(icm42688_select_bank(dev, 0) == ICM42688_OK, ICM42688_ERR);
    CHECK_ERR(icm42688_read_reg(dev, ICM42688_REG_GYRO_CONFIG1, &data, 1) == ICM42688_OK, ICM42688_ERR);
    data = (data & 0xF0) | ((gyro_ord & 0x03) << 2) | 0x02;
    CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_GYRO_CONFIG1, &data, 1) == ICM42688_OK, ICM42688_ERR);

    /* ACCEL_CONFIG1: [4:3]=ACCEL_UI_FILT_ORD, [2:1]=DEC2_M2_ORD(fixed=0x02) */
    CHECK_ERR(icm42688_read_reg(dev, ICM42688_REG_ACCEL_CONFIG1, &data, 1) == ICM42688_OK, ICM42688_ERR);
    data = (data & 0xE0) | ((accel_ord & 0x03) << 3) | (0x02 << 1);
    CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_ACCEL_CONFIG1, &data, 1) == ICM42688_OK, ICM42688_ERR);

    dev->config.gyro_ui_filt_ord  = gyro_ord;
    dev->config.accel_ui_filt_ord = accel_ord;
    return ICM42688_OK;
}

icm42688_err_t icm42688_set_ui_filter_bw(icm42688_dev_t *dev, icm42688_ui_filt_bw_t gyro_bw, icm42688_ui_filt_bw_t accel_bw)
{


	CHECK_ERR(dev != NULL, ICM42688_ERR_PARAM);

	uint8_t data;

	/* GYRO_ACCEL_CONFIG0: [7:4]=ACCEL_UI_FILT_BW, [3:0]=GYRO_UI_FILT_BW */
	CHECK_ERR(icm42688_select_bank(dev, 0) == ICM42688_OK, ICM42688_ERR);
	data = ((accel_bw & 0x0F) << 4) |
		   ((gyro_bw  & 0x0F));
	CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_GYRO_ACCEL_CONFIG0, &data, 1) == ICM42688_OK, ICM42688_ERR);

	dev->config.gyro_ui_filt_ord  = gyro_bw;
	dev->config.accel_ui_filt_ord = accel_bw;
	return ICM42688_OK;
}

icm42688_err_t icm42688_set_temp_filter_bw(icm42688_dev_t *dev, icm42688_temp_filt_bw_t temp_bw)
{
	CHECK_ERR(dev != NULL, ICM42688_ERR_PARAM);

	uint8_t data;

	/* GYRO_CONFIG1: [7:5] = TEMP_FILT_BW, [3:2]=GYRO_UI_FILT_ORD, [1:0]=DEC2_M2_ORD(fixed=0x02) */
	CHECK_ERR(icm42688_select_bank(dev, 0) == ICM42688_OK, ICM42688_ERR);
	CHECK_ERR(icm42688_read_reg(dev, ICM42688_REG_GYRO_CONFIG1, &data, 1) == ICM42688_OK, ICM42688_ERR);
	data = ((temp_bw & 0x03) << 5) | (data & 0x1F);
	CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_GYRO_CONFIG1, &data, 1) == ICM42688_OK, ICM42688_ERR);

	return ICM42688_OK;
}

icm42688_err_t icm42688_set_gyro_notch_filter(icm42688_dev_t        *dev,
                                               float                  freq_x,
                                               float                  freq_y,
                                               float                  freq_z,
                                               icm42688_gyro_nf_bw_t  bw)
{
    CHECK_ERR(dev != NULL, ICM42688_ERR_PARAM);

    /* Đọc CLKDIV từ Bank 3 để tính Fdrv */
    uint8_t reg;
    CHECK_ERR(icm42688_select_bank(dev, 3) == ICM42688_OK, ICM42688_ERR);
    CHECK_ERR(icm42688_read_reg(dev, ICM42688_REG_CLKDIV, &reg, 1) == ICM42688_OK, ICM42688_ERR);
    uint8_t clkdiv = reg & 0x3F;

    /* Fdrv = 19200 kHz / (clkdiv * 10), freq_x/y/z đơn vị Hz → ÷1000 để ra kHz */
    float Fdrv = 19200.0f / ((float)clkdiv * 10.0f);
    const float fdesired[3] = { freq_x / 1000.0f, freq_y / 1000.0f, freq_z / 1000.0f };

    uint8_t coswz_low[3] = {0};
    uint8_t coswz_sel_buf = 0;

    for (uint8_t i = 0; i < 3; i++)
    {
        float   coswz   = cosf(2.0f * (float)M_PI * fdesired[i] / Fdrv);
        uint16_t nf_val;

        if (coswz <= 0.875f)
        {
            nf_val       = (uint16_t)roundf(coswz * 256.0f);
            coswz_low[i] = (uint8_t)(nf_val & 0x00FF);
            coswz_sel_buf |= (uint8_t)(((nf_val & 0x0100) >> 8) << i);
        }
        else
        {
            coswz_sel_buf |= (uint8_t)(1 << (3 + i));
            if (coswz > 0.875f)
            {
                nf_val       = (uint16_t)roundf(8.0f * (1.0f - coswz) * 256.0f);
                coswz_low[i] = (uint8_t)(nf_val & 0x00FF);
                coswz_sel_buf |= (uint8_t)(((nf_val & 0x0100) >> 8) << i);
            }
            else if (coswz < -0.875f)
            {
                nf_val       = (uint16_t)roundf(-8.0f * (1.0f + coswz) * 256.0f);
                coswz_low[i] = (uint8_t)(nf_val & 0x00FF);
                coswz_sel_buf |= (uint8_t)(((nf_val & 0x0100) >> 8) << i);
            }
        }
    }

    CHECK_ERR(icm42688_select_bank(dev, 1) == ICM42688_OK, ICM42688_ERR);
    CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_GYRO_CONFIG_STATIC6,  &coswz_low[0],  1) == ICM42688_OK, ICM42688_ERR);
    CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_GYRO_CONFIG_STATIC7,  &coswz_low[1],  1) == ICM42688_OK, ICM42688_ERR);
    CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_GYRO_CONFIG_STATIC8,  &coswz_low[2],  1) == ICM42688_OK, ICM42688_ERR);
    CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_GYRO_CONFIG_STATIC9,  &coswz_sel_buf, 1) == ICM42688_OK, ICM42688_ERR);
    reg = (uint8_t)bw;
    CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_GYRO_CONFIG_STATIC10, &reg,           1) == ICM42688_OK, ICM42688_ERR);

    CHECK_ERR(icm42688_select_bank(dev, 0) == ICM42688_OK, ICM42688_ERR);
    return ICM42688_OK;
}

icm42688_err_t icm42688_compute_hardware_offsets(icm42688_dev_t *dev, uint16_t num_samples)
{
    CHECK_ERR((dev != NULL && num_samples != 0), ICM42688_ERR_PARAM);

    icm42688_gyro_fs_t  saved_gyro_fs  = dev->config.gyro_fs;
    icm42688_accel_fs_t saved_accel_fs = dev->config.accel_fs;

    CHECK_ERR(icm42688_set_gyro_fs(dev,  ICM42688_GYRO_FS_250DPS) == ICM42688_OK, ICM42688_ERR);
    CHECK_ERR(icm42688_set_accel_fs(dev, ICM42688_ACCEL_FS_2G)    == ICM42688_OK, ICM42688_ERR);

    CHECK_ERR(icm42688_select_bank(dev, 4) == ICM42688_OK, ICM42688_ERR);
    uint8_t zero = 0;
    for (uint8_t r = ICM42688_REG_OFFSET_USER0; r <= ICM42688_REG_OFFSET_USER8; r++)
    {
        CHECK_ERR(icm42688_write_reg(dev, r, &zero, 1) == ICM42688_OK, ICM42688_ERR);
    }
    CHECK_ERR(icm42688_select_bank(dev, 0) == ICM42688_OK, ICM42688_ERR);

    dev->bus.delay_ms(10);

    /* Thu thập raw samples */
    int32_t sum_gx = 0, sum_gy = 0, sum_gz = 0;
    int32_t sum_ax = 0, sum_ay = 0, sum_az = 0;
    uint8_t buf[12];

    for (uint16_t i = 0; i < num_samples; i++)
    {
        CHECK_ERR(icm42688_read_reg(dev, ICM42688_REG_ACCEL_DATA_X1, buf, 12) == ICM42688_OK, ICM42688_ERR);
        sum_ax += (int32_t)(int16_t)((buf[0] << 8) | buf[1]);
		sum_ay += (int32_t)(int16_t)((buf[2] << 8) | buf[3]);
		sum_az += (int32_t)(int16_t)((buf[4] << 8) | buf[5]);
        sum_gx += (int32_t)(int16_t)((buf[6] << 8) | buf[7]);
        sum_gy += (int32_t)(int16_t)((buf[8] << 8) | buf[9]);
        sum_gz += (int32_t)(int16_t)((buf[10] << 8) | buf[11]);

        dev->bus.delay_ms(2);
    }

    float avg_gx = (float)sum_gx / (float)num_samples;
    float avg_gy = (float)sum_gy / (float)num_samples;
    float avg_gz = (float)sum_gz / (float)num_samples;
    float avg_ax = (float)sum_ax / (float)num_samples;
    float avg_ay = (float)sum_ay / (float)num_samples;
    float avg_az = (float)sum_az / (float)num_samples;

    float gyro_sens  = ICM42688_GYRO_SENS_LUT[(uint8_t)dev->config.gyro_fs  & 0x07];
    float accel_sens = ICM42688_ACCEL_SENS_LUT[(uint8_t)dev->config.accel_fs & 0x03];

    dev->calib.gyro_offset.x = avg_gx / gyro_sens;
    dev->calib.gyro_offset.y = avg_gy / gyro_sens;
    dev->calib.gyro_offset.z = avg_gz / gyro_sens;

    float one_g     = accel_sens;
    float threshold = 0.8f * one_g;

    float raw_ax_f = avg_ax;
    float raw_ay_f = avg_ay;
    float raw_az_f = avg_az;

    if  (raw_ax_f >  threshold) raw_ax_f -= one_g;
    else if (raw_ax_f < -threshold) raw_ax_f += one_g;

    if  (raw_ay_f >  threshold) raw_ay_f -= one_g;
    else if (raw_ay_f < -threshold) raw_ay_f += one_g;

    if  (raw_az_f >  threshold) raw_az_f -= one_g;
    else if (raw_az_f < -threshold) raw_az_f += one_g;

    dev->calib.accel_offset.x = raw_ax_f / accel_sens;
    dev->calib.accel_offset.y = raw_ay_f / accel_sens;
    dev->calib.accel_offset.z = raw_az_f / accel_sens;

    CHECK_ERR(icm42688_set_gyro_fs(dev,  saved_gyro_fs)  == ICM42688_OK, ICM42688_ERR);
    CHECK_ERR(icm42688_set_accel_fs(dev, saved_accel_fs) == ICM42688_OK, ICM42688_ERR);

    return ICM42688_OK;
}

icm42688_err_t icm42688_set_hardware_offsets(icm42688_dev_t *dev)
{
    CHECK_ERR(dev != NULL, ICM42688_ERR_PARAM);

    /* Scale float offset → int12 hardware unit
     * Gyro:  1 LSB = 1/32 dps  → val = offset_dps × 32
     * Accel: 1 LSB = 0.5 mg    → val = offset_g × 2048
     *
     * Lưu ý: offset lưu trong dev->calib là giá trị trung bình đo được
     * (sai số điện tử), hardware cần trừ đi → negate khi ghi vào register
     */
    int16_t gx = (int16_t)(-dev->calib.gyro_offset.x  * 32.0f);
    int16_t gy = (int16_t)(-dev->calib.gyro_offset.y  * 32.0f);
    int16_t gz = (int16_t)(-dev->calib.gyro_offset.z  * 32.0f);
    int16_t ax = (int16_t)(-dev->calib.accel_offset.x * 2048.0f);
    int16_t ay = (int16_t)(-dev->calib.accel_offset.y * 2048.0f);
    int16_t az = (int16_t)(-dev->calib.accel_offset.z * 2048.0f);

    CHECK_ERR(icm42688_select_bank(dev, 4) == ICM42688_OK, ICM42688_ERR);

    /* Xóa toàn bộ trước */
    uint8_t zero = 0;
    for (uint8_t r = ICM42688_REG_OFFSET_USER0; r <= ICM42688_REG_OFFSET_USER8; r++)
    {
        CHECK_ERR(icm42688_write_reg(dev, r, &zero, 1) == ICM42688_OK, ICM42688_ERR);
    }

    /* Pack theo layout datasheet (12-bit signed, little-endian nibble):
     * USER0[7:0]       = GX[7:0]
     * USER1[7:4]       = GY[11:8],  USER1[3:0] = GX[11:8]
     * USER2[7:0]       = GY[7:0]
     * USER3[7:0]       = GZ[7:0]
     * USER4[7:4]       = AX[11:8],  USER4[3:0] = GZ[11:8]
     * USER5[7:0]       = AX[7:0]
     * USER6[7:0]       = AY[7:0]
     * USER7[7:4]       = AZ[11:8],  USER7[3:0] = AY[11:8]
     * USER8[7:0]       = AZ[7:0]
     */
    uint8_t reg;

    reg = (uint8_t)(gx & 0xFF);
    CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_OFFSET_USER0, &reg, 1) == ICM42688_OK, ICM42688_ERR);

    reg = (uint8_t)(((gy & 0x0F00) >> 4) | ((gx & 0x0F00) >> 8));
    CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_OFFSET_USER1, &reg, 1) == ICM42688_OK, ICM42688_ERR);

    reg = (uint8_t)(gy & 0xFF);
    CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_OFFSET_USER2, &reg, 1) == ICM42688_OK, ICM42688_ERR);

    reg = (uint8_t)(gz & 0xFF);
    CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_OFFSET_USER3, &reg, 1) == ICM42688_OK, ICM42688_ERR);

    reg = (uint8_t)(((ax & 0x0F00) >> 4) | ((gz & 0x0F00) >> 8));
    CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_OFFSET_USER4, &reg, 1) == ICM42688_OK, ICM42688_ERR);

    reg = (uint8_t)(ax & 0xFF);
    CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_OFFSET_USER5, &reg, 1) == ICM42688_OK, ICM42688_ERR);

    reg = (uint8_t)(ay & 0xFF);
    CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_OFFSET_USER6, &reg, 1) == ICM42688_OK, ICM42688_ERR);

    reg = (uint8_t)(((az & 0x0F00) >> 4) | ((ay & 0x0F00) >> 8));
    CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_OFFSET_USER7, &reg, 1) == ICM42688_OK, ICM42688_ERR);

    reg = (uint8_t)(az & 0xFF);
    CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_OFFSET_USER8, &reg, 1) == ICM42688_OK, ICM42688_ERR);

    CHECK_ERR(icm42688_select_bank(dev, 0) == ICM42688_OK, ICM42688_ERR);

    dev->config.use_calibrated = false;

    return ICM42688_OK;
}

icm42688_err_t icm42688_set_filter(icm42688_dev_t *dev, bool gyro_filter, bool accel_filter)
{
	uint8_t data;
	CHECK_ERR(icm42688_select_bank(dev, 1) == ICM42688_OK, ICM42688_ERR);
	if(gyro_filter == true)
	{
		/* GYRO_CONFIG_STATIC2: [7:3]= rsd, [1]=GYRO_AAF_DIS, [0]=GYRO_NF_DIS */
		CHECK_ERR(icm42688_select_bank(dev, 0) == ICM42688_OK, ICM42688_ERR);
		CHECK_ERR(icm42688_read_reg(dev, ICM42688_REG_GYRO_CONFIG_STATIC2, &data, 1) == ICM42688_OK, ICM42688_ERR);
		data = (data & 0x7C) | 0x00;
		CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_GYRO_CONFIG_STATIC2, &data, 1) == ICM42688_OK, ICM42688_ERR);
	}
	else
	{
		/* GYRO_CONFIG_STATIC2: [7:3]= rsd, [1]=GYRO_AAF_DIS, [0]=GYRO_NF_DIS */
		CHECK_ERR(icm42688_select_bank(dev, 0) == ICM42688_OK, ICM42688_ERR);
		CHECK_ERR(icm42688_read_reg(dev, ICM42688_REG_GYRO_CONFIG_STATIC2, &data, 1) == ICM42688_OK, ICM42688_ERR);
		data = (data & 0x7C) | 0x03;
		CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_GYRO_CONFIG_STATIC2, &data, 1) == ICM42688_OK, ICM42688_ERR);
	}

	CHECK_ERR(icm42688_select_bank(dev, 2) == ICM42688_OK, ICM42688_ERR);
	if(accel_filter == true)
	{
		/* ACCEL_CONFIG_STATIC2: [7]= rsd, [6:1]=ACCEL_AAF_DELT, [0]=ACCEL_AAF_DIS */
		CHECK_ERR(icm42688_select_bank(dev, 0) == ICM42688_OK, ICM42688_ERR);
		CHECK_ERR(icm42688_read_reg(dev, ICM42688_REG_ACCEL_CONFIG_STATIC2, &data, 1) == ICM42688_OK, ICM42688_ERR);
		data = (data & 0x7E) | 0x00;
		CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_ACCEL_CONFIG_STATIC2, &data, 1) == ICM42688_OK, ICM42688_ERR);
	}
	else
	{
		/* ACCEL_CONFIG_STATIC2: [7]= rsd, [6:1]=ACCEL_AAF_DELT, [0]=ACCEL_AAF_DIS */
		CHECK_ERR(icm42688_select_bank(dev, 0) == ICM42688_OK, ICM42688_ERR);
		CHECK_ERR(icm42688_read_reg(dev, ICM42688_REG_ACCEL_CONFIG_STATIC2, &data, 1) == ICM42688_OK, ICM42688_ERR);
		data = (data & 0x7E) | 0x01;
		CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_ACCEL_CONFIG_STATIC2, &data, 1) == ICM42688_OK, ICM42688_ERR);
	}

	dev->config.gyro_filter_enable 	= gyro_filter;
	dev->config.accel_filter_enable = accel_filter;

	return ICM42688_OK;
}

icm42688_err_t icm42688_get_calibration(const icm42688_dev_t *dev, icm42688_calibration_t *calib)
{
	CHECK_ERR((dev != NULL && calib != NULL), ICM42688_ERR_PARAM);

	*calib = dev->calib;
	return ICM42688_OK;
}

icm42688_err_t icm42688_soft_reset(icm42688_dev_t *dev)
{
	CHECK_ERR(dev != NULL, ICM42688_ERR_PARAM);

	uint8_t data;

	/* Ensure we're in Bank 0 */
	CHECK_ERR(icm42688_select_bank(dev, 0) == ICM42688_OK, ICM42688_ERR);

	/* Trigger soft reset: Set bit 0 of DEVICE_CONFIG */
	data = ICM42688_ENABLE_SOFT_RESET;
	CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_DEVICE_CONFIG, &data, 1) == ICM42688_OK, ICM42688_ERR);

	/* Wait for reset to complete (datasheet: max 1ms, add margin) */
	dev->bus.delay_ms(50);

	return ICM42688_OK;
}

icm42688_err_t icm42688_setup_interrupt(icm42688_dev_t *dev)
{
	CHECK_ERR(dev != NULL, ICM42688_ERR_PARAM);

	uint8_t data;

	/* Ensure Bank 0 */
	CHECK_ERR(icm42688_select_bank(dev, 0) == ICM42688_OK, ICM42688_ERR);

	/* INT_CONFIG: Configure interrupt pin
	* bit[2]=INT1_MODE (0=pulsed, 1=latched)
	* bit[1]=INT1_DRIVE_CIRCUIT (0=open drain, 1=push-pull)
	* bit[0]=INT1_POLARITY (0=active low, 1=active high)
	*/
	data = 0x03;  /* Push-pull, active high, pulsed */
	CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_INT_CONFIG, &data, 1) == ICM42688_OK, ICM42688_ERR);

	/* INT_SOURCE0: Enable UI data ready interrupt on INT1
	* bit[3]=UI_DRDY_INT1_EN
	*/
	data = 0x08;
	CHECK_ERR(icm42688_write_reg(dev, ICM42688_REG_INT_SOURCE0, &data, 1) == ICM42688_OK, ICM42688_ERR);

	return ICM42688_OK;
}

icm42688_err_t icm42688_clear_interrupt(icm42688_dev_t *dev)
{
	CHECK_ERR(dev != NULL, ICM42688_ERR_PARAM);

	uint8_t data;
	/* Read INT_STATUS to clear interrupt */
	return icm42688_read_reg(dev, ICM42688_REG_INT_STATUS, &data, 1);
}

void icm42688_irq_handler(icm42688_dev_t *dev)
{
	if (!dev)
		return;

	dev->data_ready = true;
}

icm42688_err_t icm42688_self_test(icm42688_dev_t *dev)
{
	CHECK_ERR(dev != NULL, ICM42688_ERR_PARAM);

	/* Self-test implementation (simplified)
	* Full self-test requires complex procedures - see datasheet section 4.9
	* This is a placeholder for basic WHO_AM_I verification
	*/
	uint8_t data;

	CHECK_ERR(icm42688_select_bank(dev, 0) == ICM42688_OK, ICM42688_ERR);

	CHECK_ERR(icm42688_read_reg(dev, ICM42688_REG_WHO_AM_I, &data, 1) == ICM42688_OK, ICM42688_ERR);

	return (data == ICM42688_WHO_AM_I_VALUE) ? ICM42688_OK : ICM42688_ERR;
}

/* Private function implementation ------------------------------------------ */
static inline uint8_t icm42688_who_am_i(icm42688_dev_t *dev)
{
	uint8_t data;

	CHECK_ERR(icm42688_select_bank(dev, 0) == ICM42688_OK, ICM42688_ERR);
	CHECK_ERR(icm42688_read_reg(dev, ICM42688_REG_WHO_AM_I, &data, 1) == ICM42688_OK, ICM42688_ERR);

	return data;
}

icm42688_err_t icm42688_read_reg(icm42688_dev_t *dev, uint8_t reg, uint8_t *data, uint16_t len)
{
	CHECK_ERR((dev != NULL && data != NULL && len != 0), ICM42688_ERR_PARAM);

	uint8_t tx_buf[1] = {reg | ICM42688_SPI_READ};
	uint8_t dummy_rx;

	dev->bus.set_cs(true);
	dev->bus.delay_us(10);  /* CS setup time */

	/* Send register address */
	if (!dev->bus.spi_transfer(tx_buf, &dummy_rx, 1))
	{
		dev->bus.set_cs(false);
		return ICM42688_ERR;
	}

	/* Small delay between address and data (optional but safe) */
	dev->bus.delay_us(1);

	/* Read data */
	if (!dev->bus.spi_transfer(NULL, data, len))
	{
		dev->bus.set_cs(false);
		return ICM42688_ERR;
	}

	dev->bus.set_cs(false);
	dev->bus.delay_us(10);  /* CS hold time */

	return ICM42688_OK;
}

icm42688_err_t icm42688_write_reg(icm42688_dev_t *dev, uint8_t reg, const uint8_t *data, uint16_t len)
{
	CHECK_ERR((dev != NULL && data != NULL && len != 0), ICM42688_ERR_PARAM);

	uint8_t tx_buf[64];
	CHECK_ERR((len + 1 < sizeof(tx_buf)), ICM42688_ERR_PARAM);

	tx_buf[0] = reg | ICM42688_SPI_WRITE;
	memcpy(&tx_buf[1], data, len);

	dev->bus.set_cs(true);
	dev->bus.delay_us(1);

	bool result = dev->bus.spi_transfer(tx_buf, NULL, len + 1);

	dev->bus.set_cs(false);
	dev->bus.delay_us(1);  /* Inter-transaction delay */

	return result ? ICM42688_OK : ICM42688_ERR;
}

static icm42688_err_t icm42688_select_bank(icm42688_dev_t *dev, uint8_t bank)
{
	CHECK_ERR((dev != NULL && bank <= 4), ICM42688_ERR_PARAM);

	uint8_t data = bank & 0x07;
	return icm42688_write_reg(dev, ICM42688_REG_REG_BANK_SEL, &data, 1);
}

/* End of file -------------------------------------------------------- */
