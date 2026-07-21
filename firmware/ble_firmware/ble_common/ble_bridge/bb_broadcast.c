/**
 * @file       bb_broadcast.c
 * @copyright  [Your Copyright]
 * @license    [Your License]
 * @version    1.0.0
 * @date       2026-07-21
 * @author     Phuong Mai
 *
 * @brief      Self-contained BLE broadcast module (TX + RX). See bb_broadcast.h.
 *
 *             Broadcast runs on its own dedicated advertising set and its own
 *             BLE observer, so it never has to stop/restore the role's
 *             connectable advertising. On the peripheral this module also owns a
 *             duty-cycled scanner for RX; on the central it piggybacks on the
 *             application scanner that is already running.
 */
/* Includes ----------------------------------------------------------- */
#include "bb_broadcast.h"

#include <string.h>

#include "ble.h"
#include "ble_advdata.h"
#include "nrf_sdh_ble.h"
#include "app_timer.h"
#include "app_util.h"
#include "nrf_log.h"

#include "ble_config.h"

/* Private defines ---------------------------------------------------- */
#define BB_BROADCAST_MAGIC              0xB7u
#define BB_BROADCAST_VERSION            0x01u
#define BB_BROADCAST_AD_TYPE_MANUF      0xFFu
#define BB_BROADCAST_AD_TYPE_FLAGS      0x01u
#define BB_BROADCAST_FLAGS_GENERAL      0x06u
#define BB_BROADCAST_HEADER_SIZE        8u

#define BB_BROADCAST_CONN_CFG_TAG       1
#define BB_BROADCAST_OBSERVER_PRIO      3

/* Legacy fragmentation: ADV events aired per fragment. */
#define BB_BROADCAST_ADV_EVENTS_PER_FRAGMENT   3u

/* TX burst watchdog: recover if a burst never terminates. */
#define BB_BROADCAST_WATCHDOG_MS        3000u

/* RX reassembly (legacy path). */
#define BB_BROADCAST_REASSEMBLY_TIMEOUT_MS     1000u
#if defined(BLE_CENTRAL)
#define BB_BROADCAST_RX_CONTEXTS        4u
#else
#define BB_BROADCAST_RX_CONTEXTS        2u
#endif

/* Peripheral duty-cycled RX scan (battery friendly). window < interval. */
#ifndef BB_BROADCAST_SCAN_INTERVAL_MS
#define BB_BROADCAST_SCAN_INTERVAL_MS   300u
#endif
#ifndef BB_BROADCAST_SCAN_WINDOW_MS
#define BB_BROADCAST_SCAN_WINDOW_MS     30u
#endif

/* Private enumerate/structure ---------------------------------------- */
typedef struct
{
    bool active;
    uint8_t addr[BLE_GAP_ADDR_LEN];
    uint8_t addr_type;
    bb_broadcast_reassembly_t reassembly;
} bb_broadcast_rx_slot_t;

/* Private macros ----------------------------------------------------- */
/* Public variables --------------------------------------------------- */
/* Private variables -------------------------------------------------- */
static bb_broadcast_rx_cb_t m_rx_cb = NULL;
static bool m_initialized = false;

/* ---- TX ---- */
static uint8_t  m_bcast_adv_handle = BLE_GAP_ADV_SET_HANDLE_NOT_SET;
#if BLE_BROADCAST_USE_EXTENDED
static uint8_t  m_bcast_advdata[BLE_GAP_ADV_SET_DATA_SIZE_EXTENDED_MAX_SUPPORTED];
#else
static uint8_t  m_bcast_advdata[BLE_GAP_ADV_SET_DATA_SIZE_MAX];
#endif
static uint8_t  m_bcast_packet[BLE_BROADCAST_MAX_PACKET_SIZE];
static uint8_t  m_bcast_packet_len = 0;
static uint8_t  m_bcast_seq = 0;
static uint8_t  m_bcast_frag_index = 0;
static uint8_t  m_bcast_frag_count = 0;
static bool     m_bcast_active = false;
static uint32_t m_bcast_start_tick = 0;

static ble_gap_adv_data_t m_bcast_adv_data =
{
    .adv_data      = { .p_data = m_bcast_advdata, .len = 0 },
    .scan_rsp_data = { .p_data = NULL, .len = 0 },
};

/* ---- RX ---- */
static bb_broadcast_rx_slot_t m_rx_slots[BB_BROADCAST_RX_CONTEXTS];
static uint8_t  m_rx_packet[BLE_BROADCAST_MAX_PACKET_SIZE];

#if defined(BLE_PERIPHERAL)
static uint8_t  m_scan_buffer_data[BLE_GAP_SCAN_BUFFER_EXTENDED_MIN];
static ble_data_t m_scan_buffer = { .p_data = m_scan_buffer_data, .len = sizeof(m_scan_buffer_data) };
static bool     m_scan_active = false;
#endif

/* Private function prototypes ---------------------------------------- */
static uint8_t bb_broadcast_fragment_count(uint8_t packet_len);
static ret_code_t bcast_start_current_fragment(void);
static void bcast_advance_fragment(void);
static void bcast_finish(void);
static bb_broadcast_rx_slot_t *rx_slot_get(ble_gap_addr_t const *addr);
static void rx_scan_report_handle(ble_gap_evt_adv_report_t const *p_adv_report);
static void bb_broadcast_ble_evt(ble_evt_t const *p_ble_evt, void *p_context);
#if defined(BLE_PERIPHERAL)
static void rx_scan_start(void);
#endif

/* Function definitions ----------------------------------------------- */
/* ----- Wire-format helpers ------------------------------------------ */
static uint8_t bb_broadcast_fragment_count(uint8_t packet_len)
{
    return (uint8_t)((packet_len + BLE_BROADCAST_FRAGMENT_PAYLOAD_SIZE - 1u) /
                     BLE_BROADCAST_FRAGMENT_PAYLOAD_SIZE);
}

uint16_t bb_broadcast_crc16(const uint8_t *data, uint8_t len)
{
    uint16_t crc = 0xFFFFu;

    if (data == NULL)
    {
        return crc;
    }

    for (uint8_t i = 0; i < len; i++)
    {
        crc ^= (uint16_t)data[i] << 8;
        for (uint8_t bit = 0; bit < 8u; bit++)
        {
            if ((crc & 0x8000u) != 0u)
            {
                crc = (uint16_t)((crc << 1) ^ 0x1021u);
            }
            else
            {
                crc <<= 1;
            }
        }
    }

    return crc;
}

bool bb_broadcast_encode_fragment(const uint8_t *packet,
                                  uint8_t packet_len,
                                  uint8_t seq,
                                  uint8_t frag_index,
                                  uint8_t *out,
                                  uint8_t out_size,
                                  uint8_t *out_len)
{
    if (packet == NULL || out == NULL || out_len == NULL || packet_len == 0u ||
        packet_len > BLE_BROADCAST_MAX_PACKET_SIZE)
    {
        return false;
    }

    uint8_t frag_count = bb_broadcast_fragment_count(packet_len);
    if (frag_count == 0u || frag_count > BLE_BROADCAST_MAX_FRAGMENTS ||
        frag_index >= frag_count || out_size < BB_BROADCAST_HEADER_SIZE)
    {
        return false;
    }

    uint8_t offset = (uint8_t)(frag_index * BLE_BROADCAST_FRAGMENT_PAYLOAD_SIZE);
    uint8_t remaining = (uint8_t)(packet_len - offset);
    uint8_t payload_len = remaining > BLE_BROADCAST_FRAGMENT_PAYLOAD_SIZE ?
                          BLE_BROADCAST_FRAGMENT_PAYLOAD_SIZE :
                          remaining;

    if (out_size < (uint8_t)(BB_BROADCAST_HEADER_SIZE + payload_len))
    {
        return false;
    }

    uint16_t crc = bb_broadcast_crc16(packet, packet_len);
    out[0] = BB_BROADCAST_MAGIC;
    out[1] = BB_BROADCAST_VERSION;
    out[2] = seq;
    out[3] = frag_index;
    out[4] = frag_count;
    out[5] = packet_len;
    out[6] = (uint8_t)(crc & 0xFFu);
    out[7] = (uint8_t)(crc >> 8);
    memcpy(&out[BB_BROADCAST_HEADER_SIZE], &packet[offset], payload_len);

    *out_len = (uint8_t)(BB_BROADCAST_HEADER_SIZE + payload_len);
    return true;
}

bool bb_broadcast_decode_fragment(const uint8_t *manuf_data,
                                  uint8_t manuf_data_len,
                                  bb_broadcast_fragment_t *fragment)
{
    if (manuf_data == NULL || fragment == NULL ||
        manuf_data_len < BB_BROADCAST_HEADER_SIZE ||
        manuf_data_len > BLE_BROADCAST_MANUF_DATA_MAX_SIZE)
    {
        return false;
    }

    if (manuf_data[0] != BB_BROADCAST_MAGIC ||
        manuf_data[1] != BB_BROADCAST_VERSION)
    {
        return false;
    }

    uint8_t frag_index = manuf_data[3];
    uint8_t frag_count = manuf_data[4];
    uint8_t total_len = manuf_data[5];
    uint8_t payload_len = (uint8_t)(manuf_data_len - BB_BROADCAST_HEADER_SIZE);

    if (total_len == 0u || total_len > BLE_BROADCAST_MAX_PACKET_SIZE ||
        frag_count == 0u || frag_count > BLE_BROADCAST_MAX_FRAGMENTS ||
        frag_count != bb_broadcast_fragment_count(total_len) ||
        frag_index >= frag_count || payload_len == 0u ||
        payload_len > BLE_BROADCAST_FRAGMENT_PAYLOAD_SIZE)
    {
        return false;
    }

    uint8_t expected_payload = BLE_BROADCAST_FRAGMENT_PAYLOAD_SIZE;
    if (frag_index == (uint8_t)(frag_count - 1u))
    {
        uint8_t last_offset = (uint8_t)(frag_index * BLE_BROADCAST_FRAGMENT_PAYLOAD_SIZE);
        expected_payload = (uint8_t)(total_len - last_offset);
    }

    if (payload_len != expected_payload)
    {
        return false;
    }

    fragment->seq = manuf_data[2];
    fragment->frag_index = frag_index;
    fragment->frag_count = frag_count;
    fragment->total_len = total_len;
    fragment->crc16 = (uint16_t)manuf_data[6] | ((uint16_t)manuf_data[7] << 8);
    fragment->payload = &manuf_data[BB_BROADCAST_HEADER_SIZE];
    fragment->payload_len = payload_len;
    return true;
}

bool bb_broadcast_find_fragment(const uint8_t *adv_data,
                                uint16_t adv_data_len,
                                bb_broadcast_fragment_t *fragment)
{
    if (adv_data == NULL || fragment == NULL)
    {
        return false;
    }

    uint16_t pos = 0;
    while (pos < adv_data_len)
    {
        uint8_t field_len = adv_data[pos++];
        if (field_len == 0u)
        {
            break;
        }
        if ((uint16_t)(pos + field_len) > adv_data_len)
        {
            return false;
        }

        uint8_t ad_type = adv_data[pos];
        const uint8_t *field_data = &adv_data[pos + 1u];
        uint8_t field_data_len = (uint8_t)(field_len - 1u);

        if (ad_type == BB_BROADCAST_AD_TYPE_MANUF && field_data_len >= 2u)
        {
            uint16_t company_id = (uint16_t)field_data[0] | ((uint16_t)field_data[1] << 8);
            if (company_id == BLE_BROADCAST_COMPANY_ID)
            {
                return bb_broadcast_decode_fragment(&field_data[2],
                                                    (uint8_t)(field_data_len - 2u),
                                                    fragment);
            }
        }

        pos = (uint16_t)(pos + field_len);
    }

    return false;
}

bool bb_broadcast_build_adv_data(const uint8_t *manuf_data,
                                 uint8_t manuf_data_len,
                                 uint8_t *adv_data,
                                 uint8_t adv_data_size,
                                 uint8_t *adv_data_len)
{
    if (manuf_data == NULL || adv_data == NULL || adv_data_len == NULL ||
        manuf_data_len == 0u ||
        manuf_data_len > BLE_BROADCAST_MANUF_DATA_MAX_SIZE)
    {
        return false;
    }

    uint8_t total_len = (uint8_t)(7u + manuf_data_len);
    if (adv_data_size < total_len || total_len > BLE_BROADCAST_ADV_DATA_MAX_SIZE)
    {
        return false;
    }

    adv_data[0] = 2u;
    adv_data[1] = BB_BROADCAST_AD_TYPE_FLAGS;
    adv_data[2] = BB_BROADCAST_FLAGS_GENERAL;
    adv_data[3] = (uint8_t)(1u + 2u + manuf_data_len);
    adv_data[4] = BB_BROADCAST_AD_TYPE_MANUF;
    adv_data[5] = (uint8_t)(BLE_BROADCAST_COMPANY_ID & 0xFFu);
    adv_data[6] = (uint8_t)(BLE_BROADCAST_COMPANY_ID >> 8);
    memcpy(&adv_data[7], manuf_data, manuf_data_len);

    *adv_data_len = total_len;
    return true;
}

void bb_broadcast_reassembly_reset(bb_broadcast_reassembly_t *ctx)
{
    if (ctx != NULL)
    {
        memset(ctx, 0, sizeof(*ctx));
    }
}

bool bb_broadcast_reassembly_push(bb_broadcast_reassembly_t *ctx,
                                  const bb_broadcast_fragment_t *fragment,
                                  uint32_t now_tick,
                                  uint32_t timeout_ticks,
                                  uint8_t *out_packet,
                                  uint8_t out_size,
                                  uint8_t *out_len)
{
    if (ctx == NULL || fragment == NULL || out_packet == NULL || out_len == NULL ||
        out_size < fragment->total_len)
    {
        return false;
    }

    bool expired = ctx->active &&
                   timeout_ticks != 0u &&
                   (uint32_t)(now_tick - ctx->last_tick) > timeout_ticks;

    if (!ctx->active || expired ||
        ctx->seq != fragment->seq ||
        ctx->frag_count != fragment->frag_count ||
        ctx->total_len != fragment->total_len ||
        ctx->crc16 != fragment->crc16)
    {
        bb_broadcast_reassembly_reset(ctx);
        ctx->active = true;
        ctx->seq = fragment->seq;
        ctx->frag_count = fragment->frag_count;
        ctx->total_len = fragment->total_len;
        ctx->crc16 = fragment->crc16;
    }

    uint8_t offset = (uint8_t)(fragment->frag_index * BLE_BROADCAST_FRAGMENT_PAYLOAD_SIZE);
    if ((uint16_t)offset + fragment->payload_len > ctx->total_len)
    {
        return false;
    }

    memcpy(&ctx->packet[offset], fragment->payload, fragment->payload_len);
    ctx->received_mask |= (uint16_t)(1u << fragment->frag_index);
    ctx->last_tick = now_tick;

    uint16_t complete_mask = (uint16_t)((1u << ctx->frag_count) - 1u);
    if ((ctx->received_mask & complete_mask) != complete_mask)
    {
        return false;
    }

    if (bb_broadcast_crc16(ctx->packet, ctx->total_len) != ctx->crc16)
    {
        bb_broadcast_reassembly_reset(ctx);
        return false;
    }

    memcpy(out_packet, ctx->packet, ctx->total_len);
    *out_len = ctx->total_len;
    bb_broadcast_reassembly_reset(ctx);
    return true;
}

/* ----- TX: burst state machine (dedicated advertising set) ---------- */
static ret_code_t bcast_start_current_fragment(void)
{
#if BLE_BROADCAST_USE_EXTENDED
    uint8_t manuf_payload[BLE_BROADCAST_EXT_MANUF_TYPE_SIZE + BLE_BROADCAST_MAX_PACKET_SIZE];
    manuf_payload[0] = BLE_BROADCAST_TYPE_EXT_PACKET;
    memcpy(&manuf_payload[BLE_BROADCAST_EXT_MANUF_TYPE_SIZE], m_bcast_packet, m_bcast_packet_len);

    ble_advdata_manuf_data_t manuf_data;
    manuf_data.company_identifier = BLE_BROADCAST_COMPANY_ID;
    manuf_data.data.p_data        = manuf_payload;
    manuf_data.data.size          = (uint16_t)(BLE_BROADCAST_EXT_MANUF_TYPE_SIZE + m_bcast_packet_len);

    ble_advdata_t advdata;
    memset(&advdata, 0, sizeof(advdata));
    advdata.name_type             = BLE_ADVDATA_NO_NAME;
    advdata.p_manuf_specific_data = &manuf_data;

    uint16_t adv_len = sizeof(m_bcast_advdata);
    ret_code_t err_code = ble_advdata_encode(&advdata, m_bcast_advdata, &adv_len);
    if (err_code != NRF_SUCCESS)
    {
        return err_code;
    }

    m_bcast_adv_data.adv_data.p_data      = m_bcast_advdata;
    m_bcast_adv_data.adv_data.len         = adv_len;
    m_bcast_adv_data.scan_rsp_data.p_data = NULL;
    m_bcast_adv_data.scan_rsp_data.len    = 0;

    ble_gap_adv_params_t adv_params;
    memset(&adv_params, 0, sizeof(adv_params));
    adv_params.properties.type = BLE_GAP_ADV_TYPE_EXTENDED_NONCONNECTABLE_NONSCANNABLE_UNDIRECTED;
    adv_params.primary_phy     = BLE_GAP_PHY_1MBPS;
    adv_params.secondary_phy   = BLE_GAP_PHY_1MBPS;
    adv_params.duration        = BLE_GAP_ADV_TIMEOUT_GENERAL_UNLIMITED;
    adv_params.max_adv_evts    = SYSTEM_CONFIG_BCAST_ADV_EVENTS;
    adv_params.filter_policy   = BLE_GAP_ADV_FP_ANY;
    adv_params.interval        = SYSTEM_CONFIG_BCAST_ADV_INTERVAL;
#else
    uint8_t manuf_data_buf[BLE_BROADCAST_MANUF_DATA_MAX_SIZE];
    uint8_t manuf_len = 0;

    if (!bb_broadcast_encode_fragment(m_bcast_packet,
                                      m_bcast_packet_len,
                                      m_bcast_seq,
                                      m_bcast_frag_index,
                                      manuf_data_buf,
                                      sizeof(manuf_data_buf),
                                      &manuf_len))
    {
        return NRF_ERROR_INTERNAL;
    }

    uint8_t adv_len = 0;
    if (!bb_broadcast_build_adv_data(manuf_data_buf,
                                     manuf_len,
                                     m_bcast_advdata,
                                     sizeof(m_bcast_advdata),
                                     &adv_len))
    {
        return NRF_ERROR_DATA_SIZE;
    }

    m_bcast_adv_data.adv_data.p_data      = m_bcast_advdata;
    m_bcast_adv_data.adv_data.len         = adv_len;
    m_bcast_adv_data.scan_rsp_data.p_data = NULL;
    m_bcast_adv_data.scan_rsp_data.len    = 0;

    ble_gap_adv_params_t adv_params;
    memset(&adv_params, 0, sizeof(adv_params));
    adv_params.properties.type = BLE_GAP_ADV_TYPE_NONCONNECTABLE_NONSCANNABLE_UNDIRECTED;
    adv_params.primary_phy     = BLE_GAP_PHY_1MBPS;
    adv_params.duration        = BLE_GAP_ADV_TIMEOUT_GENERAL_UNLIMITED;
    adv_params.max_adv_evts    = BB_BROADCAST_ADV_EVENTS_PER_FRAGMENT;
    adv_params.filter_policy   = BLE_GAP_ADV_FP_ANY;
    adv_params.interval        = SYSTEM_CONFIG_ADV_INTERVAL;
#endif

    ret_code_t err_code = sd_ble_gap_adv_set_configure(&m_bcast_adv_handle, &m_bcast_adv_data, &adv_params);
    if (err_code != NRF_SUCCESS)
    {
        return err_code;
    }

    err_code = sd_ble_gap_tx_power_set(BLE_GAP_TX_POWER_ROLE_ADV, m_bcast_adv_handle, SYSTEM_CONFIG_TX_POWER);
    if (err_code != NRF_SUCCESS)
    {
        return err_code;
    }

    NRF_LOG_INFO("BB BCAST ADV frag %u/%u len=%u",
                 (unsigned)(m_bcast_frag_index + 1u),
                 (unsigned)m_bcast_frag_count,
                 (unsigned)m_bcast_packet_len);
    return sd_ble_gap_adv_start(m_bcast_adv_handle, BB_BROADCAST_CONN_CFG_TAG);
}

static void bcast_finish(void)
{
    m_bcast_active = false;
}

static void bcast_advance_fragment(void)
{
    if (!m_bcast_active)
    {
        return;
    }

    m_bcast_frag_index++;
    if (m_bcast_frag_index >= m_bcast_frag_count)
    {
        NRF_LOG_INFO("BB BCAST ADV burst complete");
        bcast_finish();
        return;
    }

    ret_code_t err_code = bcast_start_current_fragment();
    if (err_code != NRF_SUCCESS)
    {
        NRF_LOG_WARNING("BB BCAST frag start failed: 0x%x", err_code);
        bcast_finish();
    }
}

ret_code_t bb_broadcast_send(const uint8_t *data, uint16_t length)
{
    if (!m_initialized)
    {
        return NRF_ERROR_INVALID_STATE;
    }

    if (data == NULL || length == 0u)
    {
        return NRF_ERROR_NULL;
    }

    if (length > BLE_BROADCAST_MAX_PACKET_SIZE)
    {
        NRF_LOG_WARNING("BB BCAST packet too large: %u > %u",
                        (unsigned)length, (unsigned)BLE_BROADCAST_MAX_PACKET_SIZE);
        return NRF_ERROR_DATA_SIZE;
    }

    if (m_bcast_active)
    {
        return NRF_ERROR_BUSY;
    }

    memcpy(m_bcast_packet, data, length);
    m_bcast_packet_len = (uint8_t)length;
#if BLE_BROADCAST_USE_EXTENDED
    m_bcast_frag_count = 1;
#else
    m_bcast_frag_count = (uint8_t)((length + BLE_BROADCAST_FRAGMENT_PAYLOAD_SIZE - 1u) /
                                   BLE_BROADCAST_FRAGMENT_PAYLOAD_SIZE);
#endif
    m_bcast_frag_index = 0;
    m_bcast_seq++;
    m_bcast_active = true;
    m_bcast_start_tick = app_timer_cnt_get();

    ret_code_t err_code = bcast_start_current_fragment();
    if (err_code != NRF_SUCCESS)
    {
        NRF_LOG_WARNING("BB BCAST start failed: 0x%x", err_code);
        bcast_finish();
        return err_code;
    }

    return NRF_SUCCESS;
}

/* ----- RX: scan report parsing + reassembly ------------------------- */
static bb_broadcast_rx_slot_t *rx_slot_get(ble_gap_addr_t const *addr)
{
    bb_broadcast_rx_slot_t *free_slot = NULL;
    bb_broadcast_rx_slot_t *oldest_slot = &m_rx_slots[0];

    for (uint8_t i = 0; i < BB_BROADCAST_RX_CONTEXTS; i++)
    {
        bb_broadcast_rx_slot_t *slot = &m_rx_slots[i];
        if (slot->active &&
            slot->addr_type == addr->addr_type &&
            memcmp(slot->addr, addr->addr, BLE_GAP_ADDR_LEN) == 0)
        {
            return slot;
        }

        if (!slot->active && free_slot == NULL)
        {
            free_slot = slot;
        }

        if (slot->reassembly.last_tick < oldest_slot->reassembly.last_tick)
        {
            oldest_slot = slot;
        }
    }

    bb_broadcast_rx_slot_t *slot = (free_slot != NULL) ? free_slot : oldest_slot;
    memset(slot, 0, sizeof(*slot));
    slot->active = true;
    slot->addr_type = addr->addr_type;
    memcpy(slot->addr, addr->addr, BLE_GAP_ADDR_LEN);
    return slot;
}

static void rx_scan_report_handle(ble_gap_evt_adv_report_t const *p_adv_report)
{
#if BLE_BROADCAST_USE_EXTENDED
    if (p_adv_report->type.extended_pdu)
    {
        const uint8_t *p_payload = NULL;
        uint16_t payload_len = 0;
        uint16_t pos = 0;
        while (pos < p_adv_report->data.len)
        {
            uint8_t field_len = p_adv_report->data.p_data[pos++];
            if (field_len == 0u)
            {
                break;
            }
            if ((uint16_t)(pos + field_len) > p_adv_report->data.len)
            {
                break;
            }

            uint8_t ad_type = p_adv_report->data.p_data[pos];
            const uint8_t *field_data = &p_adv_report->data.p_data[pos + 1u];
            uint8_t field_data_len = (uint8_t)(field_len - 1u);

            if (ad_type == BB_BROADCAST_AD_TYPE_MANUF && field_data_len >= 2u)
            {
                uint16_t company_id = (uint16_t)field_data[0] | ((uint16_t)field_data[1] << 8);
                if (company_id == BLE_BROADCAST_COMPANY_ID &&
                    (field_data_len - 2u) > BLE_BROADCAST_EXT_MANUF_TYPE_SIZE &&
                    field_data[2] == BLE_BROADCAST_TYPE_EXT_PACKET)
                {
                    p_payload = &field_data[2u + BLE_BROADCAST_EXT_MANUF_TYPE_SIZE];
                    payload_len = (uint16_t)(field_data_len - 2u - BLE_BROADCAST_EXT_MANUF_TYPE_SIZE);
                    break;
                }
            }
            pos = (uint16_t)(pos + field_len);
        }

        if (p_payload != NULL && payload_len > 0)
        {
            NRF_LOG_INFO("BB BCAST RX EXT complete len=%u", (unsigned)payload_len);
            if (m_rx_cb != NULL)
            {
                m_rx_cb(p_payload, payload_len);
            }
            return;
        }
    }
#endif

    bb_broadcast_fragment_t fragment;
    if (!bb_broadcast_find_fragment(p_adv_report->data.p_data,
                                    p_adv_report->data.len,
                                    &fragment))
    {
        return;
    }

    bb_broadcast_rx_slot_t *slot = rx_slot_get(&p_adv_report->peer_addr);
    uint8_t packet_len = 0;
    bool complete = bb_broadcast_reassembly_push(&slot->reassembly,
                                                 &fragment,
                                                 app_timer_cnt_get(),
                                                 APP_TIMER_TICKS(BB_BROADCAST_REASSEMBLY_TIMEOUT_MS),
                                                 m_rx_packet,
                                                 sizeof(m_rx_packet),
                                                 &packet_len);
    if (!complete)
    {
        return;
    }

    NRF_LOG_INFO("BB BCAST RX complete len=%u", (unsigned)packet_len);
    if (m_rx_cb != NULL)
    {
        m_rx_cb(m_rx_packet, packet_len);
    }
}

#if defined(BLE_PERIPHERAL)
static void rx_scan_start(void)
{
    ble_gap_scan_params_t scan_params;
    memset(&scan_params, 0, sizeof(scan_params));
    scan_params.active        = 0; /* passive: broadcast carries all data */
    scan_params.interval      = (uint16_t)MSEC_TO_UNITS(BB_BROADCAST_SCAN_INTERVAL_MS, UNIT_0_625_MS);
    scan_params.window        = (uint16_t)MSEC_TO_UNITS(BB_BROADCAST_SCAN_WINDOW_MS, UNIT_0_625_MS);
    scan_params.timeout       = BLE_GAP_SCAN_TIMEOUT_UNLIMITED;
    scan_params.filter_policy = BLE_GAP_SCAN_FP_ACCEPT_ALL;
#if BLE_BROADCAST_USE_EXTENDED
    scan_params.extended      = 1;
#endif
    scan_params.scan_phys     = BLE_GAP_PHY_1MBPS;

    ret_code_t err_code = sd_ble_gap_scan_start(&scan_params, &m_scan_buffer);
    if (err_code == NRF_SUCCESS || err_code == NRF_ERROR_INVALID_STATE)
    {
        m_scan_active = true;
    }
    else
    {
        m_scan_active = false;
        NRF_LOG_WARNING("BB BCAST scan start failed: 0x%x", err_code);
    }
}
#endif

/* ----- BLE observer ------------------------------------------------- */
static void bb_broadcast_ble_evt(ble_evt_t const *p_ble_evt, void *p_context)
{
    (void)p_context;

    switch (p_ble_evt->header.evt_id)
    {
        case BLE_GAP_EVT_ADV_SET_TERMINATED:
            /* Only our dedicated broadcast set drives the burst forward. */
            if (m_bcast_active &&
                p_ble_evt->evt.gap_evt.params.adv_set_terminated.adv_handle == m_bcast_adv_handle)
            {
                bcast_advance_fragment();
            }
            break;

        case BLE_GAP_EVT_ADV_REPORT:
            rx_scan_report_handle(&p_ble_evt->evt.gap_evt.params.adv_report);
#if defined(BLE_PERIPHERAL)
            /* Peripheral owns the scanner: re-arm to keep receiving. */
            {
                ret_code_t err_code = sd_ble_gap_scan_start(NULL, &m_scan_buffer);
                if (err_code != NRF_SUCCESS && err_code != NRF_ERROR_INVALID_STATE)
                {
                    m_scan_active = false;
                }
            }
#endif
            break;

#if defined(BLE_PERIPHERAL)
        case BLE_GAP_EVT_TIMEOUT:
            if (p_ble_evt->evt.gap_evt.params.timeout.src == BLE_GAP_TIMEOUT_SRC_SCAN)
            {
                m_scan_active = false;
            }
            break;
#endif

        default:
            break;
    }
}

NRF_SDH_BLE_OBSERVER(m_bb_broadcast_observer, BB_BROADCAST_OBSERVER_PRIO, bb_broadcast_ble_evt, NULL);

/* ----- Public init / maintenance ------------------------------------ */
void bb_broadcast_register_rx_cb(bb_broadcast_rx_cb_t cb)
{
    m_rx_cb = cb;
}

ret_code_t bb_broadcast_init(void)
{
    m_bcast_active = false;
    m_bcast_adv_handle = BLE_GAP_ADV_SET_HANDLE_NOT_SET;
    memset(m_rx_slots, 0, sizeof(m_rx_slots));
    m_initialized = true;

#if defined(BLE_PERIPHERAL)
    /* Peripheral has no other scanner; start the duty-cycled RX scan. */
    rx_scan_start();
#endif

    return NRF_SUCCESS;
}

void bb_broadcast_process(void)
{
    if (!m_initialized)
    {
        return;
    }

    /* TX burst watchdog: recover if a burst never terminates. */
    if (m_bcast_active &&
        app_timer_cnt_diff_compute(app_timer_cnt_get(), m_bcast_start_tick) >
            APP_TIMER_TICKS(BB_BROADCAST_WATCHDOG_MS))
    {
        NRF_LOG_WARNING("BB BCAST watchdog fired, forcing finish");
        bcast_finish();
    }

#if defined(BLE_PERIPHERAL)
    /* Re-arm the RX scanner if it dropped (timeout / transient error). */
    if (!m_scan_active)
    {
        rx_scan_start();
    }
#endif
}

/* End of file -------------------------------------------------------- */
