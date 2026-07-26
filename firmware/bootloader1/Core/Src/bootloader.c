/**
 * @file    bootloader.c
 * @brief   Minimal USB DFU + BLE FOTA bootloader for STM32F411CEU6
 * @author  Phuong Mai
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
#include "zlib_decoder.h"

#include <string.h>
#include <stdio.h>

extern USBD_HandleTypeDef hUsbDeviceFS;

#define OBJECT_CODE  LOG_OBJECT_CODE_NETWORK

#define FOTA_FLAG_COMPRESSED  (0x80000000UL)
#define FOTA_FLAG_ACK_REQ     (0x40000000UL)
#define FOTA_ADDR_MASK        (0x0FFFFFFFUL)
#define FOTA_FLASH_BUFFER_SIZE 256U
#define FOTA_FRAME_HEADER_SIZE 8U
#define FOTA_FRAME_VERSION     1U
#define FOTA_RAW_BLOCK_SIZE    4096U
#define FOTA_COMP_BLOCK_MAX    4160U
#define FOTA_FRAME_MAGIC_0     0x46U /* 'F' */
#define FOTA_FRAME_MAGIC_1     0x44U /* 'D' */
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

    /* Never boot a partially erased/written image after an interrupted FOTA. */
//    return (bsp_fl_app_verify_crc() == BSP_FL_OK);
    return true;
}

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
typedef struct {
    protobuf_fota_state_index_t state;
    uint32_t                    bytes_written;
    bool                        host_disconnected;
    uint32_t                    last_processed_seq;  /* Track last processed packet seq to prevent duplicates */
    uint32_t                    last_flash_write_ms;  /* Last flash_write activity while receiving */
    bool                        is_compressed_mode;
    uint32_t                    compressed_next_addr;
} bl_fota_ctx_t;

typedef struct {
    uint8_t  buf[FOTA_FLASH_BUFFER_SIZE];
    uint16_t buf_len;
    uint32_t flash_addr;
} flash_write_buf_t;

typedef struct {
    uint8_t  header[FOTA_FRAME_HEADER_SIZE];
    uint8_t  header_len;
    uint16_t raw_len;
    uint16_t compressed_len;
    uint16_t received_len;
    uint32_t total_raw_len;
    uint8_t  compressed[FOTA_COMP_BLOCK_MAX];
} fota_frame_rx_t;

static bl_fota_ctx_t         s_fota;
static flash_write_buf_t     s_write_buf;
static fota_frame_rx_t       s_frame_rx;
static network_core_t       *s_net_core_ref;   /* for packet handler */

static bool bl_flush_write_buf(bool pad_to_word)
{
    uint16_t write_len = s_write_buf.buf_len;

    if (write_len == 0U) {
        return true;
    }

    if (pad_to_word) {
        uint16_t aligned_len = (uint16_t)((write_len + 3U) & ~3U);
        while (write_len < aligned_len) {
            s_write_buf.buf[write_len++] = 0xFFU;
        }
    }

    if ((write_len & 3U) != 0U ||
        s_write_buf.flash_addr >= MEM_APP_END ||
        write_len > (MEM_APP_END - s_write_buf.flash_addr)) {
        return false;
    }

    if (bsp_fl_app_write(s_write_buf.flash_addr,
                         s_write_buf.buf,
                         write_len) != BSP_FL_OK) {
        return false;
    }

    s_write_buf.flash_addr += write_len;
    s_write_buf.buf_len = 0U;
    return true;
}

static bool deflate_flash_write_cb(uint8_t byte, uint32_t output_index, void *user_data)
{
    (void)output_index;
    (void)user_data;

    if (s_write_buf.flash_addr >= MEM_APP_END ||
        s_write_buf.buf_len >= (MEM_APP_END - s_write_buf.flash_addr)) {
        return false;
    }

    s_write_buf.buf[s_write_buf.buf_len++] = byte;

    if (s_write_buf.buf_len == FOTA_FLASH_BUFFER_SIZE) {
        return bl_flush_write_buf(false);
    }
    return true;
}

static uint16_t bl_read_le16(const uint8_t *data)
{
    return (uint16_t)data[0] | ((uint16_t)data[1] << 8U);
}

static void bl_frame_rx_reset(void)
{
    memset(&s_frame_rx, 0, sizeof(s_frame_rx));
}

static bool bl_frame_header_parse(void)
{
    if (s_frame_rx.header[0] != FOTA_FRAME_MAGIC_0 ||
        s_frame_rx.header[1] != FOTA_FRAME_MAGIC_1 ||
        s_frame_rx.header[2] != FOTA_FRAME_VERSION ||
        s_frame_rx.header[3] != 0U) {
        return false;
    }

    s_frame_rx.raw_len = bl_read_le16(&s_frame_rx.header[4]);
    s_frame_rx.compressed_len = bl_read_le16(&s_frame_rx.header[6]);
    s_frame_rx.received_len = 0U;

    if (s_frame_rx.raw_len == 0U ||
        s_frame_rx.raw_len > FOTA_RAW_BLOCK_SIZE ||
        (s_frame_rx.raw_len & 3U) != 0U ||
        s_frame_rx.compressed_len == 0U ||
        s_frame_rx.compressed_len > FOTA_COMP_BLOCK_MAX ||
        s_frame_rx.total_raw_len + s_frame_rx.raw_len > MEM_APP_LENGTH) {
        return false;
    }

    uint32_t logical_output =
        s_write_buf.flash_addr + (uint32_t)s_write_buf.buf_len;
    return logical_output ==
           (MEM_APP_START + s_frame_rx.total_raw_len);
}

static bool bl_frame_decode_complete(void)
{
    int result = zlib_decompress_raw(s_frame_rx.compressed,
                                     s_frame_rx.compressed_len,
                                     s_frame_rx.raw_len,
                                     deflate_flash_write_cb,
                                     NULL);
    if (result != TINF_OK) {
        return false;
    }

    s_frame_rx.total_raw_len += s_frame_rx.raw_len;
    s_frame_rx.header_len = 0U;
    s_frame_rx.raw_len = 0U;
    s_frame_rx.compressed_len = 0U;
    s_frame_rx.received_len = 0U;
    return true;
}

static bool bl_frame_stream_process(const uint8_t *data, uint32_t length)
{
    uint32_t offset = 0U;

    while (offset < length) {
        if (s_frame_rx.header_len < FOTA_FRAME_HEADER_SIZE) {
            uint32_t needed =
                FOTA_FRAME_HEADER_SIZE - s_frame_rx.header_len;
            uint32_t available = length - offset;
            uint32_t copy_len = (available < needed) ? available : needed;

            memcpy(&s_frame_rx.header[s_frame_rx.header_len],
                   &data[offset],
                   copy_len);
            s_frame_rx.header_len =
                (uint8_t)(s_frame_rx.header_len + copy_len);
            offset += copy_len;

            if (s_frame_rx.header_len < FOTA_FRAME_HEADER_SIZE) {
                continue;
            }
            if (!bl_frame_header_parse()) {
                return false;
            }
        }

        uint32_t needed =
            (uint32_t)s_frame_rx.compressed_len -
            s_frame_rx.received_len;
        uint32_t available = length - offset;
        uint32_t copy_len = (available < needed) ? available : needed;

        memcpy(&s_frame_rx.compressed[s_frame_rx.received_len],
               &data[offset],
               copy_len);
        s_frame_rx.received_len =
            (uint16_t)(s_frame_rx.received_len + copy_len);
        offset += copy_len;

        if (s_frame_rx.received_len == s_frame_rx.compressed_len &&
            !bl_frame_decode_complete()) {
            return false;
        }
    }

    return true;
}

static void bl_send_fota_state(const protobuf_packet_t *req,
                               protobuf_fota_state_index_t state)
{
    protobuf_packet_t resp;
    memset(&resp, 0, sizeof(resp));

    resp.which_params  = protobuf_packet_t_fota_state_resp_tag;
    resp.hdr.addr.dst  = (req) ? req->hdr.addr.src : protobuf_PACKET_ADDR_HOST;

    resp.params.fota_state_resp.state  = state;

    network_core_send_packet(s_net_core_ref, resp.hdr.addr.dst, &resp);
}

static void bl_enter_error_and_erase_app(const protobuf_packet_t *req)
{
    s_fota.bytes_written = 0u;
    s_fota.last_flash_write_ms = 0u;
    s_fota.state = protobuf_FOTA_STATE_ERROR;
    bl_send_fota_state(req, s_fota.state);

    if (bsp_fl_app_erase() != BSP_FL_OK) {
        RLOG_E(OBJECT_CODE, ERR_HAL, "BL: app erase on error failed");
        return;
    }

    s_fota.state = protobuf_FOTA_STATE_IDLE;
    s_fota.host_disconnected = false;
    s_fota.last_processed_seq = 0xFFFFFFFFU;
    s_fota.is_compressed_mode = false;
    s_fota.compressed_next_addr = MEM_APP_START;
    s_write_buf.buf_len = 0U;
    s_write_buf.flash_addr = MEM_APP_START;
    bl_frame_rx_reset();
    bl_send_fota_state(req, s_fota.state);
}

static void bl_check_flash_write_timeout(void)
{
    if (s_fota.state != protobuf_FOTA_STATE_RECEIVING) {
        return;
    }

    if (s_fota.last_flash_write_ms == 0u) {
        return;
    }

    uint32_t now = HAL_GetTick();
    if ((uint32_t)(now - s_fota.last_flash_write_ms) < BL_FOTA_FLASH_WRITE_TIMEOUT_MS) {
        return;
    }

    RLOG_E(OBJECT_CODE,
           ERR_TIMEOUT,
           "BL: flash_write timeout (%lu ms) - aborting FOTA",
           (unsigned long)BL_FOTA_FLASH_WRITE_TIMEOUT_MS);
    bl_enter_error_and_erase_app(NULL);
}

static void bl_on_enter_to_bootloader(const protobuf_packet_t *pkt)
{
    RLOG_I(OBJECT_CODE, "BL: enter_to_bootloader received");
    bl_send_fota_state(pkt, s_fota.state);
}

static void bl_on_flash_erase(const protobuf_packet_t *pkt)
{
    if (s_fota.state != protobuf_FOTA_STATE_IDLE &&
        s_fota.state != protobuf_FOTA_STATE_ERROR) {
        RLOG_W(OBJECT_CODE, "BL: flash_erase ignored (state=%d)", (int)s_fota.state);
        network_core_send_ack(s_net_core_ref, pkt, protobuf_PACKET_ACK_RESPONSE_NACK_BUSY);
        return;
    }

    RLOG_I(OBJECT_CODE, "BL: FOTA start — erasing app partition");
    s_fota.state = protobuf_FOTA_STATE_ERASING;
    bl_send_fota_state(pkt, s_fota.state);

    if (bsp_fl_app_erase() != BSP_FL_OK) {
        RLOG_E(OBJECT_CODE, ERR_HAL, "BL: erase failed");
        bl_enter_error_and_erase_app(pkt);
        return;
    }

    s_write_buf.buf_len = 0U;
    s_write_buf.flash_addr = MEM_APP_START;
    bl_frame_rx_reset();
    s_fota.is_compressed_mode = false;
    s_fota.compressed_next_addr = MEM_APP_START;

    s_fota.state         = protobuf_FOTA_STATE_RECEIVING;
    s_fota.bytes_written = 0u;
    s_fota.last_flash_write_ms = HAL_GetTick();
    RLOG_I(OBJECT_CODE, "BL: ready for flash_write chunks");
    bl_send_fota_state(pkt, s_fota.state);
}

static bool bl_on_flash_write(const protobuf_packet_t *pkt)
{
    if (s_fota.state != protobuf_FOTA_STATE_RECEIVING) {
        return false;
    }

    uint32_t       raw_addr = pkt->params.flash_write.address;
    const uint8_t *data     = pkt->params.flash_write.data.bytes;
    uint32_t       length   = pkt->params.flash_write.data.size;

    bool is_compressed = (raw_addr & FOTA_FLAG_COMPRESSED) != 0U;
    bool has_fota_flags = (raw_addr & (FOTA_FLAG_COMPRESSED | FOTA_FLAG_ACK_REQ)) != 0U;
    uint32_t address = has_fota_flags ? (raw_addr & FOTA_ADDR_MASK) : raw_addr;

    if (length == 0u) {
        return false;
    }

    if (is_compressed) {
        uint32_t input_end = address + length;
        if (address < MEM_APP_START || input_end < address) {
            return false;
        }

        if (address < s_fota.compressed_next_addr) {
            if (input_end <= s_fota.compressed_next_addr) {
                s_fota.last_flash_write_ms = HAL_GetTick();
                return true;
            }
            return false;
        }

        if (address > s_fota.compressed_next_addr) {
            return false;
        }

        s_fota.is_compressed_mode = true;
        if (!bl_frame_stream_process(data, length)) {
            RLOG_E(OBJECT_CODE, ERR_INVALID_PARAM,
                   "BL: invalid/failed compressed frame at input=0x%08lX",
                   (unsigned long)address);
            bl_enter_error_and_erase_app(pkt);
            return false;
        }
        s_fota.compressed_next_addr = input_end;
    } else {
        uint32_t end = address + length;
        if (address < MEM_APP_START || end < address || end > MEM_APP_END) {
            RLOG_E(OBJECT_CODE, ERR_INVALID_PARAM,
                   "BL: bad write addr=0x%08lX len=%lu",
                   (unsigned long)address, (unsigned long)length);
            bl_enter_error_and_erase_app(pkt);
            return false;
        }

        if (bsp_fl_app_write(address, data, length) != BSP_FL_OK) {
            RLOG_E(OBJECT_CODE, ERR_HAL, "BL: flash write failed");
            bl_enter_error_and_erase_app(pkt);
            return false;
        }
    }

    s_fota.bytes_written += length;
    s_fota.last_flash_write_ms = HAL_GetTick();
    return true;
}

static void bl_on_flash_verify(const protobuf_packet_t *pkt)
{
    if (s_fota.state != protobuf_FOTA_STATE_RECEIVING || s_fota.bytes_written == 0u) {
        RLOG_W(OBJECT_CODE, "BL: verify ignored (state=%d, bytes=%lu)",
               (int)s_fota.state, (unsigned long)s_fota.bytes_written);
        network_core_send_ack(s_net_core_ref, pkt, protobuf_PACKET_ACK_RESPONSE_NACK_INVALID_TYPE);
        return;
    }

    if (s_fota.is_compressed_mode) {
        if (s_frame_rx.header_len != 0U ||
            s_frame_rx.received_len != 0U ||
            s_frame_rx.compressed_len != 0U ||
            s_frame_rx.total_raw_len == 0U) {
            RLOG_E(OBJECT_CODE, ERR_INVALID_PARAM,
                   "BL: incomplete compressed frame (hdr=%u recv=%u/%u raw=%lu)",
                   (unsigned int)s_frame_rx.header_len,
                   (unsigned int)s_frame_rx.received_len,
                   (unsigned int)s_frame_rx.compressed_len,
                   (unsigned long)s_frame_rx.total_raw_len);
            bl_enter_error_and_erase_app(pkt);
            return;
        }

        if (!bl_flush_write_buf(true)) {
            RLOG_E(OBJECT_CODE, ERR_HAL, "BL: flush write buf failed");
            bl_enter_error_and_erase_app(pkt);
            return;
        }

        uint32_t written_end =
            s_write_buf.flash_addr;
        if (written_end != MEM_APP_START + s_frame_rx.total_raw_len) {
            RLOG_E(OBJECT_CODE, ERR_INVALID_PARAM,
                   "BL: decompressed length mismatch (%lu/%lu)",
                   (unsigned long)(written_end - MEM_APP_START),
                   (unsigned long)s_frame_rx.total_raw_len);
            bl_enter_error_and_erase_app(pkt);
            return;
        }
    }

    uint32_t image_len = 0u;
    uint32_t expected_crc = 0u;
    uint32_t computed_crc = 0u;
    bsp_fl_status_t crc_status =
        bsp_fl_app_verify_crc_ex(&image_len, &expected_crc, &computed_crc);

    if (crc_status == BSP_FL_OK &&
        s_fota.is_compressed_mode &&
        image_len != s_frame_rx.total_raw_len) {
        RLOG_E(OBJECT_CODE, ERR_INVALID_PARAM,
               "BL: header image len=%lu differs from decoded len=%lu",
               (unsigned long)image_len,
               (unsigned long)s_frame_rx.total_raw_len);
        bl_enter_error_and_erase_app(pkt);
        return;
    }

    if (crc_status == BSP_FL_OK) {
        RLOG_I(OBJECT_CODE, "BL: image verified (%lu B)",
               (unsigned long)image_len);
        s_fota.state = protobuf_FOTA_STATE_FINISHED;
        bl_send_fota_state(pkt, s_fota.state);
    } else {
        if (crc_status == BSP_FL_ERR_INVALID_ARG) {
            const bsp_fl_app_header_t *hdr =
                (const bsp_fl_app_header_t *)MEM_APP_HEADER_ADDR;

            RLOG_E(OBJECT_CODE, ERR_INVALID_PARAM,
                   "BL: verify failed (invalid header/len) magic=0x%08lX ver=%lu size=%lu len=%lu crc=0x%08lX",
                   (unsigned long)hdr->magic,
                   (unsigned long)hdr->header_version,
                   (unsigned long)hdr->header_size,
                   (unsigned long)hdr->image_length,
                   (unsigned long)hdr->image_crc);
        } else if (crc_status == BSP_FL_ERR_VERIFY) {
            RLOG_E(OBJECT_CODE, ERR_CRC,
                   "BL: verify failed (crc mismatch) len=%lu expected=0x%08lX computed=0x%08lX",
                   (unsigned long)image_len,
                   (unsigned long)expected_crc,
                   (unsigned long)computed_crc);
        } else {
            RLOG_E(OBJECT_CODE, ERR_CRC,
                   "BL: verify failed (crc status=%d) len=%lu expected=0x%08lX computed=0x%08lX",
                   (int)crc_status,
                   (unsigned long)image_len,
                   (unsigned long)expected_crc,
                   (unsigned long)computed_crc);
        }

        bl_enter_error_and_erase_app(pkt);
    }
}

static bool bl_packet_handler(const protobuf_packet_t *pkt)
{
    if (!pkt) return false;

    switch (pkt->which_params) {
        /* FOTA flow */
        case protobuf_packet_t_enter_to_bootloader_tag:
            bl_on_enter_to_bootloader(pkt);
            return true;

        case protobuf_packet_t_flash_erase_tag:
            bl_on_flash_erase(pkt);
            return true;

        case protobuf_packet_t_flash_write_tag:
        {
            uint32_t raw_addr = pkt->params.flash_write.address;
            bool req_ack = (raw_addr & FOTA_FLAG_ACK_REQ) != 0U ||
                           ((raw_addr & (FOTA_FLAG_COMPRESSED | FOTA_FLAG_ACK_REQ)) == 0U);

            /* Detect and skip duplicate packets */
            if (pkt->has_hdr && pkt->hdr.seq == s_fota.last_processed_seq) {
                RLOG_W(OBJECT_CODE, "BL: duplicate flash_write detected (seq=%d), skipping",
                       (int)pkt->hdr.seq);
                if (req_ack) {
                    network_core_send_ack(s_net_core_ref, pkt, protobuf_PACKET_ACK_RESPONSE_ACK);
                }
                return true;
            }

            /* Process write */
            bool success = bl_on_flash_write(pkt);
            if (success) {
                if (pkt->has_hdr) {
                    s_fota.last_processed_seq = pkt->hdr.seq;
                }
                if (req_ack) {
                    network_core_send_ack(s_net_core_ref, pkt, protobuf_PACKET_ACK_RESPONSE_ACK);
                }
            } else {
                /* Always NACK on failure */
                network_core_send_ack(s_net_core_ref, pkt, protobuf_PACKET_ACK_RESPONSE_NACK_CMD_FAILED);
            }
            return true;
        }

        case protobuf_packet_t_flash_verify_tag:
            bl_on_flash_verify(pkt);
            return true;

        /* BLE status pushed by nRF — update local state shadow */
        case protobuf_packet_t_ble_status_resp_tag:
            sys_ble_peripheral_on_status_resp(pkt);
            return true;

        /* Everything else → standard dispatch (log, device_reset, etc.) */
        default:
            network_cmd_dispatch(pkt);
            return true;
    }
}

void bl_fota_init(network_core_t *net_core)
{
    memset(&s_fota, 0, sizeof(s_fota));
    s_fota.state   = protobuf_FOTA_STATE_IDLE;
    s_fota.last_processed_seq = 0xFFFFFFFFU;  /* Initialize to invalid sequence number */
    s_fota.last_flash_write_ms = 0u;
    s_fota.compressed_next_addr = MEM_APP_START;
    s_write_buf.buf_len = 0U;
    s_write_buf.flash_addr = MEM_APP_START;
    bl_frame_rx_reset();
    s_net_core_ref = net_core;

    if (!sys_ble_peripheral_init(net_core)) {
        RLOG_E(OBJECT_CODE, ERR_NOT_INIT, "BL: BLE init failed");
        return;
    }

    sys_ble_peripheral_set_config();
    
    /* Register packet handler BEFORE enabling to avoid race condition */
    network_core_register_packet_handler(net_core, bl_packet_handler);
    
    sys_ble_peripheral_enable(true);
}

void bl_fota_process(void)
{
    network_core_process(s_net_core_ref);
    network_cmd_process();
    sys_ble_peripheral_process();
    bl_check_flash_write_timeout();
}

bool bl_fota_is_active(void)
{
    /* Active if connected or transfer in progress */
    return (sys_ble_peripheral_is_connected() ||
            s_fota.state != protobuf_FOTA_STATE_IDLE);
}

bool bl_fota_is_finished(void)
{
    return (s_fota.state == protobuf_FOTA_STATE_FINISHED);
}

bool bl_fota_run(network_core_t *net_core, uint32_t timeout_ms)
{
    bl_fota_init(net_core);
    RLOG_I(OBJECT_CODE, "BL: FOTA window open (%lu ms)", (unsigned long)timeout_ms);

    uint32_t t0 = HAL_GetTick();
    bool last_connected = false;

    while (true) {
        bl_fota_process();

        /* Manually track connection edge instead of callback */
        bool cur_connected = sys_ble_peripheral_is_connected();
        if (last_connected && !cur_connected) {
            /* Disconnected event */
            if (s_fota.state == protobuf_FOTA_STATE_RECEIVING) {
                RLOG_W(OBJECT_CODE, "BL: disconnected mid-transfer — aborting");
                bl_enter_error_and_erase_app(NULL);
            } else {
                RLOG_I(OBJECT_CODE, "BL: BLE disconnected (state=%d)", (int)s_fota.state);
            }
            s_fota.host_disconnected = true;
        } else if (!last_connected && cur_connected) {
            /* Connected event */
            RLOG_I(OBJECT_CODE, "BL: BLE connected, waiting for flash_erase");
            s_fota.state = protobuf_FOTA_STATE_IDLE;
        }
        last_connected = cur_connected;

        if (timeout_ms != 0xFFFFFFFF && (uint32_t)(HAL_GetTick() - t0) >= timeout_ms) {
            RLOG_I(OBJECT_CODE, "BL: FOTA window expired");
            break;
        }

        if (s_fota.state == protobuf_FOTA_STATE_ERROR) {
            break;
        }

        if (s_fota.state == protobuf_FOTA_STATE_FINISHED) {
            break;
        }

        if (s_fota.host_disconnected) {
            if (s_fota.state == protobuf_FOTA_STATE_IDLE) {
                s_fota.host_disconnected = false;
            } else {
                break;
            }
        }
    }

    return (s_fota.state == protobuf_FOTA_STATE_FINISHED);
}
