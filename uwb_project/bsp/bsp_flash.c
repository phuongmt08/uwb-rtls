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

/** @brief Get pointer to metadata region */
static bsp_flash_metadata_t *get_metadata(const bsp_flash_region_t *r);

/** @brief Validate metadata structure integrity */
static bool is_metadata_valid(const bsp_flash_metadata_t *meta);

/** @brief Write metadata atomically (SOF last) */
static bsp_flash_status_t write_metadata(uint32_t base, const bsp_flash_metadata_t *meta);

/** @brief Swap active sector when current is full */
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

  /* Check metadata validity and determine active sector */
  bsp_flash_metadata_t *m0 = get_metadata(&dr->sectors[0]);
  bsp_flash_metadata_t *m1 = get_metadata(&dr->sectors[1]);

  bool m0_valid = is_metadata_valid(m0);
  bool m1_valid = is_metadata_valid(m1);

  if (m0_valid && m1_valid)
  {
    /* Both valid, choose newer generation with proper wrap-around handling */
    int32_t gen_diff = (int32_t) (m0->gen - m1->gen);
    dr->active       = (gen_diff > 0) ? 0u : 1u;
  }
  else if (m0_valid)
  {
    dr->active = 0u;
  }
  else if (m1_valid)
  {
    dr->active = 1u;
  }
  else
  {
    /* No valid sectors: initialize sector 0 */
    if (flash_erase_sector(base0) != BSP_FLASH_OK)
      return BSP_FLASH_ERR_ERASE;

    bsp_flash_metadata_t init_meta;
    memset(&init_meta, 0xFF, sizeof(init_meta)); /* Erase state */
    init_meta.sof          = BSP_FLASH_SOF_MARKER;
    init_meta.gen          = 0u;
    init_meta.write_ptr    = 0u; /* Data region starts empty */
    init_meta.read_ptr     = 0u;
    init_meta.record_count = 0u;

    if (write_metadata(base0, &init_meta) != BSP_FLASH_OK)
      return BSP_FLASH_ERR_PROGRAM;

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

  /* Safety: limit payload to half of sector size */
  bsp_flash_region_t *active_region    = &dr->sectors[dr->active];
  uint32_t            data_region_size = active_region->size - BSP_FLASH_METADATA_SIZE;
  if (size > (data_region_size / 2u))
    return BSP_FLASH_ERR_INVALID_ARG;

  /* Check if metadata table is full */
  bsp_flash_metadata_t *meta = get_metadata(active_region);
  if (meta->record_count >= BSP_FLASH_MAX_RECORDS)
  {
    /* Sector swap required */
    return swap_sector(dr, cfg, size);
  }

  uint32_t data_base    = active_region->base + BSP_FLASH_METADATA_SIZE;
  uint32_t write_offset = meta->write_ptr;
  uint32_t write_addr   = data_base + write_offset;

  if ((write_offset + size) > data_region_size)
  {
    return swap_sector(dr, cfg, size);
  }

  HAL_FLASH_Unlock();
  if (flash_write_block(write_addr, cfg, size) != BSP_FLASH_OK)
  {
    HAL_FLASH_Lock();
    return BSP_FLASH_ERR_PROGRAM;
  }
  HAL_FLASH_Lock();

  /* Prepare updated metadata */
  bsp_flash_metadata_t new_meta;
  memcpy(&new_meta, meta, sizeof(new_meta)); /* Copy existing */

  uint32_t idx                    = new_meta.record_count;
  new_meta.records[idx].offset    = write_offset;
  new_meta.records[idx].length    = size;
  new_meta.records[idx].timestamp = dr->timestamp_cb ? dr->timestamp_cb() : 0u;
  new_meta.records[idx].crc32     = dr->crc32_cb ? dr->crc32_cb(cfg, size) : 0u;
  new_meta.record_count           = idx + 1u;
  new_meta.write_ptr              = write_offset + size;
  new_meta.read_ptr               = write_offset; /* Latest record */

  if (write_metadata(active_region->base, &new_meta) != BSP_FLASH_OK)
    return BSP_FLASH_ERR_PROGRAM;

  return BSP_FLASH_OK;
}

uint32_t bsp_flash_read_config(const bsp_flash_dual_t *dr, void *out, uint32_t max_size)
{
  if (!dr || !out || max_size == 0u)
    return 0u;

  const bsp_flash_region_t *active_region = &dr->sectors[dr->active];
  bsp_flash_metadata_t     *meta          = get_metadata(active_region);

  /* Validate metadata first */
  if (!is_metadata_valid(meta))
    return 0u;

  /* Check if there are any records */
  if (meta->record_count == 0u)
    return 0u;

  /* Read latest record (pointed by read_ptr) */
  uint32_t latest_offset = meta->read_ptr;
  uint32_t latest_length = 0u;
  uint32_t latest_crc    = 0u;

  /* Find record in table matching read_ptr offset */
  for (uint32_t i = 0u; i < meta->record_count; i++)
  {
    if (meta->records[i].offset == latest_offset)
    {
      latest_length = meta->records[i].length;
      latest_crc    = meta->records[i].crc32;
      break;
    }
  }

  if (latest_length == 0u)
    return 0u;

  uint32_t data_base = active_region->base + BSP_FLASH_METADATA_SIZE;
  uint32_t read_addr = data_base + latest_offset;
  uint32_t copy_len  = (latest_length > max_size) ? max_size : latest_length;

  memcpy(out, (const void *) read_addr, copy_len);

  if (dr->crc32_cb && latest_crc != 0u)
  {
    uint32_t computed_crc = dr->crc32_cb(out, copy_len);
    if (computed_crc != latest_crc)
    {
      return 0u;
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

/* Get metadata region pointer */
static bsp_flash_metadata_t *get_metadata(const bsp_flash_region_t *r)
{
  return (bsp_flash_metadata_t *) (r->base + METADATA_START);
}

/* Validate metadata structure integrity */
static bool is_metadata_valid(const bsp_flash_metadata_t *meta)
{
  /* Check SOF marker */
  if (meta->sof != BSP_FLASH_SOF_MARKER)
    return false;

  /* Validate generation counter (not all 0xFF = erased state) */
  if (meta->gen == FLASH_ERASED_VALUE)
    return false;

  if (meta->record_count > BSP_FLASH_MAX_RECORDS)
    return false;

  if (meta->write_ptr == FLASH_ERASED_VALUE || meta->read_ptr == FLASH_ERASED_VALUE)
    return false;

  return true;
}

static bsp_flash_status_t write_metadata(uint32_t base, const bsp_flash_metadata_t *meta)
{
  HAL_FLASH_Unlock();

  /* Write everything except SOF first */
  uint32_t addr = base + METADATA_START;

  if (HAL_FLASH_Program(FLASH_TYPEPROGRAM_WORD, addr + 4u, meta->gen) != HAL_OK
      || HAL_FLASH_Program(FLASH_TYPEPROGRAM_WORD, addr + 8u, meta->write_ptr) != HAL_OK
      || HAL_FLASH_Program(FLASH_TYPEPROGRAM_WORD, addr + 12u, meta->read_ptr) != HAL_OK
      || HAL_FLASH_Program(FLASH_TYPEPROGRAM_WORD, addr + 16u, meta->record_count) != HAL_OK)
  {
    HAL_FLASH_Lock();
    return BSP_FLASH_ERR_PROGRAM;
  }

  /* Write reserved fields */
  for (uint32_t i = 0u; i < 3u; i++)
  {
    if (HAL_FLASH_Program(FLASH_TYPEPROGRAM_WORD, addr + 20u + i * 4u, meta->reserved[i]) != HAL_OK)
    {
      HAL_FLASH_Lock();
      return BSP_FLASH_ERR_PROGRAM;
    }
  }

  /* Write record table */
  const uint32_t *rec_data  = (const uint32_t *) meta->records;
  uint32_t        rec_words = (sizeof(meta->records) / 4u);
  for (uint32_t i = 0u; i < rec_words; i++)
  {
    if (HAL_FLASH_Program(FLASH_TYPEPROGRAM_WORD, addr + 32u + i * 4u, rec_data[i]) != HAL_OK)
    {
      HAL_FLASH_Lock();
      return BSP_FLASH_ERR_PROGRAM;
    }
  }

  /* LAST: Write SOF marker */
  if (HAL_FLASH_Program(FLASH_TYPEPROGRAM_WORD, addr + 0u, meta->sof) != HAL_OK)
  {
    HAL_FLASH_Lock();
    return BSP_FLASH_ERR_PROGRAM;
  }

  HAL_FLASH_Lock();
  return BSP_FLASH_OK;
}

/* Sector swap: copy latest config to new sector with fresh metadata */
static bsp_flash_status_t swap_sector(bsp_flash_dual_t *dr, const void *cfg, uint32_t size)
{
  uint8_t                   new_idx = 1u - dr->active;
  const bsp_flash_region_t *old     = &dr->sectors[dr->active];
  bsp_flash_region_t       *nr      = &dr->sectors[new_idx];

  if (flash_erase_sector(nr->base) != BSP_FLASH_OK)
    return BSP_FLASH_ERR_ERASE;

  /* Read current metadata from old sector */
  bsp_flash_metadata_t *old_meta = get_metadata(old);

  /* Prepare new metadata */
  bsp_flash_metadata_t new_meta;
  memset(&new_meta, 0xFF, sizeof(new_meta)); /* Erase state */
  new_meta.sof          = BSP_FLASH_SOF_MARKER;
  new_meta.gen          = old_meta->gen + 1u;
  new_meta.write_ptr    = size; /* Only new config written */
  new_meta.read_ptr     = 0u;   /* Offset 0 in data region */
  new_meta.record_count = 1u;

  new_meta.records[0].offset    = 0u;
  new_meta.records[0].length    = size;
  new_meta.records[0].timestamp = dr->timestamp_cb ? dr->timestamp_cb() : 0u;
  new_meta.records[0].crc32     = dr->crc32_cb ? dr->crc32_cb(cfg, size) : 0u;

  /* Write config data to new sector */
  uint32_t data_base = nr->base + BSP_FLASH_METADATA_SIZE;
  HAL_FLASH_Unlock();
  if (flash_write_block(data_base, cfg, size) != BSP_FLASH_OK)
  {
    HAL_FLASH_Lock();
    return BSP_FLASH_ERR_PROGRAM;
  }
  HAL_FLASH_Lock();

  /* Write new metadata */
  if (write_metadata(nr->base, &new_meta) != BSP_FLASH_OK)
    return BSP_FLASH_ERR_PROGRAM;

  /* Switch active sector - new sector is now fully valid */
  dr->active = new_idx;

  /* Optional: Erase old sector for cleanup (non-fatal if fails) */
  /* We already switched to new sector, so old sector erase failure is acceptable */
  (void) flash_erase_sector(old->base); /* Ignore error */

  return BSP_FLASH_OK;
}

/* End of file -------------------------------------------------------- */