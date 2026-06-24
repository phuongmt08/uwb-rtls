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
#include "sdk_errors.h"

#define BB_ROUTER_MCU_BLE_PACKET_ID_COUNT 7

/*
 * Peripheral RX counters for MCU <-> BLE packets by array index:
 * [0] cmd_id 3:  protobuf_packet_t_ack_tag
 * [1] cmd_id 39: protobuf_packet_t_ble_adv_config_set_tag
 * [2] cmd_id 40: protobuf_packet_t_ble_status_get_tag
 * [3] cmd_id 41: protobuf_packet_t_ble_status_resp_tag
 * [4] cmd_id 42: protobuf_packet_t_ble_adv_status_tag
 * [5] cmd_id 43: protobuf_packet_t_log_data_tag
 * [6] cmd_id 69: protobuf_packet_t_ble_adv_config_request_tag
 */
extern volatile uint32_t g_bb_router_mcu_rx_total_count;
extern volatile uint32_t g_bb_router_mcu_rx_id_count[BB_ROUTER_MCU_BLE_PACKET_ID_COUNT];

/**
 * @brief Error codes returned by the routing pipeline.
 */
typedef enum {
    BB_ROUTER_SUCCESS = 0,
    BB_ROUTER_ERR_NULL_POINTER,
    BB_ROUTER_ERR_DECODE_HEADER,
    BB_ROUTER_ERR_UNKNOWN_TARGET
} bb_router_err_t;

/**
 * @brief Represents the internal state of the Router.
 */
typedef enum {
    BB_ROUTER_STATE_IDLE,
    BB_ROUTER_STATE_CHECK_DST,
    BB_ROUTER_STATE_PROCESS_CMD,
    BB_ROUTER_STATE_FORWARD
} bb_router_state_t;

/**
 * @brief Initializes the Routing module.
 *
 * @return NRF_SUCCESS on successful configuration.
 */
ret_code_t bb_router_init(void);

/**
 * @brief Runs the central routing state machine.
 * Periodically calls the transport layer to check for new packets and triggers actions.
 * Should be called periodically in the main loop.
 */
void bb_router_process(void);

#endif // BB_ROUTER_H
