#include "serial.h"
#include "debug_serial.h"
#include "ble_bridge.h"

#define CHECK(cond, ret) if (!(cond)) return (ret)

static serial_func_t stream[STREAM_MAX];
static serial_network_transport_t s_network_transport = SERIAL_NETWORK_TRANSPORT_UART;

void serial_init(void)
{
    for (int i = 0; i < STREAM_MAX; i++) {
        stream[i] = 0;
    }

    debug_serial_init();
    ble_bridge_init();

    stream[STREAM_SERIAL_RX] = debug_serial_read;
    stream[STREAM_SERIAL_TX] = debug_serial_write;
    stream[STREAM_BLE_RX] = ble_bridge_read;
    stream[STREAM_BLE_TX] = ble_bridge_write;

    s_network_transport = SERIAL_NETWORK_TRANSPORT_UART;
}

void serial_register_tx_handler(stream_type_t stream_id, serial_func_t func)
{
    if (stream_id == STREAM_SERIAL_TX) {
        debug_serial_set_tx_handler(func);
    } else if (stream_id == STREAM_BLE_TX) {
        ble_bridge_set_tx_handler(func);
    }
}

void serial_uart_rx_push(const uint8_t *data, uint32_t len)
{
    if (!data || len == 0) return;
    debug_serial_rx_push(data, len);
}

void serial_ble_rx_push(const uint8_t *data, uint32_t len)
{
    if (!data || len == 0) return;
    ble_bridge_rx_push(data, len);
}

void serial_set_network_transport(serial_network_transport_t transport)
{
    s_network_transport = transport;
}

stream_type_t serial_get_network_rx_stream(void)
{
    return (s_network_transport == SERIAL_NETWORK_TRANSPORT_BLE) ? STREAM_BLE_RX : STREAM_SERIAL_RX;
}

stream_type_t serial_get_network_tx_stream(void)
{
    return (s_network_transport == SERIAL_NETWORK_TRANSPORT_BLE) ? STREAM_BLE_TX : STREAM_SERIAL_TX;
}

int _read(int file, char *ptr, int maxlen, uint8_t type)
{
    CHECK(file >= 0 && file < STREAM_MAX, -1);
    CHECK(stream[file] != 0, -1);
    return stream[file](file, ptr, maxlen, type);
}

int _write(int file, char *ptr, int len, uint8_t type)
{
    CHECK(file >= 0 && file < STREAM_MAX, -1);
    CHECK(stream[file] != 0, -1);
    return stream[file](file, ptr, len, type);
}
