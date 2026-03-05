#include "network_core.h"
#include "sys_logger.h"
#include "stm32f4xx_hal.h"

#include <string.h>

#define OBJECT_CODE LOG_OBJECT_CODE_NETWORK

#define CHECK(_cond, _ret) do { if (!(_cond)) return (_ret); } while (0)
#define CHECK_VOID(_cond) do { if (!(_cond)) return; } while (0)

typedef struct network_core_packet_size_s
{
    uint16_t protobuf_tag;
    uint16_t data_size;
} network_core_packet_size_t;

static const network_core_packet_size_t network_core_packet_size[] = {
};

static const uint16_t network_core_packet_size_count = sizeof(network_core_packet_size) / sizeof(network_core_packet_size_t);

static const uint16_t network_core_skip_ack_tb[] = {
    protobuf_packet_t_ack_tag,
    protobuf_packet_t_ble_adv_status_tag,
    protobuf_packet_t_log_data_tag,
    // protobuf_packet_t_log_erase_tag,
    protobuf_packet_t_anchor_distance_tag,
    protobuf_packet_t_tag_position_tag,
    protobuf_packet_t_flash_write_tag
};

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
    for (uint16_t i = 0; i < network_core_packet_size_count; i++) {
        if (packet->which_params == network_core_packet_size[i].protobuf_tag) {
            (void)_write(tx_stream, (char *)packet, network_core_packet_size[i].data_size, 0);
            return;
        }
    }

    {
        uint8_t buf[core->rx_buffer_size];
        uint32_t len = 0;
        if (network_core_encode_packet(packet, buf, sizeof(buf), &len)) {
            (void)_write(tx_stream, (char *)buf, (int)len, 0);
        }
    }
}

static stream_type_t network_core_get_forward_stream(stream_type_t in_stream)
{
    if (in_stream == STREAM_UART_RX) {
        return STREAM_BLE_TX;
    }

    if (in_stream == STREAM_BLE_RX) {
        return STREAM_UART_TX;
    }

    return STREAM_MAX;
}

static void network_core_forward_packet(network_core_t *core, const protobuf_packet_t *packet, stream_type_t in_stream)
{
    CHECK_VOID(core && packet);
    CHECK_VOID(packet->has_hdr && packet->hdr.has_addr);
    CHECK_VOID(packet->hdr.addr.src != packet->hdr.addr.dst);

    stream_type_t forward_stream = network_core_get_forward_stream(in_stream);
    CHECK_VOID(forward_stream != STREAM_MAX);
    CHECK_VOID(packet->hdr.addr.src != (uint32_t)forward_stream);
    CHECK_VOID(packet->hdr.addr.dst != (uint32_t)in_stream);

    if (forward_stream == STREAM_BLE_TX) {
        network_core_send_ble_packet(core, forward_stream, packet);
    } else {
        uint8_t buf[core->rx_buffer_size];
        uint32_t len = 0;
        if (network_core_encode_packet(packet, buf, sizeof(buf), &len)) {
            (void)_write(forward_stream, (char*)buf, (int)len, 0);
        }
    }
}

static void network_core_update_ack_trackers(network_core_t *core, const protobuf_packet_t *packet)
{
    CHECK_VOID(core && packet && packet->has_hdr);

    for (int i = 0; i < NETWORK_CORE_MAX_TRACKERS; i++) {
        network_ack_tracker_t *t = &core->ack_tracker[i];
        if (t->state != NETWORK_CORE_ACK_STATE_WAITING) {
            continue;
        }

        if (packet->which_params == protobuf_packet_t_ack_tag) {
            if (packet->params.ack.ack_seq != t->packet_header.seq) {
                continue;
            }

            t->state = network_core_is_ack_positive(packet->params.ack.response) ?
                       NETWORK_CORE_ACK_STATE_FOUND : NETWORK_CORE_NACK_STATE_FOUND;
        } else {
            if (packet->hdr.seq != t->packet_header.seq) {
                continue;
            }

            t->state = NETWORK_CORE_ACK_STATE_FOUND;
        }

        if (t->callback) {
            t->callback(t, packet);
        }

        t->state = NETWORK_CORE_ACK_STATE_NONE;
    }
}

static bool network_core_process_one_stream(network_core_t *core, stream_type_t in_stream)
{
    protobuf_packet_t packet;

    if (!network_core_try_receive(core, in_stream, &packet)) {
        return false;
    }

    core->latest_packet_tick = HAL_GetTick();

    if (core->packet_handler) {
        core->packet_handler(&packet);
    }

    network_core_update_ack_trackers(core, &packet);
    network_core_forward_packet(core, &packet, in_stream);

    return true;
}

bool network_core_init(network_core_t *core,
                       uint8_t *rx_buffer,
                       uint32_t rx_buffer_len)
{
    CHECK(core && rx_buffer && rx_buffer_len > 0, false);

    memset(core, 0, sizeof(*core));
    core->enabled = true;
    core->interface = 0;
    core->rx_stream = serial_get_network_rx_stream();
    core->tx_stream = serial_get_network_tx_stream();
    core->rx_packet = rx_buffer;
    core->rx_buffer_size = rx_buffer_len;
    core->tx_seq = 0;

    for (int i = 0; i < NETWORK_CORE_MAX_TRACKERS; i++) {
        core->ack_tracker[i].state = NETWORK_CORE_ACK_STATE_NONE;
    }

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

    for (int i = 0; i < NETWORK_CORE_MAX_TRACKERS; i++) {
        network_ack_tracker_t *t = &core->ack_tracker[i];
        if (t->state == NETWORK_CORE_ACK_STATE_WAITING &&
            (uint32_t)(HAL_GetTick() - t->start_time) >= t->timeout)
        {
            t->state = NETWORK_CORE_ACK_STATE_TIMEOUT;
            if (t->callback) {
                t->callback(t, NULL);
            }
            t->state = NETWORK_CORE_ACK_STATE_NONE;
        }
    }

    (void)network_core_process_one_stream(core, STREAM_UART_RX);
    (void)network_core_process_one_stream(core, STREAM_BLE_RX);

    return true;
}

bool network_core_send_packet(network_core_t *core, uint8_t dst, protobuf_packet_t *packet)
{
    CHECK(core && packet, false);

    {
        uint8_t buf[core->rx_buffer_size];
        uint32_t len = 0;

        packet->has_hdr = true;
        packet->hdr.has_addr = true;
        packet->hdr.addr.src = (uint8_t)core->tx_stream;
        packet->hdr.addr.dst = dst;
        packet->hdr.seq = (core->tx_seq)++;

        if (core->tx_stream == STREAM_BLE_TX) {
            network_core_send_ble_packet(core, core->tx_stream, packet);
            return true;
        }

        if (!network_core_encode_packet(packet, buf, sizeof(buf), &len)) {
            RLOG_E(OBJECT_CODE, 0x01, "encode fail");
            return false;
        }

        return (_write(core->tx_stream, (char*)buf, (int)len, 0) > 0);
    }
}

int network_core_wait_ack(network_core_t *core, uint8_t seq, uint32_t timeout_ms,
                          network_ack_tracker_callback_t callback, void *callback_arg)
{
    CHECK(core && callback, -1);

    for (int i = 0; i < NETWORK_CORE_MAX_TRACKERS; i++) {
        network_ack_tracker_t *t = &core->ack_tracker[i];
        if (t->state == NETWORK_CORE_ACK_STATE_NONE) {
            t->packet_header.seq = seq;
            t->start_time = HAL_GetTick();
            t->timeout = timeout_ms;
            t->callback = callback;
            t->callback_arg = callback_arg;
            t->state = NETWORK_CORE_ACK_STATE_WAITING;
            return i;
        }
    }

    return -1;
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

    return network_core_send_packet(core,
                                    (uint8_t)rx_packet->hdr.addr.src,
                                    &p);
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
