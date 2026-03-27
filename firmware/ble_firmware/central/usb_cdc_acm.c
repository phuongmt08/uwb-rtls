/**
 * @file usb_cdc_acm.c
 * @brief USB CDC ACM (Communications Device Class - Abstract Control Model) handler implementation
 */

#include "usb_cdc_acm.h"
#include "app_usbd.h"
#include "app_usbd_cdc_acm.h"
#include "app_usbd_core.h"
#include "app_usbd_string_desc.h"
#include "app_error.h"
#include "nrf_log.h"

// Endpoint and interface definitions (must be before macro)
#define CDC_ACM_COMM_INTERFACE  0
#define CDC_ACM_COMM_EPIN       NRF_DRV_USBD_EPIN2

#define CDC_ACM_DATA_INTERFACE  1
#define CDC_ACM_DATA_EPIN       NRF_DRV_USBD_EPIN1
#define CDC_ACM_DATA_EPOUT      NRF_DRV_USBD_EPOUT1

#define READ_SIZE 1

// USB state flags
static bool m_usb_connected = false;
static bool m_port_open = false;

// Rx buffer
static char m_rx_buffer[READ_SIZE];

// Forward declaration
static void usb_cdc_acm_user_event_handler(app_usbd_class_inst_t const * p_inst,
                                           app_usbd_cdc_acm_user_event_t event);

// CDC ACM class instance
APP_USBD_CDC_ACM_GLOBAL_DEF(m_usb_cdc_acm, 
                             usb_cdc_acm_user_event_handler,
                             CDC_ACM_COMM_INTERFACE,
                             CDC_ACM_DATA_INTERFACE,
                             CDC_ACM_COMM_EPIN,
                             CDC_ACM_DATA_EPIN,
                             CDC_ACM_DATA_EPOUT,
                             APP_USBD_CDC_COMM_PROTOCOL_AT_V250);

/**
 * @brief USB CDC ACM user event handler
 */
static void usb_cdc_acm_user_event_handler(app_usbd_class_inst_t const * p_inst,
                                           app_usbd_cdc_acm_user_event_t event)
{
    app_usbd_cdc_acm_t const * p_cdc_acm = app_usbd_cdc_acm_class_get(p_inst);

    switch (event)
    {
        case APP_USBD_CDC_ACM_USER_EVT_PORT_OPEN:
        {
            m_port_open = true;
            m_usb_connected = true;
            NRF_LOG_INFO("USB CDC ACM port opened");

            // Setup first transfer for reading
            ret_code_t ret = app_usbd_cdc_acm_read(&m_usb_cdc_acm,
                                                    m_rx_buffer,
                                                    READ_SIZE);
            APP_ERROR_CHECK(ret);
            break;
        }

        case APP_USBD_CDC_ACM_USER_EVT_PORT_CLOSE:
        {
            m_port_open = false;
            NRF_LOG_INFO("USB CDC ACM port closed");
            break;
        }

        case APP_USBD_CDC_ACM_USER_EVT_TX_DONE:
        {
            NRF_LOG_DEBUG("USB CDC ACM TX done");
            break;
        }

        case APP_USBD_CDC_ACM_USER_EVT_RX_DONE:
        {
            ret_code_t ret;
            
            // Log received data for debugging
            NRF_LOG_DEBUG("USB CDC ACM RX done, bytes stored: %d", 
                          app_usbd_cdc_acm_bytes_stored(p_cdc_acm));
            
            // Continue reading
            ret = app_usbd_cdc_acm_read(&m_usb_cdc_acm,
                                        m_rx_buffer,
                                        READ_SIZE);
            if (ret != NRF_SUCCESS)
            {
                NRF_LOG_ERROR("Failed to set up next USB read: %d", ret);
            }
            break;
        }

        default:
            break;
    }
}

/**
 * @brief USBD general event handler
 */
static void usbd_user_event_handler(app_usbd_event_type_t event)
{
    switch (event)
    {
        case APP_USBD_EVT_DRV_SUSPEND:
        {
            NRF_LOG_INFO("USB suspended");
            m_usb_connected = false;
            break;
        }

        case APP_USBD_EVT_DRV_RESUME:
        {
            NRF_LOG_INFO("USB resumed");
            m_usb_connected = true;
            break;
        }

        case APP_USBD_EVT_STARTED:
        {
            NRF_LOG_INFO("USB started");
            break;
        }

        case APP_USBD_EVT_STOPPED:
        {
            app_usbd_disable();
            NRF_LOG_INFO("USB stopped");
            m_usb_connected = false;
            break;
        }

        case APP_USBD_EVT_POWER_DETECTED:
        {
            NRF_LOG_INFO("USB power detected");
            if (!nrf_drv_usbd_is_enabled())
            {
                app_usbd_enable();
            }
            break;
        }

        case APP_USBD_EVT_POWER_REMOVED:
        {
            NRF_LOG_INFO("USB power removed");
            app_usbd_stop();
            m_usb_connected = false;
            break;
        }

        case APP_USBD_EVT_POWER_READY:
        {
            NRF_LOG_INFO("USB ready");
            app_usbd_start();
            break;
        }

        default:
            break;
    }
}

ret_code_t usb_cdc_acm_init(void)
{
    ret_code_t ret;

    // Initialize USBD
    static const app_usbd_config_t usbd_config = {
        .ev_state_proc = usbd_user_event_handler
    };

    ret = app_usbd_init(&usbd_config);
    if (ret != NRF_SUCCESS)
    {
        NRF_LOG_ERROR("Failed to initialize USBD: %d", ret);
        return ret;
    }

    // Append CDC ACM class
    app_usbd_class_inst_t const * p_cdc_acm_class = app_usbd_cdc_acm_class_inst_get(&m_usb_cdc_acm);
    ret = app_usbd_class_append(p_cdc_acm_class);
    if (ret != NRF_SUCCESS)
    {
        NRF_LOG_ERROR("Failed to append CDC ACM class: %d", ret);
        return ret;
    }

    // Enable USB power events
    ret = app_usbd_power_events_enable();
    if (ret != NRF_SUCCESS)
    {
        NRF_LOG_ERROR("Failed to enable power events: %d", ret);
        return ret;
    }

    NRF_LOG_INFO("USB CDC ACM initialized successfully");
    return NRF_SUCCESS;
}

ret_code_t usb_cdc_acm_putchar(char character)
{
    if (!m_port_open)
    {
        return NRF_ERROR_INVALID_STATE;
    }

    return app_usbd_cdc_acm_write(&m_usb_cdc_acm, (const uint8_t *)&character, 1);
}

ret_code_t usb_cdc_acm_getchar(char *character)
{
    if (!m_port_open || character == NULL)
    {
        return NRF_ERROR_INVALID_STATE;
    }

    size_t size = app_usbd_cdc_acm_bytes_stored(&m_usb_cdc_acm);
    if (size == 0)
    {
        return NRF_ERROR_NOT_FOUND;
    }

    return app_usbd_cdc_acm_read(&m_usb_cdc_acm, 
                                  (uint8_t *)character, 
                                  1);
}

ret_code_t usb_cdc_acm_write(const uint8_t *data, size_t length)
{
    if (!m_port_open || data == NULL || length == 0)
    {
        return NRF_ERROR_INVALID_STATE;
    }

    return app_usbd_cdc_acm_write(&m_usb_cdc_acm, data, length);
}

bool usb_cdc_acm_is_connected(void)
{
    return m_usb_connected && m_port_open;
}

bool usb_cdc_acm_process(void)
{
    return app_usbd_event_queue_process();
}
