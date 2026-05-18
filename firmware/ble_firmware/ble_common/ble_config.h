#ifndef BLE_CONFIG_H__
#define BLE_CONFIG_H__

#include <stdint.h>
#include "ble.h"

#ifdef __cplusplus
extern "C" {
#endif

// =============================================================================
// 1. SYSTEM UUID CONFIGURATION
// =============================================================================

/**
 * @brief 16-bit Custom Service UUID.
 * 
 * This UUID serves as the primary unique identifier for the system.
 * 
 * Usage:
 * - On Peripheral: Include this UUID in the Advertising or Scan Response packet.
 * - On Central: Set up a Scan Filter matching this precise UUID. This ensures 
 *               the Central only discovers your Peripherals and ignores other 
 *               Bluetooth devices in the environment.
 */
#define SYSTEM_CONFIG_SERVICE_UUID        0x0810 


// =============================================================================
// 2. BLE PARAMETERS
// =============================================================================
// Sharing these parameters ensures seamless handshakes between Central & Peripheral

/** 
 * @brief Advertising interval for the Peripheral.
 * Time between consecutive advertising packets. 
 * Value is in units of 0.625 ms. Example: 64 * 0.625 ms = 40 ms.
 */
#define SYSTEM_CONFIG_ADV_INTERVAL        64     

/** 
 * @brief Minimum acceptable connection interval.
 */
#define SYSTEM_CONFIG_MIN_CONN_INTERVAL   MSEC_TO_UNITS(7.5, UNIT_1_25_MS)

/** 
 * @brief Maximum acceptable connection interval.
 */
#define SYSTEM_CONFIG_MAX_CONN_INTERVAL   MSEC_TO_UNITS(15, UNIT_1_25_MS)

/** 
 * @brief Slave latency.
 * Determines how many connection events the Peripheral (Slave) can safely
 * skip without dropping the connection. Set to 0 for maximum responsiveness.
 */
#define SYSTEM_CONFIG_SLAVE_LATENCY       0

/** 
 * @brief Connection supervisory time-out.
 * Maximum time allowed without receiving a packet before the stack drops 
 * the connection. Value provided in milliseconds.
 */
#define SYSTEM_CONFIG_CONN_SUP_TIMEOUT    MSEC_TO_UNITS(4000, UNIT_10_MS)

/**
 * @brief Central scan interval in milliseconds.
 */
#define SYSTEM_CONFIG_SCAN_INTERVAL_MS     100

/**
 * @brief Central scan window in milliseconds.
 */
#define SYSTEM_CONFIG_SCAN_WINDOW_MS       50

/**
 * @brief Central scan duration in milliseconds.
 * Set to 0 to scan indefinitely.
 */
#define SYSTEM_CONFIG_SCAN_DURATION_MS     0


// =============================================================================
// 3. TX POWER
// =============================================================================
/**
 * @brief TX Power level for the device.
 * Valid values for nRF52: -40, -20, -16, -12, -8, -4, 0, 3, 4. 
 */
#define SYSTEM_CONFIG_TX_POWER            4


// =============================================================================
// 4. DATA CONFIGURATION
// =============================================================================

/**
 * @brief Device Name Prefix.
 */
#define SYSTEM_CONFIG_DEVICE_PREFIX       "UWB_RTLS_"

/**
 * @brief ATT MTU Size.
 */
#define SYSTEM_CONFIG_MTU_SIZE            247

/**
 * @brief Preferred BLE PHY.
 * BLE_GAP_PHY_1MBPS = Standard
 * BLE_GAP_PHY_2MBPS = High throughput
 * BLE_GAP_PHY_CODED = Long Range
 */
#define SYSTEM_CONFIG_PREFERRED_PHY       BLE_GAP_PHY_1MBPS

#ifdef __cplusplus
}
#endif

#endif // BLE_CONFIG_H__
