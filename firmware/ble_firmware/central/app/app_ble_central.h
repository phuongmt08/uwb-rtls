/**
 * @file    app_ble_central.h
 * @brief   BLE Central application public interface.
 *
 * Exposes the single entry point used by main.c to bring up the BLE stack
 * and start scanning.  Internal sub-functions are declared here so that
 * components requiring forward declarations (e.g. GATT, scan) can reference
 * them without duplicating prototypes.
 */

#ifndef APP_BLE_CENTRAL_H
#define APP_BLE_CENTRAL_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* -------------------------------------------------------------------------
 * Public API
 * ---------------------------------------------------------------------- */

/**
 * @brief Initialize BLE Central and start scanning.
 *
 * Sequentially initializes:
 *  1. SoftDevice BLE stack
 *  2. GATT module
 *  3. LEDs (via bsp_led)
 *  4. Database discovery
 *  5. LBS client
 *  6. BLE scanner
 *
 * After this call the device continuously scans for peripherals that
 * advertise the target service UUID and connects automatically.
 */
void ble_central_init(void);

/* -------------------------------------------------------------------------
 * Internal sub-functions (called by ble_central_init)
 * ---------------------------------------------------------------------- */
void ble_stack_init(void);
void db_discovery_init(void);
void scan_init(void);
void gatt_init(void);

/**
 * @brief Update connection parameters for a specific connection.
 *
 * @param[in] conn_handle          Handle of the connection.
 * @param[in] min_interval_ms      Minimum connection interval (ms).
 * @param[in] max_interval_ms      Maximum connection interval (ms).
 * @param[in] slave_latency        Slave latency.
 * @param[in] conn_sup_timeout_ms  Supervision timeout (ms).
 */
void central_update_conn_params(uint16_t conn_handle, 
                                uint16_t min_interval_ms, 
                                uint16_t max_interval_ms, 
                                uint16_t slave_latency, 
                                uint16_t conn_sup_timeout_ms);

/* -------------------------------------------------------------------------
 * Bridge API
 * ---------------------------------------------------------------------- */
#include <stdbool.h>
void app_ble_central_scan_start(uint16_t interval_ms, uint16_t window_ms, uint16_t duration_ms, bool active);
void app_ble_central_scan_stop(void);
void app_ble_central_connect(const uint8_t *mac);
void app_ble_central_disconnect(void);
void app_ble_central_conn_params_set(uint16_t min_interval_ms, uint16_t max_interval_ms, uint16_t slave_latency, uint16_t conn_sup_timeout_ms);
bool app_ble_central_conn_params_get(uint16_t *min_ms, uint16_t *max_ms, uint16_t *lat, uint16_t *to_ms);
uint8_t app_ble_central_status_get(void);

#ifdef __cplusplus
}
#endif

#endif /* APP_BLE_CENTRAL_H */