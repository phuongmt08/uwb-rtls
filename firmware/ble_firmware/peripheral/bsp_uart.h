#ifndef BSP_UART_H
#define BSP_UART_H

#include <stdint.h>
#include <stdbool.h>
#include "sdk_errors.h"

/**
 * @brief Callback invoked by bsp_uart for each received byte.
 *        bb_transport registers this to receive raw bytes.
 */
typedef void (*bsp_uart_rx_cb_t)(uint8_t byte);

/**
 * @brief Initialize UART peripheral.
 *
 * @param rx_cb  Function pointer called for every received byte.
 *               Pass NULL to disable RX notification.
 * @return NRF_SUCCESS on success, else error code.
 */
ret_code_t bsp_uart_init(bsp_uart_rx_cb_t rx_cb);

/**
 * @brief Transmit a buffer over UART.
 *
 * @param buf   Pointer to data.
 * @param len   Number of bytes to send.
 * @return NRF_SUCCESS on success, else error code.
 */
ret_code_t bsp_uart_transmit(const uint8_t *buf, uint16_t len);

void bsp_uart_read_byte(void);

#endif /* BSP_UART_H */
