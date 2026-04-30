#ifndef __NETWORK_CMD_H
#define __NETWORK_CMD_H

#include "protos/protocol.pb.h"
#include "network_core.h"
#include <stdbool.h>
#include <stdint.h>

bool network_cmd_init(network_core_t *stream);
void network_cmd_process(void);
bool network_cmd_process_packet(const protobuf_packet_t *pkt);
void network_cmd_dispatch(const protobuf_packet_t *pkt);

/* ---- Active Command Senders ---- */
#ifdef HAVE_BLE_PERIPHERAL
bool network_send_ble_adv_config_set(network_core_t *stream, uint8_t dst, bool enable, uint32_t serial_number, const char *device_name);
bool network_send_ble_status_get(network_core_t *stream, uint8_t dst);
bool network_send_ble_adv_status(network_core_t *stream, uint8_t dst, const protobuf_ble_adv_status_t *status);
#endif

#endif /* __NETWORK_CMD_H */
