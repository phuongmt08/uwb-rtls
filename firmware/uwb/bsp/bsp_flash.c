/**
 * @file       bsp_flash.c
 * @copyright  Copyright (C) 2019 ITRVN.
 * @license    This project is released under the Fiot License.
 * @version    1.3.0
 * @date       2026-03-05
 * @author     Phuong Mai
 * @brief      Generic metadata-region dual-sector flash driver
 * @note       Architecture per sector: [Metadata 16KB][Data (rest)]
 */

/* Includes ----------------------------------------------------------- */
#include "bsp_flash.h"

#include "memorylayout.h"
#include "stm32f4xx_hal.h"
#include "sys_logger.h"
#include "version.h"

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

/* Private defines ---------------------------------------------------- */
//clang-format off
#define FLASH_ERASED_VALUE 0xFFFFFFFFu

#if defined(FLASH_FLAG_RDERR)
#define FLASH_PENDING_FLAGS \
  (FLASH_FLAG_EOP | FLASH_FLAG_OPERR | FLASH_FLAG_WRPERR | FLASH_FLAG_PGAERR | FLASH_FLAG_PGPERR | FLASH_FLAG_PGSERR | FLASH_FLAG_RDERR)
#else
#define FLASH_PENDING_FLAGS \
  (FLASH_FLAG_EOP | FLASH_FLAG_OPERR | FLASH_FLAG_WRPERR | FLASH_FLAG_PGAERR | FLASH_FLAG_PGPERR | FLASH_FLAG_PGSERR)
#endif

/* Sector layout: [Metadata 16KB][Data region (rest)] */
#define METADATA_START     0u
#define DATA_START         BSP_FLASH_METADATA_SIZE

/* Private enumerate/structure ---------------------------------------- */
/* Private macros ----------------------------------------------------- */
/* Public variables --------------------------------------------------- */
/* Private variables -------------------------------------------------- */
/* Private function prototypes ---------------------------------------- */

/** @brief Convert flash address to STM32F4 sector number */
static int addr_to_sector(uint32_t base_addr);

/** @brief Convert any flash address to the containing STM32F4 sector number */
static int addr_to_containing_sector(uint32_t addr);

/** @brief Clear stale FLASH status flags before a new erase/program operation */
static void flash_clear_pending_flags(void);

/** @brief Log HAL FLASH failure details */
static void flash_log_hal_failure(const char    *op,
                                  uint32_t       addr,
                                  int            sector,
                                  HAL_StatusTypeDef hal_status,
                                  uint32_t       sector_error);

/** @brief Erase flash sector by base address */
static bsp_flash_status_t flash_erase_sector(uint32_t base);

/** @brief Write 32-bit word to flash (assumes flash unlocked) */
static int flash_write_word(uint32_t addr, uint32_t w);

/** @brief Write data block to flash (4-byte aligned) */
static bsp_flash_status_t flash_write_block(uint32_t addr, const void *data, uint32_t size);

/** @brief Get metadata region base address */
static uint32_t get_metadata_base(const bsp_flash_region_t *r);

/** @brief Find latest valid metadata entry in metadata region */
static bsp_flash_metadata_entry_t *find_latest_metadata(const bsp_flash_region_t *r, uint32_t *out_offset);

/** @brief Validate single metadata entry */
static bool is_entry_valid(const bsp_flash_metadata_entry_t *entry, bsp_flash_crc32_fn crc_cb);

/** @brief Append new metadata entry to metadata region */
static bsp_flash_status_t append_metadata_entry(uint32_t           meta_base,
                                                 uint32_t           entry_offset,
                                                 uint32_t           gen,
                                                 uint32_t           data_offset,
                                                 uint32_t           data_length,
                                                 uint32_t           timestamp,
                                                 uint32_t           data_crc,
                                                 uint32_t           log_read_pos,
                                                 bsp_flash_crc32_fn crc_cb);

/** @brief Swap active sector when metadata or data partition region full */
static bsp_flash_status_t swap_sector(bsp_flash_dual_t *dr,
                                       const void       *data,
                                       uint32_t          size,
                                       uint32_t          sub_offset,
                                       uint32_t          sub_length);

/** @brief Copy the latest config snapshot from one sector into another. */
static bsp_flash_status_t copy_latest_cfg_snapshot(const bsp_flash_region_t *src,
                                                   bsp_flash_region_t       *dst,
                                                   bsp_flash_crc32_fn        crc_cb,
                                                   uint32_t                 *out_next_meta_offset);

/* bsp_app_image_header_t is declared in bsp_flash.h */

static const bsp_app_image_header_t g_bsp_app_image_header
    __attribute__((section(".app_header"), used)) = {
  .magic            = APP_IMAGE_HEADER_MAGIC,
  .header_version   = APP_IMAGE_HEADER_VERSION,
  .header_size      = sizeof(bsp_app_image_header_t),
  .fw_major         = FW_VERSION_MAJOR,
  .fw_minor         = FW_VERSION_MINOR,
  .fw_patch         = FW_VERSION_PATCH,
  .fw_build         = FW_VERSION_BUILD,
  .fw_gitsha        = FW_VERSION_GITSHA,
  .image_timestamp  = FW_IMAGE_TIMESTAMP,
  .image_length     = FW_IMAGE_LENGTH,
  .image_crc        = FW_IMAGE_CRC,
  .reserved         = {0}
};
//clang-format on

/* Function definitions ----------------------------------------------- */
bsp_flash_status_t bsp_flash_dual_init(bsp_flash_dual_t      *dr,
                                       uint32_t               base0,
                                       uint32_t               size0,
                                       uint32_t               base1,
                                       uint32_t               size1,
                                       bsp_flash_crc32_fn     crc32_fn,
                                       bsp_flash_timestamp_fn timestamp_fn)
{
  if (!dr)
    return BSP_FLASH_ERR_NULL_PTR;

  /* Store callbacks */
  dr->crc32_cb     = crc32_fn;
  dr->timestamp_cb = timestamp_fn;

  /* Validate sector addresses and sizes */
  if (addr_to_sector(base0) < 0 || addr_to_sector(base1) < 0)
  {
    RLOG_E(LOG_OBJECT_CODE_FLASH, ERR_INVALID_PARAM, "Invalid sector address: base0=0x%08lX base1=0x%08lX",
           (unsigned long) base0, (unsigned long) base1);
    return BSP_FLASH_ERR_INVALID_ARG;
  }

  /* Size must be > 16KB (metadata) + at least 256 bytes data */
  uint32_t min_size = BSP_FLASH_METADATA_SIZE + 256u;
  if (size0 < min_size || size1 < min_size)
    return BSP_FLASH_ERR_INVALID_ARG;

  if ((size0 & 3u) != 0u || (size1 & 3u) != 0u)
    return BSP_FLASH_ERR_INVALID_ARG;

  dr->sectors[0].base   = base0;
  dr->sectors[0].size   = size0;
  dr->sectors[0].inited = 1u;
  dr->sectors[1].base   = base1;
  dr->sectors[1].size   = size1;
  dr->sectors[1].inited = 1u;

  /* Find latest metadata in each sector */
  uint32_t                    offset0 = 0u;
  uint32_t                    offset1 = 0u;
  bsp_flash_metadata_entry_t *e0      = find_latest_metadata(&dr->sectors[0], &offset0);
  bsp_flash_metadata_entry_t *e1      = find_latest_metadata(&dr->sectors[1], &offset1);

  bool e0_valid = (e0 != NULL) && is_entry_valid(e0, crc32_fn);
  bool e1_valid = (e1 != NULL) && is_entry_valid(e1, crc32_fn);

  if (e0_valid && e1_valid)
  {
    /* Both valid: choose newer generation */
    int32_t gen_diff = (int32_t) (e0->gen - e1->gen);
    dr->active       = (gen_diff > 0) ? 0u : 1u;
  }
  else if (e0_valid)
  {
    dr->active = 0u;
  }
  else if (e1_valid)
  {
    dr->active = 1u;
  }
  else
  {
    RLOG_W(LOG_OBJECT_CODE_FLASH, " No valid metadata found. Erasing sector0 and writing first entry.");
    if (flash_erase_sector(base0) != BSP_FLASH_OK)
    {
      RLOG_E(LOG_OBJECT_CODE_FLASH, ERR_HAL, "Erase sector0 failed");
      return BSP_FLASH_ERR_ERASE;
    }
    if (append_metadata_entry(base0, 0u, 0u, 0u, 0u, 0u, 0u, 0xFFFFFFFFu, crc32_fn) != BSP_FLASH_OK)
    {
      RLOG_E(LOG_OBJECT_CODE_FLASH, ERR_HAL, "Write first metadata entry failed");
      return BSP_FLASH_ERR_PROGRAM;
      dr->active = 0u;
    }
    dr->active = 0u;
  }

  return BSP_FLASH_OK;
}

bsp_flash_status_t bsp_flash_cfg_write(bsp_flash_dual_t *dr, const void *data, uint32_t size)
{
  const uint32_t sub_offset = BSP_FLASH_CFG_DATA_OFFSET;
  const uint32_t sub_length = BSP_FLASH_CFG_DATA_LENGTH;
  if (!dr || !data || size == 0u)
    return BSP_FLASH_ERR_NULL_PTR;
  if ((size & 3u) != 0u || (sub_offset & 3u) != 0u)
    return BSP_FLASH_ERR_INVALID_ARG;
  if (size > sub_length)
    return BSP_FLASH_ERR_INVALID_ARG;

  bsp_flash_region_t *active_region    = &dr->sectors[dr->active];
  uint32_t            data_region_size = active_region->size - BSP_FLASH_METADATA_SIZE;

  /* sub-partition must fit inside the data region */
  if ((sub_offset + sub_length) > data_region_size)
    return BSP_FLASH_ERR_INVALID_ARG;

  uint32_t next_meta_offset = 0u;
  uint32_t next_data_offset = sub_offset;
  uint32_t last_gen         = 0u;
  bool     has_valid_gen    = false;
  uint32_t cfg_record_count = 0u;

  while (next_meta_offset < BSP_FLASH_METADATA_SIZE)
  {
    bsp_flash_metadata_entry_t *e = (bsp_flash_metadata_entry_t *) (active_region->base + next_meta_offset);

    if (e->marker == FLASH_ERASED_VALUE)
      break;

    if (e->marker == BSP_FLASH_ENTRY_MARKER && is_entry_valid(e, dr->crc32_cb))
    {
      last_gen      = e->gen;
      has_valid_gen = true;

      if ((e->data_length > 0u) && (e->data_offset >= sub_offset)
          && (e->data_offset < (sub_offset + sub_length)))
      {
        cfg_record_count++;
        uint32_t after_last = e->data_offset + e->data_length;
        if (after_last > next_data_offset)
          next_data_offset = after_last;
      }
    }

    next_meta_offset += BSP_FLASH_ENTRY_SIZE;
  }

  uint32_t gen = has_valid_gen ? (last_gen + 1u) : 0u;

  if ((next_meta_offset + BSP_FLASH_ENTRY_SIZE) > BSP_FLASH_METADATA_SIZE)
  {
    RLOG_W(LOG_OBJECT_CODE_SYS_CFG,
           "[FLASH][SWAP] partition=cfg reason=metadata_full records=%lu bytes_used=%lu active=%u->%u",
           (unsigned long) cfg_record_count, (unsigned long) (next_data_offset - sub_offset),
           (unsigned) dr->active, (unsigned) (1u - dr->active));
    return swap_sector(dr, data, size, sub_offset, sub_length);
  }

  if ((next_data_offset + size) > (sub_offset + sub_length))
  {
    RLOG_W(LOG_OBJECT_CODE_SYS_CFG,
           "[FLASH][SWAP] partition=cfg reason=data_full records=%lu bytes_used=%lu active=%u->%u",
           (unsigned long) cfg_record_count, (unsigned long) (next_data_offset - sub_offset),
           (unsigned) dr->active, (unsigned) (1u - dr->active));
    return swap_sector(dr, data, size, sub_offset, sub_length);
  }

  /* Write data to flash */
  uint32_t write_addr = active_region->base + BSP_FLASH_METADATA_SIZE + next_data_offset;

  HAL_FLASH_Unlock();
  if (flash_write_block(write_addr, data, size) != BSP_FLASH_OK)
  {
    HAL_FLASH_Lock();
    return BSP_FLASH_ERR_PROGRAM;
  }
  HAL_FLASH_Lock();

  /* Calculate CRC and timestamp */
  uint32_t data_crc  = dr->crc32_cb ? dr->crc32_cb(data, size) : 0u;
  uint32_t timestamp = dr->timestamp_cb ? dr->timestamp_cb() : 0u;

  return append_metadata_entry(active_region->base, next_meta_offset, gen, next_data_offset, size, timestamp,
                               data_crc, 0xFFFFFFFFu, dr->crc32_cb);
}

uint32_t bsp_flash_cfg_read(const bsp_flash_dual_t *dr, void *out, uint32_t max_size)
{
  const uint32_t sub_offset = BSP_FLASH_CFG_DATA_OFFSET;
  const uint32_t sub_length = BSP_FLASH_CFG_DATA_LENGTH;
  if (!dr || !out || max_size == 0u)
    return 0u;

  const bsp_flash_region_t         *best_region = NULL;
  const bsp_flash_metadata_entry_t *best_entry  = NULL;

  for (uint8_t region_idx = 0u; region_idx < 2u; region_idx++)
  {
    const bsp_flash_region_t *region      = &dr->sectors[region_idx];
    uint32_t                  scan_offset = 0u;

    while (scan_offset < BSP_FLASH_METADATA_SIZE)
    {
      const bsp_flash_metadata_entry_t *e = (const bsp_flash_metadata_entry_t *) (region->base + scan_offset);

      if (e->marker == FLASH_ERASED_VALUE)
        break; /* end of written entries */

      if (e->marker == BSP_FLASH_ENTRY_MARKER && is_entry_valid(e, dr->crc32_cb) && e->data_length > 0u
          && e->data_offset >= sub_offset && e->data_offset < (sub_offset + sub_length))
      {
        if (!best_entry || ((int32_t) (e->gen - best_entry->gen) > 0))
        {
          best_entry  = e;
          best_region = region;
        }
      }

      scan_offset += BSP_FLASH_ENTRY_SIZE;
    }
  }

  if (!best_entry || !best_region)
    return 0u;

  uint32_t copy_len  = (best_entry->data_length > max_size) ? max_size : best_entry->data_length;
  uint32_t read_addr = best_region->base + BSP_FLASH_METADATA_SIZE + best_entry->data_offset;

  memcpy(out, (const void *) read_addr, copy_len);

  /* Verify CRC if available */
  if (dr->crc32_cb && best_entry->crc32 != 0u)
  {
    uint32_t computed = dr->crc32_cb(out, copy_len);
    if (computed != best_entry->crc32)
      return 0u;
  }

  return copy_len;
}

bool bsp_flash_app_header_valid(void)
{
  const bsp_app_image_header_t *hdr = (const bsp_app_image_header_t *) MEM_APP_HEADER_ADDR;
  if (hdr->magic != APP_IMAGE_HEADER_MAGIC)
    return false;
  if (hdr->header_version != APP_IMAGE_HEADER_VERSION)
    return false;
  if (hdr->header_size < sizeof(bsp_app_image_header_t) || hdr->header_size > MEM_APP_HEADER_SIZE)
    return false;
  return true;
}

bool bsp_flash_read_app_header(void *out, uint32_t size)
{
  if (!out || size < sizeof(bsp_app_image_header_t))
    return false;
  if (!bsp_flash_app_header_valid())
    return false;
  const bsp_app_image_header_t *hdr = (const bsp_app_image_header_t *) MEM_APP_HEADER_ADDR;
  memcpy(out, hdr, sizeof(bsp_app_image_header_t));
  return true;
}

/* Private definitions ----------------------------------------------- */

static int addr_to_sector(uint32_t base_addr)
{
  switch (base_addr)
  {
  case 0x08000000u: return FLASH_SECTOR_0;
  case 0x08004000u: return FLASH_SECTOR_1;
  case 0x08008000u: return FLASH_SECTOR_2;
  case 0x0800C000u: return FLASH_SECTOR_3;
  case 0x08010000u: return FLASH_SECTOR_4;
  case 0x08020000u: return FLASH_SECTOR_5;
  case 0x08040000u: return FLASH_SECTOR_6;
  case 0x08060000u: return FLASH_SECTOR_7;
  default: return -1;
  }
}

static int addr_to_containing_sector(uint32_t addr)
{
  if (addr < 0x08000000u || addr >= 0x08080000u)
    return -1;
  if (addr < 0x08004000u)
    return FLASH_SECTOR_0;
  if (addr < 0x08008000u)
    return FLASH_SECTOR_1;
  if (addr < 0x0800C000u)
    return FLASH_SECTOR_2;
  if (addr < 0x08010000u)
    return FLASH_SECTOR_3;
  if (addr < 0x08020000u)
    return FLASH_SECTOR_4;
  if (addr < 0x08040000u)
    return FLASH_SECTOR_5;
  if (addr < 0x08060000u)
    return FLASH_SECTOR_6;
  return FLASH_SECTOR_7;
}

static void flash_clear_pending_flags(void)
{
  __HAL_FLASH_CLEAR_FLAG(FLASH_PENDING_FLAGS);
}

static void flash_log_hal_failure(const char    *op,
                                  uint32_t       addr,
                                  int            sector,
                                  HAL_StatusTypeDef hal_status,
                                  uint32_t       sector_error)
{
  RLOG_E(LOG_OBJECT_CODE_FLASH, ERR_HAL,
         "[FLASH] %s failed: addr=0x%08lX sector=%d hal=%d hal_err=0x%08lX sector_err=0x%08lX SR=0x%08lX CR=0x%08lX OPTCR=0x%08lX",
         op, (unsigned long) addr, sector, (int) hal_status, (unsigned long) HAL_FLASH_GetError(),
         (unsigned long) sector_error, (unsigned long) FLASH->SR, (unsigned long) FLASH->CR,
         (unsigned long) FLASH->OPTCR);
}

static bsp_flash_status_t flash_erase_sector(uint32_t base)
{
  int sector = addr_to_sector(base);
  if (sector < 0)
    return BSP_FLASH_ERR_INVALID_ARG;

  FLASH_EraseInitTypeDef erase        = { 0 };
  uint32_t               sector_error = 0xFFFFFFFFu;

  HAL_FLASH_Unlock();
  flash_clear_pending_flags();
  erase.TypeErase    = FLASH_TYPEERASE_SECTORS;
  erase.VoltageRange = FLASH_VOLTAGE_RANGE_3;
  erase.Sector       = (uint32_t) sector;
  erase.NbSectors    = 1;
  HAL_StatusTypeDef hal_status = HAL_FLASHEx_Erase(&erase, &sector_error);
  if (hal_status != HAL_OK)
  {
    flash_log_hal_failure("erase", base, sector, hal_status, sector_error);
    HAL_FLASH_Lock();
    return BSP_FLASH_ERR_ERASE;
  }
  HAL_FLASH_Lock();
  return BSP_FLASH_OK;
}

static int flash_write_word(uint32_t addr, uint32_t w)
{
  if ((addr & 3u) != 0u)
    return -1;
  flash_clear_pending_flags();
  HAL_StatusTypeDef hal_status = HAL_FLASH_Program(FLASH_TYPEPROGRAM_WORD, addr, w);
  if (hal_status != HAL_OK)
  {
    flash_log_hal_failure("program", addr, addr_to_containing_sector(addr), hal_status, 0xFFFFFFFFu);
    return -1;
  }
  return 0;
}

static bsp_flash_status_t flash_write_block(uint32_t addr, const void *data, uint32_t size)
{
  if ((size & 3u) != 0u)
    return BSP_FLASH_ERR_INVALID_ARG; /* require 4-byte multiple */
  if ((addr & 3u) != 0u)
    return BSP_FLASH_ERR_INVALID_ARG;

  const uint32_t *p     = (const uint32_t *) data;
  uint32_t        words = size / 4u;
  for (uint32_t i = 0; i < words; i++)
  {
    if (flash_write_word(addr + i * 4u, p[i]) != 0)
      return BSP_FLASH_ERR_PROGRAM;
  }
  return BSP_FLASH_OK;
}

/* Get metadata region base address */
static uint32_t get_metadata_base(const bsp_flash_region_t *r)
{
  return r->base + METADATA_START;
}

/* Find latest valid metadata entry in metadata region */
static bsp_flash_metadata_entry_t *find_latest_metadata(const bsp_flash_region_t *r, uint32_t *out_offset)
{
  uint32_t meta_base = get_metadata_base(r);
  uint32_t offset    = 0u;

  bsp_flash_metadata_entry_t *last_valid  = NULL;
  uint32_t                    last_offset = 0u;

  /* Scan metadata region for entries */
  while (offset < BSP_FLASH_METADATA_SIZE)
  {
    bsp_flash_metadata_entry_t *entry = (bsp_flash_metadata_entry_t *) (meta_base + offset);

    /* Check if this location is erased (0xFFFFFFFF) */
    if (entry->marker == FLASH_ERASED_VALUE)
    {
      /* Reached end of written entries */
      break;
    }

    /* Check if entry has valid marker */
    if (entry->marker == BSP_FLASH_ENTRY_MARKER)
    {
      /* Found potential entry, will validate later */
      last_valid  = entry;
      last_offset = offset;
    }

    offset += BSP_FLASH_ENTRY_SIZE;
  }

  if (out_offset && last_valid)
  {
    *out_offset = last_offset;
  }

  return last_valid;
}

/* Validate single metadata entry */
static bool is_entry_valid(const bsp_flash_metadata_entry_t *entry, bsp_flash_crc32_fn crc_cb)
{
  if (!entry)
    return false;

  /* Check marker */
  if (entry->marker != BSP_FLASH_ENTRY_MARKER)
    return false;

  /* Check generation not erased */
  if (entry->gen == FLASH_ERASED_VALUE)
    return false;

  /* Verify entry CRC if callback available */
  if (crc_cb && entry->entry_crc != 0u)
  {
    uint32_t computed = crc_cb(entry, 24u); /* First 6 words */
    if (computed != entry->entry_crc)
      return false;
  }

  return true;
}

/* Append new metadata entry to metadata region */
static bsp_flash_status_t append_metadata_entry(uint32_t           meta_base,
                                                uint32_t           entry_offset,
                                                uint32_t           gen,
                                                uint32_t           data_offset,
                                                uint32_t           data_length,
                                                uint32_t           timestamp,
                                                uint32_t           data_crc,
                                                uint32_t           log_read_pos,
                                                bsp_flash_crc32_fn crc_cb)
{
  /* Prepare entry */
  bsp_flash_metadata_entry_t entry;
  entry.marker       = BSP_FLASH_ENTRY_MARKER;
  entry.gen          = gen;
  entry.data_offset  = data_offset;
  entry.data_length  = data_length;
  entry.timestamp    = timestamp;
  entry.crc32        = data_crc;
  entry.entry_crc    = 0u;
  entry.log_read_pos = log_read_pos;

  /* Calculate entry CRC (first 6 words = 24 bytes: marker..crc32) */
  uint32_t entry_crc = crc_cb ? crc_cb(&entry, 24u) : 0u;
  entry.entry_crc    = entry_crc;

  /* Write entry to flash */
  uint32_t write_addr = meta_base + entry_offset;

  HAL_FLASH_Unlock();
  bsp_flash_status_t status = flash_write_block(write_addr, &entry, sizeof(entry));
  HAL_FLASH_Lock();

  return status;
}

/* Sector swap: erase new sector, write data at sub_offset, fresh metadata */
static bsp_flash_status_t
swap_sector(bsp_flash_dual_t *dr, const void *data, uint32_t size, uint32_t sub_offset, uint32_t sub_length)
{
  (void) sub_length;
  uint8_t                   new_idx = 1u - dr->active;
  const bsp_flash_region_t *old     = &dr->sectors[dr->active];
  bsp_flash_region_t       *nr      = &dr->sectors[new_idx];

  RLOG_W(LOG_OBJECT_CODE_FLASH, "Swapping sector: old=%u new=%u", dr->active, new_idx);
  if (flash_erase_sector(nr->base) != BSP_FLASH_OK)
  {
    RLOG_E(LOG_OBJECT_CODE_FLASH, ERR_HAL, "Erase new sector failed: idx=%u base=0x%08lX", new_idx,
           (unsigned long) nr->base);
    return BSP_FLASH_ERR_ERASE;
  }

  /* Preserve the latest config snapshot in the new sector before writing
   * the record that triggered the swap. This keeps config alive when the
   * active sector flips because of log growth.
   */
  uint32_t next_meta_offset = 0u;
  if (copy_latest_cfg_snapshot(old, nr, dr->crc32_cb, &next_meta_offset) != BSP_FLASH_OK)
  {
    return BSP_FLASH_ERR_PROGRAM;
  }

  /* Determine new generation */
  uint32_t                    old_offset = 0u;
  bsp_flash_metadata_entry_t *old_entry  = find_latest_metadata(old, &old_offset);
  uint32_t                    new_gen    = 0u;
  if (old_entry && is_entry_valid(old_entry, dr->crc32_cb))
  {
    new_gen = old_entry->gen + 1u;
    RLOG_I(LOG_OBJECT_CODE_FLASH, "old_entry valid. old_gen=%lu new_gen=%lu", (unsigned long) old_entry->gen,
           (unsigned long) new_gen);
  }
  else
  {
    RLOG_W(LOG_OBJECT_CODE_FLASH, "No valid old_entry. new_gen=0");
  }

  /* Write data at sub_offset in the new sector's data region */
  uint32_t write_addr = nr->base + BSP_FLASH_METADATA_SIZE + sub_offset;

  HAL_FLASH_Unlock();
  if (flash_write_block(write_addr, data, size) != BSP_FLASH_OK)
  {
    HAL_FLASH_Lock();
    RLOG_E(LOG_OBJECT_CODE_FLASH, ERR_HAL, "Write data failed at addr=0x%08lX size=%lu",
           (unsigned long) write_addr, (unsigned long) size);
    return BSP_FLASH_ERR_PROGRAM;
  }
  HAL_FLASH_Lock();

  uint32_t data_crc  = dr->crc32_cb ? dr->crc32_cb(data, size) : 0u;
  uint32_t timestamp = dr->timestamp_cb ? dr->timestamp_cb() : 0u;

  /* Write metadata after the optional copied config snapshot. */
  if (append_metadata_entry(nr->base, next_meta_offset, new_gen, sub_offset, size, timestamp, data_crc, 0xFFFFFFFFu,
                            dr->crc32_cb)
      != BSP_FLASH_OK)
  {
    RLOG_E(LOG_OBJECT_CODE_FLASH, ERR_HAL, "Write metadata failed");
    return BSP_FLASH_ERR_PROGRAM;
  }

  /* Switch active sector */
  dr->active = new_idx;
  RLOG_I(LOG_OBJECT_CODE_FLASH, " Swap done. Now active=%u", dr->active);
  return BSP_FLASH_OK;
}

static bsp_flash_status_t copy_latest_cfg_snapshot(const bsp_flash_region_t *src,
                                                   bsp_flash_region_t       *dst,
                                                   bsp_flash_crc32_fn        crc_cb,
                                                   uint32_t                 *out_next_meta_offset)
{
  const uint32_t sub_offset = BSP_FLASH_CFG_DATA_OFFSET;
  const uint32_t sub_length = BSP_FLASH_CFG_DATA_LENGTH;
  const bsp_flash_metadata_entry_t *best_entry = NULL;

  if (!src || !dst || !out_next_meta_offset)
    return BSP_FLASH_ERR_NULL_PTR;

  *out_next_meta_offset = 0u;

  for (uint32_t scan = 0u; scan < BSP_FLASH_METADATA_SIZE; scan += BSP_FLASH_ENTRY_SIZE)
  {
    const bsp_flash_metadata_entry_t *e = (const bsp_flash_metadata_entry_t *) (src->base + scan);

    if (e->marker == FLASH_ERASED_VALUE)
      break;

    if (e->marker == BSP_FLASH_ENTRY_MARKER && is_entry_valid(e, crc_cb) && e->data_length > 0u
        && e->data_offset >= sub_offset && e->data_offset < (sub_offset + sub_length))
    {
      if (!best_entry || ((int32_t) (e->gen - best_entry->gen) > 0))
      {
        best_entry = e;
      }
    }
  }

  if (!best_entry)
    return BSP_FLASH_OK;

  uint32_t read_addr  = src->base + BSP_FLASH_METADATA_SIZE + best_entry->data_offset;
  uint32_t write_addr = dst->base + BSP_FLASH_METADATA_SIZE + best_entry->data_offset;

  HAL_FLASH_Unlock();
  if (flash_write_block(write_addr, (const void *) read_addr, best_entry->data_length) != BSP_FLASH_OK)
  {
    HAL_FLASH_Lock();
    return BSP_FLASH_ERR_PROGRAM;
  }

  if (append_metadata_entry(dst->base, 0u, best_entry->gen, best_entry->data_offset, best_entry->data_length,
                            best_entry->timestamp, best_entry->crc32, best_entry->log_read_pos, crc_cb)
      != BSP_FLASH_OK)
  {
    HAL_FLASH_Lock();
    return BSP_FLASH_ERR_PROGRAM;
  }

  HAL_FLASH_Lock();
  *out_next_meta_offset = BSP_FLASH_ENTRY_SIZE;
  return BSP_FLASH_OK;
}

bsp_flash_status_t
bsp_flash_log_get_positions(const bsp_flash_dual_t *dr, uint32_t *out_write_pos, uint32_t *out_read_pos)
{
  const uint32_t sub_offset = BSP_FLASH_LOG_DATA_OFFSET;
  const uint32_t sub_length = BSP_FLASH_LOG_DATA_LENGTH;
  if (!dr)
    return BSP_FLASH_ERR_NULL_PTR;

  const bsp_flash_region_t *active    = &dr->sectors[dr->active];
  uint32_t                  write_pos = 0u;
  uint32_t                  read_pos  = 0u;
  uint32_t                  scan      = 0u;
  uint32_t                  last_gen  = 0u;

  while (scan < BSP_FLASH_METADATA_SIZE)
  {
    const bsp_flash_metadata_entry_t *e = (const bsp_flash_metadata_entry_t *) (active->base + scan);
    if (e->marker == FLASH_ERASED_VALUE)
      break;
    if (e->marker == BSP_FLASH_ENTRY_MARKER && is_entry_valid(e, dr->crc32_cb))
    {
      last_gen = e->gen;

      if (e->data_offset >= sub_offset && e->data_offset < (sub_offset + sub_length))
      {
        if (e->data_length > 0u)
        {
          uint32_t end = (e->data_offset - sub_offset) + e->data_length;
          if (end > write_pos)
            write_pos = end;
        }
        if (e->log_read_pos != 0xFFFFFFFFu)
          read_pos = e->log_read_pos;
      }
    }
    scan += BSP_FLASH_ENTRY_SIZE;
  }

  if (out_write_pos)
    *out_write_pos = (last_gen << 20) | write_pos;
  if (out_read_pos) {
    if (read_pos == 0u || read_pos == 0xFFFFFFFFu) {
        *out_read_pos = (last_gen << 20) | 0;
    } else {
        uint32_t rgen = read_pos >> 20;
        if (last_gen > 0u && rgen < (last_gen - 1u)) {
            /* Pointer is dead (sector overwritten)! Force it to oldest alive generation */
            uint8_t inactive_idx = 1u - dr->active;
            bsp_flash_metadata_entry_t *oe = (bsp_flash_metadata_entry_t *)dr->sectors[inactive_idx].base;
            if (oe->marker == BSP_FLASH_ENTRY_MARKER && oe->gen == (last_gen - 1u)) {
                *out_read_pos = ((last_gen - 1u) << 20) | 0;
            } else {
                *out_read_pos = (last_gen << 20) | 0;
            }
        } else {
            *out_read_pos = read_pos;
        }
    }
  }
  return BSP_FLASH_OK;
}

uint32_t bsp_flash_log_read_virtual(const bsp_flash_dual_t *dr, uint32_t virtual_offset, void *out, uint32_t length)
{
    if (!dr || !out || length == 0u)
        return 0u;

    uint32_t req_gen = virtual_offset >> 20;
    uint32_t local_offset = virtual_offset & 0xFFFFF;

    if (local_offset >= BSP_FLASH_LOG_DATA_LENGTH)
        return 0u;

    /* Find which sector contains this log generation.
     * Scan full metadata instead of checking the first entry only, because
     * slot 0 may hold a copied config snapshot with an older generation.
     */
    uint8_t target_idx = 0xFFu;
    for (uint8_t i = 0u; i < 2u; i++) {
      uint32_t scan = 0u;
      while (scan < BSP_FLASH_METADATA_SIZE) {
        const bsp_flash_metadata_entry_t *e =
          (const bsp_flash_metadata_entry_t *)(dr->sectors[i].base + scan);

        if (e->marker == FLASH_ERASED_VALUE) {
          break;
        }

        if (e->marker == BSP_FLASH_ENTRY_MARKER && is_entry_valid(e, dr->crc32_cb)
          && e->gen == req_gen && e->data_length > 0u
          && e->data_offset >= BSP_FLASH_LOG_DATA_OFFSET
          && e->data_offset < (BSP_FLASH_LOG_DATA_OFFSET + BSP_FLASH_LOG_DATA_LENGTH)) {
          target_idx = i;
          break;
        }

        scan += BSP_FLASH_ENTRY_SIZE;
      }

      if (target_idx != 0xFFu) {
        break;
      }
    }

    if (target_idx > 1u)
        return 0u;

    uint32_t avail = BSP_FLASH_LOG_DATA_LENGTH - local_offset;
    uint32_t copy_len = (length > avail) ? avail : length;
    uint32_t read_addr = dr->sectors[target_idx].base + BSP_FLASH_METADATA_SIZE + BSP_FLASH_LOG_DATA_OFFSET + local_offset;

    memcpy(out, (const void *)read_addr, copy_len);
    return copy_len;
}

bsp_flash_status_t bsp_flash_log_update_read_pos(bsp_flash_dual_t *dr, uint32_t read_pos)
{
  if (!dr)
    return BSP_FLASH_ERR_NULL_PTR;

  const uint32_t      sub_offset = BSP_FLASH_LOG_DATA_OFFSET;
  bsp_flash_region_t *active     = &dr->sectors[dr->active];
  uint32_t            scan       = 0u;
  uint32_t            last_gen   = 0u;
  bool                has_gen    = false;
  uint32_t            log_pos    = 0u; /* derived from metadata */

  while (scan < BSP_FLASH_METADATA_SIZE)
  {
    bsp_flash_metadata_entry_t *e = (bsp_flash_metadata_entry_t *) (active->base + scan);
    if (e->marker == FLASH_ERASED_VALUE)
      break;
    if (e->marker == BSP_FLASH_ENTRY_MARKER && is_entry_valid(e, dr->crc32_cb))
    {
      last_gen = e->gen;
      has_gen  = true;
      if (e->data_length > 0u && e->data_offset >= sub_offset
          && e->data_offset < (sub_offset + BSP_FLASH_LOG_DATA_LENGTH))
      {
        uint32_t end = (e->data_offset - sub_offset) + e->data_length;
        if (end > log_pos)
          log_pos = end;
      }
    }
    scan += BSP_FLASH_ENTRY_SIZE;
  }

  if ((scan + BSP_FLASH_ENTRY_SIZE) > BSP_FLASH_METADATA_SIZE)
    return BSP_FLASH_ERR_NO_SPACE; /* metadata region full */

  uint32_t gen       = has_gen ? last_gen : 0u;
  uint32_t timestamp = dr->timestamp_cb ? dr->timestamp_cb() : 0u;

  return append_metadata_entry(active->base, scan, gen, sub_offset + log_pos, /* bookmark */
                               0u,                                            /* data_length = 0 */
                               timestamp, 0u,                                 /* no data CRC */
                               read_pos,                                      /* persisted cursor */
                               dr->crc32_cb);
}

bsp_flash_status_t bsp_flash_log_append(bsp_flash_dual_t *dr,
                                        uint32_t          log_read_pos,
                                        const void       *data,
                                        uint32_t          size,
                                        uint32_t         *out_actual_pos)
{
  if (!dr || !data || size == 0u)
    return BSP_FLASH_ERR_NULL_PTR;
  if ((size & 3u) != 0u)
    return BSP_FLASH_ERR_INVALID_ARG;
  if (size > BSP_FLASH_LOG_DATA_LENGTH)
    return BSP_FLASH_ERR_INVALID_ARG;

  const uint32_t      sub_offset       = BSP_FLASH_LOG_DATA_OFFSET;
  const uint32_t      sub_length       = BSP_FLASH_LOG_DATA_LENGTH;
  bsp_flash_region_t *active           = &dr->sectors[dr->active];
  uint32_t            data_region_size = active->size - BSP_FLASH_METADATA_SIZE;
  if ((sub_offset + sub_length) > data_region_size)
    return BSP_FLASH_ERR_INVALID_ARG;

  /* Scan metadata: find next free slot, highest gen, and current log_pos */
  uint32_t scan             = 0u;
  uint32_t last_gen         = 0u;
  bool     has_valid_gen    = false;
  uint32_t log_pos          = 0u; /* derived from metadata - same as bsp_flash_cfg_write */
  uint32_t log_record_count = 0u;

  while (scan < BSP_FLASH_METADATA_SIZE)
  {
    bsp_flash_metadata_entry_t *e = (bsp_flash_metadata_entry_t *) (active->base + scan);
    if (e->marker == FLASH_ERASED_VALUE)
      break; /* end of written entries — next free slot is here */
    if (e->marker == BSP_FLASH_ENTRY_MARKER && is_entry_valid(e, dr->crc32_cb))
    {
      last_gen      = e->gen;
      has_valid_gen = true;
      if (e->data_length > 0u && e->data_offset >= sub_offset && e->data_offset < (sub_offset + sub_length))
      {
        log_record_count++;
        uint32_t end = (e->data_offset - sub_offset) + e->data_length;
        if (end > log_pos)
          log_pos = end;
      }
    }
    scan += BSP_FLASH_ENTRY_SIZE;
  }

  uint32_t next_meta_offset = scan;
  uint32_t gen              = has_valid_gen ? last_gen : 0u;

  /* Decide whether the active sector has room for this write */
  bool reason_meta_full = ((next_meta_offset + BSP_FLASH_ENTRY_SIZE) > BSP_FLASH_METADATA_SIZE);
  bool reason_data_full = ((log_pos + size) > sub_length);
  bool need_swap        = reason_meta_full || reason_data_full;

  if (need_swap)
  {
    const char *reason = "unknown";
    if (reason_meta_full && reason_data_full)
    {
      reason = "metadata_full+data_full";
    }
    else if (reason_meta_full)
    {
      reason = "metadata_full";
    }
    else if (reason_data_full)
    {
      reason = "data_full";
    }

    RLOG_W(LOG_OBJECT_CODE_SYS_CFG,
           "[FLASH][SWAP] partition=log reason=%s records=%lu bytes_used=%lu active=%u->%u", reason,
           (unsigned long) log_record_count, (unsigned long) log_pos, (unsigned) dr->active,
           (unsigned) (1u - dr->active));

    /* ── Sector wrap ──────────────────────────────────────────────────────
     * Erase the INACTIVE sector only. The ACTIVE (old) sector is left
     * intact — its data remains readable until the caller (sys_logger)
     * resets g_flash_log_read_pos after confirming the host received it.
     * ──────────────────────────────────────────────────────────────────── */
    uint8_t                   new_idx = 1u - dr->active;
    const bsp_flash_region_t *old     = &dr->sectors[dr->active];
    bsp_flash_region_t       *nr      = &dr->sectors[new_idx];

    if (flash_erase_sector(nr->base) != BSP_FLASH_OK)
      return BSP_FLASH_ERR_ERASE;

    /* Preserve the latest config snapshot before writing the new log entry.
     * Without this, repeated log swaps can erase the only valid config copy.
     */
    uint32_t next_meta_offset = 0u;
    if (copy_latest_cfg_snapshot(old, nr, dr->crc32_cb, &next_meta_offset) != BSP_FLASH_OK)
      return BSP_FLASH_ERR_PROGRAM;

    /* Write data at the start of the log sub-partition in the new sector */
    uint32_t write_addr = nr->base + BSP_FLASH_METADATA_SIZE + sub_offset;
    HAL_FLASH_Unlock();
    if (flash_write_block(write_addr, data, size) != BSP_FLASH_OK)
    {
      HAL_FLASH_Lock();
      return BSP_FLASH_ERR_PROGRAM;
    }
    HAL_FLASH_Lock();

    uint32_t data_crc  = dr->crc32_cb ? dr->crc32_cb(data, size) : 0u;
    uint32_t timestamp = dr->timestamp_cb ? dr->timestamp_cb() : 0u;

    if (append_metadata_entry(nr->base, next_meta_offset, gen + 1u, sub_offset, size, timestamp, data_crc, log_read_pos,
                              dr->crc32_cb)
        != BSP_FLASH_OK)
      return BSP_FLASH_ERR_PROGRAM;

    /* Switch active — old sector deliberately NOT erased */
    dr->active = new_idx;

    if (out_actual_pos)
      *out_actual_pos = 0u;
    return BSP_FLASH_OK;
  }

  /* ── Normal append at log_pos ─────────────────────────────────────────── */
  uint32_t write_addr = active->base + BSP_FLASH_METADATA_SIZE + sub_offset + log_pos;
  HAL_FLASH_Unlock();
  if (flash_write_block(write_addr, data, size) != BSP_FLASH_OK)
  {
    HAL_FLASH_Lock();
    return BSP_FLASH_ERR_PROGRAM;
  }
  HAL_FLASH_Lock();

  uint32_t data_crc  = dr->crc32_cb ? dr->crc32_cb(data, size) : 0u;
  uint32_t timestamp = dr->timestamp_cb ? dr->timestamp_cb() : 0u;

  if (append_metadata_entry(active->base, next_meta_offset, gen, sub_offset + log_pos, size, timestamp,
                            data_crc, log_read_pos, dr->crc32_cb)
      != BSP_FLASH_OK)
    return BSP_FLASH_ERR_PROGRAM;

  if (out_actual_pos)
    *out_actual_pos = log_pos;
  return BSP_FLASH_OK;
}

/* End of file -------------------------------------------------------- */
