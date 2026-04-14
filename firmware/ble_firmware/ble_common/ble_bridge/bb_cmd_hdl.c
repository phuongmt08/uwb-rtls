/**
 * @file bb_cmd_hdl.c
 * @brief Implementation of BLE Bridge Application Controller
 */
#include "bb_cmd_hdl.h"
#include <stddef.h>

#include "../../../protocol/nanopb/pb_encode.h"
#include "../../../protocol/nanopb/pb_decode.h"
#include "../../../protocol/protos/protocol.pb.h"
#include "nrf_log.h"

// Macro helper cho init packet
#define PKT_INIT protobuf_packet_t_init_zero

/* Private function prototypes ---------------------------------------- */
static void handle_ble_status_get(protobuf_ble_status_get_t const * p_req);

/* Function definitions ----------------------------------------------- */
ret_code_t bb_cmd_hdl_init(void)
{
    // Cấu hình các flag ban đầu nếu có.
    return NRF_SUCCESS;
}

ret_code_t bb_cmd_hdl_process(uint8_t const * p_data, uint16_t length)
{
    if (p_data == NULL || length == 0) {
        return NRF_ERROR_NULL;
    }

    protobuf_packet_t pkt = PKT_INIT;
    pb_istream_t stream = pb_istream_from_buffer(p_data, length);
    
    // Decode data thông qua con trỏ
    if (!pb_decode(&stream, protobuf_packet_t_fields, &pkt)) {
        NRF_LOG_ERROR("bb_cmd_hdl: Protobuf decode failed: %s", PB_GET_ERROR(&stream));
        return NRF_ERROR_INVALID_DATA;
    }

    // Kiểm tra xem packet chứa params gì (sử dụng which_params enum của union)
    switch (pkt.which_params) {
        // Ví dụ: MCU yêu cầu hỏi trạng thái BLE hiện tại
        case protobuf_packet_t_ble_status_get_tag:
            handle_ble_status_get(&pkt.params.ble_status_get);
            break;
            
        // Nhận được lệnh nào thì add vào từng case rồi đẩy vào hàm static tương ứng...
        
        default:
            NRF_LOG_WARNING("bb_cmd_hdl: Unhandled packet type (%d) for Peripheral", pkt.which_params);
            break;
    }
    
    return NRF_SUCCESS;
}

/* Private definitions ------------------------------------------------ */
static void handle_ble_status_get(protobuf_ble_status_get_t const * p_req)
{
    NRF_LOG_INFO("MCU Requested BLE Status. Mock response...");
    
    // Thực tế sẽ tiến hành kiểm tra Stack BLE của Nordic:
    // ...
    // Tạo 1 Protobuf trả về chứa protobuf_ble_status_resp_t
    // Gọi bb_transport_send_uart(buffer, size)
}

/* End of file -------------------------------------------------------- */
