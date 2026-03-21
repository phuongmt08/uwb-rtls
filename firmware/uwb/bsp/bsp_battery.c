/**
 * @file       bsp_battery.c
 * @version    1.2.0
 * @date       2026-03-21
 * @brief      Battery BSP for MAX17048 fuel gauge — STM32F411CEUx
 * @note       I2C3 — SCL: PA8 | SDA: PC9
 */
/*
 * This is the only file you need to include.
 * Open bsp_battery.h to see all available functions and data fields.
 *
 * Quick reference — where to find things:
 *   bsp_battery_init()         → startup, call once
 *   bsp_battery_read_all()     → get voltage + SOC + charge rate in one call
 *   bsp_battery_read_status()  → get FULL / HALF / LOW / CRITICAL
 *   bsp_battery_read_voltage() → get voltage only (mV)
 *   bsp_battery_read_soc()     → get SOC only (%)
 *   bsp_battery_read_crate()   → get charge/discharge rate only
 *   bsp_battery_is_present()   → check if IC is on the bus
 *
 * Data fields after bsp_battery_read_all(&bat):
 *   bat.voltage_mv   — voltage in mV        (e.g. 3742)
 *   bat.soc_pct      — SOC 0-100 %          (e.g. 73)
 *   bat.soc_frac     — fractional SOC       (e.g. 115 → 73.45%)
 *   bat.crate_mphph  — charge rate milli-%/hr (negative = discharging)
 */
/* Includes ----------------------------------------------------------------- */
#include "bsp_battery.h"
#include "max17048.h"

/* Private defines ---------------------------------------------------------- */
#define SOC_THRESHOLD_FULL      50u   /* above this → FULL     */
#define SOC_THRESHOLD_LOW       20u   /* below this → LOW      */
#define SOC_THRESHOLD_CRITICAL  10u   /* below this → CRITICAL */

/* Private variables -------------------------------------------------------- */
extern I2C_HandleTypeDef BSP_BATTERY_I2C_HANDLE;

static max17048_dev_t s_dev;

/* Tracks the last SOC at which a low-battery warning was issued.
 * Warning fires every BSP_BATTERY_WARN_STEP_PCT (2%) drop below 20%.
 * Reset to SOC_THRESHOLD_LOW on init so the first warning fires at 20%. */
static uint8_t s_last_warn_soc = SOC_THRESHOLD_LOW;

static const max17048_config_t s_lipo_cfg =
{
  .rcomp               = 0x97,
  .empty_alert         = (max17048_empty_alert_t)(32 - BSP_BATTERY_EMPTY_ALERT_PCT),
  .valrt_min_mv        = BSP_BATTERY_VALRT_MIN_MV,
  .valrt_max_mv        = BSP_BATTERY_VALRT_MAX_MV,
  .vreset_mv           = BSP_BATTERY_VRESET_MV,
  .en_soc_change_alert = false,
  .en_vreset_alert     = true,
  .dis_hibernate_comp  = false,
};

/* Private function prototypes ---------------------------------------------- */
static int32_t              s_i2c_write              (uint8_t dev_addr, uint8_t reg_addr,
                                                       const uint8_t *data, uint16_t len);
static int32_t              s_i2c_read               (uint8_t dev_addr, uint8_t reg_addr,
                                                       uint8_t *data, uint16_t len);
static bsp_battery_err_t    s_map_err                (max17048_err_t err);
static bsp_battery_status_t s_get_status             (uint8_t soc_pct);
static void                 s_handle_alerts          (void);
static void                 s_check_low_battery_warn (uint8_t soc_pct);

/* Public function implementation ------------------------------------------- */

bsp_battery_err_t bsp_battery_init(void)
{
  s_dev.bus.i2c_write = s_i2c_write;
  s_dev.bus.i2c_read  = s_i2c_read;

  max17048_err_t err = max17048_init(&s_dev, &s_lipo_cfg);
  if (err != MAX17048_OK)
    return s_map_err(err);

  /* Fixed temperature compensation — nearby ICs keep the board warm at ~40°C.
   * Update this value if a temperature sensor is added later. */
  max17048_update_temp_comp(&s_dev, BSP_BATTERY_TEMP_DEGC);

  s_last_warn_soc = SOC_THRESHOLD_LOW;

  return BSP_BATTERY_OK;
}

bsp_battery_err_t bsp_battery_read_all(bsp_battery_data_t *data)
{
  if (!data)
    return BSP_BATTERY_ERR_PARAM;

  max17048_data_t raw;

  max17048_err_t err = max17048_read_all(&s_dev, &raw);
  if (err != MAX17048_OK)
    return s_map_err(err);

  data->voltage_mv  = raw.voltage_mv;
  data->soc_pct     = raw.soc_pct;
  data->soc_frac    = raw.soc_frac;
  data->crate_mphph = raw.crate_mphph;

  /* IC alert flags are handled here — upper layers don't need to know */
  if (raw.alert_active)
    s_handle_alerts();

  /* Low battery warning — fires every 2% drop below 20% */
  s_check_low_battery_warn(raw.soc_pct);

  return BSP_BATTERY_OK;
}

bsp_battery_err_t bsp_battery_read_status(bsp_battery_status_t *status)
{
  if (!status)
    return BSP_BATTERY_ERR_PARAM;

  uint8_t soc = 0;

  max17048_err_t err = max17048_read_soc(&s_dev, &soc);
  if (err != MAX17048_OK)
    return s_map_err(err);

  *status = s_get_status(soc);

  return BSP_BATTERY_OK;
}

bsp_battery_err_t bsp_battery_read_voltage(uint16_t *voltage_mv)
{
  if (!voltage_mv)
    return BSP_BATTERY_ERR_PARAM;

  return s_map_err(max17048_read_voltage(&s_dev, voltage_mv));
}

bsp_battery_err_t bsp_battery_read_soc(uint8_t *soc_pct, uint8_t *soc_frac)
{
  if (!soc_pct)
    return BSP_BATTERY_ERR_PARAM;

  /* soc_frac is optional — pass NULL if fractional part is not needed */
  if (soc_frac == NULL)
    return s_map_err(max17048_read_soc(&s_dev, soc_pct));

  return s_map_err(max17048_read_soc_full(&s_dev, soc_pct, soc_frac));
}

bsp_battery_err_t bsp_battery_read_crate(int16_t *crate_mphph)
{
  if (!crate_mphph)
    return BSP_BATTERY_ERR_PARAM;

  return s_map_err(max17048_read_crate(&s_dev, crate_mphph));
}

bool bsp_battery_is_present(void)
{
  return max17048_is_present(&s_dev);
}

/* Private function implementation ------------------------------------------ */

static bsp_battery_status_t s_get_status(uint8_t soc_pct)
{
  if (soc_pct < SOC_THRESHOLD_CRITICAL) return BSP_BATTERY_STATUS_CRITICAL;
  if (soc_pct < SOC_THRESHOLD_LOW)      return BSP_BATTERY_STATUS_LOW;
  if (soc_pct < SOC_THRESHOLD_FULL)     return BSP_BATTERY_STATUS_HALF;

  return BSP_BATTERY_STATUS_FULL;
}

static void s_check_low_battery_warn(uint8_t soc_pct)
{
  /* Only active below 20%. Fires once per BSP_BATTERY_WARN_STEP_PCT (2%) drop.
   *
   * Timeline example:
   *   20% → warn, tracker = 20
   *   19% → skip  (19 > 20 - 2 = 18)
   *   18% → warn, tracker = 18
   *   17% → skip
   *   16% → warn, tracker = 16  ... and so on
   *
   * TODO: add your warning action inside the if block below.
   *   Options:
   *     set a flag      → g_battery_low = true;
   *     call a callback → on_low_battery(soc_pct);
   *     BLE notify      → ble_battery_warn_notify(soc_pct);  (implement later)
   *     log             → LOG_WARN("Battery low: %d%%", soc_pct);
   */
  if (soc_pct >= SOC_THRESHOLD_LOW)
  {
    s_last_warn_soc = SOC_THRESHOLD_LOW; /* reset so warning fires again next time */
    return;
  }

  if (soc_pct <= (s_last_warn_soc - BSP_BATTERY_WARN_STEP_PCT))
  {
    s_last_warn_soc = soc_pct;

    /* --- add warning action here --- */
  }
}

static void s_handle_alerts(void)
{
  uint16_t status = 0;
  if (max17048_read_status(&s_dev, &status, 0) != MAX17048_OK)
    return;

  if (status & MAX17048_STATUS_VR)
  {
    /* Battery was swapped — IC already re-estimated SOC automatically.
     * Reset warning tracker so it works correctly with the new battery. */
    s_last_warn_soc = SOC_THRESHOLD_LOW;
  }

  if (status & MAX17048_STATUS_HD)
  {
    /* SOC dropped below 10% (empty alert threshold) — critically low.
     * TODO: add action here, e.g. ble_battery_critical_notify(); */
  }

  if (status & MAX17048_STATUS_VL)
  {
    /* Voltage dropped below VALRT.MIN (3000mV) — hardware level warning.
     * TODO: same as HD above. */
  }

  if (status & MAX17048_STATUS_VH)
  {
    /* Voltage exceeded VALRT.MAX (4200mV) — overcharge.
     * Charger circuit should handle this; log here if needed. */
  }

  /* Always clear the alert flag after handling — must not skip this */
  max17048_clear_alert(&s_dev);
}

static int32_t s_i2c_write(uint8_t dev_addr, uint8_t reg_addr,
                            const uint8_t *data, uint16_t len)
{
  /* Driver passes 7-bit address (0x36), HAL expects 8-bit — shift left 1 */
  if (HAL_I2C_Mem_Write(&BSP_BATTERY_I2C_HANDLE,
                         (uint16_t)(dev_addr << 1),
                         reg_addr,
                         I2C_MEMADD_SIZE_8BIT,
                         (uint8_t *)data,
                         len,
                         BSP_BATTERY_I2C_TIMEOUT_MS) == HAL_OK)
    return 0;

  return -1;
}

static int32_t s_i2c_read(uint8_t dev_addr, uint8_t reg_addr,
                           uint8_t *data, uint16_t len)
{
  if (HAL_I2C_Mem_Read(&BSP_BATTERY_I2C_HANDLE,
                        (uint16_t)(dev_addr << 1),
                        reg_addr,
                        I2C_MEMADD_SIZE_8BIT,
                        data,
                        len,
                        BSP_BATTERY_I2C_TIMEOUT_MS) == HAL_OK)
    return 0;

  return -1;
}

static bsp_battery_err_t s_map_err(max17048_err_t err)
{
  switch (err)
  {
    case MAX17048_OK:         return BSP_BATTERY_OK;
    case MAX17048_ERR_BUS:    return BSP_BATTERY_ERR_BUS;
    case MAX17048_ERR_PARAM:  return BSP_BATTERY_ERR_PARAM;
    case MAX17048_ERR_NO_DEV: return BSP_BATTERY_ERR_NO_DEV;
    default:                  return BSP_BATTERY_ERR;
  }
}

/* End of file -------------------------------------------------------------- */