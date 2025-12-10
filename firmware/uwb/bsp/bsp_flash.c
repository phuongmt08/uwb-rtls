/**
 * @file       bsp_flash.h
 * @version    1.2.1
 * @date       2025-6-12
 * @author     Phuong Mai
 * @brief      Metadata-region Flash for sys_config
 * @note       Architecture: [Metadata 16KB][Data (rest)]
 *             Metadata updated atomically on each write
 */

/* Includes ----------------------------------------------------------- */
#include "bsp_flash.h"

#include "stm32f4xx_hal.h"

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

/* Private defines ---------------------------------------------------- */
#define FLASH_ERASED_VALUE 0xFFFFFFFFu

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
                                                 bsp_flash_crc32_fn crc_cb);

/** @brief Swap active sector when metadata or data region full */
static bsp_flash_status_t swap_sector(bsp_flash_dual_t *dr, const void *cfg, uint32_t size);

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
    return BSP_FLASH_ERR_INVALID_ARG;

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
  uint32_t                   offset0 = 0u;
  uint32_t                   offset1 = 0u;
  bsp_flash_metadata_entry_t *e0     = find_latest_metadata(&dr->sectors[0], &offset0);
  bsp_flash_metadata_entry_t *e1     = find_latest_metadata(&dr->sectors[1], &offset1);

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
    /* No valid metadata: erase sector 0 and write first entry */
    if (flash_erase_sector(base0) != BSP_FLASH_OK)
      return BSP_FLASH_ERR_ERASE;

    /* Write empty first entry (no data config yet) */
    bsp_flash_metadata_entry_t init_entry;
    memset(&init_entry, 0xFF, sizeof(init_entry));
    init_entry.marker      = BSP_FLASH_ENTRY_MARKER;
    init_entry.gen         = 0u;
    init_entry.data_offset = 0u;
    init_entry.data_length = 0u; /* No config data yet */
    init_entry.timestamp   = 0u;
    init_entry.crc32       = 0u;

    /* Calculate entry CRC (first 6 words) */
    uint32_t entry_crc = crc32_fn ? crc32_fn(&init_entry, 24u) : 0u;
    init_entry.entry_crc = entry_crc;
    init_entry.reserved  = 0xFFFFFFFFu;

    HAL_FLASH_Unlock();
    if (flash_write_block(base0, &init_entry, sizeof(init_entry)) != BSP_FLASH_OK)
    {
      HAL_FLASH_Lock();
      return BSP_FLASH_ERR_PROGRAM;
    }
    HAL_FLASH_Lock();

    dr->active = 0u;
  }

  return BSP_FLASH_OK;
}

bsp_flash_status_t bsp_flash_write_config(bsp_flash_dual_t *dr, const void *cfg, uint32_t size)
{
  if (!dr || !cfg || size == 0u)
    return BSP_FLASH_ERR_NULL_PTR;
  if ((size & 3u) != 0u)
    return BSP_FLASH_ERR_INVALID_ARG;

  bsp_flash_region_t *active_region = &dr->sectors[dr->active];
  uint32_t            data_region_size = active_region->size - BSP_FLASH_METADATA_SIZE;

  /* Safety: limit payload to half of data region size */
  if (size > (data_region_size / 2u))
    return BSP_FLASH_ERR_INVALID_ARG;

  /* Find current metadata entry and determine next write positions */
  uint32_t                   current_meta_offset = 0u;
  bsp_flash_metadata_entry_t *current_entry      = find_latest_metadata(active_region, &current_meta_offset);

  uint32_t next_meta_offset = 0u;
  uint32_t next_data_offset = 0u;
  uint32_t gen              = 0u;

  if (current_entry && is_entry_valid(current_entry, dr->crc32_cb))
  {
    gen              = current_entry->gen + 1u;  // Increment generation for new entry
    next_meta_offset = current_meta_offset + BSP_FLASH_ENTRY_SIZE;
    next_data_offset = current_entry->data_offset + current_entry->data_length;
  }
  else
  {
    /* No valid entry found, start from beginning */
    next_meta_offset = 0u;
    next_data_offset = 0u;
    gen              = 0u;
  }

  /* Check if metadata region full */
  if ((next_meta_offset + BSP_FLASH_ENTRY_SIZE) > BSP_FLASH_METADATA_SIZE)
  {
    return swap_sector(dr, cfg, size);
  }

  /* Check if data region full */
  if ((next_data_offset + size) > data_region_size)
  {
    return swap_sector(dr, cfg, size);
  }

  /* Write data to data region */
  uint32_t data_base = active_region->base + BSP_FLASH_METADATA_SIZE;
  uint32_t write_addr = data_base + next_data_offset;

  HAL_FLASH_Unlock();
  if (flash_write_block(write_addr, cfg, size) != BSP_FLASH_OK)
  {
    HAL_FLASH_Lock();
    return BSP_FLASH_ERR_PROGRAM;
  }
  HAL_FLASH_Lock();

  /* Calculate data CRC and timestamp */
  uint32_t data_crc  = dr->crc32_cb ? dr->crc32_cb(cfg, size) : 0u;
  uint32_t timestamp = dr->timestamp_cb ? dr->timestamp_cb() : 0u;

  /* Append new metadata entry */
  uint32_t meta_base = active_region->base;
  return append_metadata_entry(meta_base,
                                next_meta_offset,
                                gen,
                                next_data_offset,
                                size,
                                timestamp,
                                data_crc,
                                dr->crc32_cb);
}

uint32_t bsp_flash_read_config(const bsp_flash_dual_t *dr, void *out, uint32_t max_size)
{
  if (!dr || !out || max_size == 0u)
    return 0u;

  const bsp_flash_region_t *active_region = &dr->sectors[dr->active];

  /* Find latest valid metadata entry */
  uint32_t                   entry_offset = 0u;
  bsp_flash_metadata_entry_t *entry       = find_latest_metadata(active_region, &entry_offset);

  if (!entry || !is_entry_valid(entry, dr->crc32_cb))
    return 0u;

  /* Check if there's actual data (length > 0) */
  if (entry->data_length == 0u)
    return 0u;

  /* Read config data from data region */
  uint32_t data_base = active_region->base + BSP_FLASH_METADATA_SIZE;
  uint32_t read_addr = data_base + entry->data_offset;
  uint32_t copy_len  = (entry->data_length > max_size) ? max_size : entry->data_length;

  memcpy(out, (const void *) read_addr, copy_len);

  /* Verify data CRC if available */
  if (dr->crc32_cb && entry->crc32 != 0u)
  {
    uint32_t computed_crc = dr->crc32_cb(out, copy_len);
    if (computed_crc != entry->crc32)
    {
      return 0u; /* CRC mismatch */
    }
  }

  return copy_len;
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

static bsp_flash_status_t flash_erase_sector(uint32_t base)
{
  int sector = addr_to_sector(base);
  if (sector < 0)
    return BSP_FLASH_ERR_INVALID_ARG;

  FLASH_EraseInitTypeDef erase        = { 0 };
  uint32_t               sector_error = 0;

  HAL_FLASH_Unlock();
  erase.TypeErase    = FLASH_TYPEERASE_SECTORS;
  erase.VoltageRange = FLASH_VOLTAGE_RANGE_3;
  erase.Sector       = (uint32_t) sector;
  erase.NbSectors    = 1;
  if (HAL_FLASHEx_Erase(&erase, &sector_error) != HAL_OK)
  {
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
  return (HAL_FLASH_Program(FLASH_TYPEPROGRAM_WORD, addr, w) == HAL_OK) ? 0 : -1;
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

  bsp_flash_metadata_entry_t *last_valid = NULL;
  uint32_t                   last_offset = 0u;

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
      last_valid = entry;
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
                                                 bsp_flash_crc32_fn crc_cb)
{
  /* Prepare entry */
  bsp_flash_metadata_entry_t entry;
  memset(&entry, 0xFF, sizeof(entry));

  entry.marker      = BSP_FLASH_ENTRY_MARKER;
  entry.gen         = gen;
  entry.data_offset = data_offset;
  entry.data_length = data_length;
  entry.timestamp   = timestamp;
  entry.crc32       = data_crc;
  entry.reserved    = 0xFFFFFFFFu;

  /* Calculate entry CRC (first 6 words: marker through crc32) */
  uint32_t entry_crc = crc_cb ? crc_cb(&entry, 24u) : 0u;
  entry.entry_crc    = entry_crc;

  /* Write entry to flash */
  uint32_t write_addr = meta_base + entry_offset;

  HAL_FLASH_Unlock();
  bsp_flash_status_t status = flash_write_block(write_addr, &entry, sizeof(entry));
  HAL_FLASH_Lock();

  return status;
}

/* Sector swap: copy latest config to new sector with fresh metadata */
static bsp_flash_status_t swap_sector(bsp_flash_dual_t *dr, const void *cfg, uint32_t size)
{
  uint8_t                   new_idx = 1u - dr->active;
  const bsp_flash_region_t *old     = &dr->sectors[dr->active];
  bsp_flash_region_t       *nr      = &dr->sectors[new_idx];

  /* Erase new sector */
  if (flash_erase_sector(nr->base) != BSP_FLASH_OK)
    return BSP_FLASH_ERR_ERASE;

  /* Read current metadata from old sector */
  uint32_t                   old_offset = 0u;
  bsp_flash_metadata_entry_t *old_entry = find_latest_metadata(old, &old_offset);

  /* Determine new generation */
  uint32_t new_gen = 0u;
  if (old_entry && is_entry_valid(old_entry, dr->crc32_cb))
  {
    new_gen = old_entry->gen + 1u;
  }

  /* Write config data to new sector data region */
  uint32_t data_base = nr->base + BSP_FLASH_METADATA_SIZE;

  HAL_FLASH_Unlock();
  if (flash_write_block(data_base, cfg, size) != BSP_FLASH_OK)
  {
    HAL_FLASH_Lock();
    return BSP_FLASH_ERR_PROGRAM;
  }
  HAL_FLASH_Lock();

  /* Calculate data CRC and timestamp */
  uint32_t data_crc  = dr->crc32_cb ? dr->crc32_cb(cfg, size) : 0u;
  uint32_t timestamp = dr->timestamp_cb ? dr->timestamp_cb() : 0u;

  /* Write first metadata entry in new sector */
  uint32_t meta_base = nr->base;
  if (append_metadata_entry(meta_base, 0u, new_gen, 0u, size, timestamp, data_crc, dr->crc32_cb) != BSP_FLASH_OK)
    return BSP_FLASH_ERR_PROGRAM;

  /* Switch active sector */
  dr->active = new_idx;

  /* Erase old sector (lazy erase, non-fatal if fails) */
  (void) flash_erase_sector(old->base);

  return BSP_FLASH_OK;
}

/* End of file -------------------------------------------------------- */