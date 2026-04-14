/**
 * @file    bsp_usbd.c
 * @brief   BSP layer for USB CDC ACM (Communications Device Class).
 *
 * Handles USB peripheral initialization, CDC ACM class registration,
 * power-event-driven USB enable/disable, and TX/RX primitives.
 *
 * Originally: usb_cdc_acm.c — restructured into the BSP layer.
 */

#include "bsp_usbd.h"

#include <stdlib.h>
#include <stdio.h>

#include "app_usbd.h"
#include "app_usbd_cdc_acm.h"
#include "app_usbd_core.h"
#include "app_usbd_string_desc.h"
#include "app_error.h"
#include "app_timer.h"
#include "sdk_errors.h"
#include "nrf_log.h"
#include "central_io.h"   
#include "boards.h"

/* -------------------------------------------------------------------------
 * Endpoint / interface definitions
 * ---------------------------------------------------------------------- */
#define CDC_ACM_COMM_INTERFACE   0
#define CDC_ACM_COMM_EPIN        NRF_DRV_USBD_EPIN2

#define CDC_ACM_DATA_INTERFACE   1
#define CDC_ACM_DATA_EPIN        NRF_DRV_USBD_EPIN1
#define CDC_ACM_DATA_EPOUT       NRF_DRV_USBD_EPOUT1

/** Use power-detection events to start/stop USBD automatically. */
#define BSP_USBD_USE_POWER_DETECTION  1

/** Number of bytes read per OUT transfer. */
#define BSP_USBD_READ_SIZE  1

/** Period of the internal stub timer (ms). */
#define BSP_USBD_STUB_PERIOD_MS     300
#define BSP_USBD_STUB_TIMER_TICKS   APP_TIMER_TICKS(BSP_USBD_STUB_PERIOD_MS)

/* -------------------------------------------------------------------------
 * Module state
 * ---------------------------------------------------------------------- */
static bool m_usb_connected = false;   /**< USBD is enumerated on the bus.  */
static bool m_port_open     = false;   /**< Host has opened the COM port.   */
static bool m_stub_pending  = false;   /**< Timer fired; TX not yet ACK-ed. */

/** Single-byte RX scratch buffer used for the continuous OUT transfer. */
static char m_rx_buffer[BSP_USBD_READ_SIZE];

static char m_rx_line_buf[64];
static int  m_rx_line_len = 0;

typedef void (*bsp_usbd_rx_line_cb_t)(const char *line);
static bsp_usbd_rx_line_cb_t m_rx_line_cb = NULL;

void bsp_usbd_rx_line_cb_set(bsp_usbd_rx_line_cb_t cb)
{
    m_rx_line_cb = cb;
}

/* -------------------------------------------------------------------------
 * app_timer
 * ---------------------------------------------------------------------- */
APP_TIMER_DEF(m_stub_timer);

/* -------------------------------------------------------------------------
 * Forward declarations (internal)
 * ---------------------------------------------------------------------- */
static void cdc_acm_user_event_handler(app_usbd_class_inst_t const *p_inst,
                                       app_usbd_cdc_acm_user_event_t event);
static void stub_timer_handler(void *p_context);
static void usb_try_send_stub(void);

/* -------------------------------------------------------------------------
 * CDC ACM class instance
 * ---------------------------------------------------------------------- */
APP_USBD_CDC_ACM_GLOBAL_DEF(m_usb_cdc_acm,
                             cdc_acm_user_event_handler,
                             CDC_ACM_COMM_INTERFACE,
                             CDC_ACM_DATA_INTERFACE,
                             CDC_ACM_COMM_EPIN,
                             CDC_ACM_DATA_EPIN,
                             CDC_ACM_DATA_EPOUT,
                             APP_USBD_CDC_COMM_PROTOCOL_AT_V250);

/* -------------------------------------------------------------------------
 * Internal helpers
 * ---------------------------------------------------------------------- */

/** Returns true when a read return code means "no problem, just wait". */
static bool read_result_is_ok(ret_code_t code)
{
    return (code == NRF_SUCCESS)         ||
           (code == NRF_ERROR_IO_PENDING) ||
           (code == NRF_ERROR_BUSY);
}

/**
 * @brief Attempt to transmit the periodic stub message.
 *
 * Called from the timer callback and from TX_DONE (retry after a busy TX).
 * The actual stub body is commented out – remove the comment block once a
 * real payload replaces it.
 */
static void usb_try_send_stub(void)
{
    if (!m_port_open)
    {
        /* Host has not opened the COM port yet — nothing to do. */
        return;
    }

    if (!m_stub_pending)
    {
        /* No stub queued. */
        return;
    }

    m_stub_pending = false;

    /* Demo: blink LED_0 via stub timer to verify 1-second periodic timer works.
     * LED_0 = scanning indicator; when USB port is open it blinks @ 1 Hz.
     * Remove / replace once real payload is ready.
     */
    bsp_board_led_invert(BSP_BOARD_LED_0);
}

/**
 * @brief 1-second timer callback.
 *
 * Arms the pending flag and immediately attempts a send.  If the USB
 * endpoint is busy the send is retried from the TX_DONE event.
 */
static void stub_timer_handler(void *p_context)
{
    UNUSED_PARAMETER(p_context);
    m_stub_pending = true;
    usb_try_send_stub();
}

/* -------------------------------------------------------------------------
 * CDC ACM user event handler
 * ---------------------------------------------------------------------- */
static void cdc_acm_user_event_handler(app_usbd_class_inst_t const *p_inst,
                                       app_usbd_cdc_acm_user_event_t event)
{
    app_usbd_cdc_acm_t const *p_cdc_acm = app_usbd_cdc_acm_class_get(p_inst);
    (void)p_cdc_acm;

    switch (event)
    {
        /* ---- PORT OPEN -------------------------------------------- */
        case APP_USBD_CDC_ACM_USER_EVT_PORT_OPEN:
        {
            NRF_LOG_INFO("USB CDC ACM port opened");
            m_port_open     = true;
            m_usb_connected = true;
            m_stub_pending  = false;

            /* Start the periodic stub timer. */
            ret_code_t timer_ret = app_timer_start(m_stub_timer,
                                                    BSP_USBD_STUB_TIMER_TICKS,
                                                    NULL);
            if (timer_ret != NRF_SUCCESS &&
                timer_ret != NRF_ERROR_INVALID_STATE /* already running */)
            {
                NRF_LOG_ERROR("USB: stub_timer start failed: 0x%08x", timer_ret);
            }

            /* Queue the first OUT (RX) transfer. */
            ret_code_t ret = app_usbd_cdc_acm_read(&m_usb_cdc_acm,
                                                    m_rx_buffer,
                                                    BSP_USBD_READ_SIZE);
            if (!read_result_is_ok(ret))
            {
                NRF_LOG_ERROR("USB: initial read setup failed: 0x%08x", ret);
            }
            break;
        }

        /* ---- PORT CLOSE ------------------------------------------- */
        case APP_USBD_CDC_ACM_USER_EVT_PORT_CLOSE:
        {
            NRF_LOG_INFO("USB CDC ACM port closed");
            m_port_open    = false;
            m_stub_pending = false;

            /* Stop stub timer — ignore error; it may already be stopped. */
            (void)app_timer_stop(m_stub_timer);
            break;
        }

        /* ---- TX DONE ---------------------------------------------- */
        case APP_USBD_CDC_ACM_USER_EVT_TX_DONE:
        {
            NRF_LOG_DEBUG("USB CDC ACM TX done");
            /* Retry any pending stub that failed while the endpoint was busy. */
            usb_try_send_stub();
            break;
        }

        /* ---- RX DONE ---------------------------------------------- */
        case APP_USBD_CDC_ACM_USER_EVT_RX_DONE:
        {
            ret_code_t ret;
            do
            {
                char c = m_rx_buffer[0];

                /* Echo the received byte back to the terminal. */
                if (m_port_open)
                {
                    if (c == '\r' || c == '\n')
                    {
                        /* Print CRLF for terminal newline */
                        bsp_usbd_write((const uint8_t *)"\r\n", 2);
                    }
                    else
                    {
                        app_usbd_cdc_acm_write(&m_usb_cdc_acm, (const uint8_t *)&c, 1);
                    }
                }

                if (c == '\r' || c == '\n')
                {
                    if (m_rx_line_len > 0)
                    {
                        m_rx_line_buf[m_rx_line_len] = '\0';
                        if (m_rx_line_cb)
                        {
                            m_rx_line_cb(m_rx_line_buf);
                        }
                        m_rx_line_len = 0;
                    }
                }
                else if (c == '\b' || c == 0x7F) /* Backspace */
                {
                    if (m_rx_line_len > 0)
                    {
                        m_rx_line_len--;
                    }
                }
                else if (m_rx_line_len < sizeof(m_rx_line_buf) - 1)
                {
                    m_rx_line_buf[m_rx_line_len++] = c;
                }

                /* Read next byte synchronously (if available in internal FIFO) or re-arm OUT transfer (if empty) */
                ret = app_usbd_cdc_acm_read(&m_usb_cdc_acm, m_rx_buffer, BSP_USBD_READ_SIZE);

                if (!read_result_is_ok(ret))
                {
                    NRF_LOG_ERROR("USB: re-read failed: 0x%08x", ret);
                    break;
                }
            }
            while (ret == NRF_SUCCESS);
            break;
        }

        default:
            break;
    }
}

/* -------------------------------------------------------------------------
 * USBD general event handler
 * ---------------------------------------------------------------------- */
static void usbd_event_handler(app_usbd_event_type_t event)
{
    switch (event)
    {
        case APP_USBD_EVT_DRV_SUSPEND:
            NRF_LOG_INFO("USB suspended");
            m_usb_connected = false;
            break;

        case APP_USBD_EVT_DRV_RESUME:
            NRF_LOG_INFO("USB resumed");
            m_usb_connected = true;
            break;

        case APP_USBD_EVT_STARTED:
            NRF_LOG_INFO("USB USBD started");
            break;

        case APP_USBD_EVT_STOPPED:
            app_usbd_disable();
            NRF_LOG_INFO("USB USBD stopped");
            m_usb_connected = false;
            break;

        case APP_USBD_EVT_POWER_DETECTED:
            NRF_LOG_INFO("USB power detected");
            if (!nrf_drv_usbd_is_enabled())
            {
                app_usbd_enable();
            }
            break;

        case APP_USBD_EVT_POWER_REMOVED:
            NRF_LOG_INFO("USB power removed");
            app_usbd_stop();
            m_usb_connected = false;
            break;

        case APP_USBD_EVT_POWER_READY:
            NRF_LOG_INFO("USB power ready - starting USBD");
            app_usbd_start();
            break;

        default:
            break;
    }
}

/* -------------------------------------------------------------------------
 * Public API implementation
 * ---------------------------------------------------------------------- */

ret_code_t bsp_usbd_init(void)
{
    ret_code_t ret;

    /* Create the periodic stub timer. */
    ret = app_timer_create(&m_stub_timer, APP_TIMER_MODE_REPEATED, stub_timer_handler);
    if (ret != NRF_SUCCESS)
    {
        NRF_LOG_ERROR("USB: app_timer_create failed: 0x%08x", ret);
        return ret;
    }

    /* Initialize the USBD stack. */
    static const app_usbd_config_t usbd_config = {
        .ev_state_proc = usbd_event_handler
    };

    NRF_LOG_INFO("USB: app_usbd_init");
    ret = app_usbd_init(&usbd_config);
    if (ret != NRF_SUCCESS)
    {
        NRF_LOG_ERROR("USB: app_usbd_init failed: 0x%08x", ret);
        return ret;
    }

    /* Register the CDC ACM class. */
    NRF_LOG_INFO("USB: app_usbd_class_append");
    app_usbd_class_inst_t const *p_cdc_acm_class =
        app_usbd_cdc_acm_class_inst_get(&m_usb_cdc_acm);
    ret = app_usbd_class_append(p_cdc_acm_class);
    if (ret != NRF_SUCCESS)
    {
        NRF_LOG_ERROR("USB: app_usbd_class_append failed: 0x%08x", ret);
        return ret;
    }

    /* Start USBD — either via power events or directly. */
#if BSP_USBD_USE_POWER_DETECTION
    NRF_LOG_INFO("USB: app_usbd_power_events_enable");
    ret = app_usbd_power_events_enable();
    if (ret != NRF_SUCCESS)
    {
        NRF_LOG_WARNING("USB: power events enable failed (0x%08x), using direct start", ret);
        app_usbd_enable();
        app_usbd_start();
    }
#else
    app_usbd_enable();
    app_usbd_start();
#endif

    NRF_LOG_INFO("USB CDC ACM initialized successfully");
    return NRF_SUCCESS;
}

bool bsp_usbd_process(void)
{
    return app_usbd_event_queue_process();
}

bool bsp_usbd_is_connected(void)
{
    return m_usb_connected && m_port_open;
}

ret_code_t bsp_usbd_write(const uint8_t *p_data, size_t length)
{
    if (!m_port_open || p_data == NULL || length == 0)
    {
        return NRF_ERROR_INVALID_STATE;
    }
    ret_code_t ret = app_usbd_cdc_acm_write(&m_usb_cdc_acm, p_data, length);
    if (ret != NRF_SUCCESS && ret != NRF_ERROR_BUSY && ret != NRF_ERROR_IO_PENDING)
    {
        NRF_LOG_WARNING("USB TX: write(%u bytes) failed: 0x%08x", (unsigned)length, ret);
    }
    return ret;
}

ret_code_t bsp_usbd_putchar(char character)
{
    if (!m_port_open)
    {
        return NRF_ERROR_INVALID_STATE;
    }
    return app_usbd_cdc_acm_write(&m_usb_cdc_acm, (const uint8_t *)&character, 1);
}

ret_code_t bsp_usbd_getchar(char *p_character)
{
    if (!m_port_open || p_character == NULL)
    {
        return NRF_ERROR_INVALID_STATE;
    }

    size_t size = app_usbd_cdc_acm_bytes_stored(&m_usb_cdc_acm);
    if (size == 0)
    {
        return NRF_ERROR_NOT_FOUND;
    }

    return app_usbd_cdc_acm_read(&m_usb_cdc_acm, (uint8_t *)p_character, 1);
}
