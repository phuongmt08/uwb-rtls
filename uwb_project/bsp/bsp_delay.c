/**
 * @file        bsp_delay.c
 * @license     This project is for academic and educational purposes under the ITR Internship Program.
 * @version     1.0.0
 * @date        2025-08-05
 * @author      Chinh Nguyen
 * @author      Phuong Mai
 *
 * @brief       This file implements the delay functionality using TIM9 for microsecond delays.
 *
 * @note        This implementation is based on the STM32F4xx HAL library.
 */

/* Includes ----------------------------------------------------------- */
#include "bsp_delay.h"
#include "stm32f4xx_hal.h"
#include "assert.h"
#include "stdbool.h"
/* Private types ------------------------------------------------------------- */
typedef struct
{
  TIM_HandleTypeDef htim;
} delay_handle_t;

/* Private variables --------------------------------------------------------- */
static delay_handle_t g_delay;
static bool is_bsp_delay_inited = false;
/* Private function prototypes ---------------------------------------- */
/**
 * @brief  Internal helper to wait for given microseconds.
 * @param  us: microseconds to wait.
 */
static void delay_wait_us_(uint32_t us);

/* Exported functions -------------------------------------------------------- */
delay_err_t bsp_delay_init(void)
{
  __HAL_RCC_TIM9_CLK_ENABLE();

  g_delay.htim.Instance               = TIM9;
  g_delay.htim.Init.Prescaler         = (SystemCoreClock / 1000000UL) - 1;
  g_delay.htim.Init.CounterMode       = TIM_COUNTERMODE_UP;
  g_delay.htim.Init.Period            = 0xFFFF;
  g_delay.htim.Init.ClockDivision     = TIM_CLOCKDIVISION_DIV1;
  g_delay.htim.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;

  if (HAL_TIM_Base_Init(&g_delay.htim) != HAL_OK)
    return DELAY_ERR;

  if (HAL_TIM_Base_Start(&g_delay.htim) != HAL_OK)
    return DELAY_ERR;
  is_bsp_delay_inited = true;
  return DELAY_OK;
}

void bsp_delay_us(uint32_t us)
{
  delay_wait_us_(us);
}

void bsp_delay(uint32_t ms)
{
  while (ms--) delay_wait_us_(1000);
}

/* Private functions --------------------------------------------------------- */
static void delay_wait_us_(uint32_t us)
{
  assert(is_bsp_delay_inited && "bsp_delay used before bsp_delay_init");
  uint16_t start = __HAL_TIM_GET_COUNTER(&g_delay.htim);
  while ((uint16_t) (__HAL_TIM_GET_COUNTER(&g_delay.htim) - start) < us)
  {
    __NOP();
  }
}

/* End of file -------------------------------------------------------- */
