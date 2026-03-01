#ifndef __UART_DEBUG_H
#define __UART_DEBUG_H

#include "serial.h"

void uart_debug_init(void);
void uart_debug_set_tx_handler(serial_func_t handler);
void uart_debug_rx_push(const uint8_t *data, uint32_t len);
int uart_debug_read(int file, char *ptr, int len, uint8_t type);
int uart_debug_write(int file, char *ptr, int len, uint8_t type);

#endif /* __UART_DEBUG_H */
