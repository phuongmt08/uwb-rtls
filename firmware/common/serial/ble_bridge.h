#ifndef __BLE_BRIDGE_H
#define __BLE_BRIDGE_H

#include "serial.h"

typedef struct {
    uint32_t dma_rx_checks;
    uint32_t dma_not_running;
    uint32_t dma_recoveries;
    uint32_t dma_start_failed;
    uint32_t rx_bytes;
    uint32_t ring_dropped_bytes;
    uint32_t frames_ok;
    uint32_t frame_bad_checksum;
    uint32_t frame_bad_length;
    uint32_t frame_resync;
    uint32_t uart_error_count;
    uint32_t uart_last_error;
    uint32_t uart_parity_errors;
    uint32_t uart_noise_errors;
    uint32_t uart_framing_errors;
    uint32_t uart_overrun_errors;
    uint32_t uart_dma_errors;
    uint32_t tx_ok;
    uint32_t tx_failed;
} ble_bridge_diag_t;

/* Live debugger watchpoint. Reset by ble_bridge_init(). */
extern volatile ble_bridge_diag_t g_ble_bridge_diag;

void ble_bridge_init(void);
void ble_bridge_set_tx_handler(serial_func_t handler);
void ble_bridge_rx_push(const uint8_t *data, uint32_t len);
void ble_bridge_uart_rx_check(void);
void ble_bridge_uart_rx_cplt(void);
void ble_bridge_uart_rx_error(uint32_t error_code);
void ble_bridge_uart_rx_recover(void);
int  ble_bridge_read(int file, char *ptr, int len, uint8_t type);
int  ble_bridge_write(int file, char *ptr, int len, uint8_t type);

#endif /* __BLE_BRIDGE_H */
