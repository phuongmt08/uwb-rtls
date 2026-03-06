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
#include <string.h>
#include <stddef.h>
#ifdef HAVE_FLASH_STORAGE
#include "sys_flash_storage.h"
#include "bsp_util.h"
#endif

/* Private defines ---------------------------------------------------------- */
#define CONFIG_RAM_MAGIC    0xC0FEC0DE

/* Flash sector addresses are defined in sys_flash_storage.h */

/* Private variables -------------------------------------------------------- */

/**
 * @brief Internal flash storage layout.
 *        sys_config_t holds config_version, device_type, and the protobuf
 *        uwb config struct (protobuf_uwb_cfg_t).  crc32 guards everything.
 */
typedef struct {
    sys_config_t  config;       /* {config_version, _pad[3], device_type, uwb} */
    uint8_t       _reserved[4];
    uint32_t      crc32;        /* must be last */
} sys_config_storage_t;

static sys_config_storage_t g_storage;

#ifdef HAVE_FLASH_STORAGE
/* Flash handle is owned by sys_flash_storage — obtained via sys_flash_storage_get() */
#else
static sys_config_storage_t g_storage_backup;
static uint32_t g_config_magic = 0;
#endif

static bool sys_config_device_role_valid(device_role_t role)
{
    return role == DEVICE_ROLE_TAG || role == DEVICE_ROLE_ANCHOR;
}

static bool sys_config_device_type_valid(device_type_t device_type)
{
    return device_type == DEVICE_TYPE_UNSPECIFIED ||
           device_type == DEVICE_TYPE_TAG ||
           device_type == DEVICE_TYPE_ANCHOR ||
           device_type == DEVICE_TYPE_GATEWAY ||
           device_type == DEVICE_TYPE_DEBUG_TOOL;
}

static bool sys_config_host_transport_valid(host_transport_t host_transport)
{
    return host_transport == HOST_TRANSPORT_UNSPECIFIED ||
           host_transport == HOST_TRANSPORT_USB ||
           host_transport == HOST_TRANSPORT_UART;
}

static device_type_t sys_config_default_device_type_from_role(device_role_t role)
{
    if (role == DEVICE_ROLE_TAG) {
        return DEVICE_TYPE_TAG;
    }
    if (role == DEVICE_ROLE_ANCHOR) {
        return DEVICE_TYPE_ANCHOR;
    }
    return DEVICE_TYPE_UNSPECIFIED;
}

/* Private function prototypes ---------------------------------------------- */
#ifdef HAVE_FLASH_STORAGE
static int flash_storage_init(void);
#endif

/* ========================================================================== */
/*                         PRIVATE FUNCTIONS                                 */
/* ========================================================================== */

#ifdef HAVE_FLASH_STORAGE

/**
 * @brief Proxy: delegate flash initialisation to the shared singleton.
 */
static int flash_storage_init(void)
{
    return sys_flash_storage_init();
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
    /* Delegate to shared singleton — also handles bsp_util_init internally */
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

sys_config_t *sys_config_get(void)
{
    return &g_storage.config;
}

int sys_config_set_role(device_role_t role)
{
    if (!sys_config_device_role_valid(role)) {
        RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_INVALID_PARAM, "Invalid role: %d", role);
        return -1;
    }
    g_storage.config.uwb.role = role;
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Role set to: %s",
           role == DEVICE_ROLE_TAG ? "TAG" : "ANCHOR");
    return 0;
}

int sys_config_set_device_type(device_type_t device_type)
{
    if (!sys_config_device_type_valid(device_type)) {
        RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_INVALID_PARAM, "Invalid device_type: %d", device_type);
        return -1;
    }
    g_storage.config.device_type = device_type;
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Device type set to: %u", (unsigned)device_type);
    return 0;
}

int sys_config_set_host_transport(host_transport_t host_transport)
{
    if (!sys_config_host_transport_valid(host_transport)) {
        RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_INVALID_PARAM,
               "Invalid host_transport: %d", host_transport);
        return -1;
    }

    if (host_transport == HOST_TRANSPORT_UNSPECIFIED) {
        host_transport = HOST_TRANSPORT_USB;
    }

    g_storage.config.host_transport = host_transport;
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Host transport set to: %s",
           host_transport == HOST_TRANSPORT_USB ? "USB" : "UART");
    return 0;
}

int sys_config_set_device_id(uint8_t id)
{
    if (id == 0x00 || id == 0xFF) {
        RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_INVALID_PARAM, "Invalid device ID: 0x%02X", id);
        return -1;
    }
    g_storage.config.uwb.device_id = id;
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Device ID set to: 0x%02X", id);
    return 0;
}

int sys_config_save(void)
{
#ifdef HAVE_FLASH_STORAGE
    if (!sys_flash_storage_get()) {
        RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_NOT_INIT, "Flash not initialized");
        return -1;
    }

    uint32_t crc_offset = offsetof(sys_config_storage_t, crc32);
    g_storage.crc32 = bsp_crc32(&g_storage, crc_offset);

    bsp_flash_status_t status = sys_flash_cfg_write(&g_storage, sizeof(sys_config_storage_t));

    if (status != BSP_FLASH_OK) {
        RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_HAL, "Flash write failed: %d", status);
        return -1;
    }

#ifdef HAVE_RTC
    uint32_t timestamp = bsp_rtc_get_timestamp();
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Config saved to flash (CRC: 0x%08X, TS: %lu)", g_storage.crc32, timestamp);
#else
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Config saved to flash (CRC: 0x%08X)", g_storage.crc32);
#endif
    return 0;
#else
    memcpy(&g_storage_backup, &g_storage, sizeof(sys_config_storage_t));
    g_config_magic = CONFIG_RAM_MAGIC;

    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Config saved to RAM");
    return 0;
#endif
}

int sys_config_load(void)
{
    #ifdef HAVE_FLASH_STORAGE
    if (!sys_flash_storage_get()) {
        return -1;
    }

    sys_config_storage_t temp_storage;
    uint32_t bytes_read = sys_flash_cfg_read(&temp_storage, sizeof(sys_config_storage_t));

    if (bytes_read != sizeof(sys_config_storage_t)) {
        RLOG_D(LOG_OBJECT_CODE_SYS_CFG, "No valid config in flash");
        return -1;
    }

    uint32_t crc_offset = offsetof(sys_config_storage_t, crc32);
    uint32_t calc_crc = bsp_crc32(&temp_storage, crc_offset);

    if (calc_crc != temp_storage.crc32) {
        RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_CRC, "Config CRC mismatch: calc=0x%08X != stored=0x%08X",
               calc_crc, temp_storage.crc32);
        return -1;
    }

    if (temp_storage.config.config_version != CONFIG_VERSION) {
        RLOG_W(LOG_OBJECT_CODE_SYS_CFG, "Config version mismatch: flash=%u != fw=%u - resetting to defaults",
               temp_storage.config.config_version, CONFIG_VERSION);
        uint32_t old_tx_delay = temp_storage.config.uwb.tx_antenna_delay;
        uint32_t old_rx_delay = temp_storage.config.uwb.rx_antenna_delay;
        sys_config_reset_to_defaults();
        g_storage.config.uwb.tx_antenna_delay = old_tx_delay;
        g_storage.config.uwb.rx_antenna_delay = old_rx_delay;
        if (sys_config_save() == 0) {
            RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Default config saved to flash (antenna delay giữ nguyên)");
        } else {
            RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_HAL, "Failed to save default config to flash");
        }
        return -1;
    }

    if (!sys_config_device_role_valid(temp_storage.config.uwb.role)) {
        RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_INVALID_PARAM, "Invalid role in flash");
        return -1;
    }

    if (!sys_config_device_type_valid(temp_storage.config.device_type)) {
        RLOG_W(LOG_OBJECT_CODE_SYS_CFG, "Invalid device_type in flash, deriving from role");
        temp_storage.config.device_type = sys_config_default_device_type_from_role(temp_storage.config.uwb.role);
    }

    if (!sys_config_host_transport_valid(temp_storage.config.host_transport)) {
        RLOG_W(LOG_OBJECT_CODE_SYS_CFG, "Invalid host_transport in flash, forcing USB");
        temp_storage.config.host_transport = HOST_TRANSPORT_USB;
    }

    if (temp_storage.config.host_transport == HOST_TRANSPORT_UNSPECIFIED) {
        temp_storage.config.host_transport = HOST_TRANSPORT_USB;
    }

    if (temp_storage.config.device_type == DEVICE_TYPE_UNSPECIFIED) {
        temp_storage.config.device_type = sys_config_default_device_type_from_role(temp_storage.config.uwb.role);
    }

    if (temp_storage.config.uwb.uwb_channel < 1 || temp_storage.config.uwb.uwb_channel > 7) {
        RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_INVALID_PARAM, "Invalid channel in flash");
        return -1;
    }

    if (temp_storage.config.uwb.rx_timeout_ms > 500 || temp_storage.config.uwb.rx_timeout_ms < 5) {
        RLOG_W(LOG_OBJECT_CODE_SYS_CFG, "Invalid rx_timeout=%lu in flash, forcing to %u ms",
               temp_storage.config.uwb.rx_timeout_ms, DEFAULT_RX_TIMEOUT_MS);
        temp_storage.config.uwb.rx_timeout_ms = DEFAULT_RX_TIMEOUT_MS;
    }

    if (temp_storage.config.uwb.ranging_period_ms > 5000 || temp_storage.config.uwb.ranging_period_ms < 50) {
        RLOG_W(LOG_OBJECT_CODE_SYS_CFG, "Invalid ranging_period=%lu in flash, forcing to %u ms",
               temp_storage.config.uwb.ranging_period_ms, DEFAULT_RANGING_PERIOD_MS);
        temp_storage.config.uwb.ranging_period_ms = DEFAULT_RANGING_PERIOD_MS;
    }

    memcpy(&g_storage, &temp_storage, sizeof(sys_config_storage_t));
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Config loaded from flash (CRC: 0x%08X)", calc_crc);
    return 0;
#else
    if (g_config_magic == CONFIG_RAM_MAGIC) {
        uint32_t old_tx_delay = g_storage_backup.config.uwb.tx_antenna_delay;
        uint32_t old_rx_delay = g_storage_backup.config.uwb.rx_antenna_delay;
        if (g_storage_backup.config.config_version != CONFIG_VERSION) {
            RLOG_W(LOG_OBJECT_CODE_SYS_CFG, "Config version mismatch: RAM=%u != fw=%u - resetting to defaults",
                   g_storage_backup.config.config_version, CONFIG_VERSION);
            sys_config_reset_to_defaults();
            g_storage.config.uwb.tx_antenna_delay = old_tx_delay;
            g_storage.config.uwb.rx_antenna_delay = old_rx_delay;
            if (sys_config_save() == 0) {
                RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Default config saved to RAM (antenna delay giữ nguyên)");
            } else {
                RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_HAL, "Failed to save default config to RAM");
            }
            return -1;
        }
        memcpy(&g_storage, &g_storage_backup, sizeof(sys_config_storage_t));
        RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Config loaded from RAM");
        return 0;
    } else {
        RLOG_D(LOG_OBJECT_CODE_SYS_CFG, "No valid RAM backup found");
        return -1;
    }
#endif
}


void sys_config_reset_to_defaults(void)
{
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Resetting to factory defaults");

    memset(&g_storage, 0, sizeof(sys_config_storage_t));

    g_storage.config.config_version            = CONFIG_VERSION;
    g_storage.config.device_type               = DEFAULT_DEVICE_TYPE;
    g_storage.config.host_transport            = DEFAULT_HOST_TRANSPORT;
    g_storage.config.uwb.role                  = DEFAULT_DEVICE_ROLE;
    g_storage.config.uwb.device_id             = DEFAULT_DEVICE_ID;
    g_storage.config.uwb.uwb_channel           = DEFAULT_UWB_CHANNEL;
    g_storage.config.uwb.ranging_period_ms     = DEFAULT_RANGING_PERIOD_MS;
    g_storage.config.uwb.rx_timeout_ms         = DEFAULT_RX_TIMEOUT_MS;
    g_storage.config.uwb.uwb_prf               = DEFAULT_UWB_PRF;
    g_storage.config.uwb.uwb_data_rate         = DEFAULT_UWB_DATA_RATE;
    g_storage.config.uwb.uwb_preamble_code     = DEFAULT_UWB_PREAMBLE_CODE;
    g_storage.config.uwb.tx_antenna_delay      = DEFAULT_TX_ANT_DLY;
    g_storage.config.uwb.rx_antenna_delay      = DEFAULT_RX_ANT_DLY;
    g_storage.config.uwb.tx_power              = DEFAULT_TX_POWER;
    g_storage.config.uwb.anchor_list.size      = 0;
}

void sys_config_print(void)
{
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "");
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "========== DEVICE CONFIGURATION ==========");
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Device Role  : %s (0x%02X)",
           g_storage.config.uwb.role == DEVICE_ROLE_TAG ? "TAG" : "ANCHOR",
           (unsigned)g_storage.config.uwb.role);
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Device Type  : %u", (unsigned)g_storage.config.device_type);
        RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Host I/O     : %s",
            g_storage.config.host_transport == HOST_TRANSPORT_USB ? "USB" : "UART");
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Device ID    : 0x%02X", (unsigned)g_storage.config.uwb.device_id);
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "-------------- UWB RADIO --------------");
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Channel      : %lu", g_storage.config.uwb.uwb_channel);
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "PRF          : %lu MHz", g_storage.config.uwb.uwb_prf);
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Data Rate    : %lu", g_storage.config.uwb.uwb_data_rate);
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Preamble Code: %lu", g_storage.config.uwb.uwb_preamble_code);
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "-------------- CALIBRATION ------------");
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "TX Ant Delay : %lu", g_storage.config.uwb.tx_antenna_delay);
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "RX Ant Delay : %lu", g_storage.config.uwb.rx_antenna_delay);
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "TX Power     : 0x%08lX", g_storage.config.uwb.tx_power);
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "-------------- TIMING -----------------");
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Ranging Period: %lu ms", g_storage.config.uwb.ranging_period_ms);
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "RX Timeout    : %lu ms", g_storage.config.uwb.rx_timeout_ms);
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "==========================================");
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "");
}

device_type_t sys_config_get_device_type(void)
{
    return g_storage.config.device_type;
}

host_transport_t sys_config_get_host_transport(void)
{
    if (g_storage.config.host_transport == HOST_TRANSPORT_UNSPECIFIED) {
        return HOST_TRANSPORT_USB;
    }
    return g_storage.config.host_transport;
}

void sys_config_export_protobuf(protobuf_uwb_cfg_t *dst)
{
    if (!dst) return;
    memcpy(dst, &g_storage.config.uwb, sizeof(protobuf_uwb_cfg_t));
}

int sys_config_import_protobuf(const protobuf_uwb_cfg_t *src)
{
    if (!src) return -1;

    if (!sys_config_device_role_valid(src->role)) {
        RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_INVALID_PARAM,
               "Invalid role in protobuf config: %d", src->role);
        return -1;
    }

    if (src->uwb_channel < 1 || src->uwb_channel > 7) {
        RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_INVALID_PARAM,
               "Invalid UWB channel in protobuf config: %lu", src->uwb_channel);
        return -1;
    }

    memcpy(&g_storage.config.uwb, src, sizeof(protobuf_uwb_cfg_t));

    if (g_storage.config.device_type == DEVICE_TYPE_UNSPECIFIED) {
        g_storage.config.device_type = sys_config_default_device_type_from_role(g_storage.config.uwb.role);
    }

    return 0;
}

/* End of file -------------------------------------------------------- */