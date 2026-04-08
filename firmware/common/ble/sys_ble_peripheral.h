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
* Called on every state transition (old → new).
* Runs inside network_core packet handler context — keep it short.
*/
typedef void (*sys_ble_on_state_change_t)(protobuf_ble_state_t old_state,
                                         protobuf_ble_state_t new_state,
                                         void                *user_arg);

/**
* Called when a central device connects.
* Bootloader uses this to arm the FOTA receiver.
*/
typedef void (*sys_ble_on_connected_t)(int32_t rssi_dbm, void *user_arg);

/**
* Called when the central disconnects (or connection times out).
*/
typedef void (*sys_ble_on_disconnected_t)(void *user_arg);

typedef struct {
   sys_ble_on_state_change_t on_state_change;  /**< May be NULL */
   sys_ble_on_connected_t    on_connected;     /**< May be NULL */
   sys_ble_on_disconnected_t on_disconnected;  /**< May be NULL */
   void                     *user_arg;         /**< Passed to every callback */
} sys_ble_callbacks_t;

typedef struct {
   network_core_t        *stream;        /**< Network core (serial <-> nRF)    */
   protobuf_ble_state_t   state;         /**< Current BLE state                */
   sys_ble_callbacks_t    callbacks;     /**< User-supplied event callbacks     */
   int32_t                rssi_dbm;      /**< Last known RSSI (connected only)  */
   bool                   enabled;       /**< Module active flag                */
   uint32_t               serial_number; /**< Local SN for advertising         */
   char                   device_name[32];/**< Local name for advertising        */
} sys_ble_peripheral_t;

bool sys_ble_peripheral_init(sys_ble_peripheral_t  *ble,
                             network_core_t        *stream,
                             const sys_ble_callbacks_t *callbacks);

/**
 * Configure local identity for connection-mode advertising.
 * Should be called before sys_ble_peripheral_enable().
 */
void sys_ble_peripheral_set_config(sys_ble_peripheral_t *ble,
                                   uint32_t              serial_number,
                                   const char           *device_name);

bool sys_ble_peripheral_enable(sys_ble_peripheral_t *ble, bool enable);

void sys_ble_peripheral_on_status_resp(sys_ble_peripheral_t  *ble,
                                       const protobuf_packet_t *pkt);

void sys_ble_peripheral_process(sys_ble_peripheral_t *ble);

bool sys_ble_peripheral_is_connected(const sys_ble_peripheral_t *ble);

protobuf_ble_state_t sys_ble_peripheral_get_state(const sys_ble_peripheral_t *ble);

int32_t sys_ble_peripheral_get_rssi(const sys_ble_peripheral_t *ble);

#ifdef __cplusplus
}
#endif
