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
#include <string.h>
#include "app_util_platform.h"
#include "nrf_libuarte_async.h"
#include "nrf_uarte.h"
#include "nrf_error.h"
#include "peripheral_io.h"

#include "nrf_log.h"
#include "nrf_log_ctrl.h"
#include "nrf_log_default_backends.h"
#include "../ble_common/ble_bridge/bb_debug.h"

/* Private defines ---------------------------------------------------- */
#define UART_BAUDRATE             NRF_UARTE_BAUDRATE_1000000
#define UART_RX_DMA_BUF_SIZE      128U
#define UART_RX_DMA_BUF_COUNT     4U
#define UART_RX_TIMEOUT_US        250U
#define UART_TX_QUEUE_DEPTH       8U
#define UART_TX_MAX_FRAME_LEN     517U

/* Private enumerate/structure ---------------------------------------- */
/* Private macros ----------------------------------------------------- */
/* Public variables --------------------------------------------------- */
/* Private variables -------------------------------------------------- */
static bsp_uart_rx_cb_t s_rx_cb = NULL;

typedef struct
{
    uint16_t len;
    uint8_t data[UART_TX_MAX_FRAME_LEN];
} uart_tx_item_t;

static uart_tx_item_t s_tx_queue[UART_TX_QUEUE_DEPTH];
static volatile uint8_t s_tx_head;
static volatile uint8_t s_tx_tail;
static volatile bool s_tx_active;
static volatile uint32_t s_uart_error_count;
static volatile uint32_t s_uart_overrun_count;

/*
 * TIMER0 is reserved by the SoftDevice. TIMER1 counts received bytes and
 * TIMER2 detects the RX idle gap. PPI connects both timers to UARTE, so RX
 * buffering does not depend on per-byte ISR latency. Four 128-byte buffers
 * cover long SoftDevice interrupt latencies.
 */
NRF_LIBUARTE_ASYNC_DEFINE(s_uart,
                         0,
                         1,
                         NRF_LIBUARTE_PERIPHERAL_NOT_USED,
                         2,
                         UART_RX_DMA_BUF_SIZE,
                         UART_RX_DMA_BUF_COUNT);

/* Private function prototypes ---------------------------------------- */
static void uart_event_handler(void * context, nrf_libuarte_async_evt_t * p_event);
static ret_code_t uart_start_next_tx(void);

/* Function definitions ----------------------------------------------- */
ret_code_t bsp_uart_init(bsp_uart_rx_cb_t rx_cb)
{
    s_rx_cb = rx_cb;

    nrf_libuarte_async_config_t const config =
    {
        .rx_pin     = RX_PIN_NUMBER,
        .tx_pin     = TX_PIN_NUMBER,
        .rts_pin    = NRF_UARTE_PSEL_DISCONNECTED,
        .cts_pin    = NRF_UARTE_PSEL_DISCONNECTED,
        .baudrate   = UART_BAUDRATE,
        .parity     = NRF_UARTE_PARITY_EXCLUDED,
        .hwfc       = NRF_UARTE_HWFC_DISABLED,
        .timeout_us = UART_RX_TIMEOUT_US,
        /* libUARTE assigns the timeout TIMER one application level lower. */
        .int_prio   = APP_IRQ_PRIORITY_LOW,
        .pullup_rx  = true
    };

    s_tx_head = 0;
    s_tx_tail = 0;
    s_tx_active = false;
    s_uart_error_count = 0;
    s_uart_overrun_count = 0;

    ret_code_t err_code = nrf_libuarte_async_init(&s_uart,
                                                   &config,
                                                   uart_event_handler,
                                                   NULL);
    if (err_code != NRF_SUCCESS)
    {
        return err_code;
    }

    nrf_libuarte_async_enable(&s_uart);
    BB_DEBUG_LOG_INFO("UARTE initialized at 1000000 8N1, DMA RX 4x128, HWFC off");
    return NRF_SUCCESS;
}

ret_code_t bsp_uart_transmit(const uint8_t *buf, uint16_t len)
{
    if (buf == NULL)
        return NRF_ERROR_NULL;
    
    if (len == 0U)
        return NRF_SUCCESS;

    if (len > UART_TX_MAX_FRAME_LEN)
    {
        return NRF_ERROR_DATA_SIZE;
    }

    uint8_t const head = s_tx_head;
    uint8_t const next = (uint8_t)((head + 1U) % UART_TX_QUEUE_DEPTH);
    if (next == s_tx_tail)
    {
        return NRF_ERROR_NO_MEM;
    }

    /*
     * The caller's HDLC frame is stack-backed. Copy it into DMA-safe static RAM
     * before queueing it for asynchronous transmission.
     */
    memcpy(s_tx_queue[head].data, buf, len);
    s_tx_queue[head].len = len;

    ret_code_t err_code = NRF_SUCCESS;
    CRITICAL_REGION_ENTER();
    s_tx_head = next;
    if (!s_tx_active)
    {
        err_code = uart_start_next_tx();
    }
    CRITICAL_REGION_EXIT();
    return err_code;
}

/* Private definitions ------------------------------------------------ */
static ret_code_t uart_start_next_tx(void)
{
    if (s_tx_tail == s_tx_head)
    {
        s_tx_active = false;
        return NRF_SUCCESS;
    }

    uart_tx_item_t * item = &s_tx_queue[s_tx_tail];
    ret_code_t err_code = nrf_libuarte_async_tx(&s_uart, item->data, item->len);
    if (err_code == NRF_SUCCESS)
    {
        s_tx_active = true;
    }
    else
    {
        s_tx_active = false;
    }
    return err_code;
}

static void uart_event_handler(void * context, nrf_libuarte_async_evt_t * p_event)
{
    (void)context;

    switch (p_event->type)
    {
        case NRF_LIBUARTE_ASYNC_EVT_RX_DATA:
            if (s_rx_cb != NULL)
            {
                for (size_t i = 0; i < p_event->data.rxtx.length; ++i)
                {
                    s_rx_cb(p_event->data.rxtx.p_data[i]);
                }
            }
            nrf_libuarte_async_rx_free(&s_uart,
                                       p_event->data.rxtx.p_data,
                                       p_event->data.rxtx.length);
            break;

        case NRF_LIBUARTE_ASYNC_EVT_TX_DONE:
            s_tx_tail = (uint8_t)((s_tx_tail + 1U) % UART_TX_QUEUE_DEPTH);
            s_tx_active = false;
            if (uart_start_next_tx() != NRF_SUCCESS)
            {
                ++s_uart_error_count;
            }
            break;

        case NRF_LIBUARTE_ASYNC_EVT_ERROR:
            ++s_uart_error_count;
            NRF_LOG_WARNING("UARTE hardware error: ERRORSRC=0x%02x total=%lu",
                            p_event->data.errorsrc,
                            (unsigned long)s_uart_error_count);
            break;

        case NRF_LIBUARTE_ASYNC_EVT_OVERRUN_ERROR:
            s_uart_overrun_count += p_event->data.overrun_err.overrun_length;
            NRF_LOG_WARNING("UARTE RX software overrun: lost=%lu total=%lu",
                            (unsigned long)p_event->data.overrun_err.overrun_length,
                            (unsigned long)s_uart_overrun_count);
            break;

        default:
            break;
    }
}

/* End of file -------------------------------------------------------- */
