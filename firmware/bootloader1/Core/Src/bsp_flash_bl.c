/**
 * @file    bsp_flash_bl.c
 * @author  Phuong Mai
 * @brief   Flash driver for bootloader — STM32F411CEU6
 */

#include "bsp_flash_bl.h"
#include "memorylayout.h"
#include "stm32f4xx_hal.h"

#include <stddef.h>
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
    __HAL_FLASH_CLEAR_FLAG(FLASH_FLAG_EOP | FLASH_FLAG_OPERR | FLASH_FLAG_WRPERR | 
                           FLASH_FLAG_PGAERR | FLASH_FLAG_PGPERR | FLASH_FLAG_PGSERR);
    HAL_StatusTypeDef status = HAL_FLASHEx_Erase(&erase, &sector_error);
    HAL_FLASH_Lock();

    return (status == HAL_OK) ? BSP_FL_OK : BSP_FL_ERR_ERASE;
}

static bsp_fl_status_t write_words(uint32_t addr, const uint8_t *data, uint32_t length)
{
    const uint32_t *words = (const uint32_t *)data;
    uint32_t        count = length / 4u;

    HAL_FLASH_Unlock();
    __HAL_FLASH_CLEAR_FLAG(FLASH_FLAG_EOP | FLASH_FLAG_OPERR | FLASH_FLAG_WRPERR | 
                           FLASH_FLAG_PGAERR | FLASH_FLAG_PGPERR | FLASH_FLAG_PGSERR);

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

// static uint32_t crc32_hw(const uint8_t *data, uint32_t length)
// {
//     /* Use STM32 hardware CRC32 peripheral (polynomial 0x04C11DB7) */
//     __HAL_RCC_CRC_CLK_ENABLE();

//     CRC->CR = CRC_CR_RESET;

//     const uint32_t *words     = (const uint32_t *)data;
//     uint32_t        word_count = length / 4u;

//     for (uint32_t i = 0; i < word_count; i++) {
//         CRC->DR = words[i];
//     }

//     /* Handle remaining bytes (< 4) by padding with 0xFF */
//     uint32_t remainder = length % 4u;
//     if (remainder > 0u) {
//         uint32_t tail = 0xFFFFFFFFu;
//         memcpy(&tail, data + word_count * 4u, remainder);
//         CRC->DR = tail;
//     }

//     return CRC->DR;
// }

static uint32_t crc32_hw_with_zero_word(const uint8_t *data,
                                        uint32_t length,
                                        uint32_t zero_word_addr)
{
    __HAL_RCC_CRC_CLK_ENABLE();

    CRC->CR = CRC_CR_RESET;

    uint32_t start_addr = (uint32_t)data;
    uint32_t word_count = length / 4u;

    for (uint32_t i = 0; i < word_count; i++) {
        uint32_t addr = start_addr + i * 4u;
        if (addr == zero_word_addr) {
            CRC->DR = 0u;
        } else {
            CRC->DR = *(const uint32_t *)addr;
        }
    }

    uint32_t remainder = length % 4u;
    if (remainder > 0u) {
        uint32_t tail_addr = start_addr + word_count * 4u;
        uint32_t tail = 0xFFFFFFFFu;
        memcpy(&tail, (const void *)tail_addr, remainder);

        if (zero_word_addr >= tail_addr && zero_word_addr < (tail_addr + remainder)) {
            uint32_t zero_start = zero_word_addr - tail_addr;
            uint32_t zero_end = zero_start + 4u;
            if (zero_end > remainder) {
                zero_end = remainder;
            }
            for (uint32_t i = zero_start; i < zero_end; i++) {
                ((uint8_t *)&tail)[i] = 0u;
            }
        }

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
    return bsp_fl_app_verify_crc_ex(NULL, NULL, NULL);
}

bsp_fl_status_t bsp_fl_app_verify_crc_ex(uint32_t *out_image_length,
                                         uint32_t *out_expected_crc,
                                         uint32_t *out_computed_crc)
{
    bsp_fl_app_header_t hdr;

    if (!bsp_fl_read_app_header(&hdr)) {
        return BSP_FL_ERR_INVALID_ARG;
    }

    if (out_image_length) {
        *out_image_length = hdr.image_length;
    }
    if (out_expected_crc) {
        *out_expected_crc = hdr.image_crc;
    }

    if (hdr.image_length == 0u ||
        hdr.image_length > MEM_APP_LENGTH) {
        return BSP_FL_ERR_INVALID_ARG;
    }

    uint32_t crc_field_addr = MEM_APP_HEADER_ADDR + (uint32_t)offsetof(bsp_fl_app_header_t, image_crc);
    uint32_t computed = crc32_hw_with_zero_word((const uint8_t *)MEM_APP_START,
                                                hdr.image_length,
                                                crc_field_addr);
    if (out_computed_crc) {
        *out_computed_crc = computed;
    }

    return (computed == hdr.image_crc) ? BSP_FL_OK : BSP_FL_ERR_VERIFY;
}

uint16_t DFU_Erase_AppSectors(void)
{
    return (bsp_fl_app_erase() == BSP_FL_OK) ? 0u /* USBD_OK */ : 1u /* USBD_FAIL */;
}

uint32_t DFU_GetSectorFromAddress(uint32_t address)
{
    if (address < 0x08010000UL) return FLASH_SECTOR_3;   /* 0x0800C000-0x0800FFFF 16 KB */
    if (address < 0x08020000UL) return FLASH_SECTOR_4;   /* 0x08010000-0x0801FFFF 64 KB */
    if (address < 0x08040000UL) return FLASH_SECTOR_5;   /* 0x08020000-0x0803FFFF 128 KB */
    if (address < 0x08060000UL) return FLASH_SECTOR_6;   /* data storage */
    return FLASH_SECTOR_7;
}
