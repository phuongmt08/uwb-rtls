/**
 * @file       bb_transport.c
 * @copyright  [Your Copyright]
 * @license    [Your License]
 * @version    1.0.0
 * @date       2026-04-08
 * @author     Dong Son
 *
 * @brief      
 */
/* Includes ----------------------------------------------------------- */
#include <string.h>
#include <stddef.h>
#include "bb_transport.h"
#include "hdlc.h"
#include "logger.h"
#if defined(BLE_PERIPHERAL)
#include "../../peripheral/bsp_uart.h"
#include "../../peripheral/ble_peripheral.h"
#elif defined(BLE_CENTRAL)
#include "../../central/bsp/bsp_usbd.h"
#include "../../central/app/app_ble_central.h"
#endif

/* Private defines ---------------------------------------------------- */
/* Private enumerate/structure ---------------------------------------- */
/* Private macros ----------------------------------------------------- */
/* Public variables --------------------------------------------------- */
/* Private variables -------------------------------------------------- */
static uint8_t * p_protobuf_buffer = NULL;
static bb_packet_source_t m_rx_source = BB_SOURCE_SERIAL;
static uint16_t * p_protobuf_len = NULL;
static uint16_t m_max_payload_len = 0;

static bool m_is_packet_ready = false;
static bb_transport_state_transition_cb_t m_rx_cb = NULL;

static hdlc_parser_t m_hdlc_parser;

/* Private function prototypes ---------------------------------------- */
// static void on_rx_byte(uint8_t byte);
static ret_code_t bb_transport_send_serial(uint8_t const * p_data, uint16_t length);
static ret_code_t bb_transport_send_ble(uint8_t const * p_data, uint16_t length);
static ret_code_t bb_transport_send_ble_broadcast(uint8_t const * p_data, uint16_t length);
static void on_rx_ble(uint8_t const * p_data, uint16_t length);
static void on_rx_byte(uint8_t byte);

/* Function definitions ----------------------------------------------- */
ret_code_t bb_transport_init(uint8_t * p_payload_buf, uint16_t * p_payload_len, uint16_t max_len, bb_transport_state_transition_cb_t cb)
{
    // Keep the router-owned protobuf payload buffer.
    p_protobuf_buffer = p_payload_buf;
    p_protobuf_len = p_payload_len;
    m_max_payload_len = max_len;
    
    m_rx_cb = cb;
    m_is_packet_ready = false;
    
    // Initialize the HDLC parser state machine.
    hdlc_parser_init(&m_hdlc_parser);
    
    ret_code_t err_code = NRF_SUCCESS;

#if defined(BLE_PERIPHERAL)
    err_code = bsp_uart_init(on_rx_byte);
    
    // Register the BLE receive callback.
    ble_peripheral_rx_cb_register(on_rx_ble);
#elif defined(BLE_CENTRAL)
    NRF_LOG_INFO("Initializing USB CDC ACM for Central...");
    err_code = bsp_usbd_init(on_rx_byte); // Central serial I/O is handled by USB CDC ACM.
    
    // Register BLE receive callback for Central
    ble_central_rx_cb_register(on_rx_ble);
#endif

    return err_code;
}

void bb_transport_process(void)
{
    #if defined(BLE_PERIPHERAL)
    // Peripheral transport is event driven by UART RX callbacks; keep this hook for polling/timeout work.
    bsp_uart_read_byte();
    #endif
}

bool bb_transport_is_packet_ready(void)
{
    // Return the packet-ready flag consumed by the router.
    return m_is_packet_ready;
}

void bb_transport_clear_packet_ready(void)
{
    m_is_packet_ready = false;
}

ret_code_t bb_transport_send_data(uint8_t const * p_data, uint16_t length, bb_packet_source_t tx_source)
{
    if (tx_source == BB_SOURCE_SERIAL) 
    {
        return bb_transport_send_serial(p_data, length);
    } 
    else if (tx_source == BB_SOURCE_BLE) 
    {
        NRF_LOG_INFO("Send packet via BLE link");
        return bb_transport_send_ble(p_data, length);
    }
    else if (tx_source == BB_SOURCE_BLE_BROADCAST)
    {
        NRF_LOG_INFO("Send packet via BLE broadcast");
        return bb_transport_send_ble_broadcast(p_data, length);
    }
    return NRF_ERROR_INVALID_PARAM;
}

/* Private definitions ------------------------------------------------ */
static ret_code_t bb_transport_send_serial(uint8_t const * p_data, uint16_t length)
{
    // Build a temporary HDLC frame.
    uint8_t tx_buf[HDLC_FRAME_MAX_LEN];
    
    // Add the HDLC header/checksum and encode the frame.
    int frame_size = hdlc_build(tx_buf, sizeof(tx_buf), 0x00, p_data, length);
    
    if (frame_size > 0) {
        // Send the framed bytes through the peripheral UART.
#if defined(BLE_PERIPHERAL)
        ret_code_t err_code = bsp_uart_transmit(tx_buf, (uint16_t)frame_size);
        NRF_LOG_INFO("bb_transport: Transmitted %d bytes over UART", frame_size);
        if (err_code == NRF_SUCCESS) 
        {
            return NRF_SUCCESS;
        }
        return err_code;
#elif defined(BLE_CENTRAL)
        // Central serial output goes through USB CDC ACM.
        ret_code_t err_code = bsp_usbd_write(tx_buf, (size_t)frame_size);
        if (err_code == NRF_SUCCESS) 
        {
        return NRF_SUCCESS;
        }
        return err_code;
#else
        return NRF_ERROR_NOT_SUPPORTED;
#endif
    }
    
    return NRF_ERROR_INTERNAL;
}

static ret_code_t bb_transport_send_ble(uint8_t const * p_data, uint16_t length)
{
#if defined(BLE_PERIPHERAL)
    // Send raw protobuf over the active BLE link.
    return ble_peripheral_send_data(p_data, length);
#elif defined(BLE_CENTRAL)
    return app_ble_central_send_data(p_data, length);
#else
    return NRF_ERROR_NOT_SUPPORTED;
#endif
}

static ret_code_t bb_transport_send_ble_broadcast(uint8_t const * p_data, uint16_t length)
{
#if defined(BLE_PERIPHERAL)
    return ble_peripheral_broadcast_send(p_data, length);
#elif defined(BLE_CENTRAL)
    return app_ble_central_broadcast_send(p_data, length);
#else
    return NRF_ERROR_NOT_SUPPORTED;
#endif
}

/**
 * @brief Callback invoked by bsp_uart for each received byte.
 */
uint32_t count_data = 0;
void on_rx_byte(uint8_t byte)
{
    // Drop new bytes while the router still owns the current buffer.
    if (m_is_packet_ready || p_protobuf_buffer == NULL || p_protobuf_len == NULL) {
        return;
    }

    hdlc_data_chunk_t rx_chunk;
    // NRF_LOG_INFO("Received byte: 0x%02X, count=%u\n", byte, ++count_data);

    if (hdlc_parse_byte(&m_hdlc_parser, byte, &rx_chunk)) 

    {   
        // Copy the decoded payload into the router-owned buffer.
        if (rx_chunk.len <= m_max_payload_len) 
        {
            m_rx_source = BB_SOURCE_SERIAL;
            *p_protobuf_len = rx_chunk.len;
            if (rx_chunk.len > 0) 
            {
                memcpy(p_protobuf_buffer, rx_chunk.data, rx_chunk.len);
            }
            m_is_packet_ready = true;

            // Notify the router that a complete protobuf payload is ready.
            if (m_rx_cb != NULL) 
            {
                m_rx_cb(); 
            }
        }
    }
}

static void on_rx_ble(uint8_t const * p_data, uint16_t length)
{
    if (p_data == NULL || length == 0 || length > m_max_payload_len) 
    {
        return;
    }

    if (m_is_packet_ready)
    {
        return;
    }

    if (p_protobuf_buffer != NULL && p_protobuf_len != NULL) 
    {
        m_rx_source = BB_SOURCE_BLE;
        memcpy(p_protobuf_buffer, p_data, length);
        *p_protobuf_len = length;
        m_is_packet_ready = true;

        if (m_rx_cb != NULL) 
        {
            m_rx_cb(); 
        }
    }
}

bb_packet_source_t bb_transport_get_rx_source(void)
{
    return m_rx_source;
}

/* End of file -------------------------------------------------------- */
