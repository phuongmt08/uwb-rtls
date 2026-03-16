/**
 * @file       bsp_battery.h
 * @copyright
 * @license
 * @version    1.0.0
 * @date       2026-03-14
 * @author     Trung Quan
 * @brief      BSP layer for MAX17048 fuel gauge
 * @note       Hardware: STM32F411CEUx
 *             I2C3  — SCL: PA8 | SDA: PC9
 *             LED   — PWM on PC13 via TIM1_CH3
 */

#ifndef __BSP_BATTERY_H
#define __BSP_BATTERY_H

/* Includes ----------------------------------------------------------------- */
#include "max17048.h"
#include "main.h"

/* Public defines ----------------------------------------------------------- */

/* --- I2C peripheral ------------------------------------------------------- */
#ifndef BSP_BATTERY_I2C_HANDLE
  #define BSP_BATTERY_I2C_HANDLE         hi2c3
#endif

#ifndef BSP_BATTERY_I2C_TIMEOUT_MS
  #define BSP_BATTERY_I2C_TIMEOUT_MS     100
#endif

/* --- PWM timer for LED (PC13 = TIM1_CH3) ---------------------------------- */
#ifndef BSP_BATTERY_TIM_HANDLE
  #define BSP_BATTERY_TIM_HANDLE         htim1
#endif

#ifndef BSP_BATTERY_TIM_CHANNEL
  #define BSP_BATTERY_TIM_CHANNEL        TIM_CHANNEL_3
#endif

/* --- Battery config (1-cell lipo) ----------------------------------------- */

/* Alert when voltage drops below 3.0V — lipo cutoff */
#ifndef BSP_BATTERY_VALRT_MIN_MV
  #define BSP_BATTERY_VALRT_MIN_MV       3000
#endif

/* Alert when voltage exceeds 4.2V — lipo full charge */
#ifndef BSP_BATTERY_VALRT_MAX_MV
  #define BSP_BATTERY_VALRT_MAX_MV       4200
#endif

/*
 * Reset threshold: below = battery removed, above = new battery inserted
 * Set to 2400mV — safely below lipo cutoff (3.0V) so normal discharge
 * does not trigger a false reset
 */
#ifndef BSP_BATTERY_VRESET_MV
  #define BSP_BATTERY_VRESET_MV          2400
#endif

/* SOC threshold to fire empty alert */
#ifndef BSP_BATTERY_EMPTY_ALERT
  #define BSP_BATTERY_EMPTY_ALERT        MAX17048_EMPTY_ALERT_10PCT
#endif

/* Public enumerate/structure ----------------------------------------------- */

/* Re-use driver error codes directly */
typedef max17048_err_t  bsp_battery_err_t;

#define BSP_BATTERY_OK          MAX17048_OK
#define BSP_BATTERY_ERR         MAX17048_ERR
#define BSP_BATTERY_ERR_BUS     MAX17048_ERR_BUS
#define BSP_BATTERY_ERR_PARAM   MAX17048_ERR_PARAM
#define BSP_BATTERY_ERR_NO_DEV  MAX17048_ERR_NO_DEV

/* Re-use driver data struct directly */
typedef max17048_data_t  bsp_battery_data_t;

/* Public function prototypes ----------------------------------------------- */

/**
 * @brief  Initialize fuel gauge: bind HAL functions, apply lipo config, start PWM
 * @return BSP_BATTERY_OK on success
 */
bsp_battery_err_t bsp_battery_init(void);

/**
 * @brief  Read all fuel gauge data in one call
 * @param  data  Output: voltage, SOC, CRATE, status
 * @return BSP_BATTERY_OK on success
 */
bsp_battery_err_t bsp_battery_read(bsp_battery_data_t *data);

/**
 * @brief  Update LED PWM brightness based on current SOC
 * @return BSP_BATTERY_OK on success
 */
bsp_battery_err_t bsp_battery_update_led(void);

/**
 * @brief  Update temperature compensation — call once after init
 * @param  temp_degc  Battery temperature in degrees Celsius
 * @return BSP_BATTERY_OK on success
 */
bsp_battery_err_t bsp_battery_update_temp(int8_t temp_degc);

/**
 * @brief  Clear ALRT pin after servicing an alert
 * @return BSP_BATTERY_OK on success
 */
bsp_battery_err_t bsp_battery_clear_alert(void);

/**
 * @brief  Check if IC is present on I2C bus
 * @return true if device responds
 */
bool bsp_battery_is_present(void);

#endif /* __BSP_BATTERY_H */

/* End of file -------------------------------------------------------------- */