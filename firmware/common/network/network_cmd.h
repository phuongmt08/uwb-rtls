#ifndef __NETWORK_CMD_H
#define __NETWORK_CMD_H

#include "protos/protocol.pb.h"
#include "network_core.h"
#include <stdbool.h>
#include <stdint.h>

/** Set false via IO task DOUBLE_CLICK; set true via IO task CLICK */
extern bool g_ranging_enabled;
/** Set true while PM wants ranging paused; independent from manual stop/start. */
extern bool g_pm_ranging_blocked;

bool network_cmd_init(network_core_t *stream);
void network_cmd_process(void);
bool network_cmd_process_packet(const protobuf_packet_t *pkt);
void network_cmd_dispatch(const protobuf_packet_t *pkt);
bool network_cmd_is_ble_host_active(void);
bool network_cmd_set_ranging_enabled(bool enabled);
bool network_cmd_is_ranging_enabled(void);

/* ---- Active Command Senders ---- */
#ifdef HAVE_BLE_PERIPHERAL
bool network_send_ble_adv_config_set(network_core_t *stream, uint8_t dst, bool enable, uint32_t serial_number, const char *device_name);
bool network_send_ble_status_get(network_core_t *stream, uint8_t dst);
bool network_send_ble_adv_status(network_core_t *stream, uint8_t dst, const protobuf_ble_adv_status_t *status);
bool network_send_sensor_fusion_result(network_core_t *stream, uint8_t dst, const protobuf_sensor_fusion_result_t *data);
#endif

#endif /* __NETWORK_CMD_H */
