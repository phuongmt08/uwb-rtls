/**
 * @file       bsp_battery.h
 * @version    1.2.0
 * @date       2026-03-21
 * @brief      Battery BSP for MAX17048 fuel gauge — STM32F411CEUx
 *
 * How to use:
 *
 *   // 1. Call once at startup
 *   bsp_battery_init();
 *
 *   // 2. Read everything in one call
 *   bsp_battery_data_t   bat;
 *   bsp_battery_status_t status;
 *   bsp_battery_read_all(&bat);        // fills voltage_mv, soc_pct, crate_mphph
 *   bsp_battery_read_status(&status);  // FULL / HALF / LOW / CRITICAL
 *
 *   // 3. Or read individual values if you only need one
 *   uint16_t mv;   bsp_battery_read_voltage(&mv);
 *   uint8_t  soc;  bsp_battery_read_soc(&soc, NULL);  // pass NULL to skip fractional
 *   int16_t  rate; bsp_battery_read_crate(&rate);      // negative = discharging
 *
 * Sending to app via BLE (implement later):
 *
 *   bsp_battery_read_all(&bat);
 *   bsp_battery_read_status(&status);
 *
 *   payload.voltage_mv     = bat.voltage_mv;
 *   payload.soc_pct        = bat.soc_pct;
 *   payload.crate_mphph    = bat.crate_mphph;
 *   payload.battery_status = (uint8_t)status;  // cast enum to byte for transport
 *
 *   ble_battery_notify(&payload, sizeof(payload));
 *   // tip: only notify on status change or every N seconds to save BLE bandwidth
 *
 * Hardware:
 *   I2C3 — SCL: PA8 | SDA: PC9
 */

#ifndef __BSP_BATTERY_H
#define __BSP_BATTERY_H

/* Includes ----------------------------------------------------------------- */
#include "main.h"
#include <stdbool.h>
#include <stdint.h>

/* Configuration ------------------------------------------------------------ */

#ifndef BSP_BATTERY_I2C_HANDLE
  #define BSP_BATTERY_I2C_HANDLE         hi2c1
#endif

#ifndef BSP_BATTERY_I2C_TIMEOUT_MS
  #define BSP_BATTERY_I2C_TIMEOUT_MS     100     /* ms */
#endif

#ifndef BSP_BATTERY_VALRT_MIN_MV
  #define BSP_BATTERY_VALRT_MIN_MV       3000    /* alert if voltage drops below this */
#endif

#ifndef BSP_BATTERY_VALRT_MAX_MV
  #define BSP_BATTERY_VALRT_MAX_MV       4200    /* alert if voltage exceeds this     */
#endif

#ifndef BSP_BATTERY_VRESET_MV
  #define BSP_BATTERY_VRESET_MV          2800    /* battery swap detection threshold  */
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

/* Return codes ------------------------------------------------------------- */

typedef enum
{
  BSP_BATTERY_OK         =  0,  /* success                         */
  BSP_BATTERY_ERR        = -1,  /* general error                   */
  BSP_BATTERY_ERR_BUS    = -2,  /* I2C communication failed        */
  BSP_BATTERY_ERR_PARAM  = -3,  /* invalid parameter (NULL, etc.)  */
  BSP_BATTERY_ERR_NO_DEV = -4,  /* IC not found on I2C bus         */
} bsp_battery_err_t;

/* Battery status ----------------------------------------------------------- */

/**
 * @brief Charge level status — use this to drive UI or trigger alerts
 *
 * FULL     — SOC > 50%,  all good
 * HALF     — SOC 20-50%, normal operation
 * LOW      — SOC < 20%,  warning fires every 2% drop (see bsp_battery.c)
 * CRITICAL — SOC < 10%,  charge immediately
 *
 * Cast to uint8_t when putting into a BLE payload:
 *   payload.battery_status = (uint8_t)status;
 */
typedef enum
{
  BSP_BATTERY_STATUS_CRITICAL = 0,  /* SOC < 10%  */
  BSP_BATTERY_STATUS_LOW      = 1,  /* SOC < 20%  */
  BSP_BATTERY_STATUS_HALF     = 2,  /* SOC 20-50% */
  BSP_BATTERY_STATUS_FULL     = 3,  /* SOC > 50%  */
} bsp_battery_status_t;

/* Data --------------------------------------------------------------------- */

/**
 * @brief All battery measurements in one struct
 *
 * voltage_mv   — cell voltage in mV            (e.g. 3742)
 * soc_pct      — state of charge, 0-100 %      (e.g. 73)
 * soc_frac     — fractional SOC, unit 1/256 %  (e.g. 115 means 73.45%)
 * crate_mphph  — charge/discharge rate in milli-%/hr
 *                positive = charging, negative = discharging  (e.g. -237)
 */
typedef struct
{
  uint16_t voltage_mv;
  uint8_t  soc_pct;
  uint8_t  soc_frac;
  int16_t  crate_mphph;
} bsp_battery_data_t;

/* API ---------------------------------------------------------------------- */

/**
 * @brief  Initialize the fuel gauge
 *         Call once at startup before any read function
 * @return BSP_BATTERY_OK on success
 *         BSP_BATTERY_ERR_NO_DEV if IC is not found on the bus
 */
bsp_battery_err_t bsp_battery_init(void);

/**
 * @brief  Read all battery measurements in one call
 *         Also handles IC alerts internally — no extra calls needed
 * @param  data  Output struct
 * @return BSP_BATTERY_OK on success
 */
bsp_battery_err_t bsp_battery_read_all(bsp_battery_data_t *data);

/**
 * @brief  Read charge level status
 *         Returns FULL / HALF / LOW / CRITICAL based on current SOC
 *         When LOW, an internal warning fires every 2% drop automatically
 * @param  status  Output
 * @return BSP_BATTERY_OK on success
 *
 * Example:
 *   bsp_battery_status_t status;
 *   bsp_battery_read_status(&status);
 *   if (status == BSP_BATTERY_STATUS_CRITICAL) { ... }
 */
bsp_battery_err_t bsp_battery_read_status(bsp_battery_status_t *status);

/**
 * @brief  Read cell voltage
 * @param  voltage_mv  Output: voltage in mV (e.g. 3742)
 * @return BSP_BATTERY_OK on success
 */
bsp_battery_err_t bsp_battery_read_voltage(uint16_t *voltage_mv);

/**
 * @brief  Read state of charge
 * @param  soc_pct   Output: integer SOC 0-100 % (e.g. 73)
 * @param  soc_frac  Output: fractional part 0-255, unit 1/256 %
 *                   Pass NULL if you don't need the fractional part
 * @return BSP_BATTERY_OK on success
 */
bsp_battery_err_t bsp_battery_read_soc(uint8_t *soc_pct, uint8_t *soc_frac);

/**
 * @brief  Read charge/discharge rate
 * @param  crate_mphph  Output: rate in milli-%/hr
 *                      Positive = charging, negative = discharging (e.g. -237)
 * @return BSP_BATTERY_OK on success
 */
bsp_battery_err_t bsp_battery_read_crate(int16_t *crate_mphph);

/**
 * @brief  Check if the IC is present on the I2C bus
 * @return true if IC responds
 */
bool bsp_battery_is_present(void);

#endif /* __BSP_BATTERY_H */

/* End of file -------------------------------------------------------------- */