/**
 * @file bb_transport.h
 * @brief BLE Bridge Transport Layer
 *
 * Handles physical I/O abstraction. Specifically:
 * - HDLC encode/decode framing over UART.
 * - Nordic NUS (UART over BLE) payload wrapping.
 */
#ifndef BB_TRANSPORT_H
#define BB_TRANSPORT_H

#include <stdint.h>
#include <stdbool.h>
#include "sdk_errors.h"

/**
 * @brief Source of the incoming network packet
 */
typedef enum {
    BB_SOURCE_UART = 0,
    BB_SOURCE_BLE  = 1,
} bb_packet_source_t;

/**
 * @brief Event structure passed when a complete frame is received.
 */
typedef struct {
    bb_packet_source_t source;
    uint8_t *          p_data;
    uint16_t           length;
} bb_transport_evt_t;

/**
 * @brief Callback function type for transport events (when a full packet is formed)
 */
typedef void (*bb_transport_data_handler_t)(bb_transport_evt_t const * p_evt);

/**
 * @brief Initialization configurations for the transport layer.
 */
typedef struct {
    bb_transport_data_handler_t data_handler;
} bb_transport_init_t;

/**
 * @brief Initializes the transport layer (HDLC state machines and peripheral drivers)
 *
 * @param[in] p_init Pointer to config struct containing the data router handler.
 * @return NRF_SUCCESS on successful initialization.
 */
ret_code_t bb_transport_init(bb_transport_init_t const * p_init);

/**
 * @brief Wraps the payload in HDLC framing and transmits over physical UART.
 *
 * @param[in] p_data Pointer to raw Protobuf payload.
 * @param[in] length Size of the payload.
 * @return NRF_SUCCESS if pushed to TX buffer successfully.
 */
ret_code_t bb_transport_send_uart(uint8_t const * p_data, uint16_t length);

/**
 * @brief Transmits the payload directly over BLE NUS without HDLC framing.
 *
 * @param[in] p_data Pointer to raw Protobuf payload.
 * @param[in] length Size of the payload.
 * @return NRF_SUCCESS if pushed to BLE TX queue successfully.
 */
ret_code_t bb_transport_send_ble(uint8_t const * p_data, uint16_t length);

/**
 * @brief Feeds a single byte from the UART Rx Interrupt into the HDLC framing parser.
 * This should be called directly inside the app_uart Rx event handler.
 *
 * @param[in] byte Raw byte received from UART line.
 */
void bb_transport_feed_uart_rx(uint8_t byte);

#endif // BB_TRANSPORT_H
