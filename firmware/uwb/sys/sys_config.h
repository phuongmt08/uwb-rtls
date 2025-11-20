/* ============================== sys_config.h ===============================
 * @file       sys_config.h
 * @brief      System runtime configuration - Can be changed via BLE/USB
 * @version    1.0.0
 * @date       2025-11-15
 * 
 * @note       These settings can be modified at runtime.
 *             Storage options (configured in platform_config.h):
 *             - HAVE_FLASH_STORAGE undefined: RAM only (lost on reset)
 *             - HAVE_FLASH_STORAGE defined: Flash storage (persistent)
 */

#ifndef __SYS_CONFIG_H
#define __SYS_CONFIG_H

#ifdef __cplusplus
extern "C" {
#endif

/* Includes ----------------------------------------------------------------- */
#include <stdint.h>
#include <stdbool.h>
#include "platform_config.h"

/* ========================================================================== */
/*                    DEVICE RUNTIME CONFIGURATION                           */
/* ========================================================================== */

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
    /* Device identification */
    device_role_t   role;              /* Tag or Anchor */
    uint8_t         device_id;         /* Unique device ID (0x01-0xFF) */
    
    /* Ranging configuration */
    ranging_method_t method;           /* DS-TWR or TDoA (can change) */
    uint8_t         uwb_channel;       /* UWB channel (1-7) */
    uint16_t        ranging_period_ms; /* Tag ranging period (ms) */
    
    /* Hardware revision (from actual board) */
    uint8_t         hw_rev_major;      /* e.g., 1 for v1.0 */
    uint8_t         hw_rev_minor;      /* e.g., 0 for v1.0 */
    
    /* Reserved for future use */
    uint8_t         reserved[8];
    
    /* CRC for validation */
    uint32_t        crc32;
} sys_config_t;

/* ========================================================================== */
/*                         DEFAULT CONFIGURATION                             */
/* ========================================================================== */

/* Factory default values  */
#define DEFAULT_DEVICE_ROLE         DEVICE_ROLE_ANCHOR  /* Default: Anchor */
#define DEFAULT_DEVICE_ID           0x10                /* Default ID: 0x10 */
#define DEFAULT_RANGING_METHOD      RANGING_DS_TWR      /* Default: DS-TWR */
#define DEFAULT_UWB_CHANNEL         5                   /* Channel 5 (6489.6 MHz) */
#define DEFAULT_RANGING_PERIOD_MS   200                 /* 200ms (5Hz) */
#define DEFAULT_HW_REV_MAJOR        HW_REV_MAJOR        /* From platform_config.h */
#define DEFAULT_HW_REV_MINOR        HW_REV_MINOR        /* From platform_config.h */

/* ========================================================================== */
/*                         PUBLIC FUNCTIONS                                  */
/* ========================================================================== */

/**
 * @brief Initialize system configuration with defaults
 */
void sys_config_init(void);

/**
 * @brief Get current configuration
 * @return Pointer to current config structure
 */
sys_config_t* sys_config_get(void);

/**
 * @brief Set device role
 * @param role Device role (TAG or ANCHOR)
 * @return 0 on success, -1 on error
 */
int sys_config_set_role(device_role_t role);

/**
 * @brief Set device ID
 * @param id Device ID (0x01-0xFF)
 * @return 0 on success, -1 on error
 */
int sys_config_set_device_id(uint8_t id);

/**
 * @brief Set ranging method
 * @param method Ranging method (DS_TWR or TDOA)
 * @return 0 on success, -1 on error
 */
int sys_config_set_ranging_method(ranging_method_t method);

/**
 * @brief Set UWB channel
 * @param channel UWB channel (1-7)
 * @return 0 on success, -1 on error
 */
int sys_config_set_uwb_channel(uint8_t channel);

/**
 * @brief Set ranging period (Tag only)
 * @param period_ms Period in milliseconds
 * @return 0 on success, -1 on error
 */
int sys_config_set_ranging_period(uint16_t period_ms);

/**
 * @brief Save configuration
 * @note  RAM mode: saves to backup RAM (lost on reset)
 *        Flash mode: saves to flash (persistent) - not implemented yet
 * @return 0 on success, -1 on error
 */
int sys_config_save(void);

/**
 * @brief Load configuration
 * @note  RAM mode: loads from backup RAM
 *        Flash mode: loads from flash - not implemented yet
 * @return 0 on success, -1 on error
 */
int sys_config_load(void);

/**
 * @brief Reset to factory defaults
 */
void sys_config_reset_to_defaults(void);

/**
 * @brief Print current configuration
 */
void sys_config_print(void);

#ifdef __cplusplus
}
#endif

#endif /* __SYS_CONFIG_H */

/* End of file -------------------------------------------------------- */
