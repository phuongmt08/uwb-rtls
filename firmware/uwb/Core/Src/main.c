/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Simple TX/RX validation before DS-TWR
  * @note           : Validates basic UWB communication first
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
#include "usbd_cdc_if.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include "sys_config.h"
#include "sys_logger.h"
#include "bsp_uwb.h"
#include "common.h"
#include <string.h>
/* USER CODE END Includes */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */
#define BL_MAGIC_ADDR      (0x2001FFF0UL)
#define BL_MAGIC_VALUE     (0xDEADB007UL)

/* CRITICAL: Choose ONE mode */
#define TEST_MODE       1   // Set to 1 for transmitter

/* Test packet configuration */
#define TEST_PACKET_SIZE   12   // Minimum safe size (with CRC = 14 bytes total)
#define TX_INTERVAL_MS     1000  // Send every 1 second
#define RX_POLL_MS         10    // Check RX every 10ms

/* USER CODE END PD */

/* Private variables ---------------------------------------------------------*/
/* USER CODE BEGIN PV */
static uint32_t s_tx_count = 0;
static uint32_t s_rx_count = 0;
static uint32_t s_last_tx_tick = 0;
/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
/* USER CODE BEGIN PFP */
void simple_tx_test(void);
void simple_rx_test(void);
/* USER CODE END PFP */

/* USER CODE BEGIN 0 */

void app_reset_config(void)
{
  __disable_irq();
  SCB->VTOR = 0x08008000;
  SysTick->CTRL = 0; SysTick->LOAD = 0; SysTick->VAL = 0;
  for (uint32_t i=0; i<8; i++) {
    NVIC->ICER[i]=0xFFFFFFFF;
    NVIC->ICPR[i]=0xFFFFFFFF;
  }
  __DSB(); __ISB();
  __enable_irq();
}

/**
 * @brief Simple TX test - sends packets periodically
 */
void simple_tx_test(void)
{
  static bool first_tx = true;
  uint32_t current_tick = HAL_GetTick();


  /* Send packet every TX_INTERVAL_MS */
  if ((current_tick - s_last_tx_tick) >= TX_INTERVAL_MS) {
    s_last_tx_tick = current_tick;

    /* Create test packet with IEEE 802.15.4 header structure
     * Frame format: [FC(2) | SEQ(1) | PANID(2) | DEST(2) | SRC(2) | DATA(3)]
     */
    uint8_t tx_packet[TEST_PACKET_SIZE];
    tx_packet[0] = 0x41;  // Frame Control: Data frame
    tx_packet[1] = 0x88;  // Frame Control: PAN compression, short addressing
    tx_packet[2] = (uint8_t)s_tx_count;  // Sequence number
    tx_packet[3] = 0xCA;  // PAN ID low
    tx_packet[4] = 0xDE;  // PAN ID high
    tx_packet[5] = 0x01;  // Dest address low
    tx_packet[6] = 0x00;  // Dest address high
    tx_packet[7] = 0x02;  // Source address low
    tx_packet[8] = 0x00;  // Source address high

    /* Payload: test pattern */
    tx_packet[9] = 0xAA;
    tx_packet[10] = 0xBB;
    tx_packet[11] = (uint8_t)(s_tx_count & 0xFF);

    RLOG_I(LOG_OBJECT_CODE_APPLICATION, "");
    RLOG_I(LOG_OBJECT_CODE_APPLICATION, "=================================");
    RLOG_I(LOG_OBJECT_CODE_APPLICATION, "    TX TEST #%lu", s_tx_count);
    RLOG_I(LOG_OBJECT_CODE_APPLICATION, "=================================");

    /* Transmit */
    bsp_err_t err = bsp_uwb_tx(tx_packet, TEST_PACKET_SIZE);

    if (err == BSP_OK) {
      s_tx_count++;
      RLOG_I(LOG_OBJECT_CODE_APPLICATION, "✓ TX Success! Seq:%02X",
             tx_packet[2]);

      /* LED feedback */
      HAL_GPIO_WritePin(GPIOC, GPIO_PIN_13, GPIO_PIN_RESET);
      HAL_Delay(50);
      HAL_GPIO_WritePin(GPIOC, GPIO_PIN_13, GPIO_PIN_SET);
    } else {
      RLOG_E(LOG_OBJECT_CODE_APPLICATION, ERR_UWB_TX, "✗ TX Failed");
    }

    RLOG_I(LOG_OBJECT_CODE_APPLICATION, "=================================");
  }
}

/**
 * @brief Simple RX test - polls for incoming packets
 */
void simple_rx_test(void)
{
  static uint32_t s_last_rx_poll = 0;
  static bool s_rx_enabled = false;

  uint32_t current_tick = HAL_GetTick();

  /* Poll for packets every RX_POLL_MS */
  if ((current_tick - s_last_rx_poll) >= RX_POLL_MS) {
    s_last_rx_poll = current_tick;

    uint8_t rx_buffer[128];
    uint16_t rx_length = 0;

    bsp_err_t err = bsp_uwb_rx(rx_buffer, sizeof(rx_buffer), &rx_length);

    if (err == BSP_OK) {
      /* Packet received! */
      s_rx_count++;
      RLOG_I(LOG_OBJECT_CODE_APPLICATION, "✓ RX Success! #%lu Len:%u Seq:%02X",
             s_rx_count, rx_length, (rx_length >= 3) ? rx_buffer[2] : 0);
    } else {
      /* Error occurred */
      RLOG_W(LOG_OBJECT_CODE_APPLICATION, "RX error: %d, re-enabling...", err);
    }
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
  app_reset_config();
  /* USER CODE END 1 */

  /* MCU Configuration--------------------------------------------------------*/
  HAL_Init();

  /* Configure the system clock */
  SystemClock_Config();

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
  HAL_Delay(500);
  (void)CDC_Transmit_FS((uint8_t*)"VALIDATION_TEST\r\n", 17);

  /* Initialize logger */
  sys_logger_init();
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "[BOOT] Validation Test Starting");

  /* Display test mode */
#if TEST_MODE
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "");
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "╔════════════════════════════════╗");
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "║    MODE: TRANSMITTER (TX)      ║");
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "║  Will send packets every 1s    ║");
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "╚════════════════════════════════╝");
#else
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "");
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "╔════════════════════════════════╗");
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "║     MODE: RECEIVER (RX)        ║");
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "║   Waiting for packets...       ║");
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "╚════════════════════════════════╝");
#endif

  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "");

  /* Initialize UWB hardware */
  bsp_uwb_config_t uwb_cfg = {
    .channel          = 5,
    .prf              = 64,
    .data_rate        = 1,
  };

  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "[INIT] Initializing UWB...");

  if (bsp_uwb_init() != BSP_OK) {
    RLOG_E(LOG_OBJECT_CODE_UWB_DRIVER, ERR_UWB_INIT, "UWB init failed!");
  }

  if (bsp_uwb_configure(&uwb_cfg) != BSP_OK) {
    RLOG_E(LOG_OBJECT_CODE_UWB_DRIVER, ERR_UWB_CONFIG, "UWB config failed!");
  }
  dwt_setleds(1);

  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "[INIT] UWB configured successfully");
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "  Channel: 5");
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "  PRF: 64 MHz");

  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
#if TEST_MODE
    /* Transmitter mode */
    simple_tx_test();

#else
    /* Receiver mode */
    simple_rx_test();
#endif

    /* Flush log buffer */
    sys_logger_task();

    /* Small delay to prevent overwhelming the system */
    HAL_Delay(1);
  }
  /* USER CODE END WHILE */

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

  __HAL_RCC_PWR_CLK_ENABLE();
  __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);

  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_LSI|RCC_OSCILLATORTYPE_HSE;
  RCC_OscInitStruct.HSEState = RCC_HSE_ON;
  RCC_OscInitStruct.LSIState = RCC_LSI_ON;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSE;
  RCC_OscInitStruct.PLL.PLLM = 6;
  RCC_OscInitStruct.PLL.PLLN = 168;
  RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV4;
  RCC_OscInitStruct.PLL.PLLQ = 7;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK) {
    Error_Handler();
  }

  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV2;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_2) != HAL_OK) {
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
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
