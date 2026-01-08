/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : DS-TWR Tag/Anchor Application with Calibration
  * @version        : 3.1.0
  * @date           : 2025-12-24
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
#include "sys_config.h"
#include "sys_logger.h"
#include "bsp_uwb.h"
#include "bsp_io.h"
#include "bsp_util.h"
#include "common.h"
#include "app_tag.h"
#include "app_anchor.h"
#include "positioning_config.h"
#include <string.h>
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */
#define BL_MAGIC_ADDR      (0x2001FFF0UL)
#define BL_MAGIC_VALUE     (0xDEADB007UL)

/* ========== Position Test Mode ========== */
#define TEST_SEND_POS           1     /* 0=disabled, 1=enabled */

#if TEST_SEND_POS
  #define TEST_DISABLE_RANGING    1     /* 1=disable ranging (UART only), 0=keep ranging */
  #define TEST_POS_INTERVAL_MS    100   /* Send interval (ms) */
  #define TEST_POS_START_X        1.0f  /* Start X coordinate */
  #define TEST_POS_START_Y        1.0f  /* Start Y coordinate */
  #define TEST_POS_END_X          5.0f  /* End X coordinate */
  #define TEST_POS_END_Y          5.0f  /* End Y coordinate */
  #define TEST_POS_STEP           0.5f  /* Step increment */
  #define TEST_POS_Z              0.5f  /* Fixed Z coordinate */
  #define TEST_POS_ERROR          0.1f  /* Fixed error estimate */
#endif

/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/

/* USER CODE BEGIN PV */
static bool s_ranging_enabled = true;

#if TEST_SEND_POS
static float s_test_x = TEST_POS_START_X;
static float s_test_y = TEST_POS_START_Y;
static uint32_t s_last_test_send_tick = 0;
#endif
/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
/* USER CODE BEGIN PFP */
/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */

#if TEST_SEND_POS
/**
 * @brief Test function to send dummy position data via UART
 * @note Non-blocking, called periodically from main loop
 */
static void test_send_position(void)
{
  uint32_t current_tick = HAL_GetTick();
  
  /* Check if enough time has passed */
  if ((current_tick - s_last_test_send_tick) < TEST_POS_INTERVAL_MS) {
    return;
  }
  
  s_last_test_send_tick = current_tick;
  
  /* Send current position */
  bsp_err_t ret = bsp_io_uart_send_position(s_test_x, s_test_y, TEST_POS_Z, TEST_POS_ERROR);
  
  if (ret == BSP_OK) {
    /* Update position for next send */
    s_test_x += TEST_POS_STEP;
    
    /* Check X boundary */
    if (s_test_x > TEST_POS_END_X) {
      s_test_x = TEST_POS_START_X;
      s_test_y += TEST_POS_STEP;
      
      /* Check Y boundary - loop back */
      if (s_test_y > TEST_POS_END_Y) {
        s_test_y = TEST_POS_START_Y;
      }
    }
  }
  /* If BSP_ERR (busy), just skip this interval and try next time */
}
#endif

void app_reset_config(void)
{
  __disable_irq();
  SCB->VTOR = 0x08008000;
  SysTick->CTRL = 0; 
  SysTick->LOAD = 0; 
  SysTick->VAL = 0;
  
  for (uint32_t i = 0; i < 8; i++) {
    NVIC->ICER[i] = 0xFFFFFFFF;
    NVIC->ICPR[i] = 0xFFFFFFFF;
  }
  
  __DSB(); 
  __ISB();
  __enable_irq();
}

/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{

  /* USER CODE BEGIN 1 */
  app_reset_config();
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
  
  sys_logger_init();
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "System Starting...");
  
#if TEST_SEND_POS
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "========================================");
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "TEST MODE: Position sending ENABLED");
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "Interval: %dms", TEST_POS_INTERVAL_MS);
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "Range: (%.1f,%.1f) to (%.1f,%.1f)", 
         TEST_POS_START_X, TEST_POS_START_Y, TEST_POS_END_X, TEST_POS_END_Y);
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "Step: %.2f", TEST_POS_STEP);
#if TEST_DISABLE_RANGING
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "*** RANGING DISABLED (UART TEST ONLY) ***");
#endif
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "========================================");
#endif
  
  sys_config_init();
  sys_config_t *cfg = sys_config_get();
  
#if TEST_SEND_POS && TEST_DISABLE_RANGING
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "[SKIP] UWB init skipped (test mode)");
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "[SKIP] App init skipped (test mode)");
#else
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "[INIT] Initializing DW1000...");
  
  if (bsp_uwb_init() != 0) {
    RLOG_E(LOG_OBJECT_CODE_APPLICATION, ERR_UWB_INIT, "DW1000 initialization failed!");
  }
  
  bsp_uwb_config_t uwb_cfg = {
    .channel           = cfg->uwb_channel,
    .prf               = cfg->uwb_prf,
    .data_rate         = cfg->uwb_data_rate,
    .preamble_code     = cfg->uwb_preamble_code,
    .tx_antenna_delay  = cfg->tx_antenna_delay,
    .rx_antenna_delay  = cfg->rx_antenna_delay,
    .tx_power          = cfg->tx_power,
  };
  
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "[CFG] Loaded from flash: CH=%u PRF=%u DR=%u PCode=%u", 
         uwb_cfg.channel, uwb_cfg.prf, uwb_cfg.data_rate, uwb_cfg.preamble_code);
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "[CFG] Antenna delays: TX=%u RX=%u", 
         uwb_cfg.tx_antenna_delay, uwb_cfg.rx_antenna_delay);
  
  bsp_uwb_configure(&uwb_cfg);
#endif
  
  bsp_io_init();
  bsp_io_led_off();
  
#if !(TEST_SEND_POS && TEST_DISABLE_RANGING)
  /* Read DIP switch - ALWAYS OVERRIDES saved config */
  uint8_t dip_value = bsp_io_dip_read();
  if (dip_value == 0) {
    RLOG_I(LOG_OBJECT_CODE_APPLICATION, "[DIP=0] Using saved Device ID: %u", cfg->device_id);
  } else {
    sys_config_set_device_id(dip_value);
    RLOG_I(LOG_OBJECT_CODE_APPLICATION, "[DIP=%u] Device ID FORCED to: %u", dip_value, dip_value);
  }
  
  cfg = sys_config_get();
  
  /* Initialize application based on role */
  if (cfg->role == DEVICE_ROLE_TAG) {
    app_tag_init();
    RLOG_I(LOG_OBJECT_CODE_APPLICATION, "Tag application initialized");
  } else {
    app_anchor_init();
    RLOG_I(LOG_OBJECT_CODE_APPLICATION, "Anchor application initialized");
  }
#endif
  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
    bsp_io_button_event_t btn_event = bsp_io_button_event();
    
#if ENABLE_ANCHOR_AUTO_CALIB
    /* In calibration build, anchor button events handled differently */
    if (cfg->role == DEVICE_ROLE_ANCHOR && btn_event != BSP_IO_EVENT_NONE) {
      app_anchor_on_button(btn_event);
      btn_event = BSP_IO_EVENT_NONE;  /* Prevent normal button handling */
    }
#endif
    
    switch (btn_event)
    {
#if !ENABLE_ANCHOR_AUTO_CALIB
      case BSP_IO_EVENT_HOLD:
        /* Toggle TAG/ANCHOR role and save to flash */
        {
          sys_config_t *cfg_curr = sys_config_get();
          device_role_t new_role = (cfg_curr->role == DEVICE_ROLE_TAG) ? 
                                    DEVICE_ROLE_ANCHOR : DEVICE_ROLE_TAG;
          
          sys_config_set_role(new_role);
          sys_config_save();
          
          /* Quick LED blink to indicate save */
          for (uint8_t i = 0; i < 3; i++) {
            bsp_io_led_on();
            bsp_delay_ms(50);
            bsp_io_led_off();
            bsp_delay_ms(50);
          }
          
          RLOG_I(LOG_OBJECT_CODE_APPLICATION, "Role changed to: %s",
                 new_role == DEVICE_ROLE_TAG ? "TAG" : "ANCHOR");
          RLOG_I(LOG_OBJECT_CODE_APPLICATION, "System will restart...");
          bsp_delay_ms(100);
          HAL_NVIC_SystemReset();
        }
        break;
#endif
        
      case BSP_IO_EVENT_DOUBLE_CLICK:
        /* Stop ranging */
        if (s_ranging_enabled) {
          s_ranging_enabled = false;
          bsp_uwb_idle();
          RLOG_I(LOG_OBJECT_CODE_APPLICATION, "Ranging stopped - DW1000 idle");
        }
        break;
        
      case BSP_IO_EVENT_CLICK:
        /* Start ranging */
        if (!s_ranging_enabled) {
          s_ranging_enabled = true;
          RLOG_I(LOG_OBJECT_CODE_APPLICATION, "Ranging started");
        }
        break;
        
      default:
        break;
    }
    
    /* Process ranging if enabled */
#if TEST_SEND_POS && TEST_DISABLE_RANGING
    /* Ranging disabled in test mode */
#else
    if (s_ranging_enabled)
    {
      sys_config_t *cfg_curr = sys_config_get();
      if (cfg_curr->role == DEVICE_ROLE_TAG) {
        app_tag_process();
      } else {
        app_anchor_process(NULL);
      }
    }
#endif`

#if TEST_SEND_POS
    /* Test mode: Send dummy position periodically */
    test_send_position();
#endif

    sys_logger_task();
    bsp_delay_ms(1);
    
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
  __disable_irq();
  while (1) {
    bsp_io_led_toggle();
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
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
