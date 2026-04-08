/**
 * @file    bootloader.c
 * @brief   Minimal USB DFU + BLE FOTA bootloader for STM32F411CEU6
 *
 * Boot flow:
 *   1. main() opens a DFU/FOTA window for BL_DFU_TIMEOUT_MS.
 *   2. nRF52 boots independently and self-advertises — STM32 does NOT
 *      call ble_enable or start_adv. STM32 only listens for ble_status_resp
 *      pushed by nRF when state changes (ADVERTISING → CONNECTED etc.).
 *   3. When a host connects over BLE and sends a flash_erase command,
 *      the FOTA receiver is armed:
 *        a. Erase app partition.
 *        b. Accept flash_write packets.
 *        c. On flash_verify: verify CRC + vector table.
 *        d. If OK → reboot into new image. If fail → keep old image.
 *   4. If no FOTA within the timeout → jump to existing app (if valid).
 */

#include "bootloader.h"
#include "sys_logger_bl.h"
#include "stm32f4xx_hal.h"
#include "usb_device.h"
#include "usbd_core.h"
#include "sys_ble_peripheral.h"
#include "network_core.h"
#include "network_cmd.h"
#include "bsp_flash_bl.h"

#include <string.h>
#include <stdio.h>

extern USBD_HandleTypeDef hUsbDeviceFS;

#define OBJECT_CODE  LOG_OBJECT_CODE_NETWORK

/* ─────────────────────────────────────────────
 * App vector validation
 * ───────────────────────────────────────────── */

bool bl_app_vector_valid(void)
{
    const uint32_t magic        = *(uint32_t *)(MEM_APP_HEADER_ADDR + 0U);
    const uint32_t hdr_ver      = *(uint32_t *)(MEM_APP_HEADER_ADDR + 4U);
    const uint32_t hdr_size     = *(uint32_t *)(MEM_APP_HEADER_ADDR + 8U);
    const uint32_t msp          = *(uint32_t *)MEM_APP_START;
    const uint32_t reset_vector = *(uint32_t *)(MEM_APP_START + 4U);

    if (magic        != APP_IMAGE_HEADER_MAGIC)                          return false;
    if (hdr_ver      != APP_IMAGE_HEADER_VERSION)                        return false;
    if (hdr_size     == 0U || hdr_size > MEM_APP_HEADER_SIZE)            return false;
    if (msp          <  SRAM_BASE_ADDR || msp > SRAM_END_ADDR)           return false;
    if (reset_vector <  MEM_APP_START  || reset_vector >= MEM_APP_END)   return false;

    return true;
}

/* ─────────────────────────────────────────────
 * DFU magic / jump
 * ───────────────────────────────────────────── */

bool bl_should_enter_dfu(void)
{
    bool req = (*(volatile uint32_t *)BL_MAGIC_ADDR == BL_MAGIC_VALUE);
    *(volatile uint32_t *)BL_MAGIC_ADDR = 0;
    return req;
}

void bl_jump_to_app(void)
{
    __disable_irq();

    SysTick->CTRL = 0;
    SysTick->LOAD = 0;
    SysTick->VAL  = 0;

    USBD_Stop(&hUsbDeviceFS);
    USBD_DeInit(&hUsbDeviceFS);

    HAL_DeInit();
    HAL_RCC_DeInit();

    for (uint32_t i = 0; i < 8; i++) {
        NVIC->ICER[i] = 0xFFFFFFFFU;
        NVIC->ICPR[i] = 0xFFFFFFFFU;
    }

    SCB->VTOR = MEM_APP_START;
    __DSB();
    __ISB();

    __set_MSP(*(uint32_t *)MEM_APP_START);
    ((void (*)(void))(*(uint32_t *)(MEM_APP_START + 4U)))();
}

/* ─────────────────────────────────────────────
 * BLE FOTA — internal state
 * ───────────────────────────────────────────── */

typedef enum {
    FOTA_IDLE = 0,   /* BLE connected, waiting for flash_erase to start */
    FOTA_RECEIVING,  /* app partition erased, accepting flash_write      */
    FOTA_DONE,       /* image verified — caller must HAL_NVIC_SystemReset */
    FOTA_ABORTED,
} fota_state_t;

typedef struct {
    fota_state_t state;
    uint32_t     bytes_written;
    bool         host_disconnected;
} bl_fota_ctx_t;

static bl_fota_ctx_t         s_fota;
static sys_ble_peripheral_t  s_ble;
static network_core_t       *s_net_core_ref;   /* for packet handler */
static network_cmd_t        *s_net_cmd_ref;     /* for dispatch fallback */

/* ─────────────────────────────────────────────
 * BLE event callbacks
 * ───────────────────────────────────────────── */

static void on_ble_connected(int32_t rssi_dbm, void *arg)
{
    (void)rssi_dbm;
    (void)arg;
    RLOG_I(OBJECT_CODE, "BL: BLE connected, waiting for flash_erase");
}

static void on_ble_disconnected(void *arg)
{
    bl_fota_ctx_t *ctx = (bl_fota_ctx_t *)arg;

    if (ctx->state == FOTA_RECEIVING) {
        RLOG_W(OBJECT_CODE, "BL: disconnected mid-transfer — aborting");
        ctx->state = FOTA_ABORTED;
    } else {
        RLOG_I(OBJECT_CODE, "BL: BLE disconnected (state=%d)", (int)ctx->state);
    }

    ctx->host_disconnected = true;
}

/* ─────────────────────────────────────────────
 * FOTA packet handlers (called from bl_packet_handler)
 * ───────────────────────────────────────────── */

static void bl_on_flash_erase(const protobuf_packet_t *pkt)
{
    (void)pkt;

    if (s_fota.state != FOTA_IDLE) {
        RLOG_W(OBJECT_CODE, "BL: flash_erase ignored (state=%d)", (int)s_fota.state);
        return;
    }

    RLOG_I(OBJECT_CODE, "BL: FOTA start — erasing app partition");

    if (bsp_fl_app_erase() != BSP_FL_OK) {
        RLOG_E(OBJECT_CODE, ERR_HAL, "BL: erase failed");
        s_fota.state = FOTA_ABORTED;
        return;
    }

    s_fota.state         = FOTA_RECEIVING;
    s_fota.bytes_written = 0u;
    RLOG_I(OBJECT_CODE, "BL: ready for flash_write chunks");
}

static void bl_on_flash_write(const protobuf_packet_t *pkt)
{
    if (s_fota.state != FOTA_RECEIVING) {
        return;
    }

    uint32_t       address = pkt->params.flash_write.address;
    const uint8_t *data    = pkt->params.flash_write.data.bytes;
    uint32_t       length  = pkt->params.flash_write.data.size;

    if (length == 0u ||
        address < MEM_APP_START ||
        address + length > MEM_APP_END) {
        RLOG_E(OBJECT_CODE, ERR_INVALID_PARAM,
               "BL: bad write addr=0x%08lX len=%lu",
               (unsigned long)address, (unsigned long)length);
        s_fota.state = FOTA_ABORTED;
        return;
    }

    if (bsp_fl_app_write(address, data, length) != BSP_FL_OK) {
        RLOG_E(OBJECT_CODE, ERR_HAL, "BL: flash write failed");
        s_fota.state = FOTA_ABORTED;
        return;
    }

    s_fota.bytes_written += length;
}

static void bl_on_flash_verify(const protobuf_packet_t *pkt)
{
    (void)pkt;

    if (s_fota.state != FOTA_RECEIVING || s_fota.bytes_written == 0u) {
        RLOG_W(OBJECT_CODE, "BL: verify ignored (state=%d, bytes=%lu)",
               (int)s_fota.state, (unsigned long)s_fota.bytes_written);
        return;
    }

    if (bsp_fl_app_verify_crc() == BSP_FL_OK && bl_app_vector_valid()) {
        RLOG_I(OBJECT_CODE, "BL: image verified (%lu B)",
               (unsigned long)s_fota.bytes_written);
        s_fota.state = FOTA_DONE;
    } else {
        RLOG_E(OBJECT_CODE, ERR_CRC, "BL: image verification failed");
        s_fota.state = FOTA_ABORTED;
    }
}

/* ─────────────────────────────────────────────
 * Combined packet handler
 *
 * Intercepts FOTA-specific packets (flash_erase, flash_write,
 * flash_verify, ble_status_resp) and delegates everything else
 * to network_cmd_dispatch for standard handling (log, ack, etc.).
 * ───────────────────────────────────────────── */

static bool bl_packet_handler(const protobuf_packet_t *pkt)
{
    if (!pkt) return false;

    switch (pkt->which_params) {
        /* FOTA flow */
        case protobuf_packet_t_flash_erase_tag:
            bl_on_flash_erase(pkt);
            network_core_send_ack(s_net_cmd_ref->stream, pkt, protobuf_PACKET_ACK_RESPONSE_ACK);
            return true;

        case protobuf_packet_t_flash_write_tag:
            bl_on_flash_write(pkt);
            network_core_send_ack(s_net_cmd_ref->stream, pkt, protobuf_PACKET_ACK_RESPONSE_ACK);
            return true;

        case protobuf_packet_t_flash_verify_tag:
            bl_on_flash_verify(pkt);
            network_core_send_ack(s_net_cmd_ref->stream, pkt, protobuf_PACKET_ACK_RESPONSE_ACK);
            return true;

        /* BLE status pushed by nRF — update local state shadow */
        case protobuf_packet_t_ble_status_resp_tag:
            sys_ble_peripheral_on_status_resp(&s_ble, pkt);
            return true;

        /* Everything else → standard dispatch (log, device_reset, etc.) */
        default:
            network_cmd_dispatch(s_net_cmd_ref, pkt);
            return true;
    }
}

/* ─────────────────────────────────────────────
 * Public: BLE FOTA entry point
 * ───────────────────────────────────────────── */

bool bl_fota_run(network_core_t *net_core, network_cmd_t *net_cmd, uint32_t timeout_ms)
{
    memset(&s_fota, 0, sizeof(s_fota));
    s_fota.state  = FOTA_IDLE;
    s_net_core_ref = net_core;
    s_net_cmd_ref  = net_cmd;

    /* Set up BLE state observer */
    sys_ble_callbacks_t cbs = {
        .on_connected    = on_ble_connected,
        .on_disconnected = on_ble_disconnected,
        .user_arg        = &s_fota,
    };

    if (!sys_ble_peripheral_init(&s_ble, net_core, &cbs)) {
        RLOG_E(OBJECT_CODE, ERR_NOT_INIT, "BL: BLE init failed");
        return false;
    }

    /* Configure BLE identity (Serial Number + Name) */
    uint32_t sn = *(volatile uint32_t *)0x1FFF7A10; // MCU Unique ID (part 1)
    
    /* Try to read DIP Switch for ID (PB5, PB6, PB7) */
    __HAL_RCC_GPIOB_CLK_ENABLE();
    GPIO_InitTypeDef GPIO_InitStruct = {0};
    GPIO_InitStruct.Pin = GPIO_PIN_5 | GPIO_PIN_6 | GPIO_PIN_7;
    GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
    GPIO_InitStruct.Pull = GPIO_NOPULL;
    HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);
    
    uint8_t id = 0;
    if (HAL_GPIO_ReadPin(GPIOB, GPIO_PIN_5) == GPIO_PIN_SET) id |= 0x01;
    if (HAL_GPIO_ReadPin(GPIOB, GPIO_PIN_6) == GPIO_PIN_SET) id |= 0x02;
    if (HAL_GPIO_ReadPin(GPIOB, GPIO_PIN_7) == GPIO_PIN_SET) id |= 0x04;

    /* Try to read Role from Flash config (0x08040000) */
    /* Offset calculation for protobuf_device_role_t in sys_config_t:
     * config_version(1) + pad(3) + device_type(4) + host_transport(4) + device_id(4) = 16 bytes
     */
    volatile uint32_t *flash_cfg_ptr = (volatile uint32_t *)0x08040000;
    uint32_t role = 0;
    if (*flash_cfg_ptr == 14) { // 14 is the current CONFIG_VERSION
        role = flash_cfg_ptr[4]; // role is the 5th uint32 (indices: 0=ver+pad, 1=type, 2=transport, 3=id, 4=role)
    }

    char name[33];
    const char *role_str = (role == 1) ? "TAG" : (role == 2 ? "ANC" : "BL");

    if (id > 0) {
        snprintf(name, sizeof(name), "RTLS-%s-%u", role_str, (unsigned int)id);
    } else {
        /* Fallback to last 4 hex digits of SN if no DIP ID set */
        snprintf(name, sizeof(name), "RTLS-%s-%04X", role_str, (unsigned int)(sn & 0xFFFF));
    }
    
    sys_ble_peripheral_set_config(&s_ble, sn, name);
    sys_ble_peripheral_enable(&s_ble, true);

    /*
     * Register our combined handler AFTER network_cmd_init.
     * This replaces network_cmd's own handler — we manually call
     * network_cmd_dispatch for non-FOTA packets inside bl_packet_handler.
     */
    network_core_register_packet_handler(net_core, bl_packet_handler);

    RLOG_I(OBJECT_CODE, "BL: FOTA window open (%lu ms)", (unsigned long)timeout_ms);

    uint32_t t0 = HAL_GetTick();

    while (true) {
        network_core_process(net_core);
        network_cmd_process(net_cmd);
        sys_ble_peripheral_process(&s_ble);

        if ((uint32_t)(HAL_GetTick() - t0) >= timeout_ms) {
            RLOG_I(OBJECT_CODE, "BL: FOTA window expired");
            break;
        }

        if (s_fota.state == FOTA_ABORTED) {
            break;
        }

        if (s_fota.state == FOTA_DONE) {
            break;
        }

        if (s_fota.host_disconnected) {
            if (s_fota.state == FOTA_IDLE) {
                s_fota.host_disconnected = false;
            } else {
                break;
            }
        }
    }

    return (s_fota.state == FOTA_DONE);
}
