/**
 * @file       bsp_battery.h
 * @version    2.0.0
 * @date       2026-03-27
 * @brief      Battery BSP for MAX17048 fuel gauge — STM32F411CEUx
 */

#ifndef BSP_BATTERY_H
#define BSP_BATTERY_H

/* Includes ----------------------------------------------------------------- */
#include "log_config.h"
#include "main.h"
#include <stdbool.h>
#include <stdint.h>

/* Configuration ------------------------------------------------------------ */

#ifndef BSP_BATTERY_I2C_HANDLE
  #define BSP_BATTERY_I2C_HANDLE  hi2c1
#endif

/* Public types ------------------------------------------------------------- */

/** @brief Return codes for all BSP battery functions */
typedef enum
{
  BSP_BATTERY_OK         =  0,  /**< Success                        */
  BSP_BATTERY_ERR        = -1,  /**< General error                  */
  BSP_BATTERY_ERR_BUS    = -2,  /**< I2C communication failed       */
  BSP_BATTERY_ERR_PARAM  = -3,  /**< Invalid parameter (NULL, etc.) */
  BSP_BATTERY_ERR_NO_DEV = -4,  /**< IC not found on I2C bus        */
} bsp_battery_err_t;

/**
 * @brief Charge level status based on SOC
 *
 * | Status   | SOC range | Action                            |
 * |----------|-----------|-----------------------------------|
 * | FULL     | > 90 %    | Fully charged                     |
 * | HALF     | 20–70 %   | Normal operation                  |
 * | LOW      | 10–20 %   | Warning fires every 2% drop       |
 * | CRITICAL | < 10 %    | Charge immediately                |
 *
 * Safe to cast to uint8_t for BLE payloads.
 */
typedef enum
{
  BSP_BATTERY_STATUS_CRITICAL = 0,  /**< SOC < 10 %  */
  BSP_BATTERY_STATUS_LOW      = 1,  /**< SOC 10–20 % */
  BSP_BATTERY_STATUS_HALF     = 2,  /**< SOC 20–70 % */
  BSP_BATTERY_STATUS_FULL     = 3,  /**< SOC > 90 %  */
} bsp_battery_status_t;

/**
 * @brief Full battery snapshot — populated by bsp_battery_get_info()
 *
 * All fields reflect the last successful bsp_battery_task() read.
 * Use this struct when sending a BLE battery payload.
 *
 * @code
 *   bsp_battery_info_t info;
 *   bsp_battery_get_info(&info);
 *   ble_battery_notify(&info, sizeof(info));
 * @endcode
 */
typedef struct
{
  uint16_t             voltage_mv;      /**< Battery voltage in mV                       */
  uint8_t              soc_pct;         /**< State of charge, 0–100 %                     */
  int16_t              crate_mphph;     /**< Charge rate in milli-%/hr (+charge, -discharge) */
  int32_t              remaining_min;   /**< Estimated remaining time in minutes*/
  bsp_battery_status_t status;          /**< Charge level: CRITICAL / LOW / HALF / FULL   */
  bool                 is_charging;     /**< true if crate > +10 milli-%/hr               */
  bool                 is_present;      /**< true if IC responds on I2C bus                */
} bsp_battery_info_t;

/* Public API --------------------------------------------------------------- */

/**
 * @brief  Initialize the MAX17048 fuel gauge.
 *         Call once at startup before bsp_battery_task().
 *
 * @return BSP_BATTERY_OK      on success
 * @return BSP_BATTERY_ERR_BUS if I2C is unresponsive
 * @return BSP_BATTERY_ERR     on any other init failure
 */
bsp_battery_err_t bsp_battery_init(void);

/**
 * @brief  Main battery task — call periodically (e.g. every 1 s or 10 s).
 *
 *         Each call performs one I2C burst read, then:
 *           - Logs voltage, SOC, charge rate, and status
 *           - Fires a low-battery warning every 2% drop below 20% SOC
 *           - Fires a critical warning once when SOC drops below 10%
 *           - Handles IC hardware alert flags (VR / HD / VL / VH)
 *           - Checks for abnormal charge/discharge rates
 *
 * @return BSP_BATTERY_OK  on success
 * @return BSP_BATTERY_ERR if the I2C read failed (retried next call)
 */
bsp_battery_err_t bsp_battery_task(void);

/**
 * @brief  Get a full battery snapshot (no I2C read — uses last task data).
 *
 * @param[out] info  Pointer to caller-allocated struct to fill.
 * @return BSP_BATTERY_OK        on success
 * @return BSP_BATTERY_ERR_PARAM if info is NULL
 */
bsp_battery_err_t bsp_battery_get_info(bsp_battery_info_t *info);

/**
 * @brief  Get last measured voltage.
 * @return Voltage in mV (e.g. 3742), or 0 before first successful read.
 */
uint16_t bsp_battery_get_voltage(void);

/**
 * @brief  Get last measured state of charge.
 * @return SOC as integer percentage 0–100.
 */
uint8_t bsp_battery_get_soc(void);

/**
 * @brief  Get last measured charge/discharge rate.
 * @return Rate in milli-%/hr — positive = charging, negative = discharging.
 */
int16_t bsp_battery_get_crate(void);

/**
 * @brief  Get current charge level status.
 * @return BSP_BATTERY_STATUS_FULL / HALF / LOW / CRITICAL
 */
bsp_battery_status_t bsp_battery_get_status(void);

/**
 * @brief  Estimate remaining time based on current charge rate.
 *
 * @note   This is a rough estimate — no battery capacity or actual current
 *         is available from the MAX17048.  Use with caution.
 *
 * @return Remaining time in minutes (positive = discharging, negative = charging to full).
 *         Returns INT32_MIN if the rate is zero (idle / indeterminate).
 */
int32_t bsp_battery_get_remaining_time(void);

/**
 * @brief  Check if the battery IC is present on the I2C bus.
 * @return true if detected, false otherwise.
 */
bool bsp_battery_is_present(void);

#endif /* BSP_BATTERY_H */

/* End of file -------------------------------------------------------------- */