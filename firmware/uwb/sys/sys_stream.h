/**
 * @file       sys_stream.h
 * @copyright
 * @license
 * @version    1.0.0
 * @date       2025
 * @author
 * @brief      System stream module for encoding, decoding, and managing system packets.
 */
/* Define to prevent recursive inclusion ------------------------------------ */
#ifndef __SYS_STREAM_H
#define __SYS_STREAM_H
/* Includes ----------------------------------------------------------------- */
#include "config.h"
#include "common.h"
#include "nanopb/pb_encode.h"
#include "nanopb/pb_decode.h"
#include "protos/protocol.pb.h"
#include "serial.h"

/* Public defines ----------------------------------------------------------- */
#define SYS_STREAM_MAX_TRACKERS 4

/**
 * @brief System stream interface types
 */
typedef enum {
    SYS_STREAM_IF_USB = 0,   /**< USB-C (CDC / bulk) */
    SYS_STREAM_IF_BLE = 1    /**< BLE Peripheral (mobile app is central) */
} sys_stream_interface_t;

/**
 * @brief SSP (Serial Stream Protocol) types for packet handling
 * @note These control how packets are processed by the lower layer
 */
#ifndef SSP_TYPE_NONE
#define SSP_TYPE_NONE        0  // No special processing
#define SSP_TYPE_BLE_FORWARD 1  // Forward via BLE (full encoding)
#define SSP_TYPE_BLE_HANDLE  2  // BLE optimized (direct structure write)
#endif

/* Public enumerate/structure ----------------------------------------------- */
typedef enum
{
    SYS_STREAM_ACK_STATE_NONE,
    SYS_STREAM_ACK_STATE_WAITING,
    SYS_STREAM_ACK_STATE_FOUND,
    SYS_STREAM_NACK_STATE_FOUND,
    SYS_STREAM_ACK_STATE_TIMEOUT
} sys_stream_ack_state_t;

typedef struct sys_ack_tracker_s sys_ack_tracker_t;
typedef void (*sys_ack_tracker_callback_t)(sys_ack_tracker_t *p_tracker, const protocol_packet_t *packet);
struct sys_ack_tracker_s
{
    sys_stream_ack_state_t state;
    protocol_hdr_t packet_header;
    uint32_t start_time;
    uint32_t timeout;
    sys_ack_tracker_callback_t callback;
    void *callback_arg;
};

/**
 * @brief Callback for processing received packets
 */
typedef bool (*sys_stream_packet_handler_t)(const protocol_packet_t *packet);

typedef struct
{
    bool enabled;
    uint8_t interface; // sys_stream_interface_t

    uint8_t *rx_packet;      // RX working buffer (provided by user)
    uint32_t rx_buffer_size; // Size of rx_packet buffer
    uint32_t rx_packet_len;  // Length of last received packet
    uint8_t  tx_seq;         // Sequence counter

    sys_stream_ack_state_t ack_state;
    sys_ack_tracker_t ack_tracker[SYS_STREAM_MAX_TRACKERS];

    uint32_t latest_packet_tick;
    
    // Command handler callback
    sys_stream_packet_handler_t packet_handler;
} sys_stream_t;

/* Public macros ------------------------------------------------------------ */
#define SYS_STREAM_PKT_HDR_SIZE sizeof(protocol_hdr_t)

/* Public variables --------------------------------------------------------- */

/* Public APIs -------------------------------------------------------------- */
/**
 * @brief Process incoming packets and manage ACK tracking
 * @param ss Pointer to the system stream instance
 * @return true if processing was successful, false otherwise
 * 
 * @note This function should be called periodically in main loop.
 *       It handles:
 *       - ACK timeout checking
 *       - Packet reception and decoding
 *       - ACK tracker state updates
 *       - Callback invocation
 */
bool sys_stream_process(sys_stream_t *ss);

/**
 * @brief Register a packet handler callback
 * @param ss Pointer to the system stream instance
 * @param handler Function pointer to handle received packets
 * @return true on success
 */
bool sys_stream_register_packet_handler(sys_stream_t *ss, sys_stream_packet_handler_t handler);

/**
 * @brief Send a packet through the stream
 * @param ss Pointer to the system stream instance
 * @param dst Destination address
 * @param packet Pointer to the packet to send
 * @return true if packet was sent successfully, false otherwise
 * 
 * @note Automatically handles:
 *       - Sequence number assignment
 *       - Header population
 *       - Interface-specific optimization (BLE vs USB-C)
 */
bool sys_stream_send_packet(sys_stream_t *ss, uint8_t dst, protocol_packet_t *packet);

/**
 * @brief Register ACK tracker for a packet (wait for ACK or timeout)
 * @param ss Pointer to sys_stream instance
 * @param seq Sequence number to match
 * @param timeout_ms Timeout in milliseconds
 * @param callback Callback function when ACK received or timeout
 * @param callback_arg User data passed to callback
 * @return Tracker ID (>=0) if registered, <0 on error
 * 
 * @note Callback will be called when:
 *       - ACK packet received with matching seq
 *       - Timeout expires (pkt=NULL)
 */
int sys_stream_wait_ack(sys_stream_t *ss, uint8_t seq, uint32_t timeout_ms,
                        sys_ack_tracker_callback_t callback, void *callback_arg);

/**
 * @brief Send an acknowledgment packet
 * @param ss Pointer to the system stream instance
 * @param dst Destination address
 * @param rx_seq Sequence number of the packet being acknowledged
 * @param response Response code (ACK/NACK)
 * 
 * @note Use protobuf_ACK_RESPONSE_OK for ACK, other values for NACK
 */
void sys_stream_send_ack(sys_stream_t *ss, uint8_t dst, uint32_t rx_seq, uint8_t response);

/**
 * @brief Encode a Protobuf packet into a byte buffer
 * @param encode_msg Pointer to the packet to encode
 * @param buff Buffer to store the encoded packet
 * @param buff_len Length of the buffer
 * @param len Pointer to store the actual encoded length
 * @return true if encoding was successful, false otherwise
 * 
 * @note Used internally by send functions, but can be called directly
 *       for custom packet handling
 */
bool sys_stream_encode_packet(const protocol_packet_t *encode_msg, uint8_t *buff, uint32_t buff_len, uint32_t *len);

/**
 * @brief Decode a Protobuf packet from a byte buffer
 * @param buff Buffer containing the encoded packet
 * @param len Length of the buffer
 * @param decode_msg Pointer to store the decoded packet
 * @return true if decoding was successful, false otherwise
 * 
 * @note Used internally by process function, but can be called directly
 *       for custom packet handling
 */
bool sys_stream_decode_packet(const uint8_t *buff, uint32_t len, protocol_packet_t *decode_msg);

/**
 * @brief Initialize sys_stream for a specific interface (USB or BLE)
 * @param ss            Pointer to instance
 * @param interface     SYS_STREAM_IF_USB or SYS_STREAM_IF_BLE
 * @param rx_buffer     RX working buffer memory
 * @param rx_buffer_len Size of rx_buffer
 * @return true on success
 */
bool sys_stream_init(sys_stream_t *ss,
                     sys_stream_interface_t interface,
                     uint8_t *rx_buffer,
                     uint32_t rx_buffer_len);



/* -------------------------------------------------------------------------- */
#endif /* __SYS_STREAM_H */

/* End of file -------------------------------------------------------------- */
