/**
 * @file bb_router.h
 * @brief BLE Bridge Router Layer
 *
 * Implements the Zero-Copy routing strategy. Parses only the Protobuf header
 * to determine the packet's destination. It directly calls transport APIs to bypass
 * the CPU processing if the packet is destined elsewhere.
 */
#ifndef BB_ROUTER_H
#define BB_ROUTER_H

#include <stdint.h>
#include <stdbool.h>
#include "bb_transport.h"
#include "sdk_errors.h"

/**
 * @brief Error codes returned by the routing pipeline.
 */
typedef enum {
    BB_ROUTER_SUCCESS = 0,
    BB_ROUTER_ERR_NULL_POINTER,    /**< Invalid pointer supplied */
    BB_ROUTER_ERR_DECODE_HEADER,   /**< Failed to decode the nanopb network header */
    BB_ROUTER_ERR_UNKNOWN_TARGET   /**< Target Address is not handled by this router */
} bb_router_err_t;

/**
 * @brief Initializes the Routing module and links to Transport / App layers.
 * 
 * @return NRF_SUCCESS on successful configuration.
 */
ret_code_t bb_router_init(void);

/**
 * @brief The primary gateway function. It inspects the payload and decides where it goes.
 * This function should be registered as the callback (`bb_transport_data_handler_t`) 
 * inside `bb_transport_init`.
 * 
 * Flow Logic:
 *  - Dest = PACKET_ADDR_PERIPHERAL -> Forward to bb_app_handle_packet()
 *  - Dest = PACKET_ADDR_TAG / HOST -> Passthrough to correct bb_transport_send_XXX()
 * 
 * @param[in] p_evt The transport event containing the fully de-framed protobuf payload.
 * @return Router processing status.
 */
bb_router_err_t bb_route_packet_evt(bb_transport_evt_t const * p_evt);

#endif // BB_ROUTER_H
