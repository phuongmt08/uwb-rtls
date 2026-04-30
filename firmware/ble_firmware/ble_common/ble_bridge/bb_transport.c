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

/* Private function prototypes ---------------------------------------- */
// static void on_rx_byte(uint8_t byte);
static ret_code_t bb_transport_send_serial(uint8_t const * p_data, uint16_t length);
static ret_code_t bb_transport_send_ble(uint8_t const * p_data, uint16_t length);

/* Function definitions ----------------------------------------------- */
ret_code_t bb_transport_init(uint8_t * p_payload_buf, uint16_t * p_payload_len, uint16_t max_len, bb_transport_state_transition_cb_t cb)
{
    // Lưu giữ địa chỉ buffer do Router cấp phát để chứa data protobuf
    p_protobuf_buffer = p_payload_buf;
    p_protobuf_len = p_payload_len;
    m_max_payload_len = max_len;
    
    m_rx_cb = cb;
    m_is_packet_ready = false;
    
    // Khởi tạo state machine HDLC
    hdlc_parser_init(&m_hdlc_parser);
    
    ret_code_t err_code = NRF_SUCCESS;

#if defined(BLE_PERIPHERAL)
    err_code = bsp_uart_init(on_rx_byte);
#elif defined(BLE_CENTRAL)
    NRF_LOG_INFO("Initializing USB CDC ACM for Central...");
    err_code = bsp_usbd_init(on_rx_byte); // ─ code api cho central sau
#endif

    return err_code;
}

void bb_transport_process(void)
{
    #if defined(BLE_PERIPHERAL)
    // Cỗ máy trạng thái giờ đây chạy tự động dựa vào event callback (on_rx_byte)
    // được ngắt từ UART (APP_UART_DATA_READY) gọi lên.
    // Nên hàm này có thể bỏ trống, hoặc có thể dùng xử lý các tác vụ delay timeout nếu cần sau này.
    bsp_uart_read_byte();
    #endif
}

bool bb_transport_is_packet_ready(void)
{
    // Trả về cờ báo hiệu
    return m_is_packet_ready;
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
    // Tương tự cho BLE Central nếu có
    // return ble_central_send_data(p_data, length);
    return NRF_SUCCESS;
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
    // Nếu buffer hiện tại chưa được Router xử lý xong thì drop byte mới
    // để đảm bảo không bị ghi đè dữ liệu (nguyên tắc Zero-Copy)
    // if (m_is_packet_ready || p_protobuf_buffer == NULL || p_protobuf_len == NULL) {
    //     return;
    // }

    hdlc_data_chunk_t rx_chunk;
    // NRF_LOG_INFO("Received byte: 0x%02X, count=%u\n", byte, ++count_data);
    // NRF_LOG_INFO("Received byte: 0x%02X, count=%u\n", byte, ++count_data);

    if (hdlc_parse_byte(&m_hdlc_parser, byte, &rx_chunk)) 

    {   
        // Copy data payload sang buffer do Router truyền xuống
        if (rx_chunk.len <= m_max_payload_len) 
        {
            *p_protobuf_len = rx_chunk.len;
            if (rx_chunk.len > 0) 
            {
                memcpy(p_protobuf_buffer, rx_chunk.data, rx_chunk.len);
            }
            m_is_packet_ready = true;
            
            // Gọi callback chuyển đổi State cho bb_router
            if (m_rx_cb != NULL) 
            {
                m_rx_cb(); 
            }
        }
    }
}

/* End of file -------------------------------------------------------- */
