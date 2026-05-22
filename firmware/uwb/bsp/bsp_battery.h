/**
 * @file       bsp_battery.h
 * @brief      Battery BSP for MAX17048 fuel gauge (Data Provider)
 */

#ifndef BSP_BATTERY_H
#define BSP_BATTERY_H

#include "main.h"
#include <stdbool.h>
#include <stdint.h>

/* MAX17048 Hardware Alert Flags */
#define BAT_HW_ALRT_RESET      (1 << 0)  /* Battery was swapped or IC reset */
#define BAT_HW_ALRT_SOC_LOW    (1 << 1)  /* Hardware SOC alert (HD) */
#define BAT_HW_ALRT_VOLT_LOW   (1 << 2)  /* Hardware Voltage low (VL) */
#define BAT_HW_ALRT_VOLT_HIGH  (1 << 3)  /* Hardware Voltage high (VH) */

typedef enum
{
  BSP_BATTERY_OK         =  0,
  BSP_BATTERY_ERR        = -1,
} bsp_battery_err_t;

/* Public API --------------------------------------------------------------- */

bsp_battery_err_t bsp_battery_init(void);
bsp_battery_err_t bsp_battery_task(void);

uint16_t bsp_battery_get_voltage(void);
uint8_t  bsp_battery_get_soc(void);
int16_t  bsp_battery_get_crate(void);
uint16_t bsp_battery_get_hw_alerts(void);

/**
 * @brief  Estimate remaining time based on current charge rate.
 * @return Remaining time in minutes. Positive = discharging, negative = charging.
 *         Returns INT32_MIN if indeterminate.
 */
int32_t bsp_battery_get_remaining_time(void);

/**
 * @brief  Set hardware voltage alert thresholds on the IC.
 * @param  min_mv  Minimum voltage in mV.
 * @param  max_mv  Maximum voltage in mV.
 */
void bsp_battery_set_thresholds(uint16_t min_mv, uint16_t max_mv);

bool bsp_battery_is_present(void);

/**
 * @brief  Check if battery fuel gauge was successfully initialized.
 * @retval true if initialized, false otherwise
 */
bool bsp_battery_is_initialized(void);

#endif /* BSP_BATTERY_H */