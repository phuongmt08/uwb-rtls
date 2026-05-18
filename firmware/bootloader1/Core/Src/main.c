/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Main program body
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2025 STMicroelectronics.
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
#include "main.h"
#include "rtc.h"
#include "usart.h"
#include "usb_device.h"
#include "gpio.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include "bootloader.h"
#include "network_core.h"
#include "network_cmd.h"
#include "sys_logger_bl.h"
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */
#define OBJECT_CODE LOG_OBJECT_CODE_BOOTLOADER
/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */

/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/

/* USER CODE BEGIN PV */
static network_core_t  s_net_core;
static uint8_t         s_net_rx_buf[512];
/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
/* USER CODE BEGIN PFP */

/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */
static void bl_led_tick(void)
{
    HAL_GPIO_TogglePin(GPIOC, GPIO_PIN_13);
}

static void bl_log_stub_task(void)
{
  static uint32_t s_last_log_ms = 0u;
  uint32_t now = HAL_GetTick();

  if ((now - s_last_log_ms) >= 500u) {
    s_last_log_ms = now;
    (void)sys_logger_write_record(INFO_LOG,
                    OBJECT_CODE,
                    "BL stub log tick=%lu",
                    (unsigned long)now);
  }
}
/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{

  /* USER CODE BEGIN 1 */

  /* USER CODE END 1 */

  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();

  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();

  /* USER CODE BEGIN SysInit */

  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_USB_DEVICE_Init();
  MX_USART1_UART_Init();
  MX_USART2_UART_Init();
  MX_RTC_Init();
  /* USER CODE BEGIN 2 */
  
  /* Check if app requested DFU mode via magic flag */
  bool force_dfu = bl_should_enter_dfu();

  /* Logger must be initialized before any RLOG/sys_logger_write_record usage. */
  sys_logger_init();
  RLOG_D(OBJECT_CODE, "=================================================");
  RLOG_D(OBJECT_CODE, "=               BOOTLOADER STARTED              =");
  RLOG_D(OBJECT_CODE, "=================================================");
  /* Initialize protocol stack */
  serial_init();
  network_core_init(&s_net_core, protobuf_PACKET_ADDR_MCU, s_net_rx_buf, sizeof(s_net_rx_buf));
  network_cmd_init(&s_net_core);
  bl_fota_init(&s_net_core);

  uint32_t t0 = HAL_GetTick();
  uint32_t t_last_blink = t0;
  uint32_t current_timeout = force_dfu ? BL_DFU_EXTENDED_TIMEOUT_MS : BL_DFU_TIMEOUT_MS;
  bool activity_detected = false;

  RLOG_I(OBJECT_CODE, "BL: Bootup window open (%lu ms)", (unsigned long)current_timeout);

  while (1)
  {
    uint32_t now = HAL_GetTick();
    /* Inject periodic logs so network_cmd has data to send in BL mode. */
    // bl_log_stub_task();
    
    /* Process stacks */
    bl_fota_process();

    /* 1. Detect Activity */
    bool ble_active = bl_fota_is_active();
    bool usb_active = (g_dfu_last_activity != 0);

    if (ble_active || usb_active) {
      if (!activity_detected) {
        RLOG_I(OBJECT_CODE, "BL: Activity detected! Switching to Active mode.");
      }
      activity_detected = true;
    }

    /* 2. LED Signaling Logic */
    uint32_t blink_interval = 500;
    
    if (activity_detected) {
      blink_interval = 100;
    } else if ((now - t0) >= current_timeout) {
      blink_interval = 5000;
    }

    if ((now - t_last_blink) >= blink_interval) {
      bl_led_tick();
      t_last_blink = now;
    }

    /* 3. Exit/Jump Logic */
    
    /* Case A: FOTA transfer completed successfully */
    if (bl_fota_is_finished()) {
      RLOG_I(OBJECT_CODE, "BL: FOTA finished, jumping to app...");
      HAL_Delay(500);
      bl_jump_to_app();
    }

    /* Case B: Initial timeout reached with NO activity */
    if (!activity_detected && (now - t0 >= current_timeout)) {
      if (bl_app_vector_valid()) {
        RLOG_I(OBJECT_CODE, "BL: Timeout, jumping to app...");
        bl_jump_to_app();
      }
      /* If we are here, app is invalid. Loop continues in IDLE mode (slow blink). */
      RLOG_I(OBJECT_CODE, "BL: No valid app found, staying in bootloader.");
    }

    /* Case C: Active USB DFU mode - allow manual exit via USER button */
    if (activity_detected && !ble_active) {
      if (HAL_GPIO_ReadPin(GPIOA, GPIO_PIN_0) == GPIO_PIN_RESET) {
        HAL_Delay(50);
        if (HAL_GPIO_ReadPin(GPIOA, GPIO_PIN_0) == GPIO_PIN_RESET) {
          if (bl_app_vector_valid()) {
            RLOG_I(OBJECT_CODE, "BL: User requested exit, jumping to app...");
            bl_jump_to_app();
          }
        }
      }
    }
  }
  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
  }
  /* USER CODE END 3 */
}

/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  /** Configure the main internal regulator output voltage
  */
  __HAL_RCC_PWR_CLK_ENABLE();
  __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);

  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSE;
  RCC_OscInitStruct.HSEState = RCC_HSE_ON;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSE;
  RCC_OscInitStruct.PLL.PLLM = 6;
  RCC_OscInitStruct.PLL.PLLN = 168;
  RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV4;
  RCC_OscInitStruct.PLL.PLLQ = 7;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV2;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_2) != HAL_OK)
  {
    Error_Handler();
  }
}

/* USER CODE BEGIN 4 */

/* USER CODE END 4 */

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  /* User can add his own implementation to report the HAL error return state */
  __disable_irq();
  while (1)
  {
  }
  /* USER CODE END Error_Handler_Debug */
}
#ifdef USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* User can add his own implementation to report the file name and line number,
     ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
