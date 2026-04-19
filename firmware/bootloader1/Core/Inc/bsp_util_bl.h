/**
 * @file    bsp_util_bl.h
 * @brief   Minimal BSP utility stubs for the Bootloader.
 * @author  Phuong Mai
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

typedef struct {
    uint8_t year;    /* offset from 2000 */
    uint8_t month;   /* 1-12 */
    uint8_t day;     /* 1-31 */
    uint8_t hour;    /* 0-23 */
    uint8_t minute;  /* 0-59 */
    uint8_t second;  /* 0-59 */
} bsp_rtc_time_t;

/* Inline implementation for performance */
static inline uint32_t bsp_util_get_ticks(void)
{
    return HAL_GetTick();
}

uint32_t          bsp_util_get_serial_number(void);
bsp_util_status_t bsp_util_device_reset(void);
bsp_util_status_t bsp_util_enter_bootloader(void);

bsp_util_status_t bsp_rtc_set_time(const bsp_rtc_time_t *time);
bsp_util_status_t bsp_rtc_get_time(bsp_rtc_time_t *time);
uint64_t          bsp_rtc_get_timestamp_ms(void);
uint32_t          bsp_rtc_get_timestamp_s(void);
bsp_util_status_t bsp_rtc_sync_set(uint64_t unix_time_ms, int32_t timezone_offset_s);
bsp_util_status_t bsp_rtc_sync_get(uint64_t *unix_time_ms, int32_t *timezone_offset_s);
bool              bsp_rtc_is_synced(void);
void              bsp_rtc_mark_unsynced(void);
int32_t           bsp_rtc_timezone_get(void);
void              bsp_rtc_timezone_restore(int32_t offset_s);

#endif /* BSP_UTIL_BL_H */
