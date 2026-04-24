#ifndef __NETWORK_CMD_H
#define __NETWORK_CMD_H

#include "protos/protocol.pb.h"
#include "network_core.h"
#include <stdbool.h>
#include <stdint.h>

typedef struct {
    network_core_t *stream;
    bool enabled;

    protobuf_packet_t last_resp;
    bool resp_pending;
    uint8_t resp_retry_left;
    uint32_t resp_deadline_ms;
} network_cmd_t;

bool network_cmd_init(network_cmd_t *cmd, network_core_t *stream);
void network_cmd_process(network_cmd_t *cmd);
bool network_cmd_process_packet(network_cmd_t *cmd, const protobuf_packet_t *pkt);
void network_cmd_dispatch(network_cmd_t *cmd, const protobuf_packet_t *pkt);
bool network_cmd_is_distance_streaming(const network_cmd_t *cmd);
bool network_cmd_is_log_streaming(const network_cmd_t *cmd);

#endif /* __NETWORK_CMD_H */
