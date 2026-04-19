/**
 * @file    bsp_led.h
 * @brief   BSP layer for board LEDs.
 *
 * Provides named, application-level LED control for the BLE Central
 * application (scanning indicator, connection indicator).
 */

#ifndef BSP_LED_H
#define BSP_LED_H

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/* -------------------------------------------------------------------------
 * Public API
 * ---------------------------------------------------------------------- */

/**
 * @brief Initialize board LEDs.
 *
 * Must be called before any other bsp_led_* function.
 * Internally calls bsp_board_init(BSP_INIT_LEDS).
 */
void bsp_led_init(void);

/**
 * @brief Enter the "scanning" LED state.
 *
 * Turns the scanning LED on and the connected LED off.
 */
void bsp_led_scanning(void);

/**
 * @brief Enter the "connected" LED state.
 *
 * Turns the connected LED on and the scanning LED off.
 */
void bsp_led_connected(void);

/**
 * @brief Turn all application LEDs off.
 */
void bsp_led_all_off(void);

#ifdef __cplusplus
}
#endif

#endif /* BSP_LED_H */
