/**
 * @file       icm45686.h
 * @copyright
 * @license
 * @version    0.1.0
 * @date       2025
 * @author     Phuong Mai
 * @brief      ICM-45686 6-axis IMU driver với low-noise configuration
 * @note       SPI mode 0/3, max 24MHz, MSB first
 * @example    None
 */

#include "icm45686.h"
#include <string.h>
#include <math.h>
#include <stdlib.h>

/* Private defines ---------------------------------------------------------- */
#define ICM_SPI_READ  0x80
#define ICM_SPI_WRITE 0x00

/* Private function prototypes ---------------------------------------------- */
static icm_err_t icm_select_bank(icm45686_t *dev, uint8_t bank);
static icm_err_t icm_soft_reset(icm45686_t *dev);
static void      icm_apply_calibration(const icm45686_t *dev, 
                                        icm_axis_raw_t *gyro, 
                                        icm_axis_raw_t *accel);

/* Public function implementation ------------------------------------------- */

icm_err_t icm_read_reg(icm45686_t *dev, uint8_t reg, uint8_t *data, uint16_t len)
{
  if (!dev || !data || len == 0)
    return ICM_ERR_PARAM;

  uint8_t tx_buf[257];  /* 1 byte address + up to 256 data bytes */
  uint8_t rx_buf[257];
  
  if (len + 1 > sizeof(tx_buf))
    return ICM_ERR_PARAM;

  /* Prepare TX buffer: address + dummy bytes for RX */
  tx_buf[0] = reg | ICM_SPI_READ;
  for (uint16_t i = 1; i <= len; i++)
    tx_buf[i] = 0x00;  /* Dummy bytes */

  dev->bus.set_cs(true);
  dev->bus.delay_us(10);  /* Longer setup time */
  
  /* Send address + read data in one transaction */
  if (!dev->bus.spi_transfer(tx_buf, rx_buf, len + 1))
  {
    dev->bus.set_cs(false);
    return ICM_ERR;
  }
  
  dev->bus.delay_us(10);  /* Longer hold time */
  dev->bus.set_cs(false);
  
  /* Copy received data (skip first byte which is during address transmission) */
  for (uint16_t i = 0; i < len; i++)
    data[i] = rx_buf[i + 1];
  
  return ICM_OK;
}

icm_err_t icm_write_reg(icm45686_t *dev, uint8_t reg, const uint8_t *data, uint16_t len)
{
  if (!dev || !data || len == 0)
    return ICM_ERR_PARAM;

  uint8_t tx_buf[64]; /* Adjust size as needed */
  if (len + 1 > sizeof(tx_buf))
    return ICM_ERR_PARAM;

  tx_buf[0] = reg | ICM_SPI_WRITE;
  for (uint16_t i = 0; i < len; i++)
    tx_buf[i + 1] = data[i];

  dev->bus.set_cs(true);
  dev->bus.delay_us(1);
  
  bool result = dev->bus.spi_transfer(tx_buf, NULL, len + 1);
  
  dev->bus.set_cs(false);
  return result ? ICM_OK : ICM_ERR;
}

static icm_err_t icm_select_bank(icm45686_t *dev, uint8_t bank)
{
  uint8_t data = bank & 0x07;
  return icm_write_reg(dev, ICM_REG_REG_BANK_SEL, &data, 1);
}

static icm_err_t icm_soft_reset(icm45686_t *dev)
{
  uint8_t data;
  icm_err_t err;

  /* Set SOFT_RESET bit in SIGNAL_PATH_RESET register */
  data = 0x02;
  err = icm_write_reg(dev, ICM_REG_SIGNAL_PATH_RESET, &data, 1);
  if (err != ICM_OK)
    return err;

  /* Wait for reset to complete */
  dev->bus.delay_us(1000);

  return ICM_OK;
}

icm_err_t icm_init(icm45686_t *dev, const icm_config_t *config)
{
  if (!dev || !config)
    return ICM_ERR_PARAM;

  icm_err_t err;
  uint8_t   data;

  /* Initialize calibration to zero */
  memset(&dev->calib, 0, sizeof(icm_calibration_t));

  /* Verify WHO_AM_I */
  err = icm_read_reg(dev, ICM_REG_WHO_AM_I, &data, 1);
  if (err != ICM_OK || data != ICM_WHO_AM_I_VALUE)
    return ICM_ERR;

  /* Soft reset */
  err = icm_soft_reset(dev);
  if (err != ICM_OK)
    return err;

  /* Select bank 0 */
  err = icm_select_bank(dev, 0);
  if (err != ICM_OK)
    return err;

  /* Configure gyro */
  err = icm_set_gyro_config(dev, config->gyro_fs, config->gyro_odr);
  if (err != ICM_OK)
    return err;

  /* Configure accel */
  err = icm_set_accel_config(dev, config->accel_fs, config->accel_odr);
  if (err != ICM_OK)
    return err;

  /* Configure filters (low-noise + high-res) */
  icm_filter_config_t filter = {
    .use_low_noise_mode = config->use_low_noise_mode,
    .use_high_resolution = config->use_high_resolution,
    .gyro_notch = {.enable = false},
    .accel_notch = {.enable = false},
    .gyro_ui_filt_ord = 2,   /* 3rd order (recommended) */
    .accel_ui_filt_ord = 2
  };
  
  err = icm_set_filter_config(dev, &filter);
  if (err != ICM_OK)
    return err;

  /* Power management: Enable gyro and accel in low-noise mode */
  data = config->use_low_noise_mode ? 0x0F : 0x00;
  err = icm_write_reg(dev, ICM_REG_PWR_MGMT0, &data, 1);
  if (err != ICM_OK)
    return err;

  /* Wait for sensors to stabilize */
  dev->bus.delay_us(50000); /* 50ms */

  /* Save config */
  dev->config = *config;
  dev->data_ready = false;

  return ICM_OK;
}

icm_err_t icm_setup_interrupt(icm45686_t *dev)
{
  if (!dev)
    return ICM_ERR_PARAM;

  icm_err_t err;
  uint8_t   data;

  /* INT_CONFIG: Configure interrupt pin
   * bit[2] = INT1_DRIVE_CIRCUIT (0=Open Drain, 1=Push Pull)
   * bit[1] = INT1_POLARITY (0=Active Low, 1=Active High)
   */
  data = 0x06; /* Push-pull, active high */
  err = icm_write_reg(dev, ICM_REG_INT_CONFIG, &data, 1);
  if (err != ICM_OK)
    return err;

  /* INT_CONFIG0: Interrupt behavior */
  data = 0x00; /* Clear on status read */
  err = icm_write_reg(dev, ICM_REG_INT_CONFIG0, &data, 1);
  if (err != ICM_OK)
    return err;

  /* INT_SOURCE0: Enable data ready interrupt
   * bit[3] = UI_DRDY_INT1_EN (Data Ready)
   */
  data = 0x08;
  err = icm_write_reg(dev, ICM_REG_INT_SOURCE0, &data, 1);
  if (err != ICM_OK)
    return err;

  return ICM_OK;
}

icm_err_t icm_clear_interrupt(icm45686_t *dev)
{
  if (!dev)
    return ICM_ERR_PARAM;

  uint8_t data;
  /* Read INT_STATUS to clear */
  return icm_read_reg(dev, ICM_REG_INT_STATUS, &data, 1);
}

void icm_irq_handler(icm45686_t *dev)
{
  if (dev)
  {
    dev->data_ready = true;
  }
}

icm_err_t icm_set_gyro_config(icm45686_t *dev, icm_gyro_fs_t fs, icm_odr_t odr)
{
  if (!dev)
    return ICM_ERR_PARAM;

  uint8_t data = (fs << 5) | (odr & 0x0F);
  icm_err_t err = icm_write_reg(dev, ICM_REG_GYRO_CONFIG0, &data, 1);
  if (err == ICM_OK)
  {
    dev->config.gyro_fs = fs;
    dev->config.gyro_odr = odr;
  }
  return err;
}

icm_err_t icm_set_accel_config(icm45686_t *dev, icm_accel_fs_t fs, icm_odr_t odr)
{
  if (!dev)
    return ICM_ERR_PARAM;

  uint8_t data = (fs << 6) | (odr & 0x0F);
  icm_err_t err = icm_write_reg(dev, ICM_REG_ACCEL_CONFIG0, &data, 1);
  if (err == ICM_OK)
  {
    dev->config.accel_fs = fs;
    dev->config.accel_odr = odr;
  }
  return err;
}

icm_err_t icm_set_filter_config(icm45686_t *dev, const icm_filter_config_t *filter)
{
  if (!dev || !filter)
    return ICM_ERR_PARAM;

  icm_err_t err;
  uint8_t   data;

  /* ========== Bank 0: Low-noise mode (AAF) ========== */
  
  /* Configure AAF for gyro */
  data = filter->use_low_noise_mode ? 0x10 : 0x00;
  err = icm_write_reg(dev, ICM_REG_GYRO_CONFIG1, &data, 1);
  if (err != ICM_OK)
    return err;

  /* Configure AAF for accel */
  data = filter->use_low_noise_mode ? 0x10 : 0x00;
  err = icm_write_reg(dev, ICM_REG_ACCEL_CONFIG1, &data, 1);
  if (err != ICM_OK)
    return err;

  /* ========== Bank 1: High-res + Gyro filters ========== */
  
  err = icm_select_bank(dev, 1);
  if (err != ICM_OK)
    return err;

  /* High resolution mode */
  if (filter->use_high_resolution)
  {
    if (dev->config.accel_fs < ICM_ACCEL_FS_4G || 
        dev->config.gyro_fs < ICM_GYRO_FS_250)
    {
      icm_select_bank(dev, 0);
      return ICM_ERR_PARAM;
    }
  }
  
  data = filter->use_high_resolution ? 0x01 : 0x00;
  err = icm_write_reg(dev, ICM_REG_INTF_CONFIG5, &data, 1);
  if (err != ICM_OK)
    goto exit_bank1;

  /* Gyro UI filter order */
  data = filter->gyro_ui_filt_ord & 0x03;
  err = icm_write_reg(dev, ICM_REG_GYRO_CONFIG_STATIC2, &data, 1);
  if (err != ICM_OK)
    goto exit_bank1;

  /* Gyro notch filter */
  if (filter->gyro_notch.enable)
  {
    float odr_hz = 1000.0f; /* Adjust according to config */
    float normalized_freq = (float)filter->gyro_notch.frequency_hz / odr_hz;
    float coswz = cosf(2.0f * 3.14159f * normalized_freq);
    int16_t coswz_val = (int16_t)(coswz * 16384.0f);

    data = (uint8_t)((coswz_val >> 8) & 0xFF);
    err = icm_write_reg(dev, ICM_REG_GYRO_CONFIG_STATIC3, &data, 1);
    if (err != ICM_OK)
      goto exit_bank1;

    data = (uint8_t)(coswz_val & 0xFF);
    err = icm_write_reg(dev, ICM_REG_GYRO_CONFIG_STATIC4, &data, 1);
    if (err != ICM_OK)
      goto exit_bank1;

    data = 0x01 | ((filter->gyro_notch.bandwidth & 0x03) << 1);
    err = icm_write_reg(dev, ICM_REG_GYRO_CONFIG_STATIC5, &data, 1);
  }
  else
  {
    data = 0x00;
    err = icm_write_reg(dev, ICM_REG_GYRO_CONFIG_STATIC5, &data, 1);
  }

exit_bank1:
  if (err != ICM_OK)
  {
    icm_select_bank(dev, 0);
    return err;
  }

  /* ========== Bank 2: Accel notch filter ========== */
  
  err = icm_select_bank(dev, 2);
  if (err != ICM_OK)
    return err;

  if (filter->accel_notch.enable)
  {
    float odr_hz = 1000.0f;
    float normalized_freq = (float)filter->accel_notch.frequency_hz / odr_hz;
    float coswz = cosf(2.0f * 3.14159f * normalized_freq);
    int16_t coswz_val = (int16_t)(coswz * 16384.0f);

    data = (uint8_t)((coswz_val >> 8) & 0xFF);
    err = icm_write_reg(dev, ICM_REG_ACCEL_CONFIG_STATIC2, &data, 1);
    if (err != ICM_OK)
      goto exit_bank2;

    data = (uint8_t)(coswz_val & 0xFF);
    err = icm_write_reg(dev, ICM_REG_ACCEL_CONFIG_STATIC3, &data, 1);
    if (err != ICM_OK)
      goto exit_bank2;

    data = 0x01 | ((filter->accel_notch.bandwidth & 0x03) << 1);
    err = icm_write_reg(dev, ICM_REG_ACCEL_CONFIG_STATIC4, &data, 1);
  }
  else
  {
    data = 0x00;
    err = icm_write_reg(dev, ICM_REG_ACCEL_CONFIG_STATIC4, &data, 1);
  }

exit_bank2:
  icm_select_bank(dev, 0);
  
  if (err == ICM_OK)
  {
    dev->config.use_low_noise_mode = filter->use_low_noise_mode;
    dev->config.use_high_resolution = filter->use_high_resolution;
  }

  return err;
}

icm_err_t icm_run_selftest(icm45686_t *dev, icm_selftest_result_t *result)
{
  if (!dev || !result)
    return ICM_ERR_PARAM;

  icm_err_t      err;
  uint8_t        data;
  icm_axis_raw_t gyro_normal, accel_normal;
  icm_axis_raw_t gyro_test, accel_test;
  
  /* Initialize result */
  result->all_pass = false;

  /* Step 1: Read normal output */
  dev->bus.delay_us(20000); /* 20ms settle */
  err = icm_get_gyro(dev, &gyro_normal);
  if (err != ICM_OK)
    return err;
  err = icm_get_accel(dev, &accel_normal);
  if (err != ICM_OK)
    return err;

  /* Step 2: Enable self-test
   * SELF_TEST_CONFIG: bits[5:3]=accel, bits[2:0]=gyro
   * Set all to 1 to enable test
   */
  data = 0x38 | 0x07; /* Enable all axes */
  err = icm_write_reg(dev, ICM_REG_SELF_TEST_CONFIG, &data, 1);
  if (err != ICM_OK)
    return err;

  /* Wait for self-test to complete */
  dev->bus.delay_us(20000); /* 20ms */

  /* Step 3: Read test output */
  err = icm_get_gyro(dev, &gyro_test);
  if (err != ICM_OK)
    goto cleanup;
  err = icm_get_accel(dev, &accel_test);
  if (err != ICM_OK)
    goto cleanup;

  /* Step 4: Calculate self-test response (STR)
   * STR = |test_output - normal_output|
   * Pass criteria: STR > minimum threshold
   */
  int32_t gyro_str_x = abs(gyro_test.x - gyro_normal.x);
  int32_t gyro_str_y = abs(gyro_test.y - gyro_normal.y);
  int32_t gyro_str_z = abs(gyro_test.z - gyro_normal.z);
  int32_t accel_str_x = abs(accel_test.x - accel_normal.x);
  int32_t accel_str_y = abs(accel_test.y - accel_normal.y);
  int32_t accel_str_z = abs(accel_test.z - accel_normal.z);

  /* Pass criteria (approximate values, adjust theo datasheet) */
  result->gyro_x_pass = (gyro_str_x > 500);   /* Min threshold */
  result->gyro_y_pass = (gyro_str_y > 500);
  result->gyro_z_pass = (gyro_str_z > 500);
  result->accel_x_pass = (accel_str_x > 200);
  result->accel_y_pass = (accel_str_y > 200);
  result->accel_z_pass = (accel_str_z > 200);

  result->all_pass = result->gyro_x_pass && result->gyro_y_pass && 
                     result->gyro_z_pass && result->accel_x_pass &&
                     result->accel_y_pass && result->accel_z_pass;

cleanup:
  /* Disable self-test */
  data = 0x00;
  icm_write_reg(dev, ICM_REG_SELF_TEST_CONFIG, &data, 1);
  
  /* Wait for sensor to stabilize */
  dev->bus.delay_us(50000); /* 50ms */

  return err;
}

/**
 * @brief  Apply calibration offset to raw sensor data
 */
static void icm_apply_calibration(const icm45686_t *dev, 
                                   icm_axis_raw_t *gyro, 
                                   icm_axis_raw_t *accel)
{
  if (!dev || !gyro || !accel)
    return;

  /* Apply gyro offset */
  gyro->x -= dev->calib.gyro_offset.x;
  gyro->y -= dev->calib.gyro_offset.y;
  gyro->z -= dev->calib.gyro_offset.z;

  /* Apply accel offset */
  accel->x -= dev->calib.accel_offset.x;
  accel->y -= dev->calib.accel_offset.y;
  accel->z -= dev->calib.accel_offset.z;
}

icm_err_t icm_calibrate_gyro_offset(icm45686_t *dev, uint16_t num_samples)
{
  if (!dev || num_samples == 0)
    return ICM_ERR_PARAM;

  icm_err_t      err;
  icm_axis_raw_t gyro;
  int32_t        sum_x = 0, sum_y = 0, sum_z = 0;

  /* Temporarily disable existing calibration */
  icm_axis_offset_t temp_offset = dev->calib.gyro_offset;
  memset(&dev->calib.gyro_offset, 0, sizeof(icm_axis_offset_t));

  /* Collect samples */
  for (uint16_t i = 0; i < num_samples; i++)
  {
    err = icm_get_gyro(dev, &gyro);
    if (err != ICM_OK)
    {
      dev->calib.gyro_offset = temp_offset; /* Restore */
      return err;
    }

    sum_x += gyro.x;
    sum_y += gyro.y;
    sum_z += gyro.z;

    dev->bus.delay_us(10000); /* 10ms between samples */
  }

  /* Calculate average offset */
  if (num_samples > 0)
  {
    dev->calib.gyro_offset.x = (int16_t)(sum_x / num_samples);
    dev->calib.gyro_offset.y = (int16_t)(sum_y / num_samples);
    dev->calib.gyro_offset.z = (int16_t)(sum_z / num_samples);
  }

  return ICM_OK;
}

icm_err_t icm_calibrate_accel_offset(icm45686_t *dev, uint16_t num_samples)
{
  if (!dev || num_samples == 0)
    return ICM_ERR_PARAM;

  icm_err_t      err;
  icm_axis_raw_t accel;
  int32_t        sum_x = 0, sum_y = 0, sum_z = 0;

  /* Get 1g value based on current full-scale setting */
  int16_t one_g;
  switch (dev->config.accel_fs)
  {
    case ICM_ACCEL_FS_16G: one_g = 2048;  break;
    case ICM_ACCEL_FS_8G:  one_g = 4096;  break;
    case ICM_ACCEL_FS_4G:  one_g = 8192;  break;
    case ICM_ACCEL_FS_2G:  one_g = 16384; break;
    default: return ICM_ERR_PARAM;
  }

  /* Temporarily disable existing calibration */
  icm_axis_offset_t temp_offset = dev->calib.accel_offset;
  memset(&dev->calib.accel_offset, 0, sizeof(icm_axis_offset_t));

  /* Collect samples */
  for (uint16_t i = 0; i < num_samples; i++)
  {
    err = icm_get_accel(dev, &accel);
    if (err != ICM_OK)
    {
      dev->calib.accel_offset = temp_offset; /* Restore */
      return err;
    }

    sum_x += accel.x;
    sum_y += accel.y;
    sum_z += accel.z;

    dev->bus.delay_us(10000); /* 10ms */
  }

  /* Calculate offset: X/Y should be 0, Z should be 1g */
  if (num_samples > 0)
  {
    dev->calib.accel_offset.x = (int16_t)(sum_x / num_samples);
    dev->calib.accel_offset.y = (int16_t)(sum_y / num_samples);
    dev->calib.accel_offset.z = (int16_t)(sum_z / num_samples) - one_g;
  }

  return ICM_OK;
}

icm_err_t icm_set_calibration(icm45686_t *dev, const icm_calibration_t *calib)
{
  if (!dev || !calib)
    return ICM_ERR_PARAM;

  dev->calib = *calib;
  return ICM_OK;
}

icm_err_t icm_get_calibration(icm45686_t *dev, icm_calibration_t *calib)
{
  if (!dev || !calib)
    return ICM_ERR_PARAM;

  *calib = dev->calib;
  return ICM_OK;
}

icm_err_t icm_get_status(icm45686_t *dev, icm_status_t *status)
{
  if (!dev || !status)
    return ICM_ERR_PARAM;

  uint8_t   int_status;
  icm_err_t err;

  err = icm_read_reg(dev, ICM_REG_INT_STATUS, &int_status, 1);
  if (err != ICM_OK)
    return err;

  /* Parse status bits */
  status->data_ready = (int_status & 0x08) ? true : false;
  status->fifo_overflow = (int_status & 0x04) ? true : false;
  status->fifo_watermark = (int_status & 0x02) ? true : false;

  return ICM_OK;
}

void icm_convert_gyro_to_dps(const icm45686_t *dev, 
                             const icm_axis_raw_t *raw, 
                             icm_axis_float_t *dps)
{
  if (!dev || !raw || !dps)
    return;

  float sensitivity;

  /* Get sensitivity based on full-scale range */
  if (dev->config.use_high_resolution)
  {
    /* 20-bit mode: only ±250dps supported */
    sensitivity = 2097.0f; /* LSB/dps */
  }
  else
  {
    /* 16-bit mode */
    switch (dev->config.gyro_fs)
    {
      case ICM_GYRO_FS_2000: sensitivity = 16.4f;  break;
      case ICM_GYRO_FS_1000: sensitivity = 32.8f;  break;
      case ICM_GYRO_FS_500:  sensitivity = 65.5f;  break;
      case ICM_GYRO_FS_250:  sensitivity = 131.0f; break;
      default: sensitivity = 32.8f;
    }
  }

  dps->x = (float)raw->x / sensitivity;
  dps->y = (float)raw->y / sensitivity;
  dps->z = (float)raw->z / sensitivity;
}

void icm_convert_accel_to_g(const icm45686_t *dev, 
                            const icm_axis_raw_t *raw, 
                            icm_axis_float_t *g)
{
  if (!dev || !raw || !g)
    return;

  float sensitivity;

  /* Get sensitivity based on full-scale range */
  if (dev->config.use_high_resolution)
  {
    /* 20-bit mode */
    switch (dev->config.accel_fs)
    {
      case ICM_ACCEL_FS_4G: sensitivity = 131072.0f; break;
      case ICM_ACCEL_FS_2G: sensitivity = 262144.0f; break;
      default: sensitivity = 131072.0f;
    }
  }
  else
  {
    /* 16-bit mode */
    switch (dev->config.accel_fs)
    {
      case ICM_ACCEL_FS_16G: sensitivity = 2048.0f;  break;
      case ICM_ACCEL_FS_8G:  sensitivity = 4096.0f;  break;
      case ICM_ACCEL_FS_4G:  sensitivity = 8192.0f;  break;
      case ICM_ACCEL_FS_2G:  sensitivity = 16384.0f; break;
      default: sensitivity = 8192.0f;
    }
  }

  g->x = (float)raw->x / sensitivity;
  g->y = (float)raw->y / sensitivity;
  g->z = (float)raw->z / sensitivity;
}

float icm_convert_temp_to_celsius(int16_t raw_temp)
{
  return ((float)raw_temp / 132.48f) + 25.0f;
}

icm_err_t icm_get_gyro(icm45686_t *dev, icm_axis_raw_t *gyro)
{
  if (!dev || !gyro)
    return ICM_ERR_PARAM;

  uint8_t   data[6];
  icm_err_t err;

  err = icm_read_reg(dev, ICM_REG_GYRO_DATA_X1, data, 6);
  if (err != ICM_OK)
    return err;

  gyro->x = (int16_t)((data[0] << 8) | data[1]);
  gyro->y = (int16_t)((data[2] << 8) | data[3]);
  gyro->z = (int16_t)((data[4] << 8) | data[5]);

  /* Apply calibration offset */
  gyro->x -= dev->calib.gyro_offset.x;
  gyro->y -= dev->calib.gyro_offset.y;
  gyro->z -= dev->calib.gyro_offset.z;

  return ICM_OK;
}

icm_err_t icm_get_accel(icm45686_t *dev, icm_axis_raw_t *accel)
{
  if (!dev || !accel)
    return ICM_ERR_PARAM;

  uint8_t   data[6];
  icm_err_t err;

  err = icm_read_reg(dev, ICM_REG_ACCEL_DATA_X1, data, 6);
  if (err != ICM_OK)
    return err;

  accel->x = (int16_t)((data[0] << 8) | data[1]);
  accel->y = (int16_t)((data[2] << 8) | data[3]);
  accel->z = (int16_t)((data[4] << 8) | data[5]);

  /* Apply calibration offset */
  accel->x -= dev->calib.accel_offset.x;
  accel->y -= dev->calib.accel_offset.y;
  accel->z -= dev->calib.accel_offset.z;

  return ICM_OK;
}

icm_err_t icm_get_temp(icm45686_t *dev, int16_t *temp)
{
  if (!dev || !temp)
    return ICM_ERR_PARAM;

  uint8_t   data[2];
  icm_err_t err;

  err = icm_read_reg(dev, ICM_REG_TEMP_DATA1, data, 2);
  if (err != ICM_OK)
    return err;

  *temp = (int16_t)((data[0] << 8) | data[1]);

  return ICM_OK;
}

icm_err_t icm_get_all_data(icm45686_t *dev, icm_sensor_data_t *data)
{
  if (!dev || !data)
    return ICM_ERR_PARAM;

  icm_err_t err;

  if (dev->config.use_high_resolution)
  {
    /* 20-bit mode: read additional LSB bytes */
    uint8_t buf[17]; /* accel(6) + gyro(6) + temp(2) + LSB(3) */
    
    /* Read from ACCEL_DATA_X1 through GYRO_DATA_Z0 + LSB bytes */
    err = icm_read_reg(dev, ICM_REG_ACCEL_DATA_X1, buf, 17);
    if (err != ICM_OK)
      return err;

    /* Parse accel (20-bit) */
    data->accel.x = (int16_t)((buf[0] << 8) | buf[1]);
    data->accel.y = (int16_t)((buf[2] << 8) | buf[3]);
    data->accel.z = (int16_t)((buf[4] << 8) | buf[5]);

    /* Parse gyro (20-bit) */
    data->gyro.x = (int16_t)((buf[6] << 8) | buf[7]);
    data->gyro.y = (int16_t)((buf[8] << 8) | buf[9]);
    data->gyro.z = (int16_t)((buf[10] << 8) | buf[11]);

    /* Parse temp */
    data->temp = (int16_t)((buf[12] << 8) | buf[13]);

    /* LSB bytes at buf[14], buf[15], buf[16]
     * Can extend precision if needed:
     * accel_x_20bit = (data->accel.x << 4) | ((buf[14] >> 4) & 0x0F);
     */
  }
  else
  {
    /* 16-bit mode: standard read */
    uint8_t buf[14]; /* accel(6) + gyro(6) + temp(2) */

    err = icm_read_reg(dev, ICM_REG_ACCEL_DATA_X1, buf, 14);
    if (err != ICM_OK)
      return err;

    /* Parse accel */
    data->accel.x = (int16_t)((buf[0] << 8) | buf[1]);
    data->accel.y = (int16_t)((buf[2] << 8) | buf[3]);
    data->accel.z = (int16_t)((buf[4] << 8) | buf[5]);

    /* Parse gyro */
    data->gyro.x = (int16_t)((buf[6] << 8) | buf[7]);
    data->gyro.y = (int16_t)((buf[8] << 8) | buf[9]);
    data->gyro.z = (int16_t)((buf[10] << 8) | buf[11]);

    /* Parse temp */
    data->temp = (int16_t)((buf[12] << 8) | buf[13]);
  }

  /* Apply calibration to both gyro and accel */
  icm_apply_calibration(dev, &data->gyro, &data->accel);

  return ICM_OK;
}

/* End of file -------------------------------------------------------------- */