/**
 * @file       bb_cmd_hdl.c
 * @copyright  [Your Copyright]
 * @license    [Your License]
 * @version    1.0.0
 * @date       [Date]
 * @author     [Your Name]
 *
 * @brief      
 */
/* Includes ----------------------------------------------------------- */
#include "bb_cmd_hdl.h"
#include <stddef.h>

#include "../../../protocol/nanopb/pb_encode.h"
#include "../../../protocol/nanopb/pb_decode.h"
#include "../../../protocol/protos/protocol.pb.h"
#include "nrf_log.h"

/* Private defines ---------------------------------------------------- */
#define PKT_INIT protobuf_packet_t_init_zero
typedef void (*bb_cmd_handler_t)(const protobuf_packet_t * p_in_pkt, protobuf_packet_t * p_out_pkt, bb_cmd_action_t * p_action);

/* Private enumerate/structure ----------------------------------------- */
typedef struct {
    uint32_t         cmd_id;
    bb_cmd_handler_t cmd_hdl;
    const char      *name;
} bb_cmd_entry_t;

#define CMD_INFO(_cmd_id, _cmd_hdl, _name) \
    [_cmd_id] = { .cmd_id = _cmd_id, .cmd_hdl = _cmd_hdl, .name = _name }

/* Private function prototypes ---------------------------------------- */
static void handle_ble_adv_config_set(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action);
static void handle_ble_status_get(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action);
static void handle_ble_adv_status(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action);

/* Private variables -------------------------------------------------- */
// Chỉ config mảng những lệnh nào nRF52832 tự xử lý. 
// Nếu id nào không được config sẽ tự rớt xuống undefined / bỏ qua.
static const bb_cmd_entry_t m_cmd_table[] = {
    CMD_INFO(protobuf_packet_t_ble_adv_config_set_tag, handle_ble_adv_config_set, "ble_adv_config_set"),
    CMD_INFO(protobuf_packet_t_ble_status_get_tag,     handle_ble_status_get,     "ble_status_get"),
    CMD_INFO(protobuf_packet_t_ble_adv_status_tag,     handle_ble_adv_status,     "ble_adv_status"),
};

uint32_t max_id_table = sizeof(m_cmd_table) / sizeof(m_cmd_table[0]);

/* Function definitions ----------------------------------------------- */
ret_code_t bb_cmd_hdl_init(void)
{
    // Cấu hình các flag ban đầu nếu có.
    return NRF_SUCCESS;
}

bb_cmd_action_t bb_cmd_hdl_process(uint8_t * p_buf, uint16_t * p_length, uint16_t max_len)
{
    if (p_buf == NULL || p_length == NULL || *p_length == 0 || max_len == 0) 
    {
        return BB_CMD_ACTION_ERROR;
    }

    protobuf_packet_t in_pkt = PKT_INIT;
    pb_istream_t stream = pb_istream_from_buffer(p_buf, *p_length);
    
    // 1. Decode data
    if (!pb_decode(&stream, protobuf_packet_t_fields, &in_pkt)) 
    {
        NRF_LOG_ERROR("bb_cmd_hdl: Pb decode err: %s", PB_GET_ERROR(&stream));
        return BB_CMD_ACTION_ERROR;
    }

    uint32_t cmd_idx = in_pkt.which_params;
    bb_cmd_handler_t handler = NULL;

    // 2. Tra bảng Handler tương ứng với message ID
    if (cmd_idx < max_id_table) 
    {
        handler = m_cmd_table[cmd_idx].cmd_hdl;
    }

    if (handler == NULL) 
    {
        NRF_LOG_WARNING("bb_cmd_hdl: No handler for param_tag (%d)", cmd_idx);
        return BB_CMD_ACTION_NONE; 
    }

    // 3. Khởi tạo một Gói Response tĩnh
    protobuf_packet_t out_pkt = PKT_INIT;
    bb_cmd_action_t action = BB_CMD_ACTION_NONE;

    // Tự động gán Header ngược lại cho gói đáp trả
    if (in_pkt.has_hdr) 
    {
        out_pkt.has_hdr = true;
        out_pkt.hdr.timestamp = in_pkt.hdr.timestamp; 
        
        // Khi nRF trả lời lại STM32, Destination sẽ là Host (STM32)
        out_pkt.hdr.has_addr = true;
        out_pkt.hdr.addr.dst = protobuf_PACKET_ADDR_HOST; 
    }

    // 4. Gọi Handler thực thi Logic ứng dụng
    handler(&in_pkt, &out_pkt, &action);

    // 5. Nếu kết quả sau xử lý là cần GỬI Response, tiến hành encode ĐÈ vào buffer
    if (action == BB_CMD_ACTION_SEND_SERIAL || action == BB_CMD_ACTION_SEND_BLE) 
    {
        pb_ostream_t ostream = pb_ostream_from_buffer(p_buf, max_len);
        if (!pb_encode(&ostream, protobuf_packet_t_fields, &out_pkt)) 
        {
            NRF_LOG_ERROR("bb_cmd_hdl: Pb encode err: %s", PB_GET_ERROR(&ostream));
            return BB_CMD_ACTION_ERROR;
        }
        
        // Đổi giá trị *p_length thành kích thước mới sau mã hoá
        *p_length = ostream.bytes_written;
    }

    return action;
}

/* Private definitions ------------------------------------------------ */
/**
 * @brief STM32 cấu hình thông số quảng bá (Advertising) của nRF52
 * Mặc định cấu hình xong không cần ping lại response.
 */
static void handle_ble_adv_config_set(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action)
{
    NRF_LOG_INFO("MCU Requested BLE ADV Config Set");
    const protobuf_ble_adv_config_t * p_req = &p_in->params.ble_adv_config_set;
    
    // (Nordic logic API) Bật/Tắt quảng bá ở đây ... 

    *p_action = BB_CMD_ACTION_NONE; 
}

/**
 * @brief STM32 hỏi trạng thái mạng BLE. Cần trả lời lại bằng status_resp
 */
static void handle_ble_status_get(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action)
{
    NRF_LOG_INFO("MCU Requested BLE Status");
    // const protobuf_ble_status_get_t * p_req = &p_in->params.ble_status_get;

    // (Nordic logic API) Lấy trạng thái Stack BLE hiện tại ...

    // Load vào out package
    p_out->which_params = protobuf_packet_t_ble_status_resp_tag;
    p_out->params.ble_status_resp.state = protobuf_BLE_STATE_CONNECTED; // Mock data

    // Gắn nhãn báo cho Router biết hãy ném gói mới này vào đường SERIAL
    *p_action = BB_CMD_ACTION_SEND_SERIAL; 
}

/**
 * @brief STM32 bắn dữ liệu lên nRF52 yêu cầu Broadcast / Forward đến thiết bị BLE Host (Central / Phone) 
 */
static void handle_ble_adv_status(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action)
{
    NRF_LOG_INFO("MCU sent status for BLE Central broadcast");
    // const protobuf_ble_adv_status_t * p_evt = &p_in->params.ble_adv_status;

    // Gắn nhãn báo cho Router báo gói này là truyền xuống radio BLE
    // Wait, ta có thể route luôn gói In (đỡ encode lại) nếu Router. 
    // Nhưng vì cơ chế buffer chung, nên nếu chọn SEND_BLE, hệ thống cũng encode lại nguyên xi.
    // Thực tế nên để Handler Forwarder độc lập khỏi cmd_hdl này. Tạm ghi mock:
    
    *p_out = *p_in;
    *p_action = BB_CMD_ACTION_SEND_BLE;
}

/* End of file -------------------------------------------------------- */
