/**
 * @file       bb_transport.h
 * @copyright  [Your Copyright]
 * @license    [Your License]
 * @version    1.0.0
 * @date       2026-04-08
 * @author     Dong Son
 *
 * @brief      
 */
/* Define to prevent recursive inclusion ------------------------------ */
#ifndef BB_TRANSPORT_H
#define BB_TRANSPORT_H

/* Includes ----------------------------------------------------------- */
#include <stdint.h>
#include <stdbool.h>
#include "sdk_errors.h"

/* Public defines ----------------------------------------------------- */

/* Public enumerate/structure ----------------------------------------- */
/**
 * @brief Source of the incoming network packet
 */
typedef enum 
{
    BB_SOURCE_SERIAL = 0,
    BB_SOURCE_BLE  = 1,
} bb_packet_source_t;

/**
 * @brief Header Callback khi có frame được nhận đầy đủ (Router đăng ký)
 */
typedef void (*bb_transport_state_transition_cb_t)(void);

/* Public macros ------------------------------------------------------ */
/* Public variables --------------------------------------------------- */
/* Public function prototypes ----------------------------------------- */
/**
 * @brief Initializes the transport layer (HDLC state machines, buffer allocations)
 *
 * @param[in] p_payload_buf Con trỏ trỏ tới mảng `uint8_t` do Router cấp phát để chứa dữ liệu Protobuf.
 * @param[in] p_payload_len Con trỏ lưu kích thước payload sau khi nhận xong.
 * @param[in] max_len Kích thước tối đa của buffer.
 * @param[in] cb Hàm callback được gọi khi decode HDLC thành công để chuyển State cho Router
 * @return NRF_SUCCESS on successful initialization.
 */
ret_code_t bb_transport_init(uint8_t * p_payload_buf, uint16_t * p_payload_len, uint16_t max_len, bb_transport_state_transition_cb_t cb);

/**
 * @brief Runs the transport state machine (polls bsp_uart, decodes HDLC)
 * Should be called periodically from bb_router_process or main loop.
 */
void bb_transport_process(void);

/**
 * @brief Kiểm tra xem đã có 1 HDLC frame giải mã xong và pass CRC nằm trong buffer không.
 * @return true nếu đã sẵn sàng.
 */
bool bb_transport_is_packet_ready(void);

/**
 * @brief Truyền data theo cấu hình Source.
 *        Nếu type = SERIAL, tiến hành bọc HDLC vào gửi UART.
 *        Nếu type = BLE, gửi raw Protobuf qua môi trường radio.
 *
 * @param[in] p_data Pointer to raw Protobuf payload.
 * @param[in] length Size of the payload.
 * @param[in] tx_source Destination source mode (Serial / BLE).
 * @return NRF_SUCCESS if success.
 */
ret_code_t bb_transport_send_data(uint8_t const * p_data, uint16_t  length, bb_packet_source_t tx_source);

#endif // BB_TRANSPORT_H
