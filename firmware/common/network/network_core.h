#ifndef __NETWORK_CORE_H
#define __NETWORK_CORE_H

#include "config.h"
#include "common.h"
#include "nanopb/pb_encode.h"
#include "nanopb/pb_decode.h"
#include "protos/protocol.pb.h"
#include "../serial/serial.h"
#include <stdbool.h>
#include <stdint.h>

#define NETWORK_CORE_MAX_TRACKERS 4

typedef enum {
    NETWORK_CORE_ACK_STATE_NONE,
    NETWORK_CORE_ACK_STATE_WAITING,
    NETWORK_CORE_ACK_STATE_FOUND,
    NETWORK_CORE_NACK_STATE_FOUND,
    NETWORK_CORE_ACK_STATE_TIMEOUT
} network_core_ack_state_t;

typedef struct network_ack_tracker_s network_ack_tracker_t;
typedef void (*network_ack_tracker_callback_t)(network_ack_tracker_t *p_tracker, const protobuf_packet_t *packet);

struct network_ack_tracker_s {
    network_core_ack_state_t state;
    protobuf_hdr_t packet_header;
    uint32_t start_time;
    uint32_t timeout;
    network_ack_tracker_callback_t callback;
    void *callback_arg;
};

typedef bool (*network_core_packet_handler_t)(const protobuf_packet_t *packet);

typedef struct {
    bool enabled;
    uint8_t interface;
    stream_type_t rx_stream;
    stream_type_t tx_stream;
    protobuf_device_addr_t local_addr;

    uint8_t *rx_packet;
    uint32_t rx_buffer_size;
    uint32_t rx_packet_len;
    uint8_t  tx_seq;

    network_core_ack_state_t ack_state;
    network_ack_tracker_t ack_tracker[NETWORK_CORE_MAX_TRACKERS];

    uint32_t latest_packet_tick;
    network_core_packet_handler_t packet_handler;
} network_core_t;

#define NETWORK_CORE_PKT_HDR_SIZE sizeof(protobuf_hdr_t)

bool network_core_process(network_core_t *core);
bool network_core_register_packet_handler(network_core_t *core, network_core_packet_handler_t handler);
bool network_core_send_packet(network_core_t *core, uint8_t dst, protobuf_packet_t *packet);
int network_core_wait_ack(network_core_t *core, uint8_t seq, uint32_t timeout_ms,
                          network_ack_tracker_callback_t callback, void *callback_arg);
bool network_core_send_ack(network_core_t *core,
                           const protobuf_packet_t *rx_packet,
                           protobuf_packet_ack_response_t response);
bool network_core_encode_packet(const protobuf_packet_t *encode_msg, uint8_t *buff, uint32_t buff_len, uint32_t *len);
bool network_core_decode_packet(const uint8_t *buff, uint32_t len, protobuf_packet_t *decode_msg);
bool network_core_init(network_core_t *core,
                       protobuf_device_addr_t local_addr,
                       uint8_t *rx_buffer,
                       uint32_t rx_buffer_len);

#endif /* __NETWORK_CORE_H */
