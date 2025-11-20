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
#include "crc.h"
#include "i2c.h"
#include "rtc.h"
#include "spi.h"
#include "tim.h"
#include "usart.h"
#include "usb_device.h"
#include "gpio.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include "bsp_uwb.h"
#include "string.h"
#include "bsp_util.h"
#include "sys_task.h"
#include "sys_logger.h"
#include "sys_config.h"

/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */
/* Magic flag in SRAM to request DFU after soft reset */
#define BL_MAGIC_ADDR      (0x2001FFF0UL)
#define BL_MAGIC_VALUE     (0xDEADB007UL)
#if 0
// Set magic and perform a clean system reset
*(volatile uint32_t*)BL_MAGIC_ADDR = BL_MAGIC_VALUE;  // request DFU
__DSB(); __ISB();
NVIC_SystemReset();
}
#endif
/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/

/* USER CODE BEGIN PV */

/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
/* USER CODE BEGIN PFP */

/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */
void task_toggle_led(void *arg)
{
//	bsp_uwb_tx(test_frame, strlen(test_frame));
	HAL_GPIO_TogglePin(GPIOC, GPIO_PIN_13);
}
//static void task_sys_logger_test(void *arg)
//{
//    static bool inited = false;
//    static uint32_t cnt = 0;
//
//    if (!inited) {
//        sys_logger_init();
//        RLOG_D(LOG_OBJECT_CODE_APPLICATION, "Logger init OK");
//        RLOG_I(LOG_OBJECT_CODE_APPLICATION, "System started, buffer size=%d bytes", SYS_LOGGER_BUF_SIZE);
//        inited = true;
//    }
//
//    // Routine logs with timestamp
//    RLOG_D(LOG_OBJECT_CODE_APPLICATION, "Tick=%lu, Free=%u, Used=%u",
//           cnt, sys_logger_space_count(), sys_logger_data_count());
//
//    // Periodic warning
//    if ((cnt % 10) == 0) {
//        RLOG_W(LOG_OBJECT_CODE_APPLICATION, "Periodic check at tick %lu", cnt);
//    }
//
//    // Simulated error
//    if ((cnt % 33) == 0) {
//        RLOG_E(LOG_OBJECT_CODE_APPLICATION, ERR_TIMEOUT, "Simulated error: code=%d", -123);
//    }
//
//    // Stress test: long message near SYS_LOGGER_MAX_MSG_LEN
//    if ((cnt % 25) == 0) {
//        char big[220];
//        for (size_t i = 0; i < sizeof(big) - 1; i++) big[i] = 'A';
//        big[sizeof(big) - 1] = '\0';
//        RLOG_D(LOG_OBJECT_CODE_APPLICATION, "Long msg test: %s", big);  // Will be truncated
//    }
//
//    // Test different components
//    if ((cnt % 50) == 0) {
//        RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "UWB module status check");
//        RLOG_D(LOG_OBJECT_CODE_RANGING, "Distance measurement ready");
//    }
//
//
//    cnt++;
//}

static void task_flash_config_test(void *arg)
{
    static bool inited = false;
    static uint32_t test_phase = 0;
    static uint32_t cycle_count = 0;
    
    if (!inited) {
        /* Initialize config system */
        sys_logger_init();
        sys_config_init();
        RLOG_I(LOG_OBJECT_CODE_APPLICATION, "=== Flash Config Test Started ===");
        inited = true;
        /* Don't return - continue to phase 0 */
    }
    
    sys_config_t *cfg = sys_config_get();
    
    switch (test_phase) {
        case 0:
            /* Print current config */
            RLOG_I(LOG_OBJECT_CODE_APPLICATION, "Current config:");
            RLOG_I(LOG_OBJECT_CODE_APPLICATION, "  Role: %s", 
                   cfg->role == DEVICE_ROLE_TAG ? "TAG" : "ANCHOR");
            RLOG_I(LOG_OBJECT_CODE_APPLICATION, "  Device ID: 0x%02X", cfg->device_id);
            RLOG_I(LOG_OBJECT_CODE_APPLICATION, "  Channel: %d", cfg->uwb_channel);
            RLOG_I(LOG_OBJECT_CODE_APPLICATION, "  Period: %d ms", cfg->ranging_period_ms);
            break;
            
        case 1:
            /* Modify config */
            RLOG_I(LOG_OBJECT_CODE_APPLICATION, "Modifying config...");
            sys_config_set_role(DEVICE_ROLE_TAG);
            sys_config_set_device_id(0x42);
            sys_config_set_uwb_channel(7);
            sys_config_set_ranging_period(500);
            break;
            
        case 2:
            /* Save to flash */
            RLOG_I(LOG_OBJECT_CODE_APPLICATION, "Saving to flash...");
            if (sys_config_save() == 0) {
                RLOG_I(LOG_OBJECT_CODE_APPLICATION, "Config saved successfully!");
            } else {
                RLOG_E(LOG_OBJECT_CODE_APPLICATION, ERR_HAL, "Config save failed");
            }
            break;
            
        case 3:
            /* Verify saved values */
            RLOG_I(LOG_OBJECT_CODE_APPLICATION, "Verify saved config:");
            RLOG_I(LOG_OBJECT_CODE_APPLICATION, "  Role: %s (expect TAG)", 
                   cfg->role == DEVICE_ROLE_TAG ? "TAG" : "ANCHOR");
            RLOG_I(LOG_OBJECT_CODE_APPLICATION, "  Device ID: 0x%02X (expect 0x42)", cfg->device_id);
            RLOG_I(LOG_OBJECT_CODE_APPLICATION, "  Channel: %d (expect 7)", cfg->uwb_channel);
            RLOG_I(LOG_OBJECT_CODE_APPLICATION, "  Period: %d ms (expect 500)", cfg->ranging_period_ms);
            break;
            
        case 4:
            /* Reset to defaults */
            RLOG_I(LOG_OBJECT_CODE_APPLICATION, "Resetting to defaults...");
            sys_config_reset_to_defaults();
            break;
            
        case 5:
            /* Verify defaults */
            RLOG_I(LOG_OBJECT_CODE_APPLICATION, "After reset:");
            RLOG_I(LOG_OBJECT_CODE_APPLICATION, "  Role: %s", 
                   cfg->role == DEVICE_ROLE_TAG ? "TAG" : "ANCHOR");
            RLOG_I(LOG_OBJECT_CODE_APPLICATION, "  Device ID: 0x%02X", cfg->device_id);
            break;
            
        case 6:
            /* Load from flash */
            RLOG_I(LOG_OBJECT_CODE_APPLICATION, "Loading from flash...");
            if (sys_config_load() == 0) {
                RLOG_I(LOG_OBJECT_CODE_APPLICATION, "Config loaded successfully!");
                RLOG_I(LOG_OBJECT_CODE_APPLICATION, "  Device ID: 0x%02X (should be 0x42)", cfg->device_id);
            }
            break;
            
        case 7:
            RLOG_I(LOG_OBJECT_CODE_APPLICATION, "=== Initial Test Complete ===");
            RLOG_I(LOG_OBJECT_CODE_APPLICATION, "Now looping continuous read/write test...");
            break;
            
        default:
            /* Continuous read/write loop */
            if (cycle_count == 0) {
                RLOG_I(LOG_OBJECT_CODE_APPLICATION, "=== Starting Continuous Loop ===");
            }
            
            /* Alternate between write and read */
            if (cycle_count % 2 == 0) {
                /* Write cycle */
                uint8_t new_id = 0x50 + (cycle_count / 2) % 16;
                sys_config_set_device_id(new_id);
                if (sys_config_save() == 0) {
                    RLOG_I(LOG_OBJECT_CODE_APPLICATION, "[Cycle %lu] Wrote ID=0x%02X", cycle_count, new_id);
                } else {
                    RLOG_E(LOG_OBJECT_CODE_APPLICATION, ERR_HAL, "[Cycle %lu] Write failed", cycle_count);
                }
            } else {
                /* Read cycle */
                if (sys_config_load() == 0) {
                    RLOG_I(LOG_OBJECT_CODE_APPLICATION, "[Cycle %lu] Read ID=0x%02X", cycle_count, cfg->device_id);
                } else {
                    RLOG_E(LOG_OBJECT_CODE_APPLICATION, ERR_HAL, "[Cycle %lu] Read failed", cycle_count);
                }
            }
            
            cycle_count++;
            return;
    }
    
    test_phase++;
}

static void task_dwm1000_basic_check(void *arg) {
    static bool initialized = false;
    static uint32_t phase = 0;
    bsp_err_t berr;
    dwm_err_t derr;
    dwm1000_t *dev;
    uint32_t temp_u32;
    uint64_t temp_u64;

    if (!initialized) {
        RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "=== DWM1000 Function Test ===");
        berr = bsp_uwb_init();
        if (berr != BSP_OK) {
            RLOG_E(LOG_OBJECT_CODE_UWB_DRIVER, 0x29, "Init failed: %d", berr);
            return;
        }
        RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "Init OK");
        initialized = true;
    }

    dev = bsp_uwb_get_device();
    if (dev == NULL) {
        RLOG_E(LOG_OBJECT_CODE_UWB_DRIVER, 0x2A, "Device NULL");
        return;
    }

    switch (phase) {
    case 0:
        // Test 1: Read Device ID
        derr = dwm_read_device_id(dev, &temp_u32);
        RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "[Phase 0] Device ID: 0x%08lX (err=%d)", temp_u32, derr);
        break;

    case 1:
        // Test 2: Read System Status
        derr = dwm_read_system_status(dev, &temp_u32);
        RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "[Phase 1] SYS_STATUS: 0x%08lX (err=%d)", temp_u32, derr);
        break;

    case 2:
        // Test 3: Clear System Status (all bits)
        derr = dwm_clear_system_status(dev, 0xFFFFFFFFu);
        RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "[Phase 2] Clear SYS_STATUS (err=%d)", derr);
        break;

    case 3:
        // Test 4: Read SYS_STATUS after clear
        derr = dwm_read_system_status(dev, &temp_u32);
        RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "[Phase 3] SYS_STATUS after clear: 0x%08lX", temp_u32);
        break;

    case 4:
        // Test 5: Read 40-bit register (e.g., SYS_TIME at 0x06)
        derr = dwm_read_40bit(dev, 0x06, -1, &temp_u64);
        RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "[Phase 4] SYS_TIME: 0x%08lX%08lX (err=%d)", 
               (uint32_t)(temp_u64 >> 32), (uint32_t)temp_u64, derr);
        break;

    case 5:
        // Test 6: Write TX Frame Control (register 0x08, 5 bytes)
        temp_u64 = 0x0800000080ULL; // Example: 128-byte frame, no ranging
        derr = dwm_write_40bit(dev, 0x08, -1, temp_u64);
        RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "[Phase 5] Write TX_FCTRL (err=%d)", derr);
        break;

    case 6:
        // Test 7: Read back TX Frame Control
        derr = dwm_read_40bit(dev, 0x08, -1, &temp_u64);
        RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "[Phase 6] Read TX_FCTRL: 0x%08lX%08lX (err=%d)",
               (uint32_t)(temp_u64 >> 32), (uint32_t)temp_u64, derr);
        break;

    case 7:
        // Test 8: Write test data to TX buffer
        {
            uint8_t test_data[16] = {0x41, 0x88, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06,
                                     0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E};
            derr = dwm_write_tx_buffer(dev, test_data, 16);
            RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "[Phase 7] Write TX buffer 16 bytes (err=%d)", derr);
        }
        break;

    case 8:
        // Test 9: Read RX buffer (whatever is there)
        {
            uint8_t read_back[16];
            derr = dwm_read_rx_buffer(dev, read_back, 16);
            RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "[Phase 8] Read RX buffer (err=%d)", derr);
            RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "  First 8 bytes: %02X %02X %02X %02X %02X %02X %02X %02X",
                   read_back[0], read_back[1], read_back[2], read_back[3],
                   read_back[4], read_back[5], read_back[6], read_back[7]);
        }
        break;

    case 9:
        // Test 10: Read EUI-64 (register 0x01, 8 bytes)
        {
            uint8_t eui[8];
            derr = dwm_read_register(dev, 0x01, -1, eui, 8);
            RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "[Phase 9] EUI-64 (err=%d):", derr);
            RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "  %02X:%02X:%02X:%02X:%02X:%02X:%02X:%02X",
                   eui[0], eui[1], eui[2], eui[3], eui[4], eui[5], eui[6], eui[7]);
        }
        break;

    default:
        RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "=== Test cycle complete, restarting ===");
        phase = 0;
        return;
    }

    phase++;
}

  /*
   * Extended DWM1000 test task
   * - Phase 0: read device ID
   * - Phase 1: read system status
   * - Phase 2: clear system status
   * - Phase 3: transmit a short test frame
   * - Phase 4: attempt receive (short window)
   * Repeats phases on subsequent invocations.
   */
  static void task_dwm1000_extended_test(void *arg)
  {
    static bool inited = false;
    static uint32_t phase = 0;
    bsp_err_t berr;
    dwm_err_t derr;
    dwm1000_t *dev;

    if (!inited) {
      RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "DWM1000-EXT: Initializing extended test...");
      berr = bsp_uwb_init();
      if (berr != BSP_OK) {
        RLOG_E(LOG_OBJECT_CODE_UWB_DRIVER, 0x29, "DWM1000-EXT: Init failed: %d", berr);
        return;
      }
      inited = true;
    }

    dev = bsp_uwb_get_device();
    if (dev == NULL) {
      RLOG_E(LOG_OBJECT_CODE_UWB_DRIVER, 0x2A, "DWM1000-EXT: Device handle is NULL");
      return;
    }

    switch (phase) {
    case 0: {
      uint32_t id = 0;
      derr = dwm_read_device_id(dev, &id);
      if (derr == DWM_OK) {
        RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "DWM1000-EXT: Device ID = 0x%08lX", id);
      } else {
        RLOG_E(LOG_OBJECT_CODE_UWB_DRIVER, 0x2B, "DWM1000-EXT: Read ID failed: %d", derr);
      }
    } break;

    case 1: {
      uint32_t status = 0;
      derr = dwm_read_system_status(dev, &status);
      if (derr == DWM_OK) {
        RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "DWM1000-EXT: SYS_STATUS = 0x%08lX", status);
      } else {
        RLOG_E(LOG_OBJECT_CODE_UWB_DRIVER, 0x2B, "DWM1000-EXT: Read SYS_STATUS failed: %d", derr);
      }
    } break;

    case 2: {
      derr = dwm_clear_system_status(dev, 0xFFFFFFFFu);
      if (derr == DWM_OK) {
        RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "DWM1000-EXT: Cleared SYS_STATUS");
      } else {
        RLOG_E(LOG_OBJECT_CODE_UWB_DRIVER, 0x2B, "DWM1000-EXT: Clear SYS_STATUS failed: %d", derr);
      }
    } break;

    case 3: {
      const char tf[] = "PING";
      berr = bsp_uwb_tx(tf, (uint16_t)(sizeof(tf) - 1));
      if (berr == BSP_OK) {
        RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "DWM1000-EXT: TX frame sent (%u bytes)", (unsigned)(sizeof(tf) - 1));
      } else {
        RLOG_E(LOG_OBJECT_CODE_UWB_DRIVER, 0x29, "DWM1000-EXT: TX failed: %d", berr);
      }
    } break;

    case 4: {
      uint8_t buf[128];
      uint16_t outlen = 0;
      berr = bsp_uwb_rx(buf, sizeof(buf), &outlen);
      if (berr == BSP_OK) {
        RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "DWM1000-EXT: RX ok, len=%u", outlen);
      } else {
        RLOG_W(LOG_OBJECT_CODE_UWB_DRIVER, "DWM1000-EXT: RX timeout/err");
      }
    } break;

    default:
      phase = 0;
      break;
    }

    phase++;
  }
/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{

  /* USER CODE BEGIN 1 */
	__disable_irq();
	SCB->VTOR = 0x08008000;
	SysTick->CTRL = 0; SysTick->LOAD = 0; SysTick->VAL = 0;
	for (uint32_t i=0;i<8;i++){ NVIC->ICER[i]=0xFFFFFFFF; NVIC->ICPR[i]=0xFFFFFFFF; }
	__DSB(); __ISB();
	__enable_irq();
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
  MX_I2C1_Init();
  MX_USART1_UART_Init();
  MX_USART2_UART_Init();
  MX_SPI1_Init();
  MX_TIM10_Init();
  MX_USB_DEVICE_Init();
  MX_TIM11_Init();
  MX_CRC_Init();
  MX_RTC_Init();
  /* USER CODE BEGIN 2 */

  // Set RTC to current date/time
  bsp_rtc_time_t current_time = {
    .year   = 25,   // 2025 - 2000 = 25
    .month  = 11,   // November
    .day    = 18,
    .hour   = 1,
    .minute = 20,
    .second = 0
  };
  bsp_rtc_set_time(&current_time);

  HAL_Delay(1000);
//  if (bsp_uwb_init() != BSP_OK) {
//    // stay here if initialization failed
//    while (1);
//  }
//  const char test_frame[] = "HELLO_UWB";
  
  // Initialize logger BEFORE tasks
  sys_logger_init();
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "System starting...");
  
  sys_task_init();
  //int id = sys_task_add(task_toggle_led, NULL, 1000, 0);
  //sys_task_start(id);
//  int id_log = sys_task_add(task_sys_logger_test, NULL, 1000, 0);  // 20 ms period
//  sys_task_start(id_log);
  
  /* Flash config test - runs every 2 seconds to show each phase */
  //int id_flash = sys_task_add(task_flash_config_test, NULL, 2000, 0);
  //sys_task_start(id_flash);
  
  /* DWM1000 basic check - runs every 5 seconds */
  int id_uwb = sys_task_add(task_dwm1000_basic_check, NULL, 5000, 0);
  sys_task_start(id_uwb);

  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
	  sys_task_process();

	// Pump buffer to USB CDC
	sys_logger_task();
//	HAL_Delay(1000);

//	if (HAL_GetTick() >= 5000) {
//		*(volatile uint32_t*)BL_MAGIC_ADDR = BL_MAGIC_VALUE;
//		__DSB(); __ISB();
//		NVIC_SystemReset();
//	}

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
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_LSI|RCC_OSCILLATORTYPE_HSE;
  RCC_OscInitStruct.HSEState = RCC_HSE_ON;
  RCC_OscInitStruct.LSIState = RCC_LSI_ON;
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
	HAL_GPIO_TogglePin(GPIOC, GPIO_PIN_13);
	HAL_Delay(100);
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
