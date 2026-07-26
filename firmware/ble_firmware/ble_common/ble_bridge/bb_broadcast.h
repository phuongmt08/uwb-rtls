/**
 * @file       bb_broadcast.h
 * @copyright  [Your Copyright]
 * @license    [Your License]
 * @version    1.0.0
 * @date       2026-07-21
 * @author     Phuong Mai
 *
 * @brief      Self-contained BLE connectionless-broadcast module (TX + RX).
 *
 *             Owns the on-air packet format (fragmentation, CRC, manufacturer
 *             advertising data), the transmit burst state machine, and the
 *             receive path (scanning + reassembly). All broadcast advertising
 *             and scanning run on a dedicated advertising set driven by this
 *             module's own BLE observer. Role behaviour is selected at compile
 *             time via BLE_PERIPHERAL / BLE_CENTRAL: the peripheral owns a
 *             duty-cycled scanner for RX, while the central piggybacks RX on
 *             the application scanner that is already running.
 */
/* Define to prevent recursive inclusion ------------------------------ */
#ifndef BB_BROADCAST_H
#define BB_BROADCAST_H

/* Includes ----------------------------------------------------------- */
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "sdk_errors.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Public defines ----------------------------------------------------- */
#define BLE_BROADCAST_COMPANY_ID              0xFFFFu
#define BLE_BROADCAST_TYPE_ADV_STATUS         0xA1u
#define BLE_BROADCAST_TYPE_EXT_PACKET         0xB0u
#define BLE_BROADCAST_EXT_ADV_DATA_SIZE       255u
#define BLE_BROADCAST_EXT_MANUF_TYPE_SIZE     1u
#define BLE_BROADCAST_EXT_MANUF_OVERHEAD      (4u + BLE_BROADCAST_EXT_MANUF_TYPE_SIZE)
#define BLE_BROADCAST_MAX_PACKET_SIZE         (BLE_BROADCAST_EXT_ADV_DATA_SIZE - BLE_BROADCAST_EXT_MANUF_OVERHEAD)
#define BLE_BROADCAST_TYPED_LEGACY_MAX_PACKET_SIZE \
    (BLE_BROADCAST_ADV_DATA_MAX_SIZE - BLE_BROADCAST_EXT_MANUF_OVERHEAD)
#define BLE_BROADCAST_FRAGMENT_PAYLOAD_SIZE   16u
#define BLE_BROADCAST_MAX_FRAGMENTS           16u
#define BLE_BROADCAST_MANUF_DATA_MAX_SIZE     24u
#define BLE_BROADCAST_ADV_DATA_MAX_SIZE       31u

/* Public enumerate/structure ----------------------------------------- */
/**
 * @brief One decoded broadcast fragment (legacy fragmentation path).
 */
typedef struct
{
    uint8_t seq;
    uint8_t frag_index;
    uint8_t frag_count;
    uint8_t total_len;
    uint16_t crc16;
    const uint8_t *payload;
    uint8_t payload_len;
} bb_broadcast_fragment_t;

/**
 * @brief Per-peer reassembly context for the legacy fragmentation path.
 */
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
} bb_broadcast_reassembly_t;

/**
 * @brief Invoked (from BLE event context) with a fully reassembled packet.
 */
typedef void (*bb_broadcast_rx_cb_t)(const uint8_t *data, uint16_t length);

/**
 * @brief Optional advertising-handle arbitration hooks.
 *
 *        Roles that already own an advertising set (the peripheral) can lend
 *        that handle to the broadcaster for one short burst. Roles without an
 *        existing advertiser (the central) leave these hooks unset and the
 *        broadcast module configures its own handle.
 */
typedef ret_code_t (*bb_broadcast_adv_acquire_cb_t)(uint8_t *adv_handle);
typedef void (*bb_broadcast_adv_release_cb_t)(uint8_t adv_handle);

/* Public macros ------------------------------------------------------ */

/* Public function prototypes ----------------------------------------- */
/**
 * @brief Register optional hooks used to borrow an existing advertising set.
 *        Call before @ref bb_broadcast_init. Pass NULL/NULL to let this module
 *        own its advertising set.
 */
void bb_broadcast_adv_hooks_set(bb_broadcast_adv_acquire_cb_t acquire_cb,
                                bb_broadcast_adv_release_cb_t release_cb);

/**
 * @brief Initialise the module: reset state and (peripheral) start the RX scan.
 *        Call once after the SoftDevice is enabled.
 * @return NRF_SUCCESS on success.
 */
ret_code_t bb_broadcast_init(void);

/**
 * @brief Transmit a raw protobuf packet as a connectionless BLE broadcast.
 * @param[in] data   Pointer to the raw protobuf payload.
 * @param[in] length Payload size in bytes.
 * @return NRF_SUCCESS, NRF_ERROR_BUSY if a burst is in flight, else a size/param error.
 */
ret_code_t bb_broadcast_send(const uint8_t *data, uint16_t length);

/**
 * @brief Register the callback that receives reassembled broadcast packets.
 * @param[in] cb Callback invoked with a complete packet.
 */
void bb_broadcast_register_rx_cb(bb_broadcast_rx_cb_t cb);

/**
 * @brief Periodic maintenance: TX burst watchdog and (peripheral) scan re-arm.
 *        Call from the main loop.
 */
void bb_broadcast_process(void);

/**
 * @brief CRC-16/CCITT-FALSE over a buffer (broadcast integrity check).
 */
uint16_t bb_broadcast_crc16(const uint8_t *data, uint8_t len);

/**
 * @brief Encode one fragment of a packet into manufacturer-specific data.
 */
bool bb_broadcast_encode_fragment(const uint8_t *packet,
                                  uint8_t packet_len,
                                  uint8_t seq,
                                  uint8_t frag_index,
                                  uint8_t *out,
                                  uint8_t out_size,
                                  uint8_t *out_len);

/**
 * @brief Decode and validate a manufacturer-specific data blob into a fragment.
 */
bool bb_broadcast_decode_fragment(const uint8_t *manuf_data,
                                  uint8_t manuf_data_len,
                                  bb_broadcast_fragment_t *fragment);

/**
 * @brief Scan an advertising payload for our manufacturer fragment.
 */
bool bb_broadcast_find_fragment(const uint8_t *adv_data,
                                uint16_t adv_data_len,
                                bb_broadcast_fragment_t *fragment);

/**
 * @brief Build a legacy advertising payload wrapping the manufacturer data.
 */
bool bb_broadcast_build_adv_data(const uint8_t *manuf_data,
                                 uint8_t manuf_data_len,
                                 uint8_t *adv_data,
                                 uint8_t adv_data_size,
                                 uint8_t *adv_data_len);

/**
 * @brief Reset a reassembly context.
 */
void bb_broadcast_reassembly_reset(bb_broadcast_reassembly_t *ctx);

/**
 * @brief Push a fragment into a reassembly context; emit the packet when full.
 */
bool bb_broadcast_reassembly_push(bb_broadcast_reassembly_t *ctx,
                                  const bb_broadcast_fragment_t *fragment,
                                  uint32_t now_tick,
                                  uint32_t timeout_ticks,
                                  uint8_t *out_packet,
                                  uint8_t out_size,
                                  uint8_t *out_len);

#ifdef __cplusplus
}
#endif

#endif /* BB_BROADCAST_H */
