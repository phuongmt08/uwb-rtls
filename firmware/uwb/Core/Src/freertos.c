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
/* Definitions for myBinarySem01 */
osSemaphoreId_t myBinarySem01Handle;
const osSemaphoreAttr_t myBinarySem01_attributes = {
  .name = "myBinarySem01"
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
  /* creation of myBinarySem01 */
  myBinarySem01Handle = osSemaphoreNew(1, 1, &myBinarySem01_attributes);

  /* creation of g_logger_sem */
  g_logger_semHandle = osSemaphoreNew(1, 1, &g_logger_sem_attributes);

  /* creation of g_io_btn_sem */
  g_io_btn_semHandle = osSemaphoreNew(1, 1, &g_io_btn_sem_attributes);

  /* USER CODE BEGIN RTOS_SEMAPHORES */
  /* add semaphores, ... */
  /* USER CODE END RTOS_SEMAPHORES */

  /* USER CODE BEGIN RTOS_TIMERS */
  /* start timers, add new ones, ... */
  /* USER CODE END RTOS_TIMERS */

  /* USER CODE BEGIN RTOS_QUEUES */
  /* add queues, ... */
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
  /* Infinite loop */
  for(;;)
  {
    osDelay(1);
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
  /* Infinite loop */
  for(;;)
  {
    osDelay(1);
  }
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
  /* Infinite loop */
  for(;;)
  {
    osDelay(1);
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
  /* Infinite loop */
  for(;;)
  {
    osDelay(1);
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
  /* Infinite loop */
  for(;;)
  {
    osDelay(1);
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
  /* Infinite loop */
  for(;;)
  {
    osDelay(1);
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
  /* Infinite loop */
  for(;;)
  {
    osDelay(1);
  }
  /* USER CODE END power_manage_entry */
}

/* Private application code --------------------------------------------------*/
/* USER CODE BEGIN Application */

/* USER CODE END Application */

