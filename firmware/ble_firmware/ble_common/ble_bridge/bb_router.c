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
#include <string.h>
#include "bb_router.h"
#include "bb_cmd_hdl.h"
#include "app_timer.h"

#include "logger.h"
#include "nrf_log.h"
#include "bb_debug.h"
#include "bb_transport.h"
#if defined(BLE_CENTRAL)
#include "bb_broadcast.h"
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

#define MAX_PROTOBUF_PAYLOAD_SIZE 512
#define BB_ROUTER_APP_TIMER_TICKS_TO_MS(ticks) \
    ((uint32_t)(((uint64_t)(ticks) * (APP_TIMER_CONFIG_RTC_FREQUENCY + 1u) * 1000u) / 32768u))

#if defined(BLE_CENTRAL)
/* One initial burst plus bounded retry bursts after each ACK timeout. */
#define BB_RELIABLE_MAX_TRACKERS 2u
#define BB_RELIABLE_MAX_RETRIES  3u
#define BB_RELIABLE_RETRY_MS     3000u
#define BB_ACK_DEDUPE_SLOTS      8u
#define BB_ACK_DEDUPE_MS         1000u
#elif defined(BLE_PERIPHERAL)
#define BB_BCAST_DEBOUNCE_MS           3000u
#define BB_BCAST_ACK_REPLAY_MIN_MS     2500u
#define BB_BCAST_ACK_CACHE_MAX_LEN       64u
#endif

/* Private enumerate/structure ------------------------------------------ */
#if defined(BLE_CENTRAL)
typedef struct
{
    bool active;
    uint32_t which_params;
    uint32_t seq;
    uint8_t retries_left;
    uint32_t last_action_tick;
    uint16_t encoded_len;
    uint8_t encoded[BLE_BROADCAST_MAX_PACKET_SIZE];
} bb_reliable_tracker_t;

typedef struct
{
    bool active;
    uint32_t serial_number;
    uint32_t cmd_tag;
    uint32_t cmd_seq;
    uint32_t tick;
} bb_ack_dedupe_t;
#elif defined(BLE_PERIPHERAL)
typedef enum
{
    BB_BCAST_NEW_COMMAND = 0,
    BB_BCAST_DROP_DUPLICATE,
    BB_BCAST_REPLAY_CACHED_ACK,
} bb_bcast_dedupe_action_t;

typedef struct
{
    bool active;
    uint32_t packet_hash;
    uint16_t packet_len;
    uint32_t cmd_tag;
    uint32_t cmd_seq;
    uint32_t last_rx_tick;
    bool ack_valid;
    uint16_t ack_len;
    uint8_t ack_data[BB_BCAST_ACK_CACHE_MAX_LEN];
    uint32_t last_ack_tx_tick;
} bb_bcast_dedupe_t;
#endif

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
#if defined(BLE_CENTRAL)
static bb_reliable_tracker_t m_reliable[BB_RELIABLE_MAX_TRACKERS];
static bb_ack_dedupe_t m_ack_dedupe[BB_ACK_DEDUPE_SLOTS];
static uint8_t m_ack_dedupe_next;
#elif defined(BLE_PERIPHERAL)
static bb_bcast_dedupe_t m_bcast_dedupe;
#endif

// Router-owned buffer that stores protobuf payloads only.
static uint8_t protobuf_buffer[MAX_PROTOBUF_PAYLOAD_SIZE];
static uint16_t protobuf_buffer_len;
static bool m_route_packet_valid;
static bool m_route_drop_packet;

/* Private function prototypes ---------------------------------------- */
static void bb_router_state_transition(void);
static bool bb_router_check_dst(uint8_t *p_data, uint16_t length);
static void bb_router_state_check_dst_handle(void);
static void bb_router_state_process_cmd_handle(void);
static void bb_router_state_forward_handle(void);
static void bb_router_log_packet(const char *direction, bb_packet_source_t source, uint8_t *p_data, uint16_t length);
#if defined(BLE_CENTRAL)
static void bb_reliable_track(uint32_t which_params, uint32_t seq, const uint8_t *data, uint16_t len);
static void bb_reliable_on_ack(uint32_t cmd_seq, uint32_t cmd_tag);
static void bb_reliable_process(void);
static bool bb_ack_is_duplicate(uint32_t serial_number, uint32_t cmd_tag, uint32_t cmd_seq);
#elif defined(BLE_PERIPHERAL)
static bb_bcast_dedupe_action_t bb_bcast_dedupe_command(const protobuf_packet_t *pkt,
                                                        const uint8_t *data,
                                                        uint16_t len);
static void bb_bcast_cache_ack(const protobuf_packet_t *pkt, const uint8_t *data, uint16_t len);
#endif

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
    m_route_drop_packet = false;

#if defined(BLE_CENTRAL)
    memset(m_reliable, 0, sizeof(m_reliable));
    memset(m_ack_dedupe, 0, sizeof(m_ack_dedupe));
    m_ack_dedupe_next = 0u;
#elif defined(BLE_PERIPHERAL)
    memset(&m_bcast_dedupe, 0, sizeof(m_bcast_dedupe));
#endif

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
#if defined(BLE_CENTRAL)
    bb_reliable_process();
#endif

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

    if (!m_route_packet_valid)
    {
        NRF_LOG_WARNING("Dropping malformed protobuf packet, len=%u",
                        (unsigned)protobuf_buffer_len);
        m_state = BB_ROUTER_STATE_IDLE;
        bb_transport_clear_packet_ready();
        return;
    }

    if (m_route_drop_packet)
    {
        m_state = BB_ROUTER_STATE_IDLE;
        bb_transport_clear_packet_ready();
        return;
    }

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
    bb_router_log_packet("TX", m_target_source, protobuf_buffer, protobuf_buffer_len);
    ret_code_t err_code = bb_transport_send_data(protobuf_buffer, protobuf_buffer_len, m_target_source);
    if (err_code == NRF_ERROR_RESOURCES ||
        err_code == NRF_ERROR_BUSY ||
        err_code == NRF_ERROR_IO_PENDING)
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

static void bb_router_log_packet(const char *direction, bb_packet_source_t source, uint8_t *p_data, uint16_t length)
{
#if BB_DEBUG_TRANSPORT_LOG_ENABLED
    protobuf_packet_t pkt = protobuf_packet_t_init_zero;
    pb_istream_t stream = pb_istream_from_buffer(p_data, length);

    if (pb_decode(&stream, protobuf_packet_t_fields, &pkt))
    {
        const char *src_name = "UNKNOWN";
        if (source == BB_SOURCE_SERIAL) {
            src_name = "Serial";
        } else if (source == BB_SOURCE_BLE) {
            src_name = "BLE";
        } else if (source == BB_SOURCE_BLE_BROADCAST) {
            src_name = "BLE_BCAST";
        }

        uint8_t pkt_src = (pkt.has_hdr && pkt.hdr.has_addr) ? pkt.hdr.addr.src : 0;
        uint8_t pkt_dst = (pkt.has_hdr && pkt.hdr.has_addr) ? pkt.hdr.addr.dst : 0;
        uint32_t pkt_seq = pkt.has_hdr ? pkt.hdr.seq : 0;

        NRF_LOG_INFO("[Transport] %s %s: cmd=%u seq=%u",
                     src_name,
                     direction,
                     (unsigned)pkt.which_params,
                     (unsigned)pkt_seq);
        NRF_LOG_INFO("            src=0x%02X dst=0x%02X len=%u",
                     (unsigned)pkt_src,
                     (unsigned)pkt_dst,
                     (unsigned)length);
    }
    else
    {
        NRF_LOG_WARNING("[Transport] Failed to decode %s packet, len=%u, error=%s",
                        direction,
                        length,
                        PB_GET_ERROR(&stream));
        NRF_LOG_HEXDUMP_WARNING(p_data, length);
    }
#endif
}

#if defined(BLE_CENTRAL)
static void bb_reliable_track(uint32_t which_params, uint32_t seq, const uint8_t *data, uint16_t len)
{
    if (len > BLE_BROADCAST_MAX_PACKET_SIZE)
    {
        return;
    }

    /* Reuse the tracker already covering this command, else a free slot,
     * else slot 0 (a newer command send supersedes an older one in flight). */
    bb_reliable_tracker_t *t = &m_reliable[0];
    for (uint8_t i = 0; i < BB_RELIABLE_MAX_TRACKERS; i++)
    {
        if (m_reliable[i].active && m_reliable[i].which_params == which_params)
        {
            t = &m_reliable[i];
            break;
        }
        if (!m_reliable[i].active)
        {
            t = &m_reliable[i];
        }
    }

    t->active = true;
    t->which_params = which_params;
    t->seq = seq;
    /* The normal forward path sends the initial burst. retries_left counts
     * additional bursts, each separated by BB_RELIABLE_RETRY_MS. */
    t->retries_left = BB_RELIABLE_MAX_RETRIES;
    t->last_action_tick = app_timer_cnt_get();
    t->encoded_len = len;
    memcpy(t->encoded, data, len);
}

static void bb_reliable_on_ack(uint32_t cmd_seq, uint32_t cmd_tag)
{
    for (uint8_t i = 0; i < BB_RELIABLE_MAX_TRACKERS; i++)
    {
        if (m_reliable[i].active && m_reliable[i].which_params == cmd_tag && m_reliable[i].seq == cmd_seq)
        {
            m_reliable[i].active = false;
            NRF_LOG_INFO("bb_router: bcast confirmed tag=%u seq=%u", (unsigned)cmd_tag, (unsigned)cmd_seq);
        }
    }
}

static void bb_reliable_process(void)
{
    uint32_t now = app_timer_cnt_get();

    for (uint8_t i = 0; i < BB_RELIABLE_MAX_TRACKERS; i++)
    {
        bb_reliable_tracker_t *t = &m_reliable[i];
        if (!t->active ||
            app_timer_cnt_diff_compute(now, t->last_action_tick) < APP_TIMER_TICKS(BB_RELIABLE_RETRY_MS))
        {
            continue;
        }

        if (t->retries_left == 0u)
        {
            NRF_LOG_WARNING("bb_router: bcast gave up tag=%u seq=%u", (unsigned)t->which_params, (unsigned)t->seq);
            t->active = false;
            continue;
        }

        if (bb_broadcast_send(t->encoded, t->encoded_len) == NRF_SUCCESS)
        {
            t->retries_left--;
            NRF_LOG_INFO("bb_router: bcast retry burst tag=%u seq=%u retries_left=%u",
                         (unsigned)t->which_params,
                         (unsigned)t->seq,
                         (unsigned)t->retries_left);
        }
        t->last_action_tick = now; /* also throttles retry attempts while the broadcaster is busy */
    }
}

static bool bb_ack_is_duplicate(uint32_t serial_number, uint32_t cmd_tag, uint32_t cmd_seq)
{
    uint32_t now = app_timer_cnt_get();

    for (uint8_t i = 0u; i < BB_ACK_DEDUPE_SLOTS; i++)
    {
        bb_ack_dedupe_t *entry = &m_ack_dedupe[i];
        if (entry->active &&
            entry->serial_number == serial_number &&
            entry->cmd_tag == cmd_tag &&
            entry->cmd_seq == cmd_seq &&
            app_timer_cnt_diff_compute(now, entry->tick) < APP_TIMER_TICKS(BB_ACK_DEDUPE_MS))
        {
            return true;
        }
    }

    bb_ack_dedupe_t *entry = &m_ack_dedupe[m_ack_dedupe_next];
    entry->active = true;
    entry->serial_number = serial_number;
    entry->cmd_tag = cmd_tag;
    entry->cmd_seq = cmd_seq;
    entry->tick = now;
    m_ack_dedupe_next = (uint8_t)((m_ack_dedupe_next + 1u) % BB_ACK_DEDUPE_SLOTS);
    return false;
}
#endif /* BLE_CENTRAL */

#if defined(BLE_PERIPHERAL)
static uint32_t bb_bcast_packet_hash(const uint8_t *data, uint16_t len)
{
    uint32_t hash = 2166136261u;
    for (uint16_t i = 0u; i < len; i++)
    {
        hash ^= data[i];
        hash *= 16777619u;
    }
    return hash;
}

static bool bb_bcast_is_debounced_command(uint32_t which_params)
{
    return which_params == protobuf_packet_t_time_sync_bcast_set_tag ||
           which_params == protobuf_packet_t_antenna_delay_bcast_set_tag;
}

static bb_bcast_dedupe_action_t bb_bcast_dedupe_command(const protobuf_packet_t *pkt,
                                                        const uint8_t *data,
                                                        uint16_t len)
{
    uint32_t now = app_timer_cnt_get();
    uint32_t hash = bb_bcast_packet_hash(data, len);
    bool duplicate = m_bcast_dedupe.active &&
                     m_bcast_dedupe.packet_hash == hash &&
                     m_bcast_dedupe.packet_len == len &&
                     m_bcast_dedupe.cmd_tag == pkt->which_params &&
                     m_bcast_dedupe.cmd_seq == pkt->hdr.seq &&
                     app_timer_cnt_diff_compute(now, m_bcast_dedupe.last_rx_tick) <=
                         APP_TIMER_TICKS(BB_BCAST_DEBOUNCE_MS);

    if (!duplicate)
    {
        memset(&m_bcast_dedupe, 0, sizeof(m_bcast_dedupe));
        m_bcast_dedupe.active = true;
        m_bcast_dedupe.packet_hash = hash;
        m_bcast_dedupe.packet_len = len;
        m_bcast_dedupe.cmd_tag = pkt->which_params;
        m_bcast_dedupe.cmd_seq = pkt->hdr.seq;
        m_bcast_dedupe.last_rx_tick = now;
        return BB_BCAST_NEW_COMMAND;
    }

    /* Measure the debounce window from the latest copy in the current burst.
     * This keeps the retry burst at 3 seconds tied to the same transaction. */
    m_bcast_dedupe.last_rx_tick = now;

    if (m_bcast_dedupe.ack_valid &&
        app_timer_cnt_diff_compute(now, m_bcast_dedupe.last_ack_tx_tick) >=
            APP_TIMER_TICKS(BB_BCAST_ACK_REPLAY_MIN_MS))
    {
        memcpy(protobuf_buffer, m_bcast_dedupe.ack_data, m_bcast_dedupe.ack_len);
        protobuf_buffer_len = m_bcast_dedupe.ack_len;
        m_bcast_dedupe.last_ack_tx_tick = now;
        m_target_source = BB_SOURCE_BLE_BROADCAST;
        NRF_LOG_INFO("BB BCAST duplicate: replay ACK tag=%u seq=%u",
                     (unsigned)pkt->which_params,
                     (unsigned)pkt->hdr.seq);
        return BB_BCAST_REPLAY_CACHED_ACK;
    }

    NRF_LOG_INFO("BB BCAST duplicate suppressed before MCU tag=%u seq=%u",
                 (unsigned)pkt->which_params,
                 (unsigned)pkt->hdr.seq);
    return BB_BCAST_DROP_DUPLICATE;
}

static void bb_bcast_cache_ack(const protobuf_packet_t *pkt, const uint8_t *data, uint16_t len)
{
    if (!m_bcast_dedupe.active ||
        pkt->which_params != protobuf_packet_t_bcast_apply_ack_tag ||
        pkt->params.bcast_apply_ack.cmd_tag != m_bcast_dedupe.cmd_tag ||
        pkt->params.bcast_apply_ack.cmd_seq != m_bcast_dedupe.cmd_seq ||
        len > sizeof(m_bcast_dedupe.ack_data))
    {
        return;
    }

    memcpy(m_bcast_dedupe.ack_data, data, len);
    m_bcast_dedupe.ack_len = len;
    m_bcast_dedupe.ack_valid = true;
    m_bcast_dedupe.last_ack_tx_tick = app_timer_cnt_get();
}
#endif /* BLE_PERIPHERAL */

static bool bb_router_check_dst(uint8_t *p_data, uint16_t length)
{
    m_route_packet_valid = false;
    m_route_drop_packet = false;
    bb_router_log_packet("RX", bb_transport_get_rx_source(), p_data, length);

    protobuf_packet_t pkt = protobuf_packet_t_init_zero;
    pb_istream_t stream = pb_istream_from_buffer(p_data, length);

    if (pb_decode(&stream, protobuf_packet_t_fields, &pkt))
    {
        m_route_packet_valid = true;
        if (pkt.has_hdr && pkt.hdr.has_addr &&
            pkt.hdr.addr.dst == protobuf_PACKET_ADDR_BCAST)
        {
            NRF_LOG_INFO("BB BCAST RX: via=%u tag=%u seq=%u src=0x%02X",
                         (unsigned)bb_transport_get_rx_source(),
                         (unsigned)pkt.which_params,
                         (unsigned)pkt.hdr.seq,
                         (unsigned)pkt.hdr.addr.src);
        }
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

#if defined(BLE_CENTRAL) || defined(BLE_PERIPHERAL)
            bb_packet_source_t rx_src = bb_transport_get_rx_source();

#if defined(BLE_PERIPHERAL)
            if (addr == protobuf_PACKET_ADDR_BCAST &&
                rx_src == BB_SOURCE_BLE &&
                bb_bcast_is_debounced_command(pkt.which_params))
            {
                bb_bcast_dedupe_action_t action =
                    bb_bcast_dedupe_command(&pkt, p_data, length);
                if (action == BB_BCAST_DROP_DUPLICATE)
                {
                    m_route_drop_packet = true;
                    return false;
                }
                if (action == BB_BCAST_REPLAY_CACHED_ACK)
                {
                    return false;
                }
            }
#endif

            if (addr == protobuf_PACKET_ADDR_BCAST && rx_src == BB_SOURCE_SERIAL)
            {
                m_target_source = BB_SOURCE_BLE_BROADCAST;
#if defined(BLE_CENTRAL)
                if (pkt.which_params == protobuf_packet_t_time_sync_bcast_set_tag ||
                    pkt.which_params == protobuf_packet_t_antenna_delay_bcast_set_tag)
                {
                    bb_reliable_track(pkt.which_params, pkt.hdr.seq, p_data, length);
                }
#elif defined(BLE_PERIPHERAL)
                if (pkt.which_params == protobuf_packet_t_bcast_apply_ack_tag)
                {
                    bb_bcast_cache_ack(&pkt, p_data, length);
                }
#endif
                return false;
            }

            /* Never send a packet back to the transport it arrived from. */
            m_target_source = (rx_src == BB_SOURCE_SERIAL)
                                ? BB_SOURCE_BLE
                                : BB_SOURCE_SERIAL;
#if defined(BLE_CENTRAL)
            if (rx_src == BB_SOURCE_BLE && pkt.which_params == protobuf_packet_t_bcast_apply_ack_tag)
            {
                bb_reliable_on_ack(pkt.params.bcast_apply_ack.cmd_seq, pkt.params.bcast_apply_ack.cmd_tag);
                if (bb_ack_is_duplicate(pkt.params.bcast_apply_ack.serial_number,
                                        pkt.params.bcast_apply_ack.cmd_tag,
                                        pkt.params.bcast_apply_ack.cmd_seq))
                {
                    m_route_drop_packet = true;
                    return false;
                }
            }
#endif
            return false;
#endif
        }
    }

    /* Malformed packets are dropped by the CHECK_DST state handler. */
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
