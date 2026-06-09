#include "ble_peripheral.h"
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include "nordic_common.h"
#include "nrf.h"
#include "app_error.h"
#include "ble.h"
#include "ble_err.h"
#include "ble_hci.h"
#include "ble_srv_common.h"
#include "ble_advdata.h"
#include "ble_conn_params.h"
#include "nrf_sdh.h"
#include "nrf_sdh_ble.h"
#include "boards.h"
#include "app_timer.h"
#include "ble_lbs.h"
#include "nrf_ble_gatt.h"
#include "nrf_ble_qwr.h"
#include "bsp_utils.h"
#include "logger.h"
#include "nrf_log.h"
#include "ble_nus.h"
#include "../ble_common/ble_config.h"
#include "../ble_common/ble_broadcast.h"
#include "../../../protocol/protos/protocol.pb.h"

#define DEVICE_NAME                     SYSTEM_CONFIG_DEVICE_PREFIX "01"

#define APP_BLE_OBSERVER_PRIO           3
#define APP_BLE_CONN_CFG_TAG            1

#define APP_ADV_DURATION                BLE_GAP_ADV_TIMEOUT_GENERAL_UNLIMITED

#define FIRST_CONN_PARAMS_UPDATE_DELAY  APP_TIMER_TICKS(20000)
#define NEXT_CONN_PARAMS_UPDATE_DELAY   APP_TIMER_TICKS(5000)
#define MAX_CONN_PARAMS_UPDATE_COUNT    3

#define DEAD_BEEF                       0xDEADBEEF
#define BCAST_ADV_EVENTS_PER_FRAGMENT   3u

BLE_LBS_DEF(m_lbs);
NRF_BLE_GATT_DEF(m_gatt);
NRF_BLE_QWR_DEF(m_qwr);
BLE_NUS_DEF(m_nus, NRF_SDH_BLE_TOTAL_LINK_COUNT);

static bool m_is_initialized = false;
static bool m_is_advertising = false;

static uint16_t m_conn_handle = BLE_CONN_HANDLE_INVALID;
static uint8_t m_adv_handle = BLE_GAP_ADV_SET_HANDLE_NOT_SET;
static uint8_t m_enc_advdata_1[BLE_GAP_ADV_SET_DATA_SIZE_MAX];
static uint8_t m_enc_advdata_2[BLE_GAP_ADV_SET_DATA_SIZE_MAX];
static bool m_adv_buffer_toggle = false; // false -> buffer 1, true -> buffer 2
static uint8_t m_enc_scan_response_data[BLE_GAP_ADV_SET_DATA_SIZE_MAX];
static uint32_t m_pending_tx_chunks = 0;
#if BLE_BROADCAST_USE_EXTENDED
static uint8_t m_bcast_advdata[BLE_GAP_ADV_SET_DATA_SIZE_EXTENDED_MAX_SUPPORTED];
#else
static uint8_t m_bcast_advdata[BLE_GAP_ADV_SET_DATA_SIZE_MAX];
#endif
static uint8_t m_bcast_packet[BLE_BROADCAST_MAX_PACKET_SIZE];
static uint8_t m_bcast_packet_len = 0;
static uint8_t m_bcast_seq = 0;
static uint8_t m_bcast_frag_index = 0;
static uint8_t m_bcast_frag_count = 0;
static bool m_bcast_active = false;
static bool m_bcast_restore_advertising = false;

static ble_peripheral_rx_cb_t m_ble_rx_cb = NULL;

static ble_gap_adv_data_t m_adv_data =
{
    .adv_data =
    {
        .p_data = m_enc_advdata_1,
        .len    = BLE_GAP_ADV_SET_DATA_SIZE_MAX
    },
    .scan_rsp_data =
    {
        .p_data = m_enc_scan_response_data,
        .len    = BLE_GAP_ADV_SET_DATA_SIZE_MAX
    }
};

static ble_gap_adv_data_t m_bcast_adv_data =
{
    .adv_data =
    {
        .p_data = m_bcast_advdata,
        .len    = 0
    },
    .scan_rsp_data =
    {
        .p_data = NULL,
        .len    = 0
    }
};

#pragma pack(push, 1)
typedef struct {
    uint8_t  device_type;
    uint8_t  device_id;
    uint8_t  bat_soc_percent;
    uint16_t status_flags;
    uint8_t  warning_count;
    uint8_t  error_count;
    uint32_t timestamp_s;
} ble_adv_status_packed_t;
#pragma pack(pop)

void ble_peripheral_rx_cb_register(ble_peripheral_rx_cb_t cb)
{
    m_ble_rx_cb = cb;
}

void assert_nrf_callback(uint16_t line_num, const uint8_t * p_file_name)
{
    app_error_handler(DEAD_BEEF, line_num, p_file_name);
}

static void gap_params_init(void)
{
    ret_code_t              err_code;
    ble_gap_conn_params_t   gap_conn_params;
    ble_gap_conn_sec_mode_t sec_mode;

    BLE_GAP_CONN_SEC_MODE_SET_OPEN(&sec_mode);

    err_code = sd_ble_gap_device_name_set(&sec_mode,
                                          (const uint8_t *)DEVICE_NAME,
                                          strlen(DEVICE_NAME));
    APP_ERROR_CHECK(err_code);

    memset(&gap_conn_params, 0, sizeof(gap_conn_params));

    gap_conn_params.min_conn_interval = SYSTEM_CONFIG_MIN_CONN_INTERVAL;
    gap_conn_params.max_conn_interval = SYSTEM_CONFIG_MAX_CONN_INTERVAL;
    gap_conn_params.slave_latency     = SYSTEM_CONFIG_SLAVE_LATENCY;
    gap_conn_params.conn_sup_timeout  = SYSTEM_CONFIG_CONN_SUP_TIMEOUT;

    err_code = sd_ble_gap_ppcp_set(&gap_conn_params);
    APP_ERROR_CHECK(err_code);
}

static void gatt_init(void)
{
    ret_code_t err_code = nrf_ble_gatt_init(&m_gatt, NULL);
    APP_ERROR_CHECK(err_code);

    err_code = nrf_ble_gatt_att_mtu_periph_set(&m_gatt, SYSTEM_CONFIG_MTU_SIZE);
    APP_ERROR_CHECK(err_code);
}

static void advertising_init(void);
static void advertising_params_init(ble_gap_adv_params_t *adv_params)
{
    memset(adv_params, 0, sizeof(*adv_params));
    adv_params->primary_phy     = BLE_GAP_PHY_1MBPS;
    adv_params->duration        = APP_ADV_DURATION;
    adv_params->properties.type = BLE_GAP_ADV_TYPE_CONNECTABLE_SCANNABLE_UNDIRECTED;
    adv_params->p_peer_addr     = NULL;
    adv_params->filter_policy   = BLE_GAP_ADV_FP_ANY;
    adv_params->interval        = SYSTEM_CONFIG_ADV_INTERVAL;
}

static ret_code_t advertising_configure_current(void)
{
    ble_gap_adv_params_t adv_params;
    advertising_params_init(&adv_params);

    ret_code_t err_code = sd_ble_gap_adv_set_configure(&m_adv_handle, &m_adv_data, &adv_params);
    if (err_code == NRF_SUCCESS)
    {
        err_code = sd_ble_gap_tx_power_set(BLE_GAP_TX_POWER_ROLE_ADV, m_adv_handle, SYSTEM_CONFIG_TX_POWER);
    }
    return err_code;
}

static ret_code_t bcast_start_current_fragment(void)
{
#if BLE_BROADCAST_USE_EXTENDED
    ble_advdata_manuf_data_t manuf_data;
    manuf_data.company_identifier = BLE_BROADCAST_COMPANY_ID;
    manuf_data.data.p_data        = (uint8_t *)m_bcast_packet;
    manuf_data.data.size          = m_bcast_packet_len;

    ble_advdata_t advdata;
    memset(&advdata, 0, sizeof(advdata));
    advdata.name_type             = BLE_ADVDATA_NO_NAME;
    advdata.p_manuf_specific_data = &manuf_data;

    uint16_t adv_len = sizeof(m_bcast_advdata);
    ret_code_t err_code = ble_advdata_encode(&advdata, m_bcast_advdata, &adv_len);
    if (err_code != NRF_SUCCESS)
    {
        return err_code;
    }

    m_bcast_adv_data.adv_data.p_data = m_bcast_advdata;
    m_bcast_adv_data.adv_data.len = adv_len;
    m_bcast_adv_data.scan_rsp_data.p_data = NULL;
    m_bcast_adv_data.scan_rsp_data.len = 0;

    ble_gap_adv_params_t adv_params;
    memset(&adv_params, 0, sizeof(adv_params));
    adv_params.properties.type = BLE_GAP_ADV_TYPE_EXTENDED_NONCONNECTABLE_NONSCANNABLE_UNDIRECTED;
    adv_params.primary_phy     = BLE_GAP_PHY_1MBPS;
    adv_params.secondary_phy   = BLE_GAP_PHY_1MBPS;
    adv_params.duration        = BLE_GAP_ADV_TIMEOUT_GENERAL_UNLIMITED;
    adv_params.max_adv_evts    = SYSTEM_CONFIG_BCAST_ADV_EVENTS;
    adv_params.filter_policy   = BLE_GAP_ADV_FP_ANY;
    adv_params.interval        = SYSTEM_CONFIG_BCAST_ADV_INTERVAL;

    err_code = sd_ble_gap_adv_set_configure(&m_adv_handle, &m_bcast_adv_data, &adv_params);
    if (err_code != NRF_SUCCESS)
    {
        return err_code;
    }

    err_code = sd_ble_gap_tx_power_set(BLE_GAP_TX_POWER_ROLE_ADV, m_adv_handle, SYSTEM_CONFIG_TX_POWER);
    if (err_code != NRF_SUCCESS)
    {
        return err_code;
    }

    NRF_LOG_INFO("BLE BCAST EXT ADV len=%u evts=%u",
                 (unsigned)m_bcast_packet_len,
                 (unsigned)SYSTEM_CONFIG_BCAST_ADV_EVENTS);
    return sd_ble_gap_adv_start(m_adv_handle, APP_BLE_CONN_CFG_TAG);
#else
    uint8_t manuf_data[BLE_BROADCAST_MANUF_DATA_MAX_SIZE];
    uint8_t manuf_len = 0;

    if (!ble_broadcast_encode_fragment(m_bcast_packet,
                                       m_bcast_packet_len,
                                       m_bcast_seq,
                                       m_bcast_frag_index,
                                       manuf_data,
                                       sizeof(manuf_data),
                                       &manuf_len))
    {
        return NRF_ERROR_INTERNAL;
    }

    uint8_t adv_len = 0;
    if (!ble_broadcast_build_adv_data(manuf_data,
                                      manuf_len,
                                      m_bcast_advdata,
                                      sizeof(m_bcast_advdata),
                                      &adv_len))
    {
        return NRF_ERROR_DATA_SIZE;
    }

    m_bcast_adv_data.adv_data.p_data = m_bcast_advdata;
    m_bcast_adv_data.adv_data.len = adv_len;
    m_bcast_adv_data.scan_rsp_data.p_data = NULL;
    m_bcast_adv_data.scan_rsp_data.len = 0;

    ble_gap_adv_params_t adv_params;
    memset(&adv_params, 0, sizeof(adv_params));
    adv_params.properties.type = BLE_GAP_ADV_TYPE_NONCONNECTABLE_NONSCANNABLE_UNDIRECTED;
    adv_params.primary_phy     = BLE_GAP_PHY_1MBPS;
    adv_params.duration        = BLE_GAP_ADV_TIMEOUT_GENERAL_UNLIMITED;
    adv_params.max_adv_evts    = BCAST_ADV_EVENTS_PER_FRAGMENT;
    adv_params.filter_policy   = BLE_GAP_ADV_FP_ANY;
    adv_params.interval        = SYSTEM_CONFIG_ADV_INTERVAL;

    ret_code_t err_code = sd_ble_gap_adv_set_configure(&m_adv_handle, &m_bcast_adv_data, &adv_params);
    if (err_code != NRF_SUCCESS)
    {
        return err_code;
    }

    err_code = sd_ble_gap_tx_power_set(BLE_GAP_TX_POWER_ROLE_ADV, m_adv_handle, SYSTEM_CONFIG_TX_POWER);
    if (err_code != NRF_SUCCESS)
    {
        return err_code;
    }

    NRF_LOG_INFO("BLE BCAST ADV fragment %u/%u len=%u",
                 (unsigned)(m_bcast_frag_index + 1u),
                 (unsigned)m_bcast_frag_count,
                 (unsigned)m_bcast_packet_len);
    return sd_ble_gap_adv_start(m_adv_handle, APP_BLE_CONN_CFG_TAG);
#endif
}

static void bcast_restore_advertising(void)
{
    bool restore = m_bcast_restore_advertising;
    m_bcast_active = false;
    m_bcast_restore_advertising = false;

    if (!restore || m_conn_handle != BLE_CONN_HANDLE_INVALID)
    {
        return;
    }

    ret_code_t err_code = advertising_configure_current();
    if (err_code != NRF_SUCCESS)
    {
        NRF_LOG_WARNING("BLE BCAST restore advertiser configure failed: 0x%x", err_code);
        return;
    }

    ble_peripheral_advertising_start();
}

static void bcast_advance_fragment(void)
{
    if (!m_bcast_active)
    {
        return;
    }

    m_bcast_frag_index++;
    if (m_bcast_frag_index >= m_bcast_frag_count)
    {
        NRF_LOG_INFO("BLE BCAST ADV burst complete");
        bcast_restore_advertising();
        return;
    }

    ret_code_t err_code = bcast_start_current_fragment();
    if (err_code != NRF_SUCCESS)
    {
        NRF_LOG_WARNING("BLE BCAST ADV fragment start failed: 0x%x", err_code);
        bcast_restore_advertising();
    }
}

static void advertising_init(void)
{
    ret_code_t    err_code;
    ble_advdata_t advdata;
    ble_advdata_t srdata;

    ble_uuid_t adv_uuids[] = {{SYSTEM_CONFIG_SERVICE_UUID, BLE_UUID_TYPE_BLE}};

    memset(&advdata, 0, sizeof(advdata));

    advdata.name_type          = BLE_ADVDATA_NO_NAME;
    advdata.include_appearance = true;
    advdata.flags              = BLE_GAP_ADV_FLAGS_LE_ONLY_GENERAL_DISC_MODE;

    memset(&srdata, 0, sizeof(srdata));
    srdata.name_type               = BLE_ADVDATA_FULL_NAME;
    srdata.uuids_complete.uuid_cnt = sizeof(adv_uuids) / sizeof(adv_uuids[0]);
    srdata.uuids_complete.p_uuids  = adv_uuids;

    m_adv_data.adv_data.len = BLE_GAP_ADV_SET_DATA_SIZE_MAX;
    m_adv_data.scan_rsp_data.len = BLE_GAP_ADV_SET_DATA_SIZE_MAX;

    err_code = ble_advdata_encode(&advdata, m_adv_data.adv_data.p_data, &m_adv_data.adv_data.len);
    APP_ERROR_CHECK(err_code);

    err_code = ble_advdata_encode(&srdata, m_adv_data.scan_rsp_data.p_data, &m_adv_data.scan_rsp_data.len);
    APP_ERROR_CHECK(err_code);

    err_code = advertising_configure_current();
    APP_ERROR_CHECK(err_code);
}

static void nrf_qwr_error_handler(uint32_t nrf_error)
{
    APP_ERROR_HANDLER(nrf_error);
}

static void led_write_handler(uint16_t conn_handle, ble_lbs_t * p_lbs, uint8_t led_state)
{
    UNUSED_PARAMETER(conn_handle);
    UNUSED_PARAMETER(p_lbs);
    UNUSED_PARAMETER(led_state);
}

static void nus_data_handler(ble_nus_evt_t * p_evt)
{
    if (p_evt->type == BLE_NUS_EVT_RX_DATA)
    {
        NRF_LOG_INFO("BLE Peripheral NUS RX: %u bytes", p_evt->params.rx_data.length);
        bsp_utils_led_activity_pulse();
        if (m_ble_rx_cb != NULL)
        {
            m_ble_rx_cb(p_evt->params.rx_data.p_data, p_evt->params.rx_data.length);
        }
    }
}

static void services_init(void)
{
    ret_code_t         err_code;
    ble_lbs_init_t     init     = {0};
    nrf_ble_qwr_init_t qwr_init = {0};
    ble_nus_init_t     nus_init = {0};

    qwr_init.error_handler = nrf_qwr_error_handler;

    err_code = nrf_ble_qwr_init(&m_qwr, &qwr_init);
    APP_ERROR_CHECK(err_code);

    init.led_write_handler = led_write_handler;

    err_code = ble_lbs_init(&m_lbs, &init);
    APP_ERROR_CHECK(err_code);

    // Initialize Nordic UART Service
    nus_init.data_handler = nus_data_handler;
    err_code = ble_nus_init(&m_nus, &nus_init);
    APP_ERROR_CHECK(err_code);
}

static void on_conn_params_evt(ble_conn_params_evt_t * p_evt)
{
    if (p_evt->evt_type == BLE_CONN_PARAMS_EVT_FAILED)
    {
        NRF_LOG_WARNING("Connection parameter update failed.");
    }
}

static void conn_params_error_handler(uint32_t nrf_error)
{
    APP_ERROR_HANDLER(nrf_error);
}

static void conn_params_init(void)
{
    ret_code_t             err_code;
    ble_conn_params_init_t cp_init;

    memset(&cp_init, 0, sizeof(cp_init));

    cp_init.p_conn_params                  = NULL;
    cp_init.first_conn_params_update_delay = FIRST_CONN_PARAMS_UPDATE_DELAY;
    cp_init.next_conn_params_update_delay  = NEXT_CONN_PARAMS_UPDATE_DELAY;
    cp_init.max_conn_params_update_count   = MAX_CONN_PARAMS_UPDATE_COUNT;
    cp_init.start_on_notify_cccd_handle    = BLE_GATT_HANDLE_INVALID;
    cp_init.disconnect_on_fail             = false;
    cp_init.evt_handler                    = on_conn_params_evt;
    cp_init.error_handler                  = conn_params_error_handler;

    err_code = ble_conn_params_init(&cp_init);
    APP_ERROR_CHECK(err_code);
}

// Forward declaration
static void ble_stack_init(void);

void ble_peripheral_init(void)
{
    if (m_is_initialized) return;
    
    ble_stack_init();
    gap_params_init();
    gatt_init();
    services_init();
    advertising_init();
    conn_params_init();
    
    m_is_initialized = true;
}

void ble_peripheral_advertising_start(void)
{
    ret_code_t           err_code;

    if (!m_is_initialized) return;
    if (m_is_advertising) return;
    if (m_bcast_active) return;
    if (m_conn_handle != BLE_CONN_HANDLE_INVALID) return;

    err_code = sd_ble_gap_adv_start(m_adv_handle, APP_BLE_CONN_CFG_TAG);
    APP_ERROR_CHECK(err_code);

    m_is_advertising = true;
    bsp_utils_led_on();
}

static void ble_evt_handler(ble_evt_t const * p_ble_evt, void * p_context)
{
    ret_code_t err_code;

    switch (p_ble_evt->header.evt_id)
    {
        case BLE_GAP_EVT_CONNECTED:
            NRF_LOG_INFO("Connected");
            m_conn_handle = p_ble_evt->evt.gap_evt.conn_handle;
            m_is_advertising = false; // SoftDevice stops advertising automatically on connection
            err_code = nrf_ble_qwr_conn_handle_assign(&m_qwr, m_conn_handle);
            APP_ERROR_CHECK(err_code);
            bsp_utils_led_blink_start();
            m_pending_tx_chunks = 0;
            if (err_code != NRF_SUCCESS && err_code != NRF_ERROR_INVALID_STATE)
            {
                APP_ERROR_CHECK(err_code);
            }
            break;

        case BLE_GAP_EVT_DISCONNECTED:
            NRF_LOG_WARNING("Disconnected, reason: 0x%x",
                            p_ble_evt->evt.gap_evt.params.disconnected.reason);
            m_conn_handle = BLE_CONN_HANDLE_INVALID;
            bsp_utils_led_blink_stop();
            m_pending_tx_chunks = 0;
            m_is_advertising = false; // Reset flag so that advertising_start does not return early
            if (m_bcast_active)
            {
                m_bcast_restore_advertising = true;
            }
            else
            {
                ble_peripheral_advertising_start();
            }
            break;

        case BLE_GAP_EVT_SEC_PARAMS_REQUEST:
            err_code = sd_ble_gap_sec_params_reply(m_conn_handle,
                                                   BLE_GAP_SEC_STATUS_PAIRING_NOT_SUPP,
                                                   NULL,
                                                   NULL);
            APP_ERROR_CHECK(err_code);
            break;

        case BLE_GAP_EVT_PHY_UPDATE_REQUEST:
        {
            NRF_LOG_DEBUG("PHY update request.");
            ble_gap_phys_t const phys =
            {
                .rx_phys = SYSTEM_CONFIG_PREFERRED_PHY,
                .tx_phys = SYSTEM_CONFIG_PREFERRED_PHY,
            };
            err_code = sd_ble_gap_phy_update(p_ble_evt->evt.gap_evt.conn_handle, &phys);
            APP_ERROR_CHECK(err_code);
        } break;

        case BLE_GATTS_EVT_SYS_ATTR_MISSING:
            err_code = sd_ble_gatts_sys_attr_set(m_conn_handle, NULL, 0, 0);
            APP_ERROR_CHECK(err_code);
            break;

        case BLE_GATTC_EVT_TIMEOUT:
            NRF_LOG_DEBUG("GATT Client Timeout.");
            err_code = sd_ble_gap_disconnect(p_ble_evt->evt.gattc_evt.conn_handle,
                                             BLE_HCI_REMOTE_USER_TERMINATED_CONNECTION);
            APP_ERROR_CHECK(err_code);
            break;

        case BLE_GATTS_EVT_TIMEOUT:
            NRF_LOG_DEBUG("GATT Server Timeout.");
            err_code = sd_ble_gap_disconnect(p_ble_evt->evt.gatts_evt.conn_handle,
                                             BLE_HCI_REMOTE_USER_TERMINATED_CONNECTION);
            APP_ERROR_CHECK(err_code);
            break;

        case BLE_GATTS_EVT_HVN_TX_COMPLETE:
        {
            uint16_t completed_count = p_ble_evt->evt.gatts_evt.params.hvn_tx_complete.count;
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
                bsp_utils_led_activity_pulse();
            }
        } break;

        case BLE_GAP_EVT_ADV_SET_TERMINATED:
            if (m_bcast_active &&
                p_ble_evt->evt.gap_evt.params.adv_set_terminated.adv_handle == m_adv_handle)
            {
                bcast_advance_fragment();
            }
            break;

        default:
            break;
    }
}

static void ble_stack_init(void)
{
    ret_code_t err_code;

    err_code = nrf_sdh_enable_request();
    APP_ERROR_CHECK(err_code);

    uint32_t ram_start = 0;
    err_code = nrf_sdh_ble_default_cfg_set(APP_BLE_CONN_CFG_TAG, &ram_start);
    APP_ERROR_CHECK(err_code);

    err_code = nrf_sdh_ble_enable(&ram_start);
    APP_ERROR_CHECK(err_code);

    NRF_SDH_BLE_OBSERVER(m_ble_observer, APP_BLE_OBSERVER_PRIO, ble_evt_handler, NULL);
}

void ble_peripheral_advertising_stop(void)
{
    if (m_is_advertising)
    {
        sd_ble_gap_adv_stop(m_adv_handle);
        m_is_advertising = false;
    }
}

void ble_peripheral_adv_config_set(bool enable, const char * device_name, uint32_t serial_number)
{
    if (!m_is_initialized && enable)
    {
        ble_peripheral_init();
    }
    
    NRF_LOG_INFO("MCU Requested BLE ADV Config Set: enable=%d, device_name=%s, serial_number=%u", enable, device_name, serial_number);

    if (m_bcast_active)
    {
        NRF_LOG_WARNING("BLE ADV config update skipped during BCAST burst");
        return;
    }

    if (enable)
    {
        if (!m_is_advertising)
        {
            ble_gap_conn_sec_mode_t sec_mode;
            BLE_GAP_CONN_SEC_MODE_SET_OPEN(&sec_mode);
            
            if (device_name != NULL && strlen(device_name) > 0)
            {
                sd_ble_gap_device_name_set(&sec_mode, (const uint8_t *)device_name, strlen(device_name));
            }
            else
            {
                char auto_name[30];
                snprintf(auto_name, sizeof(auto_name), "%s%02X", SYSTEM_CONFIG_DEVICE_PREFIX, (unsigned int)serial_number);
                sd_ble_gap_device_name_set(&sec_mode, (const uint8_t *)auto_name, strlen(auto_name));            
            }
            
            // Re-encode advertisement data to reflect new device name
            advertising_init();

            ble_peripheral_advertising_start();
        }
    }
    else
    {
        ble_peripheral_advertising_stop();
    }
}

uint8_t ble_peripheral_status_get(void)
{
    if (!m_is_initialized) return 1; // IDLE
    if (m_conn_handle != BLE_CONN_HANDLE_INVALID) return 5; // CONNECTED
    if (m_is_advertising) return 3; // ADVERTISING
    return 1; // IDLE
}

void ble_peripheral_adv_status_update(const void * p_adv_status)
{
    if (!m_is_initialized || p_adv_status == NULL) return;

    const protobuf_ble_adv_status_t * status = (const protobuf_ble_adv_status_t *)p_adv_status;
    NRF_LOG_INFO("MCU Requested BLE ADV Status Update: Bat=%d%%, Errs=%d", 
                 (int)status->bat_soc_percent, (int)status->error_count);

    if (m_bcast_active)
    {
        NRF_LOG_WARNING("BLE ADV status update skipped during BCAST burst");
        return;
    }

    if (!m_is_advertising)
    {
        NRF_LOG_INFO("Advertising inactive; ADV status update skipped");
        return;
    }

    /* Pack status into the compact manufacturer-specific advertising payload. */
    ble_adv_status_packed_t packed_status;
    packed_status.device_type     = (uint8_t)status->device;
    packed_status.device_id       = (uint8_t)status->device_id;
    packed_status.bat_soc_percent = (uint8_t)status->bat_soc_percent;
    packed_status.status_flags    = (uint16_t)status->status_flags;
    packed_status.warning_count   = (uint8_t)status->warning_count;
    packed_status.error_count     = (uint8_t)status->error_count;
    packed_status.timestamp_s     = status->local_timestamp_s;

    ble_advdata_manuf_data_t manuf_data;
    manuf_data.company_identifier = 0xFFFF; // Test/internal Company ID
    manuf_data.data.p_data        = (uint8_t *)&packed_status;
    manuf_data.data.size          = sizeof(packed_status);

    ble_advdata_t advdata;
    memset(&advdata, 0, sizeof(advdata));
    advdata.name_type          = BLE_ADVDATA_NO_NAME;
    advdata.include_appearance = true;
    advdata.flags              = BLE_GAP_ADV_FLAGS_LE_ONLY_GENERAL_DISC_MODE;
    advdata.p_manuf_specific_data = &manuf_data;

    uint8_t * p_new_buffer = m_adv_buffer_toggle ? m_enc_advdata_1 : m_enc_advdata_2;
    uint16_t encoded_len = BLE_GAP_ADV_SET_DATA_SIZE_MAX;

    ret_code_t err_code = ble_advdata_encode(&advdata, p_new_buffer, &encoded_len);
    if (err_code != NRF_SUCCESS)
    {
        NRF_LOG_ERROR("Failed to encode advertising data: 0x%x", err_code);
        return;
    }

    m_adv_data.adv_data.p_data = p_new_buffer;
    m_adv_data.adv_data.len    = encoded_len;

    err_code = sd_ble_gap_adv_set_configure(&m_adv_handle, &m_adv_data, NULL);
    if (err_code != NRF_SUCCESS)
    {
        NRF_LOG_ERROR("Failed to update active advertising data: 0x%x", err_code);
        return;
    }

    m_adv_buffer_toggle = !m_adv_buffer_toggle;
    NRF_LOG_INFO("BLE Advertising Data updated successfully (Active buffer: %d)", !m_adv_buffer_toggle);
}

uint32_t ble_peripheral_broadcast_send(uint8_t const * p_data, uint16_t length)
{
    if (!m_is_initialized)
    {
        return NRF_ERROR_INVALID_STATE;
    }

    if (p_data == NULL || length == 0u)
    {
        return NRF_ERROR_NULL;
    }

    if (length > BLE_BROADCAST_MAX_PACKET_SIZE)
    {
        NRF_LOG_WARNING("BLE BCAST packet too large: %u > %u",
                        (unsigned)length,
                        (unsigned)BLE_BROADCAST_MAX_PACKET_SIZE);
        return NRF_ERROR_DATA_SIZE;
    }

    if (m_bcast_active)
    {
        return NRF_ERROR_BUSY;
    }

    memcpy(m_bcast_packet, p_data, length);
    m_bcast_packet_len = (uint8_t)length;
#if BLE_BROADCAST_USE_EXTENDED
    m_bcast_frag_count = 1;
#else
    m_bcast_frag_count = (uint8_t)((length + BLE_BROADCAST_FRAGMENT_PAYLOAD_SIZE - 1u) /
                                   BLE_BROADCAST_FRAGMENT_PAYLOAD_SIZE);
#endif
    m_bcast_frag_index = 0;
    m_bcast_seq++;
    m_bcast_restore_advertising = m_is_advertising;

    if (m_is_advertising)
    {
        ret_code_t err_code = sd_ble_gap_adv_stop(m_adv_handle);
        if (err_code != NRF_SUCCESS && err_code != NRF_ERROR_INVALID_STATE)
        {
            m_bcast_restore_advertising = false;
            return err_code;
        }
        m_is_advertising = false;
    }

    m_bcast_active = true;
    ret_code_t err_code = bcast_start_current_fragment();
    if (err_code != NRF_SUCCESS)
    {
        NRF_LOG_WARNING("BLE BCAST start failed: 0x%x", err_code);
        bcast_restore_advertising();
        return err_code;
    }

    return NRF_SUCCESS;
}

uint32_t ble_peripheral_send_data(uint8_t const * p_data, uint16_t length)
{
    if (!m_is_initialized || m_conn_handle == BLE_CONN_HANDLE_INVALID)
    {
        return NRF_ERROR_INVALID_STATE;
    }

    if (p_data == NULL || length == 0)
    {
        return NRF_ERROR_NULL;
    }

    NRF_LOG_INFO("Forwarding %u bytes over BLE...", length);
    
    uint16_t offset = 0;
    
    while (offset < length)
    {
        // Use the effective MTU and subtract the 3-byte ATT notification header.
        uint16_t current_payload_mtu = nrf_ble_gatt_eff_mtu_get(&m_gatt, m_conn_handle);
        if (current_payload_mtu == 0 || current_payload_mtu > SYSTEM_CONFIG_MTU_SIZE) 
        {
            current_payload_mtu = SYSTEM_CONFIG_MTU_SIZE; // Clamp to the configured maximum.
        }
        
        current_payload_mtu -= 3; // Subtract the ATT header.
        
        uint16_t send_len = length - offset;
        
        if (send_len > current_payload_mtu) 
        {
            send_len = current_payload_mtu;
        }

        ret_code_t err_code = ble_nus_data_send(&m_nus, (uint8_t *)(p_data + offset), &send_len, m_conn_handle);
        
        if (err_code == NRF_ERROR_RESOURCES)
        {
            // SoftDevice notification TX buffers are full; the router will retry later.
            // This path can run from UART RX context, so do not delay here.
            NRF_LOG_WARNING("BLE TX buffer full, dropping remaining byte: %u", length - offset);
            return err_code;
        }
        else if (err_code != NRF_SUCCESS)
        {
            NRF_LOG_ERROR("BLE Send Failed! Code: 0x%x", err_code);
            return err_code;
        }

        m_pending_tx_chunks++;
        
        offset += send_len;
    }

    return NRF_SUCCESS;
}
