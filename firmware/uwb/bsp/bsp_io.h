/**
 * @file       bsp_io.h
 * @brief      BSP for GPIO: LED, Button, DIP Switch + UART1 Position Sender
 * @version    1.3.0
 * @date       2025-12-21
 * @note       Non-blocking state machine for button and DIP switch monitoring
 *             UART1: TX=PA9, RX=PA10, Baudrate=115200 (interrupt mode)
 */

#ifndef __BSP_IO_H
#define __BSP_IO_H

/* Includes ----------------------------------------------------------- */
#include "common.h"
#include <stdbool.h>
#include <stdint.h>

/* Public defines ----------------------------------------------------- */
/* LED PC13 */
#define BSP_IO_LED_PORT        GPIOC
#define BSP_IO_LED_PIN         GPIO_PIN_13

/* Button PA0 */
#define BSP_IO_BUTTON_PORT     GPIOA
#define BSP_IO_BUTTON_PIN      GPIO_PIN_0

/* DIP Switch 3-bit: PB5, PB6, PB7 */
#define BSP_IO_DIP_PORT        GPIOB
#define BSP_IO_DIP_PIN_0       GPIO_PIN_5  /* LSB */
#define BSP_IO_DIP_PIN_1       GPIO_PIN_6
#define BSP_IO_DIP_PIN_2       GPIO_PIN_7  /* MSB */

/* Button timing constants (ms) */
#define BSP_IO_DEBOUNCE_MS     25 
#define BSP_IO_DOUBLE_MS       300
#define BSP_IO_HOLD_MS         1000
#define BSP_IO_RELEASE_MS      300

/* Public enumerate/structure ----------------------------------------- */
/**
 * @brief Button event types
 */
typedef enum {
  BSP_IO_EVENT_NONE = 0,
  BSP_IO_EVENT_CLICK,
  BSP_IO_EVENT_DOUBLE_CLICK,
  BSP_IO_EVENT_HOLD,
  BSP_IO_EVENT_RELEASE
} bsp_io_button_event_t;

/**
 * @brief Button state machine states
 */
typedef enum {
  BSP_IO_BUTTON_IDLE = 0,
  BSP_IO_BUTTON_DEBOUNCE,
  BSP_IO_BUTTON_PRESSED,
  BSP_IO_BUTTON_WAIT_SECOND,
  BSP_IO_BUTTON_HOLD_DETECTED
} bsp_io_button_state_t;

/* Public function prototypes ----------------------------------------- */

/**
 * @brief Initialize BSP IO (LED, Button, DIP switch, UART1)
 * @return BSP_OK on success, BSP_ERR on failure
 * @note GPIO and UART1 should be initialized by CubeMX first
 */
bsp_err_t bsp_io_init(void);

/* LED control -------------------------------------------------------- */
/**
 * @brief Turn LED on (PC13 LOW = ON for typical dev boards)
 */
void bsp_io_led_on(void);

/**
 * @brief Turn LED off (PC13 HIGH = OFF)
 */
void bsp_io_led_off(void);

/**
 * @brief Toggle LED state
 */
void bsp_io_led_toggle(void);

/* Button control ----------------------------------------------------- */
/**
 * @brief Process button state machine (call periodically)
 * @return Button event (NONE, CLICK, DOUBLE_CLICK, HOLD, RELEASE)
 * @note Non-blocking, call from main loop
 */
bsp_io_button_event_t bsp_io_button_event(void);

/**
 * @brief Check if button activity detected (from interrupt)
 * @return true if activity detected since last call, false otherwise
 * @note Clears activity flag after reading
 */
bool bsp_io_button_activity(void);

/* DIP Switch control ------------------------------------------------- */
/**
 * @brief Read 3-bit DIP switch value
 * @return 0-7 representing switch position
 * @note Non-blocking immediate read
 */
uint8_t bsp_io_dip_read(void);

/**
 * @brief Check if DIP switch changed (from interrupt)
 * @return true if any DIP pin changed since last call
 * @note Clears change flag after reading
 */
bool bsp_io_dip_changed(void);

/* UART Position Sender ----------------------------------------------- */
/**
 * @brief Send position data via UART1 (interrupt mode)
 * @param x X coordinate in meters
 * @param y Y coordinate in meters
 * @param z Z coordinate in meters
 * @param distance Pointer to anchor distance array (NUM_ANCHORS elements), pass NULL if unavailable
 * @param error Error estimate in meters (from trilateration)
 * @return BSP_OK on success, BSP_ERR on failure
 * @note Frame format: SOF(1) + X(4) + Y(4) + Z(4) + DIST[6](24) + ERROR(4) + LENGTH(1)
 *       SOF = 0xAA, LENGTH = 40 (payload size), unused DIST slots are zero-filled
 */
bsp_err_t bsp_io_uart_send_position(float x, float y, float z, float *distance, float error);

#endif /* __BSP_IO_H */
/* End of file -------------------------------------------------------- */