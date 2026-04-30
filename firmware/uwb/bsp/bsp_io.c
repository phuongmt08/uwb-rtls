/**
 * @file       bsp_io.c
 * @brief      BSP for GPIO: LED, Button, DIP Switch + UART1 Position Sender
 * @version    1.3.0
 * @date       2025-12-21
 */

/* Includes ----------------------------------------------------------- */
#include "bsp_io.h"

#include "bsp_uwb.h"
#include "gpio.h"
#include "positioning_config.h"
#include "stm32f4xx_hal.h"
#include "app_rtos_handles.h"

#include <string.h>

/* Private defines ---------------------------------------------------- */
#define UART_SOF           (0xAA)
#define UART_TX_TIMEOUT_MS (100)
#define UART_PAYLOAD_LEN_BYTES ((uint8_t) ((3U + NUM_ANCHORS + 1U) * sizeof(float)))

/* Private types ------------------------------------------------------ */
typedef struct
{
  uint8_t sof;                   /* Start of frame: 0xAA */
  uint8_t length;                /* Payload length bytes */
  float   x;                     /* X position in meters */
  float   y;                     /* Y position in meters */
  float   z;                     /* Z position in meters */
  float   distance[NUM_ANCHORS]; /* Distance each anchor */
  float   error;                 /* Error estimate in meters */
} __attribute__((packed)) uart_position_frame_t;

/* Private variables -------------------------------------------------- */
static bsp_io_button_state_t s_button_state       = BSP_IO_BUTTON_IDLE;
static uint32_t              s_last_tick          = 0;
static uint32_t              s_press_start_tick   = 0;
static uint8_t               s_pending_single     = 0;
static volatile uint8_t      s_button_activity    = 0;
static volatile uint8_t      s_dip_changed        = 0;
static bool                  s_sm_active          = false;
static volatile uint8_t      s_led_blink_active   = 0;
static uint32_t              s_led_blink_off_tick = 0;

static uart_position_frame_t s_frame;
static volatile uint8_t      s_tx_busy = 0;

/* UART handle (extern from main.c or usart.c) */
extern UART_HandleTypeDef huart1;

/* Private function prototypes ---------------------------------------- */
static bool button_is_pressed(void);

/* Public functions ------------------------------------ */

bsp_err_t bsp_io_init(void)
{
  /* GPIO pins should already be initialized by MX_GPIO_Init() */
  /* UART1 should already be initialized by MX_USART1_UART_Init() */

  /* Just reset internal state */
  s_button_state       = BSP_IO_BUTTON_IDLE;
  s_last_tick          = HAL_GetTick();
  s_press_start_tick   = 0;
  s_pending_single     = 0;
  s_button_activity    = 0;
  s_dip_changed        = 0;
  s_led_blink_active   = 0;
  s_led_blink_off_tick = 0;

  /* LED off by default (PC13 HIGH = OFF) */
  bsp_io_led_off();

  return BSP_OK;
}

/* LED control -------------------------------------------------------- */

void bsp_io_led_on(void)
{
  s_led_blink_active = 0;
  HAL_GPIO_WritePin(BSP_IO_LED_PORT, BSP_IO_LED_PIN, GPIO_PIN_RESET);
}

void bsp_io_led_off(void)
{
  s_led_blink_active = 0;
  HAL_GPIO_WritePin(BSP_IO_LED_PORT, BSP_IO_LED_PIN, GPIO_PIN_SET);
}

void bsp_io_led_toggle(void)
{
  s_led_blink_active = 0;
  HAL_GPIO_TogglePin(BSP_IO_LED_PORT, BSP_IO_LED_PIN);
}

void bsp_io_led_blink(uint32_t duration_ms)
{
  if (duration_ms == 0U)
  {
    duration_ms = 1U;
  }

  HAL_GPIO_WritePin(BSP_IO_LED_PORT, BSP_IO_LED_PIN, GPIO_PIN_RESET);
  s_led_blink_off_tick = HAL_GetTick() + duration_ms;
  s_led_blink_active   = 1;
}

void bsp_io_task(void)
{
  if (!s_led_blink_active)
  {
    return;
  }

  if ((int32_t) (HAL_GetTick() - s_led_blink_off_tick) >= 0)
  {
    HAL_GPIO_WritePin(BSP_IO_LED_PORT, BSP_IO_LED_PIN, GPIO_PIN_SET);
    s_led_blink_active = 0;
  }
}

/* Button control ----------------------------------------------------- */

bsp_io_button_event_t bsp_io_button_event(void)
{
  uint32_t now     = HAL_GetTick();
  bool     pressed = button_is_pressed();

  if (s_button_state == BSP_IO_BUTTON_IDLE && !s_button_activity)
  {
    return BSP_IO_EVENT_NONE;
  }

  if (s_button_activity)
  {
    s_button_activity = 0;
  }

  switch (s_button_state)
  {
  case BSP_IO_BUTTON_IDLE:
    if (pressed)
    {
      s_button_state = BSP_IO_BUTTON_DEBOUNCE;
      s_last_tick    = now;
      s_sm_active    = true;
    }
    break;

  case BSP_IO_BUTTON_DEBOUNCE:
    if ((now - s_last_tick) >= BSP_IO_DEBOUNCE_MS)
    {
      if (pressed)
      {
        s_button_state     = BSP_IO_BUTTON_PRESSED;
        s_press_start_tick = now;
      }
      else
      {
        s_button_state = BSP_IO_BUTTON_IDLE;
        s_sm_active    = false;
      }
    }
    break;

  case BSP_IO_BUTTON_PRESSED:
    if ((now - s_press_start_tick <= BSP_IO_RELEASE_MS) && (!pressed))
    {
      if (s_pending_single)
      {
        s_pending_single = 0;
        s_button_state   = BSP_IO_BUTTON_IDLE;
        s_sm_active      = false;
        return BSP_IO_EVENT_DOUBLE_CLICK;
      }
      else
      {
        s_pending_single = 1;
        s_last_tick      = now;
        s_button_state   = BSP_IO_BUTTON_WAIT_SECOND;
      }
    }
    else if ((now - s_press_start_tick) >= BSP_IO_HOLD_MS && pressed)
    {
      s_button_state   = BSP_IO_BUTTON_HOLD_DETECTED;
      s_pending_single = 0;
      return BSP_IO_EVENT_HOLD;
    }
    else if ((now - s_press_start_tick) > BSP_IO_RELEASE_MS && !pressed)
    {
      s_button_state = BSP_IO_BUTTON_IDLE;
      s_sm_active    = false;
    }
    break;

  case BSP_IO_BUTTON_WAIT_SECOND:
    if (pressed)
    {
      s_button_state     = BSP_IO_BUTTON_DEBOUNCE;
      s_last_tick        = now;
      s_press_start_tick = now;
    }
    else if ((now - s_last_tick) >= BSP_IO_DOUBLE_MS)
    {
      s_pending_single = 0;
      s_button_state   = BSP_IO_BUTTON_IDLE;
      s_sm_active      = false;
      return BSP_IO_EVENT_CLICK;
    }
    break;

  case BSP_IO_BUTTON_HOLD_DETECTED:
    if (!pressed)
    {
      s_button_state = BSP_IO_BUTTON_IDLE;
      s_sm_active    = false;
      return BSP_IO_EVENT_RELEASE;
    }
    break;

  default:
    s_button_state = BSP_IO_BUTTON_IDLE;
    s_sm_active    = false;
    break;
  }

  return BSP_IO_EVENT_NONE;
}

bool bsp_io_button_activity(void)
{
  uint8_t temp      = s_button_activity;
  s_button_activity = 0;
  return (temp != 0);
}

/* DIP Switch control ------------------------------------------------- */

uint8_t bsp_io_dip_read(void)
{
  uint8_t value = 0;

  if (HAL_GPIO_ReadPin(BSP_IO_DIP_PORT, BSP_IO_DIP_PIN_0) == GPIO_PIN_SET)
    value |= 0x01;

  if (HAL_GPIO_ReadPin(BSP_IO_DIP_PORT, BSP_IO_DIP_PIN_1) == GPIO_PIN_SET)
    value |= 0x02;

  if (HAL_GPIO_ReadPin(BSP_IO_DIP_PORT, BSP_IO_DIP_PIN_2) == GPIO_PIN_SET)
    value |= 0x04;

  return value;
}

bool bsp_io_dip_changed(void)
{
  uint8_t temp  = s_dip_changed;
  s_dip_changed = 0;
  return (temp != 0);
}

/* UART Position Sender ----------------------------------------------- */

bsp_err_t bsp_io_uart_send_position(float x, float y, float z, const float *distance, float error)
{
  if (s_tx_busy)
    return BSP_ERR;  // hoặc queue lại

  s_frame.sof    = UART_SOF;
  s_frame.length = UART_PAYLOAD_LEN_BYTES;
  s_frame.x      = x;
  s_frame.y      = y;
  s_frame.z      = z;
  s_frame.error  = error;

  for (uint8_t id = 0; id < NUM_ANCHORS; id++)
  {
    s_frame.distance[id] = 0.0f;
  }

  if (distance != NULL)
  {
    for (uint8_t id = 0; id < NUM_ANCHORS; id++)
    {
      s_frame.distance[id] = distance[id];
    }
  }

  s_tx_busy = 1;
  if (HAL_UART_Transmit_IT(&huart1, (uint8_t *) &s_frame, sizeof(s_frame)) != HAL_OK)
  {
    s_tx_busy = 0;
    return BSP_ERR;
  }
  return BSP_OK;
}

void HAL_UART_TxCpltCallback(UART_HandleTypeDef *huart)
{
  if (huart->Instance == USART1)
  {
    s_tx_busy = 0;
  }
}

/* Private functions ----------------------------------- */

static bool button_is_pressed(void)
{
  return (HAL_GPIO_ReadPin(BSP_IO_BUTTON_PORT, BSP_IO_BUTTON_PIN) == GPIO_PIN_RESET);
}

/* Interrupt callback ------------------------------------------------- */

void HAL_GPIO_EXTI_Callback(uint16_t GPIO_Pin)
{
  /* Button PA0 interrupt */
  if (GPIO_Pin == BSP_IO_BUTTON_PIN)
  {
    s_button_activity = 1;
    /* Wake IO task from ISR */
    osSemaphoreRelease(g_io_btn_semHandle);
  }

  /* DIP Switch interrupts PB5, PB6, PB7 */
  if (GPIO_Pin == BSP_IO_DIP_PIN_0 || GPIO_Pin == BSP_IO_DIP_PIN_1 || GPIO_Pin == BSP_IO_DIP_PIN_2)
  {
    s_dip_changed = 1;
  }

  /* UWB IRQ interrupt PA4 */
  if (GPIO_Pin == UWB_IRQ_PIN)
  {
    bsp_uwb_on_irq();
  }
}

/* End of file -------------------------------------------------------- */