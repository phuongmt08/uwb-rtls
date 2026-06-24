#include "ble_broadcast.h"

#include <string.h>

#define BLE_BROADCAST_MAGIC              0xB7u
#define BLE_BROADCAST_VERSION            0x01u
#define BLE_BROADCAST_AD_TYPE_MANUF      0xFFu
#define BLE_BROADCAST_AD_TYPE_FLAGS      0x01u
#define BLE_BROADCAST_FLAGS_GENERAL      0x06u
#define BLE_BROADCAST_HEADER_SIZE        8u

static uint8_t ble_broadcast_fragment_count(uint8_t packet_len)
{
    return (uint8_t)((packet_len + BLE_BROADCAST_FRAGMENT_PAYLOAD_SIZE - 1u) /
                     BLE_BROADCAST_FRAGMENT_PAYLOAD_SIZE);
}

uint16_t ble_broadcast_crc16(const uint8_t *data, uint8_t len)
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

bool ble_broadcast_encode_fragment(const uint8_t *packet,
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

    uint8_t frag_count = ble_broadcast_fragment_count(packet_len);
    if (frag_count == 0u || frag_count > BLE_BROADCAST_MAX_FRAGMENTS ||
        frag_index >= frag_count || out_size < BLE_BROADCAST_HEADER_SIZE)
    {
        return false;
    }

    uint8_t offset = (uint8_t)(frag_index * BLE_BROADCAST_FRAGMENT_PAYLOAD_SIZE);
    uint8_t remaining = (uint8_t)(packet_len - offset);
    uint8_t payload_len = remaining > BLE_BROADCAST_FRAGMENT_PAYLOAD_SIZE ?
                          BLE_BROADCAST_FRAGMENT_PAYLOAD_SIZE :
                          remaining;

    if (out_size < (uint8_t)(BLE_BROADCAST_HEADER_SIZE + payload_len))
    {
        return false;
    }

    uint16_t crc = ble_broadcast_crc16(packet, packet_len);
    out[0] = BLE_BROADCAST_MAGIC;
    out[1] = BLE_BROADCAST_VERSION;
    out[2] = seq;
    out[3] = frag_index;
    out[4] = frag_count;
    out[5] = packet_len;
    out[6] = (uint8_t)(crc & 0xFFu);
    out[7] = (uint8_t)(crc >> 8);
    memcpy(&out[BLE_BROADCAST_HEADER_SIZE], &packet[offset], payload_len);

    *out_len = (uint8_t)(BLE_BROADCAST_HEADER_SIZE + payload_len);
    return true;
}

bool ble_broadcast_decode_fragment(const uint8_t *manuf_data,
                                   uint8_t manuf_data_len,
                                   ble_broadcast_fragment_t *fragment)
{
    if (manuf_data == NULL || fragment == NULL ||
        manuf_data_len < BLE_BROADCAST_HEADER_SIZE ||
        manuf_data_len > BLE_BROADCAST_MANUF_DATA_MAX_SIZE)
    {
        return false;
    }

    if (manuf_data[0] != BLE_BROADCAST_MAGIC ||
        manuf_data[1] != BLE_BROADCAST_VERSION)
    {
        return false;
    }

    uint8_t frag_index = manuf_data[3];
    uint8_t frag_count = manuf_data[4];
    uint8_t total_len = manuf_data[5];
    uint8_t payload_len = (uint8_t)(manuf_data_len - BLE_BROADCAST_HEADER_SIZE);

    if (total_len == 0u || total_len > BLE_BROADCAST_MAX_PACKET_SIZE ||
        frag_count == 0u || frag_count > BLE_BROADCAST_MAX_FRAGMENTS ||
        frag_count != ble_broadcast_fragment_count(total_len) ||
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
    fragment->payload = &manuf_data[BLE_BROADCAST_HEADER_SIZE];
    fragment->payload_len = payload_len;
    return true;
}

bool ble_broadcast_find_fragment(const uint8_t *adv_data,
                                 uint16_t adv_data_len,
                                 ble_broadcast_fragment_t *fragment)
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

        if (ad_type == BLE_BROADCAST_AD_TYPE_MANUF && field_data_len >= 2u)
        {
            uint16_t company_id = (uint16_t)field_data[0] | ((uint16_t)field_data[1] << 8);
            if (company_id == BLE_BROADCAST_COMPANY_ID)
            {
                return ble_broadcast_decode_fragment(&field_data[2],
                                                     (uint8_t)(field_data_len - 2u),
                                                     fragment);
            }
        }

        pos = (uint16_t)(pos + field_len);
    }

    return false;
}

bool ble_broadcast_build_adv_data(const uint8_t *manuf_data,
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
    adv_data[1] = BLE_BROADCAST_AD_TYPE_FLAGS;
    adv_data[2] = BLE_BROADCAST_FLAGS_GENERAL;
    adv_data[3] = (uint8_t)(1u + 2u + manuf_data_len);
    adv_data[4] = BLE_BROADCAST_AD_TYPE_MANUF;
    adv_data[5] = (uint8_t)(BLE_BROADCAST_COMPANY_ID & 0xFFu);
    adv_data[6] = (uint8_t)(BLE_BROADCAST_COMPANY_ID >> 8);
    memcpy(&adv_data[7], manuf_data, manuf_data_len);

    *adv_data_len = total_len;
    return true;
}

void ble_broadcast_reassembly_reset(ble_broadcast_reassembly_t *ctx)
{
    if (ctx != NULL)
    {
        memset(ctx, 0, sizeof(*ctx));
    }
}

bool ble_broadcast_reassembly_push(ble_broadcast_reassembly_t *ctx,
                                   const ble_broadcast_fragment_t *fragment,
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
        ble_broadcast_reassembly_reset(ctx);
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

    if (ble_broadcast_crc16(ctx->packet, ctx->total_len) != ctx->crc16)
    {
        ble_broadcast_reassembly_reset(ctx);
        return false;
    }

    memcpy(out_packet, ctx->packet, ctx->total_len);
    *out_len = ctx->total_len;
    ble_broadcast_reassembly_reset(ctx);
    return true;
}
