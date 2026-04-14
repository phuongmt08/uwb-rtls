/**
 * @file    bsp_led.c
 * @brief   BSP layer for board LEDs.
 *
 * Maps application-level LED states (scanning, connected) to the physical
 * board LEDs defined in boards.h.
 */

#include "bsp_led.h"
#include "central_io.h"

#include "boards.h"
#include "bsp.h"

/* -------------------------------------------------------------------------
 * LED index mapping
 * ---------------------------------------------------------------------- */
#define LED_SCANNING   BSP_BOARD_LED_0   /**< Blinks/on while scanning.    */
#define LED_CONNECTED  BSP_BOARD_LED_2   /**< On while a peripheral is connected. */

/* -------------------------------------------------------------------------
 * Public API implementation
 * ---------------------------------------------------------------------- */

void bsp_led_init(void)
{
    bsp_board_init(BSP_INIT_LEDS);
}

void bsp_led_scanning(void)
{
    bsp_board_led_off(LED_CONNECTED);
    bsp_board_led_on(LED_SCANNING);
}

void bsp_led_connected(void)
{
    bsp_board_led_off(LED_SCANNING);
    bsp_board_led_on(LED_CONNECTED);
}

void bsp_led_all_off(void)
{
    bsp_board_led_off(LED_SCANNING);
    bsp_board_led_off(LED_CONNECTED);
}
