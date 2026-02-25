/**
 * @file       network_cmd.h
 * @copyright
 * @license
 * @version    1.0.0
 * @date       2025
 * @author
 * @brief 
 */

#ifndef __NETWORK_CMD_H
#define __NETWORK_CMD_H

/* Includes ----------------------------------------------------------------- */
#include "protos/protocol.pb.h"
#include "sys_stream.h"
#include <stdbool.h>
#include <stdint.h>

/**
 * @brief Network command handler context
 */
typedef struct {
    sys_stream_t *stream;
    bool enabled;

    protocol_packet_t last_resp;
    bool resp_pending;
    uint8_t resp_retry_left;
    uint32_t resp_deadline_ms;
} network_cmd_t;

/* Public APIs -------------------------------------------------------------- */

/**
 * @brief Initialize network command handler
 * @param cmd Pointer to network_cmd context
 * @param stream Pointer to sys_stream instance
 * @return true if successful
 */
bool network_cmd_init(network_cmd_t *cmd, sys_stream_t *stream);

/**
 * @brief Process network commands from sys_stream
 * @param cmd Pointer to network_cmd context
 * @return true if processing was successful, false otherwise
 * 
 * @note This function:
 *       1. Attempts to receive a packet from sys_stream
 *       2. Processes the packet as a command
 *       3. Sends appropriate response
 */
void network_cmd_process(network_cmd_t *cmd);

/**
 * @brief Dispatch/process one packet (skeleton: no command handlers)
 */
bool network_cmd_process_packet(network_cmd_t *cmd, const protocol_packet_t *pkt);

/**
 * @brief Dispatch wrapper called by stream callback
 */
void network_cmd_dispatch(network_cmd_t *cmd, const protocol_packet_t *pkt);

/**
 * @brief Check if distance streaming is active
 * @return Always false in skeleton mode
 */
bool network_cmd_is_distance_streaming(const network_cmd_t *cmd);

/**
 * @brief Check if log streaming is active
 * @return Always false in skeleton mode
 */
bool network_cmd_is_log_streaming(const network_cmd_t *cmd);

#endif /* __NETWORK_CMD_H */
