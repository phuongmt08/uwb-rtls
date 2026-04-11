/**
 * @file       ble_central.c
 * @version    2.0.0
 * @date       2025-3-14
 * @author     Phuong Mai
 * @brief      BLE Central Application
 */
/* Includes ----------------------------------------------------------- */;
#include "app_ble_central.h"

#include "boards.h"
#include "bsp.h"
#include "bsp_btn_ble.h"
#include "ble.h"
#include "ble_hci.h"
#include "ble_advertising.h"
#include "ble_advdata.h"
#include "ble_conn_params.h"
#include "ble_db_discovery.h"
#include "ble_lbs_c.h"

#include "../../ble_common/ble_config.h"

#include "nrf_ble_gatt.h"
#include "nrf_ble_scan.h"
#include "nrf_log.h"
#include "nrf_log_ctrl.h"
#include "nrf_sdh.h"
#include "nrf_sdh_ble.h"


/* Private defines ---------------------------------------------------- */
#define CENTRAL_SCANNING_LED            BSP_BOARD_LED_0
#define CENTRAL_CONNECTED_LED           BSP_BOARD_LED_2

#define SCAN_INTERVAL                   0x00A0                              /**< Determines scan interval in units of 0.625 millisecond. */
#define SCAN_WINDOW                     0x0050                              /**< Determines scan window in units of 0.625 millisecond. */
#define SCAN_DURATION                   0x0000  

#define MIN_CONNECTION_INTERVAL         MSEC_TO_UNITS(7.5, UNIT_1_25_MS)    /**< Determines minimum connection interval in milliseconds. */
#define MAX_CONNECTION_INTERVAL         MSEC_TO_UNITS(30, UNIT_1_25_MS)     /**< Determines maximum connection interval in milliseconds. */
#define SLAVE_LATENCY                   0                                   /**< Determines slave latency in terms of connection events. */
#define SUPERVISION_TIMEOUT             MSEC_TO_UNITS(4000, UNIT_10_MS)     /**< Determines supervision time-out in units of 10 milliseconds. */

#define APP_BLE_CONN_CFG_TAG            1                                   /**< A tag identifying the SoftDevice BLE configuration. */
#define APP_BLE_OBSERVER_PRIO           3                                   /**< Application's BLE observer priority. You shouldn't need to modify this value. */

/* Private instances -------------------------------------------------- */
NRF_BLE_SCAN_DEF(m_scan);                                       /**< Scanning module instance. */
BLE_LBS_C_DEF(m_ble_lbs_c);                                     /**< Main structure used by the LBS client module. */
NRF_BLE_GATT_DEF(m_gatt);                                       /**< GATT module instance. */
BLE_DB_DISCOVERY_DEF(m_db_disc);                                /**< DB discovery module instance. */
NRF_BLE_GQ_DEF(m_ble_gatt_queue,                                /**< BLE GATT Queue instance. */
               NRF_SDH_BLE_CENTRAL_LINK_COUNT,
               NRF_BLE_GQ_QUEUE_SIZE);

static ble_uuid_t const m_target_periph_uuid =
{
    .uuid = SYSTEM_CONFIG_SERVICE_UUID,
    .type = BLE_UUID_TYPE_BLE,
};

/* Private variables -------------------------------------------------- */

/* Private prototypes ------------------------------------------------- */
static void ble_evt_handler(ble_evt_t const * p_ble_evt, void * p_context);
static void scan_evt_handler(scan_evt_t const * p_scan_evt);
static void db_disc_handler(ble_db_discovery_evt_t * p_evt);
static void scan_start(void);
static void lbs_c_init(void);
static void leds_init(void);

static void scan_report_log(ble_gap_evt_adv_report_t const * p_adv_report, bool matched)
{
    char     dev_name[NRF_BLE_SCAN_NAME_MAX_LEN + 1] = "<no_name>";
    uint16_t offset                                   = 0;
    uint16_t field_len;

    field_len = ble_advdata_search(p_adv_report->data.p_data,
                                   p_adv_report->data.len,
                                   &offset,
                                   BLE_GAP_AD_TYPE_COMPLETE_LOCAL_NAME);

    if (field_len == 0)
    {
        offset = 0;
        field_len = ble_advdata_search(p_adv_report->data.p_data,
                                       p_adv_report->data.len,
                                       &offset,
                                       BLE_GAP_AD_TYPE_SHORT_LOCAL_NAME);
    }

    if (field_len > 0)
    {
        uint16_t copy_len = MIN(field_len, NRF_BLE_SCAN_NAME_MAX_LEN);
        memcpy(dev_name, &p_adv_report->data.p_data[offset], copy_len);
        dev_name[copy_len] = '\0';
    }

    NRF_LOG_INFO("SCAN %s RSSI=%d Name=%s",
                 matched ? "MATCH" : "SEEN",
                 p_adv_report->rssi,
                 nrf_log_push(dev_name));
    NRF_LOG_INFO("MAC=%02x:%02x:%02x:%02x:%02x:%02x",
                 p_adv_report->peer_addr.addr[5],
                 p_adv_report->peer_addr.addr[4],
                 p_adv_report->peer_addr.addr[3],
                 p_adv_report->peer_addr.addr[2],
                 p_adv_report->peer_addr.addr[1],
                 p_adv_report->peer_addr.addr[0]);
}

/* Private functions -------------------------------------------------- */
static void scan_start(void)
{
    ret_code_t err_code;

    err_code = nrf_ble_scan_start(&m_scan);
    APP_ERROR_CHECK(err_code);

    bsp_board_led_off(CENTRAL_CONNECTED_LED);
    bsp_board_led_on(CENTRAL_SCANNING_LED);
}

static void leds_init(void)
{
    bsp_board_init(BSP_INIT_LEDS);
}

static void lbs_error_handler(uint32_t nrf_error)
{
    APP_ERROR_HANDLER(nrf_error);
}

static void lbs_c_evt_handler(ble_lbs_c_t * p_lbs_c, ble_lbs_c_evt_t * p_lbs_c_evt)
{
    switch (p_lbs_c_evt->evt_type)
    {
        case BLE_LBS_C_EVT_DISCOVERY_COMPLETE:
        {
            ret_code_t err_code;

            err_code = ble_lbs_c_handles_assign(&m_ble_lbs_c,
                                                p_lbs_c_evt->conn_handle,
                                                &p_lbs_c_evt->params.peer_db);
            APP_ERROR_CHECK(err_code);

            err_code = ble_lbs_c_button_notif_enable(p_lbs_c);
            APP_ERROR_CHECK(err_code);
        } break;

        case BLE_LBS_C_EVT_BUTTON_NOTIFICATION:
            NRF_LOG_INFO("Peer button state: 0x%x.", p_lbs_c_evt->params.button.button_state);
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

/**@brief Function for handling BLE events.
 *
 * @param[in]   p_ble_evt   Bluetooth stack event.
 * @param[in]   p_context   Unused.
 */
static void ble_evt_handler(ble_evt_t const * p_ble_evt, void * p_context)
{
    ret_code_t err_code;

    // For readability.
    ble_gap_evt_t const * p_gap_evt = &p_ble_evt->evt.gap_evt;

    switch (p_ble_evt->header.evt_id)
    {
        // Upon connection, check which peripheral has connected (HR or RSC), initiate DB
        // discovery, update LEDs status and resume scanning if necessary. */
        case BLE_GAP_EVT_CONNECTED:
        {
            NRF_LOG_INFO("Connected. conn_handle=0x%x", p_gap_evt->conn_handle);

            bsp_board_led_on(CENTRAL_CONNECTED_LED);
            bsp_board_led_off(CENTRAL_SCANNING_LED);

            err_code = ble_lbs_c_handles_assign(&m_ble_lbs_c, p_gap_evt->conn_handle, NULL);
            if (err_code != NRF_SUCCESS)
            {
                NRF_LOG_WARNING("ble_lbs_c_handles_assign failed: 0x%08x", err_code);
            }
            else
            {
                NRF_LOG_INFO("ble_lbs_c_handles_assign ok");
            }

            err_code = ble_db_discovery_start(&m_db_disc, p_gap_evt->conn_handle);
            if (err_code != NRF_SUCCESS)
            {
                NRF_LOG_WARNING("ble_db_discovery_start failed: 0x%08x", err_code);
            }
            else
            {
                NRF_LOG_INFO("ble_db_discovery_start ok");
            }

        } break;

        // Upon disconnection, reset the connection handle of the peer which disconnected, update
        // the LEDs status and start scanning again.
        case BLE_GAP_EVT_DISCONNECTED:
        {
            NRF_LOG_INFO("Disconnected. reason=0x%x", p_gap_evt->params.disconnected.reason);
            scan_start();
        } break;

        case BLE_GAP_EVT_TIMEOUT:
        {
            // We have not specified a timeout for scanning, so only connection attemps can timeout.
            if (p_gap_evt->params.timeout.src == BLE_GAP_TIMEOUT_SRC_CONN)
            {
                NRF_LOG_DEBUG("Connection request timed out.");
            }
        } break;

        case BLE_GAP_EVT_CONN_PARAM_UPDATE_REQUEST:
        {
            // Accept parameters requested by peer.
            err_code = sd_ble_gap_conn_param_update(p_gap_evt->conn_handle,
                                        &p_gap_evt->params.conn_param_update_request.conn_params);
            APP_ERROR_CHECK(err_code);
        } break;

        case BLE_GAP_EVT_PHY_UPDATE_REQUEST:
        {
            // NRF_LOG_DEBUG("PHY update request.");
            ble_gap_phys_t const phys =
            {
                .rx_phys = BLE_GAP_PHY_AUTO,
                .tx_phys = BLE_GAP_PHY_AUTO,
            };
            err_code = sd_ble_gap_phy_update(p_ble_evt->evt.gap_evt.conn_handle, &phys);
            APP_ERROR_CHECK(err_code);
        } break;

        case BLE_GATTC_EVT_TIMEOUT:
        {
            // Disconnect on GATT Client timeout event.
            // NRF_LOG_DEBUG("GATT Client Timeout.");
            err_code = sd_ble_gap_disconnect(p_ble_evt->evt.gattc_evt.conn_handle,
                                             BLE_HCI_REMOTE_USER_TERMINATED_CONNECTION);
            APP_ERROR_CHECK(err_code);
        } break;

        case BLE_GATTS_EVT_TIMEOUT:
        {
            // Disconnect on GATT Server timeout event.
            // NRF_LOG_DEBUG("GATT Server Timeout.");
            err_code = sd_ble_gap_disconnect(p_ble_evt->evt.gatts_evt.conn_handle,
                                             BLE_HCI_REMOTE_USER_TERMINATED_CONNECTION);
            APP_ERROR_CHECK(err_code);
        } break;

        default:
            // No implementation needed.
            break;
    }
}
static void scan_evt_handler(scan_evt_t const * p_scan_evt)
{
    ret_code_t err_code;

    switch(p_scan_evt->scan_evt_id)
    {
        case NRF_BLE_SCAN_EVT_FILTER_MATCH:
            scan_report_log(p_scan_evt->params.filter_match.p_adv_report, true);
            break;

        case NRF_BLE_SCAN_EVT_NOT_FOUND:
            scan_report_log(p_scan_evt->params.p_not_found, false);
            break;

        case NRF_BLE_SCAN_EVT_CONNECTING_ERROR:
            err_code = p_scan_evt->params.connecting_err.err_code;
            APP_ERROR_CHECK(err_code);
            break;
        default:
          break;
    }
}

/**@brief Function for handling database discovery events.
 *
 * @details This function is callback function to handle events from the database discovery module.
 *          Depending on the UUIDs that are discovered, this function should forward the events
 *          to their respective services.
 *
 * @param[in] p_event  Pointer to the database discovery event.
 */
static void db_disc_handler(ble_db_discovery_evt_t * p_evt)
{
    ble_lbs_on_db_disc_evt(&m_ble_lbs_c, p_evt);
}



/* Public functions ------------------------------------------------ */


/**@brief Function for initializing the BLE stack.
 *
 * @details Initializes the SoftDevice and the BLE event interrupts.
 */
void ble_stack_init(void)
{
    ret_code_t err_code;

    NRF_LOG_INFO("BLE: nrf_sdh_enable_request");
    err_code = nrf_sdh_enable_request();
    if (err_code != NRF_SUCCESS)
    {
        NRF_LOG_ERROR("BLE: nrf_sdh_enable_request failed: 0x%08x", err_code);
        NRF_LOG_FLUSH();
    }
    APP_ERROR_CHECK(err_code);

    // Configure the BLE stack using the default settings.
    // Fetch the start address of the application RAM.
    uint32_t ram_start = 0;
    NRF_LOG_INFO("BLE: nrf_sdh_ble_default_cfg_set");
    err_code = nrf_sdh_ble_default_cfg_set(APP_BLE_CONN_CFG_TAG, &ram_start);
    if (err_code != NRF_SUCCESS)
    {
        NRF_LOG_ERROR("BLE: default_cfg_set failed: 0x%08x", err_code);
        NRF_LOG_FLUSH();
    }
    else
    {
        NRF_LOG_INFO("BLE: RAM start required by SD = 0x%08x", ram_start);
    }
    APP_ERROR_CHECK(err_code);

    // Enable BLE stack.
    NRF_LOG_INFO("BLE: nrf_sdh_ble_enable");
    err_code = nrf_sdh_ble_enable(&ram_start);
    if (err_code != NRF_SUCCESS)
    {
        NRF_LOG_ERROR("BLE: nrf_sdh_ble_enable failed: 0x%08x", err_code);
        NRF_LOG_FLUSH();
    }
    APP_ERROR_CHECK(err_code);

    // Register a handler for BLE events.
    NRF_SDH_BLE_OBSERVER(m_ble_observer, APP_BLE_OBSERVER_PRIO, ble_evt_handler, NULL);
}
/**@brief Database discovery initialization.
 */
void db_discovery_init(void)
{
    ble_db_discovery_init_t db_init;

    memset(&db_init, 0, sizeof(db_init));

    db_init.evt_handler  = db_disc_handler;
    db_init.p_gatt_queue = &m_ble_gatt_queue;

    ret_code_t err_code = ble_db_discovery_init(&db_init);
    if (err_code != NRF_SUCCESS)
    {
        NRF_LOG_ERROR("BLE: ble_db_discovery_init failed: 0x%08x", err_code);
        NRF_LOG_FLUSH();
    }
    APP_ERROR_CHECK(err_code);
}

/**@brief Initialize BLE central application.
 *
 * This function calls all necessary BLE initialization functions in order.
 */
void ble_central_init(void)
{
    NRF_LOG_INFO("BLE: ble_stack_init");
    ble_stack_init();
    NRF_LOG_INFO("BLE: gatt_init");
    gatt_init();
    NRF_LOG_INFO("BLE: leds_init");
    leds_init();
    NRF_LOG_INFO("BLE: db_discovery_init");
    db_discovery_init();
    NRF_LOG_INFO("BLE: lbs_c_init");
    lbs_c_init();
    NRF_LOG_INFO("BLE: scan_init");
    scan_init();
    NRF_LOG_INFO("BLE: scan_start");
    scan_start();
}

void scan_init(void)
{
    ret_code_t          err_code;
    nrf_ble_scan_init_t init_scan;

    memset(&init_scan, 0, sizeof(init_scan));

    init_scan.connect_if_match = true;
    init_scan.conn_cfg_tag     = APP_BLE_CONN_CFG_TAG;

    err_code = nrf_ble_scan_init(&m_scan, &init_scan, scan_evt_handler);
    if (err_code != NRF_SUCCESS)
    {
        NRF_LOG_ERROR("BLE: nrf_ble_scan_init failed: 0x%08x", err_code);
        NRF_LOG_FLUSH();
    }
    APP_ERROR_CHECK(err_code);

    // Setting filters for scanning.
    err_code = nrf_ble_scan_filter_set(&m_scan, SCAN_UUID_FILTER, &m_target_periph_uuid);
    if (err_code != NRF_SUCCESS)
    {
        NRF_LOG_ERROR("BLE: scan_filter_set failed: 0x%08x", err_code);
        NRF_LOG_FLUSH();
    }
    APP_ERROR_CHECK(err_code);

    err_code = nrf_ble_scan_filters_enable(&m_scan, NRF_BLE_SCAN_UUID_FILTER, false);
    if (err_code != NRF_SUCCESS)
    {
        NRF_LOG_ERROR("BLE: scan_filters_enable failed: 0x%08x", err_code);
        NRF_LOG_FLUSH();
    }
    APP_ERROR_CHECK(err_code);
}




/**@brief Function for initializing the GATT module.
 */
void gatt_init(void)
{
    ret_code_t err_code = nrf_ble_gatt_init(&m_gatt, NULL);
    if (err_code != NRF_SUCCESS)
    {
        NRF_LOG_ERROR("BLE: nrf_ble_gatt_init failed: 0x%08x", err_code);
        NRF_LOG_FLUSH();
    }
    APP_ERROR_CHECK(err_code);
}
/* End of file -------------------------------------------------------- */