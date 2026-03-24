/**
 * @file       bsp_battery.h
 * @version    1.4.0
 * @date       2026-03-25
 * @brief      Battery BSP for MAX17048 fuel gauge — STM32F411CEUx
 *
 * How to use:
 *
 *   // 1. Call once at startup
 *   bsp_battery_init();
 *
 *   // 2. Call periodically — reads all data, logs status, handles alerts
 *   bsp_battery_task();   // call every 1s or 10s
 *
 *   // 3. Get individual values after task runs (no extra I2C read)
 *   uint16_t mv     = bsp_battery_get_voltage();
 *   uint8_t  soc    = bsp_battery_get_soc();
 *   int16_t  rate   = bsp_battery_get_crate();
 *   bsp_battery_status_t s = bsp_battery_get_status();
 *
 * Log output (printed by bsp_battery_task on every call):
 *   [BATTERY] --- Battery Status ---
 *   [BATTERY] Voltage  : 3742 mV
 *   [BATTERY] SOC      : 73.44%
 *   [BATTERY] CRate    : -237 milli%/hr (Discharging)
 *   [BATTERY] Status   : HALF
 *   [BATTERY] Hibernate: NO
 *
 * Sending to app via BLE (implement later):
 *   payload.voltage_mv     = bsp_battery_get_voltage();
 *   payload.soc_pct        = bsp_battery_get_soc();
 *   payload.battery_status = (uint8_t)bsp_battery_get_status();
 *   ble_battery_notify(&payload, sizeof(payload));
 *
 * Hardware:
 *   I2C3 — SCL: PA8 | SDA: PC9
 */

#ifndef __BSP_BATTERY_H
#define __BSP_BATTERY_H

/* Includes ----------------------------------------------------------------- */
#include "main.h"
#include "log_config.h"
#include <stdbool.h>
#include <stdint.h>

/* Configuration ------------------------------------------------------------ */

#ifndef BSP_BATTERY_I2C_HANDLE
  #define BSP_BATTERY_I2C_HANDLE         hi2c1
#endif

#ifndef BSP_BATTERY_I2C_TIMEOUT_MS
  #define BSP_BATTERY_I2C_TIMEOUT_MS     100
#endif

#ifndef BSP_BATTERY_VALRT_MIN_MV
  #define BSP_BATTERY_VALRT_MIN_MV       3000    /* alert if voltage drops below this */
#endif

#ifndef BSP_BATTERY_VALRT_MAX_MV
  #define BSP_BATTERY_VALRT_MAX_MV       4200    /* alert if voltage exceeds this     */
#endif

#ifndef BSP_BATTERY_VRESET_MV
  #define BSP_BATTERY_VRESET_MV          2400    /* battery swap detection threshold  */
#endif

#ifndef BSP_BATTERY_EMPTY_ALERT_PCT
  #define BSP_BATTERY_EMPTY_ALERT_PCT    10      /* IC fires alert when SOC < 10%     */
#endif

#ifndef BSP_BATTERY_TEMP_DEGC
  #define BSP_BATTERY_TEMP_DEGC          40      /* assumed ambient temperature in °C */
#endif

/* Warning fires every time SOC drops by this amount while below 20%
 * e.g. warn at 20%, 18%, 16%, 14% ... */
#ifndef BSP_BATTERY_WARN_STEP_PCT
  #define BSP_BATTERY_WARN_STEP_PCT      2
#endif

#ifndef BSP_BATTERY_CRATE_OVERCHARGE_WARN
  #define BSP_BATTERY_CRATE_OVERCHARGE_WARN   5000  /* > 5000 milli%/hr: sạc quá nhanh (nguy hiểm, ~0.25C) */
#endif

#ifndef BSP_BATTERY_CRATE_OVERDISCHARGE_WARN
  #define BSP_BATTERY_CRATE_OVERDISCHARGE_WARN -10000 /* < -10000 milli%/hr: xả quá nhanh (tụt pin nhanh, ~0.5C) */
#endif

#ifndef BSP_BATTERY_CRATE_SLOW_CHARGE_WARN
  #define BSP_BATTERY_CRATE_SLOW_CHARGE_WARN  500   /* < 500 milli%/hr khi sạc: sạc quá chậm */
#endif

/* Return codes ------------------------------------------------------------- */

typedef enum
{
  BSP_BATTERY_OK         =  0,  /* success                        */
  BSP_BATTERY_ERR        = -1,  /* general error                  */
  BSP_BATTERY_ERR_BUS    = -2,  /* I2C communication failed       */
  BSP_BATTERY_ERR_PARAM  = -3,  /* invalid parameter (NULL, etc.) */
  BSP_BATTERY_ERR_NO_DEV = -4,  /* IC not found on I2C bus        */
} bsp_battery_err_t;

/* Battery status ----------------------------------------------------------- */

/**
 * @brief Charge level status
 *
 * FULL     — SOC > 50%,  all good
 * HALF     — SOC 20-50%, normal operation
 * LOW      — SOC < 20%,  warning fires every 2% drop automatically
 * CRITICAL — SOC < 10%,  charge immediately
 *
 * Cast to uint8_t when putting into a BLE payload:
 *   payload.battery_status = (uint8_t)bsp_battery_get_status();
 */
typedef enum
{
  BSP_BATTERY_STATUS_CRITICAL = 0,  /* SOC < 10%  */
  BSP_BATTERY_STATUS_LOW      = 1,  /* SOC < 20%  */
  BSP_BATTERY_STATUS_HALF     = 2,  /* SOC 20-70% */
  BSP_BATTERY_STATUS_FULL     = 3,  /* SOC > 70%  */
} bsp_battery_status_t;

/* API ---------------------------------------------------------------------- */

/**
 * @brief  Initialize the fuel gauge
 *         Call once at startup before bsp_battery_task()
 * @return BSP_BATTERY_OK on success
 *         BSP_BATTERY_ERR_NO_DEV if IC is not found on the bus
 */
bsp_battery_err_t bsp_battery_init(void);

/**
 * @brief  Main battery task — call this periodically (e.g. every 1s or 10s)
 *
 *         Each call:
 *           - Reads voltage, SOC, charge rate, hibernate state
 *           - Prints full status log via sys_logger
 *           - Handles IC alert flags internally (VR, HD, VL, VH)
 *           - Fires low-battery warning every 2% drop below 20%
 *
 * @return BSP_BATTERY_OK on success
 */
bsp_battery_err_t bsp_battery_task(void);

/**
 * @brief  Get last measured voltage (updated by bsp_battery_task)
 * @return voltage in mV (e.g. 3742)
 */
uint16_t bsp_battery_get_voltage(void);

/**
 * @brief  Get last measured SOC (updated by bsp_battery_task)
 * @return integer SOC 0-100 % (e.g. 73)
 */
uint8_t bsp_battery_get_soc(void);

/**
 * @brief  Get last measured charge/discharge rate (updated by bsp_battery_task)
 * @return rate in milli-%/hr — positive = charging, negative = discharging
 */
int16_t bsp_battery_get_crate(void);

/**
 * @brief  Get current charge level status (based on last bsp_battery_task reading)
 * @return BSP_BATTERY_STATUS_FULL / HALF / LOW / CRITICAL
 */
bsp_battery_status_t bsp_battery_get_status(void);

/**
 * @brief  Check if the IC is present on the I2C bus
 * @return true if IC responds
 */
bool bsp_battery_is_present(void);

#endif /* __BSP_BATTERY_H */

/* End of file -------------------------------------------------------------- */