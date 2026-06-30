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
#elif defined(BLE_PERIPHERAL)
#include "../../peripheral/ble_peripheral.h"
#endif
#include <stddef.h>
#include <string.h>

#include "../../../protocol/nanopb/pb_encode.h"
#include "../../../protocol/nanopb/pb_decode.h"
#include "../../../protocol/protos/protocol.pb.h"
#include "nrf_log.h"
#include "bb_debug.h"
#include "bb_transport.h"
#include "app_timer.h"

/* Private defines ---------------------------------------------------- */
#define PKT_INIT protobuf_packet_t_init_zero
#define BLE_ADV_CONFIG_REQUEST_PERIOD_TICKS APP_TIMER_TICKS(1000)
#ifdef BLE_PERIPHERAL
#define PACKET_ADDR protobuf_PACKET_ADDR_PERIPHERAL
#elif defined(BLE_CENTRAL)
#define PACKET_ADDR protobuf_PACKET_ADDR_CENTRAL
#else
#define PACKET_ADDR protobuf_PACKET_ADDR_UNSPECIFIED
#endif
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
// Common handler
static void handle_ble_status_get(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action);
static void handle_ble_unimplemented(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action);
static void handle_device_information_get(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action);

#if defined(BLE_PERIPHERAL)
static void handle_ble_adv_config_set(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action);
static void handle_ble_adv_status(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action);
#endif
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
// Only list commands handled locally by the nRF52832.
// Unlisted payload tags fall through to the undefined/unimplemented path.
static const bb_cmd_entry_t m_cmd_table[] = {
    CMD_INFO(protobuf_packet_t_ble_status_get_tag,                handle_ble_status_get,            "ble_status_get"),
#if defined(BLE_PERIPHERAL)
    CMD_INFO(protobuf_packet_t_ble_adv_status_tag,                handle_ble_adv_status,            "ble_adv_status"),
    CMD_INFO(protobuf_packet_t_ble_adv_config_set_tag,            handle_ble_adv_config_set,        "ble_adv_config_set"),
    CMD_INFO(protobuf_packet_t_ble_adv_status_tag,                handle_ble_adv_status,            "ble_adv_status"),
    CMD_INFO(protobuf_packet_t_ble_adv_config_set_tag,            handle_ble_adv_config_set,        "ble_adv_config_set"),
#else
    CMD_INFO(protobuf_packet_t_ble_adv_status_tag,                handle_ble_unimplemented,         "ble_adv_status"),
    CMD_INFO(protobuf_packet_t_ble_adv_config_set_tag,            handle_ble_unimplemented,         "ble_adv_config_set"),
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
    CMD_INFO(protobuf_packet_t_device_information_get_tag, handle_device_information_get, "device_information_get"),
};

uint32_t max_id_table = sizeof(m_cmd_table) / sizeof(m_cmd_table[0]);

typedef enum {
    BB_CMD_HDL_STATE_IDLE,
    BB_CMD_HDL_STATE_DECODE,
    BB_CMD_HDL_STATE_PROCESS,
    BB_CMD_HDL_STATE_ENCODE,
} bb_cmd_hdl_state_t;

static bb_cmd_hdl_state_t m_cmd_state = BB_CMD_HDL_STATE_IDLE;
#if defined(BLE_PERIPHERAL)
static bool m_ble_adv_config_received = false;
static uint32_t m_last_ble_adv_config_request_tick = 0;
#endif

/* Function definitions ----------------------------------------------- */
ret_code_t bb_cmd_hdl_init(void)
{
    // Initialize local command-handler state.
    m_cmd_state = BB_CMD_HDL_STATE_IDLE;
#if defined(BLE_PERIPHERAL)
    m_ble_adv_config_received = false;
    m_last_ble_adv_config_request_tick = 0;
#endif
    return NRF_SUCCESS;
}

ret_code_t bb_cmd_request_ble_adv_config(void)
{
#if defined(BLE_PERIPHERAL)
    protobuf_packet_t pkt = protobuf_packet_t_init_zero;
    pkt.has_hdr = true;
    pkt.hdr.has_addr = true;
    pkt.hdr.addr.src = protobuf_PACKET_ADDR_PERIPHERAL;
    pkt.hdr.addr.dst = protobuf_PACKET_ADDR_MCU;
    pkt.hdr.seq = 0;

    pkt.which_params = protobuf_packet_t_ble_adv_config_request_tag;
    pkt.params.ble_adv_config_request.dummy = 1;

    uint8_t buffer[64];
    pb_ostream_t stream = pb_ostream_from_buffer(buffer, sizeof(buffer));
    if (!pb_encode(&stream, protobuf_packet_t_fields, &pkt))
    {
        NRF_LOG_ERROR("ble_adv_config_request encode failed: %s", PB_GET_ERROR(&stream));
        return NRF_ERROR_INTERNAL;
    }

    BB_DEBUG_LOG_INFO("Requesting BLE advertising config from MCU");
    m_last_ble_adv_config_request_tick = app_timer_cnt_get();
    return bb_transport_send_data(buffer, stream.bytes_written, BB_SOURCE_SERIAL);
#else
    return NRF_ERROR_NOT_SUPPORTED;
#endif
}

void bb_cmd_ble_adv_config_request_process(void)
{
#if defined(BLE_PERIPHERAL)
    if (m_ble_adv_config_received)
    {
        return;
    }

    uint32_t now = app_timer_cnt_get();
    if ((uint32_t)(now - m_last_ble_adv_config_request_tick) < BLE_ADV_CONFIG_REQUEST_PERIOD_TICKS)
    {
        return;
    }

    ret_code_t err_code = bb_cmd_request_ble_adv_config();
    if (err_code != NRF_SUCCESS)
    {
        NRF_LOG_WARNING("ble_adv_config_request retry failed: 0x%08X", err_code);
    }
#endif
}

bb_cmd_action_t bb_cmd_hdl_process(uint8_t * p_buf, uint16_t * p_length, uint16_t max_len)
{
    if (p_buf == NULL || p_length == NULL || *p_length == 0 || max_len == 0) 
    {
        return BB_CMD_ACTION_ERROR;
    }

    protobuf_packet_t in_pkt = PKT_INIT;
    pb_istream_t stream = pb_istream_from_buffer(p_buf, *p_length);
    
    // Decode the incoming protobuf payload.
    if (!pb_decode(&stream, protobuf_packet_t_fields, &in_pkt)) 
    {
        NRF_LOG_ERROR("bb_cmd_hdl: Pb decode err: %s", PB_GET_ERROR(&stream));
        return BB_CMD_ACTION_ERROR;
    }

    uint32_t cmd_idx = in_pkt.which_params;
    bb_cmd_handler_t handler = NULL;

    // Look up the handler for this payload tag.
    if (cmd_idx < max_id_table) 
    {
        BB_DEBUG_LOG_INFO("bb_cmd_hdl: Received cmd_id=%u, looking up handler...", cmd_idx);
        handler = m_cmd_table[cmd_idx].cmd_hdl;
    }

    if (handler == NULL) 
    {
        NRF_LOG_WARNING("bb_cmd_hdl: No handler for param_tag (%d)", cmd_idx);
        return BB_CMD_ACTION_NONE; 
    }

    // Prepare a static response packet.
    protobuf_packet_t out_pkt = PKT_INIT;
    bb_cmd_action_t action = BB_CMD_ACTION_NONE;

    // Mirror the request header into the response.
    if (in_pkt.has_hdr) 
    {
        out_pkt.has_hdr = true;
        out_pkt.hdr.timestamp = in_pkt.hdr.timestamp; 
        out_pkt.hdr.seq = in_pkt.hdr.seq;
        
        // Reply to the original sender (STM32 TAG/ANCHOR or PC HOST).
        if (in_pkt.hdr.has_addr)
        {
            out_pkt.hdr.has_addr = true;
            out_pkt.hdr.addr.src = PACKET_ADDR;
            out_pkt.hdr.addr.dst = in_pkt.hdr.addr.src;
        }
    }

    // Execute the application handler.
    handler(&in_pkt, &out_pkt, &action);
    // If the handler produced a response, encode it back into the shared buffer.
    if (action == BB_CMD_ACTION_SEND_SERIAL || action == BB_CMD_ACTION_SEND_BLE) 
    {
        pb_ostream_t ostream = pb_ostream_from_buffer(p_buf, max_len);
        if (!pb_encode(&ostream, protobuf_packet_t_fields, &out_pkt)) 
        {
            NRF_LOG_ERROR("bb_cmd_hdl: Pb encode err: %s", PB_GET_ERROR(&ostream));
            return BB_CMD_ACTION_ERROR;
        }
        
        // Replace the input length with the encoded response length.
        *p_length = ostream.bytes_written;
    }

    return action;
}

/* Private definitions ------------------------------------------------ */
/**
 * @brief STM32 hỏi trạng thái mạng BLE. Cần trả lời lại bằng status_resp
 */
static void handle_ble_status_get(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action)
{

    // Fill the output packet.
    p_out->which_params = protobuf_packet_t_ble_status_resp_tag;

#if defined(BLE_CENTRAL)
    p_out->params.ble_status_resp.state = (protobuf_ble_state_t)app_ble_central_status_get();
    p_out->params.ble_status_resp.rssi_dbm = app_ble_central_rssi_dbm_get();
    
    uint32_t active_disconnect_reason = app_ble_central_disconnect_reason_get();
#elif defined(BLE_PERIPHERAL)
    p_out->params.ble_status_resp.state = (protobuf_ble_state_t)ble_peripheral_status_get();
    p_out->params.ble_status_resp.rssi_dbm = 0;
    
    uint32_t active_disconnect_reason = 0;
#else
    p_out->params.ble_status_resp.state = protobuf_BLE_STATE_IDLE;
    p_out->params.ble_status_resp.rssi_dbm = 0;
    
    uint32_t active_disconnect_reason = 0;
#endif
    
    if (active_disconnect_reason != 0)
    {
        p_out->params.ble_status_resp.has_disconnect_reason = true;
        p_out->params.ble_status_resp.disconnect_reason = active_disconnect_reason;
    }

    // Tell the router to send this response back over serial.
    *p_action = BB_CMD_ACTION_SEND_SERIAL; 
}

/*================BLE_PERIPHERAL=================== */
#if defined(BLE_PERIPHERAL)
/**
 * @brief Handles STM32 advertising configuration requests for the nRF52.
 * No response is required after applying the configuration.
 */
static void handle_ble_adv_config_set(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action)
{
    const protobuf_ble_adv_config_t * p_req = &p_in->params.ble_adv_config_set;
    
    ble_peripheral_adv_config_set(p_req->enable, p_req->device_name, p_req->serial_number);
    m_ble_adv_config_received = true;
    BB_DEBUG_LOG_INFO("BLE advertising config received from MCU");

    *p_action = BB_CMD_ACTION_NONE; 
}

/**
 * @brief Update advertising status and forward it over the active BLE link.
 */
static void handle_ble_adv_status(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action)
{
    UNUSED_PARAMETER(p_out);
    BB_DEBUG_LOG_INFO("MCU sent BLE advertiser status update");
    const protobuf_ble_adv_status_t * p_evt = &p_in->params.ble_adv_status;

    ble_peripheral_adv_status_update(p_evt);
    *p_action = BB_CMD_ACTION_NONE;
}
#endif /* BLE_PERIPHERAL */

static void handle_device_information_get(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action)
{
    BB_DEBUG_LOG_INFO("MCU Requested Device Information");
    p_out->which_params = protobuf_packet_t_ack_tag;
    p_out->params.ack.ack_seq = p_in->hdr.seq;
    p_out->params.ack.response = protobuf_PACKET_ACK_RESPONSE_ACK;
    *p_action = BB_CMD_ACTION_SEND_SERIAL;
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
}

/*================!BLE_CENTRAL=================== */
#if defined(BLE_CENTRAL)

static void handle_ble_scan_start(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action)
{
    BB_DEBUG_LOG_INFO("PC Requested BLE Scan Start");
    const protobuf_ble_scan_start_t * p_req = &p_in->params.ble_scan_start;
    
    // Logic to start scanning goes here
    app_ble_central_scan_start((uint16_t)p_req->interval_ms, (uint16_t)p_req->window_ms, (uint16_t)p_req->duration_ms, p_req->active_scanning);
    
    // Fill the output packet.
    p_out->which_params = protobuf_packet_t_ble_status_resp_tag;
    p_out->params.ble_status_resp.state = protobuf_BLE_STATE_SCANNING; 
    p_out->params.ble_status_resp.has_disconnect_reason = false;

    // Tell the router to send this response back over serial.
    *p_action = BB_CMD_ACTION_SEND_SERIAL;
}
static void handle_ble_scan_stop(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action)
{
    BB_DEBUG_LOG_INFO("PC Requested BLE Scan Stop");
    
    // Logic to stop scanning goes here
    app_ble_central_scan_stop();
    
    p_out->which_params = protobuf_packet_t_ble_status_resp_tag;
    p_out->params.ble_status_resp.state = protobuf_BLE_STATE_IDLE; 
    p_out->params.ble_status_resp.has_disconnect_reason = false;
    
    *p_action = BB_CMD_ACTION_SEND_SERIAL;
}

static void handle_ble_scan_result(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action)
{
    BB_DEBUG_LOG_INFO("PC provided BLE Scan Result");
    
    *p_action = BB_CMD_ACTION_NONE;
}

static void handle_ble_conn_params_get(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action)
{
    BB_DEBUG_LOG_INFO("PC Requested BLE Conn Params Get");
    
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
    BB_DEBUG_LOG_INFO("PC Requested BLE Conn Params Set");
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
    BB_DEBUG_LOG_INFO("PC Requested BLE Conn Params Resp");
    *p_action = BB_CMD_ACTION_NONE;
}

static void handle_ble_connect(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action)
{
    BB_DEBUG_LOG_INFO("PC Requested BLE Connect");
    const protobuf_ble_connect_t * p_req = &p_in->params.ble_connect;
    
    app_ble_central_connect(p_req->mac_address.bytes);
    
    p_out->which_params = protobuf_packet_t_ble_status_resp_tag;
    p_out->params.ble_status_resp.state = protobuf_BLE_STATE_CONNECTING; 
    p_out->params.ble_status_resp.has_disconnect_reason = false;
    *p_action = BB_CMD_ACTION_SEND_SERIAL;
}

static void handle_ble_disconnect(const protobuf_packet_t * p_in, protobuf_packet_t * p_out, bb_cmd_action_t * p_action)
{
    BB_DEBUG_LOG_INFO("PC Requested BLE Disconnect");
    
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
    
    // Send scan results asynchronously to the UART/serial host.
    uint8_t buffer[128];
    pb_ostream_t stream = pb_ostream_from_buffer(buffer, sizeof(buffer));
    if (pb_encode(&stream, protobuf_packet_t_fields, &pkt)) {
        bb_transport_send_data(buffer, stream.bytes_written, BB_SOURCE_SERIAL);
    }
}

void bb_cmd_notify_adv_status(const protobuf_ble_adv_status_t * status)
{
    if (status == NULL)
    {
        return;
    }

    protobuf_packet_t pkt = protobuf_packet_t_init_zero;

    pkt.has_hdr = true;
    pkt.hdr.has_addr = true;
    pkt.hdr.addr.src = protobuf_PACKET_ADDR_CENTRAL;
    pkt.hdr.addr.dst = protobuf_PACKET_ADDR_HOST;

    pkt.which_params = protobuf_packet_t_ble_adv_status_tag;
    pkt.params.ble_adv_status = *status;

    uint8_t buffer[96];
    pb_ostream_t stream = pb_ostream_from_buffer(buffer, sizeof(buffer));
    if (pb_encode(&stream, protobuf_packet_t_fields, &pkt))
    {
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
