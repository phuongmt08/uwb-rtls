#ifndef __BLE_BRIDGE_H
#define __BLE_BRIDGE_H

#include "serial.h"
#include "stm32f4xx_hal.h"

void ble_bridge_init(void);
void ble_bridge_set_tx_handler(serial_func_t handler);
void ble_bridge_rx_push(const uint8_t *data, uint32_t len);
void ble_bridge_uart_rx_cplt(void);
void ble_bridge_on_tx_cplt(UART_HandleTypeDef *huart);
int  ble_bridge_read(int file, char *ptr, int len, uint8_t type);
int  ble_bridge_write(int file, char *ptr, int len, uint8_t type);

#endif /* __BLE_BRIDGE_H */
