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

#include "adc.h"
#include "crc.h"
#include "dma.h"
#include "gpio.h"
#include "i2c.h"
#include "rtc.h"
#include "spi.h"
#include "tim.h"
#include "usart.h"
#include "usb_device.h"

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
#include "network/network_cmd.h"
#include "network/network_core.h"
#include "positioning_config.h"
#include "serial/serial.h"
#include "sys_config.h"
#include "sys_flash_storage.h"
#include "sys_logger.h"
#include "sys_task.h"

#include <string.h>
#include "bsp_imu.h"
#include "sys_sensor_fusion.h"
#include "sys_pm.h"
#include "otp/otp.h"
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */
#define BL_MAGIC_ADDR  (0x2001FFF0UL)
#define BL_MAGIC_VALUE (0xDEADB007UL)

/* ========== Position Test Mode ========== */
#define TEST_SEND_POS  1 /* 0=disabled, 1=enabled */

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

/* USER CODE BEGIN PV */
static bool s_ranging_enabled = true;

static network_core_t s_network_core;
static uint8_t        s_network_rx_buf[512];

#ifdef HAVE_BLE_PERIPHERAL
static void ble_peripheral_process_task(void *arg);
#endif

#if TEST_SEND_POS
static float    s_test_x              = TEST_POS_START_X;
static float    s_test_y              = TEST_POS_START_Y;
static uint32_t s_last_test_send_tick = 0;
#endif

#if ENABLE_SYS_FUSION_LOG || ENABLE_SYS_FUSION
float dt;
uint32_t last_time = 0;
uint32_t imu_get_data_err = 0;
bsp_imu_data_t imu_current;
sys_sensor_fusion_data_t ukf_data = {0};
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
  if ((current_tick - s_last_test_send_tick) < TEST_POS_INTERVAL_MS)
  {
    return;
  }

  s_last_test_send_tick = current_tick;

  float test_distances[NUM_ANCHORS] = {
    s_test_x + 0.10f,
    s_test_y + 0.20f,
    s_test_x + s_test_y + 0.30f,
    (s_test_x * 0.5f) + (s_test_y * 0.5f) + 0.40f
  };
  
  /* Send current position */
  bsp_err_t ret =     bsp_io_uart_send_fusion_data(s_test_x, s_test_y, 5.554, s_test_y, s_test_x, 6.0, 10);

  
  if (ret == BSP_OK) {
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
static void ranging_process_task(void *arg)
{
#if TEST_SEND_POS && TEST_DISABLE_RANGING
  /* Ranging disabled in test mode */
#else
  static bool s_ranging_halted = false;
  if (!sys_pm_is_safe())
  {
    /* Halt ranging to protect the hardware under brownout conditions */
    if (!s_ranging_halted) {
        bsp_uwb_idle();
        s_ranging_halted = true;

        sys_pm_status_t pm_status;
        sys_pm_get_status(&pm_status);
        uint32_t mask = pm_status.critical_mask;

        RLOG_E(LOG_OBJECT_CODE_APPLICATION, ERR_POS_OUT_OF_RANGE,
               "\033[1;31m[CRITICAL] RANGING HALTED! Safety checks failed. Mask: 0x%04X (SOC: %.1f%%, VDDA: %.1f mV, VBAT: %.1f mV)\033[0m",
               (unsigned int)mask, pm_status.soc, pm_status.vdda_mv, pm_status.bat_voltage_mv);
    }
    return;
  }
  else if (s_ranging_halted)
  {
      s_ranging_halted = false;
      RLOG_I(LOG_OBJECT_CODE_APPLICATION, "\033[1;32m[INFO] RANGING RESUMED! Safety conditions restored.\033[0m");
  }

  if (s_ranging_enabled)
  {
    sys_config_t *cfg_curr = sys_config_get();
    if (cfg_curr->uwb.role == DEVICE_ROLE_TAG)
    {
      app_tag_process();
    }
    else
    {
      app_anchor_process(NULL);
    }
  }
#endif
}

static void logger_process_task(void *arg)
{
  sys_logger_task();
}

static void network_core_process_task(void *arg)
{
  network_core_process(&s_network_core);
}

static void network_cmd_process_task(void *arg)
{
  network_cmd_process();
}

static void ble_peripheral_process_task(void *arg)
{
  (void)arg;
  sys_ble_peripheral_process();
}

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

#if ENABLE_SYS_FUSION
static float get_dt(void)
{
    uint32_t current_time = HAL_GetTick(); // milliseconds
    uint32_t dt_ms = current_time - last_time;
    last_time = current_time;

    // Giới hạn dt để tránh giá trị bất thường
    if(dt_ms > 100) dt_ms = 100;
    if(dt_ms < 1) dt_ms = 1;

    return (float)dt_ms / 1000.0f; // chuyển sang giây
}
static void fusion_task(void *arg)
{
	if(sys_sensor_fusion_check_predict_flag() == false) return;
	static uint8_t first_call = 1;

	if(first_call)
	{
		last_time = HAL_GetTick();
		dt = 0.01f; // giá trị mặc định cho lần đầu
		first_call = 0;
	}
	else
	{
		dt = get_dt();
	}

    sys_sensor_fusion_predict(&ukf_data, dt);
    float ukf_yaw = sys_sensor_fusion_get_ukf_yaw_deg();
    float yaw = sys_sensor_fusion_get_yaw_deg();
    float tril_x = 0.0f;
    float tril_y = 0.0f;
    uint32_t err_count = 0;
    app_tag_get_latest_fusion_data(&tril_x, &tril_y, &err_count);
    bsp_io_uart_send_fusion_data(ukf_data.px, ukf_data.py, ukf_yaw, tril_x, tril_y, yaw, err_count);
//  float uwb_dists[NUM_ANCHORS];
//	float uwb_err;
//	uint32_t err_cnt;
//	app_tag_get_latest_uwb_data(uwb_dists, &uwb_err, &err_cnt);
//  bsp_io_uart_send_fusion_data(ukf_data.px, ukf_data.py, ukf_data.vx, ukf_data.vy, (ukf_data.theta*RAD2DEG),
//                                      uwb_dists, uwb_err, err_cnt);
}
#endif

#if ENABLE_SYS_FUSION_LOG
static float get_dt(void)
{
    uint32_t current_time = HAL_GetTick(); // milliseconds
    uint32_t dt_ms = current_time - last_time;
    last_time = current_time;

    // Giới hạn dt để tránh giá trị bất thường
    if(dt_ms > 100) dt_ms = 100;
    if(dt_ms < 1) dt_ms = 1;

    return (float)dt_ms / 1000.0f; // chuyển sang giây
}

static void fusion_log_task(void *arg)
{
	if(sys_sensor_fusion_check_predict_flag() == false) return;
	static uint8_t first_call = 1;

	if(first_call)
	{
		last_time = HAL_GetTick();
		dt = 0.01f;
		first_call = 0;
	}
	else
	{
		dt = get_dt();
	}

	if( bsp_imu_get_raw_data(&imu_current) != BSP_IMU_OK)
	{
		imu_get_data_err++;
	}
	else
	{
		float uwb_dists[NUM_ANCHORS] = {0.0};
		double fp_amp_norm[NUM_ANCHORS];
		double fp_snr[NUM_ANCHORS];

		for (uint8_t i = 0; i < NUM_ANCHORS; i++) {
			fp_amp_norm[i] = 0.0f;
			fp_snr[i] = 0.0f;
		}

		bsp_io_uart_send_fusion_log_data(0, 0, imu_current.ax, imu_current.ay, imu_current.gz, 0.0, 0.0, uwb_dists, fp_amp_norm, fp_snr, dt);
	}
}
#endif
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
  MX_TIM2_Init();
  MX_ADC1_Init();
  MX_TIM10_Init();
  MX_USB_DEVICE_Init();
  MX_TIM11_Init();
  MX_CRC_Init();
  MX_RTC_Init();
  /* USER CODE BEGIN 2 */

  int flash_storage_init_status = sys_flash_storage_init();

  sys_logger_init();
#if MOCK_OTP_IN_FLASH && OTP_ENABLE_FLASH_SELF_TEST
  otp_test_run();
#else
  otp_init();
#endif
  RLOG_D(LOG_OBJECT_CODE_APPLICATION, "=================================================");
  RLOG_D(LOG_OBJECT_CODE_APPLICATION, "=               APPLICATION STARTED             =");
  RLOG_D(LOG_OBJECT_CODE_APPLICATION, "=================================================");
  if (flash_storage_init_status != 0)
  {
    RLOG_W(LOG_OBJECT_CODE_APPLICATION, "Flash storage init failed; log persistence may be degraded");
  }
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

  serial_init();
  protobuf_device_addr_t local_addr = protobuf_PACKET_ADDR_MCU;

  if (!network_core_init(&s_network_core, local_addr, s_network_rx_buf, sizeof(s_network_rx_buf)))
  {
    RLOG_E(LOG_OBJECT_CODE_APPLICATION, ERR_NOT_INIT, "network_core_init failed");
  }
  else if (!network_cmd_init(&s_network_core))
  {
    RLOG_E(LOG_OBJECT_CODE_APPLICATION, ERR_NOT_INIT, "network_cmd_init failed");
  } else {
    RLOG_I(LOG_OBJECT_CODE_APPLICATION, "Network command stack ready");
  }

#if TEST_SEND_POS && TEST_DISABLE_RANGING
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "[SKIP] UWB init skipped (test mode)");
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "[SKIP] App init skipped (test mode)");
#else
  if (cfg->uwb.role == DEVICE_ROLE_TAG)
  {
    #if (ENABLE_SYS_FUSION_LOG || ENABLE_SYS_FUSION)
    if (sys_sensor_fusion_init(&ukf_data) != SYS_SENSOR_FUSION_OK)
    {
      RLOG_E(LOG_OBJECT_CODE_APPLICATION, ERR_NOT_INIT, "Sensor fusion init failed");
    }
    #else
    if(bsp_imu_init() != BSP_IMU_OK)
    {
      RLOG_E(LOG_OBJECT_CODE_APPLICATION, ERR_NOT_INIT, "IMU init failed");
    }
    #endif
  }
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
           
    /* Force save new configuration to flash for Anchor */
    sys_config_save();
    RLOG_I(LOG_OBJECT_CODE_APPLICATION, "[CFG] Saved FORCE_DEFAULT_ANT_DLY to flash");
  }
#else
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "[CFG] Use previous/calibrated antenna delay from flash");
#endif

  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "[CFG] Loaded from flash: CH=%u PRF=%u DR=%u PCode=%u",
         cfg->uwb.uwb_channel, cfg->uwb.uwb_prf, cfg->uwb.uwb_data_rate, cfg->uwb.uwb_preamble_code);
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "[CFG] Antenna delays: TX=%u RX=%u",
         cfg->uwb.tx_antenna_delay, cfg->uwb.rx_antenna_delay);

  bsp_uwb_configure(&cfg->uwb);
#endif

  bsp_io_init();
  bsp_io_led_off();

  sys_task_init();
  bsp_battery_init(); // Initialize MAX17048 battery fuel gauge
  sys_pm_init();      // Initialize PM Service (including internal ADC)

  int pm_task_id = sys_task_add((sys_task_cb_t)sys_pm_task, NULL, SYS_TASK_TYPE_PERIODIC, 100, 0);
  if (pm_task_id >= 0)
  {
    sys_task_start(pm_task_id);
  }

  int rng_task_id = sys_task_add((sys_task_cb_t)ranging_process_task, NULL, SYS_TASK_TYPE_FREERUN, 0, 0);
  if (rng_task_id >= 0) sys_task_start(rng_task_id);

  int log_task_id = sys_task_add((sys_task_cb_t)logger_process_task, NULL, SYS_TASK_TYPE_FREERUN, 0, 0);
  if (log_task_id >= 0) sys_task_start(log_task_id);

  int net_core_task_id = sys_task_add((sys_task_cb_t)network_core_process_task, NULL, SYS_TASK_TYPE_FREERUN, 0, 0);
  if (net_core_task_id >= 0) sys_task_start(net_core_task_id);

  int net_cmd_task_id = sys_task_add((sys_task_cb_t)network_cmd_process_task, NULL, SYS_TASK_TYPE_FREERUN, 0, 0);
  if (net_cmd_task_id >= 0) sys_task_start(net_cmd_task_id);

  /* BLE Peripheral Init */
  if (sys_ble_peripheral_init(&s_network_core))
  {
      sys_ble_peripheral_set_config();
      sys_ble_peripheral_enable(true);
      
      int ble_task_id = sys_task_add((sys_task_cb_t)ble_peripheral_process_task, NULL, SYS_TASK_TYPE_FREERUN, 0, 0);
      if (ble_task_id >= 0) sys_task_start(ble_task_id);
  }

#if ENABLE_SYS_FUSION
  if (cfg->uwb.role == DEVICE_ROLE_TAG)
  {
    int fusion_task_id = sys_task_add((sys_task_cb_t)fusion_task, NULL, SYS_TASK_TYPE_PERIODIC, 50, 0);
    if (fusion_task_id >= 0) sys_task_start(fusion_task_id);
  }
#endif

#if ENABLE_SYS_FUSION_LOG
  if (cfg->uwb.role == DEVICE_ROLE_TAG)
  {
    int fusion_log_task_id = sys_task_add((sys_task_cb_t)fusion_log_task, NULL, SYS_TASK_TYPE_PERIODIC, 50, 0);
    if (fusion_log_task_id >= 0) sys_task_start(fusion_log_task_id);
  }
#endif

#if TEST_SEND_POS
  int test_pos_task_id = sys_task_add((sys_task_cb_t)test_send_pos_task, NULL, SYS_TASK_TYPE_FREERUN, 0, 0);
  if (test_pos_task_id >= 0) sys_task_start(test_pos_task_id);
#endif
  
#if !(TEST_SEND_POS && TEST_DISABLE_RANGING)
  cfg = sys_config_get();

  /* Initialize application based on role */
  if (cfg->uwb.role == DEVICE_ROLE_TAG) {
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
  uint32_t tick = HAL_GetTick();
  while (1)
  {
    bsp_io_button_event_t btn_event = bsp_io_button_event();

#if ENABLE_ANCHOR_AUTO_CALIB
    /* In calibration build, anchor button events handled differently */
    if (cfg->uwb.role == DEVICE_ROLE_ANCHOR && btn_event != BSP_IO_EVENT_NONE)
    {
      app_anchor_on_button(btn_event);
      btn_event = BSP_IO_EVENT_NONE; /* Prevent normal button handling */
    }
#endif

    switch (btn_event)
    {
#if !ENABLE_ANCHOR_AUTO_CALIB
    case BSP_IO_EVENT_HOLD:
      /* Toggle TAG/ANCHOR role and save to flash */
      {
        sys_config_t *cfg_curr = sys_config_get();
        device_role_t new_role =
          (cfg_curr->uwb.role == DEVICE_ROLE_TAG) ? DEVICE_ROLE_ANCHOR : DEVICE_ROLE_TAG;

        sys_config_set_role(new_role);
        sys_config_save();

        /* Quick LED blink to indicate save */
        for (uint8_t i = 0; i < 3; i++)
        {
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
      if (s_ranging_enabled)
      {
        s_ranging_enabled = false;
        bsp_uwb_idle();
        RLOG_I(LOG_OBJECT_CODE_APPLICATION, "\033[1;31mRanging stopped - DW1000 idle (Disabled by Button Double-Click)\033[0m");
      }
      break;

    case BSP_IO_EVENT_CLICK:
      /* Start ranging */
      if (!s_ranging_enabled)
      {
        s_ranging_enabled = true;
        RLOG_I(LOG_OBJECT_CODE_APPLICATION, "Ranging started");
      }
      break;

    default: break;
    }

    sys_task_process();
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
  RCC_OscInitTypeDef RCC_OscInitStruct = { 0 };
  RCC_ClkInitTypeDef RCC_ClkInitStruct = { 0 };

  /** Configure the main internal regulator output voltage
   */
  __HAL_RCC_PWR_CLK_ENABLE();
  __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);

  /** Initializes the RCC Oscillators according to the specified parameters
   * in the RCC_OscInitTypeDef structure.
   */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSE;
  RCC_OscInitStruct.HSEState       = RCC_HSE_ON;
  RCC_OscInitStruct.PLL.PLLState   = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource  = RCC_PLLSOURCE_HSE;
  RCC_OscInitStruct.PLL.PLLM       = 6;
  RCC_OscInitStruct.PLL.PLLN       = 96;
  RCC_OscInitStruct.PLL.PLLP       = RCC_PLLP_DIV2;
  RCC_OscInitStruct.PLL.PLLQ       = 4;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  /** Initializes the CPU, AHB and APB buses clocks
   */
  RCC_ClkInitStruct.ClockType =
    RCC_CLOCKTYPE_HCLK | RCC_CLOCKTYPE_SYSCLK | RCC_CLOCKTYPE_PCLK1 | RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource   = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider  = RCC_SYSCLK_DIV1;
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
 * @brief  This function is executed in case of error occurrence.
 * @retval None
 */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  __disable_irq();
  while (1)
  {
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
