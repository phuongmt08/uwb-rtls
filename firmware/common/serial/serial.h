#ifndef __SERIAL_H
#define __SERIAL_H

#include <stdint.h>

typedef enum
{
    STREAM_UART_RX = 0,
    STREAM_UART_TX,
    STREAM_BLE_RX,
    STREAM_BLE_TX,
    STREAM_MAX
} stream_type_t;

typedef enum
{
    SERIAL_NETWORK_TRANSPORT_UART = 0,
    SERIAL_NETWORK_TRANSPORT_BLE  = 1
} serial_network_transport_t;

typedef int (*serial_func_t)(int file, char *ptr, int len, uint8_t type);

void serial_init(void);
void serial_register_tx_handler(stream_type_t stream, serial_func_t func);
void serial_uart_rx_push(const uint8_t *data, uint32_t len);
void serial_ble_rx_push(const uint8_t *data, uint32_t len);
void serial_set_network_transport(serial_network_transport_t transport);
stream_type_t serial_get_network_rx_stream(void);
stream_type_t serial_get_network_tx_stream(void);
int _read(int file, char *ptr, int maxlen, uint8_t type);
int _write(int file, char *ptr, int len, uint8_t type);

#endif /* __SERIAL_H */
