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
#ifdef BOOTLOADER
    #include "sys_logger_bl.h"
#else
    #include "sys_logger.h"
#endif

#include <string.h>

#define OBJECT_CODE             LOG_OBJECT_CODE_BLE
#define BLE_WATCHDOG_PERIOD_MS  10000u

#define CHECK(_cond, _ret)  do { if (!(_cond)) return (_ret); } while (0)
#define CHECK_VOID(_cond)   do { if (!(_cond)) return; } while (0)

/* ─────────────────────────────────────────────
* Internal helpers
* ───────────────────────────────────────────── */

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

static void ble_set_state(sys_ble_peripheral_t *ble,
                          protobuf_ble_state_t  new_state,
                          int32_t               rssi_dbm)
{
   protobuf_ble_state_t old_state = ble->state;
   if (old_state == new_state) {
       return;
   }

   ble->state = new_state;
   RLOG_I(OBJECT_CODE, "BLE state: %s -> %s",
          ble_state_name(old_state), ble_state_name(new_state));

   if (ble->callbacks.on_state_change) {
       ble->callbacks.on_state_change(old_state, new_state, ble->callbacks.user_arg);
   }

   if (new_state == protobuf_BLE_STATE_CONNECTED) {
       ble->rssi_dbm = rssi_dbm;
       RLOG_I(OBJECT_CODE, "BLE host connected (RSSI %ld dBm)", (long)rssi_dbm);
       if (ble->callbacks.on_connected) {
           ble->callbacks.on_connected(rssi_dbm, ble->callbacks.user_arg);
       }
   }

   if (old_state == protobuf_BLE_STATE_CONNECTED &&
       new_state != protobuf_BLE_STATE_CONNECTED) {
       ble->rssi_dbm = 0;
       RLOG_I(OBJECT_CODE, "BLE host disconnected");
       if (ble->callbacks.on_disconnected) {
           ble->callbacks.on_disconnected(ble->callbacks.user_arg);
       }
   }
}

static void ble_poll_status(sys_ble_peripheral_t *ble)
{
   protobuf_packet_t pkt;
   memset(&pkt, 0, sizeof(pkt));
   pkt.which_params = protobuf_packet_t_ble_status_get_tag;
   pkt.hdr.addr.dst = protobuf_PACKET_ADDR_PERIPHERAL;

   if (!network_core_send_packet(ble->stream, (uint8_t)pkt.hdr.addr.dst, &pkt)) {
       RLOG_W(OBJECT_CODE, "ble_status_get send failed");
   }
}

/* ─────────────────────────────────────────────
* Public API
* ───────────────────────────────────────────── */

bool sys_ble_peripheral_init(sys_ble_peripheral_t      *ble,
                             network_core_t            *stream,
                             const sys_ble_callbacks_t *callbacks)
{
   CHECK(ble && stream, false);

   memset(ble, 0, sizeof(*ble));
   ble->stream  = stream;
   ble->state   = protobuf_BLE_STATE_UNSPECIFIED;
   ble->enabled = true;

   if (callbacks) {
       ble->callbacks = *callbacks;
   }

   ble_poll_status(ble);
   RLOG_I(OBJECT_CODE, "sys_ble_peripheral initialised");
   return true;
}

void sys_ble_peripheral_set_config(sys_ble_peripheral_t *ble,
                                   uint32_t              serial_number,
                                   const char           *device_name)
{
    CHECK_VOID(ble);
    ble->serial_number = serial_number;
    if (device_name) {
        strncpy(ble->device_name, device_name, sizeof(ble->device_name) - 1);
        ble->device_name[sizeof(ble->device_name) - 1] = '\0';
    }
}

bool sys_ble_peripheral_enable(sys_ble_peripheral_t *ble, bool enable)
{
   CHECK(ble && ble->stream && ble->enabled, false);

   protobuf_packet_t pkt;
   memset(&pkt, 0, sizeof(pkt));
   pkt.which_params                            = protobuf_packet_t_ble_adv_config_set_tag;
   pkt.params.ble_adv_config_set.enable        = enable;
   pkt.params.ble_adv_config_set.serial_number  = ble->serial_number;
   strncpy(pkt.params.ble_adv_config_set.device_name, ble->device_name,
           sizeof(pkt.params.ble_adv_config_set.device_name) - 1);

   pkt.hdr.addr.dst                            = protobuf_PACKET_ADDR_PERIPHERAL;

   bool ok = network_core_send_packet(ble->stream, (uint8_t)pkt.hdr.addr.dst, &pkt);
   if (!ok) {
       RLOG_W(OBJECT_CODE, "ble_adv_config_set(%d) send failed", (int)enable);
   }
   return ok;
}

void sys_ble_peripheral_on_status_resp(sys_ble_peripheral_t   *ble,
                                       const protobuf_packet_t *pkt)
{
   CHECK_VOID(ble && pkt);
   CHECK_VOID(pkt->which_params == protobuf_packet_t_ble_status_resp_tag);

   /* Proto enum IS our state — direct assignment, no mapping */
   ble_set_state(ble, pkt->params.ble_status_resp.state,
                      pkt->params.ble_status_resp.rssi_dbm);
}

void sys_ble_peripheral_process(sys_ble_peripheral_t *ble)
{
   CHECK_VOID(ble && ble->stream && ble->enabled);

   uint32_t now = HAL_GetTick();
   static uint32_t s_last_poll_tick = 0u;
   if ((uint32_t)(now - s_last_poll_tick) >= BLE_WATCHDOG_PERIOD_MS) {
       s_last_poll_tick = now;
       ble_poll_status(ble);
   }
}

bool sys_ble_peripheral_is_connected(const sys_ble_peripheral_t *ble)
{
   CHECK(ble, false);
   return ble->state == protobuf_BLE_STATE_CONNECTED;
}

protobuf_ble_state_t sys_ble_peripheral_get_state(const sys_ble_peripheral_t *ble)
{
   if (!ble) {
       return protobuf_BLE_STATE_UNSPECIFIED;
   }
   return ble->state;
}

int32_t sys_ble_peripheral_get_rssi(const sys_ble_peripheral_t *ble)
{
   if (!ble || ble->state != protobuf_BLE_STATE_CONNECTED) {
       return 0;
   }
   return ble->rssi_dbm;
}
