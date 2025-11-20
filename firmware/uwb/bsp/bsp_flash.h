/**
 * @file       bsp_flash.h
 * @version    1.2.1
 * @date       2025-6-12
 * @author     Phuong Mai
 * @brief      Metadata-region Flash for sys_config
 * @note       Architecture: [Metadata 16KB][Data (rest)]
 *             Metadata updated atomically on each write
 */

#ifndef __BSP_FLASH_H
#define __BSP_FLASH_H
/* Includes ----------------------------------------------------------- */
#include <stdint.h>

/* Public defines ----------------------------------------------------- */
#define BSP_FLASH_SOF_MARKER    0xDEADBEEFu   /* Metadata valid marker */
#define BSP_FLASH_METADATA_SIZE (16u * 1024u) /* 16 KB metadata region */
#define BSP_FLASH_MAX_RECORDS   64u           /* Max config records tracked */

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
 * @brief Record descriptor in metadata table
 * @note Each write creates new entry, read_ptr points to latest valid
 */
typedef struct
{
  uint32_t offset;    /**< Offset in data region */
  uint32_t length;    /**< Record length in bytes */
  uint32_t timestamp; /**< Write timestamp (optional) */
  uint32_t crc32;     /**< Data CRC32 (optional, 0 = unused) */
} bsp_flash_record_entry_t;

/**
 * @brief Metadata region structure (16 KB)
 * @note Layout: [header][record_table][padding]
 *       SOF written LAST for atomic commit
 */
typedef struct
{
  uint32_t                 sof;                            /**< Start-of-Flash marker (written LAST) */
  uint32_t                 gen;                            /**< Generation counter */
  uint32_t                 write_ptr;                      /**< Next write offset in data region */
  uint32_t                 read_ptr;                       /**< Current valid config record offset */
  uint32_t                 record_count;                   /**< Number of records in table */
  uint32_t                 reserved[3];                    /**< Reserved for future use */
  bsp_flash_record_entry_t records[BSP_FLASH_MAX_RECORDS]; /**< Record table */
} bsp_flash_metadata_t;

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
 * @brief Write configuration data to active sector
 * @param[in,out] dr Dual-sector descriptor
 * @param[in] cfg Configuration data buffer
 * @param[in] size Size of config data (must be 4-byte aligned)
 * @return BSP_FLASH_OK on success
 * @note Process:
 *       1. Write data to data region at write_ptr
 *       2. Update metadata (add record entry, increment write_ptr)
 *       3. Write SOF marker LAST (atomic commit)
 *       Triggers sector swap if insufficient space
 */
bsp_flash_status_t bsp_flash_write_config(bsp_flash_dual_t *dr, const void *cfg, uint32_t size);

/**
 * @brief Read most recent valid configuration
 * @param[in] dr Dual-sector descriptor
 * @param[out] out Buffer to store config data
 * @param[in] max_size Maximum bytes to read
 * @return Number of bytes read, 0 if no config found or CRC mismatch
 * @note Reads record pointed by metadata read_ptr
 *       Validates CRC32 if callback was provided during init
 */
uint32_t bsp_flash_read_config(const bsp_flash_dual_t *dr, void *out, uint32_t max_size);

#endif /* __BSP_FLASH_H */