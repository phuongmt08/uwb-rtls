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
#include "bsp_io.h"
#include "sys_logger.h"
#include "otp/otp.h"
#include <string.h>
#include <stddef.h>
#include <math.h>
#include "version.h"
#ifdef HAVE_FLASH_STORAGE
#include "sys_flash_storage.h"
#endif

/* Private defines ---------------------------------------------------------- */
#define CONFIG_RAM_MAGIC    0xC0FEC0DE
#define CFG_LOG(fmt, ...) RLOG_I(LOG_OBJECT_CODE_SYS_CFG, fmt, ##__VA_ARGS__)
#define FACTORY_OTP_CONFIRM_MAGIC 0x4F545057u /* 'OTPW' */

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

typedef struct {
    uint8_t device_type;
    uint8_t mfg_date[3];        /* day, month, year - 2000 */
    uint8_t hw_rev;
} __attribute__((packed)) sys_config_otp_device_info_t;

typedef struct {
    uint16_t tx_delay;
    uint16_t rx_delay;
} __attribute__((packed)) sys_config_otp_antenna_delay_t;

static sys_config_storage_t g_storage;
static uint32_t s_active_zone_id = 1;

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

static bool sys_config_prefilter_valid(const sys_prefilter_cfg_t *prefilter)
{
    if (!prefilter) {
        return false;
    }

    if (!isfinite(prefilter->recover_d2) ||
        !isfinite(prefilter->reject_d2) ||
        !isfinite(prefilter->r_base) ||
        !isfinite(prefilter->r_gate) ||
        !isfinite(prefilter->velocity_weight) ||
        !isfinite(prefilter->min_covariance)) {
        return false;
    }

    return prefilter->recover_d2 >= 0.0f &&
           prefilter->reject_d2 > prefilter->recover_d2 &&
           prefilter->r_base > 0.0f &&
           prefilter->r_gate > 0.0f &&
           prefilter->velocity_weight >= 0.0f &&
           prefilter->min_covariance > 0.0f;
}

static device_role_t sys_config_default_role_from_device_type(device_type_t device_type)
{
    if (device_type == DEVICE_TYPE_TAG) {
        return DEVICE_ROLE_TAG;
    }
    return DEVICE_ROLE_ANCHOR;
}

static const char *sys_config_device_type_name(device_type_t device_type)
{
    switch (device_type) {
    case DEVICE_TYPE_TAG:
        return "TAG";
    case DEVICE_TYPE_ANCHOR:
        return "ANCHOR";
    case DEVICE_TYPE_GATEWAY:
        return "GATEWAY";
    case DEVICE_TYPE_DEBUG_TOOL:
        return "DEBUG_TOOL";
    default:
        return "UNSPECIFIED";
    }
}

static void sys_config_apply_forced_mode(void)
{
#if FORCE_DEVICE_TAG_MODE
    g_storage.config.device_type = DEVICE_TYPE_TAG;
    g_storage.config.uwb.role = DEVICE_ROLE_TAG;
    RLOG_W(LOG_OBJECT_CODE_SYS_CFG, "FORCE_DEVICE_TAG_MODE enabled: forcing Device Type/Role to TAG");
#endif
}

static void sys_config_apply_dip_device_id_override(void)
{
    uint8_t dip_value = bsp_io_dip_read();
    if (dip_value == 0U) {
        RLOG_I(LOG_OBJECT_CODE_SYS_CFG,
               "[DIP=0] Using saved Device ID: %u",
               g_storage.config.uwb.device_id);
        return;
    }

    if (sys_config_set_device_id(dip_value) == 0) {
        RLOG_I(LOG_OBJECT_CODE_SYS_CFG,
               "[DIP=%u] Device ID FORCED to: %u",
               dip_value,
               dip_value);
    }
}

static bool sys_config_find_zone_for_anchor_id(uint8_t anchor_id, uint32_t *zone_id_out)
{
    if (anchor_id == 0U || !zone_id_out) {
        return false;
    }

    uint32_t matched_zone_id = 0U;
    uint32_t match_count = 0U;

    for (uint32_t zone = 0U; zone < 4U; zone++) {
        const protobuf_zone_profile_t *profile = &g_storage.config.zone_profiles[zone];
        if (!sys_config_zone_profile_valid(profile)) {
            continue;
        }

        uint32_t anchor_count = profile->anchors_count;

        for (uint32_t i = 0U; i < anchor_count; i++) {
            if (profile->anchors[i].anchor_id == anchor_id) {
                matched_zone_id = profile->zone_id;
                match_count++;
                break;
            }
        }
    }

    if (match_count == 1U && matched_zone_id >= 1U && matched_zone_id <= 4U) {
        *zone_id_out = matched_zone_id;
        return true;
    }

    return false;
}

static uint32_t sys_config_get_boot_zone_id(void)
{
    uint32_t default_zone_id = g_storage.config.default_zone_id;
    if (default_zone_id < 1U || default_zone_id > 4U) {
        default_zone_id = DEFAULT_ZONE_ID;
    }

    if (g_storage.config.uwb.role != DEVICE_ROLE_ANCHOR) {
        return default_zone_id;
    }

    uint32_t zone_id = 0U;
    if (sys_config_find_zone_for_anchor_id(g_storage.config.uwb.device_id, &zone_id)) {
        RLOG_I(LOG_OBJECT_CODE_SYS_CFG,
               "Anchor ID %u found in Zone %lu",
               g_storage.config.uwb.device_id,
               (unsigned long)zone_id);
        return zone_id;
    }

    RLOG_I(LOG_OBJECT_CODE_SYS_CFG,
           "Anchor ID %u not found in any zone; using default Zone %lu",
           g_storage.config.uwb.device_id,
           (unsigned long)default_zone_id);
    return default_zone_id;
}

static bool sys_config_mfg_date_pack(uint32_t date_ddmmyyyy, uint8_t packed[3])
{
    if (!packed) {
        return false;
    }

    uint32_t day = date_ddmmyyyy / 1000000u;
    uint32_t month = (date_ddmmyyyy / 10000u) % 100u;
    uint32_t year = date_ddmmyyyy % 10000u;

    if (day == 0u || day > 31u || month == 0u || month > 12u ||
        year < 2000u || year > 2255u) {
        return false;
    }

    packed[0] = (uint8_t)day;
    packed[1] = (uint8_t)month;
    packed[2] = (uint8_t)(year - 2000u);
    return true;
}

static uint32_t sys_config_mfg_date_unpack(const uint8_t packed[3])
{
    uint32_t day = packed[0];
    uint32_t month = packed[1];
    uint32_t year = 2000u + packed[2];
    return day * 1000000u + month * 10000u + year;
}

static otp_err_t sys_config_otp_get_device_info(sys_config_otp_device_info_t *info)
{
    if (!info) {
        return OTP_ERR_INVALID_ARG;
    }

    uint8_t len = 0u;
    otp_err_t err = otp_get(OTP_TYPE_DEVICE_INFO, info, sizeof(*info), &len);
    if (err != OTP_OK) {
        return err;
    }

    return (len == sizeof(*info)) ? OTP_OK : OTP_ERR_INVALID_ARG;
}

static otp_err_t sys_config_otp_set_device_info(device_type_t device_type, uint32_t mfg_date, uint8_t hw_rev)
{
    if (device_type == DEVICE_TYPE_UNSPECIFIED || !sys_config_device_type_valid(device_type)) {
        return OTP_ERR_INVALID_ARG;
    }

    sys_config_otp_device_info_t info = {
        .device_type = (uint8_t)device_type,
        .hw_rev = hw_rev,
    };

    if (!sys_config_mfg_date_pack(mfg_date, info.mfg_date)) {
        return OTP_ERR_INVALID_ARG;
    }

    return otp_set(OTP_TYPE_DEVICE_INFO, sizeof(info), &info);
}

static otp_err_t sys_config_otp_get_antenna_delay(uint16_t *tx_delay, uint16_t *rx_delay)
{
    if (!tx_delay || !rx_delay) {
        return OTP_ERR_INVALID_ARG;
    }

    sys_config_otp_antenna_delay_t ant = {0};
    uint8_t len = 0u;
    otp_err_t err = otp_get(OTP_TYPE_ANTENNA_DELAY, &ant, sizeof(ant), &len);
    if (err != OTP_OK) {
        return err;
    }
    if (len != sizeof(ant)) {
        return OTP_ERR_INVALID_ARG;
    }

    *tx_delay = ant.tx_delay;
    *rx_delay = ant.rx_delay;
    return OTP_OK;
}

static otp_err_t sys_config_otp_set_antenna_delay(uint16_t tx_delay, uint16_t rx_delay)
{
    sys_config_otp_antenna_delay_t ant = {
        .tx_delay = tx_delay,
        .rx_delay = rx_delay,
    };
    return otp_set(OTP_TYPE_ANTENNA_DELAY, sizeof(ant), &ant);
}

static void sys_config_reconcile_factory_otp(void)
{
    sys_config_otp_device_info_t otp_info = {0};
    if (sys_config_otp_get_device_info(&otp_info) == OTP_OK) {
        device_type_t otp_device_type = (device_type_t)otp_info.device_type;
        if (otp_device_type == DEVICE_TYPE_UNSPECIFIED ||
            !sys_config_device_type_valid(otp_device_type)) {
            RLOG_W(LOG_OBJECT_CODE_SYS_CFG,
                   "Invalid OTP device type ignored: 0x%02X",
                   otp_info.device_type);
        } else if (g_storage.config.device_type != otp_device_type) {
            RLOG_W(LOG_OBJECT_CODE_SYS_CFG,
                   "OTP device type overwrite: %s -> %s",
                   sys_config_device_type_name(g_storage.config.device_type),
                   sys_config_device_type_name(otp_device_type));
            g_storage.config.device_type = otp_device_type;
        }
    }

    uint16_t otp_tx_delay = 0;
    uint16_t otp_rx_delay = 0;
    if (sys_config_otp_get_antenna_delay(&otp_tx_delay, &otp_rx_delay) == OTP_OK) {
        if (g_storage.config.uwb.tx_antenna_delay != otp_tx_delay ||
            g_storage.config.uwb.rx_antenna_delay != otp_rx_delay) {
            RLOG_W(LOG_OBJECT_CODE_SYS_CFG,
                   "OTP antenna delay overwrite: TX=%lu RX=%lu -> TX=%u RX=%u",
                   g_storage.config.uwb.tx_antenna_delay,
                   g_storage.config.uwb.rx_antenna_delay,
                   otp_tx_delay,
                   otp_rx_delay);
            g_storage.config.uwb.tx_antenna_delay = otp_tx_delay;
            g_storage.config.uwb.rx_antenna_delay = otp_rx_delay;
        }
    }
}

/* ========================================================================== */
/*                         PUBLIC FUNCTIONS                                  */
/* ========================================================================== */

void sys_config_init(void)
{
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Initializing configuration...");

    /* Load persisted config first; fall back to defaults only if invalid/missing. */
    if (sys_config_load() != 0) {
        sys_config_reset_to_defaults();
        RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "No valid saved config, using defaults");

        if (sys_config_save() != 0) {
            RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_HAL, "Failed to persist default config");
        }
    }

    /* Load and override with OTP factory values if available */
    sys_config_otp_device_info_t otp_info = {0};
    if (sys_config_otp_get_device_info(&otp_info) == OTP_OK) {
        device_type_t otp_device_type = (device_type_t)otp_info.device_type;
        if (sys_config_set_device_type(otp_device_type) == 0) {
            RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Device Type overridden by OTP factory config: 0x%02X", otp_info.device_type);
        } else {
            RLOG_W(LOG_OBJECT_CODE_SYS_CFG, "Invalid OTP device type ignored: 0x%02X", otp_info.device_type);
        }
    }

    uint16_t otp_tx_delay = 0;
    uint16_t otp_rx_delay = 0;
    if (sys_config_otp_get_antenna_delay(&otp_tx_delay, &otp_rx_delay) == OTP_OK) {
        sys_config_t *cfg = sys_config_get();
        cfg->uwb.tx_antenna_delay = otp_tx_delay;
        cfg->uwb.rx_antenna_delay = otp_rx_delay;
        RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Antenna Delays loaded from OTP: TX=%u, RX=%u", 
               cfg->uwb.tx_antenna_delay, cfg->uwb.rx_antenna_delay);
    }

    sys_config_apply_forced_mode();
    sys_config_apply_dip_device_id_override();

    s_active_zone_id = sys_config_get_boot_zone_id();
    if (s_active_zone_id < 1U || s_active_zone_id > 4U) {
        s_active_zone_id = DEFAULT_ZONE_ID;
        g_storage.config.default_zone_id = DEFAULT_ZONE_ID;
    }
    if (!sys_config_apply_zone_profile(s_active_zone_id)) {
        RLOG_W(LOG_OBJECT_CODE_SYS_CFG,
               "Default zone %lu is invalid; falling back to Zone %d",
               (unsigned long)s_active_zone_id, (int)DEFAULT_ZONE_ID);
        s_active_zone_id = DEFAULT_ZONE_ID;
        g_storage.config.default_zone_id = DEFAULT_ZONE_ID;
        (void)sys_config_apply_zone_profile(DEFAULT_ZONE_ID);
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

otp_err_t sys_config_factory_otp_write(const protobuf_factory_otp_write_t *req)
{
    if (!req || req->confirm_magic != FACTORY_OTP_CONFIRM_MAGIC) {
        return OTP_ERR_INVALID_ARG;
    }

    otp_err_t err = OTP_ERR_INVALID_ARG;

    switch (req->otp_type) {
    case OTP_TYPE_DEVICE_INFO:
        if (req->device_type == DEVICE_TYPE_UNSPECIFIED ||
            !sys_config_device_type_valid((device_type_t)req->device_type)) {
            return OTP_ERR_INVALID_ARG;
        }
        if (req->value_u8 > UINT8_MAX) {
            return OTP_ERR_INVALID_ARG;
        }
        err = sys_config_otp_set_device_info((device_type_t)req->device_type,
                                             req->value_u32,
                                             (uint8_t)req->value_u8);
        break;

    case OTP_TYPE_ANTENNA_DELAY:
        if (req->tx_antenna_delay > UINT16_MAX || req->rx_antenna_delay > UINT16_MAX) {
            return OTP_ERR_INVALID_ARG;
        }
        err = sys_config_otp_set_antenna_delay((uint16_t)req->tx_antenna_delay,
                                               (uint16_t)req->rx_antenna_delay);
        break;

    default:
        return OTP_ERR_INVALID_ARG;
    }

    if (err != OTP_OK) {
        return err;
    }

    sys_config_reconcile_factory_otp();

    return OTP_OK;
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
    uint32_t timestamp = bsp_rtc_get_timestamp_s();
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

    static sys_config_storage_t temp_storage;
    bool                 normalize_and_save = false;
    uint32_t bytes_read = sys_flash_cfg_read(&temp_storage, sizeof(sys_config_storage_t));

    if (bytes_read != sizeof(sys_config_storage_t)) {
        RLOG_D(LOG_OBJECT_CODE_SYS_CFG,
               "No valid config in flash (bytes_read=%lu expected=%lu)",
               (unsigned long)bytes_read,
               (unsigned long)sizeof(sys_config_storage_t));
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
            RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Default config saved to flash (antenna delay preserved)");
        } else {
            RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_HAL, "Failed to save default config to flash");
        }
        return 0;
    }

    if (!sys_config_device_role_valid(temp_storage.config.uwb.role)) {
        RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_INVALID_PARAM, "Invalid role in flash");
        return -1;
    }

    if (!sys_config_device_type_valid(temp_storage.config.device_type)) {
        RLOG_W(LOG_OBJECT_CODE_SYS_CFG, "Invalid device_type in flash, forcing default");
        temp_storage.config.device_type = DEFAULT_DEVICE_TYPE;
        normalize_and_save = true;
    }

    if (!sys_config_host_transport_valid(temp_storage.config.host_transport)) {
        RLOG_W(LOG_OBJECT_CODE_SYS_CFG, "Invalid host_transport in flash, forcing USB");
        temp_storage.config.host_transport = HOST_TRANSPORT_USB;
    }

    if (temp_storage.config.host_transport == HOST_TRANSPORT_UNSPECIFIED) {
        temp_storage.config.host_transport = HOST_TRANSPORT_USB;
    }

    if (temp_storage.config.uwb.uwb_channel < 1 || temp_storage.config.uwb.uwb_channel > 7) {
        RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_INVALID_PARAM, "Invalid channel in flash");
        return -1;
    }

    if (temp_storage.config.uwb.rx_timeout_ms > 500 || temp_storage.config.uwb.rx_timeout_ms < 5) {
        RLOG_W(LOG_OBJECT_CODE_SYS_CFG, "Invalid rx_timeout=%lu in flash, forcing to %u ms",
               temp_storage.config.uwb.rx_timeout_ms, DEFAULT_RX_TIMEOUT_MS);
        temp_storage.config.uwb.rx_timeout_ms = DEFAULT_RX_TIMEOUT_MS;
        normalize_and_save = true;
    }

    if (temp_storage.config.uwb.ranging_period_ms > 5000 || temp_storage.config.uwb.ranging_period_ms < 50) {
        RLOG_W(LOG_OBJECT_CODE_SYS_CFG, "Invalid ranging_period=%lu in flash, forcing to %u ms",
               temp_storage.config.uwb.ranging_period_ms, DEFAULT_RANGING_PERIOD_MS);
        temp_storage.config.uwb.ranging_period_ms = DEFAULT_RANGING_PERIOD_MS;
    }

    if (temp_storage.config.uwb.uwb_preamble_len != 0x04 &&
        temp_storage.config.uwb.uwb_preamble_len != 0x14 &&
        temp_storage.config.uwb.uwb_preamble_len != 0x24 &&
        temp_storage.config.uwb.uwb_preamble_len != 0x34 &&
        temp_storage.config.uwb.uwb_preamble_len != 0x08 &&
        temp_storage.config.uwb.uwb_preamble_len != 0x18 &&
        temp_storage.config.uwb.uwb_preamble_len != 0x28 &&
        temp_storage.config.uwb.uwb_preamble_len != 0x0C) {
        RLOG_W(LOG_OBJECT_CODE_SYS_CFG, "Invalid preamble length in flash, forcing DWT_PLEN_512");
        temp_storage.config.uwb.uwb_preamble_len = DEFAULT_UWB_PREAMBLE_LEN;
        normalize_and_save = true;
    }

    if (temp_storage.config.uwb.uwb_rx_pac > 3) {
        RLOG_W(LOG_OBJECT_CODE_SYS_CFG, "Invalid rx PAC in flash, forcing DWT_PAC16");
        temp_storage.config.uwb.uwb_rx_pac = DEFAULT_UWB_RX_PAC;
        normalize_and_save = true;
    }

    if (temp_storage.config.uwb.uwb_ns_sfd > 1) {
        RLOG_W(LOG_OBJECT_CODE_SYS_CFG, "Invalid nsSFD in flash, forcing 1");
        temp_storage.config.uwb.uwb_ns_sfd = DEFAULT_UWB_NS_SFD;
        normalize_and_save = true;
    }

    if (temp_storage.config.uwb.uwb_phr_mode > 1) {
        RLOG_W(LOG_OBJECT_CODE_SYS_CFG, "Invalid PHR mode in flash, forcing DWT_PHRMODE_STD");
        temp_storage.config.uwb.uwb_phr_mode = DEFAULT_UWB_PHR_MODE;
        normalize_and_save = true;
    }

    if (temp_storage.config.uwb.pg_delay == 0) {
        RLOG_W(LOG_OBJECT_CODE_SYS_CFG, "Invalid PG delay in flash, forcing 0xC2");
        temp_storage.config.uwb.pg_delay = DEFAULT_PG_DELAY;
        normalize_and_save = true;
    }

    if (!sys_config_prefilter_valid(&temp_storage.config.prefilter)) {
        RLOG_W(LOG_OBJECT_CODE_SYS_CFG, "Invalid prefilter config in flash, forcing defaults");
        memset(&temp_storage.config.prefilter, 0, sizeof(temp_storage.config.prefilter));
        temp_storage.config.prefilter.enable = DEFAULT_PREFILTER_ENABLE;
        temp_storage.config.prefilter.recover_d2 = MAHALANOBIS_PREFILTER_D2_RECOVER;
        temp_storage.config.prefilter.reject_d2 = MAHALANOBIS_PREFILTER_D2_REJECT;
        temp_storage.config.prefilter.r_base = MAHALANOBIS_PREFILTER_R_BASE;
        temp_storage.config.prefilter.r_gate = MAHALANOBIS_PREFILTER_R_GATE;
        temp_storage.config.prefilter.velocity_weight = MAHALANOBIS_PREFILTER_VELOCITY_WEIGHT;
        temp_storage.config.prefilter.min_covariance = MAHALANOBIS_PREFILTER_MIN_COVARIANCE;
        normalize_and_save = true;
    }

    memcpy(&g_storage, &temp_storage, sizeof(sys_config_storage_t));
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Config loaded from flash (CRC: 0x%08X)", calc_crc);

    if (normalize_and_save) {
        if (sys_config_save() == 0) {
            RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Normalized config persisted back to flash");
        } else {
            RLOG_W(LOG_OBJECT_CODE_SYS_CFG, "Normalized config could not be persisted back to flash");
        }
    }

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
                RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Default config saved to RAM (antenna delay preserved)");
            } else {
                RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_HAL, "Failed to save default config to RAM");
            }
            return 0;
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


int sys_config_set_power_mode(anchor_power_mode_t mode)
{
    if (mode > ANCHOR_POWER_MODE_DEEP_ECO) {
        RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_INVALID_PARAM, "Invalid power mode: %d", mode);
        return -1;
    }
    
    g_storage.config.uwb.power_mode = (uint32_t)mode;
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Power mode set to: %d", mode);
    return 0;
}

void sys_config_reset_to_defaults(void)
{
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Resetting to factory defaults");

    memset(&g_storage, 0, sizeof(sys_config_storage_t));

    /* ================================================================================================
       CONFIGURATION PARAMETERS TABLE
       Configuration Parameter                              |           Value
       ================================================================================================
       Device Config
       ---------- */
    g_storage.config.config_version                         =           CONFIG_VERSION;
    g_storage.config.device_type                            =           DEFAULT_DEVICE_TYPE;
    g_storage.config.host_transport                         =           DEFAULT_HOST_TRANSPORT;
    
    /* UWB Base Configuration
       ---------- */
    g_storage.config.uwb.role                               =           sys_config_default_role_from_device_type(DEFAULT_DEVICE_TYPE);
    g_storage.config.uwb.device_id                          =           DEFAULT_DEVICE_ID;
    
    /* UWB Radio Parameters
       ---------- */
    g_storage.config.uwb.uwb_channel                        =           DEFAULT_UWB_CHANNEL;
    g_storage.config.uwb.uwb_prf                            =           DEFAULT_UWB_PRF;
    g_storage.config.uwb.uwb_data_rate                      =           DEFAULT_UWB_DATA_RATE;
    
    /* UWB Antenna Calibration
       ---------- */
    g_storage.config.uwb.tx_antenna_delay                   =           DEFAULT_TX_ANT_DLY;
    g_storage.config.uwb.rx_antenna_delay                   =           DEFAULT_RX_ANT_DLY;
    g_storage.config.uwb.tx_power                           =           DEFAULT_TX_POWER;
    
    /* UWB Timing Parameters
       ---------- */
    g_storage.config.uwb.ranging_period_ms                  =           DEFAULT_RANGING_PERIOD_MS;
    g_storage.config.uwb.rx_timeout_ms                      =           DEFAULT_RX_TIMEOUT_MS;
    g_storage.config.uwb.power_mode                        =            DEFAULT_ANCHOR_POWER_MODE;
    g_storage.config.uwb.anchor_list.size                   =           0;
    g_storage.config.uwb.uwb_preamble_len                   =           DEFAULT_UWB_PREAMBLE_LEN;
    g_storage.config.uwb.uwb_rx_pac                         =           DEFAULT_UWB_RX_PAC;
    g_storage.config.uwb.uwb_ns_sfd                         =           DEFAULT_UWB_NS_SFD;
    g_storage.config.uwb.uwb_phr_mode                       =           DEFAULT_UWB_PHR_MODE;
    g_storage.config.uwb.smart_tx_power                     =           DEFAULT_SMART_TX_POWER;
    g_storage.config.uwb.pg_delay                           =           DEFAULT_PG_DELAY;

    /* Positioning Prefilter Configuration
       ---------- */
    g_storage.config.prefilter.enable                       =           DEFAULT_PREFILTER_ENABLE;
    g_storage.config.prefilter.recover_d2                   =           MAHALANOBIS_PREFILTER_D2_RECOVER;
    g_storage.config.prefilter.reject_d2                    =           MAHALANOBIS_PREFILTER_D2_REJECT;
    g_storage.config.prefilter.r_base                       =           MAHALANOBIS_PREFILTER_R_BASE;
    g_storage.config.prefilter.r_gate                       =           MAHALANOBIS_PREFILTER_R_GATE;
    g_storage.config.prefilter.velocity_weight              =           MAHALANOBIS_PREFILTER_VELOCITY_WEIGHT;
    g_storage.config.prefilter.min_covariance               =           MAHALANOBIS_PREFILTER_MIN_COVARIANCE;
    
    /* Calibration Configuration
       ---------- */
    g_storage.config.calib.enable_anchor_auto_calib         =           0U;
    g_storage.config.calib.enable_tag_auto_calib            =           0U;

    /* Anchor Layout Positions (X, Y, Z in meters)
       ================================================================================================
       ID  |    X_M     |    Y_M     |    Z_M
       ================================================================================================ */
    /* Zone Profile configurations default init */
    g_storage.config.default_zone_id                        =           DEFAULT_ZONE_ID;

    g_storage.config.zone_profiles[0].zone_id               =           1;
    g_storage.config.zone_profiles[0].preamble_code         =           DEFAULT_ZONE_1_PREAMBLE_CODE;
    g_storage.config.zone_profiles[0].anchors_count         =           4;
    g_storage.config.zone_profiles[0].anchor_count          =           4;

    g_storage.config.zone_profiles[0].anchors[0].anchor_id  =           ZONE_1_ANCHOR_1_ID;
    g_storage.config.zone_profiles[0].anchors[0].x_m        =           ZONE_1_ANCHOR_1_X;
    g_storage.config.zone_profiles[0].anchors[0].y_m        =           ZONE_1_ANCHOR_1_Y;
    g_storage.config.zone_profiles[0].anchors[0].z_m        =           ZONE_1_ANCHOR_1_Z;

    g_storage.config.zone_profiles[0].anchors[1].anchor_id  =           ZONE_1_ANCHOR_2_ID;
    g_storage.config.zone_profiles[0].anchors[1].x_m        =           ZONE_1_ANCHOR_2_X;
    g_storage.config.zone_profiles[0].anchors[1].y_m        =           ZONE_1_ANCHOR_2_Y;
    g_storage.config.zone_profiles[0].anchors[1].z_m        =           ZONE_1_ANCHOR_2_Z;

    g_storage.config.zone_profiles[0].anchors[2].anchor_id  =           ZONE_1_ANCHOR_3_ID;
    g_storage.config.zone_profiles[0].anchors[2].x_m        =           ZONE_1_ANCHOR_3_X;
    g_storage.config.zone_profiles[0].anchors[2].y_m        =           ZONE_1_ANCHOR_3_Y;
    g_storage.config.zone_profiles[0].anchors[2].z_m        =           ZONE_1_ANCHOR_3_Z;

    g_storage.config.zone_profiles[0].anchors[3].anchor_id  =           ZONE_1_ANCHOR_4_ID;
    g_storage.config.zone_profiles[0].anchors[3].x_m        =           ZONE_1_ANCHOR_4_X;
    g_storage.config.zone_profiles[0].anchors[3].y_m        =           ZONE_1_ANCHOR_4_Y;
    g_storage.config.zone_profiles[0].anchors[3].z_m        =           ZONE_1_ANCHOR_4_Z;

    g_storage.config.zone_profiles[1].zone_id               =           2;
    g_storage.config.zone_profiles[1].preamble_code         =           DEFAULT_ZONE_2_PREAMBLE_CODE;
    g_storage.config.zone_profiles[1].anchors_count         =           4;
    g_storage.config.zone_profiles[1].anchor_count          =           4;

    g_storage.config.zone_profiles[1].anchors[0].anchor_id  =           ZONE_2_ANCHOR_1_ID;
    g_storage.config.zone_profiles[1].anchors[0].x_m        =           ZONE_2_ANCHOR_1_X;
    g_storage.config.zone_profiles[1].anchors[0].y_m        =           ZONE_2_ANCHOR_1_Y;
    g_storage.config.zone_profiles[1].anchors[0].z_m        =           ZONE_2_ANCHOR_1_Z;

    g_storage.config.zone_profiles[1].anchors[1].anchor_id  =           ZONE_2_ANCHOR_2_ID;
    g_storage.config.zone_profiles[1].anchors[1].x_m        =           ZONE_2_ANCHOR_2_X;
    g_storage.config.zone_profiles[1].anchors[1].y_m        =           ZONE_2_ANCHOR_2_Y;
    g_storage.config.zone_profiles[1].anchors[1].z_m        =           ZONE_2_ANCHOR_2_Z;

    g_storage.config.zone_profiles[1].anchors[2].anchor_id  =           ZONE_2_ANCHOR_3_ID;
    g_storage.config.zone_profiles[1].anchors[2].x_m        =           ZONE_2_ANCHOR_3_X;
    g_storage.config.zone_profiles[1].anchors[2].y_m        =           ZONE_2_ANCHOR_3_Y;
    g_storage.config.zone_profiles[1].anchors[2].z_m        =           ZONE_2_ANCHOR_3_Z;

    g_storage.config.zone_profiles[1].anchors[3].anchor_id  =           ZONE_2_ANCHOR_4_ID;
    g_storage.config.zone_profiles[1].anchors[3].x_m        =           ZONE_2_ANCHOR_4_X;
    g_storage.config.zone_profiles[1].anchors[3].y_m        =           ZONE_2_ANCHOR_4_Y;
    g_storage.config.zone_profiles[1].anchors[3].z_m        =           ZONE_2_ANCHOR_4_Z;

    g_storage.config.zone_profiles[2].zone_id               =           3;
    g_storage.config.zone_profiles[2].preamble_code         =           DEFAULT_ZONE_3_PREAMBLE_CODE;
    g_storage.config.zone_profiles[2].anchors_count         =           0;
    g_storage.config.zone_profiles[2].anchor_count          =           0;

    g_storage.config.zone_profiles[3].zone_id               =           4;
    g_storage.config.zone_profiles[3].preamble_code         =           DEFAULT_ZONE_4_PREAMBLE_CODE;
    g_storage.config.zone_profiles[3].anchors_count         =           0;
    g_storage.config.zone_profiles[3].anchor_count          =           0;

    g_storage.config.anchor_count                           =           4;
    g_storage.config.anchor_layout[0]                       =           g_storage.config.zone_profiles[DEFAULT_ZONE_ID - 1].anchors[0];
    g_storage.config.anchor_layout[1]                       =           g_storage.config.zone_profiles[DEFAULT_ZONE_ID - 1].anchors[1];
    g_storage.config.anchor_layout[2]                       =           g_storage.config.zone_profiles[DEFAULT_ZONE_ID - 1].anchors[2];
    g_storage.config.anchor_layout[3]                       =           g_storage.config.zone_profiles[DEFAULT_ZONE_ID - 1].anchors[3];
    /* ================================================================================================ */

}

static const char *sys_config_power_mode_name(uint32_t mode)
{
    switch (mode)
    {
    case ANCHOR_POWER_MODE_PERFORMANCE: return "PERFORMANCE";
    case ANCHOR_POWER_MODE_BALANCED:    return "BALANCED";
    case ANCHOR_POWER_MODE_ECO:         return "ECO";
    case ANCHOR_POWER_MODE_DEEP_ECO:    return "DEEP_ECO";
    default:                            return "UNKNOWN";
    }
}

void sys_config_print(void)
{
    /* Read OTP values to indicate sources */
    sys_config_otp_device_info_t otp_info = {0};
    bool has_otp_device = (sys_config_otp_get_device_info(&otp_info) == OTP_OK);

    uint16_t otp_tx_delay = 0;
    uint16_t otp_rx_delay = 0;
    bool has_otp_ant = (sys_config_otp_get_antenna_delay(&otp_tx_delay, &otp_rx_delay) == OTP_OK);

    uint32_t otp_mfg_date = has_otp_device ? sys_config_mfg_date_unpack(otp_info.mfg_date) : 0u;

    CFG_LOG("");
    CFG_LOG("=========== FIRMWARE VERSION ===========");
    CFG_LOG("FW Version    : %d.%d.%d.%d", FW_VERSION_MAJOR, FW_VERSION_MINOR, FW_VERSION_PATCH, FW_VERSION_BUILD);
    CFG_LOG("Git SHA       : %08lX", (unsigned long)FW_VERSION_GITSHA_HEX);
    CFG_LOG("Timestamp     : %lu", (unsigned long)FW_IMAGE_TIMESTAMP);
    CFG_LOG("========== DEVICE INFORMATION ==========");
    CFG_LOG("Config Ver    : %u", (unsigned)g_storage.config.config_version);
    CFG_LOG("Device Serial : 0x%08lX", (unsigned long)bsp_util_get_serial_number());
    CFG_LOG("Device Role   : %s (0x%02X)",
           g_storage.config.uwb.role == DEVICE_ROLE_TAG ? "TAG" : "ANCHOR",
           (unsigned)g_storage.config.uwb.role);
    if (has_otp_device) {
        CFG_LOG("Device Type   : %s (0x%02X, OTP Factory)",
                sys_config_device_type_name(g_storage.config.device_type),
                (unsigned)g_storage.config.device_type);
    } else {
        CFG_LOG("Device Type   : %s (0x%02X, Flash Default)",
                sys_config_device_type_name(g_storage.config.device_type),
                (unsigned)g_storage.config.device_type);
    }
    if (has_otp_device) {
        CFG_LOG("Mfg Date      : %02lu/%02lu/%04lu (OTP Factory)", 
                (unsigned long)(otp_mfg_date / 1000000), 
                (unsigned long)((otp_mfg_date / 10000) % 100), 
                (unsigned long)(otp_mfg_date % 10000));
    } else {
        CFG_LOG("Mfg Date      : Not set");
    }
    if (has_otp_device) {
        CFG_LOG("HW Revision   : %u (OTP Factory)", (unsigned)otp_info.hw_rev);
    } else {
        CFG_LOG("HW Revision   : Not set");
    }
    CFG_LOG("Host I/O      : %s",
           g_storage.config.host_transport == HOST_TRANSPORT_USB ? "USB" : "UART");
    CFG_LOG("Device ID     : 0x%02X", (unsigned)g_storage.config.uwb.device_id);
    CFG_LOG("-------------- UWB RADIO --------------");
    CFG_LOG("Channel       : %lu", g_storage.config.uwb.uwb_channel);
    CFG_LOG("PRF           : %lu MHz", g_storage.config.uwb.uwb_prf);
    CFG_LOG("Data Rate     : %lu", g_storage.config.uwb.uwb_data_rate);
    CFG_LOG("Preamble Code : %lu", g_storage.config.uwb.uwb_preamble_code);
    CFG_LOG("-------------- CALIBRATION ------------");
    if (has_otp_ant) {
        CFG_LOG("TX Ant Delay  : %lu (OTP Factory)", g_storage.config.uwb.tx_antenna_delay);
        CFG_LOG("RX Ant Delay  : %lu (OTP Factory)", g_storage.config.uwb.rx_antenna_delay);
    } else {
        CFG_LOG("TX Ant Delay  : %lu", g_storage.config.uwb.tx_antenna_delay);
        CFG_LOG("RX Ant Delay  : %lu", g_storage.config.uwb.rx_antenna_delay);
    }
    CFG_LOG("TX Power      : 0x%08lX", g_storage.config.uwb.tx_power);
    CFG_LOG("Calib MeanErr : %+.3fm", g_storage.config.calib.last_pair_error_mean_m);
    CFG_LOG("Calib Spread  : %.3fm", g_storage.config.calib.last_pair_error_spread_m);
    CFG_LOG("Calib RMS     : %.3fm", g_storage.config.calib.last_pair_error_rms_m);
    CFG_LOG("Calib AbsErr  : mean=%.3fm max=%.3fm",
            g_storage.config.calib.last_pair_error_mean_abs_m,
            g_storage.config.calib.last_pair_error_max_abs_m);
    CFG_LOG("Calib PairCnt : %lu usable / %lu rejected",
            (unsigned long)g_storage.config.calib.last_usable_pair_count,
            (unsigned long)g_storage.config.calib.last_rejected_pair_count);
    CFG_LOG("Calib Rejects : %lu", (unsigned long)g_storage.config.calib.rejected_batch_count);
    CFG_LOG("Calib Iter    : %u", (unsigned)g_storage.config.calib.iterations_taken);
    CFG_LOG("-------------- TIMING -----------------");
    CFG_LOG("Ranging Period: %lu ms", g_storage.config.uwb.ranging_period_ms);
    CFG_LOG("RX Timeout    : %lu ms", g_storage.config.uwb.rx_timeout_ms);
    CFG_LOG("Power Mode    : %s (%lu)", sys_config_power_mode_name(g_storage.config.uwb.power_mode), g_storage.config.uwb.power_mode);
    CFG_LOG("Prefilter     : %s recover=%.2f reject=%.2f r_base=%.3f r_gate=%.3f",
            g_storage.config.prefilter.enable ? "ON" : "OFF",
            g_storage.config.prefilter.recover_d2,
            g_storage.config.prefilter.reject_d2,
            g_storage.config.prefilter.r_base,
            g_storage.config.prefilter.r_gate);
    CFG_LOG("==========================================");
    CFG_LOG("");
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

const sys_calib_cfg_t *sys_config_get_calib(void)
{
    return &g_storage.config.calib;
}

int sys_config_set_calib(const sys_calib_cfg_t *calib)
{
    if (!calib) return -1;
    g_storage.config.calib = *calib;
    return 0;
}

const sys_prefilter_cfg_t *sys_config_get_prefilter(void)
{
    return &g_storage.config.prefilter;
}

int sys_config_set_prefilter(const sys_prefilter_cfg_t *prefilter)
{
    if (!sys_config_prefilter_valid(prefilter)) {
        RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_INVALID_PARAM, "Invalid prefilter config");
        return -1;
    }

    g_storage.config.prefilter = *prefilter;
    return 0;
}

void sys_config_get_anchor_layout(sys_anchor_layout_t *anchors, uint32_t *count)
{
    if (!anchors || !count) return;
    uint32_t n = g_storage.config.anchor_count;
    if (n > SYS_CONFIG_MAX_ANCHORS) n = SYS_CONFIG_MAX_ANCHORS;
    memcpy(anchors, g_storage.config.anchor_layout, (size_t)n * sizeof(sys_anchor_layout_t));
    *count = n;
}

int sys_config_set_anchor_layout(const sys_anchor_layout_t *anchors, uint32_t count)
{
    if (!anchors || count == 0U || count > SYS_CONFIG_MAX_ANCHORS) return -1;

    protobuf_zone_profile_t profile =
        g_storage.config.zone_profiles[s_active_zone_id - 1U];
    profile.zone_id = s_active_zone_id;
    profile.anchor_count = count;
    profile.anchors_count = count;
    memset(profile.anchors, 0, sizeof(profile.anchors));
    memcpy(profile.anchors, anchors, (size_t)count * sizeof(sys_anchor_layout_t));
    if (!sys_config_zone_profile_valid(&profile)) {
        return -1;
    }

    g_storage.config.zone_profiles[s_active_zone_id - 1U] = profile;
    g_storage.config.anchor_count = count;
    memset(g_storage.config.anchor_layout, 0, sizeof(g_storage.config.anchor_layout));
    memcpy(g_storage.config.anchor_layout, anchors, (size_t)count * sizeof(sys_anchor_layout_t));
    return 0;
}

uint8_t sys_config_get_hw_rev(void)
{
    sys_config_otp_device_info_t info;
    if (sys_config_otp_get_device_info(&info) == OTP_OK) {
        return info.hw_rev;
    }
    return 0u;
}

uint32_t sys_config_get_active_zone_id(void)
{
    return s_active_zone_id;
}

void sys_config_set_active_zone_id(uint32_t zone_id)
{
    if (zone_id >= 1 && zone_id <= 4) {
        s_active_zone_id = zone_id;
    }
}

bool sys_config_zone_profile_valid(const protobuf_zone_profile_t *profile)
{
    if (!profile || profile->zone_id < 1U || profile->zone_id > 4U) {
        return false;
    }

    bool preamble_valid = (g_storage.config.uwb.uwb_prf == 64U)
                          ? (profile->preamble_code >= 9U && profile->preamble_code <= 24U)
                          : (profile->preamble_code >= 1U && profile->preamble_code <= 8U);
    if (!preamble_valid ||
        profile->anchors_count != NUM_ANCHORS ||
        profile->anchor_count != profile->anchors_count) {
        return false;
    }

    uint32_t id_mask = 0U;
    for (uint32_t i = 0U; i < profile->anchors_count; i++) {
        const sys_anchor_layout_t *anchor = &profile->anchors[i];
        if (anchor->anchor_id == 0U || anchor->anchor_id > MAX_ANCHORS_SUPPORTED ||
            !isfinite(anchor->x_m) || !isfinite(anchor->y_m) || !isfinite(anchor->z_m)) {
            return false;
        }
        uint32_t bit = 1UL << (anchor->anchor_id - 1U);
        if ((id_mask & bit) != 0U) {
            return false;
        }
        id_mask |= bit;
    }
    return id_mask != 0U;
}

int sys_config_set_zone_profile(const protobuf_zone_profile_t *profile)
{
    if (!sys_config_zone_profile_valid(profile)) {
        return -1;
    }
    g_storage.config.zone_profiles[profile->zone_id - 1U] = *profile;
    return 0;
}

bool sys_config_apply_zone_profile(uint32_t zone_id)
{
    if (zone_id < 1U || zone_id > 4U) {
        return false;
    }

    const protobuf_zone_profile_t *profile = &g_storage.config.zone_profiles[zone_id - 1U];
    if (!sys_config_zone_profile_valid(profile)) {
        RLOG_W(LOG_OBJECT_CODE_SYS_CFG, "Zone %lu profile is invalid", (unsigned long)zone_id);
        return false;
    }

    g_storage.config.uwb.uwb_preamble_code = profile->preamble_code;
    g_storage.config.anchor_count = profile->anchors_count;
    memset(g_storage.config.anchor_layout, 0, sizeof(g_storage.config.anchor_layout));
    for (uint32_t i = 0U; i < profile->anchors_count; i++) {
        g_storage.config.anchor_layout[i] = profile->anchors[i];
    }

    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Applied Zone %lu profile: preamble=%lu anchors_count=%lu",
           (unsigned long)zone_id,
           (unsigned long)profile->preamble_code,
           (unsigned long)profile->anchors_count);
    return true;
}

/* End of file -------------------------------------------------------- */
