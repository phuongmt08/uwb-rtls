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
#include <string.h>
#include "app_rtos_handles.h"
#include "app_anchor.h"
#include "app_tag.h"
#include "bsp_battery.h"
#include "bsp_io.h"
#include "bsp_imu.h"
#include "bsp_uwb.h"
#include "bsp_util.h"
#include "common.h"
#include "network/network_core.h"
#include "network/network_cmd.h"
#include "sys_config.h"
#include "sys_logger.h"
#include "sys_pm.h"
#include "sys_ranging.h"
#include "positioning_config.h"
#include "bsp_io.h"
#include <math.h>
#if ENABLE_SYS_FUSION
#include "sys_sensor_fusion.h"
#include "mw_filter.h"
#include "mw_trilateration.h"
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

bool g_ranging_enabled = false;
bool g_pm_ranging_blocked = false;

/* Network objects — non-static so main.c can init via extern */
network_core_t g_network_core;
uint8_t        g_network_rx_buf[512];

#if ENABLE_SYS_FUSION
/* Owner variables for decoupled active Sensor Fusion */
sys_sensor_fusion_data_t ukf_data;
static mahalanobis_prefilter_t s_prefilter;

static uint8_t s_last_selected_anchors_mask = 0U;
#endif

/* USER CODE END Variables */
/* Definitions for UwbRanging */
osThreadId_t UwbRangingHandle;
const osThreadAttr_t UwbRanging_attributes = {
  .name = "UwbRanging",
  .stack_size = 1536 * 4,
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
  .stack_size = 768 * 4,
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
  .stack_size = 384 * 4,
  .priority = (osPriority_t) osPriorityLow,
};
/* Definitions for IO */
osThreadId_t IOHandle;
const osThreadAttr_t IO_attributes = {
  .name = "IO",
  .stack_size = 384 * 4,
  .priority = (osPriority_t) osPriorityLow,
};
/* Definitions for PM */
osThreadId_t PMHandle;
const osThreadAttr_t PM_attributes = {
  .name = "PM",
  .stack_size = 640 * 4,
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
#if ENABLE_SYS_FUSION
static bool convert_3d_to_2d_distance(double r3d, double dz, double *r2d_out);
static bool get_anchor_position(uint8_t aid, vec3d_t *pos_out);
#endif
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
#if UKF_BLE_STREAM_TEST_ENABLE
  if (sys_config_get()->uwb.role != DEVICE_ROLE_TAG)
  {
    osThreadExit();
  }
#endif
  /* init code for USB_DEVICE */
  MX_USB_DEVICE_Init();
  /* USER CODE BEGIN uwb_ranging_entry */
  sys_config_t *cfg = sys_config_get();
  static bool was_ranging_active = false;
  SYSVIEW_RECORD_START();
  SYSVIEW_PRINTF("SystemView started after scheduler");
  SYSVIEW_PRINTF(SYSVIEW_MARKERS_DESC);

  for (;;)
  {
    /* Calculate dynamic timeout based on UWB deadline to prevent missing FINAL TX slot */
    uint32_t wait_ms = 10;
    if (g_ranging_enabled && !g_pm_ranging_blocked)
    {
      wait_ms = sys_ranging_get_ms_to_deadline();
    }

    /* Block until DW1000 ISR signals TX done or RX event.            */
    osStatus_t status = osSemaphoreAcquire(g_uwb_isr_semHandle, wait_ms);

    bool is_ranging_active = g_ranging_enabled && !g_pm_ranging_blocked;

    if (!is_ranging_active)
    {
      if (was_ranging_active)
      {
        if (cfg->uwb.role == DEVICE_ROLE_TAG)
        {
          app_tag_reset_fusion();
        }
        was_ranging_active = false;
      }
      continue;
    }

    was_ranging_active = true;
    if (status == osErrorTimeout)
    {
      if (HAL_GPIO_ReadPin(GPIOA, GPIO_PIN_4) == GPIO_PIN_SET)
      {
        RLOG_W(LOG_OBJECT_CODE_UWB_DRIVER, "[UWB] EXTI Missed! Stuck IRQ detected (PA4=HIGH), running auto-recovery...");

        osMutexAcquire(g_spi1_mutexHandle, osWaitForever);
        SYSVIEW_START(SYSVIEW_MARK_UWB_ISR_DISPATCH);
        bsp_uwb_dwt_isr();
        if (cfg->uwb.role == DEVICE_ROLE_TAG)
        {
          app_tag_process();
        }
        else
        {
          app_anchor_process(NULL);
        }
        SYSVIEW_STOP(SYSVIEW_MARK_UWB_ISR_DISPATCH);
        osMutexRelease(g_spi1_mutexHandle);
        
        continue; 
      }
    }

    osMutexAcquire(g_spi1_mutexHandle, osWaitForever);
    SYSVIEW_START(SYSVIEW_MARK_UWB_ISR_DISPATCH);
    bsp_uwb_dwt_isr();
    if (cfg->uwb.role == DEVICE_ROLE_TAG)
    {
      app_tag_process();
    }
    else
    {
      app_anchor_process(NULL);
    }
    SYSVIEW_STOP(SYSVIEW_MARK_UWB_ISR_DISPATCH);
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

#if UKF_BLE_STREAM_TEST_ENABLE
  sys_sensor_fusion_stream_test_init(&g_network_core);
  for (;;)
  {
    sys_sensor_fusion_test_stream_result(&g_network_core, g_ranging_enabled);
    osDelay(20);
  }
#else
  mw_filter_mahalanobis_init(&s_prefilter,
                             MAHALANOBIS_PREFILTER_D2_RECOVER,
                             MAHALANOBIS_PREFILTER_D2_REJECT,
                             MAHALANOBIS_PREFILTER_R_BASE);
  /* Khởi tạo bộ lọc định vị và prefilter */
  if (sys_sensor_fusion_init(&ukf_data) != SYS_SENSOR_FUSION_OK)
  {
      RLOG_E(LOG_OBJECT_CODE_TAG, ERR_SYSTEM, "Sensor fusion initialization failed");
  }
  else
  {
      RLOG_I(LOG_OBJECT_CODE_TAG, "Sensor fusion initialized successfully");
  }
  for (;;)
  {
    osDelay(20);

    if (sys_sensor_fusion_check_predict_flag())
    {
      sys_sensor_fusion_predict(&ukf_data);
    }

    {
      uwb_distance_msg_t msg;
      if (osMessageQueueGet(g_uwb_distance_queue, &msg, NULL, 0U) == osOK)
      {
        /* 1. Calculate dynamic dt for ranging if needed, and update logs */

        /* 2. Process, project to 2D, and Mahalanobis filter the ranges */
        mw_tril_anchor_t anchors_by_id[NUM_ANCHORS + 1];
        uint8_t valid_count = 0;
        for (uint8_t i = 0; i <= NUM_ANCHORS; i++) anchors_by_id[i].valid = false;

			#if ENABLE_MAHALANOBIS_PREFILTER
        mw_tril_anchor_t prefilter_rejects[NUM_ANCHORS];
        uint8_t prefilter_reject_count = 0U;
			#endif

        for (uint8_t i = 0; i < NUM_ANCHORS; i++) {
            uint8_t aid = msg.anchor_ids[i];
            if (aid < 1 || aid > NUM_ANCHORS) continue;

            float d_raw = msg.distances[i];

            /* Check if ranging result for this anchor is valid in the mask */
            if (!(msg.mask & (1 << (aid - 1)))) {
                continue;
            }

            vec3d_t anchor_pos;
            if (!get_anchor_position(aid, &anchor_pos)) {
                continue;
            }

            float d_used = d_raw;
            float d2_score = 0.0f;
            float r_adapt = MAHALANOBIS_PREFILTER_R_BASE;

            double r2d = 0.0;
            double dz = anchor_pos.z - (double)TAG_HEIGHT_M;
            if (!convert_3d_to_2d_distance((double)d_used, dz, &r2d)) {
                continue;
            }
            d_used = (float)r2d;

            mw_tril_anchor_t anchor_entry = {0};
            anchor_entry.position = anchor_pos;
            anchor_entry.distance = (double)r2d;
            anchor_entry.id = aid;
            anchor_entry.valid = true;
            anchor_entry.r_adaptive = (double)r_adapt;
            anchor_entry.fp_amp_norm = (double)msg.fp_amp_norm[aid - 1];
            anchor_entry.fp_snr = (double)msg.fp_snr[aid - 1];
            anchor_entry.quality_valid = true;
            anchor_entry.selection_score = 0.0;
            anchor_entry.residual_rms = 0.0;
            anchor_entry.gdop_penalty = 0.0;
            anchor_entry.fp_penalty = 0.0;

#if ENABLE_MAHALANOBIS_PREFILTER
            bool pass = true;
            if (sys_sensor_fusion_is_initialized())
            {
                pass = mw_filter_mahalanobis_update(&s_prefilter,
                                                    aid - 1U,
                                                    d_used,
                                                    ukf_data.px,
                                                    ukf_data.py,
                                                    TAG_HEIGHT_M,
                                                    ukf_data.vx,
                                                    ukf_data.vy,
                                                    0.0f,
                                                    (float)anchor_pos.x,
                                                    (float)anchor_pos.y,
                                                    (float)anchor_pos.z,
                                                    &d_used,
                                                    &d2_score,
                                                    &r_adapt);
            }
            anchor_entry.d2_score = (double)d2_score;
            anchor_entry.r_adaptive = (double)r_adapt;

            if (!pass)
            {
#if (MAHALANOBIS_PREFILTER_RESCUE_MIN_ANCHORS > 0U)
                if (prefilter_reject_count < NUM_ANCHORS)
                {
                    prefilter_rejects[prefilter_reject_count++] = anchor_entry;
                }
#endif
                RLOG_W(LOG_OBJECT_CODE_TAG,
                       "[FUSION PREFILTER] Anchor #%u rejected by Mahalanobis (d2=%.2f r2d=%.3fm)",
                       aid, d2_score, d_used);
                continue;
            }
#else
            anchor_entry.d2_score = (double)d2_score;
#endif

            anchors_by_id[aid] = anchor_entry;
            valid_count++;
        }

#if (ENABLE_MAHALANOBIS_PREFILTER && (MAHALANOBIS_PREFILTER_RESCUE_MIN_ANCHORS > 0U))
        if (valid_count < MAHALANOBIS_PREFILTER_RESCUE_MIN_ANCHORS && prefilter_reject_count > 0U) {
            for (uint8_t i = 1U; i < prefilter_reject_count; i++) {
                mw_tril_anchor_t key = prefilter_rejects[i];
                int j = (int)i - 1;
                while (j >= 0 && prefilter_rejects[j].d2_score > key.d2_score) {
                    prefilter_rejects[j + 1] = prefilter_rejects[j];
                    j--;
                }
                prefilter_rejects[j + 1] = key;
            }

            uint8_t rescue_target = MAHALANOBIS_PREFILTER_RESCUE_MIN_ANCHORS;
            if (rescue_target > NUM_ANCHORS) rescue_target = NUM_ANCHORS;
            for (uint8_t i = 0U; i < prefilter_reject_count && valid_count < rescue_target; i++) {
                uint8_t aid = prefilter_rejects[i].id;
                if (aid == 0U || aid > NUM_ANCHORS || anchors_by_id[aid].valid) {
                    continue;
                }
                anchors_by_id[aid] = prefilter_rejects[i];
                valid_count++;
                RLOG_W(LOG_OBJECT_CODE_TAG,
                       "[FUSION RESCUE] Anchor #%u rescued (d2=%.2f, valid=%u/%u)",
                       aid, prefilter_rejects[i].d2_score, valid_count, rescue_target);
            }
        }
#endif

        /* anchor_distances is unused in decoupled Sensor Fusion thread */

        /* 3. Sort and Select the Best 3 anchors for UKF Update */
        if (valid_count >= 3) {
            mw_tril_anchor_t anchors_compact[NUM_ANCHORS];
            uint8_t compact_idx = 0;
            for (uint8_t id = 1; id <= NUM_ANCHORS; id++) {
                if (anchors_by_id[id].valid) {
                    anchors_compact[compact_idx++] = anchors_by_id[id];
                }
            }

            mw_tril_anchor_t best_3_anchors[3];
            uint8_t best_count = mw_trilateration_select_best(anchors_compact, compact_idx, best_3_anchors, 3, s_last_selected_anchors_mask);

            if (best_count >= 3) {
                s_last_selected_anchors_mask = 0;
                for (uint8_t i = 0; i < 3; i++)
                {
                    s_last_selected_anchors_mask |= (1 << (best_3_anchors[i].id - 1));
                }

                vec2d_t tril_position = {0.0f, 0.0f};
                SYSVIEW_START(SYSVIEW_MARK_FUSION_TRILATERATION);
                mw_tril_result_t tril_result = {0};
                mw_tril_err_t err = mw_trilateration_2d(best_3_anchors, &tril_position, &tril_result);
                SYSVIEW_STOP(SYSVIEW_MARK_FUSION_TRILATERATION);
                if (err == MW_TRIL_OK)
                {
                    SYSVIEW_START(SYSVIEW_MARK_FUSION_UKF_UPDATE);
                    (void)sys_sensor_fusion_apply_trilateration_result(&ukf_data,
                                                                       &tril_position,
                                                                       best_3_anchors,
                                                                       anchors_by_id,
                                                                       anchors_compact,
                                                                       compact_idx,
                                                                       s_last_selected_anchors_mask);
                    SYSVIEW_STOP(SYSVIEW_MARK_FUSION_UKF_UPDATE);
                }
                else
                {
                    sys_sensor_fusion_report_error();
                }
            }
            else
            {
                sys_sensor_fusion_report_error();
            }
        }
        else
        {
            sys_sensor_fusion_report_error();
        }

        /* Update logging metrics mailbox passively when in Sensor Fusion mode */
      }
    }
  }
  osThreadExit();
#endif
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
    osDelay(2);
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
  bsp_util_rtos_monitor_update();
  for (;;)
  {
    osDelay(30000); /* 30 seconds */
    bsp_util_rtos_monitor_update();
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

    bool allow_uwb_telemetry = !sys_ranging_is_active();
    bool telemetry_lock_held = false;
    if (allow_uwb_telemetry) {
      if (osMutexAcquire(g_spi1_mutexHandle, 0U) == osOK) {
        telemetry_lock_held = true;
      } else {
        allow_uwb_telemetry = false;
      }
    }

    sys_pm_task(&allow_uwb_telemetry);

    if (telemetry_lock_held) {
      osMutexRelease(g_spi1_mutexHandle);
    }

    /* Ranging halt logic completely bypassed as requested. 
     * We only log warnings but NEVER call bsp_uwb_idle() or set g_pm_ranging_blocked. */
    if (!sys_pm_is_safe())
    {
      if (!s_ranging_halted_by_pm)
      {
        sys_pm_status_t pm_status;
        s_ranging_halted_by_pm = true;
        sys_pm_get_status(&pm_status);

        RLOG_W(LOG_OBJECT_CODE_APPLICATION,
               "[PM WARNING] Safety checks failed! Mask: 0x%04X (SOC: %.1f%%, VDDA: %.1f mV, VBAT: %.1f mV) - Ranging forced to run.",
               (unsigned int)pm_status.critical_mask,
               pm_status.soc,
               pm_status.vdda_mv,
               pm_status.bat_voltage_mv);
      }
    }
    else if (s_ranging_halted_by_pm)
    {
      s_ranging_halted_by_pm = false;
      g_pm_ranging_blocked = false;
      RLOG_I(LOG_OBJECT_CODE_APPLICATION, "[PM INFO] Safety conditions restored.");
    }
  }
  /* USER CODE END power_manage_entry */
}

/* Private application code --------------------------------------------------*/
/* USER CODE BEGIN Application */
#if ENABLE_SYS_FUSION
static bool convert_3d_to_2d_distance(double r3d, double dz, double *r2d_out)
{
    if (r3d < MIN_VALID_DISTANCE_M || r3d > MAX_VALID_DISTANCE_M) {
        return false;
    }
    double dz_abs = fabs(dz);
    if (r3d <= dz_abs + 1e-6) {
        return false;
    }
    double r2d_sq = r3d * r3d - dz * dz;
    if (r2d_sq < 0.0) {
        return false;
    }
    *r2d_out = sqrt(r2d_sq);
    return true;
}

static bool get_anchor_position(uint8_t aid, vec3d_t *pos_out)
{
    sys_config_t *cfg = sys_config_get();
    for (uint32_t i = 0; i < cfg->anchor_count; i++) {
        if (cfg->anchor_layout[i].anchor_id == aid) {
            pos_out->x = (double)cfg->anchor_layout[i].x_m;
            pos_out->y = (double)cfg->anchor_layout[i].y_m;
            pos_out->z = (double)cfg->anchor_layout[i].z_m;
            return true;
        }
    }
    return false;
}
#endif
/* USER CODE END Application */

