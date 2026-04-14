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
#include "../../peripheral/bsp_uart.h"
#include "../../peripheral/sys_log.h"

/* Private defines ---------------------------------------------------- */
/* Private enumerate/structure ---------------------------------------- */
/* Private macros ----------------------------------------------------- */
/* Public variables --------------------------------------------------- */
/* Private variables -------------------------------------------------- */
static uint8_t * m_p_rx_payload_buf = NULL;
static uint16_t * m_p_rx_payload_len = NULL;
static uint16_t m_max_payload_len = 0;

static bool m_is_packet_ready = false;
static bb_transport_rx_cb_t m_rx_cb = NULL;

static hdlc_parser_t m_hdlc_parser;

/* Private function prototypes ---------------------------------------- */
static void on_uart_rx_byte(uint8_t byte);
static ret_code_t bb_transport_send_serial(uint8_t const * p_data, uint16_t length);
static ret_code_t bb_transport_send_ble(uint8_t const * p_data, uint16_t length);

/* Function definitions ----------------------------------------------- */
ret_code_t bb_transport_init(uint8_t * p_payload_buf, uint16_t * p_payload_len, uint16_t max_len, bb_transport_rx_cb_t cb)
{
    // Lưu giữ địa chỉ buffer do Router cấp phát để chứa data protobuf
    m_p_rx_payload_buf = p_payload_buf;
    m_p_rx_payload_len = p_payload_len;
    m_max_payload_len = max_len;
    
    m_rx_cb = cb;
    m_is_packet_ready = false;
    
    // Khởi tạo state machine HDLC
    hdlc_parser_init(&m_hdlc_parser);
    
    // Đăng ký callback nhận byte với UART layer
#if defined(BLE_PERIPHREAL)
    bsp_uart_init(on_uart_rx_byte);
#elif defined(BLE_CENTRAL)
    // bsp_usb_init(on_uart_rx_byte); // Đợi code api cho central sau
#endif

    return NRF_SUCCESS;
}

void bb_transport_process(void)
{
    // Cỗ máy trạng thái giờ đây chạy tự động dựa vào event callback (on_uart_rx_byte)
    // được ngắt từ UART (APP_UART_DATA_READY) gọi lên.
    // Nên hàm này có thể bỏ trống, hoặc có thể dùng xử lý các tác vụ delay timeout nếu cần sau này.
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
#if defined(BLE_PERIPHREAL)
        if (bsp_uart_transmit(tx_buf, (uint16_t)frame_size)) 
        {
            return NRF_SUCCESS;
        }
        return NRF_ERROR_INTERNAL;
#elif defined(BLE_CENTRAL)
        // bsp_serial_central_transmit(tx_buf, frame_size); // Đợi code api cho central sau
        return NRF_SUCCESS;
#else
        return NRF_ERROR_NOT_SUPPORTED;
#endif
    }
    
    return NRF_ERROR_INTERNAL;
}

static ret_code_t bb_transport_send_ble(uint8_t const * p_data, uint16_t length)
{
    // Gửi byte thuần qua BLE
    // ble_nus_data_send(...)
    return NRF_SUCCESS;
}

/**
 * @brief Hàm callback được gọi từ bsp_uart mỗi khi có 1 byte nhận được
 */
static void on_uart_rx_byte(uint8_t byte)
{
    // Nếu buffer hiện tại chưa được Router xử lý xong thì drop byte mới
    // để đảm bảo không bị ghi đè dữ liệu (nguyên tắc Zero-Copy)
    if (m_is_packet_ready || m_p_rx_payload_buf == NULL || m_p_rx_payload_len == NULL) {
        return;
    }

    hdlc_data_chunk_t rx_chunk;
    
    if (hdlc_parse_byte(&m_hdlc_parser, byte, &rx_chunk)) 
    {   
        // Copy data payload sang buffer do Router truyền xuống
        if (rx_chunk.len <= m_max_payload_len) 
        {
            *m_p_rx_payload_len = rx_chunk.len;
            if (rx_chunk.len > 0) 
            {
                memcpy(m_p_rx_payload_buf, rx_chunk.data, rx_chunk.len);
            }
            m_is_packet_ready = true;
            
            NRF_LOG_INFO("Received HDLC payload: len=%u", rx_chunk.len);

            // Gọi callback chuyển đổi State cho bb_router
            if (m_rx_cb != NULL) 
            {
                m_rx_cb(); 
            }
        }
    }
}

/* End of file -------------------------------------------------------- */
