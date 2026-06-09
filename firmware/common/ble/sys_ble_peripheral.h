/**
* @file    sys_ble_peripheral.h
* @brief   BLE peripheral state manager — STM32 side
*
* @details
*   Uses protobuf_ble_state_t directly from protocol.pb.h.
*/

#pragma once

#include <stdint.h>
#include <stdbool.h>
#include "network/network_core.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Initialize BLE peripheral state manager.
 * Must be called before any other sys_ble_peripheral_* functions.
 */
bool sys_ble_peripheral_init(network_core_t *stream);

/**
 * Configure local identity for connection-mode advertising.
 * Should be called before sys_ble_peripheral_enable().
 */
void sys_ble_peripheral_set_config(void);

bool sys_ble_peripheral_enable(bool enable);

void sys_ble_peripheral_on_status_resp(const protobuf_packet_t *pkt);

void sys_ble_peripheral_send_adv_status(void);

void sys_ble_peripheral_process(void);

bool sys_ble_peripheral_is_connected(void);

protobuf_ble_state_t sys_ble_peripheral_get_state(void);

int32_t sys_ble_peripheral_get_rssi(void);

#ifdef __cplusplus
}
#endif
