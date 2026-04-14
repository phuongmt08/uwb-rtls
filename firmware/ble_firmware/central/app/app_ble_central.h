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

#ifdef __cplusplus
}
#endif

#endif /* APP_BLE_CENTRAL_H */