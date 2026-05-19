/**
 * @file       ble_peripheral.h
 * @brief      BLE Peripheral APIs
 */

#ifndef BLE_PERIPHERAL_H
#define BLE_PERIPHERAL_H

#include <stdint.h>
#include <stdbool.h>

/**
 * @brief Callback function type for passing received BLE NUS data
 */
typedef void (*ble_peripheral_rx_cb_t)(uint8_t const * p_data, uint16_t length);

void ble_peripheral_rx_cb_register(ble_peripheral_rx_cb_t cb);

void ble_peripheral_init(void);

void ble_peripheral_advertising_start(void);

void ble_peripheral_advertising_stop(void);

void ble_peripheral_adv_config_set(bool enable, const char * device_name, uint32_t serial_number);

uint8_t ble_peripheral_status_get(void);

void ble_peripheral_adv_status_update(const void * p_adv_status);

uint32_t ble_peripheral_send_data(uint8_t const * p_data, uint16_t length);

#endif // BLE_PERIPHERAL_H
