#include "network_cmd.h"
#include "sys_logger.h"
#include "config.h"
#include "stm32f4xx_hal.h"
#include "bsp_flash.h"
#include "bsp_util.h"
#include "sys_config.h"

#include <string.h>

#define OBJECT_CODE LOG_OBJECT_CODE_NETWORK
#define RESP_RETRY_MAX 2
#define RESP_RETRY_DELAY_MS 200
#define WAIT_TIME_TO_RESEND_ACK_MS 30000u
#define NETWORK_HOST_ACTIVITY_TIMEOUT_MS 30000u

#define CHECK(_cond, _ret) do { if (!(_cond)) return (_ret); } while (0)
#define CHECK_VOID(_cond) do { if (!(_cond)) return; } while (0)

typedef void (*cmd_handler_t)(network_cmd_t *cmd, const protobuf_packet_t *pkt);

typedef struct {
    uint32_t cmd_id;
    cmd_handler_t cmd_hdl;
    const char *name;
} network_cmd_entry_t;

#define CMD_INFO(_cmd_id, _cmd_hdl, _name) \
    [_cmd_id] = { .cmd_id = _cmd_id, .cmd_hdl = _cmd_hdl, .name = _name }

static network_cmd_t *g_network_cmd_instance = NULL;

static bool network_cmd_packet_handler(const protobuf_packet_t *pkt);
static void network_cmd_retry_pending(network_cmd_t *cmd);
static void network_cmd_send_packet(network_cmd_t *cmd, protobuf_packet_t *pkt);
static void network_cmd_unimplemented(network_cmd_t *cmd, const protobuf_packet_t *pkt);
static void network_cmd_none(network_cmd_t *cmd, const protobuf_packet_t *pkt);
static void network_cmd_ack(network_cmd_t *cmd, const protobuf_packet_t *pkt);
static void network_cmd_device_information_get(network_cmd_t *cmd, const protobuf_packet_t *pkt);
static void network_cmd_device_type_set(network_cmd_t *cmd, const protobuf_packet_t *pkt);
static void network_cmd_device_type_get(network_cmd_t *cmd, const protobuf_packet_t *pkt);
static void network_cmd_sys_config_get(network_cmd_t *cmd, const protobuf_packet_t *pkt);
static void network_cmd_sys_config_set(network_cmd_t *cmd, const protobuf_packet_t *pkt);
static void network_cmd_time_sync_get(network_cmd_t *cmd, const protobuf_packet_t *pkt);
static void network_cmd_time_sync_set(network_cmd_t *cmd, const protobuf_packet_t *pkt);
static void network_cmd_ble_status_get(network_cmd_t *cmd, const protobuf_packet_t *pkt);
static void network_cmd_sys_ranging_cfg_get(network_cmd_t *cmd, const protobuf_packet_t *pkt);
static void network_cmd_sys_ranging_cfg_set(network_cmd_t *cmd, const protobuf_packet_t *pkt);
static void network_cmd_device_reset(network_cmd_t *cmd, const protobuf_packet_t *pkt);
static void network_cmd_log_data_get(network_cmd_t *cmd, const protobuf_packet_t *pkt);
static void network_cmd_log_clear(network_cmd_t *cmd, const protobuf_packet_t *pkt);
static void network_cmd_host_transport_set(network_cmd_t *cmd, const protobuf_packet_t *pkt);
static void network_cmd_pos_calib_cfg_get(network_cmd_t *cmd, const protobuf_packet_t *pkt);
static void network_cmd_pos_calib_cfg_set(network_cmd_t *cmd, const protobuf_packet_t *pkt);
static void network_cmd_anchor_layout_get(network_cmd_t *cmd, const protobuf_packet_t *pkt);
static void network_cmd_anchor_layout_set(network_cmd_t *cmd, const protobuf_packet_t *pkt);
static void network_send_log(network_cmd_t *cmd, uint8_t dst, uint32_t data_length);
static void log_tracker_callback(network_ack_tracker_t *p_tracker, const protobuf_packet_t *packet);
static bool network_cmd_host_active(const network_cmd_t *cmd);

typedef struct {
    bool waiting_ack;
    uint32_t log_len;
    int tracker_id;
} network_log_tracker_t;

static network_log_tracker_t s_log_tracker = {
    .waiting_ack = false,
    .log_len = 0u,
    .tracker_id = -1
};

static bool s_log_stream_enabled = false;
static uint8_t s_log_stream_dst = protobuf_PACKET_ADDR_HOST;

/*
 * Command lookup table.
 * - Entries are sparse and indexed by protobuf tag using CMD_INFO.
 * - Tags that no longer exist in the proto are removed from this table.
 * - Unsupported/passive packets route to network_cmd_unimplemented.
 */
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

    CMD_INFO(protobuf_packet_t_sys_config_get_tag,            network_cmd_sys_config_get,              "cfg_get"),            /* 9  */
    CMD_INFO(protobuf_packet_t_sys_config_set_tag,            network_cmd_sys_config_set,              "cfg_set"),            /* 10 */
    CMD_INFO(protobuf_packet_t_sys_config_resp_tag,           network_cmd_unimplemented,               "cfg_resp"),           /* 11 */

    CMD_INFO(protobuf_packet_t_sys_ranging_cfg_get_tag,       network_cmd_sys_ranging_cfg_get,         "rng_cfg_get"),        /* 12 */
    CMD_INFO(protobuf_packet_t_sys_ranging_cfg_set_tag,       network_cmd_sys_ranging_cfg_set,         "rng_cfg_set"),        /* 13 */
    CMD_INFO(protobuf_packet_t_sys_ranging_cfg_resp_tag,      network_cmd_unimplemented,               "rng_cfg_resp"),       /* 14 */
    CMD_INFO(protobuf_packet_t_ranging_start_tag,             network_cmd_unimplemented,               "rng_start"),          /* 15 */
    CMD_INFO(protobuf_packet_t_ranging_stop_tag,              network_cmd_unimplemented,               "rng_stop"),           /* 16 */
    CMD_INFO(protobuf_packet_t_ranging_result_tag,            network_cmd_unimplemented,               "rng_result"),         /* 17 */
    CMD_INFO(protobuf_packet_t_ranging_status_get_tag,        network_cmd_unimplemented,               "rng_status_get"),     /* 18 */
    CMD_INFO(protobuf_packet_t_ranging_status_resp_tag,       network_cmd_unimplemented,               "rng_status_resp"),    /* 19 */

    CMD_INFO(protobuf_packet_t_filter_cfg_get_tag,            network_cmd_unimplemented,               "flt_cfg_get"),        /* 20 */
    CMD_INFO(protobuf_packet_t_filter_cfg_set_tag,            network_cmd_unimplemented,               "flt_cfg_set"),        /* 21 */
    CMD_INFO(protobuf_packet_t_filter_cfg_resp_tag,           network_cmd_unimplemented,               "flt_cfg_resp"),       /* 22 */
      
    CMD_INFO(protobuf_packet_t_device_reset_tag,              network_cmd_device_reset,                "dev_reset"),          /* 23 */
    CMD_INFO(protobuf_packet_t_uwb_reset_tag,                 network_cmd_unimplemented,               "uwb_reset"),          /* 24 */
    CMD_INFO(protobuf_packet_t_factory_config_reset_tag,      network_cmd_unimplemented,               "factory_reset"),      /* 25 */
    CMD_INFO(protobuf_packet_t_device_type_set_tag,           network_cmd_device_type_set,             "dev_type_set"),       /* 26 */
    CMD_INFO(protobuf_packet_t_device_type_get_tag,           network_cmd_device_type_get,             "dev_type_get"),       /* 27 */
#ifdef BOOTLOADER
    CMD_INFO(protobuf_packet_t_flash_erase_tag,               network_cmd_unimplemented,               "flash_erase"),        /* 28 */
    CMD_INFO(protobuf_packet_t_flash_read_tag,                network_cmd_unimplemented,               "flash_read"),         /* 29 */
    CMD_INFO(protobuf_packet_t_flash_data_tag,                network_cmd_unimplemented,               "flash_data"),         /* 30 */
    CMD_INFO(protobuf_packet_t_flash_write_tag,               network_cmd_unimplemented,               "flash_write"),        /* 31 */
#else
    CMD_INFO(protobuf_packet_t_flash_erase_tag,               network_cmd_unimplemented,               "flash_erase"),        /* 28 */
    CMD_INFO(protobuf_packet_t_flash_read_tag,                network_cmd_unimplemented,               "flash_read"),         /* 29 */
    CMD_INFO(protobuf_packet_t_flash_data_tag,                network_cmd_unimplemented,               "flash_data"),         /* 30 */
    CMD_INFO(protobuf_packet_t_flash_write_tag,               network_cmd_unimplemented,               "flash_write"),        /* 31 */  
#endif
#ifdef HAVE_BLE_PERIPHERAL
    CMD_INFO(protobuf_packet_t_ble_enable_tag,                network_cmd_unimplemented,               "ble_enable"),         /* 32 */
    CMD_INFO(protobuf_packet_t_ble_status_get_tag,            network_cmd_ble_status_get,              "ble_status_get"),     /* 33 */
    CMD_INFO(protobuf_packet_t_ble_status_resp_tag,           network_cmd_unimplemented,               "ble_status_resp"),    /* 34 */

    CMD_INFO(protobuf_packet_t_ble_adv_status_tag,            network_cmd_unimplemented,               "ble_adv_status"),     /* 35 */
#endif
    CMD_INFO(protobuf_packet_t_log_data_tag,                  network_cmd_log_data_get,                "log_data"),           /* 36 */
    CMD_INFO(protobuf_packet_t_log_clear_tag,                 network_cmd_log_clear,                   "log_clear"),          /* 37 */
    CMD_INFO(protobuf_packet_t_host_transport_set_tag,        network_cmd_host_transport_set,          "host_transport_set"), /* 38 */

    CMD_INFO(protobuf_packet_t_pos_calib_cfg_get_tag,         network_cmd_pos_calib_cfg_get,           "calib_cfg_get"),      /* 39 */
    CMD_INFO(protobuf_packet_t_pos_calib_cfg_set_tag,         network_cmd_pos_calib_cfg_set,           "calib_cfg_set"),      /* 40 */
    CMD_INFO(protobuf_packet_t_pos_calib_cfg_resp_tag,        network_cmd_unimplemented,               "calib_cfg_resp"),     /* 41 */

    CMD_INFO(protobuf_packet_t_anchor_layout_get_tag,         network_cmd_anchor_layout_get,           "anchor_layout_get"),  /* 42 */
    CMD_INFO(protobuf_packet_t_anchor_layout_set_tag,         network_cmd_anchor_layout_set,           "anchor_layout_set"),  /* 43 */
    CMD_INFO(protobuf_packet_t_anchor_layout_resp_tag,        network_cmd_unimplemented,               "anchor_layout_resp"), /* 44 */
    //      +=================================================+=======================================+========================+
};

#define NETWORK_CMD_TABLE_SIZE (sizeof(network_cmd_table) / sizeof(network_cmd_entry_t))

static void network_cmd_retry_pending(network_cmd_t *cmd)
{
    CHECK_VOID(cmd && cmd->resp_pending);

    uint32_t now = HAL_GetTick();
    CHECK_VOID((int32_t)(now - cmd->resp_deadline_ms) >= 0);

    if (cmd->resp_retry_left == 0) {
        cmd->resp_pending = false;
        return;
    }

    protobuf_packet_t pkt = cmd->last_resp;
    network_cmd_send_packet(cmd, &pkt);

    cmd->resp_retry_left--;
    cmd->resp_deadline_ms = now + RESP_RETRY_DELAY_MS;
}

static void network_cmd_send_packet(network_cmd_t *cmd, protobuf_packet_t *pkt)
{
    CHECK_VOID(cmd && cmd->stream && pkt);
    network_core_send_packet(cmd->stream, pkt->hdr.addr.dst, pkt);
}

static void network_cmd_unimplemented(network_cmd_t *cmd, const protobuf_packet_t *pkt)
{
    CHECK_VOID(cmd && cmd->stream && pkt);

    network_core_send_ack(cmd->stream,
                                pkt,
                                protobuf_PACKET_ACK_RESPONSE_NACK_UNIMPLEMENTED);

    RLOG_W(OBJECT_CODE, "No command handler for payload tag=%u", (unsigned)pkt->which_params);
}

static void network_cmd_none(network_cmd_t *cmd, const protobuf_packet_t *pkt)
{
    (void)cmd;
    (void)pkt;
}

static void network_cmd_ack(network_cmd_t *cmd, const protobuf_packet_t *pkt)
{
    (void)cmd;
    (void)pkt;
}

static void network_cmd_device_information_get(network_cmd_t *cmd, const protobuf_packet_t *pkt)
{
    protobuf_packet_t      resp;
    bsp_app_image_header_t app_hdr;

    CHECK_VOID(cmd && pkt && cmd->stream);

    memset(&resp,    0, sizeof(resp));
    memset(&app_hdr, 0, sizeof(app_hdr));

    resp.which_params = protobuf_packet_t_device_information_resp_tag;

    resp.params.device_information_resp.serial_number = bsp_util_get_serial_number();

    {
        const sys_config_t *cfg = sys_config_get();
        if (cfg != NULL) {
            resp.params.device_information_resp.device_type = cfg->device_type;
            resp.params.device_information_resp.role = cfg->uwb.role;
        }
    }

    if (bsp_flash_read_app_header(&app_hdr, sizeof(app_hdr))) {
        resp.params.device_information_resp.fw_version.major  = app_hdr.fw_major;
        resp.params.device_information_resp.fw_version.minor  = app_hdr.fw_minor;
        resp.params.device_information_resp.fw_version.patch  = app_hdr.fw_patch;
        resp.params.device_information_resp.fw_version.build  = app_hdr.fw_build;
        resp.params.device_information_resp.fw_version.gitsha = app_hdr.fw_gitsha;
    }

    resp.hdr.addr.dst = pkt->hdr.addr.src;
    network_cmd_send_packet(cmd, &resp);
}

static void network_cmd_device_type_set(network_cmd_t *cmd, const protobuf_packet_t *pkt)
{
    CHECK_VOID(cmd && pkt);
    (void)sys_config_set_device_type(pkt->params.device_type_set.device_type);
    (void)sys_config_save();
}

static void network_cmd_device_type_get(network_cmd_t *cmd, const protobuf_packet_t *pkt)
{
    protobuf_packet_t resp;

    CHECK_VOID(cmd && pkt && cmd->stream);

    memset(&resp, 0, sizeof(resp));
    resp.which_params                       = protobuf_packet_t_device_type_set_tag;
    resp.params.device_type_set.device_type = sys_config_get_device_type();
    resp.hdr.addr.dst                       = pkt->hdr.addr.src;

    network_cmd_send_packet(cmd, &resp);
}

static void network_cmd_sys_config_get(network_cmd_t *cmd, const protobuf_packet_t *pkt)
{
    protobuf_packet_t resp;
    const sys_config_t *cfg;

    CHECK_VOID(cmd && pkt && cmd->stream);

    cfg = sys_config_get();
    memset(&resp, 0, sizeof(resp));
    resp.which_params = protobuf_packet_t_sys_config_resp_tag;
    resp.params.sys_config_resp.has_config = true;
    resp.params.sys_config_resp.config = cfg->uwb;
    resp.hdr.addr.dst = pkt->hdr.addr.src;

    network_cmd_send_packet(cmd, &resp);
}

static void network_cmd_sys_config_set(network_cmd_t *cmd, const protobuf_packet_t *pkt)
{
    sys_config_t *cfg;

    CHECK_VOID(cmd && pkt);

    if (!pkt->params.sys_config_set.has_config) {
        return;
    }

    if (pkt->params.sys_config_set.config.role != DEVICE_ROLE_TAG &&
        pkt->params.sys_config_set.config.role != DEVICE_ROLE_ANCHOR) {
        RLOG_W(OBJECT_CODE, "Invalid role in sys_config_set: %u",
               (unsigned)pkt->params.sys_config_set.config.role);
        return;
    }

    if (pkt->params.sys_config_set.config.uwb_channel < 1u ||
        pkt->params.sys_config_set.config.uwb_channel > 7u) {
        RLOG_W(OBJECT_CODE, "Invalid UWB channel in sys_config_set: %u",
               (unsigned)pkt->params.sys_config_set.config.uwb_channel);
        return;
    }

    cfg = sys_config_get();
    cfg->uwb = pkt->params.sys_config_set.config;

    if (cfg->device_type == DEVICE_TYPE_UNSPECIFIED) {
        cfg->device_type = (cfg->uwb.role == DEVICE_ROLE_TAG) ? DEVICE_TYPE_TAG : DEVICE_TYPE_ANCHOR;
    }

    if (sys_config_save() != 0) {
        RLOG_W(OBJECT_CODE, "Failed to persist sys_config from host");
    }
}

static void network_cmd_sys_ranging_cfg_get(network_cmd_t *cmd, const protobuf_packet_t *pkt)
{
    protobuf_packet_t resp;
    const sys_config_t *cfg;

    CHECK_VOID(cmd && pkt && cmd->stream);

    cfg = sys_config_get();
    memset(&resp, 0, sizeof(resp));
    resp.which_params = protobuf_packet_t_sys_ranging_cfg_resp_tag;
    resp.params.sys_ranging_cfg_resp.has_config = true;
    resp.params.sys_ranging_cfg_resp.config.rx_timeout_ms      = cfg->uwb.rx_timeout_ms;
    resp.params.sys_ranging_cfg_resp.config.ranging_period_ms  = cfg->uwb.ranging_period_ms;
    resp.hdr.addr.dst = pkt->hdr.addr.src;
    network_cmd_send_packet(cmd, &resp);
}

static void network_cmd_sys_ranging_cfg_set(network_cmd_t *cmd, const protobuf_packet_t *pkt)
{
    sys_config_t *cfg;

    CHECK_VOID(cmd && pkt);

    if (!pkt->params.sys_ranging_cfg_set.has_config) {
        return;
    }

    cfg = sys_config_get();
    cfg->uwb.rx_timeout_ms     = pkt->params.sys_ranging_cfg_set.config.rx_timeout_ms;
    cfg->uwb.ranging_period_ms = pkt->params.sys_ranging_cfg_set.config.ranging_period_ms;

    if (sys_config_save() != 0) {
        RLOG_W(OBJECT_CODE, "Failed to persist ranging_cfg from host");
    }
}
static void network_cmd_device_reset(network_cmd_t *cmd, const protobuf_packet_t *pkt)
{
    (void)cmd;
    (void)pkt;

    bsp_util_device_reset();
}

static void network_cmd_time_sync_get(network_cmd_t *cmd, const protobuf_packet_t *pkt)
{
    protobuf_packet_t resp;
    uint64_t unix_time_ms = 0;
    int32_t timezone_offset = 0;

    CHECK_VOID(cmd && pkt && cmd->stream);

    memset(&resp, 0, sizeof(resp));
    resp.which_params = protobuf_packet_t_time_sync_resp_tag;

#ifdef HAVE_RTC
    if (bsp_rtc_sync_get(&unix_time_ms, &timezone_offset) != BSP_UTIL_OK) {
        RLOG_W(OBJECT_CODE, "RTC sync get failed, using fallback tick");
        unix_time_ms = (uint64_t)HAL_GetTick();
        timezone_offset = 0;
    }
#else
    unix_time_ms = (uint64_t)HAL_GetTick();
    timezone_offset = 0;
#endif

    resp.params.time_sync_resp.unix_time_ms = unix_time_ms;
    resp.params.time_sync_resp.timezone_offset = timezone_offset;
    resp.hdr.addr.dst = pkt->hdr.addr.src;

    network_cmd_send_packet(cmd, &resp);
}

static void network_cmd_time_sync_set(network_cmd_t *cmd, const protobuf_packet_t *pkt)
{
    CHECK_VOID(cmd && pkt);

    bsp_rtc_time_t rtc_time;

    if (bsp_rtc_sync_set(pkt->params.time_sync_set.unix_time_ms,
                         pkt->params.time_sync_set.timezone_offset) != BSP_UTIL_OK) {
        RLOG_W(OBJECT_CODE, "RTC sync set failed");
        return;
    }

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

static void network_cmd_ble_status_get(network_cmd_t *cmd, const protobuf_packet_t *pkt)
{
    protobuf_packet_t resp;
    //TODO: not implemented yet
    network_cmd_unimplemented(cmd, pkt);
    return;

    CHECK_VOID(cmd && pkt && cmd->stream);

    memset(&resp, 0, sizeof(resp));
    resp.which_params = protobuf_packet_t_ble_status_resp_tag;
    resp.params.ble_status_resp.state = protobuf_BLE_STATE_UNSPECIFIED;
    resp.params.ble_status_resp.rssi_dbm = 0;
    resp.params.ble_status_resp.connected = false;
    resp.hdr.addr.dst = pkt->hdr.addr.src;

    network_cmd_send_packet(cmd, &resp);
}

static void network_cmd_log_data_get(network_cmd_t *cmd, const protobuf_packet_t *pkt)
{
    CHECK_VOID(cmd && pkt && cmd->stream);

#ifdef HAVE_FLASH_STORAGE
    s_log_stream_enabled = true;
    s_log_stream_dst = (uint8_t)pkt->hdr.addr.src;

    protobuf_packet_t sample;
    uint16_t max_payload = (uint16_t)sizeof(sample.params.log_data.data.bytes);
    network_send_log(cmd, s_log_stream_dst, max_payload);
#endif /* HAVE_FLASH_STORAGE */
}

static void network_cmd_log_clear(network_cmd_t *cmd, const protobuf_packet_t *pkt)
{
    CHECK_VOID(cmd && pkt);

#ifdef HAVE_FLASH_STORAGE
    uint32_t length = pkt->params.log_clear.length;
    if (length > 0u) {
        sys_logger_flash_consume(length);
    }
#endif /* HAVE_FLASH_STORAGE */
}

static void network_cmd_host_transport_set(network_cmd_t *cmd, const protobuf_packet_t *pkt)
{
    CHECK_VOID(cmd && pkt);

    const sys_config_t *cfg = sys_config_get();
    host_transport_t old_transport = cfg ? cfg->host_transport : HOST_TRANSPORT_UNSPECIFIED;

    if (sys_config_set_host_transport(pkt->params.host_transport_set.transport) != 0) {
        RLOG_W(OBJECT_CODE, "Invalid host transport value: %u",
               (unsigned)pkt->params.host_transport_set.transport);
        return;
    }

    cfg = sys_config_get();
    if (cfg && cfg->host_transport == old_transport) {
        return;
    }

    if (sys_config_save() != 0) {
        RLOG_W(OBJECT_CODE, "Failed to persist host transport from host");
    }
}

static void network_cmd_pos_calib_cfg_get(network_cmd_t *cmd, const protobuf_packet_t *pkt)
{
    protobuf_packet_t resp_pkt;
    const sys_calib_cfg_t *calib_cfg;

    CHECK_VOID(cmd && pkt && cmd->stream);

    calib_cfg = sys_config_get_calib();
    if (!calib_cfg) {
        RLOG_E(OBJECT_CODE, ERR_UWB_CALIBRATION, "Failed to get calibration config");
        return;
    }

    memset(&resp_pkt, 0, sizeof(resp_pkt));
    resp_pkt.hdr = pkt->hdr;
    resp_pkt.which_params = protobuf_packet_t_pos_calib_cfg_resp_tag;
    resp_pkt.params.pos_calib_cfg_resp.config = *calib_cfg;

    network_cmd_send_packet(cmd, &resp_pkt);
}

static void network_cmd_pos_calib_cfg_set(network_cmd_t *cmd, const protobuf_packet_t *pkt)
{
    CHECK_VOID(cmd && pkt);

    if (sys_config_set_calib(&pkt->params.pos_calib_cfg_set.config) != 0) {
        RLOG_W(OBJECT_CODE, "Invalid calibration config received from host");
        return;
    }

    if (sys_config_save() != 0) {
        RLOG_W(OBJECT_CODE, "Failed to persist calibration config to flash");
    }
}

static void network_cmd_anchor_layout_get(network_cmd_t *cmd, const protobuf_packet_t *pkt)
{
    protobuf_packet_t resp_pkt;
    sys_anchor_layout_t anchors[SYS_CONFIG_MAX_ANCHORS];
    uint32_t count = 0;

    CHECK_VOID(cmd && pkt && cmd->stream);

    sys_config_get_anchor_layout(anchors, &count);
    if (count == 0 || count > SYS_CONFIG_MAX_ANCHORS) {
        RLOG_W(OBJECT_CODE, "Invalid anchor layout count: %lu", count);
        count = 0;
    }

    memset(&resp_pkt, 0, sizeof(resp_pkt));
    resp_pkt.hdr = pkt->hdr;
    resp_pkt.which_params = protobuf_packet_t_anchor_layout_resp_tag;
    resp_pkt.params.anchor_layout_resp.anchors_count = count;
    
    memcpy(resp_pkt.params.anchor_layout_resp.anchors, anchors,
           (size_t)count * sizeof(sys_anchor_layout_t));

    network_cmd_send_packet(cmd, &resp_pkt);
}

static void network_cmd_anchor_layout_set(network_cmd_t *cmd, const protobuf_packet_t *pkt)
{
    uint32_t count;

    CHECK_VOID(cmd && pkt);

    count = pkt->params.anchor_layout_set.anchors_count;
    if (count == 0 || count > SYS_CONFIG_MAX_ANCHORS) {
        RLOG_W(OBJECT_CODE, "Invalid anchor layout count: %lu", count);
        return;
    }

    if (sys_config_set_anchor_layout(pkt->params.anchor_layout_set.anchors, count) != 0) {
        RLOG_W(OBJECT_CODE, "Invalid anchor layout received from host");
        return;
    }

    if (sys_config_save() != 0) {
        RLOG_W(OBJECT_CODE, "Failed to persist anchor layout to flash");
    }
}

static void network_send_log(network_cmd_t *cmd, uint8_t dst, uint32_t data_length)
{
    CHECK_VOID(cmd && cmd->stream);

#ifdef HAVE_FLASH_STORAGE
    if (s_log_tracker.waiting_ack) {
        return;
    }

    if (sys_logger_flash_pending_bytes() == 0u) {
        return;
    }

    protobuf_packet_t packet;
    memset(&packet, 0, sizeof(packet));
    packet.which_params = protobuf_packet_t_log_data_tag;
    packet.params.log_data.type = protobuf_log_type_t_LOG_TYPE_DEVICE_LOG;

    uint16_t max_payload = (uint16_t)sizeof(packet.params.log_data.data.bytes);
    uint16_t send_len = (data_length > max_payload) ? max_payload : (uint16_t)data_length;
    uint32_t read_len = sys_logger_flash_read_packet(packet.params.log_data.data.bytes, send_len);
    if (read_len == 0u) {
        return;
    }

    packet.params.log_data.data.size = (pb_size_t)read_len;
    if (!network_core_send_packet(cmd->stream, dst, &packet)) {
        return;
    }

    s_log_tracker.waiting_ack = true;
    s_log_tracker.log_len = read_len;
    s_log_tracker.tracker_id = network_core_wait_ack(cmd->stream,
                                                     packet.hdr.seq,
                                                     WAIT_TIME_TO_RESEND_ACK_MS,
                                                     log_tracker_callback,
                                                     &s_log_tracker);
    if (s_log_tracker.tracker_id < 0) {
        s_log_tracker.waiting_ack = false;
        s_log_tracker.log_len = 0u;
    }
#else
    (void)dst;
    (void)data_length;
#endif
}

static void log_tracker_callback(network_ack_tracker_t *p_tracker, const protobuf_packet_t *packet)
{
    (void)packet;

#ifdef HAVE_FLASH_STORAGE
    CHECK_VOID(p_tracker != NULL);

    network_log_tracker_t *tracker = (network_log_tracker_t *)p_tracker->callback_arg;
    CHECK_VOID(tracker != NULL);

    if ((p_tracker->state == NETWORK_CORE_ACK_STATE_FOUND) && (tracker->log_len > 0u)) {
        sys_logger_flash_consume(tracker->log_len);
    }

    tracker->waiting_ack = false;
    tracker->log_len = 0u;
    tracker->tracker_id = -1;
#else
    (void)p_tracker;
#endif
}

static bool network_cmd_host_active(const network_cmd_t *cmd)
{
    CHECK(cmd && cmd->stream, false);

    uint32_t last_tick = cmd->stream->latest_packet_tick;
    if (last_tick == 0u) {
        return false;
    }

    return (uint32_t)(HAL_GetTick() - last_tick) <= NETWORK_HOST_ACTIVITY_TIMEOUT_MS;
}

bool network_cmd_init(network_cmd_t *cmd, network_core_t *stream)
{
    CHECK(cmd && stream, false);

    memset(cmd, 0, sizeof(network_cmd_t));
    cmd->stream = stream;
    cmd->enabled = true;

    g_network_cmd_instance = cmd;
    return network_core_register_packet_handler(stream, network_cmd_packet_handler);
}

void network_cmd_process(network_cmd_t *cmd)
{
    CHECK_VOID(cmd && cmd->enabled);
    network_cmd_retry_pending(cmd);

    if (s_log_stream_enabled && network_cmd_host_active(cmd)) {
        network_send_log(cmd, s_log_stream_dst, 0xFFFFu);
    }
}

bool network_cmd_process_packet(network_cmd_t *cmd, const protobuf_packet_t *pkt)
{
    const network_cmd_entry_t *entry = NULL;

    CHECK(cmd && pkt && cmd->enabled, false);

    if (pkt->which_params == protobuf_packet_t_ack_tag) {
        return true;
    }

    if ((size_t)pkt->which_params < NETWORK_CMD_TABLE_SIZE) {
        const network_cmd_entry_t *candidate = &network_cmd_table[pkt->which_params];
        if (candidate->cmd_hdl != NULL) {
            entry = candidate;
        }
    }

    if (entry != NULL) {
        return true;
    }

    network_cmd_unimplemented(cmd, pkt);
    return false;
}

void network_cmd_dispatch(network_cmd_t *cmd, const protobuf_packet_t *pkt)
{
    const network_cmd_entry_t *entry = NULL;

    CHECK_VOID(cmd && pkt && cmd->enabled);

    if (pkt->which_params == protobuf_packet_t_ack_tag) {
        return;
    }

    if ((size_t)pkt->which_params < NETWORK_CMD_TABLE_SIZE) {
        const network_cmd_entry_t *candidate = &network_cmd_table[pkt->which_params];
        if (candidate->cmd_hdl != NULL) {
            entry = candidate;
        }
    }

    if (entry == NULL) {
        network_cmd_unimplemented(cmd, pkt);
        return;
    }

    if (entry->cmd_hdl == network_cmd_unimplemented) {
        network_cmd_unimplemented(cmd, pkt);
        return;
    }

    if (entry->cmd_hdl != NULL) {
        entry->cmd_hdl(cmd, pkt);
    }

    (void)network_core_send_ack(cmd->stream,
                                pkt,
                                protobuf_PACKET_ACK_RESPONSE_ACK);
}

static bool network_cmd_packet_handler(const protobuf_packet_t *pkt)
{
    CHECK(g_network_cmd_instance && pkt, false);
    network_cmd_dispatch(g_network_cmd_instance, pkt);
    return true;
}

