/**
* @file    sys_ble_peripheral.c
* @brief   BLE peripheral state manager — STM32 side
*
* @details
*   STM32 does NOT own the BLE stack — nRF52832 does.
*   This module keeps a local shadow of the nRF state and exposes an
*   event-driven API so the rest of the firmware (and the bootloader's
*   FOTA path) can react to BLE connection events without polling.
*/

#include "sys_ble_peripheral.h"
#include "stm32f4xx_hal.h"
#include "network/network_cmd.h"
#include "network/network_core.h"
#ifdef BOOTLOADER
    #include "bsp_util_bl.h"
    #include "otp/otp.h"
    #include "sys_logger_bl.h"
#else
    #include "bsp_util.h"
    #include "sys_logger.h"
    #include "sys_config.h"
    #include "bsp_battery.h"
#endif

#include <string.h>
#include <stdio.h>

#define OBJECT_CODE             LOG_OBJECT_CODE_BLE
#define BLE_WATCHDOG_PERIOD_MS  10000u

#define BLE_CHECK(_cond, _ret)  do { if (!(_cond)) return (_ret); } while (0)
#define BLE_CHECK_VOID(_cond)   do { if (!(_cond)) return; } while (0)

#ifdef BOOTLOADER
typedef struct {
   uint8_t device_type;
   uint8_t mfg_date[3];
   uint8_t hw_rev;
} __attribute__((packed)) ble_otp_device_info_t;
#endif

/* ─────────────────────────────────────────────
* Internal helpers
* ───────────────────────────────────────────── */

typedef struct {
   network_core_t        *stream;        
   protobuf_ble_state_t   state;         
   int32_t                rssi_dbm;      
   bool                   enabled;       
   uint32_t               serial_number; 
   char                   device_name[32];
} sys_ble_peripheral_t;

static sys_ble_peripheral_t s_ble_peri;

#ifdef BOOTLOADER
static bool ble_device_type_valid(protobuf_device_type_t device_type)
{
   return device_type == protobuf_DEVICE_TYPE_TAG ||
          device_type == protobuf_DEVICE_TYPE_ANCHOR ||
          device_type == protobuf_DEVICE_TYPE_GATEWAY ||
          device_type == protobuf_DEVICE_TYPE_DEBUG_TOOL;
}
#endif

static const char *ble_device_type_prefix(protobuf_device_type_t device_type)
{
   switch (device_type) {
       case protobuf_DEVICE_TYPE_TAG:        return "TAG";
       case protobuf_DEVICE_TYPE_ANCHOR:     return "ANCHOR";
       case protobuf_DEVICE_TYPE_GATEWAY:    return "GATEWAY";
       case protobuf_DEVICE_TYPE_DEBUG_TOOL: return "DEBUG";
       default:                              return "NODE";
   }
}

#ifdef BOOTLOADER
static protobuf_device_type_t ble_read_otp_device_type(void)
{
   ble_otp_device_info_t info = {0};
   uint8_t len = 0u;

   if (otp_get(OTP_TYPE_DEVICE_INFO, &info, sizeof(info), &len) != OTP_OK ||
       len != sizeof(info)) {
       return protobuf_DEVICE_TYPE_UNSPECIFIED;
   }

   protobuf_device_type_t device_type = (protobuf_device_type_t)info.device_type;
   if (!ble_device_type_valid(device_type)) {
       RLOG_W(OBJECT_CODE, "Invalid OTP device type ignored: 0x%02X", info.device_type);
       return protobuf_DEVICE_TYPE_UNSPECIFIED;
   }

   return device_type;
}
#endif

static const char *ble_state_name(protobuf_ble_state_t s)
{
   switch (s) {
       case protobuf_BLE_STATE_IDLE:        return "IDLE";
       case protobuf_BLE_STATE_SCANNING:    return "SCANNING";
       case protobuf_BLE_STATE_ADVERTISING: return "ADVERTISING";
       case protobuf_BLE_STATE_CONNECTING:  return "CONNECTING";
       case protobuf_BLE_STATE_CONNECTED:   return "CONNECTED";
       default:                             return "UNSPECIFIED";
   }
}

static void ble_refresh_adv_config(void)
{
    uint32_t sn = bsp_util_get_serial_number();
    protobuf_device_type_t device_type = protobuf_DEVICE_TYPE_UNSPECIFIED;
    uint32_t device_id = sn & 0xFFFFu;
    char name[32] = {0};

#ifndef BOOTLOADER
    const sys_config_t *cfg = sys_config_get();
    if (cfg) {
        device_type = cfg->device_type;
        device_id = cfg->uwb.device_id;
    }
#else
    device_type = ble_read_otp_device_type();
#endif

    snprintf(name, sizeof(name), "%s-%lu",
             ble_device_type_prefix(device_type),
             (unsigned long)device_id);

    s_ble_peri.serial_number = sn;
    strncpy(s_ble_peri.device_name, name, sizeof(s_ble_peri.device_name) - 1);
    s_ble_peri.device_name[sizeof(s_ble_peri.device_name) - 1] = '\0';
}

static void ble_set_state(protobuf_ble_state_t new_state, int32_t rssi_dbm)
{
   protobuf_ble_state_t old_state = s_ble_peri.state;
   if (old_state == new_state) {
       return;
   }

   s_ble_peri.state = new_state;
   RLOG_I(OBJECT_CODE, "BLE state: %s -> %s",
          ble_state_name(old_state), ble_state_name(new_state));

   if (new_state == protobuf_BLE_STATE_CONNECTED) {
       s_ble_peri.rssi_dbm = rssi_dbm;
       RLOG_I(OBJECT_CODE, "BLE host connected (RSSI %ld dBm)", (long)rssi_dbm);
   }

   if (old_state == protobuf_BLE_STATE_CONNECTED &&
       new_state != protobuf_BLE_STATE_CONNECTED) {
       s_ble_peri.rssi_dbm = 0;
       RLOG_I(OBJECT_CODE, "BLE host disconnected");
   }
}

static void __attribute__((unused)) test_send_log_data_to_host(void)
{
    if (!s_ble_peri.stream) return;

    protobuf_packet_t log_pkt;
    memset(&log_pkt, 0, sizeof(log_pkt));
    log_pkt.which_params = protobuf_packet_t_log_data_tag;
    log_pkt.params.log_data.type = protobuf_log_type_t_LOG_TYPE_DEVICE_LOG;

    const char *msg = "testing BLE transmittion form MCU to HOST";
    uint8_t msg_len = (uint8_t)strlen(msg);
    uint16_t rec_len = 1 + 1 + 6 + 1 + msg_len;
    uint16_t total_len = 2 + rec_len;
    uint16_t padded_len = (total_len + 3) & ~3;

    if (padded_len > sizeof(log_pkt.params.log_data.data.bytes)) return;

    uint8_t *buf = log_pkt.params.log_data.data.bytes;
    buf[0] = rec_len & 0xFF;
    buf[1] = (rec_len >> 8) & 0xFF;
    buf[2] = 0xFE; // INFO level
    buf[3] = OBJECT_CODE;
    memset(&buf[4], 0, 6); // timestamp = 0
    buf[10] = msg_len;
    memcpy(&buf[11], msg, msg_len);

    for (uint16_t i = total_len; i < padded_len; ++i) {
        buf[i] = 0;
    }
    RLOG_I(OBJECT_CODE, "Prepared log packet with padded length %u bytes", padded_len);
    log_pkt.params.log_data.data.size = padded_len;
    network_core_send_packet(s_ble_peri.stream, protobuf_PACKET_ADDR_HOST, &log_pkt);
}

static void ble_poll_status(void)
{
   if (!s_ble_peri.stream) return;
   
   if (!network_send_ble_status_get(s_ble_peri.stream, protobuf_PACKET_ADDR_PERIPHERAL)) {
       RLOG_W(OBJECT_CODE, "ble_status_get send failed");
   }
//    // Send the message stub to check the transmission from MCU to HOST via BLE
//    RLOG_I(OBJECT_CODE, "send testing BLE transmittion log to HOST");
//    test_send_log_data_to_host();
}

/* ─────────────────────────────────────────────
* Public API
* ───────────────────────────────────────────── */

bool sys_ble_peripheral_init(network_core_t *stream)
{
   BLE_CHECK(stream, false);

   memset(&s_ble_peri, 0, sizeof(s_ble_peri));
   s_ble_peri.stream  = stream;
   s_ble_peri.state   = protobuf_BLE_STATE_UNSPECIFIED;
   s_ble_peri.enabled = true;

   RLOG_I(OBJECT_CODE, "sys_ble_peripheral initialised");
   return true;
}

void sys_ble_peripheral_set_config(void)
{
    ble_refresh_adv_config();
    RLOG_I(OBJECT_CODE, "BLE advertising name: %s", s_ble_peri.device_name);

    if (!sys_ble_peripheral_send_config(protobuf_PACKET_ADDR_PERIPHERAL)) {
        RLOG_W(OBJECT_CODE, "ble_adv_config_set send failed");
    }
}

bool sys_ble_peripheral_send_config(uint8_t dst)
{
   BLE_CHECK(s_ble_peri.stream, false);

   if (s_ble_peri.device_name[0] == '\0' || s_ble_peri.serial_number == 0u) {
       ble_refresh_adv_config();
   }

   return network_send_ble_adv_config_set(s_ble_peri.stream,
                                          dst,
                                          s_ble_peri.enabled,
                                          s_ble_peri.serial_number,
                                          s_ble_peri.device_name);
}

bool sys_ble_peripheral_enable(bool enable)
{
   BLE_CHECK(s_ble_peri.stream, false);
   
   s_ble_peri.enabled = enable;

   bool ok = sys_ble_peripheral_send_config(protobuf_PACKET_ADDR_PERIPHERAL);
   if (!ok) {
       RLOG_W(OBJECT_CODE, "ble_adv_config_set(%d) send failed", (int)enable);
   }
   return ok;
}

void sys_ble_peripheral_on_status_resp(const protobuf_packet_t *pkt)
{
   BLE_CHECK_VOID(pkt);
   BLE_CHECK_VOID(pkt->which_params == protobuf_packet_t_ble_status_resp_tag);

   ble_set_state(pkt->params.ble_status_resp.state,
                 pkt->params.ble_status_resp.rssi_dbm);
}

void sys_ble_peripheral_send_adv_status(void)
{
    if (!s_ble_peri.stream || !s_ble_peri.enabled) return;

    protobuf_ble_adv_status_t status = protobuf_ble_adv_status_t_init_zero;
    
    // 1. Fill device identity
#ifndef BOOTLOADER
    const sys_config_t *cfg = sys_config_get();
    if (cfg) {
        status.device = cfg->device_type;
        status.device_id = cfg->uwb.device_id;
    } else {
        status.device = protobuf_DEVICE_TYPE_UNSPECIFIED;
        status.device_id = 0;
    }
    
    // 2. Read battery percentage from the MAX17048 driver
    status.bat_soc_percent = bsp_battery_get_soc();
#else
    status.device = ble_read_otp_device_type();
    status.device_id = s_ble_peri.serial_number & 0xFFFFu;
    status.bat_soc_percent = 100;
#endif

    // 3. Fill diagnostic status fields
    status.status_flags = 0;
    status.warning_count = 0;
    status.error_count = 0;
    status.local_timestamp_s = bsp_rtc_get_timestamp_s();

    RLOG_I(OBJECT_CODE, "Sending ADV status packet: Device ID=%lu, Bat=%lu%%", 
           status.device_id, status.bat_soc_percent);

    if (!network_send_ble_adv_status(s_ble_peri.stream, protobuf_PACKET_ADDR_PERIPHERAL, &status)) {
        RLOG_W(OBJECT_CODE, "Failed to send BLE ADV status packet");
    }
}

void sys_ble_peripheral_process(void)
{
   BLE_CHECK_VOID(s_ble_peri.stream && s_ble_peri.enabled);

   uint32_t now = HAL_GetTick();
   static uint32_t s_last_poll_tick = 0u;
   if ((uint32_t)(now - s_last_poll_tick) >= BLE_WATCHDOG_PERIOD_MS) {
       s_last_poll_tick = now;
       ble_poll_status();
       sys_ble_peripheral_send_adv_status();
       RLOG_W(OBJECT_CODE, "send BLE status and ADV status updates");
   }
}

bool sys_ble_peripheral_is_connected(void)
{
   return s_ble_peri.state == protobuf_BLE_STATE_CONNECTED;
}

protobuf_ble_state_t sys_ble_peripheral_get_state(void)
{
   return s_ble_peri.state;
}

int32_t sys_ble_peripheral_get_rssi(void)
{
   if (s_ble_peri.state != protobuf_BLE_STATE_CONNECTED) {
       return 0;
   }
   return s_ble_peri.rssi_dbm;
}
