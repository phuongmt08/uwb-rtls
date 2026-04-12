#include "network_core.h"
#ifdef BOOTLOADER
#include "bsp_util_bl.h"
#include "sys_logger_bl.h"
#else
#include "bsp_util.h"
#include "sys_logger.h"
#endif
#include <string.h>

#define OBJECT_CODE LOG_OBJECT_CODE_NETWORK

static const uint16_t network_core_skip_ack_tb[] = {
    protobuf_packet_t_ack_tag,
    protobuf_packet_t_ble_adv_status_tag,
    // protobuf_packet_t_log_erase_tag,
//    protobuf_packet_t_anchor_distance_tag,
//    protobuf_packet_t_tag_position_tag,
    protobuf_packet_t_flash_write_tag
};

static bool network_core_encode_and_send(network_core_t *core,
                                         stream_type_t stream,
                                         const protobuf_packet_t *packet)
{
    uint8_t buf[core->rx_buffer_size];
    uint32_t len = 0;

    if (!network_core_encode_packet(packet, buf, sizeof(buf), &len)) {
        uint32_t which = packet ? packet->which_params : 0u;
        uint32_t dst = (packet && packet->has_hdr && packet->hdr.has_addr) ?
                       (uint32_t)packet->hdr.addr.dst : 0xFFu;
        RLOG_E(OBJECT_CODE, 0x01,
               "encode fail tag=%lu dst=%lu stream=%d buf=%lu",
               which, dst, (int)stream, (unsigned long)sizeof(buf));
        return false;
    }

    int wr = _write(stream, (char *)buf, (int)len, 0);
    if (wr <= 0) {
        uint32_t which = packet ? packet->which_params : 0u;
        uint32_t dst = (packet && packet->has_hdr && packet->hdr.has_addr) ?
                       (uint32_t)packet->hdr.addr.dst : 0xFFu;
        RLOG_E(OBJECT_CODE, 0x02,
               "tx fail tag=%lu dst=%lu stream=%d enc_len=%lu wr=%d",
               which, dst, (int)stream, (unsigned long)len, wr);
        return false;
    }

    return true;
}

static void network_core_finalize_tracker(network_ack_tracker_t *t,
                                          const protobuf_packet_t *packet)
{
    if (t->callback) {
        t->callback(t, packet);
    }
    t->state = NETWORK_CORE_ACK_STATE_NONE;
}

static network_ack_tracker_t *network_core_find_tracker(network_core_t *core,
                                                         network_core_ack_state_t state)
{
    for (int i = 0; i < NETWORK_CORE_MAX_TRACKERS; i++) {
        if (core->ack_tracker[i].state == state) {
            return &core->ack_tracker[i];
        }
    }
    return NULL;
}

static bool network_core_is_ack_positive(protobuf_packet_ack_response_t response)
{
    return response == protobuf_packet_ack_response_t_PACKET_ACK_RESPONSE_ACK;
}

static bool network_core_try_receive(network_core_t *core, stream_type_t in_stream, protobuf_packet_t *out_pkt)
{
    CHECK(core && out_pkt, false);

    if (core->rx_packet_len == 0) {
        int n = _read(in_stream, (char*)core->rx_packet, (int)core->rx_buffer_size, 0);
        CHECK(n > 0, false);
        core->rx_packet_len = (uint32_t)n;
    }

    if (!network_core_decode_packet(core->rx_packet, core->rx_packet_len, out_pkt)) {
        core->rx_packet_len = 0;
        return false;
    }

    core->rx_packet_len = 0;
    return true;
}

static void network_core_send_ble_packet(network_core_t *core, stream_type_t tx_stream, const protobuf_packet_t *packet)
{
    network_core_encode_and_send(core, tx_stream, packet);
}

static stream_type_t network_core_dst_to_tx_stream(protobuf_device_addr_t dst)
{
    switch (dst) {
        case protobuf_PACKET_ADDR_TAG:
        case protobuf_PACKET_ADDR_ANCHOR:
            /* Device-specific addresses – send over the primary network link */
            return STREAM_SERIAL_TX;
        case protobuf_PACKET_ADDR_CENTRAL:
        case protobuf_PACKET_ADDR_PERIPHERAL:
        case protobuf_PACKET_ADDR_HOST:
            return STREAM_BLE_TX;
        default:
            return STREAM_MAX;
    }
}

/**
 * Returns true if this packet is addressed to *us* (should be handled locally)
 * rather than forwarded.
 * BCAST → both handle AND forward.
 * Anything else must match our local identity (local_addr).
 */
static bool network_core_is_for_us(network_core_t *core, const protobuf_packet_t *packet)
{
    if (!packet->has_hdr || !packet->hdr.has_addr) return true; /* no addr → handle */

    protobuf_device_addr_t dst = (protobuf_device_addr_t)packet->hdr.addr.dst;
    if (dst == protobuf_PACKET_ADDR_BCAST) return true;

    return (dst == core->local_addr);
}

static void network_core_forward_packet(network_core_t *core, stream_type_t in_stream, const protobuf_packet_t *packet)
{
    if (!packet->has_hdr || !packet->hdr.has_addr) return;

    protobuf_device_addr_t dst = (protobuf_device_addr_t)packet->hdr.addr.dst;
    
    stream_type_t fwd = network_core_dst_to_tx_stream(dst);
    if (dst == protobuf_PACKET_ADDR_BCAST) {
        /* BCAST: route to everything EXCEPT where it came from */
        if (in_stream != STREAM_SERIAL_RX) {
            network_core_encode_and_send(core, STREAM_SERIAL_TX, packet);
        }
        if (in_stream != STREAM_BLE_RX) {
            network_core_send_ble_packet(core, STREAM_BLE_TX, packet);
        }
        return;
    }

    if (fwd == STREAM_MAX) return;          /* unknown dst -> drop */

    /* Map in_stream to its tx equivalent to avoid bouncing */
    stream_type_t in_tx_mapped = (in_stream == STREAM_BLE_RX) ? STREAM_BLE_TX : STREAM_SERIAL_TX;
    if (fwd == in_tx_mapped) return;        /* already came from this link -> don't loop */

    if (fwd == STREAM_BLE_TX) {
        network_core_send_ble_packet(core, fwd, packet);
    } else {
        network_core_encode_and_send(core, fwd, packet);
    }
}

static void network_core_update_ack_trackers(network_core_t *core, const protobuf_packet_t *packet)
{
    CHECK_VOID(core && packet && packet->has_hdr);

    if (packet->which_params != protobuf_packet_t_ack_tag) {
        return;
    }

    for (int i = 0; i < NETWORK_CORE_MAX_TRACKERS; i++) {
        network_ack_tracker_t *t = &core->ack_tracker[i];

        if (t->state != NETWORK_CORE_ACK_STATE_WAITING) {
            continue;
        }

        if (packet->params.ack.ack_seq != t->packet_header.seq) {
            continue;
        }

        t->state = network_core_is_ack_positive(packet->params.ack.response) ?
                   NETWORK_CORE_ACK_STATE_FOUND : NETWORK_CORE_NACK_STATE_FOUND;

        network_core_finalize_tracker(t, packet);
        return;
    }
}

static void network_core_check_tracker_timeouts(network_core_t *core)
{
    for (int i = 0; i < NETWORK_CORE_MAX_TRACKERS; i++) {
        network_ack_tracker_t *t = &core->ack_tracker[i];

        if (t->state != NETWORK_CORE_ACK_STATE_WAITING) {
            continue;
        }

        if ((uint32_t)(bsp_util_get_ticks() - t->start_time) < t->timeout) {
            continue;
        }

        t->state = NETWORK_CORE_ACK_STATE_TIMEOUT;
        network_core_finalize_tracker(t, NULL);
    }
}

static bool network_core_process_one_stream(network_core_t *core, stream_type_t in_stream)
{
    protobuf_packet_t packet;

    if (!network_core_try_receive(core, in_stream, &packet)) {
        return false;
    }

    core->latest_packet_tick = bsp_util_get_ticks();

    /* ---- Routing decision based on dst ---- */
    bool for_us = network_core_is_for_us(core, &packet);

    if (for_us) {
        /* Let the application layer handle it */
        if (core->packet_handler) {
            core->packet_handler(&packet);
        }
        network_core_update_ack_trackers(core, &packet);
    }

    /* BCAST: handle locally AND forward; unicast: only forward if not for us */
    bool is_bcast = packet.has_hdr && packet.hdr.has_addr &&
                    ((protobuf_device_addr_t)packet.hdr.addr.dst == protobuf_PACKET_ADDR_BCAST);
    if (!for_us || is_bcast) {
        network_core_forward_packet(core, in_stream, &packet);
    }

    return true;
}

bool network_core_init(network_core_t *core,
                       protobuf_device_addr_t local_addr,
                       uint8_t *rx_buffer,
                       uint32_t rx_buffer_len)
{
    CHECK(core && rx_buffer && rx_buffer_len > 0, false);

    memset(core, 0, sizeof(*core));
    core->enabled = true;
    core->local_addr = local_addr;
    core->interface = 0;
    core->rx_stream = serial_get_network_rx_stream();
    core->tx_stream = serial_get_network_tx_stream();
    core->rx_packet = rx_buffer;
    core->rx_buffer_size = rx_buffer_len;
    core->tx_seq = 0;

    _Static_assert(NETWORK_CORE_ACK_STATE_NONE == 0,
                   "ACK state NONE must be 0 for memset-init to work");

    return true;
}

bool network_core_register_packet_handler(network_core_t *core, network_core_packet_handler_t handler)
{
    CHECK(core, false);

    core->packet_handler = handler;
    return true;
}

bool network_core_process(network_core_t *core)
{
    CHECK(core && core->enabled, false);

    network_core_check_tracker_timeouts(core);

#ifdef BOOTLOADER
    /* In bootloader mode, keep debug_serial path isolated: process BLE transport only. */
    network_core_process_one_stream(core, STREAM_BLE_RX);
#else
    network_core_process_one_stream(core, STREAM_SERIAL_RX);
    network_core_process_one_stream(core, STREAM_BLE_RX);
#endif

    return true;
}

bool network_core_send_packet(network_core_t *core, uint8_t dst, protobuf_packet_t *packet)
{
    CHECK(core && packet, false);

    packet->has_hdr = true;
    packet->hdr.has_addr = true;
    packet->hdr.addr.src = (uint8_t)core->local_addr;
    packet->hdr.addr.dst = dst;
    packet->hdr.seq = (core->tx_seq)++;

    if (dst == protobuf_PACKET_ADDR_DEBUG) {
        /* DEBUG is a broadcast to all available interfaces */
        network_core_encode_and_send(core, STREAM_SERIAL_TX, packet);
        network_core_send_ble_packet(core, STREAM_BLE_TX, packet);
        return true;
    }

    stream_type_t tx_stream = network_core_dst_to_tx_stream((protobuf_device_addr_t)dst);
    if (tx_stream == STREAM_MAX) {
        tx_stream = core->tx_stream;
    }

    if (tx_stream == STREAM_BLE_TX) {
        network_core_send_ble_packet(core, tx_stream, packet);
        return true;
    }

    if (!network_core_encode_and_send(core, tx_stream, packet)) {
        return false;
    }

    return true;
}

int network_core_wait_ack(network_core_t *core, uint8_t seq, uint32_t timeout_ms,
                          network_ack_tracker_callback_t callback, void *callback_arg)
{
    CHECK(core && callback, -1);

    network_ack_tracker_t *t = network_core_find_tracker(core, NETWORK_CORE_ACK_STATE_NONE);
    if (!t) {
        return -1;
    }

    t->packet_header.seq = seq;
    t->start_time = bsp_util_get_ticks();
    t->timeout = timeout_ms;
    t->callback = callback;
    t->callback_arg = callback_arg;
    t->state = NETWORK_CORE_ACK_STATE_WAITING;

    return (int)(t - core->ack_tracker);
}

bool network_core_send_ack(network_core_t *core,
                           const protobuf_packet_t *rx_packet,
                           protobuf_packet_ack_response_t response)
{
    CHECK(core && rx_packet, false);
    CHECK(rx_packet->has_hdr && rx_packet->hdr.has_addr, false);

    for (uint16_t i = 0; i < (uint16_t)(sizeof(network_core_skip_ack_tb) / sizeof(network_core_skip_ack_tb[0])); i++) {
        if (rx_packet->which_params == network_core_skip_ack_tb[i]) {
            return false;
        }
    }

    protobuf_packet_t p;
    memset(&p, 0, sizeof(p));
    p.params.ack.ack_seq = rx_packet->hdr.seq;
    p.params.ack.response = response;
    p.which_params = protobuf_packet_t_ack_tag;

    return network_core_send_packet(core, (uint8_t)rx_packet->hdr.addr.src, &p);
}

bool network_core_encode_packet(const protobuf_packet_t *encode_msg, uint8_t *buff, uint32_t buff_len, uint32_t *len)
{
    pb_ostream_t stream;
    bool status;

    CHECK(encode_msg && buff && len, false);

    stream = pb_ostream_from_buffer(buff, buff_len);
    status = pb_encode(&stream, protobuf_packet_t_fields, encode_msg);
    *len = stream.bytes_written;

    return status;
}

bool network_core_decode_packet(const uint8_t *buff, uint32_t len, protobuf_packet_t *decode_msg)
{
    pb_istream_t stream;

    CHECK(buff && decode_msg && len > 0, false);

    stream = pb_istream_from_buffer(buff, len);
    return pb_decode(&stream, protobuf_packet_t_fields, decode_msg);
}
