/**
 * @file       max17048.c
 * @copyright
 * @license
 * @version    1.1.0
 * @date       2026-03-17
 * @author
 * @brief      MAX17048 Li+ ModelGauge fuel gauge driver implementation
 * @note       Based on MAX17048/MAX17049 datasheet Rev 1; 4/12
 * @example    None
 */

/* Public includes ---------------------------------------------------------- */
#include "max17048.h"
#include <stddef.h>
/* Private defines ---------------------------------------------------------- */

/*
 * VCELL register — datasheet p.10
 * 16-bit register, 1 LSb = 78.125 uV/cell (applies to full 16-bit word)
 * voltage_mv = (uint64_t)raw * 78125 / 1,000,000
 * Must use uint64_t: 65535 * 78125 = 5,120,703,125 exceeds uint32_t max
 */
#define VCELL_LSB_UV      78125 /* 1 Lsb = 78.125 uV in microvolts */

/*
 * CRATE register — datasheet p.13
 * Signed 16-bit, 1 LSb = 0.208 %/hr
 * Avoid float: work in %/hr, multiply by 208 then divide by 1000
 * Use int32_t before multiply: max 32767 * 208 = 6,815,536
 */
#define CRATE_LSB_X1000   208

/* VALRT register — datasheet p.13, 1 LSb = 20 mV */
#define VALRT_LSB_MV      20
#define VALRT_RAW_MAX     255   /* 0xFF * 20mV = 5100mV */

/* VRESET register — datasheet p.13, 1 LSb = 40 mV, range 2280-3480 mV */
#define VRESET_LSB_MV     40
#define VRESET_RAW_MIN    57    /* 2280 / 40 = 57  */
#define VRESET_RAW_MAX    87    /* 3480 / 40 = 87  */

/*
 * Temperature compensation — datasheet p.8
 * Default ROM model constants (generic Li+ cells)
 * Contact Maxim for custom RCOMP0/TempCoHot/TempCoCold for specific battery
 */
#define RCOMP0            0x97
#define TEMPCO_HOT        (-0.5f)   /* RCOMP change per degC above 20 */
#define TEMPCO_COLD       (-5.0f)   /* RCOMP change per degC below 20 */

/* Private function prototypes ---------------------------------------------- */
static max17048_err_t s_write_reg   (max17048_dev_t *dev, uint8_t reg_addr, uint16_t value);
static max17048_err_t s_read_reg    (max17048_dev_t *dev, uint8_t reg_addr, uint16_t *value);
static max17048_err_t s_apply_config(max17048_dev_t *dev);

/* Private function implementation ------------------------------------------ */

static max17048_err_t s_write_reg(max17048_dev_t *dev, uint8_t reg_addr, uint16_t value)
{
  uint8_t buf[2];

  /* MAX17048 expects MSB first — datasheet p.17 */
  buf[0] = (value >> 8) & 0xFF;
  buf[1] = (value)      & 0xFF;

  if (dev->bus.i2c_write(MAX17048_I2C_ADDR, reg_addr, buf, 2) != 0)
    return MAX17048_ERR_BUS;

  return MAX17048_OK;
}

static max17048_err_t s_read_reg(max17048_dev_t *dev, uint8_t reg_addr, uint16_t *value)
{
  uint8_t buf[2];

  if (dev->bus.i2c_read(MAX17048_I2C_ADDR, reg_addr, buf, 2) != 0)
    return MAX17048_ERR_BUS;

  /* Reassemble MSB first */
  *value = ((uint16_t)buf[0] << 8) | buf[1];

  return MAX17048_OK;
}

static max17048_err_t s_apply_config(max17048_dev_t *dev)
{
  max17048_err_t err;

  /*
   * CONFIG register — datasheet p.12
   * bits[15:8] = RCOMP
   * bit7       = SLEEP  (0 = normal)
   * bit6       = ALSC   (1 = enable SOC 1% change alert)
   * bit5       = ALRT   (0 = no pending alert)
   * bits[4:0]  = ATHD   (empty alert threshold)
   */
  uint16_t config_reg = 0;

  config_reg |= (uint16_t)dev->config.rcomp << 8;
  config_reg |= (uint16_t)(dev->config.empty_alert & MAX17048_CONFIG_ATHD_MASK);

  if (dev->config.en_soc_change_alert)
    config_reg |= MAX17048_CONFIG_ALSC;

  err = s_write_reg(dev, MAX17048_REG_CONFIG, config_reg);
  if (err != MAX17048_OK)
    return err;

  /*
   * VALRT register — datasheet p.13
   * bits[15:8] = VALRT.MIN, bits[7:0] = VALRT.MAX, 1 LSb = 20 mV
   * Clamp raw values to valid range 0x00-0xFF
   */
  uint16_t min_raw = dev->config.valrt_min_mv / VALRT_LSB_MV;
  uint16_t max_raw = dev->config.valrt_max_mv / VALRT_LSB_MV;

  if (min_raw > VALRT_RAW_MAX) min_raw = VALRT_RAW_MAX;
  if (max_raw > VALRT_RAW_MAX) max_raw = VALRT_RAW_MAX;

  err = s_write_reg(dev, MAX17048_REG_VALRT,
                    ((uint16_t)min_raw << 8) | (uint16_t)max_raw);
  if (err != MAX17048_OK)
    return err;

  /*
   * VRESET/ID register — datasheet p.13
   * bits[15:9] = VRESET threshold (writable), 1 LSb = 40 mV
   * bit8       = Dis              (writable)
   * bits[7:1]  = ID               (factory, read only — must preserve)
   * bit0       = reserved
   *
   * Read first to preserve ID, modify only VRESET and Dis bits
   * Clamp VRESET to valid range 2280-3480 mV (raw 57-87)
   */
  uint16_t vreset_reg = 0;
  err = s_read_reg(dev, MAX17048_REG_VRESET_ID, &vreset_reg);
  if (err != MAX17048_OK)
    return err;

  vreset_reg &= ~(MAX17048_VRESET_MASK | MAX17048_VRESET_DIS);

  uint8_t vreset_raw = (uint8_t)(dev->config.vreset_mv / VRESET_LSB_MV);
  if (vreset_raw < VRESET_RAW_MIN) vreset_raw = VRESET_RAW_MIN;
  if (vreset_raw > VRESET_RAW_MAX) vreset_raw = VRESET_RAW_MAX;

  vreset_reg |= (uint16_t)vreset_raw << MAX17048_VRESET_SHIFT;

  if (dev->config.dis_hibernate_comp)
    vreset_reg |= MAX17048_VRESET_DIS;

  err = s_write_reg(dev, MAX17048_REG_VRESET_ID, vreset_reg);
  if (err != MAX17048_OK)
    return err;

  /*
   * STATUS register — datasheet p.14
   * bit14 = EnVR: enable voltage reset alert
   * Read-modify-write to preserve existing alert flags
   */
  uint16_t status_reg = 0;
  err = s_read_reg(dev, MAX17048_REG_STATUS, &status_reg);
  if (err != MAX17048_OK)
    return err;

  if (dev->config.en_vreset_alert)
    status_reg |= MAX17048_STATUS_ENVR;
  else
    status_reg &= ~MAX17048_STATUS_ENVR;

  err = s_write_reg(dev, MAX17048_REG_STATUS, status_reg);
  if (err != MAX17048_OK)
    return err;

  return MAX17048_OK;
}

/* Public function implementation ------------------------------------------- */

void max17048_default_config(max17048_config_t *config)
{
  if (!config)
    return;

  config->rcomp               = RCOMP0;
  config->empty_alert         = MAX17048_EMPTY_ALERT_4PCT;
  config->valrt_max_mv        = 5100;   /* 0xFF * 20mV — alert disabled */
  config->valrt_min_mv        = 0;      /* 0x00 * 20mV — alert disabled */
  config->vreset_mv           = 3000;   /* 3.0V reset threshold         */
  config->en_soc_change_alert = false;
  config->en_vreset_alert     = true;   /* Alert on voltage reset by default, can be disabled if not needed */
  config->dis_hibernate_comp  = false;
}

bool max17048_is_present(max17048_dev_t *dev)
{
  if (!dev)
    return false;

  uint16_t version = 0;

  if (s_read_reg(dev, MAX17048_REG_VERSION, &version) != MAX17048_OK)
    return false;

  /* Datasheet p.11: VERSION register returns 0x0011 for MAX17048, 0x0012 for MAX17049 */
  return (version == 0x0011 || version == 0x0012);
}

max17048_err_t max17048_init(max17048_dev_t *dev, const max17048_config_t *config)
{
  if (!dev)
    return MAX17048_ERR_PARAM;

  if (!dev->bus.i2c_write || !dev->bus.i2c_read)
    return MAX17048_ERR_PARAM;

  dev->ready = false;

  if (!max17048_is_present(dev))
    return MAX17048_ERR_NO_DEV;

  if (config != NULL)
    dev->config = *config;
  else
    max17048_default_config(&dev->config);

  /*
   * Apply config to IC registers FIRST
   * Datasheet p.14: RI bit means IC is unconfigured — load config first,
   * then clear RI to acknowledge
   */
  max17048_err_t err = s_apply_config(dev);
  if (err != MAX17048_OK)
    return err;

  /* Clear RI (Reset Indicator) after config is applied */
  uint16_t status = 0;
  err = s_read_reg(dev, MAX17048_REG_STATUS, &status);
  if (err != MAX17048_OK)
    return err;

  if (status & MAX17048_STATUS_RI)
  {
    status &= ~MAX17048_STATUS_RI;
    err = s_write_reg(dev, MAX17048_REG_STATUS, status);
    if (err != MAX17048_OK)
      return err;
  }

  dev->ready = true;

  return MAX17048_OK;
}

max17048_err_t max17048_read_voltage(max17048_dev_t *dev, uint16_t *voltage_mv)
{
  if (!dev || !voltage_mv)
    return MAX17048_ERR_PARAM;

  uint16_t raw = 0;

  max17048_err_t err = s_read_reg(dev, MAX17048_REG_VCELL, &raw);
  if (err != MAX17048_OK)
    return err;

  /*
   * Datasheet p.10: VCELL, 16-BIT LSb = 78.125 uV/cell
   * voltage_mv = raw * 78125 / 1,000,000
   * uint64_t required: 65535 * 78125 = 5,120,703,125 > uint32_t max
   */
  *voltage_mv = (uint16_t)((uint64_t)raw * VCELL_LSB_UV / 1000000);

  return MAX17048_OK;
}

max17048_err_t max17048_read_soc(max17048_dev_t *dev, uint8_t *soc_pct)
{
  if (!dev || !soc_pct)
    return MAX17048_ERR_PARAM;

  uint16_t raw = 0;

  max17048_err_t err = s_read_reg(dev, MAX17048_REG_SOC, &raw);
  if (err != MAX17048_OK)
    return err;

  /*
   * Datasheet p.10: SOC register
   * bits[15:8] = integer percent, 1 LSb = 1%
   * bits[7:0]  = fractional,      1 LSb = 1/256 %
   */
  *soc_pct = (uint8_t)(raw >> 8);

  return MAX17048_OK;
}

max17048_err_t max17048_read_soc_full(max17048_dev_t *dev, uint8_t *soc_pct, uint8_t *soc_frac)
{
  if (!dev || !soc_pct || !soc_frac)
    return MAX17048_ERR_PARAM;

  uint16_t raw = 0;

  max17048_err_t err = s_read_reg(dev, MAX17048_REG_SOC, &raw);
  if (err != MAX17048_OK)
    return err;

  *soc_pct  = (uint8_t)(raw >> 8);    /* upper byte = integer %    */
  *soc_frac = (uint8_t)(raw & 0xFF);  /* lower byte = fractional % */

  return MAX17048_OK;
}

max17048_err_t max17048_read_crate(max17048_dev_t *dev, int16_t *crate_phr)
{
  if (!dev || !crate_phr)
    return MAX17048_ERR_PARAM;

  uint16_t raw = 0;

  max17048_err_t err = s_read_reg(dev, MAX17048_REG_CRATE, &raw);
  if (err != MAX17048_OK)
    return err;

  /*
   * Datasheet p.13: CRATE, signed 16-bit, 1 LSb = 0.208 %/hr
   * (int16_t)raw       — reinterpret bits as signed
   * (int32_t)(int16_t) — widen BEFORE multiplying to avoid overflow
   */
  *crate_phr = (int16_t)((int32_t)(int16_t)raw * CRATE_LSB_X1000 / 1000);

  return MAX17048_OK;
}

max17048_err_t max17048_read_status(max17048_dev_t *dev, uint16_t *status, uint16_t clear_flags)
{
  if (!dev || !status)
    return MAX17048_ERR_PARAM;

  max17048_err_t err = s_read_reg(dev, MAX17048_REG_STATUS, status);
  if (err != MAX17048_OK)
    return err;

  if (clear_flags != 0)
  {
    uint16_t new_val = *status & ~clear_flags;
    err = s_write_reg(dev, MAX17048_REG_STATUS, new_val);
    if (err != MAX17048_OK)
      return err;
  }

  return MAX17048_OK;
}

max17048_err_t max17048_read_all(max17048_dev_t *dev, max17048_data_t *data)
{
  if (!dev || !data)
    return MAX17048_ERR_PARAM;

  max17048_err_t err;

  err = max17048_read_voltage(dev, &data->voltage_mv);
  if (err != MAX17048_OK)
    return err;

  err = max17048_read_soc_full(dev, &data->soc_pct, &data->soc_frac);
  if (err != MAX17048_OK)
    return err;

  err = max17048_read_crate(dev, &data->crate_phr);
  if (err != MAX17048_OK)
    return err;

  /*
   * Datasheet p.11: MODE register is marked Write Only but HibStat (bit12)
   * is explicitly described as a readable status bit.
   */
  uint16_t mode = 0;
  err = s_read_reg(dev, MAX17048_REG_MODE, &mode);
  if (err != MAX17048_OK)
    return err;

  data->is_hibernating = (mode & MAX17048_MODE_HIBSTAT) != 0;

  /* Check alert flag from CONFIG register */
  uint16_t config_reg = 0;
  err = s_read_reg(dev, MAX17048_REG_CONFIG, &config_reg);
  if (err != MAX17048_OK)
    return err;

  data->alert_active = (config_reg & MAX17048_CONFIG_ALRT) != 0;

  /* Save raw STATUS for BSP to inspect individual alert bits */
  err = s_read_reg(dev, MAX17048_REG_STATUS, &data->status_reg);
  if (err != MAX17048_OK)
    return err;

  return MAX17048_OK;
}

max17048_err_t max17048_quick_start(max17048_dev_t *dev)
{
  if (!dev)
    return MAX17048_ERR_PARAM;

  return s_write_reg(dev, MAX17048_REG_MODE, MAX17048_MODE_QUICKSTART);
}

max17048_err_t max17048_por(max17048_dev_t *dev)
{
  if (!dev)
    return MAX17048_ERR_PARAM;

  /*
   * IC does NOT send I2C ACK after this command — expected, ignore error
   */
  s_write_reg(dev, MAX17048_REG_CMD, MAX17048_CMD_POR);
  dev->ready = false;

  return MAX17048_OK;
}

max17048_err_t max17048_enter_sleep(max17048_dev_t *dev)
{
  if (!dev)
    return MAX17048_ERR_PARAM;

  max17048_err_t err;

  /* Step 1: MODE.EnSleep = 1 — MODE is write only */
  err = s_write_reg(dev, MAX17048_REG_MODE, MAX17048_MODE_ENSLEEP);
  if (err != MAX17048_OK)
    return err;

  /* Step 2: CONFIG.SLEEP = 1 — read-modify-write to preserve RCOMP and ATHD */
  uint16_t config_reg = 0;
  err = s_read_reg(dev, MAX17048_REG_CONFIG, &config_reg);
  if (err != MAX17048_OK)
    return err;

  config_reg |= MAX17048_CONFIG_SLEEP;

  return s_write_reg(dev, MAX17048_REG_CONFIG, config_reg);
}

max17048_err_t max17048_exit_sleep(max17048_dev_t *dev)
{
  if (!dev)
    return MAX17048_ERR_PARAM;

  uint16_t config_reg = 0;

  max17048_err_t err = s_read_reg(dev, MAX17048_REG_CONFIG, &config_reg);
  if (err != MAX17048_OK)
    return err;

  config_reg &= ~MAX17048_CONFIG_SLEEP;

  return s_write_reg(dev, MAX17048_REG_CONFIG, config_reg);
}

max17048_err_t max17048_force_hibernate(max17048_dev_t *dev)
{
  if (!dev)
    return MAX17048_ERR_PARAM;

  return s_write_reg(dev, MAX17048_REG_HIBRT, MAX17048_HIBRT_ALWAYS);
}

max17048_err_t max17048_disable_hibernate(max17048_dev_t *dev)
{
  if (!dev)
    return MAX17048_ERR_PARAM;

  return s_write_reg(dev, MAX17048_REG_HIBRT, MAX17048_HIBRT_DISABLE);
}

max17048_err_t max17048_update_temp_comp(max17048_dev_t *dev, int8_t temp_degc)
{
  if (!dev)
    return MAX17048_ERR_PARAM;

  /*
   * Datasheet p.8: Temperature Compensation formula
   *   if temp > 20: RCOMP = RCOMP0 + (20 - temp) * TempCoHot
   *   if temp < 20: RCOMP = RCOMP0 + (20 - temp) * TempCoCold
   *   if temp = 20: RCOMP = RCOMP0
   */
  float rcomp_f;

  if (temp_degc > 20)
    rcomp_f = RCOMP0 + (20.0f - (float)temp_degc) * TEMPCO_HOT;
  else if (temp_degc < 20)
    rcomp_f = RCOMP0 + (20.0f - (float)temp_degc) * TEMPCO_COLD;
  else
    rcomp_f = RCOMP0;

  if (rcomp_f > 0xFF) rcomp_f = 0xFF;
  if (rcomp_f < 0x00) rcomp_f = 0x00;

  uint8_t rcomp = (uint8_t)rcomp_f;

  /* RCOMP lives in bits[15:8] of CONFIG — read-modify-write */
  uint16_t config_reg = 0;
  max17048_err_t err = s_read_reg(dev, MAX17048_REG_CONFIG, &config_reg);
  if (err != MAX17048_OK)
    return err;

  config_reg = (config_reg & 0x00FF) | ((uint16_t)rcomp << 8);

  return s_write_reg(dev, MAX17048_REG_CONFIG, config_reg);
}

max17048_err_t max17048_set_voltage_alert(max17048_dev_t *dev, uint16_t min_mv, uint16_t max_mv)
{
  if (!dev)
    return MAX17048_ERR_PARAM;

  uint16_t min_raw = min_mv / VALRT_LSB_MV;
  uint16_t max_raw = max_mv / VALRT_LSB_MV;

  if (min_raw > VALRT_RAW_MAX) min_raw = VALRT_RAW_MAX;
  if (max_raw > VALRT_RAW_MAX) max_raw = VALRT_RAW_MAX;

  return s_write_reg(dev, MAX17048_REG_VALRT,
                     ((uint16_t)min_raw << 8) | (uint16_t)max_raw);
}

max17048_err_t max17048_clear_alert(max17048_dev_t *dev)
{
  if (!dev)
    return MAX17048_ERR_PARAM;

  uint16_t config_reg = 0;
  /* Clear ALRT bit in CONFIG register — read-modify-write to preserve other settings */
  max17048_err_t err = s_read_reg(dev, MAX17048_REG_CONFIG, &config_reg);
  if (err != MAX17048_OK)
    return err;
  /* ALRT is bit5 of CONFIG */
  config_reg &= ~MAX17048_CONFIG_ALRT;
  /* Write back modified CONFIG to clear alert */
  return s_write_reg(dev, MAX17048_REG_CONFIG, config_reg);
}

/* End of file -------------------------------------------------------------- */
