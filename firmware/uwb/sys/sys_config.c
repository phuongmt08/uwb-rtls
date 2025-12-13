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

#ifdef HAVE_FLASH_STORAGE
#include "bsp_flash.h"
#include "bsp_util.h"
#include "stm32f4xx_hal.h"
#endif

/* Private defines ---------------------------------------------------------- */
#define CONFIG_RAM_MAGIC    0xC0FEC0DE  /* Magic number for valid config */

#ifdef HAVE_FLASH_STORAGE
/* Flash sectors for config storage (dual-sector for wear leveling) */
#define FLASH_SECTOR0_BASE  0x08010000u  /* Sector 4: 64KB */
#define FLASH_SECTOR0_SIZE  (64u * 1024u)
#define FLASH_SECTOR1_BASE  0x08020000u  /* Sector 5: 128KB */
#define FLASH_SECTOR1_SIZE  (128u * 1024u)
#endif

/* Private variables -------------------------------------------------------- */
static sys_config_t g_config;           /* Active configuration */


#ifdef HAVE_FLASH_STORAGE
static bsp_flash_dual_t g_flash_storage;  /* Flash storage manager */
static uint8_t g_flash_init_done = 0;     /* Flash init flag */
#else
static sys_config_t g_config_backup;    /* RAM backup storage */
static uint32_t g_config_magic = 0;     /* Magic number for backup validity */
#endif
/* Private function prototypes ---------------------------------------------- */
#ifdef HAVE_FLASH_STORAGE
static uint32_t config_calc_crc32(const void *data, uint32_t len);
static uint32_t config_get_timestamp(void);
static int flash_storage_init(void);
#endif

/* ========================================================================== */
/*                         PRIVATE FUNCTIONS                                 */
/* ========================================================================== */

#ifdef HAVE_FLASH_STORAGE
/**
 * @brief Hardware CRC32 calculation using bsp_util
 */
static uint32_t config_calc_crc32(const void *data, uint32_t len)
{
    return bsp_crc32(data, len);
}

/**
 * @brief Get timestamp for flash record (RTC timestamp)
 */
static uint32_t config_get_timestamp(void)
{
    return bsp_rtc_get_timestamp();
}

/**
 * @brief Initialize flash storage
 */
static int flash_storage_init(void)
{
    if (g_flash_init_done) {
        return 0;
    }
    
    bsp_flash_status_t status = bsp_flash_dual_init(
        &g_flash_storage,
        FLASH_SECTOR0_BASE, FLASH_SECTOR0_SIZE,
        FLASH_SECTOR1_BASE, FLASH_SECTOR1_SIZE,
        config_calc_crc32,
        config_get_timestamp
    );
    
    if (status != BSP_FLASH_OK) {
        RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_HAL, "Flash init failed: %d", status);
        return -1;
    }
    
    g_flash_init_done = 1;
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Flash storage initialized");
    return 0;
}
#endif

/* ========================================================================== */
/*                         PUBLIC FUNCTIONS                                  */
/* ========================================================================== */

void sys_config_init(void)
{
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Initializing configuration...");
    
#ifdef HAVE_FLASH_STORAGE
    /* Initialize bsp_util for CRC and RTC */
    if (bsp_util_init() != BSP_UTIL_OK) {
        RLOG_W(LOG_OBJECT_CODE_SYS_CFG, "BSP util init failed");
    }
    
    /* Initialize flash storage */
    if (flash_storage_init() != 0) {
        RLOG_W(LOG_OBJECT_CODE_SYS_CFG, "Flash init failed, using RAM only");
    }
#endif
    
    /* Try to load from flash/RAM */
    if (sys_config_load() != 0) {
        /* If load fails, use defaults */
        RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "No saved config, using defaults");
        sys_config_reset_to_defaults();
    }
    
    sys_config_print();
}
sys_config_t* sys_config_get(void)
{
    return &g_config;
}

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

int sys_config_save(void)
{
#ifdef HAVE_FLASH_STORAGE
    if (!g_flash_init_done) {
        RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_NOT_INIT, "Flash not initialized");
        return -1;
    }
    
    /* Calculate CRC32 (exclude crc32 field itself) */
    uint32_t crc_offset = offsetof(sys_config_t, crc32);
    g_config.crc32 = config_calc_crc32(&g_config, crc_offset);
    
    /* Write to flash */
    bsp_flash_status_t status = bsp_flash_write_config(
        &g_flash_storage,
        &g_config,
        sizeof(sys_config_t)
    );
    
    if (status != BSP_FLASH_OK) {
        RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_HAL, "Flash write failed: %d", status);
        return -1;
    }
    
#ifdef HAVE_RTC
    uint32_t timestamp = bsp_rtc_get_timestamp();
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Config saved to flash (CRC: 0x%08X, TS: %lu)", g_config.crc32, timestamp);
#else
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Config saved to flash (CRC: 0x%08X)", g_config.crc32);
#endif
    return 0;
#else
    /* Save to RAM backup */
    memcpy(&g_config_backup, &g_config, sizeof(sys_config_t));
    g_config_magic = CONFIG_RAM_MAGIC;
    
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Config saved to RAM");
    return 0;
#endif
}

int sys_config_load(void)
{
#ifdef HAVE_FLASH_STORAGE
    if (!g_flash_init_done) {
        return -1; /* Flash not initialized */
    }
    
    /* Read from flash */
    sys_config_t temp_config;
    uint32_t bytes_read = bsp_flash_read_config(
        &g_flash_storage,
        &temp_config,
        sizeof(sys_config_t)
    );
    
    if (bytes_read != sizeof(sys_config_t)) {
        RLOG_D(LOG_OBJECT_CODE_SYS_CFG, "No valid config in flash");
        return -1;
    }
    
    /* Verify CRC32 */
    uint32_t crc_offset = offsetof(sys_config_t, crc32);
    uint32_t calc_crc = config_calc_crc32(&temp_config, crc_offset);
    
    if (calc_crc != temp_config.crc32) {
        RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_CRC, "Config CRC mismatch: calc=0x%08X != stored=0x%08X",
               calc_crc, temp_config.crc32);
        return -1;
    }
    
    /* Validate config values */
    if (temp_config.role != DEVICE_ROLE_TAG && temp_config.role != DEVICE_ROLE_ANCHOR) {
        RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_INVALID_PARAM, "Invalid role in flash");
        return -1;
    }
    
    if (temp_config.uwb_channel < 1 || temp_config.uwb_channel > 7) {
        RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_INVALID_PARAM, "Invalid channel in flash");
        return -1;
    }
    
    /* All checks passed, copy to active config */
    memcpy(&g_config, &temp_config, sizeof(sys_config_t));
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Config loaded from flash (CRC: 0x%08X)", calc_crc);
    return 0;
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

int sys_config_set_rx_timeout(uint32_t timeout_ms)
{
    if (timeout_ms < 10 || timeout_ms > 1000) {
        RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_INVALID_PARAM, 
               "Invalid RX timeout: %lu ms", timeout_ms);
        return -1;
    }
    
    g_config.rx_timeout_ms = timeout_ms;
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "RX timeout set to: %lu ms", timeout_ms);
    return 0;
}

int sys_config_set_antenna_delay(uint16_t tx_delay, uint16_t rx_delay)
{
    g_config.tx_antenna_delay = tx_delay;
    g_config.rx_antenna_delay = rx_delay;
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Antenna delay: TX=%u, RX=%u", 
           tx_delay, rx_delay);
    return 0;
}


int sys_config_set_tx_power(uint32_t power)
{
    g_config.tx_power = power;
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "TX power set to: 0x%08lX", power);
    return 0;
}

void sys_config_reset_to_defaults(void)
{
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Resetting to factory defaults");
    
    memset(&g_config, 0, sizeof(sys_config_t));
    
    g_config.role = DEFAULT_DEVICE_ROLE;
    g_config.device_id = DEFAULT_DEVICE_ID;
    g_config.method = DEFAULT_RANGING_METHOD;
    g_config.uwb_channel = DEFAULT_UWB_CHANNEL;
    g_config.ranging_period_ms = DEFAULT_RANGING_PERIOD_MS;
    g_config.rx_timeout_ms = DEFAULT_RX_TIMEOUT_MS;
    g_config.hw_rev_major = DEFAULT_HW_REV_MAJOR;
    g_config.hw_rev_minor = DEFAULT_HW_REV_MINOR;
    
    /* UWB radio config */
    g_config.uwb_prf = DEFAULT_UWB_PRF;
    g_config.uwb_data_rate = DEFAULT_UWB_DATA_RATE;
    g_config.uwb_preamble_code = DEFAULT_UWB_PREAMBLE_CODE;
    
    /* Antenna delay */
    g_config.tx_antenna_delay = DEFAULT_TX_ANT_DLY;
    g_config.rx_antenna_delay = DEFAULT_RX_ANT_DLY;
    
    /* TX power */
    g_config.tx_power = DEFAULT_TX_POWER;
}

/* Update sys_config_print() to show all parameters: */
void sys_config_print(void)
{
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "");
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "========== DEVICE CONFIGURATION ==========");
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "FW Version   : v%d.%d.%d", 
           FW_VERSION_MAJOR, FW_VERSION_MINOR, FW_VERSION_PATCH);
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "HW Revision  : v%d.%d",
           g_config.hw_rev_major, g_config.hw_rev_minor);
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Device Role  : %s (0x%02X)",
           g_config.role == DEVICE_ROLE_TAG ? "TAG" : "ANCHOR",
           g_config.role);
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Device ID    : 0x%02X", g_config.device_id);
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Ranging      : %s",
           g_config.method == RANGING_DS_TWR ? "DS-TWR" : "TDoA");
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "-------------- UWB RADIO --------------");
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Channel      : %d", g_config.uwb_channel);
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "PRF          : %d MHz", g_config.uwb_prf);
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Data Rate    : %d", g_config.uwb_data_rate);
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Preamble Code: %d", g_config.uwb_preamble_code);
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "-------------- CALIBRATION ------------");
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "TX Ant Delay : %u", g_config.tx_antenna_delay);
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "RX Ant Delay : %u", g_config.rx_antenna_delay);
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "TX Power     : 0x%08lX", g_config.tx_power);
    
    if (g_config.role == DEVICE_ROLE_TAG) {
        RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "-------------- TIMING -----------------");
        RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Ranging Period: %d ms", g_config.ranging_period_ms);
        RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "RX Timeout    : %lu ms", g_config.rx_timeout_ms);
    }
    
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "==========================================");
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "");
}


/* End of file -------------------------------------------------------- */
