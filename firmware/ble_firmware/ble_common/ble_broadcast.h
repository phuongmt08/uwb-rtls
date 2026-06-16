#ifndef BLE_BROADCAST_H
#define BLE_BROADCAST_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define BLE_BROADCAST_COMPANY_ID              0xFFFFu
#define BLE_BROADCAST_TYPE_ADV_STATUS         0xA1u
#define BLE_BROADCAST_TYPE_EXT_PACKET         0xB0u
#define BLE_BROADCAST_EXT_ADV_DATA_SIZE       255u
#define BLE_BROADCAST_EXT_MANUF_TYPE_SIZE     1u
#define BLE_BROADCAST_EXT_MANUF_OVERHEAD      (4u + BLE_BROADCAST_EXT_MANUF_TYPE_SIZE)
#define BLE_BROADCAST_MAX_PACKET_SIZE         (BLE_BROADCAST_EXT_ADV_DATA_SIZE - BLE_BROADCAST_EXT_MANUF_OVERHEAD)
#define BLE_BROADCAST_FRAGMENT_PAYLOAD_SIZE   16u
#define BLE_BROADCAST_MAX_FRAGMENTS           16u
#define BLE_BROADCAST_MANUF_DATA_MAX_SIZE     24u
#define BLE_BROADCAST_ADV_DATA_MAX_SIZE       31u

typedef struct
{
    uint8_t seq;
    uint8_t frag_index;
    uint8_t frag_count;
    uint8_t total_len;
    uint16_t crc16;
    const uint8_t *payload;
    uint8_t payload_len;
} ble_broadcast_fragment_t;

typedef struct
{
    bool active;
    uint8_t seq;
    uint8_t frag_count;
    uint8_t total_len;
    uint16_t crc16;
    uint16_t received_mask;
    uint32_t last_tick;
    uint8_t packet[BLE_BROADCAST_MAX_PACKET_SIZE];
} ble_broadcast_reassembly_t;

uint16_t ble_broadcast_crc16(const uint8_t *data, uint8_t len);

bool ble_broadcast_encode_fragment(const uint8_t *packet,
                                   uint8_t packet_len,
                                   uint8_t seq,
                                   uint8_t frag_index,
                                   uint8_t *out,
                                   uint8_t out_size,
                                   uint8_t *out_len);

bool ble_broadcast_decode_fragment(const uint8_t *manuf_data,
                                   uint8_t manuf_data_len,
                                   ble_broadcast_fragment_t *fragment);

bool ble_broadcast_find_fragment(const uint8_t *adv_data,
                                 uint16_t adv_data_len,
                                 ble_broadcast_fragment_t *fragment);

bool ble_broadcast_build_adv_data(const uint8_t *manuf_data,
                                  uint8_t manuf_data_len,
                                  uint8_t *adv_data,
                                  uint8_t adv_data_size,
                                  uint8_t *adv_data_len);

void ble_broadcast_reassembly_reset(ble_broadcast_reassembly_t *ctx);

bool ble_broadcast_reassembly_push(ble_broadcast_reassembly_t *ctx,
                                   const ble_broadcast_fragment_t *fragment,
                                   uint32_t now_tick,
                                   uint32_t timeout_ticks,
                                   uint8_t *out_packet,
                                   uint8_t out_size,
                                   uint8_t *out_len);

#ifdef __cplusplus
}
#endif

#endif /* BLE_BROADCAST_H */
