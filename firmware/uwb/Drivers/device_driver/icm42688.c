/**
 * @file       icm42688.c
 * @copyright
 * @license
 * @version    1.0.0
 * @date       2026-01-10
 * @author     Phuong Mai
 * @brief      ICM-42688-P driver implementation
 * @note       Based on ICM-42688-P datasheet Rev 1.8
 * @example    None
 */

#include "icm42688.h"
#include <string.h>
#include <math.h>

/* Private defines ---------------------------------------------------------- */
#define ICM42_SPI_READ  0x80
#define ICM42_SPI_WRITE 0x00

/* Sensitivity scale factors (from datasheet) */
#define ICM42_GYRO_SENSITIVITY_2000DPS   16.4f    /* LSB/(dps) */
#define ICM42_GYRO_SENSITIVITY_1000DPS   32.8f
#define ICM42_GYRO_SENSITIVITY_500DPS    65.5f
#define ICM42_GYRO_SENSITIVITY_250DPS    131.0f
#define ICM42_GYRO_SENSITIVITY_125DPS    262.0f
#define ICM42_GYRO_SENSITIVITY_62_5DPS   524.3f
#define ICM42_GYRO_SENSITIVITY_31_25DPS  1048.6f
#define ICM42_GYRO_SENSITIVITY_15_625DPS 2097.2f

#define ICM42_ACCEL_SENSITIVITY_16G      2048.0f  /* LSB/g */
#define ICM42_ACCEL_SENSITIVITY_8G       4096.0f
#define ICM42_ACCEL_SENSITIVITY_4G       8192.0f
#define ICM42_ACCEL_SENSITIVITY_2G       16384.0f

/* Temperature sensitivity: 132.48 LSB/°C, offset = 25°C */
#define ICM42_TEMP_SENSITIVITY           132.48f
#define ICM42_TEMP_OFFSET                25.0f

/* Private function prototypes ---------------------------------------------- */
static icm42_err_t icm42_select_bank(icm42_dev_t *dev, uint8_t bank);
static void icm42_apply_calibration(const icm42_dev_t *dev,
                                     icm42_axis_raw_t *gyro,
                                     icm42_axis_raw_t *accel);
static float icm42_get_gyro_sensitivity(icm42_gyro_fs_t fs);
static float icm42_get_accel_sensitivity(icm42_accel_fs_t fs);

/* Public function implementation ------------------------------------------- */

icm42_err_t icm42_read_reg(icm42_dev_t *dev, uint8_t reg, uint8_t *data, uint16_t len)
{
  if (!dev || !data || len == 0)
    return ICM42_ERR_PARAM;

  uint8_t tx_buf[1] = {reg | ICM42_SPI_READ};
  uint8_t dummy_rx;

  dev->bus.set_cs(true);
  dev->bus.delay_us(10);  /* CS setup time */

  /* Send register address */
  if (!dev->bus.spi_transfer(tx_buf, &dummy_rx, 1))
  {
    dev->bus.set_cs(false);
    return ICM42_ERR;
  }

  /* Small delay between address and data (optional but safe) */
  dev->bus.delay_us(1);

  /* Read data */
  if (!dev->bus.spi_transfer(NULL, data, len))
  {
    dev->bus.set_cs(false);
    return ICM42_ERR;
  }

  dev->bus.set_cs(false);
  dev->bus.delay_us(10);  /* CS hold time */
  
  return ICM42_OK;
}

icm42_err_t icm42_write_reg(icm42_dev_t *dev, uint8_t reg, const uint8_t *data, uint16_t len)
{
  if (!dev || !data || len == 0)
    return ICM42_ERR_PARAM;

  uint8_t tx_buf[64];
  if (len + 1 > sizeof(tx_buf))
    return ICM42_ERR_PARAM;

  tx_buf[0] = reg | ICM42_SPI_WRITE;
  memcpy(&tx_buf[1], data, len);

  dev->bus.set_cs(true);
  dev->bus.delay_us(1);

  bool result = dev->bus.spi_transfer(tx_buf, NULL, len + 1);

  dev->bus.set_cs(false);
  dev->bus.delay_us(1);  /* Inter-transaction delay */

  return result ? ICM42_OK : ICM42_ERR;
}

static icm42_err_t icm42_select_bank(icm42_dev_t *dev, uint8_t bank)
{
  if (!dev || bank > 4)
    return ICM42_ERR_PARAM;

  uint8_t data = bank & 0x07;
  return icm42_write_reg(dev, ICM42_REG_REG_BANK_SEL, &data, 1);
}

icm42_err_t icm42_soft_reset(icm42_dev_t *dev)
{
  if (!dev)
    return ICM42_ERR_PARAM;

  icm42_err_t err;
  uint8_t data;

  /* Ensure we're in Bank 0 */
  err = icm42_select_bank(dev, 0);
  if (err != ICM42_OK)
    return err;

  /* Trigger soft reset: Set bit 0 of DEVICE_CONFIG */
  data = 0x01;
  err = icm42_write_reg(dev, ICM42_REG_DEVICE_CONFIG, &data, 1);
  if (err != ICM42_OK)
    return err;

  /* Wait for reset to complete (datasheet: max 1ms, add margin) */
  dev->bus.delay_ms(50);

  return ICM42_OK;
}

icm42_err_t icm42_init(icm42_dev_t *dev, const icm42_config_t *config)
{
  if (!dev || !config)
    return ICM42_ERR_PARAM;

  icm42_err_t err;
  uint8_t data;

  /* Initialize calibration to zero */
  memset(&dev->calib, 0, sizeof(icm42_calibration_t));
  dev->data_ready = false;

  /* Soft reset */
  err = icm42_soft_reset(dev);
  if (err != ICM42_OK)
    return err;

  /* Wait longer after reset for chip to be ready */
  dev->bus.delay_ms(50);

  /* Verify WHO_AM_I */
  err = icm42_select_bank(dev, 0);
  if (err != ICM42_OK)
    return err;

  uint8_t who_am_i = 0;
  err = icm42_read_reg(dev, ICM42_REG_WHO_AM_I, &data, 1);
  if (err != ICM42_OK)
    return err;
  
  who_am_i = data;

  if (data != ICM42_WHO_AM_I_VALUE)
  {
    /* Debug: Store actual value for logging */
    dev->bus.delay_ms(10);
    return ICM42_ERR_WHO_AM_I;
  }

  /* Configure Power Management
   * PWR_MGMT0: Set gyro and accel to Low Noise mode
   */
  if (config->use_low_noise_mode)
  {
    data = ICM42_PWR_MGMT0_GYRO_MODE_LN | ICM42_PWR_MGMT0_ACCEL_MODE_LN;
  }
  else
  {
    data = ICM42_PWR_MGMT0_GYRO_MODE_LN | ICM42_PWR_MGMT0_ACCEL_MODE_LP;
  }
  err = icm42_write_reg(dev, ICM42_REG_PWR_MGMT0, &data, 1);
  if (err != ICM42_OK)
    return err;

  /* Wait for sensors to start up (200us typ, max 500us per datasheet) */
  dev->bus.delay_ms(1);

  /* Configure Gyro: FS and ODR
   * GYRO_CONFIG0 [7:5]=FS_SEL, [3:0]=ODR
   */
  data = ((config->gyro_fs & 0x07) << 5) | (config->gyro_odr & 0x0F);
  err = icm42_write_reg(dev, ICM42_REG_GYRO_CONFIG0, &data, 1);
  if (err != ICM42_OK)
    return err;

  /* Configure Accel: FS and ODR
   * ACCEL_CONFIG0 [7:5]=FS_SEL, [3:0]=ODR
   */
  data = ((config->accel_fs & 0x07) << 5) | (config->accel_odr & 0x0F);
  err = icm42_write_reg(dev, ICM42_REG_ACCEL_CONFIG0, &data, 1);
  if (err != ICM42_OK)
    return err;

  /* Configure AAF and UI filter
   * GYRO_ACCEL_CONFIG0: [5:4]=ACCEL_UI_FILT_BW, [3:2]=GYRO_UI_FILT_BW, [1]=ACCEL_AAF, [0]=GYRO_AAF
   */
  data = ((config->ui_filt_ord & 0x03) << 4) |  /* Accel UI filter order */
         ((config->ui_filt_ord & 0x03) << 2) |  /* Gyro UI filter order */
         ((config->accel_aaf & 0x01) << 1) |    /* Accel AAF enable */
         (config->gyro_aaf & 0x01);              /* Gyro AAF enable */
  err = icm42_write_reg(dev, ICM42_REG_GYRO_ACCEL_CONFIG0, &data, 1);
  if (err != ICM42_OK)
    return err;

  /* Configure Gyro AAF DELT (if AAF enabled) - Bank 1 */
  if (config->gyro_aaf != ICM42_AAF_DISABLE)
  {
    err = icm42_select_bank(dev, 1);
    if (err != ICM42_OK)
      return err;

    /* Set AAF DELT based on selected bandwidth (see datasheet Table 5-5) */
    uint8_t delt;
    switch (config->gyro_aaf)
    {
      case ICM42_AAF_258HZ:  delt = 63; break;
      case ICM42_AAF_536HZ:  delt = 6;  break;
      case ICM42_AAF_997HZ:  delt = 1;  break;
      case ICM42_AAF_1962HZ: delt = 0;  break;
      default: delt = 63; break;
    }
    err = icm42_write_reg(dev, ICM42_REG_GYRO_CONFIG_STATIC3, &delt, 1);
    if (err != ICM42_OK)
      return err;

    /* Return to Bank 0 */
    err = icm42_select_bank(dev, 0);
    if (err != ICM42_OK)
      return err;
  }

  /* Configure Accel AAF DELT (if AAF enabled) - Bank 2 */
  if (config->accel_aaf != ICM42_AAF_DISABLE)
  {
    err = icm42_select_bank(dev, 2);
    if (err != ICM42_OK)
      return err;

    uint8_t delt;
    switch (config->accel_aaf)
    {
      case ICM42_AAF_258HZ:  delt = 63; break;
      case ICM42_AAF_536HZ:  delt = 6;  break;
      case ICM42_AAF_997HZ:  delt = 1;  break;
      case ICM42_AAF_1962HZ: delt = 0;  break;
      default: delt = 63; break;
    }
    err = icm42_write_reg(dev, ICM42_REG_ACCEL_CONFIG_STATIC3, &delt, 1);
    if (err != ICM42_OK)
      return err;

    /* Return to Bank 0 */
    err = icm42_select_bank(dev, 0);
    if (err != ICM42_OK)
      return err;
  }

  /* Wait for filter to settle */
  dev->bus.delay_ms(50);

  /* Save config */
  dev->config = *config;

  return ICM42_OK;
}

icm42_err_t icm42_get_all_data(icm42_dev_t *dev, icm42_sensor_data_t *data)
{
  if (!dev || !data)
    return ICM42_ERR_PARAM;

  icm42_err_t err;
  uint8_t buf[14];

  /* Burst read from TEMP_DATA1 (0x1D) to GYRO_DATA_Z0 (0x2A) = 14 bytes */
  err = icm42_read_reg(dev, ICM42_REG_TEMP_DATA1, buf, 14);
  if (err != ICM42_OK)
    return err;

  /* Parse data (big-endian) */
  data->temp = (int16_t)((buf[0] << 8) | buf[1]);
  data->accel.x = (int16_t)((buf[2] << 8) | buf[3]);
  data->accel.y = (int16_t)((buf[4] << 8) | buf[5]);
  data->accel.z = (int16_t)((buf[6] << 8) | buf[7]);
  data->gyro.x = (int16_t)((buf[8] << 8) | buf[9]);
  data->gyro.y = (int16_t)((buf[10] << 8) | buf[11]);
  data->gyro.z = (int16_t)((buf[12] << 8) | buf[13]);

  /* Apply calibration */
  icm42_apply_calibration(dev, &data->gyro, &data->accel);

  return ICM42_OK;
}

icm42_err_t icm42_get_gyro(icm42_dev_t *dev, icm42_axis_raw_t *gyro)
{
  if (!dev || !gyro)
    return ICM42_ERR_PARAM;

  icm42_err_t err;
  uint8_t buf[6];

  err = icm42_read_reg(dev, ICM42_REG_GYRO_DATA_X1, buf, 6);
  if (err != ICM42_OK)
    return err;

  gyro->x = (int16_t)((buf[0] << 8) | buf[1]);
  gyro->y = (int16_t)((buf[2] << 8) | buf[3]);
  gyro->z = (int16_t)((buf[4] << 8) | buf[5]);

  /* Apply calibration */
  if (dev->calib.is_calibrated)
  {
    gyro->x -= dev->calib.gyro_offset.x;
    gyro->y -= dev->calib.gyro_offset.y;
    gyro->z -= dev->calib.gyro_offset.z;
  }

  return ICM42_OK;
}

icm42_err_t icm42_get_accel(icm42_dev_t *dev, icm42_axis_raw_t *accel)
{
  if (!dev || !accel)
    return ICM42_ERR_PARAM;

  icm42_err_t err;
  uint8_t buf[6];

  err = icm42_read_reg(dev, ICM42_REG_ACCEL_DATA_X1, buf, 6);
  if (err != ICM42_OK)
    return err;

  accel->x = (int16_t)((buf[0] << 8) | buf[1]);
  accel->y = (int16_t)((buf[2] << 8) | buf[3]);
  accel->z = (int16_t)((buf[4] << 8) | buf[5]);

  /* Apply calibration */
  if (dev->calib.is_calibrated)
  {
    accel->x -= dev->calib.accel_offset.x;
    accel->y -= dev->calib.accel_offset.y;
    accel->z -= dev->calib.accel_offset.z;
  }

  return ICM42_OK;
}

icm42_err_t icm42_get_temp(icm42_dev_t *dev, int16_t *temp)
{
  if (!dev || !temp)
    return ICM42_ERR_PARAM;

  icm42_err_t err;
  uint8_t buf[2];

  err = icm42_read_reg(dev, ICM42_REG_TEMP_DATA1, buf, 2);
  if (err != ICM42_OK)
    return err;

  *temp = (int16_t)((buf[0] << 8) | buf[1]);
  return ICM42_OK;
}

void icm42_convert_gyro_to_dps(const icm42_dev_t *dev, const icm42_axis_raw_t *raw, icm42_axis_float_t *dps)
{
  if (!dev || !raw || !dps)
    return;

  float sensitivity = icm42_get_gyro_sensitivity(dev->config.gyro_fs);

  dps->x = (float)raw->x / sensitivity;
  dps->y = (float)raw->y / sensitivity;
  dps->z = (float)raw->z / sensitivity;
}

void icm42_convert_accel_to_g(const icm42_dev_t *dev, const icm42_axis_raw_t *raw, icm42_axis_float_t *g)
{
  if (!dev || !raw || !g)
    return;

  float sensitivity = icm42_get_accel_sensitivity(dev->config.accel_fs);

  g->x = (float)raw->x / sensitivity;
  g->y = (float)raw->y / sensitivity;
  g->z = (float)raw->z / sensitivity;
}

float icm42_convert_temp_to_celsius(int16_t raw_temp)
{
  /* Temperature formula from datasheet:
   * Temp(°C) = (TEMP_DATA / 132.48) + 25
   */
  return ((float)raw_temp / ICM42_TEMP_SENSITIVITY) + ICM42_TEMP_OFFSET;
}

icm42_err_t icm42_calibrate_gyro_offset(icm42_dev_t *dev, uint16_t num_samples)
{
  if (!dev || num_samples == 0)
    return ICM42_ERR_PARAM;

  icm42_axis_raw_t gyro;
  int32_t sum_x = 0, sum_y = 0, sum_z = 0;

  /* Temporarily disable calibration for raw readings */
  bool was_calibrated = dev->calib.is_calibrated;
  dev->calib.is_calibrated = false;

  /* Collect samples */
  for (uint16_t i = 0; i < num_samples; i++)
  {
    if (icm42_get_gyro(dev, &gyro) != ICM42_OK)
    {
      dev->calib.is_calibrated = was_calibrated;
      return ICM42_ERR;
    }

    sum_x += gyro.x;
    sum_y += gyro.y;
    sum_z += gyro.z;

    dev->bus.delay_ms(2);  /* Small delay between samples */
  }

  /* Calculate average offset */
  dev->calib.gyro_offset.x = (int16_t)(sum_x / num_samples);
  dev->calib.gyro_offset.y = (int16_t)(sum_y / num_samples);
  dev->calib.gyro_offset.z = (int16_t)(sum_z / num_samples);
  dev->calib.is_calibrated = true;

  return ICM42_OK;
}

icm42_err_t icm42_calibrate_accel_offset(icm42_dev_t *dev, uint16_t num_samples)
{
  if (!dev || num_samples == 0)
    return ICM42_ERR_PARAM;

  icm42_axis_raw_t accel;
  int32_t sum_x = 0, sum_y = 0, sum_z = 0;

  /* Temporarily disable calibration */
  bool was_calibrated = dev->calib.is_calibrated;
  dev->calib.is_calibrated = false;

  /* Collect samples */
  for (uint16_t i = 0; i < num_samples; i++)
  {
    if (icm42_get_accel(dev, &accel) != ICM42_OK)
    {
      dev->calib.is_calibrated = was_calibrated;
      return ICM42_ERR;
    }

    sum_x += accel.x;
    sum_y += accel.y;
    sum_z += accel.z;

    dev->bus.delay_ms(2);
  }

  /* Calculate average */
  int16_t avg_x = (int16_t)(sum_x / num_samples);
  int16_t avg_y = (int16_t)(sum_y / num_samples);
  int16_t avg_z = (int16_t)(sum_z / num_samples);

  /* Offset: X and Y should be 0, Z should be 1g */
  float sensitivity = icm42_get_accel_sensitivity(dev->config.accel_fs);
  int16_t one_g = (int16_t)sensitivity;

  dev->calib.accel_offset.x = avg_x;
  dev->calib.accel_offset.y = avg_y;
  dev->calib.accel_offset.z = avg_z - one_g;  /* Z-axis compensate for gravity */
  dev->calib.is_calibrated = true;

  return ICM42_OK;
}

icm42_err_t icm42_set_calibration(icm42_dev_t *dev, const icm42_calibration_t *calib)
{
  if (!dev || !calib)
    return ICM42_ERR_PARAM;

  dev->calib = *calib;
  return ICM42_OK;
}

icm42_err_t icm42_get_calibration(const icm42_dev_t *dev, icm42_calibration_t *calib)
{
  if (!dev || !calib)
    return ICM42_ERR_PARAM;

  *calib = dev->calib;
  return ICM42_OK;
}

icm42_err_t icm42_setup_interrupt(icm42_dev_t *dev)
{
  if (!dev)
    return ICM42_ERR_PARAM;

  icm42_err_t err;
  uint8_t data;

  /* Ensure Bank 0 */
  err = icm42_select_bank(dev, 0);
  if (err != ICM42_OK)
    return err;

  /* INT_CONFIG: Configure interrupt pin
   * bit[2]=INT1_MODE (0=pulsed, 1=latched)
   * bit[1]=INT1_DRIVE_CIRCUIT (0=open drain, 1=push-pull)
   * bit[0]=INT1_POLARITY (0=active low, 1=active high)
   */
  data = 0x02;  /* Push-pull, active high, pulsed */
  err = icm42_write_reg(dev, ICM42_REG_INT_CONFIG, &data, 1);
  if (err != ICM42_OK)
    return err;

  /* INT_SOURCE0: Enable UI data ready interrupt on INT1
   * bit[3]=UI_DRDY_INT1_EN
   */
  data = 0x08;
  err = icm42_write_reg(dev, ICM42_REG_INT_SOURCE0, &data, 1);
  if (err != ICM42_OK)
    return err;

  return ICM42_OK;
}

icm42_err_t icm42_clear_interrupt(icm42_dev_t *dev)
{
  if (!dev)
    return ICM42_ERR_PARAM;

  uint8_t data;
  /* Read INT_STATUS to clear interrupt */
  return icm42_read_reg(dev, ICM42_REG_INT_STATUS, &data, 1);
}

void icm42_irq_handler(icm42_dev_t *dev)
{
  if (!dev)
    return;

  dev->data_ready = true;
}

icm42_err_t icm42_self_test(icm42_dev_t *dev)
{
  if (!dev)
    return ICM42_ERR_PARAM;

  /* Self-test implementation (simplified)
   * Full self-test requires complex procedures - see datasheet section 6.1
   * This is a placeholder for basic WHO_AM_I verification
   */
  icm42_err_t err;
  uint8_t data;

  err = icm42_select_bank(dev, 0);
  if (err != ICM42_OK)
    return err;

  err = icm42_read_reg(dev, ICM42_REG_WHO_AM_I, &data, 1);
  if (err != ICM42_OK)
    return err;

  return (data == ICM42_WHO_AM_I_VALUE) ? ICM42_OK : ICM42_ERR;
}

/* Private function implementation ------------------------------------------ */

static void icm42_apply_calibration(const icm42_dev_t *dev,
                                     icm42_axis_raw_t *gyro,
                                     icm42_axis_raw_t *accel)
{
  if (!dev || !gyro || !accel)
    return;

  if (dev->calib.is_calibrated)
  {
    gyro->x -= dev->calib.gyro_offset.x;
    gyro->y -= dev->calib.gyro_offset.y;
    gyro->z -= dev->calib.gyro_offset.z;

    accel->x -= dev->calib.accel_offset.x;
    accel->y -= dev->calib.accel_offset.y;
    accel->z -= dev->calib.accel_offset.z;
  }
}

static float icm42_get_gyro_sensitivity(icm42_gyro_fs_t fs)
{
  switch (fs)
  {
    case ICM42_GYRO_FS_2000DPS:   return ICM42_GYRO_SENSITIVITY_2000DPS;
    case ICM42_GYRO_FS_1000DPS:   return ICM42_GYRO_SENSITIVITY_1000DPS;
    case ICM42_GYRO_FS_500DPS:    return ICM42_GYRO_SENSITIVITY_500DPS;
    case ICM42_GYRO_FS_250DPS:    return ICM42_GYRO_SENSITIVITY_250DPS;
    case ICM42_GYRO_FS_125DPS:    return ICM42_GYRO_SENSITIVITY_125DPS;
    case ICM42_GYRO_FS_62_5DPS:   return ICM42_GYRO_SENSITIVITY_62_5DPS;
    case ICM42_GYRO_FS_31_25DPS:  return ICM42_GYRO_SENSITIVITY_31_25DPS;
    case ICM42_GYRO_FS_15_625DPS: return ICM42_GYRO_SENSITIVITY_15_625DPS;
    default: return ICM42_GYRO_SENSITIVITY_2000DPS;
  }
}

static float icm42_get_accel_sensitivity(icm42_accel_fs_t fs)
{
  switch (fs)
  {
    case ICM42_ACCEL_FS_16G: return ICM42_ACCEL_SENSITIVITY_16G;
    case ICM42_ACCEL_FS_8G:  return ICM42_ACCEL_SENSITIVITY_8G;
    case ICM42_ACCEL_FS_4G:  return ICM42_ACCEL_SENSITIVITY_4G;
    case ICM42_ACCEL_FS_2G:  return ICM42_ACCEL_SENSITIVITY_2G;
    default: return ICM42_ACCEL_SENSITIVITY_16G;
  }
}

/* End of file -------------------------------------------------------- */
