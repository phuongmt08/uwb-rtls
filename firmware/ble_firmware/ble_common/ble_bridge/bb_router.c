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
#define PACKET_ADDR protobuf_PACKET_ADDR_HOST
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
static bool bb_router_check_dst(uint8_t * p_data, uint16_t length);

/* Function definitions ----------------------------------------------- */
ret_code_t bb_router_init(void)
{
    m_state = BB_ROUTER_STATE_IDLE;
    
    // Giao mảng protobuf_buffer cho Transport quản lý việc chép payload vào
    ret_code_t err_code = bb_transport_init(protobuf_buffer, &protobuf_buffer_len, MAX_PROTOBUF_PAYLOAD_SIZE, bb_router_state_transition);
    // err_code |= bb_cmd_hdl_init(); // Khi nào implement thì update
    
    if (err_code != NRF_SUCCESS)
    {
        return err_code;
    }

    return NRF_SUCCESS;
}

void bb_router_process(void)
{
    switch (m_state) {
        
        case BB_ROUTER_STATE_IDLE:
            bb_transport_process();
            break;
            
        case BB_ROUTER_STATE_CHECK_DST:
        {
            NRF_LOG_INFO("checking packet destination...");

            // Kiểm tra con trỏ buf có trỏ tới ta không (chỉ lấy đích dst)
            bool is_for_me = bb_router_check_dst(protobuf_buffer, protobuf_buffer_len);

            if (is_for_me) {
                NRF_LOG_INFO("Packet is for this device. Processing command...");
                m_state = BB_ROUTER_STATE_PROCESS_CMD;
            } else {
                m_target_source = BB_SOURCE_BLE;
                m_state = BB_ROUTER_STATE_FORWARD;
            }
            break;
        }

        case BB_ROUTER_STATE_PROCESS_CMD:
        {
            // Xử lý các command dạng protobuf (Decode -> Table tra cứu -> Encode đè lên chính mảng buffer)
            bb_cmd_action_t action = bb_cmd_hdl_process(protobuf_buffer, &protobuf_buffer_len, MAX_PROTOBUF_PAYLOAD_SIZE);
            if (action == BB_CMD_ACTION_SEND_SERIAL) 
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
            }
            break;
        }
            
        case BB_ROUTER_STATE_FORWARD:
        {
            // Forward raw payload (Hoặc gói response đã được cmd handler đè lên payload buf nếu cần)
            bb_transport_send_data(protobuf_buffer, protobuf_buffer_len, m_target_source);
            
            m_state = BB_ROUTER_STATE_IDLE;
            break;
        }

        default:
            m_state = BB_ROUTER_STATE_IDLE;
            break;
    }
}

/* Private definitions ------------------------------------------------ */
static bool bb_router_check_dst(uint8_t * p_data, uint16_t length)
{
    // Tạo 1 struct gói tin protobuf rỗng
    protobuf_packet_t pkt = protobuf_packet_t_init_zero;
    
    // Mở stream con trỏ để pb_decode đọc
    pb_istream_t stream = pb_istream_from_buffer(p_data, length);
    
    // Lệnh decode protobuf. Ở đây gọi pb_decode toàn bộ packet (hoặc chỉ phần cần thiết)
    // Nếu chỉ muốn parse Header để lấy addr, nanopb sẽ tự điền vào `pkt.hdr`
    // Ở những hệ thống cực thiếu CPU có thể dùng pb_decode_tag loop để nhặt mã. Nhưng
    // pb_decode là nhanh nhất nếu RAM rảnh rỗi struct
    if (pb_decode(&stream, protobuf_packet_t_fields, &pkt)) 
    {
        if (pkt.has_hdr && pkt.hdr.has_addr) 
        {
            // Kiểm tra xem có gửi cho Peripheral (Chính là nRF52) hay không
            if (pkt.hdr.addr.dst == PACKET_ADDR) 
            {
                return true;
            }
        }
    }
    
    // Mặc định hoặc lỗi là Forward
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
