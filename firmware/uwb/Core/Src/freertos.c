/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * File Name          : freertos.c
  * Description        : Code for freertos applications
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */

/* Includes ------------------------------------------------------------------*/
#include "FreeRTOS.h"
#include "task.h"
#include "main.h"
#include "cmsis_os.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include <stdio.h>
#include "app_rtos_handles.h"
#include "app_anchor.h"
#include "app_tag.h"
#include "bsp_battery.h"
#include "bsp_io.h"
#include "bsp_imu.h"
#include "bsp_uwb.h"
#include "bsp_util.h"
#include "network/network_core.h"
#include "network/network_cmd.h"
#include "sys_config.h"
#include "sys_logger.h"
#include "sys_pm.h"
#include "positioning_config.h"
#if ENABLE_SYS_FUSION
#include "sys_sensor_fusion.h"
#endif
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */

/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/
/* USER CODE BEGIN Variables */

osMessageQueueId_t g_uwb_distance_queue;

bool g_ranging_enabled = true;
bool g_pm_ranging_blocked = false;

/* Network objects — non-static so main.c can init via extern */
network_core_t g_network_core;
uint8_t        g_network_rx_buf[512];

#if ENABLE_SYS_FUSION
static uint32_t s_fusion_last_tick = 0U;
static bool     s_fusion_first_run = true;
#endif

/* USER CODE END Variables */
/* Definitions for UwbRanging */
osThreadId_t UwbRangingHandle;
const osThreadAttr_t UwbRanging_attributes = {
  .name = "UwbRanging",
  .stack_size = 512 * 4,
  .priority = (osPriority_t) osPriorityRealtime,
};
/* Definitions for SensorFusion */
osThreadId_t SensorFusionHandle;
const osThreadAttr_t SensorFusion_attributes = {
  .name = "SensorFusion",
  .stack_size = 1024 * 4,
  .priority = (osPriority_t) osPriorityHigh,
};
/* Definitions for Network */
osThreadId_t NetworkHandle;
const osThreadAttr_t Network_attributes = {
  .name = "Network",
  .stack_size = 512 * 4,
  .priority = (osPriority_t) osPriorityNormal,
};
/* Definitions for Logger */
osThreadId_t LoggerHandle;
const osThreadAttr_t Logger_attributes = {
  .name = "Logger",
  .stack_size = 512 * 4,
  .priority = (osPriority_t) osPriorityBelowNormal,
};
/* Definitions for FlashStorage */
osThreadId_t FlashStorageHandle;
const osThreadAttr_t FlashStorage_attributes = {
  .name = "FlashStorage",
  .stack_size = 256 * 4,
  .priority = (osPriority_t) osPriorityLow,
};
/* Definitions for IO */
osThreadId_t IOHandle;
const osThreadAttr_t IO_attributes = {
  .name = "IO",
  .stack_size = 256 * 4,
  .priority = (osPriority_t) osPriorityLow,
};
/* Definitions for PM */
osThreadId_t PMHandle;
const osThreadAttr_t PM_attributes = {
  .name = "PM",
  .stack_size = 256 * 4,
  .priority = (osPriority_t) osPriorityLow,
};
/* Definitions for g_spi1_mutex */
osMutexId_t g_spi1_mutexHandle;
const osMutexAttr_t g_spi1_mutex_attributes = {
  .name = "g_spi1_mutex"
};
/* Definitions for g_logger_mutex */
osMutexId_t g_logger_mutexHandle;
const osMutexAttr_t g_logger_mutex_attributes = {
  .name = "g_logger_mutex"
};
/* Definitions for g_uwb_isr_sem */
osSemaphoreId_t g_uwb_isr_semHandle;
const osSemaphoreAttr_t g_uwb_isr_sem_attributes = {
  .name = "g_uwb_isr_sem"
};
/* Definitions for g_logger_sem */
osSemaphoreId_t g_logger_semHandle;
const osSemaphoreAttr_t g_logger_sem_attributes = {
  .name = "g_logger_sem"
};
/* Definitions for g_io_btn_sem */
osSemaphoreId_t g_io_btn_semHandle;
const osSemaphoreAttr_t g_io_btn_sem_attributes = {
  .name = "g_io_btn_sem"
};

/* Private function prototypes -----------------------------------------------*/
/* USER CODE BEGIN FunctionPrototypes */

/* USER CODE END FunctionPrototypes */

void uwb_ranging_entry(void *argument);
void sensor_fusion_entry(void *argument);
void network_entry(void *argument);
void logger_entry(void *argument);
void flash_storage_entry(void *argument);
void io_entry(void *argument);
void power_manage_entry(void *argument);

extern void MX_USB_DEVICE_Init(void);
void MX_FREERTOS_Init(void); /* (MISRA C 2004 rule 8.1) */

/**
  * @brief  FreeRTOS initialization
  * @param  None
  * @retval None
  */
void MX_FREERTOS_Init(void) {
  /* USER CODE BEGIN Init */
  /* USER CODE END Init */
  /* Create the mutex(es) */
  /* creation of g_spi1_mutex */
  g_spi1_mutexHandle = osMutexNew(&g_spi1_mutex_attributes);

  /* creation of g_logger_mutex */
  g_logger_mutexHandle = osMutexNew(&g_logger_mutex_attributes);

  /* USER CODE BEGIN RTOS_MUTEX */
  /* add mutexes, ... */
  /* USER CODE END RTOS_MUTEX */

  /* Create the semaphores(s) */
  /* creation of g_uwb_isr_sem */
  g_uwb_isr_semHandle = osSemaphoreNew(1, 1, &g_uwb_isr_sem_attributes);

  /* creation of g_logger_sem */
  g_logger_semHandle = osSemaphoreNew(1, 1, &g_logger_sem_attributes);

  /* creation of g_io_btn_sem */
  g_io_btn_semHandle = osSemaphoreNew(1, 1, &g_io_btn_sem_attributes);

  /* USER CODE BEGIN RTOS_SEMAPHORES */
  /* Drain initial count to 0 — signal semaphores must start at 0 so tasks block */
  osSemaphoreAcquire(g_uwb_isr_semHandle, 0);  /* UWB ISR → UwbRanging  */
  osSemaphoreAcquire(g_logger_semHandle,   0);  /* logger signal           */
  osSemaphoreAcquire(g_io_btn_semHandle,   0);  /* button signal           */
  /* USER CODE END RTOS_SEMAPHORES */

  /* USER CODE BEGIN RTOS_TIMERS */
  /* start timers, add new ones, ... */
  /* USER CODE END RTOS_TIMERS */

  /* USER CODE BEGIN RTOS_QUEUES */
  g_uwb_distance_queue = osMessageQueueNew(4, sizeof(uwb_distance_msg_t), NULL);
  /* USER CODE END RTOS_QUEUES */

  /* Create the thread(s) */
  /* creation of UwbRanging */
  UwbRangingHandle = osThreadNew(uwb_ranging_entry, NULL, &UwbRanging_attributes);

  /* creation of SensorFusion */
  SensorFusionHandle = osThreadNew(sensor_fusion_entry, NULL, &SensorFusion_attributes);

  /* creation of Network */
  NetworkHandle = osThreadNew(network_entry, NULL, &Network_attributes);

  /* creation of Logger */
  LoggerHandle = osThreadNew(logger_entry, NULL, &Logger_attributes);

  /* creation of FlashStorage */
  FlashStorageHandle = osThreadNew(flash_storage_entry, NULL, &FlashStorage_attributes);

  /* creation of IO */
  IOHandle = osThreadNew(io_entry, NULL, &IO_attributes);

  /* creation of PM */
  PMHandle = osThreadNew(power_manage_entry, NULL, &PM_attributes);

  /* USER CODE BEGIN RTOS_THREADS */
  /* add threads, ... */
  /* USER CODE END RTOS_THREADS */

  /* USER CODE BEGIN RTOS_EVENTS */
  /* add events, ... */
  /* USER CODE END RTOS_EVENTS */

}

/* USER CODE BEGIN Header_uwb_ranging_entry */
/**
  * @brief  Function implementing the UwbRanging thread.
  * @param  argument: Not used
  * @retval None
  */
/* USER CODE END Header_uwb_ranging_entry */
void uwb_ranging_entry(void *argument)
{
  /* init code for USB_DEVICE */
  MX_USB_DEVICE_Init();
  /* USER CODE BEGIN uwb_ranging_entry */
  sys_config_t *cfg = sys_config_get();
  static bool was_ranging_active = false;

  for (;;)
  {
    /* Block until DW1000 ISR signals TX done or RX event.            */
    /* 10 ms timeout as fallback to keep state machine alive.         */
    osSemaphoreAcquire(g_uwb_isr_semHandle, 10);

    bool is_ranging_active = g_ranging_enabled && !g_pm_ranging_blocked;

    if (!is_ranging_active && was_ranging_active)
    {
      if (cfg->uwb.role == DEVICE_ROLE_TAG)
      {
        app_tag_reset_fusion();
      }
      was_ranging_active = false;
    }

    if (is_ranging_active)
    {
      was_ranging_active = true;
    }

    if (!is_ranging_active) continue;

    osMutexAcquire(g_spi1_mutexHandle, osWaitForever);
    bsp_uwb_dwt_isr();
    if (cfg->uwb.role == DEVICE_ROLE_TAG)
    {
      app_tag_process();
    }
    else
    {
      app_anchor_process(NULL);
    }
    osMutexRelease(g_spi1_mutexHandle);
  }
  /* USER CODE END uwb_ranging_entry */
}

/* USER CODE BEGIN Header_sensor_fusion_entry */
/**
* @brief Function implementing the SensorFusion thread.
* @param argument: Not used
* @retval None
*/
/* USER CODE END Header_sensor_fusion_entry */
void sensor_fusion_entry(void *argument)
{
  /* USER CODE BEGIN sensor_fusion_entry */
#if ENABLE_SYS_FUSION
  if (sys_config_get()->uwb.role != DEVICE_ROLE_TAG)
  {
    osThreadExit();
  }

  for (;;)
  {
    osDelay(50);

    if (!sys_sensor_fusion_check_predict_flag())
    {
      continue;
    }

    float dt = 0.01f;
    if (s_fusion_first_run)
    {
      s_fusion_last_tick = HAL_GetTick();
      s_fusion_first_run = false;
    }
    else
    {
      uint32_t now = HAL_GetTick();
      uint32_t dt_ms = now - s_fusion_last_tick;
      s_fusion_last_tick = now;
      if (dt_ms > 100U) dt_ms = 100U;
      if (dt_ms < 1U) dt_ms = 1U;
      dt = (float)dt_ms / 1000.0f;
    }

    sys_sensor_fusion_predict(&ukf_data, dt);

    {
      float tril_x = 0.0f;
      float tril_y = 0.0f;
      uint32_t err_count = 0U;
      float ukf_yaw = sys_sensor_fusion_get_ukf_yaw_deg();
      float yaw = sys_sensor_fusion_get_yaw_deg();

      app_tag_get_latest_fusion_data(&tril_x, &tril_y, &err_count);
      bsp_io_uart_send_fusion_data(ukf_data.px, ukf_data.py, ukf_yaw, tril_x, tril_y, yaw, err_count);
    }
  }
#else
  for (;;)
  {
    osDelay(osWaitForever);
  }
#endif
  /* USER CODE END sensor_fusion_entry */
}

/* USER CODE BEGIN Header_network_entry */
/**
* @brief Function implementing the Network thread.
* @param argument: Not used
* @retval None
*/
/* USER CODE END Header_network_entry */
void network_entry(void *argument)
{
  /* USER CODE BEGIN network_entry */
  for (;;)
  {
    network_core_process(&g_network_core);
    network_cmd_process();
    osDelay(5);
  }
  /* USER CODE END network_entry */
}

/* USER CODE BEGIN Header_logger_entry */
/**
* @brief Function implementing the Logger thread.
* @param argument: Not used
* @retval None
*/
/* USER CODE END Header_logger_entry */
void logger_entry(void *argument)
{
  /* USER CODE BEGIN logger_entry */
  for (;;)
  {
    osDelay(30000); /* 30 seconds */
    bsp_util_print_cpu_stats();
  }
  /* USER CODE END logger_entry */
}

/* USER CODE BEGIN Header_flash_storage_entry */
/**
* @brief Function implementing the FlashStorage thread.
* @param argument: Not used
* @retval None
*/
/* USER CODE END Header_flash_storage_entry */
void flash_storage_entry(void *argument)
{
  /* USER CODE BEGIN flash_storage_entry */
  for (;;)
  {
    osDelay(2000); /* every 2 seconds */
#if defined(HAVE_FLASH_STORAGE) && defined(ENABLE_FLASH_LOG)
    sys_logger_flash_persist();
#endif
  }
  /* USER CODE END flash_storage_entry */
}

/* USER CODE BEGIN Header_io_entry */
/**
* @brief Function implementing the IO thread.
* @param argument: Not used
* @retval None
*/
/* USER CODE END Header_io_entry */
void io_entry(void *argument)
{
  /* USER CODE BEGIN io_entry */
  for (;;)
  {
    /* Block until button activity ISR signals (100 ms timeout for LED blink) */
    osSemaphoreAcquire(g_io_btn_semHandle, 100);

    bsp_io_task(); /* LED blink timeout check */

    bsp_io_button_event_t evt = bsp_io_button_event();
    sys_config_t *cfg = sys_config_get();

    switch (evt)
    {
    case BSP_IO_EVENT_HOLD:
    {
      /* Toggle TAG/ANCHOR role and reboot */
      device_role_t new_role =
        (cfg->uwb.role == DEVICE_ROLE_TAG) ? DEVICE_ROLE_ANCHOR : DEVICE_ROLE_TAG;
      sys_config_set_role(new_role);
      sys_config_save();
      for (uint8_t i = 0; i < 3U; i++)
      {
        bsp_io_led_on();  osDelay(50);
        bsp_io_led_off(); osDelay(50);
      }
      RLOG_I(LOG_OBJECT_CODE_APPLICATION, "Role changed, rebooting...");
      osDelay(100);
      HAL_NVIC_SystemReset();
      break;
    }
    case BSP_IO_EVENT_DOUBLE_CLICK:
      g_ranging_enabled = false;
      bsp_uwb_idle();
      RLOG_I(LOG_OBJECT_CODE_APPLICATION, "Ranging stopped");
      break;
    case BSP_IO_EVENT_CLICK:
      g_ranging_enabled = true;
      RLOG_I(LOG_OBJECT_CODE_APPLICATION, "Ranging started");
      break;
    default:
      break;
    }
  }
  /* USER CODE END io_entry */
}

/* USER CODE BEGIN Header_power_manage_entry */
/**
* @brief Function implementing the PM thread.
* @param argument: Not used
* @retval None
*/
/* USER CODE END Header_power_manage_entry */
void power_manage_entry(void *argument)
{
  /* USER CODE BEGIN power_manage_entry */
  static bool s_ranging_halted_by_pm = false;

  for (;;)
  {
    osDelay(100); /* 10 Hz, aligned with develop PM cadence */
    sys_pm_task(NULL);

    if (!sys_pm_is_safe())
    {
      if (!s_ranging_halted_by_pm)
      {
        sys_pm_status_t pm_status;

        g_pm_ranging_blocked = true;
        bsp_uwb_idle();
        s_ranging_halted_by_pm = true;
        sys_pm_get_status(&pm_status);

        RLOG_E(LOG_OBJECT_CODE_APPLICATION, ERR_POS_OUT_OF_RANGE,
               "[CRITICAL] RANGING HALTED! Safety checks failed. Mask: 0x%04X (SOC: %.1f%%, VDDA: %.1f mV, VBAT: %.1f mV)",
               (unsigned int)pm_status.critical_mask,
               pm_status.soc,
               pm_status.vdda_mv,
               pm_status.bat_voltage_mv);
      }
      continue;
    }

    if (s_ranging_halted_by_pm)
    {
      s_ranging_halted_by_pm = false;
      g_pm_ranging_blocked = false;
      RLOG_I(LOG_OBJECT_CODE_APPLICATION, "[INFO] RANGING RESUMED! Safety conditions restored.");
    }
  }
  /* USER CODE END power_manage_entry */
}

/* Private application code --------------------------------------------------*/
/* USER CODE BEGIN Application */

/* USER CODE END Application */

