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
#include "ble/sys_ble_peripheral.h"
#include "app_calib_master.h"
#include "bsp_battery.h"
#include "bsp_io.h"
#include "bsp_imu.h"
#include "bsp_uwb.h"
#include "bsp_util.h"
#include "common.h"
#include "config.h"
#include "network/network_core.h"
#include "network/network_cmd.h"
#ifdef HAVE_BLE_PERIPHERAL
#include "ble/sys_ble_peripheral.h"
#endif
#include "sys_config.h"
#include "sys_logger.h"
#include "sys_pm.h"
#include "sys_ranging.h"
#include "positioning_config.h"
#include "bsp_io.h"
#include <math.h>
#include "sys_sensor_fusion.h"
#include "mw_filter.h"
#include "mw_trilateration.h"
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
osMessageQueueId_t g_imu_data_queue;

bool g_ranging_enabled = false;
bool g_pm_ranging_blocked = false;

/* Network objects â€” non-static so main.c can init via extern */
network_core_t g_network_core;
uint8_t        g_network_rx_buf[512];
uint8_t        uwb_entry_cnt = 0;
uint8_t        fusion_entry_cnt = 0;
bsp_imu_data_t imu_test_mutex_lock = {0};
bool imu_test_mutex_flag = false;

/* Owner variables for decoupled active Sensor Fusion */
sys_sensor_fusion_data_t ukf_data;
static mahalanobis_prefilter_t s_prefilter;

static uint8_t s_last_selected_anchors_mask = 0U;
static volatile bool s_fusion_reset_requested = false;

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
  .stack_size = 2048 * 4,
  .priority = (osPriority_t) osPriorityHigh,
};
/* Definitions for Network */
osThreadId_t NetworkHandle;
const osThreadAttr_t Network_attributes = {
  .name = "Network",
  .stack_size = 768 * 4,
  .priority = (osPriority_t) osPriorityNormal,
};
/* Definitions for SysMonitoring */
osThreadId_t SysMonitoringHandle;
const osThreadAttr_t SysMonitoring_attributes = {
  .name = "SysMonitoring",
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

static bool convert_3d_to_2d_distance(double r3d, double dz, double *r2d_out);
static bool get_anchor_position(uint8_t aid, vec3d_t *pos_out);
static void sensor_fusion_reset_state(void);

static void drain_signal_semaphore(osSemaphoreId_t sem);
static bool abort_uwb_ranging_locked(sys_config_t *cfg);
static void stop_uwb_ranging_locked(void);
static void reset_ranging_runtime_state(sys_config_t *cfg);
static bool apply_ranging_enabled(sys_config_t *cfg, bool enabled);
/* USER CODE END FunctionPrototypes */

void uwb_ranging_entry(void *argument);
void sensor_fusion_entry(void *argument);
void network_entry(void *argument);
void sys_monitoring_entry(void *argument);
void flash_storage_entry(void *argument);
void io_entry(void *argument);
void power_manage_entry(void *argument);
void app_rtos_request_sensor_fusion_reset(void);

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
  /* Drain initial count to 0 â€” signal semaphores must start at 0 so tasks block */
  osSemaphoreAcquire(g_uwb_isr_semHandle, 0);  /* UWB ISR â†’ UwbRanging  */
  osSemaphoreAcquire(g_logger_semHandle,   0);  /* logger signal           */
  osSemaphoreAcquire(g_io_btn_semHandle,   0);  /* button signal           */
  /* USER CODE END RTOS_SEMAPHORES */

  /* USER CODE BEGIN RTOS_TIMERS */
  /* start timers, add new ones, ... */
  /* USER CODE END RTOS_TIMERS */

  /* USER CODE BEGIN RTOS_QUEUES */
  g_uwb_distance_queue = osMessageQueueNew(4, sizeof(uwb_distance_msg_t), NULL);
  g_imu_data_queue = osMessageQueueNew(8, sizeof(bsp_imu_data_t), NULL);
  /* USER CODE END RTOS_QUEUES */

  /* Create the thread(s) */
  /* creation of UwbRanging */
  UwbRangingHandle = osThreadNew(uwb_ranging_entry, NULL, &UwbRanging_attributes);

  /* creation of SensorFusion */
  SensorFusionHandle = osThreadNew(sensor_fusion_entry, NULL, &SensorFusion_attributes);

  /* creation of Network */
  NetworkHandle = osThreadNew(network_entry, NULL, &Network_attributes);

  /* creation of SysMonitoring */
  SysMonitoringHandle = osThreadNew(sys_monitoring_entry, NULL, &SysMonitoring_attributes);

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
#if TEST_UKF_STREAM_BLE
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
	  uwb_entry_cnt++;
	  if(uwb_entry_cnt >= 255) uwb_entry_cnt = 0;

    app_tag_process_uwb_control(cfg);

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
          if (app_calib_master_should_run())
          {
            app_calib_master_process();
          }
          else
          {
            app_tag_process();
          }
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
      if (app_calib_master_should_run())
      {
        app_calib_master_process();
      }
      else
      {
        app_tag_process();
      }
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
  if (sys_config_get()->uwb.role != DEVICE_ROLE_TAG)
  {
    osThreadExit();
  }
  /* Init prefilter */
  sensor_fusion_reset_state();

  /* TEST */
#if TEST_UKF_STREAM_UART
  for (;;)
  {
    sys_sensor_fusion_stream_uart(UKF_STEP_PREDICT);
    osDelay(20);
  }
#endif

#if TEST_UKF_STREAM_BLE
  for (;;)
  {
    sys_sensor_fusion_stream_ble(UKF_STEP_UPDATE);
    osDelay(20);
  }
#endif

  for (;;)
  {
    bool fusion_predict_performed = false;
    bool fusion_update_performed = false;

    if (s_fusion_reset_requested)
    {
      s_fusion_reset_requested = false;
      (void)osMessageQueueReset(g_uwb_distance_queue);
      (void)osMessageQueueReset(g_imu_data_queue);
      sensor_fusion_reset_state();
      continue;
    }

    (void)sys_sensor_fusion_task();

    if (!g_ranging_enabled)
	{
    	osDelay(20);
    	continue;
	}

    uwb_distance_msg_t msg;
    bool has_uwb_msg = false;

    while (osMessageQueueGet(g_uwb_distance_queue, &msg, NULL, 0U) == osOK)
    {
        has_uwb_msg = true;
    }

#if TEST_UKF_DISTANCE_ZERO_SIMULATION
    if (g_imu_data_queue != NULL && osMessageQueueGetCount(g_imu_data_queue) > 0U)
    {
      fusion_predict_performed = (sys_sensor_fusion_predict(&ukf_data) == SYS_SENSOR_FUSION_OK);
    }
#else
    if (sys_sensor_fusion_check_predict_flag() &&
        g_imu_data_queue != NULL &&
        osMessageQueueGetCount(g_imu_data_queue) > 0U)
    {
      fusion_predict_performed = (sys_sensor_fusion_predict(&ukf_data) == SYS_SENSOR_FUSION_OK);
    }
#endif

    if (has_uwb_msg)
    {
        /* 1. Calculate dynamic dt for ranging if needed, and update logs */

        /* 2. Process, project to 2D, and Mahalanobis filter the ranges */
        mw_tril_anchor_t anchors_by_id[MAX_ANCHORS_SUPPORTED + 1] = {0};
        uint8_t valid_count = 0;

			#if ENABLE_MAHALANOBIS_PREFILTER
        mw_tril_anchor_t prefilter_rejects[NUM_ANCHORS];
        uint8_t prefilter_reject_count = 0U;
			#endif

        for (uint8_t i = 0; i < msg.count && i < MAX_ANCHORS_SUPPORTED; i++) {
            uint8_t aid = msg.anchor_ids[i];
            if (aid < 1 || aid > MAX_ANCHORS_SUPPORTED) continue;

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
            anchor_entry.fp_amp_norm = (double)msg.fp_amp_norm[i];
            anchor_entry.fp_snr = (double)msg.fp_snr[i];
            anchor_entry.quality_valid = (msg.quality_valid[i] != 0U);
            anchor_entry.selection_score = 0.0;
            anchor_entry.residual_rms = 0.0;
            anchor_entry.gdop_penalty = 0.0;
            anchor_entry.fp_penalty = 0.0;

#if ENABLE_MAHALANOBIS_PREFILTER
            bool pass = true;
            const sys_prefilter_cfg_t *active_prefilter_cfg = sys_config_get_prefilter();
            if (sys_sensor_fusion_is_initialized() && active_prefilter_cfg->enable)
            {
                s_prefilter.T1 = active_prefilter_cfg->recover_d2;
                s_prefilter.T2 = active_prefilter_cfg->reject_d2;
                s_prefilter.R_base = active_prefilter_cfg->r_base;
                s_prefilter.R_gate = active_prefilter_cfg->r_gate;
                s_prefilter.velocity_weight = active_prefilter_cfg->velocity_weight;
                s_prefilter.min_covariance = active_prefilter_cfg->min_covariance;
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
                if (aid == 0U || aid > MAX_ANCHORS_SUPPORTED || anchors_by_id[aid].valid) {
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
            for (uint8_t id = 1; id <= MAX_ANCHORS_SUPPORTED && compact_idx < NUM_ANCHORS; id++) {
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
#if TEST_UKF_DISTANCE_ZERO_SIMULATION
                    for (uint8_t i = 0U; i < 3U; i++)
                    {
                        best_3_anchors[i].distance = 0.0;
                    }
#endif
                    uint32_t current_time = HAL_GetTick();
                    uint32_t latency_ms = current_time - msg.timestamp_ms;
                    // RLOG_I(LOG_OBJECT_CODE_TAG, "[FUSION LATENCY] UWB Queue Latency: %u ms", latency_ms);

                    SYSVIEW_START(SYSVIEW_MARK_FUSION_UKF_UPDATE);
                    fusion_update_performed = sys_sensor_fusion_update(
                                                      &ukf_data,
                                                      &tril_position,
                                                      best_3_anchors,
                                                      anchors_by_id,
                                                      anchors_compact,
                                                      compact_idx,
                                                      s_last_selected_anchors_mask,
                                                      &msg);
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

    if (!has_uwb_msg)
    {
        sys_sensor_fusion_clear_latest_anchor_metrics();

#if TEST_UKF_DISTANCE_ZERO_SIMULATION
        const sys_config_t *fusion_cfg = sys_config_get();
        mw_tril_anchor_t anchors_by_id[MAX_ANCHORS_SUPPORTED + 1] = {0};
        mw_tril_anchor_t anchors_compact[NUM_ANCHORS] = {0};
        mw_tril_anchor_t best_3_anchors[3] = {0};
        uint8_t compact_idx = 0U;

        for (uint32_t i = 0U; i < fusion_cfg->anchor_count && compact_idx < 3U; i++)
        {
          uint8_t aid = (uint8_t)fusion_cfg->anchor_layout[i].anchor_id;
          if (aid < 1U || aid > MAX_ANCHORS_SUPPORTED) {
            continue;
          }

          mw_tril_anchor_t anchor_entry = {0};
          anchor_entry.position.x = (double)fusion_cfg->anchor_layout[i].x_m;
          anchor_entry.position.y = (double)fusion_cfg->anchor_layout[i].y_m;
          anchor_entry.position.z = (double)fusion_cfg->anchor_layout[i].z_m;
          anchor_entry.distance = 0.0;
          anchor_entry.id = aid;
          anchor_entry.valid = true;

          anchors_by_id[aid] = anchor_entry;
          anchors_compact[compact_idx] = anchor_entry;
          best_3_anchors[compact_idx] = anchor_entry;
          compact_idx++;
        }

        if (compact_idx >= 3U)
        {
          uint8_t selected_mask = 0U;
          for (uint8_t i = 0U; i < 3U; i++)
          {
            selected_mask |= (uint8_t)(1U << (best_3_anchors[i].id - 1U));
          }
          s_last_selected_anchors_mask = selected_mask;

          vec2d_t tril_position = {0.0, 0.0};
          fusion_update_performed = sys_sensor_fusion_update(&ukf_data,
                                                             &tril_position,
                                                             best_3_anchors,
                                                             anchors_by_id,
                                                             anchors_compact,
                                                             compact_idx,
                                                             selected_mask,
                                                             NULL);
        }
#endif
    }

    if (fusion_update_performed || fusion_predict_performed)
    {
      sys_sensor_fusion_stream_ble(fusion_update_performed ? UKF_STEP_UPDATE : UKF_STEP_PREDICT);
    }
    
    osDelay(20);
  }

  osThreadExit();

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
#ifdef HAVE_BLE_PERIPHERAL
    sys_ble_peripheral_process();
#endif
    osDelay(2);
  }
  /* USER CODE END network_entry */
}

/* USER CODE BEGIN Header_sys_monitoring_entry */
/**
* @brief Function implementing the SysMonitoring thread.
* @param argument: Not used
* @retval None
*/
/* USER CODE END Header_sys_monitoring_entry */
void sys_monitoring_entry(void *argument)
{
  /* USER CODE BEGIN sys_monitoring_entry */
  const uint32_t monitor_interval_ms = 30000U;
  (void)argument;
  bsp_util_rtos_monitor_update();
  for (;;)
  {
    osDelay(monitor_interval_ms);
    bsp_util_rtos_monitor_update();
    network_send_rtos_resource(&g_network_core, protobuf_PACKET_ADDR_HOST);

#if APP_RTOS_STATS_LOG_ENABLE
    const bsp_util_rtos_snapshot_t *snap = bsp_util_rtos_monitor_get();
    if (snap == NULL || !snap->valid) {
      RLOG_W(LOG_OBJECT_CODE_TASK, "[RTOS] stats unavailable");
      continue;
    }

    RLOG_D(LOG_OBJECT_CODE_TASK,
           "[RTOS] window=%lums cpu=%lu.%lu%% heap=%luB min_heap=%luB min_stack=%luB task_id=%lu flags=0x%02lX tasks=%lu/%lu",
           (unsigned long)snap->sample_window_ms,
           (unsigned long)(snap->cpu_busy_permille / 10U),
           (unsigned long)(snap->cpu_busy_permille % 10U),
           (unsigned long)snap->heap_free_bytes,
           (unsigned long)snap->heap_min_ever_free_bytes,
           (unsigned long)snap->min_stack_free_bytes,
           (unsigned long)snap->min_stack_task_id,
           (unsigned long)snap->health_flags,
           (unsigned long)snap->task_count,
           (unsigned long)snap->task_count_total);

    for (uint32_t i = 0U; i < snap->task_count; i++) {
      const bsp_util_rtos_task_stat_t *task = &snap->tasks[i];
      RLOG_D(LOG_OBJECT_CODE_TASK,
             "[RTOS_TASK] name=%s cpu=%lu.%lu%% stack_free=%luB id=%lu",
             task->name,
             (unsigned long)(task->cpu_permille / 10U),
             (unsigned long)(task->cpu_permille % 10U),
             (unsigned long)task->stack_min_free_bytes,
             (unsigned long)task->task_id);
    }
#endif
  }
  /* USER CODE END sys_monitoring_entry */
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
      if (cfg->uwb.role == DEVICE_ROLE_ANCHOR)
      {
        bool enable = !app_anchor_is_survey_active();
        g_ranging_enabled = false;
        if (!abort_uwb_ranging_locked(cfg)) {
          RLOG_W(LOG_OBJECT_CODE_APPLICATION, "[CALIB] UWB wake failed; survey toggle skipped");
          break;
        }
        app_anchor_set_survey_active(enable);
        app_anchor_init();
        g_ranging_enabled = true;
        if (g_uwb_isr_semHandle != NULL) {
          (void)osSemaphoreRelease(g_uwb_isr_semHandle);
        }
        RLOG_I(LOG_OBJECT_CODE_APPLICATION,
               "[CALIB] Anchor survey %s",
               enable ? "enabled" : "disabled");
        break;
      }
      break;
    case BSP_IO_EVENT_CLICK:
      if (cfg->uwb.role == DEVICE_ROLE_ANCHOR &&
          app_anchor_is_survey_active())
      {
        break;
      }
      bool enable_ranging = !g_ranging_enabled;
      bool ranging_changed = apply_ranging_enabled(cfg, enable_ranging);
      if (ranging_changed && enable_ranging)
      {
        RLOG_I(LOG_OBJECT_CODE_APPLICATION, "Ranging started");
      }
      else if (ranging_changed)
      {
        RLOG_I(LOG_OBJECT_CODE_APPLICATION, "Ranging stopped");
      }
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

    bool allow_uwb_telemetry = !sys_ranging_is_active() && !bsp_uwb_is_sleeping();
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
void vApplicationIdleHook(void)
{
#if APP_IDLE_SLEEP_ENABLE
    __DSB();
    __WFI();
    __ISB();
#endif
}

void app_rtos_set_ranging_enabled(bool enabled)
{
    g_ranging_enabled = enabled;
}

bool app_rtos_is_ranging_enabled(void)
{
    return g_ranging_enabled;
}

bool app_rtos_apply_ranging_enabled(bool enabled)
{
    return apply_ranging_enabled(sys_config_get(), enabled);
}

static void drain_signal_semaphore(osSemaphoreId_t sem)
{
    if (sem == NULL) {
        return;
    }
    while (osSemaphoreAcquire(sem, 0U) == osOK) {
    }
}

static bool abort_uwb_ranging_locked(sys_config_t *cfg)
{
    (void)osMutexAcquire(g_spi1_mutexHandle, osWaitForever);
    sys_ranging_abort();
    if (bsp_uwb_sleep_wake() != BSP_OK) {
        RLOG_W(LOG_OBJECT_CODE_UWB_DRIVER, "[SLEEP] Wake failed; resetting DW1000");
        if (bsp_uwb_init() != BSP_OK) {
            RLOG_W(LOG_OBJECT_CODE_UWB_DRIVER,
                   "[SLEEP] DW1000 fallback init failed");
            (void)osMutexRelease(g_spi1_mutexHandle);
            drain_signal_semaphore(g_uwb_isr_semHandle);
            return false;
        }
        /* Full configuration is only the recovery path. Normal SLEEP wake
         * restores the saved registers through AON and must remain fast. */
        if (cfg == NULL || bsp_uwb_configure(&cfg->uwb) != BSP_OK) {
            RLOG_W(LOG_OBJECT_CODE_UWB_DRIVER,
                   "[SLEEP] DW1000 fallback configure failed");
            (void)osMutexRelease(g_spi1_mutexHandle);
            drain_signal_semaphore(g_uwb_isr_semHandle);
            return false;
        }
    }
    bsp_uwb_idle();
    (void)osMutexRelease(g_spi1_mutexHandle);
    drain_signal_semaphore(g_uwb_isr_semHandle);
    return true;
}

static void stop_uwb_ranging_locked(void)
{
    (void)osMutexAcquire(g_spi1_mutexHandle, osWaitForever);
    sys_ranging_abort();
#if UWB_SLEEP_ENABLE
    if (bsp_uwb_sleep_enter() != BSP_OK) {
        /* sleep_enter() already forces TRX off before attempting sleep. */
        RLOG_W(LOG_OBJECT_CODE_UWB_DRIVER,
               "[SLEEP] DW1000 failed to enter sleep after ranging stopped");
    }
#else
    bsp_uwb_idle();
#endif
    (void)osMutexRelease(g_spi1_mutexHandle);
    drain_signal_semaphore(g_uwb_isr_semHandle);
}

static void reset_ranging_runtime_state(sys_config_t *cfg)
{
    if (cfg == NULL) {
        return;
    }

    if (cfg->uwb.role == DEVICE_ROLE_TAG) {
        app_calib_master_on_ranging_stopped();
        app_tag_reset_fusion();
    } else {
        (void)app_anchor_init();
    }
}

static bool apply_ranging_enabled(sys_config_t *cfg, bool enabled)
{
    g_ranging_enabled = false;
    if (enabled) {
        if (!abort_uwb_ranging_locked(cfg)) {
            RLOG_W(LOG_OBJECT_CODE_APPLICATION, "Ranging start skipped: DW1000 wake failed");
            return false;
        }
    } else {
        stop_uwb_ranging_locked();
    }
    reset_ranging_runtime_state(cfg);
    g_ranging_enabled = enabled;

    if (enabled && g_uwb_isr_semHandle != NULL) {
        (void)osSemaphoreRelease(g_uwb_isr_semHandle);
    }
    return true;
}

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

void app_rtos_request_sensor_fusion_reset(void)
{
    s_fusion_reset_requested = true;
}

static void sensor_fusion_reset_state(void)
{
    sys_sensor_fusion_clear_predict_flag();
    sys_sensor_fusion_clear_update_flag();
    sys_sensor_fusion_reset_error();
    s_last_selected_anchors_mask = 0U;

    const sys_prefilter_cfg_t *prefilter_cfg = sys_config_get_prefilter();
    mw_filter_mahalanobis_init(&s_prefilter,
                               prefilter_cfg->recover_d2,
                               prefilter_cfg->reject_d2,
                               prefilter_cfg->r_base,
                               prefilter_cfg->r_gate,
                               prefilter_cfg->velocity_weight,
                               prefilter_cfg->min_covariance);

    if (sys_sensor_fusion_init(&ukf_data) != SYS_SENSOR_FUSION_OK)
    {
        RLOG_W(LOG_OBJECT_CODE_TAG, "[FUSION] UKF re-initialization failed");
    }
    else
    {
        RLOG_I(LOG_OBJECT_CODE_TAG, "[FUSION] UKF re-initialized successfully");
    }
}

/* USER CODE END Application */
