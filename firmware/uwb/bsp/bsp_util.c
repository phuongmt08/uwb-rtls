/**
 * @file       bsp_util.c
 * @version    1.0.0
 * @date       2025-11-18
 * @author     Phuong Mai
 * @brief      BSP Utilities implementation: CRC, RTC, Delay
 */

/* Includes ----------------------------------------------------------- */
#include "bsp_util.h"
#include "stm32f4xx_hal.h"
#include <string.h>

/* External peripherals from CubeMX */
extern CRC_HandleTypeDef hcrc;
extern RTC_HandleTypeDef hrtc;

/* Private defines ---------------------------------------------------- */
#define DAYS_IN_MONTH_NORMAL {0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31}
#define DAYS_IN_MONTH_LEAP   {0, 31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31}

/* Private variables -------------------------------------------------- */
static TIM_HandleTypeDef htim_delay;
static bool util_initialized = false;

/* Private function prototypes ---------------------------------------- */
static bool is_leap_year(uint16_t year);
static uint32_t date_to_days(uint8_t year, uint8_t month, uint8_t day);

/* ===================================================================== */
/*                         INITIALIZATION                                */
/* ===================================================================== */

bsp_util_status_t bsp_util_init(void)
{
  if (util_initialized) {
    return BSP_UTIL_OK;
  }

  /* Initialize delay timer (TIM9) */
  __HAL_RCC_TIM9_CLK_ENABLE();

  htim_delay.Instance               = TIM9;
  htim_delay.Init.Prescaler         = (SystemCoreClock / 1000000UL) - 1; /* 1 MHz */
  htim_delay.Init.CounterMode       = TIM_COUNTERMODE_UP;
  htim_delay.Init.Period            = 0xFFFF;
  htim_delay.Init.ClockDivision     = TIM_CLOCKDIVISION_DIV1;
  htim_delay.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;

  if (HAL_TIM_Base_Init(&htim_delay) != HAL_OK) {
    return BSP_UTIL_ERR;
  }

  if (HAL_TIM_Base_Start(&htim_delay) != HAL_OK) {
    return BSP_UTIL_ERR;
  }

  util_initialized = true;
  return BSP_UTIL_OK;
}

/* ===================================================================== */
/*                          CRC FUNCTIONS                                */
/* ===================================================================== */

uint32_t bsp_crc32(const void *data, uint32_t len)
{
  if (!data || len == 0) {
    return 0;
  }

  /* Reset CRC unit */
  __HAL_CRC_DR_RESET(&hcrc);

  /* Calculate CRC - need word-aligned data */
  const uint8_t  *p8     = (const uint8_t *)data;
  uint32_t        words  = len / 4;
  uint32_t        remain = len % 4;
  uint32_t        crc    = 0;

  /* Process full words */
  if (words > 0) {
    crc = HAL_CRC_Calculate(&hcrc, (uint32_t *)p8, words);
    p8 += words * 4;
  }

  /* Process remaining bytes */
  if (remain > 0) {
    uint32_t last_word = 0;
    memcpy(&last_word, p8, remain);
    crc = HAL_CRC_Accumulate(&hcrc, &last_word, 1);
  }

  return crc;
}

void bsp_crc_reset(void)
{
  __HAL_CRC_DR_RESET(&hcrc);
}

/* ===================================================================== */
/*                          RTC FUNCTIONS                                */
/* ===================================================================== */

bsp_util_status_t bsp_rtc_set_time(const bsp_rtc_time_t *time)
{
  if (!time) {
    return BSP_UTIL_ERR;
  }

  RTC_TimeTypeDef sTime = {0};
  RTC_DateTypeDef sDate = {0};

  /* Set time */
  sTime.Hours   = time->hour;
  sTime.Minutes = time->minute;
  sTime.Seconds = time->second;
  sTime.DayLightSaving = RTC_DAYLIGHTSAVING_NONE;
  sTime.StoreOperation = RTC_STOREOPERATION_RESET;

  if (HAL_RTC_SetTime(&hrtc, &sTime, RTC_FORMAT_BIN) != HAL_OK) {
    return BSP_UTIL_ERR;
  }

  /* Set date */
  sDate.Year  = time->year;
  sDate.Month = time->month;
  sDate.Date  = time->day;
  sDate.WeekDay = RTC_WEEKDAY_MONDAY; /* Not used */

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

uint32_t bsp_rtc_get_timestamp(void)
{
  bsp_rtc_time_t time;
  
  if (bsp_rtc_get_time(&time) != BSP_UTIL_OK) {
    return 0;
  }

  /* Calculate days since 2000-01-01 */
  uint32_t days = date_to_days(time.year, time.month, time.day);

  /* Convert to seconds since 2000-01-01 */
  uint32_t seconds_since_2000 = days * 86400UL + time.hour * 3600UL + time.minute * 60UL + time.second;

  /* Convert to Unix epoch time (seconds since 1970-01-01)
   * Days from 1970-01-01 to 2000-01-01 = 10957 days
   * Seconds = 10957 * 86400 = 946684800 */
  uint32_t epoch_timestamp = seconds_since_2000 + 946684800UL;

  return epoch_timestamp;
}

/* ===================================================================== */
/*                         DELAY FUNCTIONS                               */
/* ===================================================================== */

void bsp_delay_us(uint32_t us)
{
  if (!util_initialized) {
    /* Fallback to HAL delay if not initialized */
    HAL_Delay((us + 999) / 1000);
    return;
  }

  uint16_t start = __HAL_TIM_GET_COUNTER(&htim_delay);
  while ((uint16_t)(__HAL_TIM_GET_COUNTER(&htim_delay) - start) < us) {
    __NOP();
  }
}

void bsp_delay_ms(uint32_t ms)
{
  while (ms--) {
    bsp_delay_us(1000);
  }
}

/* ===================================================================== */
/*                        PRIVATE FUNCTIONS                              */
/* ===================================================================== */

static bool is_leap_year(uint16_t year)
{
  return ((year % 4 == 0) && (year % 100 != 0)) || (year % 400 == 0);
}

static uint32_t date_to_days(uint8_t year, uint8_t month, uint8_t day)
{
  const uint8_t days_normal[] = DAYS_IN_MONTH_NORMAL;
  const uint8_t days_leap[]   = DAYS_IN_MONTH_LEAP;

  uint32_t total_days = 0;
  uint16_t full_year  = 2000 + year;

  /* Add days for complete years since 2000 */
  for (uint16_t y = 2000; y < full_year; y++) {
    total_days += is_leap_year(y) ? 366 : 365;
  }

  /* Add days for complete months in current year */
  const uint8_t *days_in_month = is_leap_year(full_year) ? days_leap : days_normal;
  for (uint8_t m = 1; m < month; m++) {
    total_days += days_in_month[m];
  }

  /* Add remaining days */
  total_days += day - 1; /* Day 1 = 0 days elapsed */

  return total_days;
}

/* End of file -------------------------------------------------------- */
