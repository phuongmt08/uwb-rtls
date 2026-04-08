/**
 * @file    bsp_flash_bl.c
 * @author  Phuong Mai
 * @brief   Flash driver for bootloader — STM32F411CEU6
 */

#include "bsp_flash_bl.h"
#include "memorylayout.h"
#include "stm32f4xx_hal.h"

#include <string.h>

typedef struct {
    uint32_t base;
    uint32_t hal_sector;
} sector_entry_t;

static const sector_entry_t APP_SECTORS[] = {
    { 0x0800C000UL, FLASH_SECTOR_3 },   /* 16 KB */
    { 0x08010000UL, FLASH_SECTOR_4 },   /* 64 KB */
    { 0x08020000UL, FLASH_SECTOR_5 },   /* 128 KB */
};

#define APP_SECTOR_COUNT (sizeof(APP_SECTORS) / sizeof(APP_SECTORS[0]))

static bsp_fl_status_t erase_sector(uint32_t hal_sector)
{
    FLASH_EraseInitTypeDef erase = {
        .TypeErase    = FLASH_TYPEERASE_SECTORS,
        .VoltageRange = FLASH_VOLTAGE_RANGE_3,
        .Sector       = hal_sector,
        .NbSectors    = 1,
    };

    uint32_t sector_error = 0;

    HAL_FLASH_Unlock();
    HAL_StatusTypeDef status = HAL_FLASHEx_Erase(&erase, &sector_error);
    HAL_FLASH_Lock();

    return (status == HAL_OK) ? BSP_FL_OK : BSP_FL_ERR_ERASE;
}

static bsp_fl_status_t write_words(uint32_t addr, const uint8_t *data, uint32_t length)
{
    const uint32_t *words = (const uint32_t *)data;
    uint32_t        count = length / 4u;

    HAL_FLASH_Unlock();

    for (uint32_t i = 0; i < count; i++) {
        if (HAL_FLASH_Program(FLASH_TYPEPROGRAM_WORD,
                              addr + i * 4u,
                              words[i]) != HAL_OK) {
            HAL_FLASH_Lock();
            return BSP_FL_ERR_PROGRAM;
        }
    }

    HAL_FLASH_Lock();
    return BSP_FL_OK;
}

static uint32_t crc32_hw(const uint8_t *data, uint32_t length)
{
    /* Use STM32 hardware CRC32 peripheral (polynomial 0x04C11DB7) */
    __HAL_RCC_CRC_CLK_ENABLE();

    CRC->CR = CRC_CR_RESET;

    const uint32_t *words     = (const uint32_t *)data;
    uint32_t        word_count = length / 4u;

    for (uint32_t i = 0; i < word_count; i++) {
        CRC->DR = words[i];
    }

    /* Handle remaining bytes (< 4) by padding with 0xFF */
    uint32_t remainder = length % 4u;
    if (remainder > 0u) {
        uint32_t tail = 0xFFFFFFFFu;
        memcpy(&tail, data + word_count * 4u, remainder);
        CRC->DR = tail;
    }

    return CRC->DR;
}

bsp_fl_status_t bsp_fl_app_erase(void)
{
    for (uint32_t i = 0; i < APP_SECTOR_COUNT; i++) {
        bsp_fl_status_t status = erase_sector(APP_SECTORS[i].hal_sector);
        if (status != BSP_FL_OK) {
            return status;
        }
    }
    return BSP_FL_OK;
}

bsp_fl_status_t bsp_fl_app_write(uint32_t dst_addr, const uint8_t *data, uint32_t length)
{
    if (!data || length == 0u)              return BSP_FL_ERR_NULL_PTR;
    if ((dst_addr & 3u) != 0u)             return BSP_FL_ERR_INVALID_ARG;
    if ((length   & 3u) != 0u)             return BSP_FL_ERR_INVALID_ARG;
    if (dst_addr < MEM_APP_START)          return BSP_FL_ERR_INVALID_ARG;
    if (dst_addr + length > MEM_APP_END)   return BSP_FL_ERR_INVALID_ARG;

    return write_words(dst_addr, data, length);
}

bool bsp_fl_read_app_header(bsp_fl_app_header_t *out)
{
    if (!out) {
        return false;
    }

    const bsp_fl_app_header_t *hdr =
        (const bsp_fl_app_header_t *)MEM_APP_HEADER_ADDR;

    if (hdr->magic          != APP_IMAGE_HEADER_MAGIC)   return false;
    if (hdr->header_version != APP_IMAGE_HEADER_VERSION) return false;
    if (hdr->header_size    < sizeof(bsp_fl_app_header_t) ||
        hdr->header_size    > MEM_APP_HEADER_SIZE)        return false;

    memcpy(out, hdr, sizeof(bsp_fl_app_header_t));
    return true;
}

bsp_fl_status_t bsp_fl_app_verify_crc(void)
{
    bsp_fl_app_header_t hdr;

    if (!bsp_fl_read_app_header(&hdr)) {
        return BSP_FL_ERR_INVALID_ARG;
    }

    if (hdr.image_length == 0u ||
        hdr.image_length > MEM_APP_LENGTH) {
        return BSP_FL_ERR_INVALID_ARG;
    }

    uint32_t computed = crc32_hw((const uint8_t *)MEM_APP_START, hdr.image_length);

    return (computed == hdr.image_crc) ? BSP_FL_OK : BSP_FL_ERR_VERIFY;
}