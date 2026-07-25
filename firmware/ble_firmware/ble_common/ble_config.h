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
 * Value is in units of 0.625 ms. Example: 160 * 0.625 ms = 100 ms.
 * Traded off against reconnect latency: raised from 40 ms to cut
 * unconnected-state radio wake-ups by ~2.5x.
 */
#define SYSTEM_CONFIG_ADV_INTERVAL        160

/**
 * @brief Minimum acceptable connection interval.
 * The MCU<->peripheral UART link caps real throughput at ~184 kbps (230400
 * baud, 8N1), well below what a 7.5 ms interval can carry — a shorter
 * interval only wakes the radio more often for no extra usable throughput.
 */
#define SYSTEM_CONFIG_MIN_CONN_INTERVAL   MSEC_TO_UNITS(15, UNIT_1_25_MS)

/**
 * @brief Maximum acceptable connection interval.
 */
#define SYSTEM_CONFIG_MAX_CONN_INTERVAL   MSEC_TO_UNITS(30, UNIT_1_25_MS)

/**
 * @brief Slave latency.
 * Lets the Peripheral (Slave) skip up to N connection events when it has
 * nothing queued to send. Safe regardless of traffic pattern: under
 * continuous streaming there is always data pending so no events are
 * skipped; under bursty/idle traffic this cuts radio wake-ups for free.
 */
#define SYSTEM_CONFIG_SLAVE_LATENCY      6

/**
 * @brief Connection supervisory time-out.
 * Maximum time allowed without receiving a packet before the stack drops
 * the connection. Value provided in milliseconds. Must stay above
 * (1 + SLAVE_LATENCY) * MAX_CONN_INTERVAL * 2 (currently 420 ms) if either
 * of those is increased further.
 */
#define SYSTEM_CONFIG_CONN_SUP_TIMEOUT    MSEC_TO_UNITS(4000, UNIT_10_MS)

/**
 * @brief Central scan interval in milliseconds.
 */
#define SYSTEM_CONFIG_SCAN_INTERVAL_MS     100

/**
 * @brief Central scan window in milliseconds.
 */
#define SYSTEM_CONFIG_SCAN_WINDOW_MS       100

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

/* Maximum TX power used after a connection is established. */
#if defined(NRF52840_XXAA)
#define SYSTEM_CONFIG_CONN_TX_POWER       8
#else
#define SYSTEM_CONFIG_CONN_TX_POWER       4
#endif


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

// =============================================================================
// 5. BROADCAST CONFIGURATION
// =============================================================================

/**
 * @brief Enable BLE 5.0 Extended Advertising for broadcast.
 *        When enabled and payload <= BLE_BROADCAST_MAX_PACKET_SIZE bytes,
 *        broadcast uses a single typed manufacturer payload inside one
 *        Extended ADV packet instead of application-layer fragmentation.
 *        Larger packets must be rejected or fragmented above this layer.
 *
 * Requirements:
 *   - nRF52832: SoftDevice S132 v6.x+ (Extended ADV on 1M PHY only)
 *   - nRF52840: SoftDevice S140 v6.x+ (Full Extended ADV support)
 */
#define BLE_BROADCAST_USE_EXTENDED      1

/**
 * @brief Broadcast advertising interval (units of 0.625 ms).
 *        20 ms = 32 units. Faster than connection ADV for quick burst.
 */
#define SYSTEM_CONFIG_BCAST_ADV_INTERVAL   32

/**
 * @brief Number of ADV events per broadcast burst.
 *        Both command and ACK use a short burst. Central-level retransmission
 *        is handled separately and only starts after the ACK timeout.
 */
#define SYSTEM_CONFIG_BCAST_ADV_EVENTS     3

#ifdef __cplusplus
}
#endif

#endif // BLE_CONFIG_H__

