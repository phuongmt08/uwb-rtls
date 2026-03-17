/**
 * @file       bsp_battery.h
 * @copyright
 * @license
 * @version    1.1.0
 * @date       2026-03-17
 * @author
 * @brief      BSP layer for MAX17048 fuel gauge
 * @note       Hardware: STM32F411CEUx
 *             I2C3 — SCL: PA8 | SDA: PC9
 *             Upper layers only need to include this file — not max17048.h
 */

#ifndef __BSP_BATTERY_H
#define __BSP_BATTERY_H

/* Includes ----------------------------------------------------------------- */
#include "main.h"
#include <stdbool.h>
#include <stdint.h>

/* Public defines ----------------------------------------------------------- */

/* --- I2C peripheral ------------------------------------------------------- */
#ifndef BSP_BATTERY_I2C_HANDLE
  #define BSP_BATTERY_I2C_HANDLE      hi2c3
#endif

#ifndef BSP_BATTERY_I2C_TIMEOUT_MS
  #define BSP_BATTERY_I2C_TIMEOUT_MS  100
#endif

/* --- Battery config (1-cell lipo) ----------------------------------------- */

/* Alert when voltage drops below 3.0V — lipo cutoff */
#ifndef BSP_BATTERY_VALRT_MIN_MV
  #define BSP_BATTERY_VALRT_MIN_MV    3000
#endif

/* Alert when voltage exceeds 4.2V — lipo full charge */
#ifndef BSP_BATTERY_VALRT_MAX_MV
  #define BSP_BATTERY_VALRT_MAX_MV    4200
#endif

/*
 * Reset threshold — below this = battery removed, above = new battery inserted
 * 2400mV is safely below lipo cutoff (3.0V) so normal discharge
 * does not trigger a false reset
 */
#ifndef BSP_BATTERY_VRESET_MV
  #define BSP_BATTERY_VRESET_MV       2400
#endif

/* SOC threshold to fire low battery alert */
#ifndef BSP_BATTERY_EMPTY_ALERT_PCT
  #define BSP_BATTERY_EMPTY_ALERT_PCT 10
#endif

/*
 * Assumed battery temperature in degrees Celsius
 * Used for RCOMP compensation — IC nearby runs warm so fixed at 40°C
 * Update this if a temperature sensor is added later
 */
#ifndef BSP_BATTERY_TEMP_DEGC
  #define BSP_BATTERY_TEMP_DEGC       40
#endif

/* Public enumerate/structure ----------------------------------------------- */

/**
 * @brief Battery BSP return codes
 */
typedef enum
{
  BSP_BATTERY_OK         =  0,
  BSP_BATTERY_ERR        = -1,
  BSP_BATTERY_ERR_BUS    = -2,
  BSP_BATTERY_ERR_PARAM  = -3,
  BSP_BATTERY_ERR_NO_DEV = -4,
} bsp_battery_err_t;

/**
 * @brief Battery status — what upper layers need to know about the battery
 * @note  Internal details like alert flags, hibernation, raw register values
 *        are handled by the BSP layer and not exposed here
 */
typedef struct
{
  uint16_t voltage_mv;    /* Cell voltage in mV                              */
  uint8_t  soc_pct;       /* State of charge, integer 0-100 %               */
  uint8_t  soc_frac;      /* SOC fractional part 0-255, unit 1/256 %        */
  int16_t  crate_mphph;   /* Charge rate in milli-%/hr, negative = discharge */
} bsp_battery_data_t;

/* Public function prototypes ----------------------------------------------- */

/**
 * @brief  Initialize fuel gauge — bind HAL, apply lipo config, set temp comp
 * @return BSP_BATTERY_OK on success
 */
bsp_battery_err_t bsp_battery_init(void);

/**
 * @brief  Read battery data — voltage, SOC, charge rate
 * @param  data  Output struct
 * @return BSP_BATTERY_OK on success
 */
bsp_battery_err_t bsp_battery_read(bsp_battery_data_t *data);

/**
 * @brief  Check if IC is present on I2C bus
 * @return true if device responds
 */
bool bsp_battery_is_present(void);

#endif /* __BSP_BATTERY_H */

/* End of file -------------------------------------------------------------- */