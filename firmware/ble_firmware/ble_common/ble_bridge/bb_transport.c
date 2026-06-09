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
static uint16_t * p_protobuf_len = NULL;
static uint16_t m_max_payload_len = 0;

static bool m_is_packet_ready = false;
static bb_transport_state_transition_cb_t m_rx_cb = NULL;

static hdlc_parser_t m_hdlc_parser;

#define RX_RING_BUFFER_SIZE 512
static volatile uint16_t m_rx_ring_head = 0;
static volatile uint16_t m_rx_ring_tail = 0;
static uint8_t m_rx_ring_buf[RX_RING_BUFFER_SIZE];

static void rx_ring_push(uint8_t byte)
{
    uint16_t next = (m_rx_ring_head + 1) % RX_RING_BUFFER_SIZE;
    if (next != m_rx_ring_tail)
    {
        m_rx_ring_buf[m_rx_ring_head] = byte;
        m_rx_ring_head = next;
    }
}

static bool rx_ring_pop(uint8_t *p_byte)
{
    uint16_t tail = m_rx_ring_tail;
    if (m_rx_ring_head == tail)
    {
        return false;
    }
    *p_byte = m_rx_ring_buf[tail];
    m_rx_ring_tail = (tail + 1) % RX_RING_BUFFER_SIZE;
    return true;
}

/* ---- BLE Packet Queue (for raw protobuf packets received via BLE) ---- */
#define BLE_PKT_QUEUE_SIZE  4
#define BLE_PKT_MAX_LEN     256

typedef struct {
    uint8_t data[BLE_PKT_MAX_LEN];
    uint16_t len;
} ble_pkt_t;

static ble_pkt_t m_ble_pkt_queue[BLE_PKT_QUEUE_SIZE];
static volatile uint8_t m_ble_pkt_head = 0;
static volatile uint8_t m_ble_pkt_tail = 0;

static bool ble_pkt_push(uint8_t const * p_data, uint16_t length)
{
    uint8_t next = (m_ble_pkt_head + 1) % BLE_PKT_QUEUE_SIZE;
    if (next == m_ble_pkt_tail)
    {
        return false; // Queue full
    }
    memcpy(m_ble_pkt_queue[m_ble_pkt_head].data, p_data, length);
    m_ble_pkt_queue[m_ble_pkt_head].len = length;
    m_ble_pkt_head = next;
    return true;
}

static bool ble_pkt_pop(uint8_t * p_data, uint16_t * p_length)
{
    if (m_ble_pkt_head == m_ble_pkt_tail)
    {
        return false; // Queue empty
    }
    *p_length = m_ble_pkt_queue[m_ble_pkt_tail].len;
    memcpy(p_data, m_ble_pkt_queue[m_ble_pkt_tail].data, *p_length);
    m_ble_pkt_tail = (m_ble_pkt_tail + 1) % BLE_PKT_QUEUE_SIZE;
    return true;
}

/* Private function prototypes ---------------------------------------- */
// static void on_rx_byte(uint8_t byte);
static ret_code_t bb_transport_send_serial(uint8_t const * p_data, uint16_t length);
static ret_code_t bb_transport_send_ble(uint8_t const * p_data, uint16_t length);
static void on_rx_ble(uint8_t const * p_data, uint16_t length);
static void on_rx_byte(uint8_t byte);

/* Function definitions ----------------------------------------------- */
ret_code_t bb_transport_init(uint8_t * p_payload_buf, uint16_t * p_payload_len, uint16_t max_len, bb_transport_state_transition_cb_t cb)
{
    // Lưu giữ địa chỉ buffer do Router cấp phát để chứa data protobuf
    p_protobuf_buffer = p_payload_buf;
    p_protobuf_len = p_payload_len;
    m_max_payload_len = max_len;
    
    m_rx_cb = cb;
    m_is_packet_ready = false;
    
    m_rx_ring_head = 0;
    m_rx_ring_tail = 0;
    
    m_ble_pkt_head = 0;
    m_ble_pkt_tail = 0;
    
    // Khởi tạo state machine HDLC
    hdlc_parser_init(&m_hdlc_parser);
    
    ret_code_t err_code = NRF_SUCCESS;

#if defined(BLE_PERIPHERAL)
    err_code = bsp_uart_init(on_rx_byte);
    
    // Đăng ký hàm nhận byte cho BLE
    ble_peripheral_rx_cb_register(on_rx_ble);
#elif defined(BLE_CENTRAL)
    NRF_LOG_INFO("Initializing USB CDC ACM for Central...");
    err_code = bsp_usbd_init(on_rx_byte); // ─ code api cho central sau
    
    // Register BLE receive callback for Central
    ble_central_rx_cb_register(on_rx_ble);
#endif

    return err_code;
}

void bb_transport_process(void)
{
    #if defined(BLE_PERIPHERAL)
    bsp_uart_read_byte();
    #endif

    /* 1. Drain serial ring buffer (UART/USB HDLC bytes) */
    uint8_t byte;
    while (!m_is_packet_ready && rx_ring_pop(&byte))
    {
        if (p_protobuf_buffer != NULL && p_protobuf_len != NULL)
        {
            hdlc_data_chunk_t rx_chunk;
            if (hdlc_parse_byte(&m_hdlc_parser, byte, &rx_chunk))
            {
                if (rx_chunk.len <= m_max_payload_len)
                {
                    *p_protobuf_len = rx_chunk.len;
                    if (rx_chunk.len > 0)
                    {
                        memcpy(p_protobuf_buffer, rx_chunk.data, rx_chunk.len);
                    }
                    m_is_packet_ready = true;

                    if (m_rx_cb != NULL)
                    {
                        m_rx_cb();
                    }
                }
            }
        }
    }

    /* 2. Drain BLE packet queue (raw protobuf packets from BLE link) */
    if (!m_is_packet_ready && p_protobuf_buffer != NULL && p_protobuf_len != NULL)
    {
        uint16_t pkt_len;
        if (ble_pkt_pop(p_protobuf_buffer, &pkt_len))
        {
            *p_protobuf_len = pkt_len;
            m_is_packet_ready = true;

            if (m_rx_cb != NULL)
            {
                m_rx_cb();
            }
        }
    }
}

bool bb_transport_is_packet_ready(void)
{
    // Trả về cờ báo hiệu
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
        return bb_transport_send_ble(p_data, length);
        NRF_LOG_INFO("Send packet via BLE to Central");
    }
    return NRF_ERROR_INVALID_PARAM;
}

/* Private definitions ------------------------------------------------ */
static ret_code_t bb_transport_send_serial(uint8_t const * p_data, uint16_t length)
{
    // 1. Tạo buffer tmp để làm frame HDLC
    uint8_t tx_buf[HDLC_FRAME_MAX_LEN];
    
    // 2. Chèn struct header, checksum, đóng gói (dùng hàm build của hdlc.c)
    int frame_size = hdlc_build(tx_buf, sizeof(tx_buf), 0x00, p_data, length);
    
    if (frame_size > 0) {
        // 3. Đẩy mảng byte đã đóng gói xuống UART nếu là Peripheral
#if defined(BLE_PERIPHERAL)
        ret_code_t err_code = bsp_uart_transmit(tx_buf, (uint16_t)frame_size);
        NRF_LOG_INFO("bb_transport: Transmitted %d bytes over UART", frame_size);
        if (err_code == NRF_SUCCESS) 
        {
            return NRF_SUCCESS;
        }
        return err_code;
#elif defined(BLE_CENTRAL)
        // bsp_serial_central_transmit(tx_buf, frame_size); // Đợi code api cho central sau
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
    // Gọi hàm truyền data qua BLE peripheral (NUS/GATT custom Service)
    return ble_peripheral_send_data(p_data, length);
#elif defined(BLE_CENTRAL)
    return app_ble_central_send_data(p_data, length);
#else
    return NRF_ERROR_NOT_SUPPORTED;
#endif
}

/**
 * @brief Hàm callback được gọi từ bsp_uart mỗi khi có 1 byte nhận được
 */
uint32_t count_data = 0;
void on_rx_byte(uint8_t byte)
{
    rx_ring_push(byte);
}

/**
 * @brief Hàm callback được gọi khi nhận raw protobuf packet từ BLE link.
 *        Đẩy vào BLE packet queue thay vì copy thẳng vào protobuf_buffer
 *        để tránh drop gói khi m_is_packet_ready == true.
 */
static void on_rx_ble(uint8_t const * p_data, uint16_t length)
{
    if (p_data == NULL || length == 0 || length > BLE_PKT_MAX_LEN) 
    {
        return;
    }

    if (!ble_pkt_push(p_data, length))
    {
        NRF_LOG_WARNING("BLE RX queue full, dropping packet len=%u", length);
    }
}

/* End of file -------------------------------------------------------- */

