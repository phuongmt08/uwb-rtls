/**
 * @file    bsp_flash_bl.h
 * @brief   Flash driver for bootloader — STM32F411CEU6
 * @author  Phuong Mai
 * @details
 *   Stripped-down flash driver used exclusively by the bootloader:
 *     1. Erase app partition  (sectors 3–5, 0x0800C000–0x08040000)
 *     2. Write chunks         (4-byte aligned, from flash_write packets)
 *     3. Read app header      (validate magic/version before jumping)
 *     4. CRC32 verify         (compare written image against header CRC)
 */

#pragma once

#include <stdint.h>
#include <stdbool.h>
#include "memorylayout.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    BSP_FL_OK              =  0,
    BSP_FL_ERR_NULL_PTR    = -1,
    BSP_FL_ERR_INVALID_ARG = -2,
    BSP_FL_ERR_ERASE       = -3,
    BSP_FL_ERR_PROGRAM     = -4,
    BSP_FL_ERR_VERIFY      = -5,
} bsp_fl_status_t;

typedef struct {
    uint32_t magic;             /* APP_IMAGE_HEADER_MAGIC ('APPH')  */
    uint32_t header_version;    /* APP_IMAGE_HEADER_VERSION         */
    uint32_t header_size;       /* sizeof(bsp_fl_app_header_t)      */
    uint32_t fw_major;
    uint32_t fw_minor;
    uint32_t fw_patch;
    uint32_t fw_build;
    uint64_t fw_gitsha;
    uint32_t image_timestamp;
    uint32_t image_length;      /* bytes from MEM_APP_START         */
    uint32_t image_crc;         /* CRC32 of image_length bytes      */
    uint32_t reserved[5];       /* keep compatible with app header layout */
} bsp_fl_app_header_t;

/**
 * @brief  Erase the entire application partition (sectors 3–5).
 *
 * Must be called before writing a new image. Blocking — takes ~1 s on
 * STM32F411 at 3.3 V.
 *
 * @return BSP_FL_OK on success.
 */
bsp_fl_status_t bsp_fl_app_erase(void);

/**
 * @brief  Write a chunk of firmware data to app flash.
 *
 * @param  dst_addr  Absolute flash address (must be inside MEM_APP_START..MEM_APP_END,
 *                   4-byte aligned).
 * @param  data      Source buffer.
 * @param  length    Number of bytes to write (must be a multiple of 4).
 * @return BSP_FL_OK on success.
 */
bsp_fl_status_t bsp_fl_app_write(uint32_t dst_addr, const uint8_t *data, uint32_t length);

/**
 * @brief  Read and validate the app image header.
 *
 * Checks magic, header_version, and header_size.
 *
 * @param  out   Buffer to copy the header into (must be >= sizeof(bsp_fl_app_header_t)).
 * @return true if header is valid and copied into out.
 */
bool bsp_fl_read_app_header(bsp_fl_app_header_t *out);

/**
 * @brief  Verify the written image against the CRC stored in its header.
 *
 * Reads image_length bytes from MEM_APP_START and computes CRC32 using
 * the STM32 hardware CRC peripheral.  Compares against header->image_crc.
 *
 * @return BSP_FL_OK       — image matches header CRC.
 *         BSP_FL_ERR_VERIFY — mismatch.
 *         BSP_FL_ERR_INVALID_ARG — header invalid or image_length out of range.
 */
bsp_fl_status_t bsp_fl_app_verify_crc(void);

/**
 * @brief  Verify image CRC and expose verification details.
 *
 * @param[out] out_image_length  Optional. Header image_length.
 * @param[out] out_expected_crc  Optional. Header image_crc.
 * @param[out] out_computed_crc  Optional. CRC computed over image_length bytes.
 *
 * @return BSP_FL_OK on match,
 *         BSP_FL_ERR_VERIFY on mismatch,
 *         BSP_FL_ERR_INVALID_ARG if header/length is invalid.
 */
bsp_fl_status_t bsp_fl_app_verify_crc_ex(uint32_t *out_image_length,
                                         uint32_t *out_expected_crc,
                                         uint32_t *out_computed_crc);

/**
 * @brief  Erase all application sectors (sectors 3-5).
 *         Called by usbd_dfu_if.c. Returns USBD_OK(0) or USBD_FAIL(1).
 */
uint16_t DFU_Erase_AppSectors(void);

/**
 * @brief  Map a flash address to its HAL FLASH_SECTOR_x constant.
 *         Called by usbd_dfu_if.c for selective sector erase.
 */
uint32_t DFU_GetSectorFromAddress(uint32_t address);

#ifdef __cplusplus
}
#endif
