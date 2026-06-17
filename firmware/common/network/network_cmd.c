#include "network_cmd.h"

#include "config.h"
#include "memorylayout.h"

#ifdef BOOTLOADER
#include "bsp_util_bl.h"
#else
#include "bsp_util.h"
#endif

#ifndef BOOTLOADER
    #include "bsp_flash.h"
    #include "sys_config.h"
    #include "bsp_battery.h"
    #include "sys_logger.h"
    #include "sys_pm.h"
    #include "otp/otp.h"
#else
    #include "sys_logger_bl.h"
    #include "otp/otp.h"

#endif

#include <string.h>
// clang-format off
#define OBJECT_CODE                     LOG_OBJECT_CODE_NETWORK
#define RESP_RETRY_MAX                  2
#define RESP_RETRY_DELAY_MS             200
#define WAIT_TIME_TO_RESEND_ACK_MS      30000u
#define NETWORK_HOST_ACTIVITY_TIMEOUT_MS 30000u
#define SENSOR_FUSION_STREAM_PERIOD_MS  50u

typedef void (*cmd_handler_t)(const protobuf_packet_t *pkt);

typedef struct {
    uint32_t      cmd_id;
    cmd_handler_t cmd_hdl;
    const char   *name;
} network_cmd_entry_t;

#define CMD_INFO(_cmd_id, _cmd_hdl, _name) \
    [_cmd_id] = { .cmd_id = _cmd_id, .cmd_hdl = _cmd_hdl, .name = _name }
// clang-format on

/* ---- Forward declarations ---- */

static bool network_cmd_packet_handler(const protobuf_packet_t *pkt);
static void network_cmd_retry_pending(void);
static void network_cmd_send_packet(protobuf_packet_t *pkt);
static void network_cmd_unimplemented(const protobuf_packet_t *pkt);
static void network_cmd_none(const protobuf_packet_t *pkt);
static void network_cmd_ack(const protobuf_packet_t *pkt);
static void network_cmd_device_reset(const protobuf_packet_t *pkt);
#ifndef BOOTLOADER
static void network_cmd_enter_to_bootloader(const protobuf_packet_t *pkt);
#endif
static void network_cmd_log_data_get(const protobuf_packet_t *pkt);
static void network_cmd_log_clear(const protobuf_packet_t *pkt);
static void network_send_log(uint8_t dst, uint32_t data_length);
static void log_tracker_callback(network_ack_tracker_t *p_tracker, const protobuf_packet_t *packet);
static bool network_cmd_host_active(void);

#ifdef HAVE_BLE_PERIPHERAL
static void network_cmd_ble_status_resp(const protobuf_packet_t *pkt);
static void network_cmd_ble_adv_status(const protobuf_packet_t *pkt);
#endif

static void network_cmd_device_information_get(const protobuf_packet_t *pkt);
#ifndef BOOTLOADER
static void network_cmd_device_type_set(const protobuf_packet_t *pkt);
static void network_cmd_device_type_get(const protobuf_packet_t *pkt);
static void network_cmd_sys_config_get(const protobuf_packet_t *pkt);
static void network_cmd_sys_config_set(const protobuf_packet_t *pkt);
#endif
static void network_cmd_time_sync_get(const protobuf_packet_t *pkt);
static void network_cmd_time_sync_set(const protobuf_packet_t *pkt);
#ifndef BOOTLOADER
static void network_cmd_time_sync_adv_set(const protobuf_packet_t *pkt);
#endif

#ifndef BOOTLOADER

static void network_cmd_sys_ranging_cfg_get(const protobuf_packet_t *pkt);
static void network_cmd_sys_ranging_cfg_set(const protobuf_packet_t *pkt);
static void network_cmd_ranging_start(const protobuf_packet_t *pkt);
static void network_cmd_ranging_stop(const protobuf_packet_t *pkt);
static void network_cmd_host_transport_set(const protobuf_packet_t *pkt);
static void network_cmd_pos_calib_cfg_get(const protobuf_packet_t *pkt);
static void network_cmd_pos_calib_cfg_set(const protobuf_packet_t *pkt);
static void network_cmd_anchor_layout_get(const protobuf_packet_t *pkt);
static void network_cmd_anchor_layout_set(const protobuf_packet_t *pkt);
static void network_cmd_battery_info_get(const protobuf_packet_t *pkt);
static void network_cmd_factory_otp_write(const protobuf_packet_t *pkt);
#endif /* !BOOTLOADER */
static void network_cmd_end_session(const protobuf_packet_t *pkt);


/* ---- Internal State ---- */

typedef struct {
    network_core_t *stream;
    bool enabled;

    protobuf_packet_t last_resp;
    bool resp_pending;
    uint8_t resp_retry_left;
    uint32_t resp_deadline_ms;
} network_cmd_t;

static network_cmd_t s_network_cmd;


typedef struct {
    bool     waiting_ack;
    uint32_t log_len;
    int      tracker_id;
} network_log_tracker_t;

typedef struct {
    uint32_t ack_wait_set_on_log_send;
    uint32_t ack_wait_blocked_by_existing_wait;
    uint32_t ack_wait_cleared_by_tracker_callback;
    uint32_t ack_wait_callback_entered;
    uint32_t ack_wait_callback_found;
    uint32_t ack_wait_callback_not_found;
} network_cmd_log_debug_stats_t;

volatile network_cmd_log_debug_stats_t g_network_cmd_log_debug_stats;

static network_log_tracker_t s_log_tracker = {
    .waiting_ack = false,
    .log_len     = 0u,
    .tracker_id  = -1
};

static bool    s_log_stream_enabled = false;
static uint8_t s_log_stream_dst     = protobuf_PACKET_ADDR_HOST;
static uint32_t s_last_sensor_fusion_stream_tick = 0u;

/* ---- Command dispatch table ----
 * Sparse, indexed by protobuf tag via CMD_INFO.
 * Unsupported/passive packets -> network_cmd_unimplemented.
 * -------------------------------- */
static const network_cmd_entry_t network_cmd_table[] = {
    //      +=================================================+=======================================+========================+
    //      | cmd_id (proto tag)                              | Handler                               | cmd string             |
    //      +-------------------------------------------------+---------------------------------------+------------------------+
    CMD_INFO(protobuf_packet_t_none_tag,                      network_cmd_none,                        "none"),               /* 2  */
    CMD_INFO(protobuf_packet_t_ack_tag,                       network_cmd_ack,                         "ack"),                /* 3  */

    CMD_INFO(protobuf_packet_t_device_information_get_tag,    network_cmd_device_information_get,      "dev_info_get"),       /* 4  */
    CMD_INFO(protobuf_packet_t_device_information_resp_tag,   network_cmd_unimplemented,               "dev_info_resp"),      /* 5  */

    CMD_INFO(protobuf_packet_t_time_sync_get_tag,             network_cmd_time_sync_get,               "time_sync_get"),      /* 6  */
    CMD_INFO(protobuf_packet_t_time_sync_set_tag,             network_cmd_time_sync_set,               "time_sync_set"),      /* 7  */
    CMD_INFO(protobuf_packet_t_time_sync_resp_tag,            network_cmd_unimplemented,               "time_sync_resp"),     /* 8  */

#ifndef BOOTLOADER
    CMD_INFO(protobuf_packet_t_sys_config_get_tag,            network_cmd_sys_config_get,              "cfg_get"),            /* 10 */
    CMD_INFO(protobuf_packet_t_sys_config_set_tag,            network_cmd_sys_config_set,              "cfg_set"),            /* 11 */
    CMD_INFO(protobuf_packet_t_sys_config_resp_tag,           network_cmd_unimplemented,               "cfg_resp"),           /* 12 */
#endif /* !BOOTLOADER */

#ifndef BOOTLOADER
    CMD_INFO(protobuf_packet_t_time_sync_adv_set_tag,         network_cmd_time_sync_adv_set,           "time_sync_adv_set"),  /* 9  */
#else
    CMD_INFO(protobuf_packet_t_time_sync_adv_set_tag,         network_cmd_unimplemented,               "time_sync_adv_set"),  /* 9  */
#endif

#ifndef BOOTLOADER
    CMD_INFO(protobuf_packet_t_sys_ranging_cfg_get_tag,       network_cmd_sys_ranging_cfg_get,         "rng_cfg_get"),        /* 13 */
    CMD_INFO(protobuf_packet_t_sys_ranging_cfg_set_tag,       network_cmd_sys_ranging_cfg_set,         "rng_cfg_set"),        /* 14 */
    CMD_INFO(protobuf_packet_t_sys_ranging_cfg_resp_tag,      network_cmd_unimplemented,               "rng_cfg_resp"),       /* 15 */
    CMD_INFO(protobuf_packet_t_ranging_start_tag,             network_cmd_ranging_start,               "rng_start"),          /* 16 */
    CMD_INFO(protobuf_packet_t_ranging_stop_tag,              network_cmd_ranging_stop,                "rng_stop"),           /* 17 */
    /* TODO: Need to implement */
    CMD_INFO(protobuf_packet_t_ranging_result_tag,            network_cmd_unimplemented,               "rng_result"),         /* 18 */
    CMD_INFO(protobuf_packet_t_ranging_status_get_tag,        network_cmd_unimplemented,               "rng_status_get"),     /* 19 */
    CMD_INFO(protobuf_packet_t_ranging_status_resp_tag,       network_cmd_unimplemented,               "rng_status_resp"),    /* 20 */

    CMD_INFO(protobuf_packet_t_sensor_fusion_cfg_get_tag,     network_cmd_unimplemented,               "fusion_cfg_get"),     /* 21 */
    CMD_INFO(protobuf_packet_t_sensor_fusion_cfg_set_tag,     network_cmd_unimplemented,               "fusion_cfg_set"),     /* 22 */
    CMD_INFO(protobuf_packet_t_sensor_fusion_cfg_resp_tag,    network_cmd_unimplemented,               "fusion_cfg_resp"),    /* 23 */
#endif /* !BOOTLOADER */

    CMD_INFO(protobuf_packet_t_device_reset_tag,              network_cmd_device_reset,                "dev_reset"),          /* 24 */
#ifndef BOOTLOADER
    CMD_INFO(protobuf_packet_t_enter_to_bootloader_tag,      network_cmd_enter_to_bootloader,         "enter_bootloader"),   /* 62 */
#else
    CMD_INFO(protobuf_packet_t_enter_to_bootloader_tag,      network_cmd_unimplemented,               "enter_bootloader"),   /* 62 */
#endif
    CMD_INFO(protobuf_packet_t_uwb_reset_tag,                 network_cmd_unimplemented,               "uwb_reset"),          /* 25 */
    CMD_INFO(protobuf_packet_t_factory_config_reset_tag,      network_cmd_unimplemented,               "factory_reset"),      /* 26 */

#ifndef BOOTLOADER
    CMD_INFO(protobuf_packet_t_device_type_set_tag,           network_cmd_device_type_set,             "dev_type_set"),       /* 27 */
    CMD_INFO(protobuf_packet_t_device_type_get_tag,           network_cmd_device_type_get,             "dev_type_get"),       /* 28 */
#endif

    /* Flash commands — available in both app and bootloader */
    CMD_INFO(protobuf_packet_t_flash_erase_tag,               network_cmd_unimplemented,               "flash_erase"),        /* 29 */
    CMD_INFO(protobuf_packet_t_flash_read_tag,                network_cmd_unimplemented,               "flash_read"),         /* 30 */
    CMD_INFO(protobuf_packet_t_flash_data_tag,                network_cmd_unimplemented,               "flash_data"),         /* 31 */
    CMD_INFO(protobuf_packet_t_flash_write_tag,               network_cmd_unimplemented,               "flash_write"),        /* 32 */

#ifdef HAVE_BLE_PERIPHERAL
    CMD_INFO(protobuf_packet_t_ble_adv_config_set_tag,        network_cmd_unimplemented,               "ble_adv_cfg_set"),    /* 33 */
    CMD_INFO(protobuf_packet_t_ble_status_get_tag,            network_cmd_unimplemented,              "ble_status_get"),     /* 34 */
    CMD_INFO(protobuf_packet_t_ble_status_resp_tag,           network_cmd_ble_status_resp,             "ble_status_resp"),    /* 35 */
    CMD_INFO(protobuf_packet_t_ble_adv_status_tag,            network_cmd_ble_adv_status,              "ble_adv_status"),     /* 36 */
#endif

    CMD_INFO(protobuf_packet_t_log_data_tag,                  network_cmd_log_data_get,                "log_data"),           /* 37 */
    CMD_INFO(protobuf_packet_t_log_clear_tag,                 network_cmd_log_clear,                   "log_clear"),          /* 38 */

#ifndef BOOTLOADER
    CMD_INFO(protobuf_packet_t_host_transport_set_tag,        network_cmd_host_transport_set,          "host_transport_set"), /* 39 */
#else
    CMD_INFO(protobuf_packet_t_host_transport_set_tag,        network_cmd_unimplemented,               "host_transport_set"), /* 39 */
#endif

#ifndef BOOTLOADER
    CMD_INFO(protobuf_packet_t_pos_calib_cfg_get_tag,         network_cmd_pos_calib_cfg_get,           "calib_cfg_get"),      /* 40 */
    CMD_INFO(protobuf_packet_t_pos_calib_cfg_set_tag,         network_cmd_pos_calib_cfg_set,           "calib_cfg_set"),      /* 41 */
    CMD_INFO(protobuf_packet_t_pos_calib_cfg_resp_tag,        network_cmd_unimplemented,               "calib_cfg_resp"),     /* 42 */

    CMD_INFO(protobuf_packet_t_anchor_layout_get_tag,         network_cmd_anchor_layout_get,           "anchor_layout_get"),  /* 43 */
    CMD_INFO(protobuf_packet_t_anchor_layout_set_tag,         network_cmd_anchor_layout_set,           "anchor_layout_set"),  /* 44 */
    CMD_INFO(protobuf_packet_t_anchor_layout_resp_tag,        network_cmd_unimplemented,               "anchor_layout_resp"), /* 45 */
    CMD_INFO(protobuf_packet_t_flash_verify_tag,              network_cmd_unimplemented,               "flash_verify"),       /* 46 */

    /* BLE Central messages */
    CMD_INFO(protobuf_packet_t_ble_conn_params_get_tag,       network_cmd_unimplemented,               "ble_conn_get"),       /* 47 */
    CMD_INFO(protobuf_packet_t_ble_conn_params_set_tag,       network_cmd_unimplemented,               "ble_conn_set"),       /* 48 */
    CMD_INFO(protobuf_packet_t_ble_conn_params_resp_tag,      network_cmd_unimplemented,               "ble_conn_resp"),      /* 49 */
    CMD_INFO(protobuf_packet_t_ble_disconnect_tag,            network_cmd_unimplemented,               "ble_disc"),           /* 50 */
    CMD_INFO(protobuf_packet_t_ble_scan_start_tag,            network_cmd_unimplemented,               "ble_scan_start"),     /* 51 */
    CMD_INFO(protobuf_packet_t_ble_scan_stop_tag,             network_cmd_unimplemented,               "ble_scan_stop"),      /* 52 */
    CMD_INFO(protobuf_packet_t_ble_connect_tag,               network_cmd_unimplemented,               "ble_connect"),        /* 53 */
    CMD_INFO(protobuf_packet_t_ble_scan_result_tag,           network_cmd_unimplemented,               "ble_scan_result"),    /* 54 */

    /* Battery */
    CMD_INFO(protobuf_packet_t_battery_info_resp_tag,         network_cmd_unimplemented,               "battery_info_resp"),  /* 60 */
    CMD_INFO(protobuf_packet_t_battery_info_get_tag,          network_cmd_battery_info_get,            "battery_info_get"),   /* 61 */
#endif /* !BOOTLOADER */
    CMD_INFO(protobuf_packet_t_calib_status_get_tag,          network_cmd_unimplemented,               "calib_status_get"),   /* 63 */
    CMD_INFO(protobuf_packet_t_calib_status_resp_tag,         network_cmd_unimplemented,               "calib_status_resp"),  /* 64 */
    CMD_INFO(protobuf_packet_t_end_session_tag,               network_cmd_end_session,                 "end_session"),        /* 65 */
#ifndef BOOTLOADER
    CMD_INFO(protobuf_packet_t_factory_otp_write_tag,         network_cmd_factory_otp_write,           "factory_otp_write"),  /* 66 */
#endif
    //      +=================================================+=======================================+========================+
};

#define NETWORK_CMD_TABLE_SIZE (sizeof(network_cmd_table) / sizeof(network_cmd_entry_t))

/**
 * Look up a handler entry by packet tag.
 * Returns NULL if the tag is out of range or has no handler registered.
 */
static const network_cmd_entry_t *network_cmd_lookup(uint32_t which_params)
{
    if ((size_t)which_params >= NETWORK_CMD_TABLE_SIZE) {
        return NULL;
    }

    const network_cmd_entry_t *entry = &network_cmd_table[which_params];
    return (entry->cmd_hdl != NULL) ? entry : NULL;
}

/**
 * Build a zeroed response packet addressed back to the request sender.
 */
static protobuf_packet_t network_cmd_make_resp(const protobuf_packet_t *req,
                                               uint32_t which_params)
{
    protobuf_packet_t resp;
    memset(&resp, 0, sizeof(resp));
    resp.which_params  = which_params;
    resp.hdr.addr.dst  = req->hdr.addr.src;
    return resp;
}

#ifndef BOOTLOADER
/**
 * Persist config to flash and warn on failure.
 */
static void network_cmd_config_save(const char *context)
{
    if (sys_config_save() != 0) {
        RLOG_W(OBJECT_CODE, "Failed to persist %s from host", context);
    }
}
#endif /* !BOOTLOADER */

static void network_cmd_retry_pending(void)
{
    CHECK_VOID(s_network_cmd.resp_pending);

    uint32_t now = bsp_util_get_ticks();
    CHECK_VOID((int32_t)(now - s_network_cmd.resp_deadline_ms) >= 0);

    if (s_network_cmd.resp_retry_left == 0) {
        s_network_cmd.resp_pending = false;
        return;
    }

    protobuf_packet_t pkt = s_network_cmd.last_resp;
    network_cmd_send_packet(&pkt);

    s_network_cmd.resp_retry_left--;
    s_network_cmd.resp_deadline_ms = now + RESP_RETRY_DELAY_MS;
}

static void network_cmd_send_packet(protobuf_packet_t *pkt)
{
    CHECK_VOID(s_network_cmd.stream && pkt);
    network_core_send_packet(s_network_cmd.stream, pkt->hdr.addr.dst, pkt);
}

static void network_cmd_unimplemented(const protobuf_packet_t *pkt)
{
    CHECK_VOID(s_network_cmd.stream && pkt);
    network_core_send_ack(s_network_cmd.stream, pkt, protobuf_PACKET_ACK_RESPONSE_NACK_UNIMPLEMENTED);
    RLOG_W(OBJECT_CODE, "No command handler for payload tag=%u", (unsigned)pkt->which_params);
}

static void network_cmd_none(const protobuf_packet_t *pkt)
{
    (void)pkt;
}

static void network_cmd_ack(const protobuf_packet_t *pkt)
{
    (void)pkt;
}

/* ─────────────────────────────────────────────
 * App-only command handlers
 * ───────────────────────────────────────────── */

#ifndef BOOTLOADER

static void network_cmd_device_information_get(const protobuf_packet_t *pkt)
{
    CHECK_VOID(pkt && s_network_cmd.stream);

    if (pkt->hdr.addr.src == protobuf_PACKET_ADDR_DEBUG) {
        s_network_cmd.stream->serial_connection_active = true;
    } else if (pkt->hdr.addr.src == protobuf_PACKET_ADDR_HOST) {
        s_network_cmd.stream->ble_connection_active = true;
    }

    protobuf_packet_t resp = network_cmd_make_resp(pkt, protobuf_packet_t_device_information_resp_tag);

    resp.params.device_information_resp.serial_number = bsp_util_get_serial_number();

    const sys_config_t *cfg = sys_config_get();
    if (cfg != NULL) {
        resp.params.device_information_resp.device_type = cfg->device_type;
        resp.params.device_information_resp.role        = cfg->uwb.role;
    }

    uint32_t hw_version = 0;
    uint8_t otp_buf[5];
    uint8_t otp_len = 0;
    if (otp_get(OTP_TYPE_DEVICE_INFO, otp_buf, sizeof(otp_buf), &otp_len) == OTP_OK && otp_len == 5) {
        hw_version = otp_buf[4];
    }
    resp.params.device_information_resp.hw_version = hw_version;

    bsp_app_image_header_t app_hdr;
    memset(&app_hdr, 0, sizeof(app_hdr));
    if (bsp_flash_read_app_header(&app_hdr, sizeof(app_hdr))) {
        resp.params.device_information_resp.fw_version.major  = app_hdr.fw_major;
        resp.params.device_information_resp.fw_version.minor  = app_hdr.fw_minor;
        resp.params.device_information_resp.fw_version.patch  = app_hdr.fw_patch;
        resp.params.device_information_resp.fw_version.build  = app_hdr.fw_build;
        resp.params.device_information_resp.fw_version.gitsha = app_hdr.fw_gitsha;
    }

    network_cmd_send_packet(&resp);
}

#else

static void network_cmd_device_information_get(const protobuf_packet_t *pkt)
{
    CHECK_VOID(pkt && s_network_cmd.stream);

    if (pkt->hdr.addr.src == protobuf_PACKET_ADDR_DEBUG) {
        s_network_cmd.stream->serial_connection_active = true;
    } else if (pkt->hdr.addr.src == protobuf_PACKET_ADDR_HOST) {
        s_network_cmd.stream->ble_connection_active = true;
    }

    protobuf_packet_t resp = network_cmd_make_resp(pkt, protobuf_packet_t_device_information_resp_tag);

    uint8_t otp_info[5] = {0};
    uint8_t otp_len = 0u;
    uint32_t hw_version = 0u;
    protobuf_device_type_t device_type = protobuf_DEVICE_TYPE_UNSPECIFIED;
    if (otp_get(OTP_TYPE_DEVICE_INFO, otp_info, sizeof(otp_info), &otp_len) == OTP_OK &&
        otp_len == sizeof(otp_info)) {
        protobuf_device_type_t otp_device_type = (protobuf_device_type_t)otp_info[0];
        if (otp_device_type == protobuf_DEVICE_TYPE_TAG ||
            otp_device_type == protobuf_DEVICE_TYPE_ANCHOR ||
            otp_device_type == protobuf_DEVICE_TYPE_GATEWAY ||
            otp_device_type == protobuf_DEVICE_TYPE_DEBUG_TOOL) {
            device_type = otp_device_type;
            hw_version = otp_info[4];
        }
    }

    /* Bootloader responds with minimal identity fields only. */
    resp.params.device_information_resp.device_type    = device_type;
    resp.params.device_information_resp.role           = protobuf_DEVICE_ROLE_UNSPECIFIED;
    if (resp.params.device_information_resp.device_type == protobuf_DEVICE_TYPE_TAG) {
        resp.params.device_information_resp.role = protobuf_DEVICE_ROLE_TAG;
    } else if (resp.params.device_information_resp.device_type == protobuf_DEVICE_TYPE_ANCHOR) {
        resp.params.device_information_resp.role = protobuf_DEVICE_ROLE_ANCHOR;
    }
    resp.params.device_information_resp.has_fw_version = true;
    resp.params.device_information_resp.fw_version     = (protobuf_version_t){0};
    /* Mark as bootloader firmware in fw_version metadata. */
    resp.params.device_information_resp.fw_version.gitsha = 0x424F4F54ULL; /* 'BOOT' */
    resp.params.device_information_resp.hw_version     = hw_version;
    resp.params.device_information_resp.serial_number = bsp_util_get_serial_number();

    network_cmd_send_packet(&resp);
}

#endif /* !BOOTLOADER */

#ifndef BOOTLOADER

static void network_cmd_device_type_set(const protobuf_packet_t *pkt)
{
    CHECK_VOID(pkt);
    (void)sys_config_set_device_type(pkt->params.device_type_set.device_type);
    network_cmd_config_save("device_type");
}

static void network_cmd_device_type_get(const protobuf_packet_t *pkt)
{
    CHECK_VOID(pkt && s_network_cmd.stream);

    protobuf_packet_t resp = network_cmd_make_resp(pkt, protobuf_packet_t_device_type_set_tag);
    resp.params.device_type_set.device_type = sys_config_get_device_type();

    network_cmd_send_packet(&resp);
}

static void network_cmd_sys_config_get(const protobuf_packet_t *pkt)
{
    CHECK_VOID(pkt && s_network_cmd.stream);

    const sys_config_t *cfg = sys_config_get();
    protobuf_packet_t resp  = network_cmd_make_resp(pkt, protobuf_packet_t_sys_config_resp_tag);
    resp.params.sys_config_resp.has_config = true;
    resp.params.sys_config_resp.config     = cfg->uwb;

    network_cmd_send_packet(&resp);
}

static void network_cmd_sys_config_set(const protobuf_packet_t *pkt)
{
    CHECK_VOID(pkt);

    if (!pkt->params.sys_config_set.has_config) {
        return;
    }

    const protobuf_uwb_cfg_t *new_cfg = &pkt->params.sys_config_set.config;

    if (new_cfg->role != DEVICE_ROLE_TAG && new_cfg->role != DEVICE_ROLE_ANCHOR) {
        RLOG_W(OBJECT_CODE, "Invalid role in sys_config_set: %u", (unsigned)new_cfg->role);
        return;
    }

    if (new_cfg->uwb_channel < 1u || new_cfg->uwb_channel > 7u) {
        RLOG_W(OBJECT_CODE, "Invalid UWB channel in sys_config_set: %u", (unsigned)new_cfg->uwb_channel);
        return;
    }

    if (new_cfg->uwb_preamble_len != 0x04 &&
        new_cfg->uwb_preamble_len != 0x14 &&
        new_cfg->uwb_preamble_len != 0x24 &&
        new_cfg->uwb_preamble_len != 0x34 &&
        new_cfg->uwb_preamble_len != 0x08 &&
        new_cfg->uwb_preamble_len != 0x18 &&
        new_cfg->uwb_preamble_len != 0x28 &&
        new_cfg->uwb_preamble_len != 0x0C) {
        RLOG_W(OBJECT_CODE, "Invalid UWB preamble length in sys_config_set: 0x%02X", (unsigned)new_cfg->uwb_preamble_len);
        return;
    }

    if (new_cfg->uwb_rx_pac > 3u) {
        RLOG_W(OBJECT_CODE, "Invalid UWB rx PAC in sys_config_set: %u", (unsigned)new_cfg->uwb_rx_pac);
        return;
    }

    if (new_cfg->uwb_ns_sfd > 1u) {
        RLOG_W(OBJECT_CODE, "Invalid UWB nsSFD in sys_config_set: %u", (unsigned)new_cfg->uwb_ns_sfd);
        return;
    }

    if (new_cfg->uwb_phr_mode > 1u) {
        RLOG_W(OBJECT_CODE, "Invalid UWB PHR mode in sys_config_set: %u", (unsigned)new_cfg->uwb_phr_mode);
        return;
    }

    if (new_cfg->pg_delay == 0u) {
        RLOG_W(OBJECT_CODE, "Invalid UWB PG delay in sys_config_set: %u", (unsigned)new_cfg->pg_delay);
        return;
    }

    sys_config_t *cfg = sys_config_get();
    cfg->uwb = *new_cfg;

    network_cmd_config_save("sys_config");
}

static void network_cmd_sys_ranging_cfg_get(const protobuf_packet_t *pkt)
{
    CHECK_VOID(pkt && s_network_cmd.stream);

    const sys_config_t *cfg = sys_config_get();
    protobuf_packet_t resp  = network_cmd_make_resp(pkt, protobuf_packet_t_sys_ranging_cfg_resp_tag);
    resp.params.sys_ranging_cfg_resp.has_config                     = true;
    resp.params.sys_ranging_cfg_resp.config.rx_timeout_ms           = cfg->uwb.rx_timeout_ms;
    resp.params.sys_ranging_cfg_resp.config.ranging_period_ms       = cfg->uwb.ranging_period_ms;

    network_cmd_send_packet(&resp);
}

static void network_cmd_sys_ranging_cfg_set(const protobuf_packet_t *pkt)
{
    CHECK_VOID(pkt);

    if (!pkt->params.sys_ranging_cfg_set.has_config) {
        return;
    }

    sys_config_t *cfg           = sys_config_get();
    cfg->uwb.rx_timeout_ms      = pkt->params.sys_ranging_cfg_set.config.rx_timeout_ms;
    cfg->uwb.ranging_period_ms  = pkt->params.sys_ranging_cfg_set.config.ranging_period_ms;

    network_cmd_config_save("ranging_cfg");
}

static void network_cmd_ranging_start(const protobuf_packet_t *pkt)
{
    (void)pkt;
    if (!network_cmd_set_ranging_enabled(true)) {
        RLOG_W(OBJECT_CODE, "ranging_start rejected by platform");
    }
}

static void network_cmd_ranging_stop(const protobuf_packet_t *pkt)
{
    (void)pkt;
    if (!network_cmd_set_ranging_enabled(false)) {
        RLOG_W(OBJECT_CODE, "ranging_stop rejected by platform");
    }
}

#endif /* !BOOTLOADER */

static void network_cmd_time_sync_get(const protobuf_packet_t *pkt)
{
    CHECK_VOID(pkt && s_network_cmd.stream);

    uint64_t unix_time_ms   = 0;
    int32_t  timezone_offset = 0;

#ifdef HAVE_RTC
    if (bsp_rtc_sync_get(&unix_time_ms, &timezone_offset) != BSP_UTIL_OK) {
        RLOG_W(OBJECT_CODE, "RTC sync get failed, using fallback tick");
        unix_time_ms     = (uint64_t)bsp_util_get_ticks();
        timezone_offset  = 0;
    }
#else
    unix_time_ms    = (uint64_t)bsp_util_get_ticks();
    timezone_offset = 0;
#endif

    protobuf_packet_t resp = network_cmd_make_resp(pkt, protobuf_packet_t_time_sync_resp_tag);
    resp.params.time_sync_resp.unix_time_ms     = unix_time_ms;
    resp.params.time_sync_resp.timezone_offset  = timezone_offset;

    network_cmd_send_packet(&resp);
}

static void network_cmd_time_sync_set(const protobuf_packet_t *pkt)
{
    CHECK_VOID(pkt);

    if (bsp_rtc_sync_set(pkt->params.time_sync_set.unix_time_ms,
                         pkt->params.time_sync_set.timezone_offset) != BSP_UTIL_OK) {
        RLOG_W(OBJECT_CODE, "RTC sync set failed");
        return;
    }

    bsp_rtc_time_t rtc_time;
    bsp_rtc_get_time(&rtc_time);
    RLOG_I(OBJECT_CODE,
           "RTC synced: datetime: %02u-%02u-%04u %02u:%02u:%02u, timezone offset: %ld s",
           (unsigned)rtc_time.day,
           (unsigned)rtc_time.month,
           (unsigned)(2000u + rtc_time.year),
           (unsigned)rtc_time.hour,
           (unsigned)rtc_time.minute,
           (unsigned)rtc_time.second,
           (long)pkt->params.time_sync_set.timezone_offset);
}

#ifndef BOOTLOADER
static void network_cmd_time_sync_adv_set(const protobuf_packet_t *pkt)
{
    CHECK_VOID(pkt);

    const sys_config_t *cfg = sys_config_get();
    if (cfg == NULL) {
        return;
    }

    const protobuf_time_sync_adv_set_t *adv_set = &pkt->params.time_sync_adv_set;

    // Check device type and device id
    if (adv_set->device_type == cfg->device_type && adv_set->device_id == cfg->uwb.device_id) {
        if (bsp_rtc_sync_set(adv_set->unix_time_ms,
                             adv_set->timezone_offset) != BSP_UTIL_OK) {
            RLOG_W(OBJECT_CODE, "RTC sync set failed from time_sync_adv_set");
            return;
        }

        bsp_rtc_time_t rtc_time;
        bsp_rtc_get_time(&rtc_time);
        RLOG_I(OBJECT_CODE,
               "RTC synced (adv_set): datetime: %02u-%02u-%04u %02u:%02u:%02u, timezone offset: %ld s",
               (unsigned)rtc_time.day,
               (unsigned)rtc_time.month,
               (unsigned)(2000u + rtc_time.year),
               (unsigned)rtc_time.hour,
               (unsigned)rtc_time.minute,
               (unsigned)rtc_time.second,
               (long)adv_set->timezone_offset);
    }
}
#endif


#ifndef BOOTLOADER


static void network_cmd_host_transport_set(const protobuf_packet_t *pkt)
{
    CHECK_VOID(pkt);

    const sys_config_t *cfg       = sys_config_get();
    host_transport_t old_transport = cfg ? cfg->host_transport : HOST_TRANSPORT_UNSPECIFIED;

    if (sys_config_set_host_transport(pkt->params.host_transport_set.transport) != 0) {
        RLOG_W(OBJECT_CODE, "Invalid host transport value: %u",
               (unsigned)pkt->params.host_transport_set.transport);
        return;
    }

    cfg = sys_config_get();
    if (cfg && cfg->host_transport == old_transport) {
        return; /* unchanged — no need to write flash */
    }

    network_cmd_config_save("host_transport");
}

static void network_cmd_pos_calib_cfg_get(const protobuf_packet_t *pkt)
{
    CHECK_VOID(pkt && s_network_cmd.stream);

    const sys_calib_cfg_t *calib_cfg = sys_config_get_calib();
    if (!calib_cfg) {
        RLOG_E(OBJECT_CODE, ERR_UWB_CALIBRATION, "Failed to get calibration config");
        return;
    }

    protobuf_packet_t resp = network_cmd_make_resp(pkt, protobuf_packet_t_pos_calib_cfg_resp_tag);
    resp.hdr                              = pkt->hdr;
    resp.params.pos_calib_cfg_resp.config = *calib_cfg;

    network_cmd_send_packet(&resp);
}

static void network_cmd_pos_calib_cfg_set(const protobuf_packet_t *pkt)
{
    CHECK_VOID(pkt);

    if (sys_config_set_calib(&pkt->params.pos_calib_cfg_set.config) != 0) {
        RLOG_W(OBJECT_CODE, "Invalid calibration config received from host");
        return;
    }

    network_cmd_config_save("calibration config");
}

static void network_cmd_anchor_layout_get(const protobuf_packet_t *pkt)
{
    CHECK_VOID(pkt && s_network_cmd.stream);

    sys_anchor_layout_t anchors[SYS_CONFIG_MAX_ANCHORS];
    uint32_t count = 0;
    sys_config_get_anchor_layout(anchors, &count);

    if (count == 0 || count > SYS_CONFIG_MAX_ANCHORS) {
        RLOG_W(OBJECT_CODE, "Invalid anchor layout count: %lu", count);
        count = 0;
    }

    protobuf_packet_t resp = network_cmd_make_resp(pkt, protobuf_packet_t_anchor_layout_resp_tag);
    resp.hdr                                         = pkt->hdr;
    resp.params.anchor_layout_resp.anchors_count     = count;
    memcpy(resp.params.anchor_layout_resp.anchors, anchors,
           (size_t)count * sizeof(sys_anchor_layout_t));

    network_cmd_send_packet(&resp);
}

static void network_cmd_anchor_layout_set(const protobuf_packet_t *pkt)
{
    CHECK_VOID(pkt);

    uint32_t count = pkt->params.anchor_layout_set.anchors_count;
    if (count == 0 || count > SYS_CONFIG_MAX_ANCHORS) {
        RLOG_W(OBJECT_CODE, "Invalid anchor layout count: %lu", count);
        return;
    }

    if (sys_config_set_anchor_layout(pkt->params.anchor_layout_set.anchors, count) != 0) {
        RLOG_W(OBJECT_CODE, "Invalid anchor layout received from host");
        return;
    }

    network_cmd_config_save("anchor layout");
}

static void network_cmd_factory_otp_write(const protobuf_packet_t *pkt)
{
    CHECK_VOID(pkt && s_network_cmd.stream);

    const protobuf_factory_otp_write_t *req = &pkt->params.factory_otp_write;
    otp_err_t err = sys_config_factory_otp_write(req);

    if (err == OTP_OK) {
        RLOG_W(OBJECT_CODE, "Factory OTP write accepted type=0x%02lX", (unsigned long)req->otp_type);
        network_core_send_ack(s_network_cmd.stream, pkt, protobuf_PACKET_ACK_RESPONSE_ACK);
    } else {
        RLOG_W(OBJECT_CODE, "Factory OTP write rejected type=0x%02lX status=%d",
               (unsigned long)req->otp_type, (int)err);
        network_core_send_ack(s_network_cmd.stream, pkt,
                              err == OTP_ERR_INVALID_ARG ?
                              protobuf_PACKET_ACK_RESPONSE_NACK_INVALID_TYPE :
                              protobuf_PACKET_ACK_RESPONSE_NACK_CMD_FAILED);
    }
}

static void network_cmd_battery_info_get(const protobuf_packet_t *pkt)
{
    CHECK_VOID(pkt && s_network_cmd.stream);

    sys_pm_status_t pm_status;
    sys_pm_get_status(&pm_status);

    protobuf_packet_t resp = network_cmd_make_resp(pkt, protobuf_packet_t_battery_info_resp_tag);
    resp.params.battery_info_resp.bat_voltage_mv   = (uint32_t)pm_status.bat_voltage_mv;
    resp.params.battery_info_resp.bat_soc_percent  = (uint32_t)pm_status.soc;
    resp.params.battery_info_resp.remaining_min    = pm_status.remaining_min;
    resp.params.battery_info_resp.is_charging      = pm_status.is_charging;
    
    // Hardware telemetry fields
    resp.params.battery_info_resp.mcu_temp_c       = pm_status.temp_degc;
    resp.params.battery_info_resp.mcu_voltage_mv   = (uint32_t)pm_status.vdda_mv;
    resp.params.battery_info_resp.uwb_temp_c       = pm_status.uwb_temp_c;
    resp.params.battery_info_resp.uwb_voltage_mv   = (uint32_t)pm_status.uwb_vbat_mv;
    resp.params.battery_info_resp.imu_temp_c       = pm_status.imu_temp_c;
    
    // Alert flags
    resp.params.battery_info_resp.error_mask       = pm_status.error_mask;

    network_cmd_send_packet(&resp);
}

#endif /* !BOOTLOADER */

/* ─────────────────────────────────────────────
 * Shared command handlers (app + bootloader)
 * ───────────────────────────────────────────── */

#ifdef HAVE_BLE_PERIPHERAL
#include "ble/sys_ble_peripheral.h"

static void network_cmd_ble_status_resp(const protobuf_packet_t *pkt)
{
    sys_ble_peripheral_on_status_resp(pkt);
}

static void network_cmd_ble_adv_status(const protobuf_packet_t *pkt)
{
    /* Log received telemetry from other nodes for debug */
    RLOG_I(OBJECT_CODE, "Received BLE adv status from 0x%02X", (unsigned)pkt->hdr.addr.src);
}
#endif

static void network_cmd_device_reset(const protobuf_packet_t *pkt)
{
    (void)pkt;
    bsp_util_device_reset();
}

#ifndef BOOTLOADER
static void network_cmd_enter_to_bootloader(const protobuf_packet_t *pkt)
{
    CHECK_VOID(pkt);

    if (pkt->params.enter_to_bootloader.magic != BL_MAGIC_VALUE) {
        RLOG_W(OBJECT_CODE,
               "Invalid enter_to_bootloader magic: 0x%08lX",
               (unsigned long)pkt->params.enter_to_bootloader.magic);
        return;
    }

    RLOG_I(OBJECT_CODE, "Entering bootloader...");

    /* Send ACK blocks/inline so it reaches the host before we reboot */
    network_core_send_ack(s_network_cmd.stream, pkt, protobuf_PACKET_ACK_RESPONSE_ACK);

    /* Wait for transmission to finish (USB endpoint flush / UART complete) */
    bsp_delay_ms(100);

    if (bsp_util_enter_bootloader() != BSP_UTIL_OK) {
        RLOG_E(OBJECT_CODE, ERR_WRITE, "Failed to enter bootloader");
    }
}
#endif

static void network_cmd_log_data_get(const protobuf_packet_t *pkt)
{
    CHECK_VOID(pkt && s_network_cmd.stream);

    if (pkt->hdr.addr.src == protobuf_PACKET_ADDR_DEBUG) {
        s_network_cmd.stream->serial_connection_active = true;
    } else if (pkt->hdr.addr.src == protobuf_PACKET_ADDR_HOST) {
        s_network_cmd.stream->ble_connection_active = true;
    }

    s_log_stream_enabled = true;
    s_log_stream_dst     = (uint8_t)pkt->hdr.addr.src;

    protobuf_packet_t sample;
    uint16_t max_payload = (uint16_t)sizeof(sample.params.log_data.data.bytes);
    network_send_log(s_log_stream_dst, max_payload);
}

static void network_cmd_log_clear(const protobuf_packet_t *pkt)
{
    CHECK_VOID(pkt);

#if defined(HAVE_FLASH_STORAGE) && defined(ENABLE_FLASH_LOG)
    uint32_t length = pkt->params.log_clear.length;
    if (length > 0u) {
        sys_logger_flash_consume(length);
    }
#else
    uint32_t length = pkt->params.log_clear.length;
    if (length > 0u) {
        sys_logger_ram_consume((uint16_t)length);
    } else {
        sys_logger_clear();
    }
#endif
}

static void network_send_log(uint8_t dst, uint32_t data_length)
{
    CHECK_VOID(s_network_cmd.stream);

#if defined(HAVE_FLASH_STORAGE) && defined(ENABLE_FLASH_LOG)
    if (s_log_tracker.waiting_ack) {
        g_network_cmd_log_debug_stats.ack_wait_blocked_by_existing_wait++;
        return;
    }

    if (sys_logger_flash_pending_bytes() == 0u) {
        return;
    }

    protobuf_packet_t packet;
    memset(&packet, 0, sizeof(packet));
    packet.which_params              = protobuf_packet_t_log_data_tag;
    packet.params.log_data.type      = protobuf_log_type_t_LOG_TYPE_DEVICE_LOG;

    uint16_t max_payload = (uint16_t)sizeof(packet.params.log_data.data.bytes);
    uint16_t send_len    = (data_length > max_payload) ? max_payload : (uint16_t)data_length;
    uint32_t read_len    = sys_logger_flash_peek_packet(packet.params.log_data.data.bytes, send_len);
    if (read_len == 0u) {
        return;
    }

    packet.params.log_data.data.size = (pb_size_t)read_len;
    if (!network_core_send_packet(s_network_cmd.stream, dst, &packet)) {
        return;
    }

    s_log_tracker.waiting_ack  = true;
    g_network_cmd_log_debug_stats.ack_wait_set_on_log_send++;
    s_log_tracker.log_len      = read_len;
    s_log_tracker.tracker_id   = network_core_wait_ack(s_network_cmd.stream,
                                                        packet.hdr.seq,
                                                        WAIT_TIME_TO_RESEND_ACK_MS,
                                                        log_tracker_callback,
                                                        &s_log_tracker);
    if (s_log_tracker.tracker_id < 0) {
        s_log_tracker.waiting_ack = false;
        s_log_tracker.log_len     = 0u;
    }
#else
    /* No flash storage: logger returns framed entries from RAM buffer.
     * ACK tracking mirrors the flash path - consume only after host ACKs. */
    if (s_log_tracker.waiting_ack) {
        g_network_cmd_log_debug_stats.ack_wait_blocked_by_existing_wait++;
        return;
    }

    if (sys_logger_data_count() == 0u) {
        return;
    }

    protobuf_packet_t packet;
    memset(&packet, 0, sizeof(packet));
    packet.which_params              = protobuf_packet_t_log_data_tag;
    packet.params.log_data.type      = protobuf_log_type_t_LOG_TYPE_DEVICE_LOG;

    uint16_t max_payload = (uint16_t)sizeof(packet.params.log_data.data.bytes);
    uint16_t send_len    = (data_length > max_payload) ? max_payload : (uint16_t)data_length;
    uint16_t read_len    = sys_logger_ram_peek_packet(packet.params.log_data.data.bytes, send_len);
    if (read_len == 0u) {
        return;
    }

    packet.params.log_data.data.size = (pb_size_t)read_len;
    if (!network_core_send_packet(s_network_cmd.stream, dst, &packet)) {
        return;
    }

    s_log_tracker.waiting_ack  = true;
    g_network_cmd_log_debug_stats.ack_wait_set_on_log_send++;
    s_log_tracker.log_len      = read_len;
    s_log_tracker.tracker_id   = network_core_wait_ack(s_network_cmd.stream,
                                                        packet.hdr.seq,
                                                        WAIT_TIME_TO_RESEND_ACK_MS,
                                                        log_tracker_callback,
                                                        &s_log_tracker);
    if (s_log_tracker.tracker_id < 0) {
        s_log_tracker.waiting_ack = false;
        s_log_tracker.log_len     = 0u;
    }
#endif
}

static void network_cmd_end_session(const protobuf_packet_t *pkt)
{
    CHECK_VOID(pkt && s_network_cmd.stream);
    
    protobuf_session_end_reason_t reason = pkt->params.end_session.reason;

    RLOG_I(OBJECT_CODE, "Received end_session from 0x%02X, reason: %d",
           (unsigned)pkt->hdr.addr.src, (int)reason);

    switch (reason) {
        case protobuf_SESSION_END_REASON_LOG_DATA:
            s_log_stream_enabled = false;
            RLOG_I(OBJECT_CODE, "Log streaming stopped");
            /* Also reset connection flag for LOG_DATA as it is usually the primary session */
            if(pkt->hdr.addr.src == protobuf_PACKET_ADDR_DEBUG) {
                s_network_cmd.stream->serial_connection_active = false;
            }
            break;

        case protobuf_SESSION_END_REASON_RANGING_RESULTS:
            /* Just stop streaming but keep connection if needed, though usually we end all */
            RLOG_I(OBJECT_CODE, "	Ranging streaming stopped");
            break;

        case protobuf_SESSION_END_REASON_DEBUG_STREAMING:
            s_log_stream_enabled = false;
            RLOG_I(OBJECT_CODE, "Debug streaming stopped");
            break;

        default:
            if (pkt->hdr.addr.src == protobuf_PACKET_ADDR_DEBUG) {
                s_network_cmd.stream->serial_connection_active = false;
            } else if (pkt->hdr.addr.src == protobuf_PACKET_ADDR_HOST) {
                s_network_cmd.stream->ble_connection_active = false;
            }
            s_log_stream_enabled = false;
            break;
    }
}

static void log_tracker_callback(network_ack_tracker_t *p_tracker, const protobuf_packet_t *packet)
{
    (void)packet;
    g_network_cmd_log_debug_stats.ack_wait_callback_entered++;

#if defined(HAVE_FLASH_STORAGE) && defined(ENABLE_FLASH_LOG)
    CHECK_VOID(p_tracker != NULL);

    network_log_tracker_t *tracker = (network_log_tracker_t *)p_tracker->callback_arg;
    CHECK_VOID(tracker != NULL);

    if ((p_tracker->state == NETWORK_CORE_ACK_STATE_FOUND) && (tracker->log_len > 0u)) {
        sys_logger_flash_consume(tracker->log_len);
        tracker->waiting_ack = false;
        tracker->log_len     = 0u;
        tracker->tracker_id  = -1;
        g_network_cmd_log_debug_stats.ack_wait_callback_found++;
        g_network_cmd_log_debug_stats.ack_wait_cleared_by_tracker_callback++;
        return;
    }

    g_network_cmd_log_debug_stats.ack_wait_callback_not_found++;
    tracker->waiting_ack = false;
    tracker->log_len     = 0u;
    tracker->tracker_id  = -1;
#else
    /* No flash: consume from RAM buffer when host ACKs. */
    CHECK_VOID(p_tracker != NULL);

    network_log_tracker_t *tracker = (network_log_tracker_t *)p_tracker->callback_arg;
    CHECK_VOID(tracker != NULL);

    if ((p_tracker->state == NETWORK_CORE_ACK_STATE_FOUND) && (tracker->log_len > 0u)) {
        sys_logger_ram_consume((uint16_t)tracker->log_len);
        tracker->waiting_ack = false;
        tracker->log_len     = 0u;
        tracker->tracker_id  = -1;
        g_network_cmd_log_debug_stats.ack_wait_callback_found++;
        g_network_cmd_log_debug_stats.ack_wait_cleared_by_tracker_callback++;
        return;
    }

    g_network_cmd_log_debug_stats.ack_wait_callback_not_found++;
    tracker->waiting_ack = false;
    tracker->log_len     = 0u;
    tracker->tracker_id  = -1;
#endif
}

static bool network_cmd_host_active(void)
{
    CHECK(s_network_cmd.stream, false);

    if (s_network_cmd.stream->serial_connection_active) {
        return true;
    }

    uint32_t last_tick = s_network_cmd.stream->latest_packet_tick;
    if (last_tick == 0u) {
        return false;
    }

    return (uint32_t)(bsp_util_get_ticks() - last_tick) <= NETWORK_HOST_ACTIVITY_TIMEOUT_MS;
}

/* ---- Public API ---- */

bool network_cmd_init(network_core_t *stream)
{
    CHECK(stream, false);

    memset(&s_network_cmd, 0, sizeof(network_cmd_t));
    s_network_cmd.stream  = stream;
    s_network_cmd.enabled = true;


    return network_core_register_packet_handler(stream, network_cmd_packet_handler);
}

bool network_cmd_is_ble_host_active(void)
{
    CHECK(s_network_cmd.stream, false);
    return s_network_cmd.stream->ble_connection_active;
}

bool network_cmd_set_ranging_enabled(bool enabled)
{
    g_ranging_enabled = enabled;
    return false;
}

bool network_cmd_is_ranging_enabled(void)
{
    return g_ranging_enabled;
}

void network_cmd_process(void)
{
    CHECK_VOID(s_network_cmd.enabled);

    network_cmd_retry_pending();

    if (s_log_stream_enabled && network_cmd_host_active()) {
        network_send_log(s_log_stream_dst, 0xFFFFu);
    }
}

bool network_cmd_process_packet(const protobuf_packet_t *pkt)
{
    CHECK(pkt && s_network_cmd.enabled, false);

    /* ACKs are handled transparently by network_core — nothing to do here */
    if (pkt->which_params == protobuf_packet_t_ack_tag) {
        return true;
    }

    const network_cmd_entry_t *entry = network_cmd_lookup(pkt->which_params);
    if (entry == NULL) {
        network_cmd_unimplemented(pkt);
        return false;
    }

    return true;
}

void network_cmd_dispatch(const protobuf_packet_t *pkt)
{
    CHECK_VOID(pkt && s_network_cmd.enabled);

    /* ACKs are handled transparently by network_core */
    if (pkt->which_params == protobuf_packet_t_ack_tag) {
        return;
    }

    const network_cmd_entry_t *entry = network_cmd_lookup(pkt->which_params);
    if (entry == NULL || entry->cmd_hdl == network_cmd_unimplemented) {
        network_cmd_unimplemented(pkt);
        return;
    }

    entry->cmd_hdl(pkt);

    network_core_send_ack(s_network_cmd.stream, pkt, protobuf_PACKET_ACK_RESPONSE_ACK);
}

static bool network_cmd_packet_handler(const protobuf_packet_t *pkt)
{
    network_cmd_dispatch(pkt);
    return true;
}

/* ---- Active Command Senders ----
 * These functions wrap packet construction and transmission for outgoing commands
 * to specific destinations (dst).
 * -------------------------------- */

bool network_send_sensor_fusion_result(network_core_t *stream, uint8_t dst, const protobuf_sensor_fusion_result_t *data)
{
    CHECK(stream && data, false);
    CHECK(network_cmd_is_ranging_enabled(), false);
//    CHECK(network_cmd_is_ble_host_active(), false);

    uint32_t now = bsp_util_get_ticks();
    CHECK((uint32_t)(now - s_last_sensor_fusion_stream_tick) >= SENSOR_FUSION_STREAM_PERIOD_MS, false);

    protobuf_packet_t pkt;
    memset(&pkt, 0, sizeof(pkt));
    pkt.which_params = protobuf_packet_t_sensor_fusion_result_tag;
    pkt.params.sensor_fusion_result = *data;

    if (network_core_send_packet(stream, dst, &pkt)) {
        s_last_sensor_fusion_stream_tick = now;
        return true;
    }

    return false;
}

#ifdef HAVE_BLE_PERIPHERAL

/**
 * Send advertising configuration to a specific BLE peripheral.
 */
bool network_send_ble_adv_config_set(network_core_t *stream, uint8_t dst, bool enable, uint32_t serial_number, const char *device_name)
{
    CHECK(stream, false);
    
    protobuf_packet_t pkt;
    memset(&pkt, 0, sizeof(pkt));
    pkt.which_params = protobuf_packet_t_ble_adv_config_set_tag;
    pkt.params.ble_adv_config_set.enable = enable;
    pkt.params.ble_adv_config_set.serial_number = serial_number;
    
    if (device_name) {
        strncpy(pkt.params.ble_adv_config_set.device_name, device_name,
                sizeof(pkt.params.ble_adv_config_set.device_name) - 1);
    }

    return network_core_send_packet(stream, dst, &pkt);
}

/**
 * Poll a specific BLE peripheral for its current status.
 */
bool network_send_ble_status_get(network_core_t *stream, uint8_t dst)
{
    CHECK(stream, false);
    
    protobuf_packet_t pkt;
    memset(&pkt, 0, sizeof(pkt));
    pkt.which_params = protobuf_packet_t_ble_status_get_tag;

    return network_core_send_packet(stream, dst, &pkt);
}

/**
 * Broadcast local advertising status telemetry.
 */
bool network_send_ble_adv_status(network_core_t *stream, uint8_t dst, const protobuf_ble_adv_status_t *status)
{
    CHECK(stream && status, false);
    
    protobuf_packet_t pkt;
    memset(&pkt, 0, sizeof(pkt));
    pkt.which_params = protobuf_packet_t_ble_adv_status_tag;
    pkt.params.ble_adv_status = *status;

    return network_core_send_packet(stream, dst, &pkt);
}

#endif /* HAVE_BLE_PERIPHERAL */
