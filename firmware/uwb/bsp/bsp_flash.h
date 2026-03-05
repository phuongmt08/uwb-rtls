/**
 * @file       bsp_flash.h
 * @copyright  Copyright (C) 2019 ITRVN.
 * @license    This project is released under the Fiot License.
 * @version    1.3.0
 * @date       2026-03-05
 * @author     Phuong Mai
 * @brief      Generic metadata-region dual-sector flash driver
 * @note       Architecture per sector: [Metadata 16KB][Data (rest)]
 */

#ifndef __BSP_FLASH_H
#define __BSP_FLASH_H
/* Includes ----------------------------------------------------------- */
#include <stdint.h>
#include <stdbool.h>

/* Public defines ----------------------------------------------------- */
#define BSP_FLASH_ENTRY_MARKER  0xC0DEC0DEu   /* Valid metadata entry marker */
#define BSP_FLASH_METADATA_SIZE (32u * 1024u) /* 32 KB metadata region */
#define BSP_FLASH_ENTRY_SIZE    32u           /* Size of one metadata entry (bytes) */

/* ── Data-region sub-partitions (offset relative to DATA region start) ───────
 *
 *  Each sector = 32 KB metadata + 96 KB data region.
 *  [0x00000 – 0x03FFF]  16 KB  config   (latest record only)
 *  [0x04000 – 0x17FFF]  80 KB  log      (append-only)
 *  Total = 96 KB, no waste.
 * ─────────────────────────────────────────────────────────────────────────── */
#define BSP_FLASH_CFG_DATA_OFFSET  (0u)                           /**< Config start in data region */
#define BSP_FLASH_CFG_DATA_LENGTH  (16u * 1024u)                  /**< Config size = 16 KB         */
#define BSP_FLASH_LOG_DATA_OFFSET  (BSP_FLASH_CFG_DATA_LENGTH)    /**< Log start  = 0x04000        */
#define BSP_FLASH_LOG_DATA_LENGTH  (80u * 1024u)                  /**< Log size   = 80 KB          */

/* Public typedefs ---------------------------------------------------- */
/**
 * @brief CRC32 calculation callback
 * @param data Pointer to data buffer
 * @param len  Length of data in bytes
 * @return CRC32 checksum value
 */
typedef uint32_t (*bsp_flash_crc32_fn)(const void *data, uint32_t len);

/**
 * @brief Timestamp getter callback
 * @return Current timestamp value (e.g., HAL_GetTick())
 */
typedef uint32_t (*bsp_flash_timestamp_fn)(void);

/* Public enumerate/structure ----------------------------------------- */
/**
 * @brief Flash operation status codes
 */
typedef enum
{
  BSP_FLASH_OK = 0,
  BSP_FLASH_ERR_NULL_PTR,
  BSP_FLASH_ERR_ERASE,
  BSP_FLASH_ERR_PROGRAM,
  BSP_FLASH_ERR_NO_SPACE,
  BSP_FLASH_ERR_INVALID_ARG,
  BSP_FLASH_ERR_CORRUPTED
} bsp_flash_status_t;

/**
 * @brief Single metadata entry (append-only)
 * @note Size: 32 bytes (8 words)
 *       Entries appended sequentially in metadata region
 *       Latest valid entry = last entry with valid marker + CRC
 *
 *       Word 8 (log_read_pos) is NOT covered by entry_crc — it is a
 *       best-effort persisted cursor.  0xFFFFFFFF means "not applicable"
 *       (config entries) or "read_pos = 0" (first log entry).
 */
typedef struct
{
  uint32_t marker;       /**< Entry valid marker (0xC0DEC0DE) */
  uint32_t gen;          /**< Generation counter (for wear leveling) */
  uint32_t data_offset;  /**< Absolute byte offset in data region */
  uint32_t data_length;  /**< Data length in bytes (0 = read-cursor-update only) */
  uint32_t timestamp;    /**< Write timestamp (0 if unused) */
  uint32_t crc32;        /**< Data CRC32 (0 if unused) */
  uint32_t entry_crc;    /**< CRC32 of this entry (first 6 words = 24 bytes) */
  uint32_t log_read_pos; /**< Log read cursor at time of write; 0xFFFFFFFF = N/A */
} bsp_flash_metadata_entry_t;

/**
 * @brief Single flash sector descriptor
 */
typedef struct
{
  uint32_t base;   /**< Flash sector base address */
  uint32_t size;   /**< Flash sector size in bytes */
  uint8_t  inited; /**< Initialization flag */
} bsp_flash_region_t;

/**
 * @brief Dual-sector flash with metadata-region architecture
 * @note Layout per sector: [Metadata 16KB][Data (rest)]
 *       Active sector determined by metadata generation counter
 */
typedef struct
{
  bsp_flash_region_t     sectors[2];   /**< Two sectors for wear leveling */
  uint8_t                active;       /**< Active sector index (0 or 1) */
  bsp_flash_crc32_fn     crc32_cb;     /**< CRC32 callback (optional, NULL if unused) */
  bsp_flash_timestamp_fn timestamp_cb; /**< Timestamp callback (optional, NULL if unused) */
} bsp_flash_dual_t;

/* Public macros ------------------------------------------------------ */
/* Public variables --------------------------------------------------- */
/* Public function prototypes ----------------------------------------- */

/**
 * @brief Initialize dual-sector flash system
 * @param[out] dr Dual-sector descriptor
 * @param[in] base0 Base address of sector 0
 * @param[in] size0 Size of sector 0 (must be > 16KB)
 * @param[in] base1 Base address of sector 1
 * @param[in] size1 Size of sector 1 (must be > 16KB)
 * @param[in] crc32_fn CRC32 calculation callback (NULL if not used)
 * @param[in] timestamp_fn Timestamp callback (NULL if not used)
 * @return BSP_FLASH_OK on success
 * @note Determines active sector by checking metadata generation counters
 *       If no valid sectors, initializes sector 0 with empty metadata
 */
bsp_flash_status_t bsp_flash_dual_init(bsp_flash_dual_t      *dr,
                                       uint32_t               base0,
                                       uint32_t               size0,
                                       uint32_t               base1,
                                       uint32_t               size1,
                                       bsp_flash_crc32_fn     crc32_fn,
                                       bsp_flash_timestamp_fn timestamp_fn);

/**
 * @brief Write config blob to the config sub-partition (BSP_FLASH_CFG_*).
 *
 *        The write position is derived internally from metadata — caller
 *        does not need to track any cursor (same as bsp_flash_log_append).
 *        Only the most recent write is accessible via bsp_flash_cfg_read.
 *        Triggers a sector swap when metadata or data region is full.
 *
 * @param[in,out] dr    Dual-sector descriptor
 * @param[in]     data  Data buffer to write (must be 4-byte aligned)
 * @param[in]     size  Number of bytes (must be 4-byte aligned, <= BSP_FLASH_CFG_DATA_LENGTH)
 * @return BSP_FLASH_OK on success
 */
bsp_flash_status_t bsp_flash_cfg_write(bsp_flash_dual_t *dr,
                                       const void       *data,
                                       uint32_t          size);

/**
 * @brief Read the most recent config blob from the config sub-partition.
 *
 * @param[in]  dr        Dual-sector descriptor
 * @param[out] out       Destination buffer
 * @param[in]  max_size  Maximum bytes to read
 * @return Number of bytes read, 0 if no valid record found or CRC mismatch
 */
uint32_t bsp_flash_cfg_read(const bsp_flash_dual_t *dr,
                            void                   *out,
                            uint32_t                max_size);

/**
 * @brief Application image header — plain POD, no proto dependency.
 * @note  This struct is placed in flash at APP_HEADER_ADDR by the linker;
 *        the bootloader also reads it from the same address.
 */
typedef struct {
  uint32_t magic;
  uint32_t header_version;
  uint32_t header_size;
  uint32_t fw_major;
  uint32_t fw_minor;
  uint32_t fw_patch;
  uint32_t fw_build;
  uint64_t fw_gitsha;
  uint32_t image_timestamp;
  uint32_t image_length;
  uint32_t image_crc;
  uint32_t reserved[5];
} bsp_app_image_header_t;

/**
 * @brief Check whether application image header in flash is valid.
 */
bool bsp_flash_app_header_valid(void);

/**
 * @brief Read raw app image header into caller-supplied buffer.
 * @param[out] out   Destination buffer
 * @param[in]  size  Size of destination buffer in bytes (must be >= sizeof(bsp_app_image_header_t))
 * @return true if header is valid and data copied
 */
bool bsp_flash_read_app_header(void *out, uint32_t size);

/**
 * @brief Recover write_pos and read_pos for the log sub-partition from metadata.
 *
 *        Scans all metadata entries whose data_offset falls inside the log
 *        sub-partition and computes:
 *          write_pos = highest (data_offset - sub_offset + data_length) seen
 *          read_pos  = log_read_pos field of the most recent log entry that
 *                      has log_read_pos != 0xFFFFFFFF (0 if none found).
 *
 *        O(N) where N = number of metadata entries — runs once at boot.
 *
 * @param[in]  dr             Dual-sector descriptor
 * @param[in]  sub_offset     Log partition start offset in data region
 * @param[in]  sub_length     Log partition length in bytes
 * @param[out] out_write_pos  Recovered write cursor (next byte to write)
 * @param[out] out_read_pos   Recovered read cursor (next byte host has not confirmed)
 * @return BSP_FLASH_OK on success
 */
bsp_flash_status_t bsp_flash_log_get_positions(const bsp_flash_dual_t *dr,
                                                uint32_t               *out_write_pos,
                                                uint32_t               *out_read_pos);

/**
 * @brief Persist a new log read cursor without writing any log data.
 *
 *        Appends a zero-data metadata entry whose log_read_pos field holds
 *        the new confirmed read position.  Should be called when the host
 *        confirms receipt of a log chunk (log_clear command).
 *
 * @param[in] dr          Dual-sector descriptor
 * @param[in] sub_offset  Log partition start offset in data region
 * @param[in] sub_length  Log partition length in bytes
 * @param[in] read_pos    New confirmed read cursor to persist
 * @return BSP_FLASH_OK on success, BSP_FLASH_ERR_NO_SPACE if metadata full
 * @note The current write position is derived internally from metadata (same
 *       as bsp_flash_log_append) — the caller does not need to track it.
 */
bsp_flash_status_t bsp_flash_log_update_read_pos(bsp_flash_dual_t *dr,
                                                  uint32_t          read_pos);

/**
 * @brief Append one log record to the log sub-partition via the metadata layer.
 *
 * Unlike bsp_flash_write_data (which keeps only the one latest record per
 * partition), every call here creates a new metadata entry so every log
 * record is individually tracked and preserved.
 *
 * Overflow behavior — when the active sector can no longer accept the write
 * (metadata region full, or log data partition full):
 *   - The INACTIVE sector is erased.
 *   - This write is placed at the beginning of the inactive sector's log
 *     sub-partition (pos = 0).
 *   - The previously active sector is intentionally NOT erased — its data
 *     remains readable until the caller confirms it is no longer needed.
 *   - dr->active is switched to the new sector.
 *   - *out_actual_pos is set to 0 to signal the caller that a wrap occurred.
 *
 * @param[in]  dr              Dual-sector descriptor
 * @param[in]  log_read_pos    Current confirmed read cursor to embed in metadata
 * @param[in]  data            Data to write (size must be 4-byte aligned)
 * @param[in]  size            Number of bytes to write
 * @param[in]  sub_offset      Log partition start offset inside the data region
 * @param[in]  sub_length      Log partition length in bytes
 * @param[out] out_actual_pos  Offset where data was actually written (0 = sector
 *                             wrap occurred).  May be NULL.
 * @return BSP_FLASH_OK on success
 * @note The write position is derived internally from metadata — symmetric
 *       with bsp_flash_write_data (config).  Callers do not need to track it.
 */
bsp_flash_status_t bsp_flash_log_append(bsp_flash_dual_t *dr,
                                         uint32_t          log_read_pos,
                                         const void       *data,
                                         uint32_t          size,
                                         uint32_t         *out_actual_pos);

#endif /* __BSP_FLASH_H */