/**
 * @file       bsp_battery.c
 * @version    2.0.0
 * @date       2026-03-27
 * @brief      Battery BSP for MAX17048 fuel gauge
 * @note       I2C3 — SCL: PA8 | SDA: PC9
 */

/* Includes ----------------------------------------------------------------- */
#include "bsp_battery.h"

#include "log_config.h"
#include "max17048.h"
#include "sys_logger.h"

/* Private defines ---------------------------------------------------------- */

/* SOC thresholds */
#define SOC_THRESHOLD_CRITICAL_PCT 10 /**< Below this → CRITICAL, fire once         */
#define SOC_THRESHOLD_LOW_PCT      20 /**< Below this → LOW, warn every step         */
#define SOC_THRESHOLD_FULL_PCT     90 /**< Above this → FULL                         */
#define SOC_WARN_STEP_PCT          2  /**< Fire a new warning every 2% drop in LOW   */

/* I2C */
#define I2C_TIMEOUT_MS             100

/* Voltage alert thresholds (MAX17048 VALRT register) */
#define VALRT_MIN_MV               2650 /**< Alert if voltage drops below this         */
#define VALRT_MAX_MV               4200 /**< Alert if voltage exceeds this             */
#define VRESET_MV                  2500 /**< Battery-swap detection threshold          */

/* Charge-rate anomaly thresholds */
#define CRATE_OVERCHARGE_WARN      50   /**< %/hr — charging too fast            */
#define CRATE_OVERDISCHARGE_WARN   -100 /**< %/hr — discharging too fast         */
#define CRATE_SLOW_CHARGE_WARN     2    /**< %/hr — charging suspiciously slowly */
#define CRATE_IDLE_THRESHOLD       1    /**< |crate| < this → considered Idle    */

/* Hardware configuration */
#define TEMP_COMP_DEGC             40 /**< Fixed temp compensation (~40°C board temp) */
#define EMPTY_ALERT_PCT            10 /**< IC fires HD alert when SOC < this          */

#define CRATE_IDLE_THRESHOLD       208 /* 1 LSB of MAX17048 CRATE register */
/* Private variables -------------------------------------------------------- */
extern I2C_HandleTypeDef BSP_BATTERY_I2C_HANDLE;

static max17048_dev_t  s_dev;
static max17048_data_t s_bat;

/** Last SOC at which a low-battery warning was issued.
 *  Initialised to SOC_THRESHOLD_LOW_PCT so the first warning fires at exactly 20%. */
static uint8_t s_last_warn_soc = SOC_THRESHOLD_LOW_PCT;

/** One-shot flags — prevent the same event from spamming the log. */
static bool s_critical_warned = false; /**< Critical warning already fired this cycle */

static const max17048_config_t s_lipo_cfg = {
  .rcomp               = 0x97,
  .empty_alert         = (max17048_empty_alert_t) (32 - EMPTY_ALERT_PCT),
  .valrt_min_mv        = VALRT_MIN_MV,
  .valrt_max_mv        = VALRT_MAX_MV,
  .vreset_mv           = VRESET_MV,
  .en_soc_change_alert = false,
  .en_vreset_alert     = true,
  .dis_hibernate_comp  = false,
};

/* Private function prototypes ---------------------------------------------- */
static int32_t s_i2c_write(uint8_t dev_addr, uint8_t reg_addr, const uint8_t *data, uint16_t len);
static int32_t s_i2c_read(uint8_t dev_addr, uint8_t reg_addr, uint8_t *data, uint16_t len);
static void    s_handle_alerts(void);
static void    s_check_soc(void);
static void    s_check_crate(void);
static bsp_battery_status_t s_resolve_status(uint8_t soc);

/* Public function implementation ------------------------------------------- */

bsp_battery_err_t bsp_battery_init(void)
{
  s_dev.bus.i2c_write = s_i2c_write;
  s_dev.bus.i2c_read  = s_i2c_read;

  max17048_err_t err = max17048_init(&s_dev, &s_lipo_cfg);
  if (err != MAX17048_OK)
  {
    uint8_t code = (err == MAX17048_ERR_BUS) ? ERR_BATTERY_I2C : ERR_BATTERY_INIT;
    RLOG_E(LOG_OBJECT_CODE_BATTERY, code, "Init failed: %d", err);
    return BSP_BATTERY_ERR;
  }

  /* Board runs warm (~40°C) due to nearby ICs — apply fixed temperature compensation */
  max17048_update_temp_comp(&s_dev, TEMP_COMP_DEGC);

  s_last_warn_soc = SOC_THRESHOLD_LOW_PCT;

  RLOG_I(LOG_OBJECT_CODE_BATTERY, "Init OK — temp comp %d°C, VRESET %d mV", TEMP_COMP_DEGC, VRESET_MV);
  return BSP_BATTERY_OK;
}

bsp_battery_err_t bsp_battery_task(void)
{
  max17048_err_t err = max17048_read_all(&s_dev, &s_bat);
  if (err != MAX17048_OK)
  {
    uint8_t code = (err == MAX17048_ERR_BUS) ? ERR_BATTERY_I2C : ERR_BATTERY_READ;
    RLOG_E(LOG_OBJECT_CODE_BATTERY, code, "Read failed: %d", err);
    return BSP_BATTERY_ERR;
  }

  s_check_soc();
  s_check_crate();

  const char          *charge_str   = (s_bat.crate_phr > CRATE_IDLE_THRESHOLD)    ? "Charging"
                                      : (s_bat.crate_phr < -CRATE_IDLE_THRESHOLD) ? "Discharging"
                                                                                    : "Idle";
  const char          *status_str[] = { "CRITICAL", "LOW", "HALF", "FULL" };
  bsp_battery_status_t st           = s_resolve_status(s_bat.soc_pct);

  RLOG_I(LOG_OBJECT_CODE_BATTERY, "Voltage: %u mV | SOC: %u%% | CRate: %d %%/hr | Status: %s | %s",
         s_bat.voltage_mv, s_bat.soc_pct, s_bat.crate_phr, status_str[st], charge_str);

  if (s_bat.alert_active)
    s_handle_alerts();

  return BSP_BATTERY_OK;
}

bsp_battery_err_t bsp_battery_get_info(bsp_battery_info_t *info)
{
  if (info == NULL)
    return BSP_BATTERY_ERR_PARAM;

  info->voltage_mv    = s_bat.voltage_mv;
  info->soc_pct       = s_bat.soc_pct;
  info->crate_phr     = s_bat.crate_phr;
  info->status        = s_resolve_status(s_bat.soc_pct);
  info->is_charging   = (s_bat.crate_phr > CRATE_IDLE_THRESHOLD);
  info->is_present    = max17048_is_present(&s_dev);
  info->remaining_min = bsp_battery_get_remaining_time();

  return BSP_BATTERY_OK;
}

uint16_t bsp_battery_get_voltage(void)
{
  return s_bat.voltage_mv;
}

uint8_t bsp_battery_get_soc(void)
{
  return s_bat.soc_pct;
}

int16_t bsp_battery_get_crate(void)
{
  return s_bat.crate_phr;
}

bsp_battery_status_t bsp_battery_get_status(void)
{
  return s_resolve_status(s_bat.soc_pct);
}

int32_t bsp_battery_get_remaining_time(void)
{
  int16_t crate = s_bat.crate_phr;

  if (crate > -CRATE_IDLE_THRESHOLD && crate < CRATE_IDLE_THRESHOLD)
    return INT32_MIN; /* Idle — indeterminate */

  if (crate > 0)
    /* Charging: time to full (positive) */
    return ((int32_t) (100 - s_bat.soc_pct) * 60) / (int32_t) crate;
  else
    /* Discharging: time to empty (negative) */
    return -((int32_t) s_bat.soc_pct * 60) / (int32_t) (-crate);
}

bool bsp_battery_is_present(void)
{
  return max17048_is_present(&s_dev);
}

/* Private function implementation ------------------------------------------ */

static bsp_battery_status_t s_resolve_status(uint8_t soc)
{
  if (soc < SOC_THRESHOLD_CRITICAL_PCT)
    return BSP_BATTERY_STATUS_CRITICAL;
  if (soc < SOC_THRESHOLD_LOW_PCT)
    return BSP_BATTERY_STATUS_LOW;
  if (soc < SOC_THRESHOLD_FULL_PCT)
    return BSP_BATTERY_STATUS_HALF;
  return BSP_BATTERY_STATUS_FULL;
}

static void s_check_soc(void)
{
  uint8_t soc = s_bat.soc_pct;

  if (soc > SOC_THRESHOLD_LOW_PCT)
  {
    /* Reset warning state so alerts re-arm if SOC dips low again */
    s_last_warn_soc   = SOC_THRESHOLD_LOW_PCT;
    s_critical_warned = false;
    return;
  }

  /* Warn once per SOC_WARN_STEP_PCT drop */
  if (soc <= (s_last_warn_soc - SOC_WARN_STEP_PCT))
  {
    s_last_warn_soc = soc;
    RLOG_W(LOG_OBJECT_CODE_BATTERY, "Low battery: SOC = %u%%", soc);
  }

  /* Critical — fire once per descent below threshold */
  if (soc < SOC_THRESHOLD_CRITICAL_PCT && !s_critical_warned)
  {
    s_critical_warned = true;
    RLOG_E(LOG_OBJECT_CODE_BATTERY, ERR_BATTERY_CRITICAL, "Battery critically low: SOC = %u%%", soc);
  }
}

static void s_check_crate(void)
{
  int16_t cr = s_bat.crate_phr;

  if (cr > CRATE_OVERCHARGE_WARN)
  {
    RLOG_E(LOG_OBJECT_CODE_BATTERY, ERR_BATTERY_OVERCHARGE_RATE, "Charge rate too high: %d %%/hr", cr);
  }
  else if (cr < CRATE_OVERDISCHARGE_WARN)
  {
    RLOG_E(LOG_OBJECT_CODE_BATTERY, ERR_BATTERY_OVERDISCHARGE_RATE, "Discharge rate too high: %d %%/hr",
           cr);
  }
  else if (cr > 0 && cr < CRATE_SLOW_CHARGE_WARN)
  {
    RLOG_W(LOG_OBJECT_CODE_BATTERY, ERR_BATTERY_SLOW_CHARGE, "Charge rate very slow: %d %%/hr", cr);
  }
}

static void s_handle_alerts(void)
{
  uint16_t status = 0;
  if (max17048_read_status(&s_dev, &status, 0) != MAX17048_OK)
    return; /* Skip this cycle — will retry on next bsp_battery_task() call */

  if (status & MAX17048_STATUS_VR)
  {
    /* Battery swapped — IC has already re-estimated SOC automatically */
    s_last_warn_soc   = SOC_THRESHOLD_LOW_PCT;
    s_critical_warned = false;
    RLOG_I(LOG_OBJECT_CODE_BATTERY, "Battery swapped — SOC re-estimated: %u%%", s_bat.soc_pct);
  }

  if (status & MAX17048_STATUS_HD)
  {
    /* Hardware empty alert: SOC dropped below EMPTY_ALERT_PCT threshold */
    if (!s_critical_warned)
    {
      s_critical_warned = true;
      RLOG_E(LOG_OBJECT_CODE_BATTERY, ERR_BATTERY_CRITICAL, "Battery critically low (HD alert): SOC = %u%%",
             s_bat.soc_pct);
    }
    /* TODO: ble_battery_critical_notify(); */
  }

  if (status & MAX17048_STATUS_VL)
  {
    /* Voltage fell below VALRT_MIN_MV */
    RLOG_E(LOG_OBJECT_CODE_BATTERY, ERR_BATTERY_LOW, "Voltage critically low: %u mV", s_bat.voltage_mv);
    /* TODO: trigger system shutdown or charge request */
  }

  if (status & MAX17048_STATUS_VH)
  {
    /* Voltage exceeded VALRT_MAX_MV — possible overcharge */
    RLOG_E(LOG_OBJECT_CODE_BATTERY, ERR_BATTERY_OVERVOLT, "Overvoltage detected: %u mV", s_bat.voltage_mv);
  }

  /* Must always clear alert pin after handling */
  max17048_clear_alert(&s_dev);
}

static int32_t s_i2c_write(uint8_t dev_addr, uint8_t reg_addr, const uint8_t *data, uint16_t len)
{
  /* MAX17048 uses 7-bit addressing (0x36); HAL expects the address left-shifted by 1 */
  HAL_StatusTypeDef ret = HAL_I2C_Mem_Write(&BSP_BATTERY_I2C_HANDLE, (uint16_t) (dev_addr << 1), reg_addr,
                                            I2C_MEMADD_SIZE_8BIT, (uint8_t *) data, len, I2C_TIMEOUT_MS);

  return (ret == HAL_OK) ? 0 : -1;
}

static int32_t s_i2c_read(uint8_t dev_addr, uint8_t reg_addr, uint8_t *data, uint16_t len)
{
  HAL_StatusTypeDef ret = HAL_I2C_Mem_Read(&BSP_BATTERY_I2C_HANDLE, (uint16_t) (dev_addr << 1), reg_addr,
                                           I2C_MEMADD_SIZE_8BIT, data, len, I2C_TIMEOUT_MS);

  return (ret == HAL_OK) ? 0 : -1;
}

/* End of file -------------------------------------------------------------- */
