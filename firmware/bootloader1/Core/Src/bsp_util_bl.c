/**
 * @file    bsp_util_bl.c
 * @author  Phuong Mai
 * @brief
 */

#include "bsp_util_bl.h"
#include "bootloader.h"
#include "rtc.h"
#include "stm32f4xx_hal.h"
#include "stm32f4xx_ll_rtc.h"

/* Private defines */
#define DAYS_IN_MONTH_NORMAL { 0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31 }
#define DAYS_IN_MONTH_LEAP   { 0, 31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31 }
#define EPOCH_2000_OFFSET_S  (946684800UL)

/* RTC sync state */
static bool    rtc_time_synced       = false;
static int32_t rtc_timezone_offset_s = 0;

/* Private prototypes */
static bool     is_leap_year(uint16_t year);
static uint32_t date_to_days(uint8_t year, uint8_t month, uint8_t day);
static void     epoch_s_to_rtc_time(uint32_t epoch_s, bsp_rtc_time_t *t);

bsp_util_status_t bsp_util_device_reset(void)
{
    NVIC_SystemReset();
    /* Never reached */
    return BSP_UTIL_OK;
}

bsp_util_status_t bsp_util_enter_bootloader(void)
{
    *(volatile uint32_t *)BL_MAGIC_ADDR = BL_MAGIC_VALUE;
    __DSB();
    __ISB();
    NVIC_SystemReset();
    return BSP_UTIL_OK;
}

uint32_t bsp_util_get_serial_number(void)
{
  return HAL_GetUIDw0() ^ HAL_GetUIDw1() ^ HAL_GetUIDw2();
}

bsp_util_status_t bsp_rtc_set_time(const bsp_rtc_time_t *time)
{
    if (!time) {
        return BSP_UTIL_ERR;
    }

    RTC_TimeTypeDef sTime = {0};
    RTC_DateTypeDef sDate = {0};

    sTime.Hours          = time->hour;
    sTime.Minutes        = time->minute;
    sTime.Seconds        = time->second;
    sTime.DayLightSaving = RTC_DAYLIGHTSAVING_NONE;
    sTime.StoreOperation = RTC_STOREOPERATION_RESET;
    if (HAL_RTC_SetTime(&hrtc, &sTime, RTC_FORMAT_BIN) != HAL_OK) {
        return BSP_UTIL_ERR;
    }

    sDate.Year    = time->year;
    sDate.Month   = time->month;
    sDate.Date    = time->day;
    sDate.WeekDay = RTC_WEEKDAY_MONDAY;
    if (HAL_RTC_SetDate(&hrtc, &sDate, RTC_FORMAT_BIN) != HAL_OK) {
        return BSP_UTIL_ERR;
    }

    return BSP_UTIL_OK;
}

bsp_util_status_t bsp_rtc_get_time(bsp_rtc_time_t *time)
{
    if (!time) {
        return BSP_UTIL_ERR;
    }

    RTC_TimeTypeDef sTime = {0};
    RTC_DateTypeDef sDate = {0};

    if (HAL_RTC_GetTime(&hrtc, &sTime, RTC_FORMAT_BIN) != HAL_OK) {
        return BSP_UTIL_ERR;
    }
    if (HAL_RTC_GetDate(&hrtc, &sDate, RTC_FORMAT_BIN) != HAL_OK) {
        return BSP_UTIL_ERR;
    }

    time->year   = sDate.Year;
    time->month  = sDate.Month;
    time->day    = sDate.Date;
    time->hour   = sTime.Hours;
    time->minute = sTime.Minutes;
    time->second = sTime.Seconds;

    return BSP_UTIL_OK;
}

uint64_t bsp_rtc_get_timestamp_ms(void)
{
    uint32_t time_reg  = LL_RTC_TIME_Get(hrtc.Instance);
    uint32_t date_reg  = LL_RTC_DATE_Get(hrtc.Instance);
    uint32_t prediv_s  = LL_RTC_GetSynchPrescaler(hrtc.Instance);
    uint32_t ssr       = LL_RTC_TIME_GetSubSecond(hrtc.Instance);
    uint32_t subsec_ms = ((prediv_s - ssr) * 1000U) / (prediv_s + 1U);

    uint8_t h   = __LL_RTC_CONVERT_BCD2BIN(__LL_RTC_GET_HOUR(time_reg));
    uint8_t min = __LL_RTC_CONVERT_BCD2BIN(__LL_RTC_GET_MINUTE(time_reg));
    uint8_t s   = __LL_RTC_CONVERT_BCD2BIN(__LL_RTC_GET_SECOND(time_reg));
    uint8_t day = __LL_RTC_CONVERT_BCD2BIN(__LL_RTC_GET_DAY(date_reg));
    uint8_t mon = __LL_RTC_CONVERT_BCD2BIN(__LL_RTC_GET_MONTH(date_reg));
    uint8_t yr  = __LL_RTC_CONVERT_BCD2BIN(__LL_RTC_GET_YEAR(date_reg));

    uint32_t days_since_2k = date_to_days(yr, mon, day);
    uint32_t local_epoch_s =
        days_since_2k * 86400UL + (uint32_t)h * 3600UL + (uint32_t)min * 60UL + s + EPOCH_2000_OFFSET_S;

    int64_t utc_epoch_s = (int64_t)local_epoch_s - (int64_t)rtc_timezone_offset_s;

    return (uint64_t)(utc_epoch_s * 1000LL + (int64_t)subsec_ms);
}

uint32_t bsp_rtc_get_timestamp_s(void)
{
    uint32_t time_reg = LL_RTC_TIME_Get(hrtc.Instance);
    uint32_t date_reg = LL_RTC_DATE_Get(hrtc.Instance);

    uint8_t h   = __LL_RTC_CONVERT_BCD2BIN(__LL_RTC_GET_HOUR(time_reg));
    uint8_t min = __LL_RTC_CONVERT_BCD2BIN(__LL_RTC_GET_MINUTE(time_reg));
    uint8_t s   = __LL_RTC_CONVERT_BCD2BIN(__LL_RTC_GET_SECOND(time_reg));
    uint8_t day = __LL_RTC_CONVERT_BCD2BIN(__LL_RTC_GET_DAY(date_reg));
    uint8_t mon = __LL_RTC_CONVERT_BCD2BIN(__LL_RTC_GET_MONTH(date_reg));
    uint8_t yr  = __LL_RTC_CONVERT_BCD2BIN(__LL_RTC_GET_YEAR(date_reg));

    uint32_t days_since_2k = date_to_days(yr, mon, day);
    uint32_t local_epoch_s =
        days_since_2k * 86400UL + (uint32_t)h * 3600UL + (uint32_t)min * 60UL + s + EPOCH_2000_OFFSET_S;

    int64_t utc_epoch_s = (int64_t)local_epoch_s - (int64_t)rtc_timezone_offset_s;
    return (uint32_t)utc_epoch_s;
}

bsp_util_status_t bsp_rtc_sync_set(uint64_t unix_time_ms, int32_t timezone_offset_s)
{
    uint32_t utc_s   = (uint32_t)(unix_time_ms / 1000ULL);
    int64_t  local_s = (int64_t)utc_s + (int64_t)timezone_offset_s;
    if (local_s < 0) {
        return BSP_UTIL_ERR;
    }

    bsp_rtc_time_t t;
    epoch_s_to_rtc_time((uint32_t)local_s, &t);
    if (bsp_rtc_set_time(&t) != BSP_UTIL_OK) {
        return BSP_UTIL_ERR;
    }

    rtc_timezone_offset_s = timezone_offset_s;
    rtc_time_synced       = true;
    return BSP_UTIL_OK;
}

bsp_util_status_t bsp_rtc_sync_get(uint64_t *unix_time_ms, int32_t *timezone_offset_s)
{
    if (!unix_time_ms || !timezone_offset_s) {
        return BSP_UTIL_ERR;
    }
    *unix_time_ms      = bsp_rtc_get_timestamp_ms();
    *timezone_offset_s = rtc_timezone_offset_s;
    return BSP_UTIL_OK;
}

bool bsp_rtc_is_synced(void)
{
    return rtc_time_synced;
}

void bsp_rtc_mark_unsynced(void)
{
    rtc_time_synced = false;
}

int32_t bsp_rtc_timezone_get(void)
{
    return rtc_timezone_offset_s;
}

void bsp_rtc_timezone_restore(int32_t offset_s)
{
    rtc_timezone_offset_s = offset_s;
}

static bool is_leap_year(uint16_t year)
{
    return ((year % 4u == 0u) && (year % 100u != 0u)) || (year % 400u == 0u);
}

static uint32_t date_to_days(uint8_t year, uint8_t month, uint8_t day)
{
    const uint8_t days_normal[] = DAYS_IN_MONTH_NORMAL;
    const uint8_t days_leap[]   = DAYS_IN_MONTH_LEAP;

    uint32_t total_days = 0u;
    uint16_t full_year  = (uint16_t)(2000u + year);

    for (uint16_t y = 2000u; y < full_year; y++) {
        total_days += is_leap_year(y) ? 366u : 365u;
    }

    const uint8_t *dim = is_leap_year(full_year) ? days_leap : days_normal;
    for (uint8_t mo = 1u; mo < month; mo++) {
        total_days += dim[mo];
    }

    total_days += (uint32_t)(day - 1u);
    return total_days;
}

static void epoch_s_to_rtc_time(uint32_t epoch_s, bsp_rtc_time_t *t)
{
    const uint8_t days_normal[] = DAYS_IN_MONTH_NORMAL;
    const uint8_t days_leap[]   = DAYS_IN_MONTH_LEAP;

    t->second = (uint8_t)(epoch_s % 60u);
    epoch_s /= 60u;
    t->minute = (uint8_t)(epoch_s % 60u);
    epoch_s /= 60u;
    t->hour = (uint8_t)(epoch_s % 24u);
    epoch_s /= 24u;

    uint16_t year = 1970u;
    for (;;) {
        uint32_t days_in_year = is_leap_year(year) ? 366u : 365u;
        if (epoch_s < days_in_year) {
            break;
        }
        epoch_s -= days_in_year;
        year++;
    }

    const uint8_t *dim = is_leap_year(year) ? days_leap : days_normal;
    uint8_t month = 1u;
    while (month <= 12u && epoch_s >= dim[month]) {
        epoch_s -= dim[month];
        month++;
    }

    t->year  = (uint8_t)(year - 2000u);
    t->month = month;
    t->day   = (uint8_t)(epoch_s + 1u);
}
