/**
 * @file       bsp_util.c
 * @version    2.0.0
 * @date       2025-3-14
 * @author     Phuong Mai
 * @brief      BSP Utilities: CRC, RTC, Delay (STM32F4)
 */
/* Includes ----------------------------------------------------------- */
#include "bsp_util.h"

#include "cmsis_os.h"
#include "memorylayout.h"
#ifndef BSP_TICK_SOURCE_RTC
  #include "stm32f4xx_ll_rtc.h"
#endif
#include <string.h>

/* External peripherals from CubeMX */
extern CRC_HandleTypeDef hcrc;
extern RTC_HandleTypeDef hrtc;

/* Private defines ---------------------------------------------------- */
#define DAYS_IN_MONTH_NORMAL { 0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31 }
#define DAYS_IN_MONTH_LEAP   { 0, 31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31 }
#define EPOCH_2000_OFFSET_S  (946684800UL) /* Unix epoch of 2000-01-01 */

/* Private variables -------------------------------------------------- */
static bool    rtc_time_synced       = false;
static int32_t rtc_timezone_offset_s = 0;

/* Private prototypes ------------------------------------------------- */
static bool     is_leap_year(uint16_t year);
static uint32_t date_to_days(uint8_t year, uint8_t month, uint8_t day);
static void     epoch_s_to_rtc_time(uint32_t epoch_s, bsp_rtc_time_t *t);
static bool     delay_can_yield_to_os(void);
static bool     delay_dwt_enable(void);
static uint32_t delay_cycles_per_us(void);
static void     delay_spin_us(uint32_t us);

/* ===================================================================== */
/*                         INITIALIZATION                                */
/* ===================================================================== */

bsp_util_status_t bsp_util_init(void)
{
  (void)delay_dwt_enable();
  return BSP_UTIL_OK;
}

/* CRC functions -------------------------------------------------- */

uint32_t bsp_crc32(const void *data, uint32_t len)
{
  if (!data || len == 0)
  {
    return 0;
  }

  __HAL_CRC_DR_RESET(&hcrc);

  const uint8_t *p8     = (const uint8_t *) data;
  uint32_t       words  = len / 4;
  uint32_t       remain = len % 4;
  uint32_t       crc    = 0;

  if (words > 0)
  {
    crc = HAL_CRC_Calculate(&hcrc, (uint32_t *) p8, words);
    p8 += words * 4;
  }
  if (remain > 0)
  {
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
/* RTC functions -------------------------------------------------- */
bsp_util_status_t bsp_rtc_set_time(const bsp_rtc_time_t *time)
{
  if (!time)
  {
    return BSP_UTIL_ERR;
  }

  RTC_TimeTypeDef sTime = { 0 };
  sTime.Hours           = time->hour;
  sTime.Minutes         = time->minute;
  sTime.Seconds         = time->second;
  sTime.DayLightSaving  = RTC_DAYLIGHTSAVING_NONE;
  sTime.StoreOperation  = RTC_STOREOPERATION_RESET;
  if (HAL_RTC_SetTime(&hrtc, &sTime, RTC_FORMAT_BIN) != HAL_OK)
  {
    return BSP_UTIL_ERR;
  }

  RTC_DateTypeDef sDate = { 0 };
  sDate.Year            = time->year;
  sDate.Month           = time->month;
  sDate.Date            = time->day;
  sDate.WeekDay         = RTC_WEEKDAY_MONDAY;
  if (HAL_RTC_SetDate(&hrtc, &sDate, RTC_FORMAT_BIN) != HAL_OK)
  {
    return BSP_UTIL_ERR;
  }

  return BSP_UTIL_OK;
}

bsp_util_status_t bsp_rtc_get_time(bsp_rtc_time_t *time)
{
  if (!time)
  {
    return BSP_UTIL_ERR;
  }

  RTC_TimeTypeDef sTime = { 0 };
  RTC_DateTypeDef sDate = { 0 };

  /* Always read time before date to unlock shadow registers (RM §26.4.10) */
  if (HAL_RTC_GetTime(&hrtc, &sTime, RTC_FORMAT_BIN) != HAL_OK)
  {
    return BSP_UTIL_ERR;
  }
  if (HAL_RTC_GetDate(&hrtc, &sDate, RTC_FORMAT_BIN) != HAL_OK)
  {
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
    days_since_2k * 86400UL + (uint32_t) h * 3600UL + (uint32_t) min * 60UL + s + EPOCH_2000_OFFSET_S;

  int64_t utc_epoch_s = (int64_t) local_epoch_s - (int64_t) rtc_timezone_offset_s;

  return (uint64_t) (utc_epoch_s * 1000LL + (int64_t) subsec_ms);
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
    days_since_2k * 86400UL + (uint32_t) h * 3600UL + (uint32_t) min * 60UL + s + EPOCH_2000_OFFSET_S;

  int64_t utc_epoch_s = (int64_t) local_epoch_s - (int64_t) rtc_timezone_offset_s;

  return (uint32_t) utc_epoch_s;
}

bsp_util_status_t bsp_rtc_sync_set(uint64_t unix_time_ms, int32_t timezone_offset_s)
{
  uint32_t utc_s   = (uint32_t) (unix_time_ms / 1000ULL);
  int64_t  local_s = (int64_t) utc_s + (int64_t) timezone_offset_s;
  if (local_s < 0)
  {
    return BSP_UTIL_ERR;
  }

  bsp_rtc_time_t t;
  epoch_s_to_rtc_time((uint32_t) local_s, &t);
  if (bsp_rtc_set_time(&t) != BSP_UTIL_OK)
  {
    return BSP_UTIL_ERR;
  }

  rtc_timezone_offset_s = timezone_offset_s;
  rtc_time_synced       = true;
  return BSP_UTIL_OK;
}

bsp_util_status_t bsp_rtc_sync_get(uint64_t *unix_time_ms, int32_t *timezone_offset_s)
{
  if (!unix_time_ms || !timezone_offset_s)
  {
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
/* Delay functions -------------------------------------------------- */

void bsp_delay_us(uint32_t us)
{
  delay_spin_us(us);
}

void bsp_delay_ms(uint32_t ms)
{
  if (ms == 0U)
  {
    return;
  }

  if (delay_can_yield_to_os() && (osDelay(ms) == osOK))
  {
    return;
  }

  while (ms--)
  {
    bsp_delay_us(1000);
  }
}

void HAL_Delay(uint32_t Delay)
{
  bsp_delay_ms(Delay);
}

static bool delay_can_yield_to_os(void)
{
  return (osKernelGetState() == osKernelRunning) &&
         (__get_IPSR() == 0U) &&
         (__get_PRIMASK() == 0U) &&
         (__get_BASEPRI() == 0U) &&
         (__get_FAULTMASK() == 0U);
}

static bool delay_dwt_enable(void)
{
  CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
  DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
  return ((DWT->CTRL & DWT_CTRL_CYCCNTENA_Msk) != 0U);
}

static uint32_t delay_cycles_per_us(void)
{
  uint32_t cycles_per_us = SystemCoreClock / 1000000UL;
  if (cycles_per_us == 0U)
  {
    cycles_per_us = 1U;
  }
  return cycles_per_us;
}

static void delay_spin_us(uint32_t us)
{
  if (us == 0U)
  {
    return;
  }

  uint32_t cycles_per_us = delay_cycles_per_us();

  if (!delay_dwt_enable())
  {
    while (us > 0U)
    {
      uint32_t chunk_us     = us;
      uint32_t max_chunk_us = 0xFFFFFFFFUL / cycles_per_us;
      if (chunk_us > max_chunk_us)
      {
        chunk_us = max_chunk_us;
      }

      volatile uint32_t loops = cycles_per_us * chunk_us;
      while (loops-- > 0U)
      {
        __NOP();
      }
      us -= chunk_us;
    }
    return;
  }

  while (us > 0U)
  {
    uint32_t chunk_us     = us;

    uint32_t max_chunk_us = 0xFFFFFFFFUL / cycles_per_us;
    if (chunk_us > max_chunk_us)
    {
      chunk_us = max_chunk_us;
    }

    uint32_t cycles = chunk_us * cycles_per_us;
    uint32_t start = DWT->CYCCNT;
    while ((uint32_t)(DWT->CYCCNT - start) < cycles)
    {
      __NOP();
    }
    us -= chunk_us;
  }
}
/* System functions -------------------------------------------------- */
uint32_t bsp_util_get_serial_number(void)
{
  return HAL_GetUIDw0() ^ HAL_GetUIDw1() ^ HAL_GetUIDw2();
}

bsp_util_status_t bsp_util_device_reset(void)
{
  __DSB();
  __ISB();
  NVIC_SystemReset();
  return BSP_UTIL_OK; /* Never reached */
}

bsp_util_status_t bsp_util_enter_bootloader(void)
{
  *(volatile uint32_t *) BL_MAGIC_ADDR = BL_MAGIC_VALUE;
  __DSB();
  __ISB();
  NVIC_SystemReset();
  return BSP_UTIL_OK; /* Never reached */
}

/* Private functions -------------------------------------------------- */

static bool is_leap_year(uint16_t year)
{
  return ((year % 4 == 0) && (year % 100 != 0)) || (year % 400 == 0);
}

static uint32_t date_to_days(uint8_t year, uint8_t month, uint8_t day)
{
  const uint8_t days_normal[] = DAYS_IN_MONTH_NORMAL;
  const uint8_t days_leap[]   = DAYS_IN_MONTH_LEAP;

  uint32_t total_days = 0;
  uint16_t full_year  = 2000u + year;

  for (uint16_t y = 2000; y < full_year; y++)
  {
    total_days += is_leap_year(y) ? 366u : 365u;
  }

  const uint8_t *dim = is_leap_year(full_year) ? days_leap : days_normal;
  for (uint8_t mo = 1; mo < month; mo++)
  {
    total_days += dim[mo];
  }

  total_days += day - 1u;
  return total_days;
}

static void epoch_s_to_rtc_time(uint32_t epoch_s, bsp_rtc_time_t *t)
{
  const uint8_t days_normal[] = DAYS_IN_MONTH_NORMAL;
  const uint8_t days_leap[]   = DAYS_IN_MONTH_LEAP;

  t->second = (uint8_t) (epoch_s % 60u);
  epoch_s /= 60u;
  t->minute = (uint8_t) (epoch_s % 60u);
  epoch_s /= 60u;
  t->hour = (uint8_t) (epoch_s % 24u);
  epoch_s /= 24u;

  uint16_t year = 1970u;
  for (;;)
  {
    uint32_t days_in_year = is_leap_year(year) ? 366u : 365u;
    if (epoch_s < days_in_year)
    {
      break;
    }
    epoch_s -= days_in_year;
    year++;
  }

  const uint8_t *dim   = is_leap_year(year) ? days_leap : days_normal;
  uint8_t        month = 1u;
  while (month <= 12u && epoch_s >= dim[month])
  {
    epoch_s -= dim[month];
    month++;
  }

  t->year  = (uint8_t) (year - 2000u);
  t->month = month;
  t->day   = (uint8_t) (epoch_s + 1u);
}

/* ===================================================================== */
/*                   RTOS SYSTEM HELPERS & HOOKS                         */
/* ===================================================================== */

#include "FreeRTOS.h"
#include "task.h"
#include "sys_logger.h"
#include "err.h"

/* For configGENERATE_RUN_TIME_STATS */
/* Note: Reads high-speed hardware timer TIM10 (running at 100 kHz)
   and handles 16-bit register overflows to form a 32-bit counter value. */
uint32_t getRunTimeCounterValue(void)
{
  extern TIM_HandleTypeDef htim10;
  static uint32_t s_runtime_counter_high = 0;
  static uint16_t s_last_timer_val = 0;
  
  if (htim10.Instance == NULL || (htim10.Instance->CR1 & TIM_CR1_CEN) == 0)
  {
    return HAL_GetTick(); /* Safe fallback before timer is started */
  }
  
  uint16_t now = (uint16_t)__HAL_TIM_GET_COUNTER(&htim10);
  if (now < s_last_timer_val)
  {
    s_runtime_counter_high++;
  }
  s_last_timer_val = now;
  return ((s_runtime_counter_high << 16) | now);
}

volatile char g_overflowed_task_name[16] = {0};

void vApplicationStackOverflowHook(xTaskHandle xTask, signed char *pcTaskName)
{
   if (pcTaskName != NULL)
   {
       for (int i = 0; i < 15; i++)
       {
           g_overflowed_task_name[i] = (char)pcTaskName[i];
           if (pcTaskName[i] == '\0') break;
       }
       g_overflowed_task_name[15] = '\0';
   }
   
   /* Halt system and log error */
   __disable_irq();
   while(1)
   {
   }
}

void vApplicationMallocFailedHook(void)
{
  RLOG_E(LOG_OBJECT_CODE_TASK, ERR_SYSTEM, "FreeRTOS heap exhausted");
  __disable_irq();
  while (1)
  {
  }
}

void bsp_util_print_cpu_stats(void)
{
#if (configGENERATE_RUN_TIME_STATS == 1) && (configUSE_TRACE_FACILITY == 1)
  static TaskStatus_t s_task_status[16];
  static char s_stats_buf[256];
  
  uint32_t total_runtime = 0;
  UBaseType_t task_count = uxTaskGetSystemState(s_task_status, 16, &total_runtime);

  if (task_count > 0 && total_runtime > 0)
  {
    int len = snprintf(s_stats_buf, sizeof(s_stats_buf), "CPU: ");
    for (UBaseType_t i = 0; i < task_count; i++)
    {
      uint32_t pct = (uint32_t)((uint64_t)s_task_status[i].ulRunTimeCounter * 100 / total_runtime);
      int ret = snprintf(s_stats_buf + len, sizeof(s_stats_buf) - len, "%s:%lu%% | ",
                         s_task_status[i].pcTaskName, (unsigned long)pct);
      if (ret > 0)
      {
        len += ret;
      }
    }
    if (len > 7)
    {
      s_stats_buf[len - 3] = '\0'; /* Trim the trailing " | " */
    }
    RLOG_D(LOG_OBJECT_CODE_TASK, "%s", s_stats_buf);
  }
#endif
}

/* End of file -------------------------------------------------------- */
