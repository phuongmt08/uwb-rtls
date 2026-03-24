/**
 * @file       bsp_battery.c
 * @version    1.4.0
 * @date       2026-03-25
 * @brief      Battery BSP for MAX17048 fuel gauge — STM32F411CEUx
 * @note       I2C3 — SCL: PA8 | SDA: PC9
 */
/*
 * Usage:
 *
 *   // 1. Call once at startup
 *   bsp_battery_init();
 *
 *   // 2. Call periodically (e.g. every 1s or 10s)
 *   bsp_battery_task();     // reads all data, logs status, handles alerts
 *
 *   // 3. Get individual values after task runs (no extra I2C read)
 *   uint16_t mv     = bsp_battery_get_voltage();
 *   uint8_t  soc    = bsp_battery_get_soc();
 *   int16_t  rate   = bsp_battery_get_crate();
 *   bsp_battery_status_t st = bsp_battery_get_status();
 *   bool present = bsp_battery_is_present();
 *
 * Internal:
 *   - Reads voltage, SOC (with fractional part), charge rate from MAX17048
 *   - Logs full battery status every call
 *   - Handles IC alert flags (VR=swap, HD=critical, VL=low voltage, VH=overvoltage)
 *   - Fires low-battery warnings every 2% drop below 20% SOC
 */
/* Includes ----------------------------------------------------------------- */
#include "bsp_battery.h"
#include "max17048.h"
#include "sys_logger.h"
#include "log_config.h"
/* Private defines ---------------------------------------------------------- */
#define SOC_THRESHOLD_FULL      70u
#define SOC_THRESHOLD_LOW       20u
#define SOC_THRESHOLD_CRITICAL  10u

/* Private variables -------------------------------------------------------- */
extern I2C_HandleTypeDef BSP_BATTERY_I2C_HANDLE;
static max17048_dev_t dev;
static max17048_data_t bat_data;

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
static bsp_battery_status_t s_get_status             (uint8_t soc_pct);
static void                 s_handle_alerts          (void);
static void                 s_check_low_battery_warn (uint8_t soc_pct);

/* Public function implementation ------------------------------------------- */
bsp_battery_err_t bsp_battery_task(void)
{
  max17048_err_t err = max17048_read_all(&dev, &bat_data);
  if (err != MAX17048_OK)
  {
    uint8_t err_code = (err == MAX17048_ERR_BUS) ? ERR_BATTERY_I2C : ERR_BATTERY_READ;
    RLOG_E(LOG_OBJECT_CODE_BATTERY, err_code, "Read failed: %d", err);
    return BSP_BATTERY_ERR;
  }

  bsp_battery_status_t status = s_get_status(bat_data.soc_pct);
  const char *status_str = (status == BSP_BATTERY_STATUS_FULL)     ? "FULL"     :
                           (status == BSP_BATTERY_STATUS_HALF)     ? "HALF"     :
                           (status == BSP_BATTERY_STATUS_LOW)      ? "LOW"      :
                                                                      "CRITICAL";

  const char *charge_str = (bat_data.crate_mphph > 10)  ? "Charging"    :
                           (bat_data.crate_mphph < -10) ? "Discharging" :
                                                             "Idle";

  RLOG_I(LOG_OBJECT_CODE_BATTERY, "--- Battery Status ---");
  RLOG_I(LOG_OBJECT_CODE_BATTERY, "Voltage  : %u mV",               bat_data.voltage_mv);
  RLOG_I(LOG_OBJECT_CODE_BATTERY, "SOC      : %u.%02u%%",           bat_data.soc_pct,
                                               (bat_data.soc_frac * 100u) / 256u);
  RLOG_I(LOG_OBJECT_CODE_BATTERY, "CRate    : %d milli%%/hr (%s)",  bat_data.crate_mphph, charge_str);
  RLOG_I(LOG_OBJECT_CODE_BATTERY, "Status   : %s",                  status_str);
  RLOG_I(LOG_OBJECT_CODE_BATTERY, "Hibernate: %s",                  bat_data.is_hibernating ? "YES" : "NO");

  /* Check CRATE warnings */
  if (bat_data.crate_mphph > BSP_BATTERY_CRATE_OVERCHARGE_WARN) {
    RLOG_E(LOG_OBJECT_CODE_BATTERY, ERR_BATTERY_OVERCHARGE_RATE, "Overcharge rate: CRate = %d milli%%/hr (charging too fast)", bat_data.crate_mphph);
    /* TODO: BLE notify: ble_battery_overcharge_rate_notify(bat_data.crate_mphph); */
  } else if (bat_data.crate_mphph < BSP_BATTERY_CRATE_OVERDISCHARGE_WARN) {
    RLOG_E(LOG_OBJECT_CODE_BATTERY, ERR_BATTERY_OVERDISCHARGE_RATE, "Overdischarge rate: CRate = %d milli%%/hr (discharging too fast)", bat_data.crate_mphph);
    /* TODO: BLE notify: ble_battery_overdischarge_rate_notify(bat_data.crate_mphph); */
  } else if (bat_data.crate_mphph > 0 && bat_data.crate_mphph < BSP_BATTERY_CRATE_SLOW_CHARGE_WARN) {
    RLOG_E(LOG_OBJECT_CODE_BATTERY, ERR_BATTERY_SLOW_CHARGE, "Slow charge: CRate = %d milli%%/hr (charging too slow)", bat_data.crate_mphph);
    /* TODO: BLE notify: ble_battery_slow_charge_notify(bat_data.crate_mphph); */
  }

  if (bat_data.alert_active)
    s_handle_alerts();

  s_check_low_battery_warn(bat_data.soc_pct);
  return BSP_BATTERY_OK;
}

bsp_battery_err_t bsp_battery_init(void)
{
  dev.bus.i2c_write = s_i2c_write;
  dev.bus.i2c_read  = s_i2c_read;

  max17048_err_t err = max17048_init(&dev, &s_lipo_cfg);
  if (err != MAX17048_OK)
  {
    uint8_t err_code = (err == MAX17048_ERR_BUS) ? ERR_BATTERY_I2C : ERR_BATTERY_INIT;
    RLOG_E(LOG_OBJECT_CODE_BATTERY, err_code, "Init failed: %d", err);
    return BSP_BATTERY_ERR;
  }

  /* Fixed temperature compensation — nearby ICs keep the board warm at ~40°C */
  max17048_update_temp_comp(&dev, BSP_BATTERY_TEMP_DEGC);
  s_last_warn_soc = SOC_THRESHOLD_LOW;

  RLOG_I(LOG_OBJECT_CODE_BATTERY, "Init OK — temp comp %d°C, VRESET %dmV",
            BSP_BATTERY_TEMP_DEGC, BSP_BATTERY_VRESET_MV);
  return BSP_BATTERY_OK;
}

uint16_t bsp_battery_get_voltage(void)
{
  return bat_data.voltage_mv;
}

uint8_t bsp_battery_get_soc(void)
{
  return bat_data.soc_pct;
}

int16_t bsp_battery_get_crate(void)
{
  return bat_data.crate_mphph;
}

bsp_battery_status_t bsp_battery_get_status(void)
{
  return s_get_status(bat_data.soc_pct);
}

bool bsp_battery_is_present(void)
{
  return max17048_is_present(&dev);
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
   * Timeline:
   *   20% → warn, tracker = 20
   *   19% → skip  (19 > 20 - 2 = 18)
   *   18% → warn, tracker = 18 ... and so on
   */
  if (soc_pct >= SOC_THRESHOLD_LOW)
  {
    s_last_warn_soc = SOC_THRESHOLD_LOW;
    return;
  }

  if (soc_pct <= (s_last_warn_soc - BSP_BATTERY_WARN_STEP_PCT))
  {
    s_last_warn_soc = soc_pct;
    RLOG_W(LOG_OBJECT_CODE_BATTERY, "Low battery warning: SOC = %u%%", soc_pct);

    /* TODO: add action here when ready
     *   BLE notify → ble_battery_warn_notify(soc_pct);  (implement later) */
  }
}

static void s_handle_alerts(void)
{
  uint16_t status = 0;
  if (max17048_read_status(&dev, &status, 0) != MAX17048_OK)
  /* If fail to read status, skip handling alerts this cycle — we'll try again on the next bsp_battery_task() call. */
    return;

  if (status & MAX17048_STATUS_VR)
  {
    /* Battery was removed and a new one was swapped.
     * IC already re-estimated SOC automatically. */
    s_last_warn_soc = SOC_THRESHOLD_LOW;
    RLOG_I(LOG_OBJECT_CODE_BATTERY, "Battery swapped — SOC re-estimated: %u%%", bat_data.soc_pct);
  }

  if (status & MAX17048_STATUS_HD)
  {
    /* SOC dropped below empty alert threshold (10%) — critically low */
    RLOG_E(LOG_OBJECT_CODE_BATTERY, ERR_BATTERY_CRITICAL, "Battery critically low: SOC = %u%%", bat_data.soc_pct);

    /* TODO: ble_battery_critical_notify(); */
  }

  if (status & MAX17048_STATUS_VL)
  {
    /* Voltage dropped below VALRT.MIN (3000mV) */
    RLOG_E(LOG_OBJECT_CODE_BATTERY, ERR_BATTERY_LOW, "Voltage critically low: %u mV", bat_data.voltage_mv);

    /* TODO: same action as HD */
  }

  if (status & MAX17048_STATUS_VH)
  {
    /* Voltage exceeded VALRT.MAX (4200mV) — overcharge */
    RLOG_E(LOG_OBJECT_CODE_BATTERY, ERR_BATTERY_OVERVOLT, "Overvoltage detected: %u mV", bat_data.voltage_mv);
  }

  /* Always clear the alert flag after handling — must not skip this */
  max17048_clear_alert(&dev);
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

/* End of file -------------------------------------------------------------- */