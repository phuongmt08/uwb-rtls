/**
 * @file    app_ble_central.c
 * @version 2.1.0
 * @date    2025-04-12
 * @author  Phuong Mai
 * @brief   BLE Central Application
 *
 * Handles BLE stack initialization, scanning, connection management,
 * LBS client, and terminal UI over USB CDC ACM.
 *
 * Dependencies (BSP layer):
 *  - bsp_led   : LED state control
 *  - bsp_usbd  : USB CDC ACM write
 */

/* Includes ---------------------------------------------------------------- */
#include "app_ble_central.h"

#include <stdio.h>
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
#include "app_util.h"

#include "../bsp/bsp_led.h"
#include "../bsp/bsp_usbd.h"
#include "../../ble_common/ble_bridge/bb_cmd_hdl.h"

/* Private defines --------------------------------------------------------- */
#define SCAN_INTERVAL            0x00A0  /**< Scan interval  (units of 0.625 ms). */
#define SCAN_WINDOW              0x0050  /**< Scan window    (units of 0.625 ms). */
#define SCAN_DURATION            0x0000  /**< 0 = scan indefinitely.              */

#define APP_BLE_CONN_CFG_TAG     1  /**< SoftDevice BLE configuration tag. */
#define APP_BLE_OBSERVER_PRIO    3  /**< BLE observer priority.            */

#define MAX_KNOWN_DEVICES        15 /**< Maximum devices tracked in scan list. */

/* -------------------------------------------------------------------------
 * Debug helpers
 * ---------------------------------------------------------------------- */

/**
 * @brief Map a BLE HCI disconnect reason code to a human-readable string.
 *
 * Covers the most common reasons seen in practice.  Unknown codes are
 * printed as a raw hex number by the caller.
 */
static const char * ble_hci_reason_str(uint8_t reason)
{
    switch (reason)
    {
        case BLE_HCI_STATUS_CODE_SUCCESS:                               return "Success";
        case BLE_HCI_REMOTE_USER_TERMINATED_CONNECTION:                 return "Remote user terminated";
        case BLE_HCI_REMOTE_DEV_TERMINATION_DUE_TO_LOW_RESOURCES:       return "Remote: low resources";
        case BLE_HCI_REMOTE_DEV_TERMINATION_DUE_TO_POWER_OFF:           return "Remote: power off";
        case BLE_HCI_LOCAL_HOST_TERMINATED_CONNECTION:                  return "Local host terminated";
        case BLE_HCI_CONN_INTERVAL_UNACCEPTABLE:                        return "Conn interval unacceptable";
        case BLE_HCI_CONNECTION_TIMEOUT:                                return "Connection timeout";
        case BLE_HCI_CONN_FAILED_TO_BE_ESTABLISHED:                     return "Failed to establish";
        default:                                                        return "Unknown";
    }
}

/* Private types ----------------------------------------------------------- */

/** Information record for one discovered BLE peripheral. */
typedef struct
{
    bool     active;                           /**< Slot is occupied.           */
    uint8_t  addr[BLE_GAP_ADDR_LEN];           /**< Peer MAC address.           */
    uint8_t  addr_type;                        /**< Peer MAC address type.      */
    char     name[NRF_BLE_SCAN_NAME_MAX_LEN + 1]; /**< Advertised device name. */
    uint16_t uuid;                             /**< First 16-bit service UUID.  */
    int8_t   rssi;                             /**< Last observed RSSI (dBm).   */
    uint32_t last_seen;                        /**< app_timer tick of last adv. */
} known_device_t;

/* Private instances ------------------------------------------------------- */
NRF_BLE_SCAN_DEF(m_scan);         /**< Scanning module instance.     */
BLE_LBS_C_DEF(m_ble_lbs_c);       /**< LBS client module instance.   */
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
static known_device_t m_connected_device_info;            /**< Currently connected peer. */
static bool           m_is_connected = false;             /**< Connection flag.          */
static bool           m_is_connecting = false;            /**< Connecting flag.          */

static bool           m_has_pending_connect = false;
static ble_gap_addr_t m_pending_target_addr;

static uint16_t       m_current_conn_handle = BLE_CONN_HANDLE_INVALID;
static ble_gap_conn_params_t m_current_conn_params;

/* Private prototypes ------------------------------------------------------ */
static void ble_evt_handler(ble_evt_t const *p_ble_evt, void *p_context);
static void scan_evt_handler(scan_evt_t const *p_scan_evt);
static void db_disc_handler(ble_db_discovery_evt_t *p_evt);
static void scan_start(void);
static void lbs_c_init(void);
static void scan_report_log(ble_gap_evt_adv_report_t const *p_adv_report, bool matched);

void app_ble_central_scan_start(uint16_t interval_ms, uint16_t window_ms, uint16_t duration_ms, bool active)
{
    nrf_ble_scan_stop();
    if (interval_ms > 0 && window_ms > 0) {
        m_scan.scan_params.interval = MSEC_TO_UNITS(interval_ms, UNIT_0_625_MS);
        m_scan.scan_params.window   = MSEC_TO_UNITS(window_ms, UNIT_0_625_MS);
        m_scan.scan_params.timeout  = duration_ms / 10;
        m_scan.scan_params.active   = active ? 1 : 0;
    }
    ret_code_t err_code = nrf_ble_scan_start(&m_scan);
    if (err_code == NRF_SUCCESS) {
        bsp_led_scanning();
        m_is_connecting = false;
        m_has_pending_connect = false;
    }
}

void app_ble_central_scan_stop(void)
{
    nrf_ble_scan_stop();
    // TODO: Add bsp led off if there's an API, usually bsp_led library covers state changes automatically
}

/* -------------------------------------------------------------------------
 * UI — Terminal output over USB CDC ACM
 * ---------------------------------------------------------------------- */

/**
 * @brief Redraw the device list and connection status on the terminal.
 *
 * Uses ANSI escape codes to clear the screen before printing so that the
 * output appears as an always-current, in-place list rather than scrolling.
 */
static void app_ble_ui_redraw(void)
{
    if (!bsp_usbd_is_connected())
    {
        return;
    }

    static char tx_buf[1024];
    int len = 0;

    /* Clear screen and home cursor, then print header. */
    len += snprintf(tx_buf + len, sizeof(tx_buf) - len, "\033[2J\033[H=== BLE DEVICES LIST ===\r\n");

    /* Print all currently active (recently seen) devices. */
    for (int i = 0; i < MAX_KNOWN_DEVICES; i++)
    {
        if (!m_known_devices[i].active)
        {
            continue;
        }

        int written = snprintf(tx_buf + len, sizeof(tx_buf) - len,
                               "<%s><%04X><%02x:%02x:%02x:%02x:%02x:%02x><%d dBm>\r\n",
                               m_known_devices[i].name,
                               m_known_devices[i].uuid,
                               m_known_devices[i].addr[5], m_known_devices[i].addr[4],
                               m_known_devices[i].addr[3], m_known_devices[i].addr[2],
                               m_known_devices[i].addr[1], m_known_devices[i].addr[0],
                               m_known_devices[i].rssi);

        if (written > 0 && len + written < (int)sizeof(tx_buf))
        {
            len += written;
        }
        else
        {
            break; /* Buffer full — skip remaining entries. */
        }
    }

    /* Print the connected device section if a connection is active. */
    if (m_is_connected)
    {
        int w = snprintf(tx_buf + len, sizeof(tx_buf) - len,
                         "\r\n==== BLE CONNECT ====\r\n"
                         "<%s><%04X><%02x:%02x:%02x:%02x:%02x:%02x><%d dBm>\r\n",
                         m_connected_device_info.name,
                         m_connected_device_info.uuid,
                         m_connected_device_info.addr[5], m_connected_device_info.addr[4],
                         m_connected_device_info.addr[3], m_connected_device_info.addr[2],
                         m_connected_device_info.addr[1], m_connected_device_info.addr[0],
                         m_connected_device_info.rssi);

        if (w > 0 && len + w < (int)sizeof(tx_buf))
        {
            len += w;
        }
    }

    bsp_usbd_write((const uint8_t *)tx_buf, len);
}

/* -------------------------------------------------------------------------
 * Scan report processing
 * ---------------------------------------------------------------------- */

/**
 * @brief Process one advertising report and refresh the known-device list.
 *
 * Extracts the device name and 16-bit service UUID from the advertisement
 * payload, updates the tracking table, and triggers a terminal redraw only
 * when something meaningful changed.
 *
 * @param[in] p_adv_report  Pointer to the raw advertising report.
 * @param[in] matched       true if the report matched the scan filter.
 */
static void scan_report_log(ble_gap_evt_adv_report_t const *p_adv_report, bool matched)
{
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

    /* ----- Expire stale entries (> 5 s since last adv) ------------------ */
    uint32_t current_ticks = app_timer_cnt_get();
    bool     should_redraw = false;

    for (int i = 0; i < MAX_KNOWN_DEVICES; i++)
    {
        if (!m_known_devices[i].active)
        {
            continue;
        }

        uint32_t diff = (current_ticks - m_known_devices[i].last_seen) & 0x00FFFFFF;
        if (diff > APP_TIMER_TICKS(5000))
        {
            m_known_devices[i].active = false;
            should_redraw = true; /* Device timed out — refresh list. */
        }
    }

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
            if (should_redraw) 
            {
                app_ble_ui_redraw();
            }
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
        should_redraw = true;                                                               /* New device appeared. */
        
        /* Notify via protobuf / serial */
        bb_cmd_notify_scan_result(m_known_devices[known_idx].addr, m_known_devices[known_idx].rssi, m_known_devices[known_idx].name, 0);

    }
    else
    {
        /* ----- Known device — update fields if improved ----------------- */
        if (strcmp(m_known_devices[known_idx].name, "<no_name>") == 0 &&
            strcmp(dev_name, "<no_name>") != 0)
        {
            strncpy(m_known_devices[known_idx].name, dev_name, NRF_BLE_SCAN_NAME_MAX_LEN + 1);
            should_redraw = true; /* Name resolved — refresh list. */
        }
        
        if (m_known_devices[known_idx].rssi != p_adv_report->rssi) {
            m_known_devices[known_idx].rssi = p_adv_report->rssi;
            
            /* Notify via protobuf / serial explicitly on rssi change */
            bb_cmd_notify_scan_result(m_known_devices[known_idx].addr, m_known_devices[known_idx].rssi, m_known_devices[known_idx].name, 0);
        }

        if (m_known_devices[known_idx].uuid == 0 && uuid16 != 0)
        {
            m_known_devices[known_idx].uuid = uuid16;
            should_redraw = true; /* UUID resolved — refresh list. */
        }

        /* Always refresh RSSI and heartbeat timestamp. */
        m_known_devices[known_idx].rssi      = p_adv_report->rssi;
        m_known_devices[known_idx].last_seen = current_ticks;
    }

    if (should_redraw)
    {
        app_ble_ui_redraw();
    }
}

/* -------------------------------------------------------------------------
 * Private BLE helpers
 * ---------------------------------------------------------------------- */

static void scan_start(void)
{
    ret_code_t err_code = nrf_ble_scan_start(&m_scan);
    APP_ERROR_CHECK(err_code);

    bsp_led_scanning();
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

/* -------------------------------------------------------------------------
 * Terminal Command logic
 * ---------------------------------------------------------------------- */

static void cmd_connect_to_mac(const char *cmd)
{
    unsigned int mac[6];
    /* sscanf securely parses hex whether they are lowercase or uppercase */
    if (sscanf(cmd, "%x:%x:%x:%x:%x:%x", &mac[5], &mac[4], &mac[3], &mac[2], &mac[1], &mac[0]) == 6)
    {
        uint8_t target_mac[6];
        for (int i = 0; i < 6; i++) {
            target_mac[i] = (uint8_t)mac[i];
        }

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
            NRF_LOG_INFO("CMD: Connecting to MAC: %02X:%02X:%02X:%02X:%02X:%02X",
                         target_mac[5], target_mac[4], target_mac[3],
                         target_mac[2], target_mac[1], target_mac[0]);

            if (m_is_connected || m_is_connecting || m_current_conn_handle != BLE_CONN_HANDLE_INVALID)
            {
                /* Queue the new target address */
                memset(&m_pending_target_addr, 0, sizeof(m_pending_target_addr));
                m_pending_target_addr.addr_id_peer = 0;
                m_pending_target_addr.addr_type = m_known_devices[found_idx].addr_type;
                memcpy(m_pending_target_addr.addr, m_known_devices[found_idx].addr, BLE_GAP_ADDR_LEN);
                m_has_pending_connect = true;

                if (m_is_connected) {
                    sd_ble_gap_disconnect(m_current_conn_handle, BLE_HCI_REMOTE_USER_TERMINATED_CONNECTION);
                }
                
                NRF_LOG_INFO("CMD: Disconnecting current session... Will auto-connect to new MAC.");
                return;
            }

            nrf_ble_scan_stop();

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
                NRF_LOG_WARNING("CMD: sd_ble_gap_connect failed: 0x%08x", err_code);
                m_is_connecting = false;
                scan_start();
            }
            else
            {
                m_is_connecting = true;
            }
        }
        else
        {
            NRF_LOG_WARNING("CMD: MAC address %s not found in scanned list.", cmd);
        }
    }
}

static void on_usb_rx_line(const char *line)
{
    uint16_t min_int, max_int, latency, timeout;

    /* Parse dynamic config command 
     * Format: "config <min_ms> <max_ms> <latency> <timeout_ms>"
     * Example: "config 50 75 0 4000"
     */
    if (sscanf(line, "config %hu %hu %hu %hu", &min_int, &max_int, &latency, &timeout) == 4)
    {
        if (m_is_connected && m_current_conn_handle != BLE_CONN_HANDLE_INVALID)
        {
            NRF_LOG_INFO("CMD: Config request min=%u, max=%u, lat=%u, to=%u", 
                         min_int, max_int, latency, timeout);
            central_update_conn_params(m_current_conn_handle, min_int, max_int, latency, timeout);
        }
        else
        {
            NRF_LOG_WARNING("CMD: Cannot update params, not connected!");
        }
        return;
    }

    /* Parse MAC address connection request
     * Example: "69:3E:39:55:AE:BE" 
     */
    if (strlen(line) >= 17) /* minimum length of MAC */
    {
        cmd_connect_to_mac(line);
    }
}

void button_init(void)
{
    /* We don't use button for cycling anymore, wire the USB terminal RX callback */
    bsp_usbd_rx_line_cb_set(on_usb_rx_line);
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
            m_current_conn_handle = p_gap_evt->conn_handle;
            m_current_conn_params = p_gap_evt->params.connected.conn_params;

            /* Alert PC about connection */
            bb_cmd_notify_ble_status(5 /* CONNECTED */, 0);

            /* Copy device info from the scan list into the connected record. */
            int connected_idx = -1;
            for (int i = 0; i < MAX_KNOWN_DEVICES; i++)
            {
                if (m_known_devices[i].active &&
                    memcmp(m_known_devices[i].addr,
                           p_gap_evt->params.connected.peer_addr.addr,
                           BLE_GAP_ADDR_LEN) == 0)
                {
                    connected_idx = i;
                    break;
                }
            }

            if (connected_idx != -1)
            {
                m_connected_device_info = m_known_devices[connected_idx];
            }
            else
            {
                memset(&m_connected_device_info, 0, sizeof(m_connected_device_info));
                memcpy(m_connected_device_info.addr,
                       p_gap_evt->params.connected.peer_addr.addr,
                       BLE_GAP_ADDR_LEN);
                strcpy(m_connected_device_info.name, "<Unknown>");
            }

            app_ble_ui_redraw();

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
            err_code = nrf_ble_scan_start(&m_scan);
            if (err_code != NRF_SUCCESS)
            {
                NRF_LOG_WARNING("nrf_ble_scan_start failed: 0x%08x", err_code);
            }
        } break;

        /* ---- Disconnected -------------------------------------------- */
        case BLE_GAP_EVT_DISCONNECTED:
        {
            uint8_t reason = p_gap_evt->params.disconnected.reason;
            NRF_LOG_INFO("Disconnected. reason=0x%02x (%s)",
                         reason, ble_hci_reason_str(reason));

            m_is_connected = false;
            m_is_connecting = false;
            m_current_conn_handle = BLE_CONN_HANDLE_INVALID;
            app_ble_ui_redraw();

            /* Alert PC about disconnection */
            bb_cmd_notify_ble_status(1 /* IDLE */, 0);

            if (m_has_pending_connect)
            {
                m_has_pending_connect = false;
                NRF_LOG_INFO("CMD: Auto-connecting to queued MAC...");
                nrf_ble_scan_stop();
                
                err_code = sd_ble_gap_connect(&m_pending_target_addr,
                                              &m_scan.scan_params,
                                              &m_scan.conn_params,
                                              APP_BLE_CONN_CFG_TAG);
                if (err_code != NRF_SUCCESS)
                {
                    NRF_LOG_WARNING("CMD: Auto-connect failed: 0x%08x", err_code);
                    scan_start();
                }
                else
                {
                    m_is_connecting = true;
                }
            }
            else
            {
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
                bb_cmd_notify_ble_status(1 /* IDLE */, 0);
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
            APP_ERROR_CHECK(err_code);
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

        /* ---- GATT Client timeout ------------------------------------ */
        case BLE_GATTC_EVT_TIMEOUT:
        {
            err_code = sd_ble_gap_disconnect(p_ble_evt->evt.gattc_evt.conn_handle,
                                             BLE_HCI_REMOTE_USER_TERMINATED_CONNECTION);
            APP_ERROR_CHECK(err_code);
        } break;

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
    scan_param.interval      = SCAN_INTERVAL;
    scan_param.window        = SCAN_WINDOW;
    scan_param.filter_policy = BLE_GAP_SCAN_FP_ACCEPT_ALL;
    scan_param.timeout       = SCAN_DURATION;
    scan_param.scan_phys     = SYSTEM_CONFIG_PREFERRED_PHY;

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
    NRF_LOG_INFO("BLE: ble_stack_init");
    ble_stack_init();

    NRF_LOG_INFO("BLE: gatt_init");
    gatt_init();

    NRF_LOG_INFO("BLE: bsp_led_init");
    bsp_led_init();

    NRF_LOG_INFO("BLE: button_init");
    button_init();

    NRF_LOG_INFO("BLE: db_discovery_init");
    db_discovery_init();

    NRF_LOG_INFO("BLE: lbs_c_init");
    lbs_c_init();

    NRF_LOG_INFO("BLE: scan_init");
    scan_init();

    NRF_LOG_INFO("BLE: scan_start");
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
        sd_ble_gap_disconnect(m_current_conn_handle, BLE_HCI_REMOTE_USER_TERMINATED_CONNECTION);
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
    if (m_is_connected) {
        return 5; /* protobuf_BLE_STATE_CONNECTED */
    } else if (m_is_connecting) {
        return 4; /* protobuf_BLE_STATE_CONNECTING */
    } else {
        return 1; /* protobuf_BLE_STATE_IDLE */
    }
}

/* End of file ------------------------------------------------------------- */