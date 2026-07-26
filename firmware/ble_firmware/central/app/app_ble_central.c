/**
 * @file    app_ble_central.c
 * @version 2.1.0
 * @date    2025-04-12
 * @author  Phuong Mai
 * @brief   BLE Central Application
 *
 * Handles BLE stack initialization, scanning, connection management,
 * and LBS client logic.
 */

/* Includes ---------------------------------------------------------------- */
#include "app_ble_central.h"

#include <string.h>

#include "central_io.h"
#include "boards.h"
#include "bsp.h"
#include "bsp_btn_ble.h"
#include "app_button.h"
#include "ble.h"
#include "ble_hci.h"
#include "ble_advertising.h"
#include "ble_advdata.h"
#include "ble_conn_params.h"
#include "ble_db_discovery.h"
#include "ble_lbs_c.h"
#include "ble_nus_c.h"
#include "ble_config.h"

#include "nrf_ble_gatt.h"
#include "nrf_ble_scan.h"
#include "nrf_log.h"
#include "nrf_log_ctrl.h"
#include "nrf_sdh.h"
#include "nrf_sdh_ble.h"
#include "nrf_drv_gpiote.h"
#include "app_timer.h"
#include "app_button.h"
#include "nrf_delay.h"
#include "app_util.h"

#include "bsp_led.h"
#include "bb_cmd_hdl.h"
#include "bb_broadcast.h"
#include "protocol.pb.h"

/* Private defines --------------------------------------------------------- */
#define APP_BLE_CONN_CFG_TAG     1  /**< SoftDevice BLE configuration tag. */
#define APP_BLE_OBSERVER_PRIO    3  /**< BLE observer priority.            */
#define APP_BLE_GATTC_WRITE_CMD_TX_QUEUE_SIZE 8U

#define MAX_KNOWN_DEVICES        15 /**< Maximum devices tracked in scan list. */
#define KNOWN_DEVICE_STALE_MS    5000U
#define APP_BLE_CENTRAL_RSSI_UNKNOWN_DBM                (-127)
#define APP_BLE_CENTRAL_DISCONNECT_REASON_NONE          0U
#define ADV_STATUS_CACHE_SIZE                           8U
#define ADV_STATUS_HEARTBEAT_MS                         5000U

/* -------------------------------------------------------------------------
 * Debug helpers
 * ---------------------------------------------------------------------- */

/* Private types ----------------------------------------------------------- */

/** Information record for one discovered BLE peripheral. */
typedef struct
{
    bool     active;                           /**< Slot is occupied.           */
    uint8_t  addr[BLE_GAP_ADDR_LEN];           /**< Peer MAC address.           */
    uint8_t  addr_type;                        /**< Peer MAC address type.      */
    char     name[NRF_BLE_SCAN_NAME_MAX_LEN + 1]; /**< Advertised device name. */
    uint16_t uuid;                             /**< First 16-bit service UUID.  */
    uint32_t serial_number;                    /**< Serial from ADV status.     */
    int8_t   rssi;                             /**< Last observed RSSI (dBm).   */
    uint32_t last_seen;                        /**< app_timer tick of last adv. */
} known_device_t;


#pragma pack(push, 1)
typedef struct
{
    uint8_t  device_type;
    uint8_t  device_id;
    uint8_t  bat_soc_percent;
    uint16_t status_flags;
    uint8_t  warning_count;
    uint8_t  error_count;
    uint32_t timestamp_s;
    uint32_t serial_number;
} ble_adv_status_packed_t;
#pragma pack(pop)

typedef struct
{
    bool active;
    uint8_t addr[BLE_GAP_ADDR_LEN];
    ble_adv_status_packed_t status;
    uint32_t last_forward_tick;
} adv_status_cache_t;

/* Private instances ------------------------------------------------------- */
NRF_BLE_SCAN_DEF(m_scan);         /**< Scanning module instance.     */
BLE_LBS_C_DEF(m_ble_lbs_c);       /**< LBS client module instance.   */
BLE_NUS_C_DEF(m_ble_nus_c);       /**< NUS client module instance.   */
NRF_BLE_GATT_DEF(m_gatt);         /**< GATT module instance.         */
BLE_DB_DISCOVERY_DEF(m_db_disc);  /**< DB discovery module instance. */
NRF_BLE_GQ_DEF(m_ble_gatt_queue,  /**< BLE GATT Queue instance.      */
               NRF_SDH_BLE_CENTRAL_LINK_COUNT,
               NRF_BLE_GQ_QUEUE_SIZE);

/** Service UUID filter: only connect to peripherals advertising our system service UUID. */
static ble_uuid_t const m_target_periph_uuid =
{
    .uuid = SYSTEM_CONFIG_SERVICE_UUID,
    .type = BLE_UUID_TYPE_BLE,
};

/* Private variables ------------------------------------------------------- */
static known_device_t m_known_devices[MAX_KNOWN_DEVICES]; /**< Live scan list.           */
static bool           m_is_connected = false;             /**< Connection flag.          */
static bool           m_is_connecting = false;            /**< Connecting flag.          */
static bool           m_nus_ready = false;                /**< NUS handles/notifications ready. */
static bool           m_scan_active = false;

static bool           m_has_pending_connect = false;
static ble_gap_addr_t m_pending_target_addr;
static bool           m_user_disconnect_requested = false;

static uint16_t       m_current_conn_handle = BLE_CONN_HANDLE_INVALID;
static ble_gap_conn_params_t m_current_conn_params;
static protobuf_ble_state_t m_current_ble_state = protobuf_BLE_STATE_IDLE;
static int32_t        m_last_rssi_dbm = APP_BLE_CENTRAL_RSSI_UNKNOWN_DBM;
static uint32_t       m_last_disconnect_reason = APP_BLE_CENTRAL_DISCONNECT_REASON_NONE;
static ble_central_rx_cb_t m_ble_rx_cb = NULL;
static uint32_t       m_pending_tx_chunks = 0;
static adv_status_cache_t m_adv_status_cache[ADV_STATUS_CACHE_SIZE];
static uint8_t        m_adv_status_cache_replace;
APP_TIMER_DEF(m_scan_publish_timer);

/* Private prototypes ------------------------------------------------------ */
static void ble_evt_handler(ble_evt_t const *p_ble_evt, void *p_context);
static void scan_evt_handler(scan_evt_t const *p_scan_evt);
static void db_disc_handler(ble_db_discovery_evt_t *p_evt);
static void scan_start(void);
static ret_code_t scan_start_safe(const char *context);
static void lbs_c_init(void);
static void scan_report_log(ble_gap_evt_adv_report_t const *p_adv_report, bool matched);
static void ble_state_update(protobuf_ble_state_t new_state);
static void scan_publish_timeout_handler(void *p_context);
static void central_adv_status_scan_report_handle(ble_gap_evt_adv_report_t const *p_adv_report);

static void ble_state_update(protobuf_ble_state_t new_state)
{
    if (m_current_ble_state == new_state)
    {
        return;
    }

    m_current_ble_state = new_state;

    bb_cmd_notify_ble_status((uint8_t)new_state,
                             app_ble_central_rssi_dbm_get(),
                             m_last_disconnect_reason);
}

static void scan_publish_timeout_handler(void *p_context)
{
    UNUSED_PARAMETER(p_context);

    if (m_current_ble_state != protobuf_BLE_STATE_SCANNING)
    {
        return;
    }

    uint32_t now = app_timer_cnt_get();
    uint32_t stale_ticks = APP_TIMER_TICKS(KNOWN_DEVICE_STALE_MS);

    for (int i = 0; i < MAX_KNOWN_DEVICES; i++)
    {
        if (!m_known_devices[i].active)
        {
            continue;
        }

        uint32_t age = (now - m_known_devices[i].last_seen) & 0x00FFFFFFU;
        if (age > stale_ticks)
        {
            memset(&m_known_devices[i], 0, sizeof(m_known_devices[i]));
            continue;
        }

        bb_cmd_notify_scan_result(m_known_devices[i].addr,
                                  m_known_devices[i].rssi,
                                  m_known_devices[i].name,
                                  m_known_devices[i].serial_number);
    }
}

static void central_adv_status_scan_report_handle(ble_gap_evt_adv_report_t const *p_adv_report)
{
    uint16_t pos = 0;

    while (pos < p_adv_report->data.len)
    {
        uint8_t field_len = p_adv_report->data.p_data[pos++];
        if (field_len == 0u)
        {
            break;
        }
        if ((uint16_t)(pos + field_len) > p_adv_report->data.len)
        {
            return;
        }

        uint8_t ad_type = p_adv_report->data.p_data[pos];
        const uint8_t *field_data = &p_adv_report->data.p_data[pos + 1u];
        uint8_t field_data_len = (uint8_t)(field_len - 1u);

        if (ad_type == BLE_GAP_AD_TYPE_MANUFACTURER_SPECIFIC_DATA &&
            field_data_len == (uint8_t)(2u + BLE_BROADCAST_EXT_MANUF_TYPE_SIZE + sizeof(ble_adv_status_packed_t)))
        {
            uint16_t company_id = (uint16_t)field_data[0] | ((uint16_t)field_data[1] << 8);
            if (company_id == BLE_BROADCAST_COMPANY_ID &&
                field_data[2] == BLE_BROADCAST_TYPE_ADV_STATUS)
            {
                ble_adv_status_packed_t packed;
                memcpy(&packed, &field_data[2u + BLE_BROADCAST_EXT_MANUF_TYPE_SIZE], sizeof(packed));

                uint32_t now = app_timer_cnt_get();
                adv_status_cache_t *cache = NULL;
                for (uint8_t i = 0u; i < ADV_STATUS_CACHE_SIZE; i++)
                {
                    if (m_adv_status_cache[i].active &&
                        memcmp(m_adv_status_cache[i].addr,
                               p_adv_report->peer_addr.addr,
                               BLE_GAP_ADDR_LEN) == 0)
                    {
                        cache = &m_adv_status_cache[i];
                        break;
                    }
                }

                if (cache == NULL)
                {
                    cache = &m_adv_status_cache[m_adv_status_cache_replace];
                    m_adv_status_cache_replace =
                        (uint8_t)((m_adv_status_cache_replace + 1u) % ADV_STATUS_CACHE_SIZE);
                    memset(cache, 0, sizeof(*cache));
                    cache->active = true;
                    memcpy(cache->addr, p_adv_report->peer_addr.addr, BLE_GAP_ADDR_LEN);
                }

                bool changed = memcmp(&cache->status, &packed, sizeof(packed)) != 0;
                uint32_t heartbeat_ticks = APP_TIMER_TICKS(ADV_STATUS_HEARTBEAT_MS);
                uint32_t age = (now - cache->last_forward_tick) & 0x00FFFFFFu;
                if (!changed && cache->last_forward_tick != 0u && age < heartbeat_ticks)
                {
                    return;
                }

                cache->status = packed;
                cache->last_forward_tick = now;

                for (uint8_t i = 0u; i < MAX_KNOWN_DEVICES; i++)
                {
                    if (m_known_devices[i].active &&
                        memcmp(m_known_devices[i].addr,
                               p_adv_report->peer_addr.addr,
                               BLE_GAP_ADDR_LEN) == 0)
                    {
                        m_known_devices[i].serial_number = packed.serial_number;
                        break;
                    }
                }

                protobuf_ble_adv_status_t status = protobuf_ble_adv_status_t_init_zero;
                status.device = (protobuf_device_type_t)packed.device_type;
                status.device_id = packed.device_id;
                status.bat_soc_percent = packed.bat_soc_percent;
                status.status_flags = packed.status_flags;
                status.warning_count = packed.warning_count;
                status.error_count = packed.error_count;
                status.local_timestamp_s = packed.timestamp_s;
                status.serial_number = packed.serial_number;

                NRF_LOG_INFO("BLE ADV status RX device=%u id=%u bat=%u",
                             (unsigned)status.device,
                             (unsigned)status.device_id,
                             (unsigned)status.bat_soc_percent);
                bb_cmd_notify_adv_status(&status);
                return;
            }
        }

        pos = (uint16_t)(pos + field_len);
    }
}

void app_ble_central_scan_start(uint16_t interval_ms, uint16_t window_ms, uint16_t duration_ms, bool active)
{
    if (interval_ms > 0 && window_ms > 0) {
        m_scan.scan_params.interval = MSEC_TO_UNITS(interval_ms, UNIT_0_625_MS);
        m_scan.scan_params.window   = MSEC_TO_UNITS(window_ms, UNIT_0_625_MS);
        m_scan.scan_params.timeout  = duration_ms / 10;
        m_scan.scan_params.active   = active ? 1 : 0;
    }

    /* A host-requested scan is a fresh snapshot, never a replay of old peers. */
    memset(m_known_devices, 0, sizeof(m_known_devices));

    /* A new host scan must also repair stale SoftDevice/module state. */
    nrf_ble_scan_stop();
    m_scan_active = false;
    ret_code_t err_code = scan_start_safe("host request");
    if (err_code == NRF_SUCCESS) {
        bsp_led_scanning();
        m_is_connecting = false;
        m_has_pending_connect = false;
        ble_state_update(protobuf_BLE_STATE_SCANNING);
    }
}

void app_ble_central_scan_stop(void)
{
    nrf_ble_scan_stop();
    m_scan_active = false;
    if (!m_is_connected && !m_is_connecting) {
        ble_state_update(protobuf_BLE_STATE_IDLE);
    }
    // TODO: Add bsp led off if there's an API, usually bsp_led library covers state changes automatically
}

/* -------------------------------------------------------------------------
 * Scan report processing
 * ---------------------------------------------------------------------- */

/**
 * @brief Process one advertising report and refresh the known-device list.
 *
 * Extracts the device name and 16-bit service UUID from the advertisement
 * payload and updates the tracking table.
 *
 * @param[in] p_adv_report  Pointer to the raw advertising report.
 * @param[in] matched       true if the report matched the scan filter.
 */
static void scan_report_log(ble_gap_evt_adv_report_t const *p_adv_report, bool matched)
{
    /* Broadcast RX is handled by bb_broadcast's own BLE observer. */
    central_adv_status_scan_report_handle(p_adv_report);

    /* ----- Extract device name ----------------------------------------- */
    char     dev_name[NRF_BLE_SCAN_NAME_MAX_LEN + 1] = "<no_name>";
    uint16_t offset    = 0;
    uint16_t field_len;

    field_len = ble_advdata_search(p_adv_report->data.p_data,
                                   p_adv_report->data.len,
                                   &offset,
                                   BLE_GAP_AD_TYPE_COMPLETE_LOCAL_NAME);

    if (field_len == 0)
    {
        offset    = 0;
        field_len = ble_advdata_search(p_adv_report->data.p_data,
                                       p_adv_report->data.len,
                                       &offset,
                                       BLE_GAP_AD_TYPE_SHORT_LOCAL_NAME);
    }

    if (field_len > 0)
    {
        uint16_t copy_len = (field_len < NRF_BLE_SCAN_NAME_MAX_LEN)
                            ? field_len : NRF_BLE_SCAN_NAME_MAX_LEN;
        memcpy(dev_name, &p_adv_report->data.p_data[offset], copy_len);
        dev_name[copy_len] = '\0';
    }

    /* ----- Extract 16-bit service UUID ---------------------------------- */
    uint16_t uuid16      = 0;
    uint16_t uuid_offset = 0;
    uint16_t uuid_len    = ble_advdata_search(p_adv_report->data.p_data,
                                              p_adv_report->data.len,
                                              &uuid_offset,
                                              BLE_GAP_AD_TYPE_16BIT_SERVICE_UUID_COMPLETE);
    if (uuid_len == 0)
    {
        uuid_offset = 0;
        uuid_len    = ble_advdata_search(p_adv_report->data.p_data,
                                         p_adv_report->data.len,
                                         &uuid_offset,
                                         BLE_GAP_AD_TYPE_16BIT_SERVICE_UUID_MORE_AVAILABLE);
    }

    if (uuid_len > 0 && uuid_offset + 1 < p_adv_report->data.len)
    {
        uuid16 = p_adv_report->data.p_data[uuid_offset] |
                 ((uint16_t)p_adv_report->data.p_data[uuid_offset + 1] << 8);
    }

    uint32_t current_ticks = app_timer_cnt_get();

    /* ----- Find existing entry for this peer ---------------------------- */
    int known_idx = -1;
    for (int i = 0; i < MAX_KNOWN_DEVICES; i++)
    {
        if (m_known_devices[i].active &&
            memcmp(m_known_devices[i].addr,
                   p_adv_report->peer_addr.addr,
                   BLE_GAP_ADDR_LEN) == 0)
        {
            known_idx = i;
            break;
        }
    }

    if (known_idx == -1)
    {
        /* Ignore devices that do not match our target UUID */
        if (uuid16 != SYSTEM_CONFIG_SERVICE_UUID && !matched)
        {
            return;
        }

        /* ----- New device — find a free slot ---------------------------- */
        for (int i = 0; i < MAX_KNOWN_DEVICES; i++)
        {
            if (!m_known_devices[i].active)
            {
                known_idx = i;
                break;
            }
        }

        /* If all slots are full, evict the oldest entry. */
        if (known_idx == -1)
        {
            uint32_t max_diff = 0;
            for (int i = 0; i < MAX_KNOWN_DEVICES; i++)
            {
                uint32_t diff = (current_ticks - m_known_devices[i].last_seen) & 0x00FFFFFF;
                if (diff > max_diff)
                {
                    max_diff  = diff;
                    known_idx = i;
                }
            }
        }

        /* Initialize the known device fields */
        m_known_devices[known_idx].active = true;
        memcpy(m_known_devices[known_idx].addr,
               p_adv_report->peer_addr.addr,
               BLE_GAP_ADDR_LEN);                                                           /* MAC address is 6 bytes, but BLE_GAP_ADDR_LEN is 8 — copy only 6. */
        m_known_devices[known_idx].addr_type = p_adv_report->peer_addr.addr_type;           /* For later use in connection. */
        strncpy(m_known_devices[known_idx].name, dev_name, NRF_BLE_SCAN_NAME_MAX_LEN + 1);  /* Advertised name, truncated if necessary. */
        m_known_devices[known_idx].uuid      = uuid16;                                      /* First 16-bit service UUID, or 0 if not present. */
        m_known_devices[known_idx].rssi      = p_adv_report->rssi;                          /* Signal strength in dBm. */
        m_known_devices[known_idx].last_seen = current_ticks;                               /* Timestamp for aging out old entries. */
        m_last_rssi_dbm = p_adv_report->rssi;

    }
    else
    {
        /* ----- Known device — update fields if improved ----------------- */
        if (strcmp(dev_name, "<no_name>") != 0)
        {
            strncpy(m_known_devices[known_idx].name, dev_name, NRF_BLE_SCAN_NAME_MAX_LEN + 1);
        }
        
        if (m_known_devices[known_idx].uuid == 0 && uuid16 != 0)
        {
            m_known_devices[known_idx].uuid = uuid16;
        }

        /* Always refresh RSSI and heartbeat timestamp. */
        m_known_devices[known_idx].rssi      = p_adv_report->rssi;
        m_known_devices[known_idx].last_seen = current_ticks;
        m_last_rssi_dbm = p_adv_report->rssi;
    }

}

/* -------------------------------------------------------------------------
 * Private BLE helpers
 * ---------------------------------------------------------------------- */

static void scan_start(void)
{
    ret_code_t err_code = scan_start_safe("state machine");
    if (err_code == NRF_SUCCESS)
    {
        bsp_led_scanning();
        ble_state_update(protobuf_BLE_STATE_SCANNING);
    }
}

static ret_code_t scan_start_safe(const char *context)
{
    ret_code_t err_code = nrf_ble_scan_start(&m_scan);

    if (err_code == NRF_ERROR_INVALID_STATE && !m_scan_active)
    {
        nrf_ble_scan_stop();
        err_code = nrf_ble_scan_start(&m_scan);
    }

    if (err_code == NRF_SUCCESS ||
        (err_code == NRF_ERROR_INVALID_STATE && m_scan_active))
    {
        m_scan_active = true;
        return NRF_SUCCESS;
    }

    m_scan_active = false;
    NRF_LOG_WARNING("BLE scan start failed (%s): 0x%08x",
                    context != NULL ? context : "unknown",
                    err_code);
    return err_code;
}

static void lbs_error_handler(uint32_t nrf_error)
{
    APP_ERROR_HANDLER(nrf_error);
}

static void lbs_c_evt_handler(ble_lbs_c_t *p_lbs_c, ble_lbs_c_evt_t *p_lbs_c_evt)
{
    switch (p_lbs_c_evt->evt_type)
    {
        case BLE_LBS_C_EVT_DISCOVERY_COMPLETE:
        {
            ret_code_t err_code;

            NRF_LOG_INFO("LBS: Discovery complete on conn_handle=0x%x", p_lbs_c_evt->conn_handle);
            NRF_LOG_DEBUG("LBS: LED char handle=0x%04x, Button char handle=0x%04x",
                          p_lbs_c_evt->params.peer_db.led_handle,
                          p_lbs_c_evt->params.peer_db.button_handle);

            err_code = ble_lbs_c_handles_assign(&m_ble_lbs_c,
                                                p_lbs_c_evt->conn_handle,
                                                &p_lbs_c_evt->params.peer_db);
            APP_ERROR_CHECK(err_code);

            err_code = ble_lbs_c_button_notif_enable(p_lbs_c);
            if (err_code == NRF_SUCCESS)
            {
                NRF_LOG_INFO("LBS: Button notification enabled");
            }
            else
            {
                NRF_LOG_WARNING("LBS: Button notif enable failed: 0x%08x", err_code);
            }
            APP_ERROR_CHECK(err_code);
        } break;

        case BLE_LBS_C_EVT_BUTTON_NOTIFICATION:
            NRF_LOG_INFO("LBS: Peer button state changed -> 0x%x", p_lbs_c_evt->params.button.button_state);
            break;

        default:
            break;
    }
}

/**
 * @brief Nordic UART Service Client event handler.
 */
static void nus_c_evt_handler(ble_nus_c_t *p_ble_nus_c, ble_nus_c_evt_t const *p_evt)
{
    ret_code_t err_code;

    switch (p_evt->evt_type)
    {
        case BLE_NUS_C_EVT_DISCOVERY_COMPLETE:
            NRF_LOG_INFO("NUS Service discovered on conn_handle 0x%x", p_evt->conn_handle);
            err_code = ble_nus_c_handles_assign(p_ble_nus_c, p_evt->conn_handle, &p_evt->handles);
            APP_ERROR_CHECK(err_code);

            err_code = ble_nus_c_tx_notif_enable(p_ble_nus_c);
            APP_ERROR_CHECK(err_code);
            m_nus_ready = true;
            ble_state_update(protobuf_BLE_STATE_CONNECTED);
            NRF_LOG_INFO("BLE data channel ready (NUS discovery complete)");
            break;

        case BLE_NUS_C_EVT_NUS_TX_EVT:
            NRF_LOG_INFO("NUS Data received: %u bytes", p_evt->data_len);
            bsp_led_rx_pulse();
            if (m_ble_rx_cb != NULL)
            {
                m_ble_rx_cb(p_evt->p_data, p_evt->data_len);
            }
            break;

        case BLE_NUS_C_EVT_DISCONNECTED:
            NRF_LOG_INFO("NUS Service disconnected");
            m_nus_ready = false;
            break;

        default:
            break;
    }
}

static void nus_c_init(void)
{
    ret_code_t       err_code;
    ble_nus_c_init_t nus_c_init_obj;

    nus_c_init_obj.evt_handler   = nus_c_evt_handler;
    nus_c_init_obj.error_handler = lbs_error_handler; // Use same error handler
    nus_c_init_obj.p_gatt_queue  = &m_ble_gatt_queue;

    err_code = ble_nus_c_init(&m_ble_nus_c, &nus_c_init_obj);
    APP_ERROR_CHECK(err_code);
}

static void lbs_c_init(void)
{
    ret_code_t       err_code;
    ble_lbs_c_init_t lbs_c_init_obj;

    lbs_c_init_obj.evt_handler   = lbs_c_evt_handler;
    lbs_c_init_obj.p_gatt_queue  = &m_ble_gatt_queue;
    lbs_c_init_obj.error_handler = lbs_error_handler;

    err_code = ble_lbs_c_init(&m_ble_lbs_c, &lbs_c_init_obj);
    APP_ERROR_CHECK(err_code);
}

void button_init(void)
{
    /* Reserved: no direct USB/terminal control path in central app. */
}

/* -------------------------------------------------------------------------
 * BLE event handler
 * ---------------------------------------------------------------------- */

/**
 * @brief Handler for all BLE stack events.
 */
static void ble_evt_handler(ble_evt_t const *p_ble_evt, void *p_context)
{
    ret_code_t            err_code;
    ble_gap_evt_t const  *p_gap_evt = &p_ble_evt->evt.gap_evt;

    switch (p_ble_evt->header.evt_id)
    {
        /* ---- Connected ----------------------------------------------- */
        case BLE_GAP_EVT_CONNECTED:
        {
            const ble_gap_conn_params_t *p = &p_gap_evt->params.connected.conn_params;
            NRF_LOG_INFO("Connected. conn_handle=0x%x", p_gap_evt->conn_handle);
            NRF_LOG_INFO("  peer addr=%02x:%02x:%02x:%02x:%02x:%02x",
                         p_gap_evt->params.connected.peer_addr.addr[5],
                         p_gap_evt->params.connected.peer_addr.addr[4],
                         p_gap_evt->params.connected.peer_addr.addr[3],
                         p_gap_evt->params.connected.peer_addr.addr[2],
                         p_gap_evt->params.connected.peer_addr.addr[1],
                         p_gap_evt->params.connected.peer_addr.addr[0]);
            NRF_LOG_INFO("ConnParams: interval=%u.%02u ms latency=%u timeout=%u ms",
                         (p->max_conn_interval * 125) / 100,
                         (p->max_conn_interval * 125) % 100,
                         p->slave_latency,
                         p->conn_sup_timeout * 10); 

            bsp_led_connected();

            m_is_connected = true;
            m_is_connecting = false;
            m_nus_ready = false;
            m_current_conn_handle = p_gap_evt->conn_handle;
            m_current_conn_params = p_gap_evt->params.connected.conn_params;

            err_code = sd_ble_gap_tx_power_set(BLE_GAP_TX_POWER_ROLE_CONN,
                                                   p_gap_evt->conn_handle,
                                                   SYSTEM_CONFIG_CONN_TX_POWER);
            APP_ERROR_CHECK(err_code);
            m_pending_tx_chunks = 0;

            m_last_disconnect_reason = APP_BLE_CENTRAL_DISCONNECT_REASON_NONE;
            /* GAP is connected, but the link is not usable until NUS discovery completes. */
            ble_state_update(protobuf_BLE_STATE_CONNECTING);

            /* Start RSSI reporting */
            sd_ble_gap_rssi_start(p_gap_evt->conn_handle, 0, 0);

            /* Assign handles and kick off service discovery. */
            err_code = ble_lbs_c_handles_assign(&m_ble_lbs_c, p_gap_evt->conn_handle, NULL);
            if (err_code != NRF_SUCCESS)
            {
                NRF_LOG_WARNING("ble_lbs_c_handles_assign failed: 0x%08x", err_code);
            }

            err_code = ble_db_discovery_start(&m_db_disc, p_gap_evt->conn_handle);
            if (err_code != NRF_SUCCESS)
            {
                NRF_LOG_WARNING("ble_db_discovery_start failed: 0x%08x", err_code);
            }

            /* Resume scanning to keep discovering other devices. */
            (void)scan_start_safe("connected background scan");
        } break;

        /* ---- Disconnected -------------------------------------------- */
        case BLE_GAP_EVT_DISCONNECTED:
        {
            uint32_t reason = p_gap_evt->params.disconnected.reason;
            NRF_LOG_INFO("Disconnected. reason=0x%02x", (unsigned int)reason);

            m_is_connected = false;
            m_is_connecting = false;
            m_nus_ready = false;
            m_current_conn_handle = BLE_CONN_HANDLE_INVALID;
            m_pending_tx_chunks = 0;

            m_last_disconnect_reason = reason;

            if (m_has_pending_connect)
            {
                m_has_pending_connect = false;
                /* A newer connect() request supersedes any earlier explicit
                 * disconnect() intent racing against this same event. */
                m_user_disconnect_requested = false;
                NRF_LOG_INFO("CMD: Auto-connecting to queued MAC...");
                nrf_ble_scan_stop();
                m_scan_active = false;
                
                err_code = sd_ble_gap_connect(&m_pending_target_addr,
                                              &m_scan.scan_params,
                                              &m_scan.conn_params,
                                              APP_BLE_CONN_CFG_TAG);
                if (err_code != NRF_SUCCESS)
                {
                    NRF_LOG_WARNING("CMD: Auto-connect failed: 0x%08x", err_code);
                    ble_state_update(protobuf_BLE_STATE_IDLE);
                    scan_start();
                }
                else
                {
                    m_is_connecting = true;
                    m_last_disconnect_reason = APP_BLE_CENTRAL_DISCONNECT_REASON_NONE;
                    ble_state_update(protobuf_BLE_STATE_CONNECTING);
                }
            }
            else if (m_user_disconnect_requested)
            {
                /* Caller explicitly asked to disconnect: stay idle instead
                 * of immediately reconnecting to the same peer. */
                m_user_disconnect_requested = false;
                ble_state_update(protobuf_BLE_STATE_IDLE);
            }
            else
            {
                ble_state_update(protobuf_BLE_STATE_IDLE);
                /* Restart scanning. */
                scan_start();
            }
        } break;

        /* ---- Connection timeout -------------------------------------- */
        case BLE_GAP_EVT_TIMEOUT:
        {
            if (p_gap_evt->params.timeout.src == BLE_GAP_TIMEOUT_SRC_CONN)
            {
                NRF_LOG_DEBUG("Connection request timed out.");
                m_current_conn_handle = BLE_CONN_HANDLE_INVALID;
                m_is_connected = false;
                m_is_connecting = false;
                m_nus_ready = false;
                
                m_last_disconnect_reason = p_gap_evt->params.timeout.src;
                ble_state_update(protobuf_BLE_STATE_IDLE);
                scan_start();
            }
            else if (p_gap_evt->params.timeout.src == BLE_GAP_TIMEOUT_SRC_SCAN)
            {
                m_scan_active = false;
                NRF_LOG_INFO("BLE scan duration completed");
                if (!m_is_connected && !m_is_connecting)
                {
                    ble_state_update(protobuf_BLE_STATE_IDLE);
                }
            }
        } break;

        /* ---- Connection parameter update request -------------------- */
        case BLE_GAP_EVT_CONN_PARAM_UPDATE:
        {
            const ble_gap_conn_params_t *p_updated =
                &p_gap_evt->params.conn_param_update.conn_params;
            NRF_LOG_INFO("Connection parameters updated successfully!");
            NRF_LOG_INFO("New ConnParams: interval=%u.%02u ms latency=%u timeout=%u ms",
                         (p_updated->max_conn_interval * 125) / 100,
                         (p_updated->max_conn_interval * 125) % 100,
                         p_updated->slave_latency,
                         p_updated->conn_sup_timeout * 10);
            m_current_conn_params = *p_updated;
        } break;

        case BLE_GAP_EVT_CONN_PARAM_UPDATE_REQUEST:
        {
            const ble_gap_conn_params_t *p_req =
                &p_gap_evt->params.conn_param_update_request.conn_params;
            NRF_LOG_DEBUG("ConnParam update request: interval=[%u,%u] latency=%u timeout=%u",
                          p_req->min_conn_interval,
                          p_req->max_conn_interval,
                          p_req->slave_latency,
                          p_req->conn_sup_timeout);

            err_code = sd_ble_gap_conn_param_update(
                           p_gap_evt->conn_handle,
                           p_req);
            if (err_code != NRF_SUCCESS &&
                err_code != NRF_ERROR_BUSY &&
                err_code != NRF_ERROR_INVALID_STATE)
            {
                NRF_LOG_WARNING("ConnParam request reply failed: 0x%08x",
                                err_code);
            }
        } break;

        /* ---- PHY update request ------------------------------------- */
        case BLE_GAP_EVT_PHY_UPDATE_REQUEST:
        {
            NRF_LOG_DEBUG("PHY update request from peer. Accepting AUTO.");
            ble_gap_phys_t const phys =
            {
                .rx_phys = BLE_GAP_PHY_AUTO,
                .tx_phys = BLE_GAP_PHY_AUTO,
            };
            err_code = sd_ble_gap_phy_update(p_ble_evt->evt.gap_evt.conn_handle, &phys);
            APP_ERROR_CHECK(err_code);
        } break;

        /* ---- PHY update result -------------------------------------- */
        case BLE_GAP_EVT_PHY_UPDATE:
        {
            NRF_LOG_INFO("PHY updated: TX=%s RX=%s status=0x%02x",
                         (p_gap_evt->params.phy_update.tx_phy == BLE_GAP_PHY_2MBPS) ? "2M" :
                         (p_gap_evt->params.phy_update.tx_phy == BLE_GAP_PHY_1MBPS) ? "1M" : "Coded",
                         (p_gap_evt->params.phy_update.rx_phy == BLE_GAP_PHY_2MBPS) ? "2M" :
                         (p_gap_evt->params.phy_update.rx_phy == BLE_GAP_PHY_1MBPS) ? "1M" : "Coded",
                         p_gap_evt->params.phy_update.status);
        } break;

        /* ---- RSSI changed ------------------------------------------- */
        case BLE_GAP_EVT_RSSI_CHANGED:
        {
            m_last_rssi_dbm = p_gap_evt->params.rssi_changed.rssi;
        } break;

        /* ---- GATT Client timeout ------------------------------------ */
        case BLE_GATTC_EVT_TIMEOUT:
        {
            err_code = sd_ble_gap_disconnect(p_ble_evt->evt.gattc_evt.conn_handle,
                                             BLE_HCI_REMOTE_USER_TERMINATED_CONNECTION);
            APP_ERROR_CHECK(err_code);
        } break;

        case BLE_GATTC_EVT_WRITE_CMD_TX_COMPLETE:
        {
            uint16_t completed_count = p_ble_evt->evt.gattc_evt.params.write_cmd_tx_complete.count;
            if (m_pending_tx_chunks > 0)
            {
                if (completed_count >= m_pending_tx_chunks)
                {
                    m_pending_tx_chunks = 0;
                }
                else
                {
                    m_pending_tx_chunks -= completed_count;
                }
                bsp_led_tx_pulse();
            }
        } break;

        case BLE_GATTC_EVT_WRITE_RSP:
            if (m_pending_tx_chunks > 0)
            {
                m_pending_tx_chunks--;
                bsp_led_tx_pulse();
            }
            break;

        /* ---- GATT Server timeout ------------------------------------ */
        case BLE_GATTS_EVT_TIMEOUT:
        {
            err_code = sd_ble_gap_disconnect(p_ble_evt->evt.gatts_evt.conn_handle,
                                             BLE_HCI_REMOTE_USER_TERMINATED_CONNECTION);
            APP_ERROR_CHECK(err_code);
        } break;

        default:
            break;
    }
}

/* -------------------------------------------------------------------------
 * Scan event handler
 * ---------------------------------------------------------------------- */

static void scan_evt_handler(scan_evt_t const *p_scan_evt)
{
    ret_code_t err_code;

    switch (p_scan_evt->scan_evt_id)
    {
        case NRF_BLE_SCAN_EVT_FILTER_MATCH:
            NRF_LOG_DEBUG("SCAN: Filter match — UUID 0x%04X found", SYSTEM_CONFIG_SERVICE_UUID);
            scan_report_log(p_scan_evt->params.filter_match.p_adv_report, true);
            break;

        case NRF_BLE_SCAN_EVT_NOT_FOUND:
            scan_report_log(p_scan_evt->params.p_not_found, false);
            break;

        case NRF_BLE_SCAN_EVT_CONNECTING_ERROR:
            err_code = p_scan_evt->params.connecting_err.err_code;
            NRF_LOG_WARNING("SCAN: Connect attempt failed. err=0x%08x", err_code);
            break;

        case NRF_BLE_SCAN_EVT_CONNECTED:
            NRF_LOG_INFO("SCAN: Connection established by scan module");
            break;

        default:
            break;
    }
}

/* -------------------------------------------------------------------------
 * DB discovery event handler
 * ---------------------------------------------------------------------- */

static void db_disc_handler(ble_db_discovery_evt_t *p_evt)
{
    if (p_evt->evt_type == BLE_DB_DISCOVERY_COMPLETE)
    {
        NRF_LOG_INFO("DB Discovery: complete on conn_handle=0x%x, service UUID=0x%04x, %u chars found",
                     p_evt->conn_handle,
                     p_evt->params.discovered_db.srv_uuid.uuid,
                     p_evt->params.discovered_db.char_count);
    }
    else if (p_evt->evt_type == BLE_DB_DISCOVERY_SRV_NOT_FOUND)
    {
        NRF_LOG_WARNING("DB Discovery: service UUID=0x%04x NOT found on conn_handle=0x%x",
                        p_evt->params.discovered_db.srv_uuid.uuid,
                        p_evt->conn_handle);
    }
    else if (p_evt->evt_type == BLE_DB_DISCOVERY_ERROR)
    {
        NRF_LOG_ERROR("DB Discovery: error on conn_handle=0x%x", p_evt->conn_handle);
    }

    ble_lbs_on_db_disc_evt(&m_ble_lbs_c, p_evt);
    ble_nus_c_on_db_disc_evt(&m_ble_nus_c, p_evt);
}

/* -------------------------------------------------------------------------
 * Public API — sub-init functions (called by ble_central_init)
 * ---------------------------------------------------------------------- */

void ble_stack_init(void)
{
    ret_code_t err_code;

    NRF_LOG_INFO("BLE: nrf_sdh_enable_request");
    err_code = nrf_sdh_enable_request();
    APP_ERROR_CHECK(err_code);

    /* Fetch the required RAM start address after configuration. */
    uint32_t ram_start = 0;
    NRF_LOG_INFO("BLE: nrf_sdh_ble_default_cfg_set");
    err_code = nrf_sdh_ble_default_cfg_set(APP_BLE_CONN_CFG_TAG, &ram_start);
    if (err_code == NRF_SUCCESS)
    {
        NRF_LOG_INFO("BLE: RAM start required by SD = 0x%08x", ram_start);
    }
    APP_ERROR_CHECK(err_code);

    NRF_LOG_INFO("BLE: nrf_sdh_ble_enable");
    err_code = nrf_sdh_ble_enable(&ram_start);
    APP_ERROR_CHECK(err_code);

    /* Set TX Power using value from ble_config.h */
    err_code = sd_ble_gap_tx_power_set(BLE_GAP_TX_POWER_ROLE_SCAN_INIT, 0, SYSTEM_CONFIG_TX_POWER);
    APP_ERROR_CHECK(err_code);

    /* Register the application BLE event handler. */
    NRF_SDH_BLE_OBSERVER(m_ble_observer, APP_BLE_OBSERVER_PRIO, ble_evt_handler, NULL);
}

void db_discovery_init(void)
{
    ble_db_discovery_init_t db_init;
    memset(&db_init, 0, sizeof(db_init));

    db_init.evt_handler  = db_disc_handler;
    db_init.p_gatt_queue = &m_ble_gatt_queue;

    ret_code_t err_code = ble_db_discovery_init(&db_init);
    APP_ERROR_CHECK(err_code);
}

void scan_init(void)
{
    ret_code_t          err_code;
    nrf_ble_scan_init_t init_scan;
    memset(&init_scan, 0, sizeof(init_scan));

    ble_gap_scan_params_t scan_param;
    memset(&scan_param, 0, sizeof(scan_param));
    scan_param.active        = 1;
    scan_param.interval      = MSEC_TO_UNITS(SYSTEM_CONFIG_SCAN_INTERVAL_MS, UNIT_0_625_MS);
    scan_param.window        = MSEC_TO_UNITS(SYSTEM_CONFIG_SCAN_WINDOW_MS, UNIT_0_625_MS);
    scan_param.filter_policy = BLE_GAP_SCAN_FP_ACCEPT_ALL;
    scan_param.timeout       = (SYSTEM_CONFIG_SCAN_DURATION_MS == 0)
                                ? 0
                                : (uint16_t)(SYSTEM_CONFIG_SCAN_DURATION_MS / 10U);
    scan_param.scan_phys     = SYSTEM_CONFIG_PREFERRED_PHY;
#if BLE_BROADCAST_USE_EXTENDED
    /* Central commands may use Extended Advertising. Peripheral ACKs use the
     * typed legacy form when they fit, but extended scan support is still
     * required for the command path and larger broadcast payloads. */
    scan_param.extended      = 1;
#endif

    init_scan.connect_if_match = false; /**< Manual-connect via terminal / button. */
    init_scan.conn_cfg_tag     = APP_BLE_CONN_CFG_TAG;
    init_scan.p_scan_param     = &scan_param;
    
    /* Config our desired connection parameters based on ble_config.h */
    ble_gap_conn_params_t conn_param;
    memset(&conn_param, 0, sizeof(conn_param));
    conn_param.min_conn_interval = SYSTEM_CONFIG_MIN_CONN_INTERVAL;
    conn_param.max_conn_interval = SYSTEM_CONFIG_MAX_CONN_INTERVAL;
    conn_param.slave_latency     = SYSTEM_CONFIG_SLAVE_LATENCY;
    conn_param.conn_sup_timeout  = SYSTEM_CONFIG_CONN_SUP_TIMEOUT;
    init_scan.p_conn_param       = &conn_param;

    err_code = nrf_ble_scan_init(&m_scan, &init_scan, scan_evt_handler);
    APP_ERROR_CHECK(err_code);

    /* Enable UUID filter — only auto-connect to the target UUID. */
    err_code = nrf_ble_scan_filter_set(&m_scan, SCAN_UUID_FILTER, &m_target_periph_uuid);
    APP_ERROR_CHECK(err_code);

    err_code = nrf_ble_scan_filters_enable(&m_scan, NRF_BLE_SCAN_UUID_FILTER, false);
    APP_ERROR_CHECK(err_code);
}

void gatt_init(void)
{
    ret_code_t err_code = nrf_ble_gatt_init(&m_gatt, NULL);
    APP_ERROR_CHECK(err_code);

    /* Update MTU size according to the shared configuration */
    err_code = nrf_ble_gatt_att_mtu_central_set(&m_gatt, SYSTEM_CONFIG_MTU_SIZE);
    APP_ERROR_CHECK(err_code);
}

void button_init(void);

/* -------------------------------------------------------------------------
 * Public API — main entry point
 * ---------------------------------------------------------------------- */

void ble_central_init(void)
{
    NRF_LOG_INFO("BLE: ble_central_init");
    ble_stack_init();
    gatt_init();
    bsp_led_init();
    button_init();
    db_discovery_init();
    lbs_c_init();
    nus_c_init();
    scan_init();

    /* Broadcast (TX + RX) lives in its own module with a dedicated adv set. */
    bb_broadcast_init();

    ret_code_t err_code = app_timer_create(&m_scan_publish_timer,
                                           APP_TIMER_MODE_REPEATED,
                                           scan_publish_timeout_handler);
    APP_ERROR_CHECK(err_code);
    err_code = app_timer_start(m_scan_publish_timer,
                               APP_TIMER_TICKS(2000),
                               NULL);
    APP_ERROR_CHECK(err_code);

    scan_start();
}

/**
 * @brief Update connection parameters for a specific connection.
 */
void central_update_conn_params(uint16_t conn_handle, 
                                uint16_t min_interval_ms, 
                                uint16_t max_interval_ms, 
                                uint16_t slave_latency, 
                                uint16_t conn_sup_timeout_ms)
{
    ret_code_t err_code;
    ble_gap_conn_params_t conn_params;

    if (conn_handle == BLE_CONN_HANDLE_INVALID)
    {
        return; 
    }

    memset(&conn_params, 0, sizeof(conn_params));

    conn_params.min_conn_interval = MSEC_TO_UNITS(min_interval_ms, UNIT_1_25_MS);
    conn_params.max_conn_interval = MSEC_TO_UNITS(max_interval_ms, UNIT_1_25_MS);
    conn_params.slave_latency     = slave_latency;
    conn_params.conn_sup_timeout  = MSEC_TO_UNITS(conn_sup_timeout_ms, UNIT_10_MS);

    err_code = sd_ble_gap_conn_param_update(conn_handle, &conn_params);
    if (err_code != NRF_SUCCESS)
    {
        NRF_LOG_ERROR("Failed to update conn params: %x", err_code);
    }
}


/* -------------------------------------------------------------------------
 * Public APIs for Protocol Bridge
 * ---------------------------------------------------------------------- */
void ble_central_rx_cb_register(ble_central_rx_cb_t cb)
{
    m_ble_rx_cb = cb;
}
void app_ble_central_connect(const uint8_t *mac)
{
    uint8_t target_mac[6];
    memcpy(target_mac, mac, 6);

    int found_idx = -1;
    for (int i = 0; i < MAX_KNOWN_DEVICES; i++)
    {
        if (m_known_devices[i].active &&
            memcmp(m_known_devices[i].addr, target_mac, 6) == 0)
        {
            found_idx = i;
            break;
        }
    }

    if (found_idx != -1)
    {
        NRF_LOG_INFO("API: Connecting to MAC...");
        if (m_is_connected || m_is_connecting || m_current_conn_handle != BLE_CONN_HANDLE_INVALID)
        {
            memset(&m_pending_target_addr, 0, sizeof(m_pending_target_addr));
            m_pending_target_addr.addr_id_peer = 0;
            m_pending_target_addr.addr_type = m_known_devices[found_idx].addr_type;
            memcpy(m_pending_target_addr.addr, m_known_devices[found_idx].addr, BLE_GAP_ADDR_LEN);
            m_has_pending_connect = true;

            if (m_is_connected) {
                sd_ble_gap_disconnect(m_current_conn_handle, BLE_HCI_REMOTE_USER_TERMINATED_CONNECTION);
            }
            return;
        }

        nrf_ble_scan_stop();
        m_scan_active = false;

        ble_gap_addr_t target_addr;
        memset(&target_addr, 0, sizeof(target_addr));
        target_addr.addr_id_peer = 0;
        target_addr.addr_type = m_known_devices[found_idx].addr_type;
        memcpy(target_addr.addr, m_known_devices[found_idx].addr, BLE_GAP_ADDR_LEN);

        ret_code_t err_code = sd_ble_gap_connect(&target_addr,
                                                 &m_scan.scan_params,
                                                 &m_scan.conn_params,
                                                 APP_BLE_CONN_CFG_TAG);
        
        if (err_code != NRF_SUCCESS)
        {
            m_is_connecting = false;
            scan_start();
        }
        else
        {
            m_is_connecting = true;
            m_last_disconnect_reason = APP_BLE_CENTRAL_DISCONNECT_REASON_NONE;
            ble_state_update(protobuf_BLE_STATE_CONNECTING);
        }
    }
    else
    {
        NRF_LOG_WARNING("API: MAC address not found in scanned list.");
    }
}

void app_ble_central_disconnect(void)
{
    if (m_is_connected && m_current_conn_handle != BLE_CONN_HANDLE_INVALID)
    {
        m_user_disconnect_requested = true;
        ret_code_t err_code = sd_ble_gap_disconnect(m_current_conn_handle, BLE_HCI_REMOTE_USER_TERMINATED_CONNECTION);
        if (err_code != NRF_SUCCESS)
        {
            /* No DISCONNECTED event will follow; don't leave the flag armed
             * for an unrelated future disconnect. */
            m_user_disconnect_requested = false;
        }
    }
}

void app_ble_central_conn_params_set(uint16_t min_interval_ms, uint16_t max_interval_ms, uint16_t slave_latency, uint16_t conn_sup_timeout_ms)
{
    if (m_is_connected && m_current_conn_handle != BLE_CONN_HANDLE_INVALID)
    {
        central_update_conn_params(m_current_conn_handle, min_interval_ms, max_interval_ms, slave_latency, conn_sup_timeout_ms);
    }
}

bool app_ble_central_conn_params_get(uint16_t *min_ms, uint16_t *max_ms, uint16_t *lat, uint16_t *to_ms)
{
    if (m_is_connected && m_current_conn_handle != BLE_CONN_HANDLE_INVALID)
    {
        *min_ms = (m_current_conn_params.min_conn_interval * 125) / 100;
        *max_ms = (m_current_conn_params.max_conn_interval * 125) / 100;
        *lat = m_current_conn_params.slave_latency;
        *to_ms = m_current_conn_params.conn_sup_timeout * 10;
        return true;
    }
    return false;
}

uint8_t app_ble_central_status_get(void)
{
    return m_current_ble_state;
}

int32_t app_ble_central_rssi_dbm_get(void)
{
    return m_last_rssi_dbm;
}

uint32_t app_ble_central_disconnect_reason_get(void)
{
    return m_last_disconnect_reason;
}

uint32_t app_ble_central_send_data(uint8_t const *p_data, uint16_t length)
{
    if (!m_is_connected || !m_nus_ready ||
        m_current_conn_handle == BLE_CONN_HANDLE_INVALID)
    {
        NRF_LOG_WARNING("BLE Central TX rejected: GAP/NUS link is not ready");
        return NRF_ERROR_INVALID_STATE;
    }

    if (p_data == NULL || length == 0)
    {
        return NRF_ERROR_NULL;
    }

    NRF_LOG_INFO("Forwarding %u bytes over BLE to peripheral...", length);

    uint16_t current_payload_mtu = nrf_ble_gatt_eff_mtu_get(&m_gatt, m_current_conn_handle);
    if (current_payload_mtu < 3 || current_payload_mtu > SYSTEM_CONFIG_MTU_SIZE)
    {
        current_payload_mtu = SYSTEM_CONFIG_MTU_SIZE;
    }
    current_payload_mtu -= 3; // ATT header

    if (length > current_payload_mtu)
    {
        NRF_LOG_WARNING("BLE Central TX rejected: protobuf len=%u exceeds payload MTU=%u",
                        (unsigned)length,
                        (unsigned)current_payload_mtu);
        return NRF_ERROR_DATA_SIZE;
    }

    ble_gattc_write_params_t write_params;
    memset(&write_params, 0, sizeof(write_params));
    write_params.write_op = BLE_GATT_OP_WRITE_CMD;
    write_params.flags    = BLE_GATT_EXEC_WRITE_FLAG_PREPARED_WRITE;
    write_params.handle   = m_ble_nus_c.handles.nus_rx_handle;
    write_params.offset   = 0;
    write_params.len      = length;
    write_params.p_value  = p_data;

    ret_code_t err_code =
        sd_ble_gattc_write(m_current_conn_handle, &write_params);
    if (err_code == NRF_ERROR_RESOURCES)
    {
        return err_code;
    }
    if (err_code != NRF_SUCCESS)
    {
        NRF_LOG_ERROR("BLE Central Send Failed! Code: 0x%x",
                      (unsigned int)err_code);
        return err_code;
    }

    NRF_LOG_INFO("BLE Central NUS TX queued: %u bytes", length);
    m_pending_tx_chunks++;

    return NRF_SUCCESS;
}

/* End of file ------------------------------------------------------------- */
