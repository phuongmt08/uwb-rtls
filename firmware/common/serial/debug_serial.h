#ifndef __DEBUG_SERIAL_H
#define __DEBUG_SERIAL_H

#include "serial.h"
#include <stdbool.h>

void debug_serial_init(void);
void debug_serial_set_tx_handler(serial_func_t handler);
void debug_serial_rx_push(const uint8_t *data, uint32_t len);
int debug_serial_read(int file, char *ptr, int len, uint8_t type);
int debug_serial_write(int file, char *ptr, int len, uint8_t type);

/* Defined only when SYS_DIAG_TO_VEHICLE = 1: true if the host transport is USB
 * and the CDC endpoint can take a frame right now. */
bool debug_serial_usb_tx_ready(void);

#endif /* __DEBUG_SERIAL_H */
