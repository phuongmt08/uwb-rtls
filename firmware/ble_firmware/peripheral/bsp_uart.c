/**
 * @file       bsp_uart.c
 * @copyright  [Your Copyright]
 * @license    [Your License]
 * @version    1.0.0
 * @date       2026-04-08
 * @author     Dong Son
 *
 * @brief      
 */
/* Includes ----------------------------------------------------------- */
#include "bsp_uart.h"
#include "app_uart.h"
#include "nrf_uart.h"
#include "nrf_uarte.h"
#include "nrf_error.h"
#include "peripheral_io.h"

#include "nrf_log.h"
#include "nrf_log_ctrl.h"
#include "nrf_log_default_backends.h"
#include "../ble_common/ble_bridge/bb_debug.h"

/* Private defines ---------------------------------------------------- */
#define UART_TX_BUF_SIZE 4096
#define UART_RX_BUF_SIZE 2048
#define BLE_NUS_MAX_DATA_LEN 244

/* Private enumerate/structure ---------------------------------------- */
/* Private macros ----------------------------------------------------- */
/* Public variables --------------------------------------------------- */
/* Private variables -------------------------------------------------- */
static bsp_uart_rx_cb_t s_rx_cb = NULL;

/* Private function prototypes ---------------------------------------- */
static void uart_event_handler(app_uart_evt_t * p_event);

/* Function definitions ----------------------------------------------- */
ret_code_t bsp_uart_init(bsp_uart_rx_cb_t rx_cb)
{
    s_rx_cb = rx_cb;

    ret_code_t err_code;
    app_uart_comm_params_t const comm_params =
    {
        .rx_pin_no    = RX_PIN_NUMBER,
        .tx_pin_no    = TX_PIN_NUMBER,
        .rts_pin_no   = RTS_PIN_NUMBER,
        .cts_pin_no   = CTS_PIN_NUMBER,
        .flow_control = APP_UART_FLOW_CONTROL_DISABLED,
        .use_parity   = false,
#if defined (UART_PRESENT)
        .baud_rate    = NRF_UART_BAUDRATE_230400
#else
        .baud_rate    = NRF_UARTE_BAUDRATE_230400
#endif
    };

    APP_UART_FIFO_INIT(&comm_params,
                       UART_RX_BUF_SIZE,
                       UART_TX_BUF_SIZE,
                       uart_event_handler,
                       APP_IRQ_PRIORITY_LOWEST,
                       err_code);

    if (err_code == NRF_SUCCESS)
    {
        BB_DEBUG_LOG_INFO("UART module initialized in interrupt mode (230400 8N1)");
    }
    
    return err_code;
}

ret_code_t bsp_uart_transmit(const uint8_t *buf, uint16_t len)
{
    if (buf == NULL)
        return NRF_ERROR_NULL;
    
    if (len == 0)
        return NRF_SUCCESS;
    
    for (uint16_t i = 0; i < len; i++)
    {
        ret_code_t err_code = app_uart_put(buf[i]);
        if (err_code != NRF_SUCCESS)
        {
            return err_code;
        }
    }
    
    return NRF_SUCCESS;
}

/* Private definitions ------------------------------------------------ */
static void uart_event_handler(app_uart_evt_t * p_event)
{
    switch (p_event->evt_type)
    {
        case APP_UART_DATA_READY:
            bsp_uart_read_byte();
            break;

        case APP_UART_COMMUNICATION_ERROR:
        case APP_UART_FIFO_ERROR:
        default:
            break;
    }
}

void bsp_uart_read_byte(void)
{
    uint8_t byte;
    while (app_uart_get(&byte) == NRF_SUCCESS)
    {
        if (s_rx_cb != NULL)
        {
            s_rx_cb(byte);
        }
    }
}

/* End of file -------------------------------------------------------- */
