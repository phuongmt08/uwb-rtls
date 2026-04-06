/**
 * @file bb_app.h
 * @brief BLE Bridge Application Controller
 *
 * Parses deep inside the protobuf message targeted natively for this BLE peripheral.
 * Triggers hardware radio actions such as scanning, altering GAP params, or responding
 * with system statuses.
 */
#ifndef BB_APP_H
#define BB_APP_H

#include <stdint.h>
#include <stdbool.h>
#include "sdk_errors.h"

/**
 * @brief Initializes the Application logic bindings.
 * @return NRF_SUCCESS on successful initialization.
 */
ret_code_t bb_app_init(void);

/**
 * @brief Executes the protobuf command destined for the Peripheral device.
 * It will use nanopb (pb_decode) under the hood to extract parameters like
 * `ble_scan_start_t` or `ble_conn_params_set_t`, and execute them via SoftDevice APIs.
 *
 * @param[in] p_data Pointer to the raw protobuf payload byte array.
 * @param[in] length Total size of the payload.
 * @return NRF_SUCCESS if the command was successfully decoded and executed.
 */
ret_code_t bb_app_handle_cmd(uint8_t const * p_data, uint16_t length);

#endif // BB_APP_H
