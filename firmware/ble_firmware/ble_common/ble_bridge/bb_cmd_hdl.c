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
#if defined(BLE_CENTRAL)
#include "../../central/app/app_ble_central.h"
#endif
#include <stddef.h>

#include "../../../protocol/nanopb/pb_encode.h"
#include "../../../protocol/nanopb/pb_decode.h"
#include "../../../protocol/protos/protocol.pb.h"
#include "nrf_log.h"
#include "bb_transport.h"

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
/* Common handlers Peripheral*/
static void handle_ble_adv_config_set(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action);
static void handle_ble_status_get(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action);
static void handle_ble_adv_status(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action);
static void handle_ble_unimplemented(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action) __attribute__((unused));
#if defined(BLE_CENTRAL)
static void handle_ble_scan_start(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action);
static void handle_ble_scan_stop(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action);
static void handle_ble_scan_result(const protobuf_packet_t * p_in, protobuf_packet_t * p_out   , bb_cmd_action_t * p_action);
static void handle_ble_conn_params_get(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action);     
static void handle_ble_conn_params_set(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action);
static void handle_ble_conn_params_resp(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action);
static void handle_ble_connect(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action);
static void handle_ble_disconnect(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action);
#endif

/* Private variables -------------------------------------------------- */
// Chỉ config mảng những lệnh nào nRF52832 tự xử lý. 
// Nếu id nào không được config sẽ tự rớt xuống undefined / bỏ qua.
static const bb_cmd_entry_t m_cmd_table[] = {
    CMD_INFO(protobuf_packet_t_ble_status_get_tag,     handle_ble_status_get,     "ble_status_get"),
    
#ifndef BLE_PERIPHERAL
    CMD_INFO(protobuf_packet_t_ble_adv_config_set_tag, handle_ble_adv_config_set, "ble_adv_config_set"),
    CMD_INFO(protobuf_packet_t_ble_adv_status_tag,     handle_ble_adv_status,     "ble_adv_status"),
#else
    CMD_INFO(protobuf_packet_t_ble_adv_status_tag,     handle_ble_unimplemented,  "ble_adv_status"),
    CMD_INFO(protobuf_packet_t_ble_adv_config_set_tag, handle_ble_unimplemented,  "ble_adv_config_set"),
#endif /* !BLE_PERIPHERAL */

#if defined(BLE_CENTRAL)
    CMD_INFO(protobuf_packet_t_ble_disconnect_tag,                handle_ble_disconnect,            "ble_disconnect"),
    CMD_INFO(protobuf_packet_t_ble_connect_tag,                   handle_ble_connect,               "ble_connect"),
    CMD_INFO(protobuf_packet_t_ble_scan_start_tag,                handle_ble_scan_start,            "ble_scan_start"),
    CMD_INFO(protobuf_packet_t_ble_conn_params_get_tag,           handle_ble_conn_params_get,       "ble_conn_params_get"),
    CMD_INFO(protobuf_packet_t_ble_conn_params_set_tag,           handle_ble_conn_params_set,       "ble_conn_params_set"),
    CMD_INFO(protobuf_packet_t_ble_conn_params_resp_tag,          handle_ble_conn_params_resp,      "ble_conn_params_resp"),
    CMD_INFO(protobuf_packet_t_ble_scan_stop_tag,                 handle_ble_scan_stop,             "ble_scan_stop"),
    CMD_INFO(protobuf_packet_t_ble_scan_result_tag,               handle_ble_scan_result,           "ble_scan_result"),
#else
    CMD_INFO(protobuf_packet_t_ble_disconnect_tag,                handle_ble_unimplemented,         "ble_disconnect"),
    CMD_INFO(protobuf_packet_t_ble_connect_tag,                   handle_ble_unimplemented,         "ble_connect"),
    CMD_INFO(protobuf_packet_t_ble_scan_result_tag,               handle_ble_unimplemented,         "ble_scan_result"),
    CMD_INFO(protobuf_packet_t_ble_conn_params_get_tag,           handle_ble_unimplemented,         "ble_conn_params_get"),
    CMD_INFO(protobuf_packet_t_ble_conn_params_set_tag,           handle_ble_unimplemented,         "ble_conn_params_set"),
    CMD_INFO(protobuf_packet_t_ble_conn_params_resp_tag,          handle_ble_unimplemented,         "ble_conn_params_resp"),
    CMD_INFO(protobuf_packet_t_ble_scan_start_tag,                handle_ble_unimplemented,         "ble_scan_start"),
    CMD_INFO(protobuf_packet_t_ble_scan_stop_tag,                 handle_ble_unimplemented,         "ble_scan_stop"),
#endif /* !BLE_CENTRAL */

};

uint32_t max_id_table = sizeof(m_cmd_table) / sizeof(m_cmd_table[0]);

typedef enum {
    BB_CMD_HDL_STATE_IDLE,
    BB_CMD_HDL_STATE_DECODE,
    BB_CMD_HDL_STATE_PROCESS,
    BB_CMD_HDL_STATE_ENCODE,
} bb_cmd_hdl_state_t;

static bb_cmd_hdl_state_t m_cmd_state = BB_CMD_HDL_STATE_IDLE;
static protobuf_packet_t in_pkt;
static protobuf_packet_t out_pkt;
static bb_cmd_handler_t m_current_handler;
static bb_cmd_action_t m_current_action;

/* Function definitions ----------------------------------------------- */
ret_code_t bb_cmd_hdl_init(void)
{
    // Cấu hình các flag ban đầu nếu có.
    m_cmd_state = BB_CMD_HDL_STATE_IDLE;
    return NRF_SUCCESS;
}

bb_cmd_action_t bb_cmd_hdl_process(uint8_t *p_buf, uint16_t *p_length, uint16_t max_len)
{
    if (m_cmd_state == BB_CMD_HDL_STATE_IDLE)
    {
        if (p_buf == NULL || p_length == NULL || *p_length == 0 || max_len == 0) 
        {
            return BB_CMD_ACTION_ERROR;
        }
        m_cmd_state = BB_CMD_HDL_STATE_DECODE;
    }

    switch (m_cmd_state)
    {
        case BB_CMD_HDL_STATE_DECODE:
        {
            in_pkt = PKT_INIT;
            pb_istream_t stream = pb_istream_from_buffer(p_buf, *p_length);
            
            // 1. Decode data
            if (!pb_decode(&stream, protobuf_packet_t_fields, &in_pkt)) 
            {
                NRF_LOG_ERROR("bb_cmd_hdl: Pb decode err: %s", PB_GET_ERROR(&stream));
                m_cmd_state = BB_CMD_HDL_STATE_IDLE;
                return BB_CMD_ACTION_ERROR;
            }

            uint32_t cmd_idx = in_pkt.which_params;
            m_current_handler = NULL;

    // 2. Tra bảng Handler tương ứng với message ID
    if (cmd_idx < max_id_table) 
    {
        NRF_LOG_INFO("bb_cmd_hdl: Received cmd_id=%u, looking up handler...", cmd_idx);
        handler = m_cmd_table[cmd_idx].cmd_hdl;
    }

            if (m_current_handler == NULL) 
            {
                NRF_LOG_WARNING("bb_cmd_hdl: No handler for param_tag (%d)", cmd_idx);
                m_cmd_state = BB_CMD_HDL_STATE_IDLE;
                return BB_CMD_ACTION_NONE; 
            }

            m_cmd_state = BB_CMD_HDL_STATE_PROCESS;
            return BB_CMD_ACTION_BUSY;
        }

        case BB_CMD_HDL_STATE_PROCESS:
        {
            // 3. Khởi tạo một Gói Response tĩnh
            out_pkt = PKT_INIT;
            m_current_action = BB_CMD_ACTION_NONE;

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
            m_current_handler(&in_pkt, &out_pkt, &m_current_action);

            // 5. Nếu kết quả sau xử lý là cần GỬI Response, chuyển sang state ENCODE
            if (m_current_action == BB_CMD_ACTION_SEND_SERIAL || m_current_action == BB_CMD_ACTION_SEND_BLE) 
            {
                m_cmd_state = BB_CMD_HDL_STATE_ENCODE;
                return BB_CMD_ACTION_BUSY;
            }

            m_cmd_state = BB_CMD_HDL_STATE_IDLE;
            return m_current_action;
        }

        case BB_CMD_HDL_STATE_ENCODE:
        {
            pb_ostream_t ostream = pb_ostream_from_buffer(p_buf, max_len);
            if (!pb_encode(&ostream, protobuf_packet_t_fields, &out_pkt)) 
            {
                NRF_LOG_ERROR("bb_cmd_hdl: Pb encode err: %s", PB_GET_ERROR(&ostream));
                m_cmd_state = BB_CMD_HDL_STATE_IDLE;
                return BB_CMD_ACTION_ERROR;
            }
            
            // Đổi giá trị *p_length thành kích thước mới sau mã hoá
            *p_length = ostream.bytes_written;
            
            bb_cmd_action_t final_action = m_current_action;
            m_cmd_state = BB_CMD_HDL_STATE_IDLE;
            return final_action;
        }

        default:
            m_cmd_state = BB_CMD_HDL_STATE_IDLE;
            return BB_CMD_ACTION_ERROR;
    }
}

/* Private definitions ------------------------------------------------ */
/**
 * @brief STM32 cấu hình thông số quảng bá (Advertising) của nRF52
 * Mặc định cấu hình xong không cần ping lại response.
 */
static void handle_ble_adv_config_set(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action)
{
    NRF_LOG_INFO("MCU Requested BLE ADV Config Set");
    // const protobuf_ble_adv_config_t * p_req = &p_in->params.ble_adv_config_set;
    
    // (Nordic logic API) Bật/Tắt quảng bá ở đây ... 

    *p_action = BB_CMD_ACTION_NONE; 
}

/**
 * @brief STM32 hỏi trạng thái mạng BLE. Cần trả lời lại bằng status_resp
 */
static void handle_ble_status_get(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action)
{
    NRF_LOG_INFO("MCU Requested BLE Status");

    // Load vào out package
    p_out->which_params = protobuf_packet_t_ble_status_resp_tag;
    p_out->params.ble_status_resp.state = (protobuf_ble_state_t)app_ble_central_status_get();
    p_out->params.ble_status_resp.rssi_dbm = app_ble_central_rssi_dbm_get();
    
    uint32_t active_disconnect_reason = app_ble_central_disconnect_reason_get();
    if (active_disconnect_reason != 0)
    {
        p_out->params.ble_status_resp.has_disconnect_reason = true;
        p_out->params.ble_status_resp.disconnect_reason = active_disconnect_reason;
    }

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

static void handle_ble_unimplemented(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action)
{
    NRF_LOG_WARNING("No command handler for payload tag=%u", (unsigned)p_in->which_params);

    if (p_in->has_hdr)
    {
        p_out->has_hdr = true;
        p_out->hdr = p_in->hdr;
    }
    
    p_out->which_params = protobuf_packet_t_ack_tag;
    p_out->params.ack.response = protobuf_PACKET_ACK_RESPONSE_NACK_UNIMPLEMENTED;

    *p_action = BB_CMD_ACTION_SEND_SERIAL; 
    *p_action = BB_CMD_ACTION_NONE;
}

/*================!BLE_CENTRAL=================== */
#if defined(BLE_CENTRAL)

static void handle_ble_scan_start(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action)
{
    NRF_LOG_INFO("PC Requested BLE Scan Start");
    const protobuf_ble_scan_start_t * p_req = &p_in->params.ble_scan_start;
    
    // Logic to start scanning goes here
    app_ble_central_scan_start((uint16_t)p_req->interval_ms, (uint16_t)p_req->window_ms, (uint16_t)p_req->duration_ms, p_req->active_scanning);
    
    // Load vào out package
    p_out->which_params = protobuf_packet_t_ble_status_resp_tag;
    p_out->params.ble_status_resp.state = protobuf_BLE_STATE_SCANNING; 
    p_out->params.ble_status_resp.has_disconnect_reason = false;

    // Gắn nhãn báo cho Router biết hãy ném gói mới này vào đường SERIAL
    *p_action = BB_CMD_ACTION_SEND_SERIAL;
}
static void handle_ble_scan_stop(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action)
{
    NRF_LOG_INFO("PC Requested BLE Scan Stop");
    
    // Logic to stop scanning goes here
    app_ble_central_scan_stop();
    
    p_out->which_params = protobuf_packet_t_ble_status_resp_tag;
    p_out->params.ble_status_resp.state = protobuf_BLE_STATE_IDLE; 
    p_out->params.ble_status_resp.has_disconnect_reason = false;
    
    *p_action = BB_CMD_ACTION_SEND_SERIAL;
}

static void handle_ble_scan_result(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action)
{
    NRF_LOG_INFO("PC provided BLE Scan Result");
    
    *p_action = BB_CMD_ACTION_NONE;
}

static void handle_ble_conn_params_get(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action)
{
    NRF_LOG_INFO("PC Requested BLE Conn Params Get");
    
    uint16_t min_ms = 0, max_ms = 0, lat = 0, to_ms = 0;
    app_ble_central_conn_params_get(&min_ms, &max_ms, &lat, &to_ms);
    
    p_out->which_params = protobuf_packet_t_ble_conn_params_resp_tag;
    p_out->params.ble_conn_params_resp.has_params = true;
    p_out->params.ble_conn_params_resp.params.min_interval_ms = min_ms;
    p_out->params.ble_conn_params_resp.params.max_interval_ms = max_ms;
    p_out->params.ble_conn_params_resp.params.slave_latency = lat;
    p_out->params.ble_conn_params_resp.params.sup_timeout_ms = to_ms;
    
    *p_action = BB_CMD_ACTION_SEND_SERIAL;
}

static void handle_ble_conn_params_set(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action)
{
    NRF_LOG_INFO("PC Requested BLE Conn Params Set");
    const protobuf_ble_conn_params_t * p_params = &p_in->params.ble_conn_params_set.params;
    
    app_ble_central_conn_params_set(p_params->min_interval_ms,
                                    p_params->max_interval_ms,
                                    p_params->slave_latency,
                                    p_params->sup_timeout_ms);
    
    p_out->which_params = protobuf_packet_t_ble_conn_params_resp_tag;
    p_out->params.ble_conn_params_resp.has_params = true;
    p_out->params.ble_conn_params_resp.params.min_interval_ms = p_params->min_interval_ms;
    p_out->params.ble_conn_params_resp.params.max_interval_ms = p_params->max_interval_ms;
    p_out->params.ble_conn_params_resp.params.slave_latency = p_params->slave_latency;
    p_out->params.ble_conn_params_resp.params.sup_timeout_ms = p_params->sup_timeout_ms;
    
    *p_action = BB_CMD_ACTION_SEND_SERIAL;
}

static void handle_ble_conn_params_resp(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action)
{
    NRF_LOG_INFO("PC Requested BLE Conn Params Resp");
    *p_action = BB_CMD_ACTION_NONE;
}

static void handle_ble_connect(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action)
{
    NRF_LOG_INFO("PC Requested BLE Connect");
    const protobuf_ble_connect_t * p_req = &p_in->params.ble_connect;
    
    app_ble_central_connect(p_req->mac_address.bytes);
    
    p_out->which_params = protobuf_packet_t_ble_status_resp_tag;
    p_out->params.ble_status_resp.state = protobuf_BLE_STATE_CONNECTING; 
    p_out->params.ble_status_resp.has_disconnect_reason = false;
    *p_action = BB_CMD_ACTION_SEND_SERIAL;
}

static void handle_ble_disconnect(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action)
{
    NRF_LOG_INFO("PC Requested BLE Disconnect");
    
    app_ble_central_disconnect();
    
    p_out->which_params = protobuf_packet_t_ble_status_resp_tag;
    p_out->params.ble_status_resp.state = protobuf_BLE_STATE_IDLE; 
    p_out->params.ble_status_resp.has_disconnect_reason = false;
    *p_action = BB_CMD_ACTION_SEND_SERIAL;
}

void bb_cmd_notify_scan_result(const uint8_t * mac, int8_t rssi, const char * name, uint32_t serial_num)
{
    protobuf_packet_t pkt = protobuf_packet_t_init_zero;
    pkt.which_params = protobuf_packet_t_ble_scan_result_tag;
    memcpy(pkt.params.ble_scan_result.mac_address.bytes, mac, 6);
    pkt.params.ble_scan_result.mac_address.size = 6;
    pkt.params.ble_scan_result.rssi_dbm = rssi;
    pkt.params.ble_scan_result.serial_number = serial_num;
    if (name) {
        strncpy(pkt.params.ble_scan_result.name, name, sizeof(pkt.params.ble_scan_result.name)-1);
    }
    
    // Gửi tự động về SOURCE UART/SERIAL (PC) mà không cần chờ cmd_hdl
    uint8_t buffer[128];
    pb_ostream_t stream = pb_ostream_from_buffer(buffer, sizeof(buffer));
    if (pb_encode(&stream, protobuf_packet_t_fields, &pkt)) {
        bb_transport_send_data(buffer, stream.bytes_written, BB_SOURCE_SERIAL);
    }
}

/**
 * @brief  Sends a BLE status asynchronously to PC/Host on connection changes.
 */
void bb_cmd_notify_ble_status(uint8_t state,
                              int32_t rssi_dbm,
                              uint32_t disconnect_reason)
{
    protobuf_packet_t pkt = protobuf_packet_t_init_zero;
    
    pkt.has_hdr = true;
    pkt.hdr.has_addr = true;
    pkt.hdr.addr.src = protobuf_PACKET_ADDR_CENTRAL;
    pkt.hdr.addr.dst = protobuf_PACKET_ADDR_HOST; 

    pkt.which_params = protobuf_packet_t_ble_status_resp_tag;
    pkt.params.ble_status_resp.state = (protobuf_ble_state_t)state;
    pkt.params.ble_status_resp.rssi_dbm = rssi_dbm;

    if (disconnect_reason != 0)
    {
        pkt.params.ble_status_resp.has_disconnect_reason = true;
        pkt.params.ble_status_resp.disconnect_reason = disconnect_reason;
    }

    uint8_t buffer[64];
    pb_ostream_t stream = pb_ostream_from_buffer(buffer, sizeof(buffer));

    if (pb_encode(&stream, protobuf_packet_t_fields, &pkt)) 
    {
        bb_transport_send_data(buffer, stream.bytes_written, BB_SOURCE_SERIAL);
    }
}
#endif /* BLE_CENTRAL */

/* End of file -------------------------------------------------------- */
