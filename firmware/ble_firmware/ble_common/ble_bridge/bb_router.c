/**
 * @file       bb_router.c
 * @copyright  [Your Copyright]
 * @license    [Your License]
 * @version    1.0.0
 * @date       [Date]
 * @author     [Your Name]
 *
 * @brief      
 */
/* Includes ----------------------------------------------------------- */
#include "bb_router.h"
#include "bb_cmd_hdl.h"
#include <stddef.h>

#include "logger.h"
#include "bb_transport.h"
#include "../../../protocol/nanopb/pb_decode.h"
#include "../../../protocol/protos/protocol.pb.h"

/* Private defines ---------------------------------------------------- */
#ifdef BLE_PERIPHERAL
#define PACKET_ADDR protobuf_PACKET_ADDR_PERIPHERAL
#elif defined(BLE_CENTRAL)
#define PACKET_ADDR protobuf_PACKET_ADDR_CENTRAL
#else
#define PACKET_ADDR protobuf_PACKET_ADDR_UNSPECIFIED
#endif

#define MAX_PROTOBUF_PAYLOAD_SIZE 256

/* Private variables -------------------------------------------------- */
static bb_router_state_t m_state;
static bb_packet_source_t m_target_source;

// Bộ nhớ do Router cấp phát chỉ chứa dữ liệu Protobuf (Zero-Copy flow)
static uint8_t protobuf_buffer[MAX_PROTOBUF_PAYLOAD_SIZE];
static uint16_t protobuf_buffer_len;

/* Private function prototypes ---------------------------------------- */
static void bb_router_state_transition(void);
static bool bb_router_check_dst(uint8_t *p_data, uint16_t length);
static void bb_router_state_check_dst_handle(void);
static void bb_router_state_process_cmd_handle(void);
static void bb_router_state_forward_handle(void);
static void bb_router_log_forward_packet(void);

/* Function definitions ----------------------------------------------- */
ret_code_t bb_router_init(void)
{
    m_state = BB_ROUTER_STATE_IDLE;

    ret_code_t err_code = bb_transport_init(protobuf_buffer,
                                            &protobuf_buffer_len,
                                            MAX_PROTOBUF_PAYLOAD_SIZE,
                                            bb_router_state_transition);
    if (err_code != NRF_SUCCESS)
    {
        return err_code;
    }

    return NRF_SUCCESS;
}

void bb_router_process(void)
{
    switch (m_state) 
    {
        case BB_ROUTER_STATE_IDLE:
            bb_transport_process();
            break;

        case BB_ROUTER_STATE_CHECK_DST:
            bb_router_state_check_dst_handle();
            break;

        case BB_ROUTER_STATE_PROCESS_CMD:
            bb_router_state_process_cmd_handle();
            break;

        case BB_ROUTER_STATE_FORWARD:
            bb_router_state_forward_handle();
            break;

        default:
            m_state = BB_ROUTER_STATE_IDLE;
            bb_transport_clear_packet_ready();
            break;
    }
}

/* Private definitions ------------------------------------------------ */
static void bb_router_state_check_dst_handle(void)
{
    // Kiểm tra con trỏ buf có trỏ tới ta không (chỉ lấy đích dst)
    bool is_for_me = bb_router_check_dst(protobuf_buffer, protobuf_buffer_len);

    if (is_for_me) 
    {
        m_state = BB_ROUTER_STATE_PROCESS_CMD;
    } 
    else 
    {
        m_state = BB_ROUTER_STATE_FORWARD;
    }
}

static void bb_router_state_process_cmd_handle(void)
{
    bb_cmd_action_t action = bb_cmd_hdl_process(protobuf_buffer,
                                                &protobuf_buffer_len,
                                                MAX_PROTOBUF_PAYLOAD_SIZE);

    if (action == BB_CMD_ACTION_BUSY)
    {
        return;
    }
    else if (action == BB_CMD_ACTION_SEND_SERIAL)
    {
        m_target_source = BB_SOURCE_SERIAL;
        m_state = BB_ROUTER_STATE_FORWARD;
    }
    else if (action == BB_CMD_ACTION_SEND_BLE)
    {
        m_target_source = BB_SOURCE_BLE;
        m_state = BB_ROUTER_STATE_FORWARD;
    }
    else
    {
        // Action = NONE (không cần gửi gì) hoặc ERROR (Báo lỗi payload) -> clear cờ chờ gói mới.
        m_state = BB_ROUTER_STATE_IDLE;
        bb_transport_clear_packet_ready();
    }
}

static void bb_router_state_forward_handle(void)
{
    // Forward raw payload (Hoặc gói response đã được cmd handler đè lên payload buf nếu cần)
    bb_router_log_forward_packet();
    bb_transport_send_data(protobuf_buffer, protobuf_buffer_len, m_target_source);

    m_state = BB_ROUTER_STATE_IDLE;
    bb_transport_clear_packet_ready();
}

static void bb_router_log_forward_packet(void)
{
    protobuf_packet_t pkt = protobuf_packet_t_init_zero;
    pb_istream_t stream = pb_istream_from_buffer(protobuf_buffer, protobuf_buffer_len);

    if (pb_decode(&stream, protobuf_packet_t_fields, &pkt))
    {
        uint32_t src = 0xFFu;
        uint32_t dst = 0xFFu;
        uint32_t seq = 0xFFFFFFFFu;

        if (pkt.has_hdr && pkt.hdr.has_addr)
        {
            src = pkt.hdr.addr.src;
            dst = pkt.hdr.addr.dst;
            seq = pkt.hdr.seq;
        }

        NRF_LOG_INFO("forward packet: msg_idx=%u src=%u dst=%u seq=%u target=%u len=%u",
                     (unsigned)pkt.which_params,
                     (unsigned)src,
                     (unsigned)dst,
                     (unsigned)seq,
                     (unsigned)m_target_source,
                     (unsigned)protobuf_buffer_len);
        return;
    }

    NRF_LOG_WARNING("forward packet: decode failed target=%u len=%u",
                    (unsigned)m_target_source,
                    (unsigned)protobuf_buffer_len);
}

static bool bb_router_check_dst(uint8_t *p_data, uint16_t length)
{
    protobuf_packet_t pkt = protobuf_packet_t_init_zero;
    pb_istream_t stream = pb_istream_from_buffer(p_data, length);

    if (pb_decode(&stream, protobuf_packet_t_fields, &pkt))
    {
        if (pkt.has_hdr && pkt.hdr.has_addr)
        {
            uint32_t addr = pkt.hdr.addr.dst;

            if (addr == PACKET_ADDR)
            {
                return true;
            }

#if defined(BLE_CENTRAL)
            if (addr == protobuf_PACKET_ADDR_HOST || addr == protobuf_PACKET_ADDR_DEBUG || addr == protobuf_PACKET_ADDR_BCAST)
            {
                m_target_source = BB_SOURCE_SERIAL;
                return false;
            }

            m_target_source = BB_SOURCE_BLE;
            return false;
#elif defined(BLE_PERIPHERAL)
            if (addr == protobuf_PACKET_ADDR_MCU || addr == protobuf_PACKET_ADDR_BCAST)
            {
                m_target_source = BB_SOURCE_SERIAL;
                return false;
            }

            m_target_source = BB_SOURCE_BLE;
            return false;
#endif
        }
    }

    // Mặc định hoặc lỗi là Forward ra BLE
    m_target_source = BB_SOURCE_BLE;
    return false;
}

static void bb_router_state_transition(void)
{
    // Hàm callback chuyển state được gọi khi bb_transport nhận được 1 HDLC frame hoàn chỉnh và pass CRC
    if (m_state == BB_ROUTER_STATE_IDLE) 
    {
        m_state = BB_ROUTER_STATE_CHECK_DST;
    }
}

/* End of file -------------------------------------------------------- */
