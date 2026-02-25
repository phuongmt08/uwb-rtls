/**
 * @file       sys_stream.c
 * @copyright
 * @license
 * @version    1.0.0
 * @date       2025
 * @author
 * @brief      System stream module implementation for encoding, decoding, 
 *             and managing system packets over USB-C and BLE.
 *
 * @note       This module is designed to work with both USB-C and BLE interfaces.
 *             BLE requires special handling due to MTU limitations, using direct
 *             packet structure writes instead of full Protobuf encoding for efficiency.
 */

/* Public includes ---------------------------------------------------------- */
#include "sys_stream.h"
#include "sys_logger.h"
#include "serial.h"
#include "usbd_cdc_if.h"
#include "stm32f4xx_hal.h"
#include <string.h>
#include <stdbool.h>

/* Private defines ---------------------------------------------------------- */
// Important: Maximum size of the header in protocol_packet_t structure
#define MAX_HDR_SIZE (16)

/* Private macros ----------------------------------------------------------- */
#define GET_PACKET_LENGTH(_VAR_TYPE_) (sizeof(_VAR_TYPE_) + MAX_HDR_SIZE)
#define SIZE_INFO(_protobuf_tag, _data_size) {.protobuf_tag = _protobuf_tag, .data_size = _data_size}

/* Private enumerate/structure ---------------------------------------------- */
typedef struct sys_stream_packet_size_s
{
    uint16_t protobuf_tag;
    uint16_t data_size;
} sys_stream_packet_size_t;

/* Private variables -------------------------------------------------------- */
/**
 * @brief Lookup table for packet sizes - optimizes BLE transmission
 * @note  BLE has MTU limitations, so knowing exact packet size helps avoid fragmentation
 */
static const sys_stream_packet_size_t sys_stream_packet_size[] = 
{
    // Add your packet types here based on protocol.pb.h definitions
    // Example format:
    // [0] = SIZE_INFO(protocol_packet_t_YOUR_TYPE_tag, GET_PACKET_LENGTH(protocol_YOUR_TYPE_t)),
};

static const uint16_t sys_stream_packet_size_count = sizeof(sys_stream_packet_size) / sizeof(sys_stream_packet_size_t);

// USB TX handler
static int usb_tx_handler(int file, char *ptr, int len, uint8_t type)
{
    (void)file; (void)type;
    if (!ptr || len <= 0) return -1;
    uint32_t deadline = HAL_GetTick() + 10;
    do {
        uint8_t st = CDC_Transmit_FS((uint8_t*)ptr, (uint16_t)len);
        if (st == (uint8_t)USBD_OK) return len;
    } while (HAL_GetTick() < deadline);
    return -1;
}

// BLE TX handler
static int ble_tx_handler(int file, char *ptr, int len, uint8_t type)
{
    (void)file; (void)ptr; (void)len; (void)type;
    return 0; // TODO: implement BLE write
}

/* Private function prototypes ---------------------------------------------- */
static void sys_stream_send_ble_packet(sys_stream_t *ss, const protocol_packet_t *packet);
static bool sys_stream_try_receive_usb(sys_stream_t *ss, protocol_packet_t *out_pkt);
static bool sys_stream_try_receive_ble(sys_stream_t *ss, protocol_packet_t *out_pkt);

static bool sys_stream_init_common(sys_stream_t *ss,
                                   sys_stream_interface_t interface,
                                   uint8_t *rx_buffer,
                                   uint32_t rx_buffer_len)
{
    if (!ss || !rx_buffer || rx_buffer_len == 0)
        return false;

    memset(ss, 0, sizeof(*ss));
    ss->enabled        = true;
    ss->interface      = (uint8_t)interface;
    ss->rx_packet      = rx_buffer;
    ss->rx_buffer_size = rx_buffer_len;
    ss->tx_seq         = 0;
    for (int i = 0; i < SYS_STREAM_MAX_TRACKERS; i++)
        ss->ack_tracker[i].state = SYS_STREAM_ACK_STATE_NONE;
    return true;
}

bool sys_stream_init(sys_stream_t *ss,
                     sys_stream_interface_t interface,
                     uint8_t *rx_buffer,
                     uint32_t rx_buffer_len)
{
    if (!sys_stream_init_common(ss, interface, rx_buffer, rx_buffer_len))
        return false;

    // Initialize packet handler as NULL
    ss->packet_handler = NULL;

    // Register TX handler with serial middleware based on interface
    if (interface == SYS_STREAM_IF_USB)
    {
        serial_register_tx_handler(STREAM_USB_TX, usb_tx_handler);
    }
    else if (interface == SYS_STREAM_IF_BLE)
    {
        serial_register_tx_handler(STREAM_BLE_TX, ble_tx_handler);
    }
    return true;
}

bool sys_stream_register_packet_handler(sys_stream_t *ss, sys_stream_packet_handler_t handler)
{
    if (!ss) return false;
    ss->packet_handler = handler;
    return true;
}

/* Public implementations --------------------------------------------------- */
/**
 * @brief Process incoming packets and manage ACK tracking.
 * @param ss Pointer to the system stream instance.
 * @return true if processing was successful, false otherwise.
 *
 * @note This function:
 *       1. Checks for ACK timeouts
 *       2. Reads and decodes incoming packets
 *       3. Updates ACK tracker states
 *       4. Invokes registered callbacks
 */
bool sys_stream_process(sys_stream_t *ss)
{
    if (!ss || !ss->enabled)
        return false;

    // ACK timeout handling
    for (int i = 0; i < SYS_STREAM_MAX_TRACKERS; i++)
    {
        sys_ack_tracker_t *t = &ss->ack_tracker[i];
        if (t->state == SYS_STREAM_ACK_STATE_WAITING &&
            (uint32_t)(HAL_GetTick() - t->start_time) >= t->timeout)
        {
            t->state = SYS_STREAM_ACK_STATE_TIMEOUT;
            if (t->callback) t->callback(t, NULL);
            t->state = SYS_STREAM_ACK_STATE_NONE;
        }
    }

    // Attempt to receive ALL available packets (loop to drain buffer)
    // Important: USB may receive multiple packets in one read, process all of them
    uint8_t packets_processed = 0;
    while (packets_processed < 10) // Safety limit to prevent infinite loop
    {
        protocol_packet_t packet;
        bool got_packet = false;
        
        if (ss->interface == SYS_STREAM_IF_USB)
        {
            got_packet = sys_stream_try_receive_usb(ss, &packet);
        }
        else if (ss->interface == SYS_STREAM_IF_BLE)
        {
            got_packet = sys_stream_try_receive_ble(ss, &packet);
        }
        
        if (!got_packet)
            break; // No more packets available
        
        packets_processed++;
        ss->latest_packet_tick = HAL_GetTick();

        // Call packet handler if registered (for command processing)
        if (ss->packet_handler) {
            ss->packet_handler(&packet);
        }

        // Match with trackers
        for (int i = 0; i < SYS_STREAM_MAX_TRACKERS; i++)
        {
            sys_ack_tracker_t *t = &ss->ack_tracker[i];
            if (t->state == SYS_STREAM_ACK_STATE_WAITING && t->packet_header.seq == packet.hdr.seq)
            {
                if (packet.which_payload == protocol_packet_t_ack_tag)
                {
                    t->state = (packet.payload.ack.response == protocol_response_status_RESPONSE_OK) ?
                               SYS_STREAM_ACK_STATE_FOUND : SYS_STREAM_NACK_STATE_FOUND;
                }
                else
                {
                    t->state = SYS_STREAM_ACK_STATE_FOUND;
                }
                if (t->callback) t->callback(t, &packet);
                t->state = SYS_STREAM_ACK_STATE_NONE;
            }
        }
    }
    return true;
}

/**
 * @brief Register ACK tracker for a packet
 * @return Tracker ID (>=0) if registered, <0 on error
 */
int sys_stream_wait_ack(sys_stream_t *ss, uint8_t seq, uint32_t timeout_ms,
                        sys_ack_tracker_callback_t callback, void *callback_arg)
{
    if (!ss || !callback) return -1;
    
    // Find free tracker slot
    for (int i = 0; i < SYS_STREAM_MAX_TRACKERS; i++) {
        sys_ack_tracker_t *t = &ss->ack_tracker[i];
        if (t->state == SYS_STREAM_ACK_STATE_NONE) {
            // Initialize tracker
            t->packet_header.seq = seq;
            t->start_time = HAL_GetTick();
            t->timeout = timeout_ms;
            t->callback = callback;
            t->callback_arg = callback_arg;
            t->state = SYS_STREAM_ACK_STATE_WAITING;
            return i;
        }
    }
    
    return -1;  // No free tracker
}

/**
 * @brief Send a packet using the system stream.
 * @param ss Pointer to the system stream instance.
 * @param dst Destination address.
 * @param packet Pointer to the packet to send.
 * @return true if the packet was sent successfully, false otherwise.
 *
 * @note For BLE interface, uses optimized packet structure write
 *       For USB-C interface, uses full Protobuf encoding
 */
bool sys_stream_send_packet(sys_stream_t *ss, uint8_t dst, protocol_packet_t *packet)
{
    if (!ss || !packet)
        return false;

    uint8_t  buf[ss->rx_buffer_size];
    uint32_t len = 0;

    packet->has_hdr      = true;
    packet->hdr.has_addr = true;
    packet->hdr.addr.src = ss->interface; // local interface id
    packet->hdr.addr.dst = dst;
    packet->hdr.seq      = (ss->tx_seq)++;

    if (ss->interface == SYS_STREAM_IF_USB) // USB full encode + transmit
    {
        if (!sys_stream_encode_packet(packet, buf, sizeof(buf), &len))
        {
            RLOG_E(LOG_OBJECT_CODE_NETWORK, 0x01, "encode fail");
            return false;
        }
        int ret = _write(STREAM_USB_TX, (char*)buf, (int)len, 0);
        return (ret > 0);
    }
    else // BLE peripheral (opt attempt)
    {
        sys_stream_send_ble_packet(ss, packet); // falls back internally if needed
        return true;
    }
}

/**
 * @brief Send an acknowledgment for a received packet.
 * @param ss Pointer to the system stream instance.
 * @param dst Destination address.
 * @param rx_seq Sequence number of the received packet.
 * @param response ACK or NACK response.
 */
void sys_stream_send_ack(sys_stream_t *ss, uint8_t dst, uint32_t rx_seq, uint8_t response)
{
    protocol_packet_t p;
    
    // ack_seq is the sequence number of the packet to which we are acknowledging
    p.payload.ack.ack_seq    = rx_seq;
    p.payload.ack.response   = (protocol_response_status)response;
    p.which_payload           = protocol_packet_t_ack_tag;

    // Send packet
    sys_stream_send_packet(ss, dst, &p);
}

/**
 * @brief Encode a Protobuf packet.
 * @param encode_msg Pointer to the packet to encode.
 * @param buff Buffer to store the encoded packet.
 * @param buff_len Length of the buffer.
 * @param len Pointer to store the length of the encoded packet.
 * @return true if encoding was successful, false otherwise.
 */
bool sys_stream_encode_packet(const protocol_packet_t *encode_msg, uint8_t *buff, uint32_t buff_len, uint32_t *len)
{
    bool status;

    /* Create a stream that will write to our buff. */
    pb_ostream_t stream = pb_ostream_from_buffer(buff, buff_len);

    /* Now we are ready to encode the msg! */
    status = pb_encode(&stream, protocol_packet_t_fields, encode_msg);
    *len = stream.bytes_written;

    if (!status)
    {
        // Encoding failed - log error if logging is available
        // RLOG_E(OBJECT_CODE, ERR_INVALID_PACKET_LEN, "Request encoding failed: %s", PB_GET_ERROR(&stream));
    }

    return status;
}

/**
 * @brief Decode a Protobuf packet.
 * @param buff Buffer containing the encoded packet.
 * @param len Length of the buffer.
 * @param decode_msg Pointer to store the decoded packet.
 * @return true if decoding was successful, false otherwise.
 */
bool sys_stream_decode_packet(const uint8_t *buff, uint32_t len, protocol_packet_t *decode_msg)
{
    bool status;

    /* Create a stream that reads from the buff. */
    pb_istream_t stream = pb_istream_from_buffer(buff, len);

    /* Now we are ready to decode the msg. */
    status = pb_decode(&stream, protocol_packet_t_fields, decode_msg);

    /* Check for errors... */
    if (!status)
    {
        // Decoding failed - log error if logging is available
        // RLOG_E(OBJECT_CODE, ERR_INVALID_PACKET_LEN, "Packet decoding failed: %s", PB_GET_ERROR(&stream));
    }

    return status;
}

/* Private implementations -------------------------------------------------- */
/**
 * @brief Send BLE packet using optimized structure write
 * @param ss Pointer to the system stream instance
 * @param packet Pointer to the packet to send
 *
 * @note This function optimizes BLE peripheral transmission by:
 *       1. Looking up exact packet size from pre-calculated table
 *       2. Writing packet structure directly instead of full encoding
 *       3. Using SSP_TYPE_BLE_HANDLE to indicate direct structure write
 *       This avoids MTU fragmentation issues in BLE peripheral mode
 *       (Device is BLE peripheral, app is BLE central)
 */
static void sys_stream_send_ble_packet(sys_stream_t *ss, const protocol_packet_t *packet)
{
    for (uint16_t i = 0; i < sys_stream_packet_size_count; i++)
    {
        if (packet->which_payload == sys_stream_packet_size[i].protobuf_tag)
        {
            // TODO: implement BLE peripheral write
            return;
        }
    }
    // Fallback: encode
    uint8_t buf[ss->rx_buffer_size];
    uint32_t len = 0;
    if (sys_stream_encode_packet(packet, buf, sizeof(buf), &len))
    {
        // TODO: implement BLE peripheral write
    }
}

static bool sys_stream_try_receive_usb(sys_stream_t *ss, protocol_packet_t *out_pkt)
{
    if (!ss || !out_pkt) return false;
    
    // Only read from USB if buffer is empty (no leftover data)
    if (ss->rx_packet_len == 0) {
        int n = _read(STREAM_USB_RX, (char*)ss->rx_packet, (int)ss->rx_buffer_size, 0);
        if (n <= 0)
            return false;
        ss->rx_packet_len = (uint32_t)n;
    }
    
    // Try to decode one packet from buffer
    // Note: Protobuf may not consume all bytes if there are multiple packets
    if (!sys_stream_decode_packet(ss->rx_packet, ss->rx_packet_len, out_pkt))
    {
        RLOG_W(LOG_OBJECT_CODE_NETWORK, "Decode fail len=%lu", (unsigned long)ss->rx_packet_len);
        ss->rx_packet_len = 0; // Clear buffer on decode error
        return false;
    }
    
    // Clear buffer after successful decode (Protobuf consumes entire message)
    // Note: If USB contains multiple packets, next call will read again
    ss->rx_packet_len = 0;
    
    RLOG_D(LOG_OBJECT_CODE_NETWORK, "RX packet: payload_tag=%d", out_pkt->which_payload);
    return true;
}

static bool sys_stream_try_receive_ble(sys_stream_t *ss, protocol_packet_t *out_pkt)
{
    if (!ss || !out_pkt) return false;
    int n = _read(STREAM_BLE_RX, (char*)ss->rx_packet, (int)ss->rx_buffer_size, 0);
    if (n <= 0)
        return false;
    ss->rx_packet_len = (uint32_t)n;
    if (!sys_stream_decode_packet(ss->rx_packet, ss->rx_packet_len, out_pkt))
    {
        RLOG_W(LOG_OBJECT_CODE_NETWORK, "decode fail len=%lu", (unsigned long)n);
        return false;
    }
    return true;
}

/* End of file -------------------------------------------------------------- */



