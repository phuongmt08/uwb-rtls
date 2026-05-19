/* Includes ----------------------------------------------------------- */
#include "bsp_utils.h"
#include "boards.h"
#include "nrf_delay.h"
#include "app_timer.h"
#include "app_error.h"
#include "nrf_drv_clock.h"

#include "nrf_log.h"
#include "nrf_log_ctrl.h"
#include "nrf_log_default_backends.h"

/* Private defines ---------------------------------------------------- */
#define BSP_LED_STATUS    BSP_BOARD_LED_0
#define CONNECTED_BLINK_INTERVAL        APP_TIMER_TICKS(500)
#define ACTIVITY_PULSE_INTERVAL         APP_TIMER_TICKS(60)

/* Private enumerate/structure ---------------------------------------- */
/* Private macros ----------------------------------------------------- */
/* Public variables --------------------------------------------------- */
/* Private variables -------------------------------------------------- */
APP_TIMER_DEF(m_connected_blink_timer_id);
APP_TIMER_DEF(m_activity_pulse_timer_id);
static bool m_connected_blink_active = false;
static bool m_activity_pulse_active = false;
static bool m_activity_restore_on = false;

/* Private function prototypes ---------------------------------------- */
static void lfclk_config(void) __attribute__((unused));
static void connected_blink_timeout_handler(void * p_context);
static void activity_pulse_timeout_handler(void * p_context);
static void timers_init(void);

/* Function definitions ----------------------------------------------- */
void bsp_utils_init(void)
{
    bsp_board_init(BSP_INIT_LEDS);
    NRF_LOG_INFO("BSP board initialized");

    timers_init();
}

void bsp_utils_led_blink_start(void)
{
    bsp_utils_led_off();
    m_connected_blink_active = true;

    ret_code_t    err_code;
    err_code = app_timer_start(m_connected_blink_timer_id,
                                       CONNECTED_BLINK_INTERVAL,
                                       NULL);
    if (err_code != NRF_SUCCESS && err_code != NRF_ERROR_INVALID_STATE)
    {
        APP_ERROR_CHECK(err_code);
    }
}

void bsp_utils_led_blink_stop(void)
{
    m_connected_blink_active = false;
    m_activity_pulse_active = false;

    ret_code_t    err_code;
    err_code = app_timer_stop(m_connected_blink_timer_id);
    (void)app_timer_stop(m_activity_pulse_timer_id);
    if (err_code != NRF_SUCCESS && err_code != NRF_ERROR_INVALID_STATE)
    {
        APP_ERROR_CHECK(err_code);
    }
    bsp_utils_led_off();
}

void bsp_utils_led_on()
{
    bsp_board_led_on(BSP_LED_STATUS);
}

void bsp_utils_led_off()
{
    bsp_board_led_off(BSP_LED_STATUS);
}

void bsp_utils_led_toggle()
{
    bsp_board_led_invert(BSP_LED_STATUS);
}

void bsp_utils_led_activity_pulse(void)
{
    ret_code_t err_code;

    (void)app_timer_stop(m_activity_pulse_timer_id);
    if (m_connected_blink_active)
    {
        (void)app_timer_stop(m_connected_blink_timer_id);
    }

    if (!m_activity_pulse_active)
    {
        m_activity_restore_on = bsp_board_led_state_get(BSP_LED_STATUS);
    }
    m_activity_pulse_active = true;

    if (m_activity_restore_on)
    {
        bsp_utils_led_off();
    }
    else
    {
        bsp_utils_led_on();
    }

    err_code = app_timer_start(m_activity_pulse_timer_id,
                               ACTIVITY_PULSE_INTERVAL,
                               NULL);
    if (err_code != NRF_SUCCESS && err_code != NRF_ERROR_INVALID_STATE)
    {
        APP_ERROR_CHECK(err_code);
    }
}

/* Private definitions ------------------------------------------------ */
static void lfclk_config(void)
{
    ret_code_t err_code = nrf_drv_clock_init();
    APP_ERROR_CHECK(err_code);
    nrf_drv_clock_lfclk_request(NULL);
}

static void connected_blink_timeout_handler(void * p_context)
{
    UNUSED_PARAMETER(p_context);
    bsp_board_led_invert(BSP_LED_STATUS);
}

static void activity_pulse_timeout_handler(void * p_context)
{
    UNUSED_PARAMETER(p_context);

    m_activity_pulse_active = false;
    if (m_activity_restore_on)
    {
        bsp_utils_led_on();
    }
    else
    {
        bsp_utils_led_off();
    }

    if (m_connected_blink_active)
    {
        ret_code_t err_code = app_timer_start(m_connected_blink_timer_id,
                                              CONNECTED_BLINK_INTERVAL,
                                              NULL);
        if (err_code != NRF_SUCCESS && err_code != NRF_ERROR_INVALID_STATE)
        {
            APP_ERROR_CHECK(err_code);
        }
    }
}

/**@brief Function for the Timer initialization.
 *
 * @details Initializes the timer module.
 */
static void timers_init(void)
{
    // Start 32 kHz crystal oscillator, needed for app_timer.
    // If not using the S132 SoftDevice, then the lfclk can be started by simply calling nrf_drv_clock_lfclk_request.
    // lfclk_config();

    // Initialize timer module, making it use the scheduler
    ret_code_t err_code = app_timer_init();
    APP_ERROR_CHECK(err_code);

    err_code = app_timer_create(&m_connected_blink_timer_id,
                                APP_TIMER_MODE_REPEATED,
                                connected_blink_timeout_handler);
    APP_ERROR_CHECK(err_code);

    err_code = app_timer_create(&m_activity_pulse_timer_id,
                                APP_TIMER_MODE_SINGLE_SHOT,
                                activity_pulse_timeout_handler);
    APP_ERROR_CHECK(err_code);
}

/* End of file -------------------------------------------------------- */
