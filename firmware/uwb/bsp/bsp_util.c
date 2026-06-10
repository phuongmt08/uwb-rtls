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
#include <stdio.h>

#include "FreeRTOS.h"
#include "task.h"
#include "sys_logger.h"
#include "err.h"

/* External peripherals from CubeMX */
extern CRC_HandleTypeDef hcrc;
extern RTC_HandleTypeDef hrtc;

/* Extern Thread Handles and Attributes from main.c */
extern osThreadId_t UwbRangingHandle;
extern osThreadId_t SensorFusionHandle;
extern osThreadId_t NetworkHandle;
extern osThreadId_t LoggerHandle;
extern osThreadId_t FlashStorageHandle;
extern osThreadId_t IOHandle;
extern osThreadId_t PMHandle;

extern const osThreadAttr_t UwbRanging_attributes;
extern const osThreadAttr_t SensorFusion_attributes;
extern const osThreadAttr_t Network_attributes;
extern const osThreadAttr_t Logger_attributes;
extern const osThreadAttr_t FlashStorage_attributes;
extern const osThreadAttr_t IO_attributes;
extern const osThreadAttr_t PM_attributes;

/* Private defines ---------------------------------------------------- */
#define DAYS_IN_MONTH_NORMAL { 0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31 }
#define DAYS_IN_MONTH_LEAP   { 0, 31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31 }
#define EPOCH_2000_OFFSET_S  (946684800UL) /* Unix epoch of 2000-01-01 */

#define RTOS_MONITOR_CPU_WARN_PERMILLE 900U
#define RTOS_MONITOR_HEAP_WARN_BYTES   2048U
#define RTOS_MONITOR_STACK_WARN_BYTES  256U

/* Private typedefs --------------------------------------------------- */
typedef struct {
  uint32_t task_id;
  uint32_t runtime_counter;
} rtos_prev_task_t;

typedef struct {
    osThreadId_t          handle;
    const osThreadAttr_t *attr;
} task_mem_info_t;

/* Global variables --------------------------------------------------- */
volatile char g_overflowed_task_name[16] = {0};

/* Private variables -------------------------------------------------- */
static bool    rtc_time_synced       = false;
static int32_t rtc_timezone_offset_s = 0;

static bsp_util_rtos_snapshot_t s_rtos_snapshots[2];
static volatile uint32_t        s_rtos_active_snapshot;

#if (configUSE_TRACE_FACILITY == 1)
static TaskStatus_t     s_rtos_task_status[BSP_UTIL_RTOS_MONITOR_MAX_TASKS];
static rtos_prev_task_t s_rtos_prev_tasks[BSP_UTIL_RTOS_MONITOR_MAX_TASKS];
static uint32_t         s_rtos_prev_task_count;
static uint32_t         s_rtos_prev_total_runtime;
static uint32_t         s_rtos_prev_tick;
#endif

/* Private prototypes ------------------------------------------------- */
static bool     is_leap_year(uint16_t year);
static uint32_t date_to_days(uint8_t year, uint8_t month, uint8_t day);
static void     epoch_s_to_rtc_time(uint32_t epoch_s, bsp_rtc_time_t *t);
static bool     delay_can_yield_to_os(void);
static bool     delay_dwt_enable(void);
static uint32_t delay_cycles_per_us(void);
static void     delay_spin_us(uint32_t us);

#if (configUSE_TRACE_FACILITY == 1)
static uint32_t rtos_prev_runtime(uint32_t task_id);
#endif

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

#if (configUSE_TRACE_FACILITY == 1)
static uint32_t rtos_prev_runtime(uint32_t task_id)
{
  for (uint32_t i = 0U; i < s_rtos_prev_task_count; i++)
  {
    if (s_rtos_prev_tasks[i].task_id == task_id)
    {
      return s_rtos_prev_tasks[i].runtime_counter;
    }
  }
  return 0U;
}
#endif

__attribute__((optimize("Os"))) void bsp_util_rtos_monitor_update(void)
{
  uint32_t next_index = s_rtos_active_snapshot ^ 1U;
  bsp_util_rtos_snapshot_t *snapshot = &s_rtos_snapshots[next_index];
  memset(snapshot, 0, sizeof(*snapshot));

#if (configSUPPORT_DYNAMIC_ALLOCATION == 1)
  snapshot->heap_free_bytes          = (uint32_t)xPortGetFreeHeapSize();
  snapshot->heap_min_ever_free_bytes = (uint32_t)xPortGetMinimumEverFreeHeapSize();
#endif

  snapshot->task_count_total = (uint32_t)uxTaskGetNumberOfTasks();

#if (configUSE_TRACE_FACILITY == 1)
  if (snapshot->task_count_total <= BSP_UTIL_RTOS_MONITOR_MAX_TASKS)
  {
    uint32_t total_runtime = 0U;
    UBaseType_t count = uxTaskGetSystemState(s_rtos_task_status,
                                             BSP_UTIL_RTOS_MONITOR_MAX_TASKS,
                                             &total_runtime);
    uint32_t now = bsp_util_get_ticks();
    uint32_t total_delta = total_runtime - s_rtos_prev_total_runtime;
    uint32_t idle_delta = 0U;
    bool cpu_valid = (s_rtos_prev_total_runtime != 0U) && (total_delta != 0U);

    snapshot->task_count = (uint32_t)count;
    snapshot->sample_window_ms = (s_rtos_prev_tick == 0U) ? 0U : (now - s_rtos_prev_tick);
    snapshot->min_stack_free_bytes = UINT32_MAX;

    for (uint32_t i = 0U; i < (uint32_t)count; i++)
    {
      const TaskStatus_t *status = &s_rtos_task_status[i];
      bsp_util_rtos_task_stat_t *task = &snapshot->tasks[i];
      uint32_t runtime = (uint32_t)status->ulRunTimeCounter;
      uint32_t runtime_delta = runtime - rtos_prev_runtime((uint32_t)status->xTaskNumber);

      task->task_id = (uint32_t)status->xTaskNumber;
      task->stack_min_free_bytes = (uint32_t)status->usStackHighWaterMark *
                                   (uint32_t)sizeof(StackType_t);
      if (cpu_valid)
      {
        task->cpu_permille = (uint32_t)(((uint64_t)runtime_delta * 1000U) / total_delta);
      }
      strncpy(task->name, status->pcTaskName, sizeof(task->name) - 1U);

      if (task->stack_min_free_bytes < snapshot->min_stack_free_bytes)
      {
        snapshot->min_stack_free_bytes = task->stack_min_free_bytes;
        snapshot->min_stack_task_id = task->task_id;
      }
      if (strncmp(status->pcTaskName, "IDLE", 4U) == 0)
      {
        idle_delta += runtime_delta;
      }

      s_rtos_prev_tasks[i].task_id = task->task_id;
      s_rtos_prev_tasks[i].runtime_counter = runtime;
    }

    if (snapshot->min_stack_free_bytes == UINT32_MAX)
    {
      snapshot->min_stack_free_bytes = 0U;
    }
    if (cpu_valid)
    {
      uint32_t idle_permille = (uint32_t)(((uint64_t)idle_delta * 1000U) / total_delta);
      snapshot->cpu_busy_permille = (idle_permille >= 1000U) ? 0U : (1000U - idle_permille);
    }
    else
    {
      snapshot->health_flags |= BSP_UTIL_RTOS_FLAG_CPU_UNAVAILABLE;
    }

    s_rtos_prev_task_count = (uint32_t)count;
    s_rtos_prev_total_runtime = total_runtime;
    s_rtos_prev_tick = now;
  }
  else
  {
    snapshot->health_flags |= BSP_UTIL_RTOS_FLAG_TASK_TRUNCATED |
                              BSP_UTIL_RTOS_FLAG_CPU_UNAVAILABLE;
  }
#else
  snapshot->health_flags |= BSP_UTIL_RTOS_FLAG_CPU_UNAVAILABLE;
#endif

  if (snapshot->cpu_busy_permille >= RTOS_MONITOR_CPU_WARN_PERMILLE)
  {
    snapshot->health_flags |= BSP_UTIL_RTOS_FLAG_CPU_HIGH;
  }
#if (configSUPPORT_DYNAMIC_ALLOCATION == 1)
  if (snapshot->heap_free_bytes <= RTOS_MONITOR_HEAP_WARN_BYTES)
  {
    snapshot->health_flags |= BSP_UTIL_RTOS_FLAG_HEAP_LOW;
  }
#endif
  if ((snapshot->min_stack_free_bytes != 0U) &&
      (snapshot->min_stack_free_bytes <= RTOS_MONITOR_STACK_WARN_BYTES))
  {
    snapshot->health_flags |= BSP_UTIL_RTOS_FLAG_STACK_LOW;
  }

  snapshot->valid = true;
  __DMB();
  s_rtos_active_snapshot = next_index;
}

const bsp_util_rtos_snapshot_t *bsp_util_rtos_monitor_get(void)
{
  uint32_t active_index = s_rtos_active_snapshot;
  __DMB();
  return s_rtos_snapshots[active_index].valid ? &s_rtos_snapshots[active_index] : NULL;
}

/* End of file -------------------------------------------------------- */
