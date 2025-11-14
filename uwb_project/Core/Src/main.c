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
#include "i2c.h"
#include "spi.h"
#include "tim.h"
#include "usart.h"
#include "usb_device.h"
#include "gpio.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include "bsp_uwb.h"
#include "string.h"
#include "bsp_delay.h"
#include "sys_task.h"
#include "sys_logger.h"

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
static void task_sys_logger_test(void *arg)
{
    static bool inited = false;
    static uint32_t cnt = 0;

    if (!inited) {
        sys_logger_init();
        RLOG_I(LOG_OBJECT_CODE_DEBUG, "Logger init OK");
        RLOG_I(LOG_OBJECT_CODE_APPLICATION, "System started, buffer size=%d bytes", SYS_LOGGER_BUF_SIZE);
        inited = true;
    }

    // Routine logs with timestamp
    RLOG_D(LOG_OBJECT_CODE_DEBUG, "Tick=%lu, Free=%u, Used=%u",
           cnt, sys_logger_space_count(), sys_logger_data_count());

    // Periodic warning
    if ((cnt % 10) == 0) {
        RLOG_W(LOG_OBJECT_CODE_DEBUG, "Periodic check at tick %lu", cnt);
    }
    
    // Simulated error
    if ((cnt % 33) == 0) {
        RLOG_E(LOG_OBJECT_CODE_DEBUG, ERR_TIMEOUT, "Simulated error: code=%d", -123);
    }

    // Stress test: long message near SYS_LOGGER_MAX_MSG_LEN
    if ((cnt % 25) == 0) {
        char big[220];
        for (size_t i = 0; i < sizeof(big) - 1; i++) big[i] = 'A';
        big[sizeof(big) - 1] = '\0';
        RLOG_D(LOG_OBJECT_CODE_DEBUG, "Long msg test: %s", big);  // Will be truncated
    }
    
    // Test different components
    if ((cnt % 50) == 0) {
        RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "UWB module status check");
        RLOG_D(LOG_OBJECT_CODE_RANGING, "Distance measurement ready");
    }


    cnt++;
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
  /* USER CODE BEGIN 2 */

  HAL_Delay(1000);
//  if (bsp_uwb_init() != BSP_OK) {
//    // stay here if initialization failed
//    while (1);
//  }
//  const char test_frame[] = "HELLO_UWB";
  sys_task_init();
  int id = sys_task_add(task_toggle_led, NULL, 1000, 0);
  sys_task_start(id);
  int id_log = sys_task_add(task_sys_logger_test, NULL, 1000, 0);  // 20 ms period
  sys_task_start(id_log);

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
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSE;
  RCC_OscInitStruct.HSEState = RCC_HSE_ON;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSE;
  RCC_OscInitStruct.PLL.PLLM = 25;
  RCC_OscInitStruct.PLL.PLLN = 336;
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
