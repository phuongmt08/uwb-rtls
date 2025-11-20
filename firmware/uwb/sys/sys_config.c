/* ============================== sys_config.c ===============================
 * @file       sys_config.c
 * @brief      System runtime configuration implementation
 * @version    1.0.0
 * @date       2025-11-15
 */

/* Includes ----------------------------------------------------------------- */
#include "sys_config.h"
#include "sys_logger.h"
#include "platform_config.h"
#include <string.h>

/* Private defines ---------------------------------------------------------- */
#define CONFIG_RAM_MAGIC    0xC0FEC0DE  /* Magic number for valid config */

/* Private variables -------------------------------------------------------- */
static sys_config_t g_config;           /* Active configuration */
static sys_config_t g_config_backup;    /* RAM backup storage */
static uint32_t g_config_magic = 0;     /* Magic number for backup validity */

/* ========================================================================== */
/*                         PUBLIC FUNCTIONS                                  */
/* ========================================================================== */

/**
 * @brief Initialize system configuration with defaults
 */
void sys_config_init(void)
{
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Initializing configuration...");
    
    /* Try to load from flash first */
    if (sys_config_load() != 0) {
        /* If load fails, use defaults */
        RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "No saved config, using defaults");
        sys_config_reset_to_defaults();
    }
    
    sys_config_print();
}

/**
 * @brief Get current configuration
 */
sys_config_t* sys_config_get(void)
{
    return &g_config;
}

/**
 * @brief Set device role
 */
int sys_config_set_role(device_role_t role)
{
    if (role != DEVICE_ROLE_TAG && role != DEVICE_ROLE_ANCHOR) {
        RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_INVALID_PARAM, "Invalid role: %d", role);
        return -1;
    }
    
    g_config.role = role;
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Role set to: %s", 
           role == DEVICE_ROLE_TAG ? "TAG" : "ANCHOR");
    return 0;
}

/**
 * @brief Set device ID
 */
int sys_config_set_device_id(uint8_t id)
{
    if (id == 0x00 || id == 0xFF) {
        RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_INVALID_PARAM, "Invalid device ID: 0x%02X", id);
        return -1;
    }
    
    g_config.device_id = id;
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Device ID set to: 0x%02X", id);
    return 0;
}

/**
 * @brief Set ranging method
 */
int sys_config_set_ranging_method(ranging_method_t method)
{
    if (method != RANGING_DS_TWR && method != RANGING_TDOA) {
        RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_INVALID_PARAM, "Invalid ranging method: %d", method);
        return -1;
    }
    
    g_config.method = method;
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Ranging method set to: %s",
           method == RANGING_DS_TWR ? "DS-TWR" : "TDoA");
    return 0;
}

/**
 * @brief Set UWB channel
 */
int sys_config_set_uwb_channel(uint8_t channel)
{
    if (channel < 1 || channel > 7) {
        RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_INVALID_PARAM, "Invalid UWB channel: %d", channel);
        return -1;
    }
    
    g_config.uwb_channel = channel;
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "UWB channel set to: %d", channel);
    return 0;
}

/**
 * @brief Set ranging period
 */
int sys_config_set_ranging_period(uint16_t period_ms)
{
    if (period_ms < 50 || period_ms > 5000) {
        RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_INVALID_PARAM, "Invalid ranging period: %d ms", period_ms);
        return -1;
    }
    
    g_config.ranging_period_ms = period_ms;
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Ranging period set to: %d ms", period_ms);
    return 0;
}

/**
 * @brief Save configuration
 */
int sys_config_save(void)
{
#ifdef HAVE_FLASH_STORAGE
    /* Save to flash - not implemented yet */
    RLOG_W(LOG_OBJECT_CODE_SYS_CFG, "Save to flash - NOT IMPLEMENTED YET");
    
    /* TODO: Implement flash write
     * 1. Calculate CRC32
     * 2. Write to flash
     * 3. Verify write
     */
    
    return -1; /* Not implemented */
#else
    /* Save to RAM backup */
    memcpy(&g_config_backup, &g_config, sizeof(sys_config_t));
    g_config_magic = CONFIG_RAM_MAGIC;
    
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Config saved to RAM");
    return 0;
#endif
}

/**
 * @brief Load configuration
 */
int sys_config_load(void)
{
#ifdef HAVE_FLASH_STORAGE
    /* Load from flash - not implemented yet */
    /* TODO: Implement flash read
     * 1. Read from flash
     * 2. Verify CRC32
     * 3. Validate values
     */
    
    return -1; /* Not implemented - will use defaults */
#else
    /* Load from RAM backup */
    if (g_config_magic == CONFIG_RAM_MAGIC) {
        memcpy(&g_config, &g_config_backup, sizeof(sys_config_t));
        RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Config loaded from RAM");
        return 0;
    } else {
        RLOG_D(LOG_OBJECT_CODE_SYS_CFG, "No valid RAM backup found");
        return -1; /* No valid backup */
    }
#endif
}

/**
 * @brief Reset to factory defaults
 */
void sys_config_reset_to_defaults(void)
{
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Resetting to factory defaults");
    
    memset(&g_config, 0, sizeof(sys_config_t));
    
    g_config.role = DEFAULT_DEVICE_ROLE;
    g_config.device_id = DEFAULT_DEVICE_ID;
    g_config.method = DEFAULT_RANGING_METHOD;
    g_config.uwb_channel = DEFAULT_UWB_CHANNEL;
    g_config.ranging_period_ms = DEFAULT_RANGING_PERIOD_MS;
    g_config.hw_rev_major = DEFAULT_HW_REV_MAJOR;
    g_config.hw_rev_minor = DEFAULT_HW_REV_MINOR;
}

/**
 * @brief Print current configuration
 */
void sys_config_print(void)
{
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "");
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "========== DEVICE CONFIGURATION ==========");
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "  FW Version  : v%d.%d.%d", 
           FW_VERSION_MAJOR, FW_VERSION_MINOR, FW_VERSION_PATCH);
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "  HW Revision : v%d.%d",
           g_config.hw_rev_major, g_config.hw_rev_minor);
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "  Device Role : %s (0x%02X)",
           g_config.role == DEVICE_ROLE_TAG ? "TAG" : "ANCHOR",
           g_config.role);
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "  Device ID   : 0x%02X", g_config.device_id);
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "  Ranging     : %s",
           g_config.method == RANGING_DS_TWR ? "DS-TWR" : "TDoA");
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "  UWB Channel : %d", g_config.uwb_channel);
    
    if (g_config.role == DEVICE_ROLE_TAG) {
        RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "  Ranging Period: %d ms", g_config.ranging_period_ms);
    }
    
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "==========================================");
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "");
}

/* End of file -------------------------------------------------------- */
