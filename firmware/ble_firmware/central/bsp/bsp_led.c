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
#include "app_error.h"
#include "app_timer.h"

/* -------------------------------------------------------------------------
 * LED index mapping
 * ---------------------------------------------------------------------- */
#if LEDS_NUMBER >= 4
#define LED_SCANNING   BSP_BOARD_LED_0   /**< Blinks/on while scanning.    */
#define LED_TX         BSP_BOARD_LED_1   /**< Pulses after TX activity.     */
#define LED_CONNECTED  BSP_BOARD_LED_2   /**< On while a peripheral is connected. */
#define LED_RX         BSP_BOARD_LED_3   /**< Pulses after RX activity.     */
#elif LEDS_NUMBER == 2
#define LED_SCANNING   BSP_BOARD_LED_0
#define LED_CONNECTED  BSP_BOARD_LED_0
#define LED_TX         BSP_BOARD_LED_1
#define LED_RX         BSP_BOARD_LED_1
#else // LEDS_NUMBER == 1
#define LED_SCANNING   BSP_BOARD_LED_0
#define LED_CONNECTED  BSP_BOARD_LED_0
#define LED_TX         BSP_BOARD_LED_0
#define LED_RX         BSP_BOARD_LED_0
#endif
#define LED_PULSE_INTERVAL APP_TIMER_TICKS(150)

APP_TIMER_DEF(m_tx_pulse_timer_id);
APP_TIMER_DEF(m_rx_pulse_timer_id);

static void led_tx_pulse_timeout_handler(void *p_context);
static void led_rx_pulse_timeout_handler(void *p_context);

/* -------------------------------------------------------------------------
 * Public API implementation
 * ---------------------------------------------------------------------- */

void bsp_led_init(void)
{
    ret_code_t err_code;

    bsp_board_init(BSP_INIT_LEDS);

    err_code = app_timer_create(&m_tx_pulse_timer_id,
                                APP_TIMER_MODE_SINGLE_SHOT,
                                led_tx_pulse_timeout_handler);
    APP_ERROR_CHECK(err_code);

    err_code = app_timer_create(&m_rx_pulse_timer_id,
                                APP_TIMER_MODE_SINGLE_SHOT,
                                led_rx_pulse_timeout_handler);
    APP_ERROR_CHECK(err_code);
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
    bsp_board_led_off(LED_TX);
    bsp_board_led_off(LED_RX);
}

void bsp_led_tx_pulse(void)
{
    ret_code_t err_code;

    (void)app_timer_stop(m_tx_pulse_timer_id);
    bsp_board_led_on(LED_TX);

    err_code = app_timer_start(m_tx_pulse_timer_id, LED_PULSE_INTERVAL, NULL);
    if (err_code != NRF_SUCCESS && err_code != NRF_ERROR_INVALID_STATE)
    {
        APP_ERROR_CHECK(err_code);
    }
}

void bsp_led_rx_pulse(void)
{
    ret_code_t err_code;

    (void)app_timer_stop(m_rx_pulse_timer_id);
    bsp_board_led_on(LED_RX);

    err_code = app_timer_start(m_rx_pulse_timer_id, LED_PULSE_INTERVAL, NULL);
    if (err_code != NRF_SUCCESS && err_code != NRF_ERROR_INVALID_STATE)
    {
        APP_ERROR_CHECK(err_code);
    }
}

static void led_tx_pulse_timeout_handler(void *p_context)
{
    UNUSED_PARAMETER(p_context);
    bsp_board_led_off(LED_TX);
}

static void led_rx_pulse_timeout_handler(void *p_context)
{
    UNUSED_PARAMETER(p_context);
    bsp_board_led_off(LED_RX);
}
