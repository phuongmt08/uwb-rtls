#ifndef APP_BLE_CENTRAL_H
#define APP_BLE_CENTRAL_H

/**@brief Initialize BLE stack and components.
 * 
 * This function initializes:
 * - BLE stack
 * - GATT module
 * - Database discovery
 * - Scanning
 */
void ble_central_init(void);

/* Internal functions (called by ble_central_init) */
void ble_stack_init(void);
void db_discovery_init(void);
void scan_init(void);
void gatt_init(void);

#endif // APP_BLE_CENTRAL_H