/**
 * @file       bsp_util.h
 * @version    2.0.0
 * @date       2025-3-14
 * @author     Phuong Mai
 * @brief      BSP Utilities: CRC, RTC, Delay (STM32F4)
 */
#ifndef BSP_UTIL_H
#define BSP_UTIL_H
#undef BSP_TICK_SOURCE_RTC /* uncomment to use RTC subseconds */

#include <stdint.h>
#include <stdbool.h>
#include "stm32f4xx_hal.h"
#ifdef BSP_TICK_SOURCE_RTC
#include "stm32f4xx_ll_rtc.h"
#endif

/* ===================================================================== */
/*                              TYPES                                    */
/* ===================================================================== */

typedef enum {
  BSP_UTIL_OK  = 0,
  BSP_UTIL_ERR = 1,
} bsp_util_status_t;

typedef struct {
  uint8_t year;    /*!< offset from 2000, e.g. 26 = 2026 */
  uint8_t month;   /*!< 1–12  */
  uint8_t day;     /*!< 1–31  */
  uint8_t hour;    /*!< 0–23  */
  uint8_t minute;  /*!< 0–59  */
  uint8_t second;  /*!< 0–59  */
} bsp_rtc_time_t;

/* ===================================================================== */
/*                         FUNCTION DECLARATIONS                         */
/* ===================================================================== */

/* Init --------------------------------------------------------------- */
/**
 * @brief  Prepare utility helpers.
 * @note   Does not own or configure a hardware timer. TIM9 remains the HAL
 *         timebase; app delays use DWT for spin waits and RTOS sleep when safe.
 */
bsp_util_status_t bsp_util_init(void);

/* CRC ---------------------------------------------------------------- */
uint32_t          bsp_crc32(const void *data, uint32_t len);
void              bsp_crc_reset(void);

/* RTC  --------------------------------------------------------------- */
bsp_util_status_t bsp_rtc_set_time(const bsp_rtc_time_t *time);
bsp_util_status_t bsp_rtc_get_time(bsp_rtc_time_t *time);

/**
 * @brief  Read SSR via LL and return UTC milliseconds since Unix epoch.
 *         This is the primary timestamp function — use this instead of
 *         HAL_GetTick() wherever ms-accurate UTC time is needed.
 */
uint64_t          bsp_rtc_get_timestamp_ms(void);
uint32_t          bsp_rtc_get_timestamp_s(void);

/**
 * @brief  Handle time_sync_set_t: program RTC + store timezone.
 * @param  unix_time_ms      proto field unix_time_ms   (UTC ms)
 * @param  timezone_offset_s proto field timezone_offset (s east of UTC)
 */
bsp_util_status_t bsp_rtc_sync_set(uint64_t unix_time_ms, int32_t timezone_offset_s);

/**
 * @brief  Handle time_sync_get_t: fill time_sync_resp_t fields.
 * @param  unix_time_ms      [out] UTC ms
 * @param  timezone_offset_s [out] s east of UTC
 */
bsp_util_status_t bsp_rtc_sync_get(uint64_t *unix_time_ms, int32_t *timezone_offset_s);

bool              bsp_rtc_is_synced(void);
void              bsp_rtc_mark_unsynced(void);
int32_t           bsp_rtc_timezone_get(void);
void              bsp_rtc_timezone_restore(int32_t offset_s);  /*!< call at boot with flash value */
static inline uint32_t bsp_util_get_ticks(void)
{
#ifdef BSP_TICK_SOURCE_RTC
  uint32_t prediv_s = LL_RTC_GetSynchPrescaler(hrtc.Instance);
  uint32_t ssr      = LL_RTC_TIME_GetSubSecond(hrtc.Instance);
  uint32_t time_reg = LL_RTC_TIME_Get(hrtc.Instance);

  uint32_t subsec_ms = ((prediv_s - ssr) * 1000U) / (prediv_s + 1U);
  uint32_t h         = __LL_RTC_CONVERT_BCD2BIN(__LL_RTC_GET_HOUR(time_reg));
  uint32_t min       = __LL_RTC_CONVERT_BCD2BIN(__LL_RTC_GET_MINUTE(time_reg));
  uint32_t sec       = __LL_RTC_CONVERT_BCD2BIN(__LL_RTC_GET_SECOND(time_reg));

  return (h * 3600UL + min * 60UL + sec) * 1000UL + subsec_ms;
#else
  return HAL_GetTick();
#endif
}
/* Delay -------------------------------------------------------------- */
void              bsp_delay_us(uint32_t us);
void              bsp_delay_ms(uint32_t ms);

/* System ------------------------------------------------------------- */
uint32_t          bsp_util_get_serial_number(void);
bsp_util_status_t bsp_util_device_reset(void);
bsp_util_status_t bsp_util_enter_bootloader(void);
uint32_t          getRunTimeCounterValue(void);
void              bsp_util_print_cpu_stats(void);
void              bsp_util_print_mem_stats(void);

#endif /* BSP_UTIL_H */
