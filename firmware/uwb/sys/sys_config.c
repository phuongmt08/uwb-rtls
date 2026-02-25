/**
 * @file       sys_config.c
 * @copyright
 * @license
 * @version    1.1.0
 * @date       2025-12-24
 * @author     Phuong Mai
 * @brief      
 * @note       None
 * @example    None
 */
/* Includes ----------------------------------------------------------------- */
#include "sys_config.h"
#include "sys_logger.h"
#include "positioning_config.h"
#include <string.h>

#ifdef HAVE_FLASH_STORAGE
#include "bsp_flash.h"
#include "bsp_util.h"
#include "stm32f4xx_hal.h"
#endif

/* Private defines ---------------------------------------------------------- */
#define CONFIG_RAM_MAGIC    0xC0FEC0DE

#ifdef HAVE_FLASH_STORAGE
#define FLASH_SECTOR0_BASE  0x08040000u
#define FLASH_SECTOR0_SIZE  (128u * 1024u)
#define FLASH_SECTOR1_BASE  0x08060000u
#define FLASH_SECTOR1_SIZE  (128u * 1024u)
#endif

/* Private variables -------------------------------------------------------- */
static sys_config_t g_config;

#ifdef HAVE_FLASH_STORAGE
static bsp_flash_dual_t g_flash_storage;
static uint8_t g_flash_init_done = 0;
#else
static sys_config_t g_config_backup;
static uint32_t g_config_magic = 0;
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

static uint32_t config_calc_crc32(const void *data, uint32_t len)
{
    return bsp_crc32(data, len);
}

static uint32_t config_get_timestamp(void)
{
    return bsp_rtc_get_timestamp();
}

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
    
    /* Always start with defaults first */
    sys_config_reset_to_defaults();
    
#ifdef HAVE_FLASH_STORAGE
    if (bsp_util_init() != BSP_UTIL_OK) {
        RLOG_W(LOG_OBJECT_CODE_SYS_CFG, "BSP util init failed");
    }
    
    if (flash_storage_init() != 0) {
        RLOG_W(LOG_OBJECT_CODE_SYS_CFG, "Flash init failed, using RAM only");
    }
#endif
    
    /* Try to load from flash/RAM (will override defaults if valid) */
    if (sys_config_load() != 0) {
        RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "No saved config, using defaults");
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
    
    uint32_t crc_offset = offsetof(sys_config_t, crc32);
    g_config.crc32 = config_calc_crc32(&g_config, crc_offset);
    
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
        return -1;
    }

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

    uint32_t crc_offset = offsetof(sys_config_t, crc32);
    uint32_t calc_crc = config_calc_crc32(&temp_config, crc_offset);

    if (calc_crc != temp_config.crc32) {
        RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_CRC, "Config CRC mismatch: calc=0x%08X != stored=0x%08X",
               calc_crc, temp_config.crc32);
        return -1;
    }

    if (temp_config.config_version != CONFIG_VERSION) {
        RLOG_W(LOG_OBJECT_CODE_SYS_CFG, "Config version mismatch: flash=%u != fw=%u - resetting to defaults",
               temp_config.config_version, CONFIG_VERSION);
        /* Save old antenna delay */
        uint16_t old_tx_delay = temp_config.tx_antenna_delay;
        uint16_t old_rx_delay = temp_config.rx_antenna_delay;
        /* Version changed - reset everything to defaults */
        sys_config_reset_to_defaults();
        /* Restore old antenna delay */
        g_config.tx_antenna_delay = old_tx_delay;
        g_config.rx_antenna_delay = old_rx_delay;
        /* Save defaults to flash ngay để override config cũ */
        if (sys_config_save() == 0) {
            RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Default config saved to flash (antenna delay giữ nguyên)");
        } else {
            RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_HAL, "Failed to save default config to flash");
        }
        return -1;  /* Return error to indicate config was reset */
    }

    if (temp_config.role != DEVICE_ROLE_TAG && temp_config.role != DEVICE_ROLE_ANCHOR) {
        RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_INVALID_PARAM, "Invalid role in flash");
        return -1;
    }

    if (temp_config.uwb_channel < 1 || temp_config.uwb_channel > 7) {
        RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_INVALID_PARAM, "Invalid channel in flash");
        return -1;
    }

    if (temp_config.rx_timeout_ms > 500 || temp_config.rx_timeout_ms < 5) {
        RLOG_W(LOG_OBJECT_CODE_SYS_CFG, "Invalid rx_timeout=%lu in flash, forcing to %u ms",
               temp_config.rx_timeout_ms, DEFAULT_RX_TIMEOUT_MS);
        temp_config.rx_timeout_ms = DEFAULT_RX_TIMEOUT_MS;
    }

    if (temp_config.ranging_period_ms > 5000 || temp_config.ranging_period_ms < 50) {
        RLOG_W(LOG_OBJECT_CODE_SYS_CFG, "Invalid ranging_period=%u in flash, forcing to %u ms",
               temp_config.ranging_period_ms, DEFAULT_RANGING_PERIOD_MS);
        temp_config.ranging_period_ms = DEFAULT_RANGING_PERIOD_MS;
    }

    memcpy(&g_config, &temp_config, sizeof(sys_config_t));
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Config loaded from flash (CRC: 0x%08X)", calc_crc);
    return 0;
#else
    if (g_config_magic == CONFIG_RAM_MAGIC) {
        /* Save old antenna delay */
        uint16_t old_tx_delay = g_config_backup.tx_antenna_delay;
        uint16_t old_rx_delay = g_config_backup.rx_antenna_delay;
        if (g_config_backup.config_version != CONFIG_VERSION) {
            RLOG_W(LOG_OBJECT_CODE_SYS_CFG, "Config version mismatch: RAM=%u != fw=%u - resetting to defaults",
                   g_config_backup.config_version, CONFIG_VERSION);
            sys_config_reset_to_defaults();
            g_config.tx_antenna_delay = old_tx_delay;
            g_config.rx_antenna_delay = old_rx_delay;
            if (sys_config_save() == 0) {
                RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Default config saved to RAM (antenna delay giữ nguyên)");
            } else {
                RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_HAL, "Failed to save default config to RAM");
            }
            return -1;
        }
        memcpy(&g_config, &g_config_backup, sizeof(sys_config_t));
        RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Config loaded from RAM");
        return 0;
    } else {
        RLOG_D(LOG_OBJECT_CODE_SYS_CFG, "No valid RAM backup found");
        return -1;
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
    
    g_config.config_version = CONFIG_VERSION;
    g_config.role = DEFAULT_DEVICE_ROLE;
    g_config.device_id = DEFAULT_DEVICE_ID;
    g_config.method = DEFAULT_RANGING_METHOD;
    g_config.uwb_channel = DEFAULT_UWB_CHANNEL;
    g_config.ranging_period_ms = DEFAULT_RANGING_PERIOD_MS;
    g_config.rx_timeout_ms = DEFAULT_RX_TIMEOUT_MS;
    
    g_config.uwb_prf = DEFAULT_UWB_PRF;
    g_config.uwb_data_rate = DEFAULT_UWB_DATA_RATE;
    g_config.uwb_preamble_code = DEFAULT_UWB_PREAMBLE_CODE;
    
    /* Set default antenna delays for ALL roles (TAG and ANCHOR) */
    g_config.tx_antenna_delay = DEFAULT_TX_ANT_DLY;
    g_config.rx_antenna_delay = DEFAULT_RX_ANT_DLY;
    g_config.tx_power = DEFAULT_TX_POWER;
    
    g_config.anchor_count = 0;
}

void sys_config_print(void)
{
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "");
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "========== DEVICE CONFIGURATION ==========");
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
        RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Ranging Period: %d ms (%d Hz)", 
               g_config.ranging_period_ms, 1000 / g_config.ranging_period_ms);
        RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "RX Timeout    : %lu ms", g_config.rx_timeout_ms);
    }
    
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "==========================================");
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "");
}

/* End of file -------------------------------------------------------- */