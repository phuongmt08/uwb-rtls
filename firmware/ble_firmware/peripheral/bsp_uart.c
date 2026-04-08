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
#include "app_error.h"
#include "app_util.h"
#include "boards.h"
#include <string.h>
#include "nrf_uart.h"
#include "nrf_error.h"
#include "peripheral_io.h"

#include "nrf_log.h"
#include "nrf_log_ctrl.h"
#include "nrf_log_default_backends.h"

/* Private defines ---------------------------------------------------- */
#define UART_TX_BUF_SIZE 256
#define UART_RX_BUF_SIZE 256
#define BLE_NUS_MAX_DATA_LEN 244

/* Private enumerate/structure ---------------------------------------- */
/* Private macros ----------------------------------------------------- */
/* Public variables --------------------------------------------------- */
/* Private variables -------------------------------------------------- */
static uint8_t m_uart_data_buffer[BLE_NUS_MAX_DATA_LEN];
static uint8_t m_buffer_index = 0;

/* Private function prototypes ---------------------------------------- */
static uint32_t uart_send_data(const uint8_t * p_data, uint16_t len);
static uint32_t uart_data_handler();
static void uart_event_handler(app_uart_evt_t * p_event);

/* Function definitions ----------------------------------------------- */
uint32_t bsp_uart_init(void)
{
    
    uint32_t err_code;
    app_uart_comm_params_t const comm_params =
    {
        .rx_pin_no    = RX_PIN_NUMBER,
        .tx_pin_no    = TX_PIN_NUMBER,
        .rts_pin_no   = RTS_PIN_NUMBER,
        .cts_pin_no   = CTS_PIN_NUMBER,
        .flow_control = APP_UART_FLOW_CONTROL_DISABLED,
        .use_parity   = false,
#if defined (UART_PRESENT)
        .baud_rate    = NRF_UART_BAUDRATE_115200
#else
        .baud_rate    = NRF_UARTE_BAUDRATE_115200
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
        NRF_LOG_INFO("UART module initialized (115200 8N1)");
    }
    
    return err_code;
}

uint32_t bsp_uart_send_string(const char * p_str)
{
    if (p_str == NULL)
        return NRF_ERROR_NULL;
    
    return uart_send_data((const uint8_t *)p_str, strlen(p_str));
}

/* Private definitions ------------------------------------------------ */
/**@brief UART event handler.
 */
static void uart_event_handler(app_uart_evt_t * p_event)
{
    switch (p_event->evt_type)
    {
        case APP_UART_DATA_READY:
            uart_data_handler();
            break;

        case APP_UART_COMMUNICATION_ERROR:
            NRF_LOG_WARNING("UART Communication Error: 0x%X", p_event->data.error_communication);
            break;

        case APP_UART_FIFO_ERROR:
            NRF_LOG_WARNING("UART FIFO Error: 0x%X", p_event->data.error_code);
            break;

        default:
            break;
    }
}

static uint32_t uart_send_data(const uint8_t * p_data, uint16_t len)
{
    if (p_data == NULL)
        return NRF_ERROR_NULL;
    
    for (uint16_t i = 0; i < len; i++)
    {
        uint32_t err_code = app_uart_put(p_data[i]);
        if (err_code != NRF_SUCCESS)
        {
            return err_code;
        }
    }
    
    return NRF_SUCCESS;
}

static uint32_t uart_data_handler()
{
    UNUSED_VARIABLE(app_uart_get(&m_uart_data_buffer[m_buffer_index]));
    m_buffer_index++;

    // Check if we received a complete line or buffer is full
    if ((m_uart_data_buffer[m_buffer_index - 1] == '\n') ||
        (m_uart_data_buffer[m_buffer_index - 1] == '\r') ||
        (m_buffer_index >= BLE_NUS_MAX_DATA_LEN))
    {
        if (m_buffer_index > 0)
        {
            m_uart_data_buffer[m_buffer_index] = '\0';
            
            // Remove trailing \n and \r for clean comparison
            uint8_t clean_len = m_buffer_index;
            while (clean_len > 0 && (m_uart_data_buffer[clean_len - 1] == '\n' || 
                                        m_uart_data_buffer[clean_len - 1] == '\r'))
            {
                m_uart_data_buffer[clean_len - 1] = '\0';
                clean_len--;
            }
            
            NRF_LOG_INFO("UART RX: %s", (uint32_t)m_uart_data_buffer);
            bsp_uart_send_string("\r\nReceived: ");
            bsp_uart_send_string((const char *)m_uart_data_buffer);
            bsp_uart_send_string("\n");
        }
        m_buffer_index = 0;
    }
    return NRF_SUCCESS;
}

/* End of file -------------------------------------------------------- */
