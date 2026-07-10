/**
 * @file       sys_flash_storage.h
 * @version    1.0.0
 * @date       2026-03-05
 * @author     Phuong Mai
 * @brief      Shared dual-sector flash storage singleton
 *
 */

/* Define to prevent recursive inclusion ------------------------------------ */
#ifndef __SYS_FLASH_STORAGE_H
#define __SYS_FLASH_STORAGE_H

/* Includes ----------------------------------------------------------------- */
#include "bsp_flash.h"
#include <stdint.h>
#include "config.h"

/* Public defines ----------------------------------------------------------- */

/* ── Physical sector layout ───────────────────────────────────────────────── */
#define SYS_FLASH_SECTOR0_BASE    (0x08040000UL)    /**< S6 base address      */
#define SYS_FLASH_SECTOR0_SIZE    (128u * 1024u)    /**< S6 size = 128 KB     */
#define SYS_FLASH_SECTOR1_BASE    (0x08060000UL)    /**< S7 base address      */
#define SYS_FLASH_SECTOR1_SIZE    (128u * 1024u)    /**< S7 size = 128 KB     */

/* Sub-partition sizes and offsets are defined in bsp_flash.h:
 *   BSP_FLASH_CFG_DATA_OFFSET / BSP_FLASH_CFG_DATA_LENGTH
 *   BSP_FLASH_LOG_DATA_OFFSET / BSP_FLASH_LOG_DATA_LENGTH */

/* Public function prototypes ----------------------------------------------- */

/**
 * @brief  Initialise the shared dual-sector flash handle.
 *
 *         Calls bsp_util_init() (utility delay/timing prep), then bsp_flash_dual_init().
 *         Safe to call multiple times — subsequent calls are no-ops.
 *
 * @return  0  on success
 * @return -1  on failure (bsp_util init or bsp_flash init failed)
 */
int sys_flash_storage_init(void);

/**
 * @brief  Return a pointer to the shared bsp_flash_dual_t handle.
 *
 * @return  Pointer to the dual-sector descriptor, or NULL if not yet
 *          initialised (sys_flash_storage_init() was not called).
 */
bsp_flash_dual_t *sys_flash_storage_get(void);

/* ── Config partition helpers ──────────────────────────────────────────────
 *  Thin wrappers around bsp_flash_cfg_write / bsp_flash_cfg_read that
 *  supply the shared bsp_flash_dual_t handle automatically.
 * ───────────────────────────────────────────────────────────────────────── */

/**
 * @brief  Write config blob to the config sub-partition.
 * @return BSP_FLASH_OK on success, error code otherwise.
 */
bsp_flash_status_t sys_flash_cfg_write(const void *data, uint32_t size);

/**
 * @brief  Read the most recent config blob from the config sub-partition.
 * @return Number of bytes read, 0 on error.
 */
uint32_t sys_flash_cfg_read(void *out, uint32_t max_size);

/* ── Log partition helpers ─────────────────────────────────────────────────────────────
 *  Stateless raw primitives — the write pointer is owned by sys_logger.
 *  Do NOT use write_at without checking partition bounds first.
 * ───────────────────────────────────────────────────────────────────────── */

/**
 * @brief  Write @p size bytes to the log sub-partition at byte position @p pos
 *         (0-based from the start of the log sub-partition).
 *
 * @param[in]  log_read_pos    Current confirmed read cursor to persist in metadata
 * @param[in]  data            Data buffer (4-byte aligned size)
 * @param[in]  size            Bytes to write
 * @param[out] out_actual_pos  Actual write offset; 0 signals sector wrap.
 * @note  Write position is derived internally from metadata (no external
 *        cursor needed — symmetric with bsp_flash_cfg_write).
 */
bsp_flash_status_t sys_flash_log_write_at(uint32_t    log_read_pos,
                                           const void *data,
                                           uint32_t    size,
                                           uint32_t   *out_actual_pos);

/**
 * @brief  Persist a new log read cursor to flash metadata (no data write).
 *         Call this when the host confirms receipt of a log chunk.
 * @param[in] read_pos   New confirmed read cursor
 * @note  Write position is derived internally from metadata.
 */
bsp_flash_status_t sys_flash_log_update_read_pos(uint32_t read_pos);

/**
 * @brief  Recover write_pos and read_pos from flash metadata on boot.
 *         Replaces the old raw-byte scan in sys_logger_init().
 * @param[out] out_write_pos  Recovered write cursor
 * @param[out] out_read_pos   Recovered read cursor
 */
bsp_flash_status_t sys_flash_log_get_positions(uint32_t *out_write_pos, uint32_t *out_read_pos);

/**
 * @brief  Read @p length bytes from the log sub-partition at byte offset @p offset.
 *
 * @param[out] out     Destination buffer
 * @param[in]  offset  Byte offset within the log sub-partition (0-based)
 * @param[in]  length  Number of bytes to read
 * @return Number of bytes actually read
 */
uint32_t sys_flash_log_read(void *out, uint32_t offset, uint32_t length);

#endif /* __SYS_FLASH_STORAGE_H */
