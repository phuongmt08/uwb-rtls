/**
 * @file    bsp_util_bl.h
 * @brief   Minimal BSP utility stubs for the Bootloader.
 */

#ifndef BSP_UTIL_BL_H
#define BSP_UTIL_BL_H

#include <stdint.h>
#include <stdbool.h>
#include "stm32f4xx_hal.h"

typedef enum {
    BSP_UTIL_OK  = 0,
    BSP_UTIL_ERR = 1,
} bsp_util_status_t;

/* Inline implementation for performance */
static inline uint32_t bsp_util_get_ticks(void)
{
    return HAL_GetTick();
}

uint32_t          bsp_util_get_serial_number(void);
bsp_util_status_t bsp_util_device_reset(void);
bsp_util_status_t bsp_util_enter_bootloader(void);

#endif /* BSP_UTIL_BL_H */
