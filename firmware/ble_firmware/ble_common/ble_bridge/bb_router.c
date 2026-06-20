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
#include "app_timer.h"

#include "logger.h"
#include "nrf_log.h"
#include "bb_debug.h"
#include "bb_transport.h"
#if defined(BLE_PERIPHERAL)
#include "app_timer.h"
#endif
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
#define BB_ROUTER_APP_TIMER_TICKS_TO_MS(ticks) \
    ((uint32_t)(((uint64_t)(ticks) * (APP_TIMER_CONFIG_RTC_FREQUENCY + 1u) * 1000u) / 32768u))

/* Private variables -------------------------------------------------- */
static bb_router_state_t m_state;
static bb_packet_source_t m_target_source;
#if defined(BLE_PERIPHERAL)
volatile uint32_t g_bb_router_mcu_rx_total_count = 0;
volatile uint32_t g_bb_router_mcu_rx_id_count[BB_ROUTER_MCU_BLE_PACKET_ID_COUNT] = {0};
#if BB_DEBUG_STREAM_MCU_PERI_ENABLED && (DEBUG_STREAM_MCU_PERI_STATS_INTERVAL_MS > 0)
APP_TIMER_DEF(m_mcu_rx_stats_timer_id);
#endif
#endif

// Router-owned buffer that stores protobuf payloads only.
static uint8_t protobuf_buffer[MAX_PROTOBUF_PAYLOAD_SIZE];
static uint16_t protobuf_buffer_len;

/* Private function prototypes ---------------------------------------- */
static void bb_router_state_transition(void);
static bool bb_router_check_dst(uint8_t *p_data, uint16_t length);
static void bb_router_state_check_dst_handle(void);
static void bb_router_state_process_cmd_handle(void);
static void bb_router_state_forward_handle(void);
#if defined(BLE_PERIPHERAL)
static int bb_router_mcu_ble_packet_index(uint32_t cmd_id);
#if BB_DEBUG_STREAM_MCU_PERI_ENABLED && (DEBUG_STREAM_MCU_PERI_STATS_INTERVAL_MS > 0)
static void bb_router_mcu_rx_stats_log_handler(void * p_context);
#endif
#endif


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

#if defined(BLE_PERIPHERAL) && BB_DEBUG_STREAM_MCU_PERI_ENABLED && (DEBUG_STREAM_MCU_PERI_STATS_INTERVAL_MS > 0)
    err_code = app_timer_create(&m_mcu_rx_stats_timer_id,
                                APP_TIMER_MODE_REPEATED,
                                bb_router_mcu_rx_stats_log_handler);
    if (err_code != NRF_SUCCESS)
    {
        return err_code;
    }

    err_code = app_timer_start(m_mcu_rx_stats_timer_id,
                               APP_TIMER_TICKS(DEBUG_STREAM_MCU_PERI_STATS_INTERVAL_MS),
                               NULL);
    if (err_code != NRF_SUCCESS)
    {
        return err_code;
    }
#endif

    return bb_cmd_hdl_init();
}

void bb_router_process(void)
{

    /* ---- Collapse state machine: process all ready states in one call ---- */

    if (m_state == BB_ROUTER_STATE_IDLE)
    {
        bb_transport_process();
    }

    if (m_state == BB_ROUTER_STATE_CHECK_DST)
    {
        bb_router_state_check_dst_handle();
    }

    if (m_state == BB_ROUTER_STATE_PROCESS_CMD)
    {
        bb_router_state_process_cmd_handle();
        /* BB_CMD_ACTION_BUSY keeps state at PROCESS_CMD, so we stop here */
        if (m_state == BB_ROUTER_STATE_PROCESS_CMD)
        {
            return;
        }
    }

    if (m_state == BB_ROUTER_STATE_FORWARD)
    {
        bb_router_state_forward_handle();
    }
}

/* Private definitions ------------------------------------------------ */
#if defined(BLE_PERIPHERAL)
static int bb_router_mcu_ble_packet_index(uint32_t cmd_id)
{
    switch (cmd_id) {
        case protobuf_packet_t_ack_tag:                     return 0; /* cmd_id 3 */
        case protobuf_packet_t_ble_adv_config_set_tag:      return 1; /* cmd_id 39 */
        case protobuf_packet_t_ble_status_get_tag:          return 2; /* cmd_id 40 */
        case protobuf_packet_t_ble_status_resp_tag:         return 3; /* cmd_id 41 */
        case protobuf_packet_t_ble_adv_status_tag:          return 4; /* cmd_id 42 */
        case protobuf_packet_t_log_data_tag:                return 5; /* cmd_id 43 */
        case protobuf_packet_t_ble_adv_config_request_tag:  return 6; /* cmd_id 69 */
        default:                                            return -1;
    }
}

#if BB_DEBUG_STREAM_MCU_PERI_ENABLED && (DEBUG_STREAM_MCU_PERI_STATS_INTERVAL_MS > 0)
static void bb_router_mcu_rx_stats_log_handler(void * p_context)
{
    UNUSED_PARAMETER(p_context);
    NRF_LOG_INFO("PERI: rx stats total=%u",
                 (unsigned)g_bb_router_mcu_rx_total_count);
    NRF_LOG_INFO("PERI: rx stats id3=%u id39=%u id40=%u id41=%u",
                 (unsigned)g_bb_router_mcu_rx_id_count[0],
                 (unsigned)g_bb_router_mcu_rx_id_count[1],
                 (unsigned)g_bb_router_mcu_rx_id_count[2],
                 (unsigned)g_bb_router_mcu_rx_id_count[3]);
    NRF_LOG_INFO("               id42=%u id43=%u id69=%u",
                 (unsigned)g_bb_router_mcu_rx_id_count[4],
                 (unsigned)g_bb_router_mcu_rx_id_count[5],
                 (unsigned)g_bb_router_mcu_rx_id_count[6]);
}
#endif
#endif

static void bb_router_state_check_dst_handle(void)
{
    // Check whether the destination address targets this bridge.
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
        // NONE or ERROR means there is no response to forward.
        m_state = BB_ROUTER_STATE_IDLE;
        bb_transport_clear_packet_ready();
    }
}

static void bb_router_state_forward_handle(void)
{
    /* Forward raw payload, or the response payload produced by a command handler. */
    //bb_router_log_forward_packet();
    ret_code_t err_code = bb_transport_send_data(protobuf_buffer, protobuf_buffer_len, m_target_source);
    if (err_code == NRF_ERROR_RESOURCES || err_code == NRF_ERROR_BUSY)
    {
        NRF_LOG_DEBUG("forward packet: transport busy target=%u len=%u",
                      (unsigned)m_target_source,
                      (unsigned)protobuf_buffer_len);
        return;
    }

    if (err_code != NRF_SUCCESS)
    {
        NRF_LOG_WARNING("forward packet: transport failed err=0x%x target=%u len=%u",
                        err_code,
                        (unsigned)m_target_source,
                        (unsigned)protobuf_buffer_len);
    }

    m_state = BB_ROUTER_STATE_IDLE;
    bb_transport_clear_packet_ready();
}

static bool bb_router_check_dst(uint8_t *p_data, uint16_t length)
{
    protobuf_packet_t pkt = protobuf_packet_t_init_zero;
    pb_istream_t stream = pb_istream_from_buffer(p_data, length);

    if (pb_decode(&stream, protobuf_packet_t_fields, &pkt))
    {
#if defined(BLE_PERIPHERAL)
        if (bb_transport_get_rx_source() == BB_SOURCE_SERIAL)
        {
#if BB_DEBUG_STREAM_MCU_PERI_ENABLED
            int idx = bb_router_mcu_ble_packet_index(pkt.which_params);
            g_bb_router_mcu_rx_total_count++;
            if (idx >= 0)
            {
                g_bb_router_mcu_rx_id_count[idx]++;
                NRF_LOG_INFO("PERI: received total=%u cmd_id=%u id_count=%u from MCU",
                             (unsigned)g_bb_router_mcu_rx_total_count,
                             (unsigned)pkt.which_params,
                             (unsigned)g_bb_router_mcu_rx_id_count[idx]);
            }
            else
            {
                NRF_LOG_INFO("PERI: received total=%u cmd_id=%u from MCU",
                             (unsigned)g_bb_router_mcu_rx_total_count,
                             (unsigned)pkt.which_params);
            }
#endif
        }
#endif
        if (pkt.which_params == 43) {
            uint32_t seq = (pkt.has_hdr) ? pkt.hdr.seq : 0;
            NRF_LOG_INFO("bb_router: Decoded packet cmd_id=43 seq=%u", (unsigned)seq);
#if !BB_DEBUG_STREAM_MCU_PERI_ENABLED
        } else if (pkt.which_params == protobuf_packet_t_ack_tag &&
                   pkt.has_hdr && pkt.hdr.has_addr &&
                   pkt.hdr.addr.src == protobuf_PACKET_ADDR_HOST) {
            NRF_LOG_INFO("bb_router: Decoded packet cmd_id=3 ack_seq=%u", (unsigned)pkt.params.ack.ack_seq);
        } else {
            NRF_LOG_INFO("bb_router: Decoded packet cmd_id=%u", pkt.which_params);
#endif
        }
        if (pkt.has_hdr && pkt.hdr.has_addr)
        {
            uint32_t addr = pkt.hdr.addr.dst;

            if (addr == PACKET_ADDR)
            {
                return true;
            }

#if defined(BLE_CENTRAL)
            bb_packet_source_t rx_src = bb_transport_get_rx_source();
            if (addr == protobuf_PACKET_ADDR_HOST || addr == protobuf_PACKET_ADDR_DEBUG)
            {
                m_target_source = BB_SOURCE_SERIAL;
                return false;
            }
            if (addr == protobuf_PACKET_ADDR_BCAST)
            {
                if (rx_src == BB_SOURCE_SERIAL)
                {
                    m_target_source = BB_SOURCE_BLE_BROADCAST;
                }
                else
                {
                    m_target_source = BB_SOURCE_SERIAL;
                }
                return false;
            }

            m_target_source = BB_SOURCE_BLE;
            return false;
#elif defined(BLE_PERIPHERAL)
            bb_packet_source_t rx_src = bb_transport_get_rx_source();
            if (addr == protobuf_PACKET_ADDR_MCU)
            {
                m_target_source = BB_SOURCE_SERIAL;
                return false;
            }
            if (addr == protobuf_PACKET_ADDR_BCAST)
            {
                if (rx_src == BB_SOURCE_SERIAL)
                {
                    m_target_source = BB_SOURCE_BLE_BROADCAST;
                }
                else
                {
                    m_target_source = BB_SOURCE_SERIAL;
                }
                return false;
            }

            m_target_source = BB_SOURCE_BLE;
            return false;
#endif
        }
    }

    // Default or decode-error fallback is forwarding to BLE.
    m_target_source = BB_SOURCE_BLE;
    return false;
}

static void bb_router_state_transition(void)
{
    // Transition callback invoked when the transport receives a complete HDLC frame.
    if (m_state == BB_ROUTER_STATE_IDLE) 
    {
        m_state = BB_ROUTER_STATE_CHECK_DST;
    }
}

/* End of file -------------------------------------------------------- */
