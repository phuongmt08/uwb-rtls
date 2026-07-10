/**
 * @file       bb_transport.h
 * @copyright  [Your Copyright]
 * @license    [Your License]
 * @version    1.0.0
 * @date       2026-04-08
 * @author     Dong Son
 *
 * @brief      
 */
/* Define to prevent recursive inclusion ------------------------------ */
#ifndef BB_TRANSPORT_H
#define BB_TRANSPORT_H

/* Includes ----------------------------------------------------------- */
#include <stdint.h>
#include <stdbool.h>
#include "sdk_errors.h"

/* Public defines ----------------------------------------------------- */

/* Public enumerate/structure ----------------------------------------- */
/**
 * @brief Source of the incoming network packet
 */
typedef enum 
{
    BB_SOURCE_SERIAL = 0,
    BB_SOURCE_BLE = 1,
    BB_SOURCE_BLE_BROADCAST = 2,
} bb_packet_source_t;

/**
 * @brief Router callback invoked when a complete frame is received.
 */
typedef void (*bb_transport_state_transition_cb_t)(void);

/* Public macros ------------------------------------------------------ */
/* Public variables --------------------------------------------------- */
/* Public function prototypes ----------------------------------------- */
/**
 * @brief Initializes the transport layer (HDLC state machines, buffer allocations)
 *
 * @param[in] p_payload_buf Router-owned buffer used to store protobuf payloads.
 * @param[in] p_payload_len Pointer receiving the decoded payload length.
 * @param[in] max_len Maximum payload buffer size.
 * @param[in] cb Callback invoked after HDLC decode succeeds.
 * @return NRF_SUCCESS on successful initialization.
 */
ret_code_t bb_transport_init(uint8_t * p_payload_buf, uint16_t * p_payload_len, uint16_t max_len, bb_transport_state_transition_cb_t cb);

/**
 * @brief Runs the transport state machine (polls bsp_uart, decodes HDLC)
 * Should be called periodically from bb_router_process or main loop.
 */
void bb_transport_process(void);

/**
 * @brief Checks whether a decoded HDLC frame is ready in the shared buffer.
 * @return true if a packet is ready.
 */
bool bb_transport_is_packet_ready(void);

/**
 * @brief Clears the packet-ready flag so the next packet can be received.
 */
void bb_transport_clear_packet_ready(void);

/**
 * @brief Sends data through the selected output.
 *        SERIAL output is HDLC-framed. BLE output carries raw protobuf.
 *        BLE_BROADCAST output advertises raw protobuf fragments.
 *
 * @param[in] p_data Pointer to raw Protobuf payload.
 * @param[in] length Size of the payload.
 * @param[in] tx_source Destination source mode (Serial / BLE).
 * @return NRF_SUCCESS if success.
 */
ret_code_t bb_transport_send_data(uint8_t const * p_data, uint16_t  length, bb_packet_source_t tx_source);

/**
 * @brief Returns the interface that received the packet currently being processed.
 * @return BB_SOURCE_SERIAL or BB_SOURCE_BLE.
 */
bb_packet_source_t bb_transport_get_rx_source(void);

#endif // BB_TRANSPORT_H
