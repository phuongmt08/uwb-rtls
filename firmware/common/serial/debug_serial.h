#ifndef __DEBUG_SERIAL_H
#define __DEBUG_SERIAL_H

#include "serial.h"

void debug_serial_init(void);
void debug_serial_set_tx_handler(serial_func_t handler);
void debug_serial_rx_push(const uint8_t *data, uint32_t len);
int debug_serial_read(int file, char *ptr, int len, uint8_t type);
int debug_serial_write(int file, char *ptr, int len, uint8_t type);

#endif /* __DEBUG_SERIAL_H */
