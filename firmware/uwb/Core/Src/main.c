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
#include "cmsis_os.h"
#include "adc.h"
#include "crc.h"
#include "dma.h"
#include "i2c.h"
#include "rtc.h"
#include "spi.h"
#include "tim.h"
#include "usart.h"
#include "usb_device.h"
#include "gpio.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include "app_anchor.h"
#include "app_tag.h"
#include "ble/sys_ble_peripheral.h"
#include "bsp_battery.h"
#include "bsp_io.h"
#include "bsp_util.h"
#include "bsp_uwb.h"
#include "common.h"
#include "config.h"
#include "network/network_cmd.h"
#include "network/network_core.h"
#include "positioning_config.h"
#include "serial/serial.h"
#include "sys_config.h"
#include "sys_flash_storage.h"
#include "sys_logger.h"
#ifdef HAVE_BLE_PERIPHERAL
#include "ble/sys_ble_peripheral.h"
#endif
//#include "sys_task.h" /* Deprecated */
#include "sys_pm.h"
#include "app_rtos_handles.h"

#include <string.h>
#include "bsp_imu.h"
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */
#define BL_MAGIC_ADDR  (0x2001FFF0UL)
#define BL_MAGIC_VALUE (0xDEADB007UL)

/* ========== Position Test Mode ========== */
#define TEST_SEND_POS  0 /* 0=disabled, 1=enabled */

#if TEST_SEND_POS
#define TEST_DISABLE_RANGING 1    /* 1=disable ranging (UART only), 0=keep ranging */
#define TEST_POS_INTERVAL_MS 100  /* Send interval (ms) */
#define TEST_POS_START_X     1.0f /* Start X coordinate */
#define TEST_POS_START_Y     1.0f /* Start Y coordinate */
#define TEST_POS_END_X       5.0f /* End X coordinate */
#define TEST_POS_END_Y       5.0f /* End Y coordinate */
#define TEST_POS_STEP        0.5f /* Step increment 	*/
#define TEST_POS_Z           0.5f /* Fixed Z coordinate */
#define TEST_POS_ERROR       0.1f /* Fixed error estimate */
#endif

/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/
extern network_core_t g_network_core;
extern uint8_t g_network_rx_buf[512];

/* USER CODE BEGIN PV */
#if TEST_SEND_POS
static float    s_test_x              = TEST_POS_START_X;
static float    s_test_y              = TEST_POS_START_Y;
static uint32_t s_last_test_send_tick = 0;
#endif
/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
void MX_FREERTOS_Init(void);
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
  if ((current_tick - s_last_test_send_tick) < TEST_POS_INTERVAL_MS)
  {
    return;
  }

  s_last_test_send_tick = current_tick;

  /* Send current position */
  bsp_err_t ret = bsp_io_uart_send_position(s_test_x, s_test_y, TEST_POS_Z, TEST_POS_ERROR);

  if (ret == BSP_OK)
  {
    /* Update position for next send */
    s_test_x += TEST_POS_STEP;

    /* Check X boundary */
    if (s_test_x > TEST_POS_END_X)
    {
      s_test_x = TEST_POS_START_X;
      s_test_y += TEST_POS_STEP;

      /* Check Y boundary - loop back */
      if (s_test_y > TEST_POS_END_Y)
      {
        s_test_y = TEST_POS_START_Y;
      }
    }
  }
  /* If BSP_ERR (busy), just skip this interval and try next time */
}

static void test_send_pos_task(void *arg)
{
  test_send_position();
}
#endif

void app_reset_config(void)
{
  __disable_irq();
  SCB->VTOR     = 0x0800C000;
  SysTick->CTRL = 0;
  SysTick->LOAD = 0;
  SysTick->VAL  = 0;

  for (uint32_t i = 0; i < 8; i++)
  {
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
  MX_DMA_Init();
  MX_I2C1_Init();
  MX_USART1_UART_Init();
  MX_USART2_UART_Init();
  MX_SPI1_Init();
  MX_TIM10_Init();
  MX_TIM11_Init();
  MX_TIM2_Init();
  MX_CRC_Init();
  MX_RTC_Init();
  MX_ADC1_Init();
  /* USER CODE BEGIN 2 */
  int flash_storage_init_status = sys_flash_storage_init();

  sys_logger_init();
  if (flash_storage_init_status != 0)
  {
    RLOG_W(LOG_OBJECT_CODE_APPLICATION, "Flash storage init failed; log persistence may be degraded");
  }
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "System Starting...");

#if TEST_SEND_POS
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "========================================");
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "TEST MODE: Position sending ENABLED");
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "Interval: %dms", TEST_POS_INTERVAL_MS);
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "Range: (%.1f,%.1f) to (%.1f,%.1f)", TEST_POS_START_X, TEST_POS_START_Y,
         TEST_POS_END_X, TEST_POS_END_Y);
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "Step: %.2f", TEST_POS_STEP);
#if TEST_DISABLE_RANGING
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "*** RANGING DISABLED (UART TEST ONLY) ***");
#endif
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "========================================");
#endif

  sys_config_init();
  sys_config_t *cfg = sys_config_get();

  bool network_stack_ready = false;
  serial_init();
  if (!network_core_init(&g_network_core, protobuf_PACKET_ADDR_MCU, g_network_rx_buf, sizeof(g_network_rx_buf)))
  {
    RLOG_E(LOG_OBJECT_CODE_APPLICATION, ERR_NOT_INIT, "network_core_init failed");
  }
  else if (!network_cmd_init(&g_network_core))
  {
    RLOG_E(LOG_OBJECT_CODE_APPLICATION, ERR_NOT_INIT, "network_cmd_init failed");
  }
  else
  {
    network_stack_ready = true;
    RLOG_I(LOG_OBJECT_CODE_APPLICATION, "Network command stack ready");
#ifdef HAVE_BLE_PERIPHERAL
    if (!sys_ble_peripheral_init(&g_network_core))
    {
      RLOG_E(LOG_OBJECT_CODE_APPLICATION, ERR_NOT_INIT, "sys_ble_peripheral_init failed");
    }
    else
    {
      sys_ble_peripheral_set_config();
      if (!sys_ble_peripheral_enable(true))
      {
        RLOG_W(LOG_OBJECT_CODE_APPLICATION, "BLE peripheral enable request failed");
      }
    }
#endif
  }

  bsp_util_init();
  if (cfg->device_type == DEVICE_TYPE_TAG)
  {
    if (bsp_imu_init() != BSP_IMU_OK)
    {
      RLOG_W(LOG_OBJECT_CODE_APPLICATION, "IMU initialization failed");
    }
  }
  else
  {
    RLOG_I(LOG_OBJECT_CODE_APPLICATION, "IMU initialization skipped for non-tag device_type=%u",
           (unsigned)cfg->device_type);
  }

#if TEST_SEND_POS && TEST_DISABLE_RANGING
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "[SKIP] UWB init skipped (test mode)");
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "[SKIP] App init skipped (test mode)");
#else
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "[INIT] Initializing DW1000...");
  bool uwb_startup_ready = false;

  if (bsp_uwb_init() != 0)
  {
    RLOG_E(LOG_OBJECT_CODE_APPLICATION, ERR_UWB_INIT, "DW1000 initialization failed!");
  }

#if ENABLE_FORCE_DEFAULT_ANT_DLY
  if (cfg->uwb.role == DEVICE_ROLE_TAG)
  {
    cfg->uwb.tx_antenna_delay = TAG_FACTORY_TX_ANT_DLY;
    cfg->uwb.rx_antenna_delay = TAG_FACTORY_RX_ANT_DLY;
    RLOG_I(LOG_OBJECT_CODE_APPLICATION, "[CFG] Force TAG antenna delay to config default: TX=%u RX=%u",
           TAG_FACTORY_TX_ANT_DLY, TAG_FACTORY_RX_ANT_DLY);
  }
  else if (cfg->uwb.role == DEVICE_ROLE_ANCHOR)
  {
    cfg->uwb.tx_antenna_delay = ANCHOR_DEFAULT_TX_ANT_DLY;
    cfg->uwb.rx_antenna_delay = ANCHOR_DEFAULT_RX_ANT_DLY;
    RLOG_I(LOG_OBJECT_CODE_APPLICATION, "[CFG] Force ANCHOR antenna delay to config default: TX=%u RX=%u",
           ANCHOR_DEFAULT_TX_ANT_DLY, ANCHOR_DEFAULT_RX_ANT_DLY);

    sys_config_save();
    RLOG_I(LOG_OBJECT_CODE_APPLICATION, "[CFG] Saved FORCE_DEFAULT_ANT_DLY to flash");
  }
#else
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "[CFG] Use previous/calibrated antenna delay from flash");
#endif

  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "[CFG] Active config: CH=%u PRF=%u DR=%u PCode=%u",
         cfg->uwb.uwb_channel, cfg->uwb.uwb_prf, cfg->uwb.uwb_data_rate, cfg->uwb.uwb_preamble_code);
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "[CFG] Antenna delays: TX=%u RX=%u", cfg->uwb.tx_antenna_delay,
         cfg->uwb.rx_antenna_delay);

  if (bsp_uwb_configure(&cfg->uwb) == BSP_OK)
  {
    uwb_startup_ready = true;
  }
  else
  {
    RLOG_E(LOG_OBJECT_CODE_APPLICATION, ERR_UWB_INIT,
           "DW1000 configuration skipped/failed because init is not ready");
  }
#endif

  bsp_io_init();
  bsp_io_led_off();

  /* sys_task scheduler removed — tasks are managed by FreeRTOS */
  bsp_battery_init(); /* Still init hardware; task runs in power_manage_entry */
  sys_pm_init();

#if !(TEST_SEND_POS && TEST_DISABLE_RANGING)
  /* Initialize application based on role */
  if (cfg->uwb.role == DEVICE_ROLE_TAG)
  {
    app_tag_init();
    RLOG_I(LOG_OBJECT_CODE_APPLICATION, "Tag application initialized");
  }
  else
  {
    app_anchor_init();
    RLOG_I(LOG_OBJECT_CODE_APPLICATION, "Anchor application initialized");
  }

#if UWB_SLEEP_ENABLE
  /* Ranging starts disabled. Put the DW1000 into low-power sleep instead of
   * leaving it in idle; wake restores the cached runtime PHY configuration. */
  if (uwb_startup_ready && bsp_uwb_sleep_enter() == BSP_OK)
  {
    RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "[SLEEP] DW1000 sleeping until ranging starts");
  }
  else if (!uwb_startup_ready)
  {
    RLOG_W(LOG_OBJECT_CODE_UWB_DRIVER,
           "[SLEEP] Startup sleep skipped because DW1000 is not initialized");
  }
  else
  {
    RLOG_W(LOG_OBJECT_CODE_UWB_DRIVER, "[SLEEP] DW1000 startup sleep failed");
  }
#else
  if (uwb_startup_ready)
  {
    bsp_uwb_idle();
    RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "[SLEEP] Disabled; DW1000 kept awake after init");
  }
#endif
#endif

  if (network_stack_ready)
  {
    if (sys_ble_peripheral_init(&g_network_core))
    {
      sys_ble_peripheral_set_config();
      sys_ble_peripheral_enable(true);
    }
    else
    {
      RLOG_E(LOG_OBJECT_CODE_APPLICATION, ERR_NOT_INIT, "BLE peripheral init failed");
    }
  }
#ifdef DEVELOPER_MODE
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "DEVELOPER MODE ENABLED: Verbose");
  // Configure SystemView here; recording starts after the scheduler is running.
  SYSVIEW_INIT();
  #pragma message("Developer mode: SystemView enabled")
#endif
/* Init scheduler */
osKernelInitialize();  /* Call init function for freertos objects (in cmsis_os2.c) */
MX_FREERTOS_Init();

/* Start scheduler */
osKernelStart();

/* We should never get here as control is now taken by the scheduler */
/* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
    /* Should never reach here. Button events and ranging control
     * are now handled inside IO task (io_entry) and UwbRanging task. */
    osDelay(1000);
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
  RCC_OscInitStruct.PLL.PLLN = 96;
  RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV2;
  RCC_OscInitStruct.PLL.PLLQ = 4;
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

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_3) != HAL_OK)
  {
    Error_Handler();
  }
}

/* USER CODE BEGIN 4 */
/* USER CODE END 4 */

/**
  * @brief  Period elapsed callback in non blocking mode
  * @note   This function is called  when TIM9 interrupt took place, inside
  * HAL_TIM_IRQHandler(). It makes a direct call to HAL_IncTick() to increment
  * a global variable "uwTick" used as application time base.
  * @param  htim : TIM handle
  * @retval None
  */
void HAL_TIM_PeriodElapsedCallback(TIM_HandleTypeDef *htim)
{
  /* USER CODE BEGIN Callback 0 */

  /* USER CODE END Callback 0 */
  if (htim->Instance == TIM9)
  {
    HAL_IncTick();
  }
  /* USER CODE BEGIN Callback 1 */

  /* USER CODE END Callback 1 */
}

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
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
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
