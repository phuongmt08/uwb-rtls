/**
 * @file       sys_config.h
 * @copyright
 * @license
 * @version    1.1.0
 * @date       2025-12-24
 * @author     Phuong Mai
 * @brief      
 * @note       None
 * @example    None
 */
#ifndef __SYS_CONFIG_H
#define __SYS_CONFIG_H

#ifdef __cplusplus
extern "C" {
#endif

/* Includes ----------------------------------------------------------------- */
#include <stdint.h>
#include <stdbool.h>
#include "config.h"
#include "positioning_config.h"

/**
 * @brief Device role enumeration
 */
typedef enum {
    DEVICE_ROLE_TAG = 0x01,
    DEVICE_ROLE_ANCHOR = 0x02
} device_role_t;

/**
 * @brief Ranging method enumeration
 */
typedef enum {
    RANGING_DS_TWR = 0x01,       /* Double-Sided Two-Way Ranging */
    RANGING_TDOA = 0x02          /* Time Difference of Arrival */
} ranging_method_t;

/**
 * @brief Device runtime configuration
 */
typedef struct {
  /* Config version - increment to force reset on firmware update */
  uint8_t config_version;
  
  /* Device identity */
  device_role_t role;
  uint8_t device_id;
  
  /* Ranging parameters */
  ranging_method_t method;
  uint16_t ranging_period_ms;  /* Ranging interval (ms) - synced with RANGING_INTERVAL_MS default */
  uint32_t rx_timeout_ms;
  
  /* UWB radio configuration */
  uint8_t uwb_channel;        // 1-7
  uint8_t uwb_prf;            // 16 or 64 MHz
  uint8_t uwb_data_rate;      // 0=110kbps, 1=850kbps, 2=6.8Mbps
  uint8_t uwb_preamble_code;  // 9-24
  
  /* Antenna delay calibration (in DWT units) */
  uint16_t tx_antenna_delay;
  uint16_t rx_antenna_delay;
  
  /* Power settings */
  uint32_t tx_power;          // TX power register value
  
  /* Multiple anchor support */
  uint8_t anchor_count;       // Number of anchors in list (0 = broadcast to all)
  uint8_t anchor_list[NUM_ANCHORS]; // List of anchor IDs to range with
  
  uint8_t reserved[16];
  
  /* CRC32 checksum (must be last) */
  uint32_t crc32;
} sys_config_t;

/* Default values */
#define CONFIG_VERSION              9

#define DEFAULT_DEVICE_ROLE         DEVICE_ROLE_ANCHOR
#define DEFAULT_DEVICE_ID           0x01
#define DEFAULT_RANGING_METHOD      RANGING_DS_TWR

#define DEFAULT_RANGING_PERIOD_MS   150 
#define DEFAULT_RX_TIMEOUT_MS       75

#define DEFAULT_UWB_CHANNEL         5
#define DEFAULT_UWB_PRF             64
#define DEFAULT_UWB_DATA_RATE       0   // 0=110kbps, 1=850kbps, 2=6.8Mbps
#define DEFAULT_UWB_PREAMBLE_CODE   10
#define DEFAULT_TX_ANT_DLY          16436
#define DEFAULT_RX_ANT_DLY          16436
#define DEFAULT_TX_POWER            0x1F1F1F1FUL
/* ========================================================================== */
/*                         PUBLIC FUNCTIONS                                  */
/* ========================================================================== */


void sys_config_init(void);
sys_config_t* sys_config_get(void);
/* Setters */
int sys_config_set_role(device_role_t role);
int sys_config_set_device_id(uint8_t id);
int sys_config_set_ranging_method(ranging_method_t method);
int sys_config_set_uwb_channel(uint8_t channel);
int sys_config_set_ranging_period(uint16_t period_ms);
int sys_config_set_rx_timeout(uint32_t timeout_ms);
int sys_config_set_antenna_delay(uint16_t tx_delay, uint16_t rx_delay);
int sys_config_set_tx_power(uint32_t power);

/* Storage */
int sys_config_save(void);
int sys_config_load(void);
void sys_config_reset_to_defaults(void);
void sys_config_print(void);
#ifdef __cplusplus
}
#endif

#endif /* __SYS_CONFIG_H */

/* End of file -------------------------------------------------------- */