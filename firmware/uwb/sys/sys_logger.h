/**
 * @file       sys_logger.h
 * @copyright  Copyright (C) 2019 ITRVN.
 * @license    This project is released under the Fiot License.
 * @version    1.0.0
 * @date       2025
 * @author	   Phuong Mai
 * @brief      Simple RAM logger with USB CDC output
 * 
 * @details    This module implements a circular buffer logger that stores log records
 *             in RAM
 * 
 *             Log Record Format:
 *             +----------+----------+-------------+--------+-------------+
 *             | LOG_TYPE | OBJ_CODE | TIMESTAMP   | LEN    | MESSAGE     |
 *             | 1 byte   | 1 byte   | 6 bytes     | 1 byte | LEN bytes   |
 *             +----------+----------+-------------+--------+-------------+
 *             Total Header: 9 bytes (LOG_HEADER_LEN)

 */
/* Define to prevent recursive inclusion ------------------------------------ */
#ifndef __SYS_LOGGER_H
#define __SYS_LOGGER_H

/* Includes ----------------------------------------------------------------- */
#include <stdint.h>
#include <stdbool.h>
#include <stdarg.h>
#include "log_config.h"
#include "err.h"
/* Public defines ----------------------------------------------------------- */
/**
 * @brief Total RAM buffer size for logger (4KB)
 */
#define SYS_LOGGER_BUF_SIZE        (4096U)

/**
 * @brief Maximum message length per log record (180 bytes)
 */
#define SYS_LOGGER_MAX_MSG_LEN     (180U)

/**
 * @brief Maximum size of one complete log record
 */
#define RLOG_MAX_RECORD_SIZE       (LOG_HEADER_LEN + SYS_LOGGER_MAX_MSG_LEN)

/* Public enumerate/structure ----------------------------------------------- */


/* Public macros ------------------------------------------------------------ */
/**
 * @brief Log an informational message
 * @param _OBJ_CODE Object code identifying the component
 * @param _FORMAT printf-style format string
 * @param ... Variable arguments
 */
#define RLOG_I(_OBJ_CODE, _FORMAT, ...) \
	sys_logger_write_record(INFO_LOG, _OBJ_CODE, _FORMAT, ##__VA_ARGS__)

/**
 * @brief Log a debug message
 * @param _OBJ_CODE Object code identifying the component
 * @param _FORMAT printf-style format string
 * @param ... Variable arguments
 */
#define RLOG_D(_OBJ_CODE, _FORMAT, ...) \
	sys_logger_write_record(DEBUG_LOG, _OBJ_CODE, _FORMAT, ##__VA_ARGS__)

/**
 * @brief Log a warning message
 * @param _OBJ_CODE Object code identifying the component
 * @param _FORMAT printf-style format string
 * @param ... Variable arguments
 */
#define RLOG_W(_OBJ_CODE, _FORMAT, ...) \
	sys_logger_write_record(WARNING_LOG, _OBJ_CODE, _FORMAT, ##__VA_ARGS__)

/**
 * @brief Log an error message
 * @param _OBJ_CODE Object code identifying the component
 * @param _ERR_CODE Error code
 * @param _FORMAT printf-style format string
 * @param ... Variable arguments
 */
#define RLOG_E(_OBJ_CODE, _ERR_CODE, _FORMAT, ...) \
	sys_logger_write_record(_ERR_CODE, _OBJ_CODE, _FORMAT, ##__VA_ARGS__)

/* Public variables --------------------------------------------------------- */
/* Public APIs -------------------------------------------------------------- */
/**
 * @brief Initialize the logger system
 * @note Must be called once during system initialization
 */
void sys_logger_init(void);

/**
 * @brief Clear all logs in the buffer
 * @note Resets read/write pointers to empty state
 */
void sys_logger_clear(void);

/**
 * @brief Write a formatted log record with timestamp
 * @param[in] log_type Log type (INFO_LOG, DEBUG_LOG, WARNING_LOG, or error code)
 * @param[in] obj_code Object code identifying the component
 * @param[in] format printf-style format string
 * @param[in] ... Variable arguments for format string
 * @note Automatically removes oldest logs if buffer is full
 * @return true if successful, false otherwise
 */
bool sys_logger_write_record(uint8_t log_type, log_object_code_t obj_code, const char *format, ...);

/**
 * @brief Periodic task to transmit logs via USB CDC
 * @note Call this function periodically (e.g., every 10-50ms) in main loop
 *       or timer interrupt. It attempts to send buffered logs via USB CDC.
 */
void sys_logger_task(void);

/**
 * @brief Get number of free bytes in the buffer
 * @return Number of bytes available for new logs
 */
uint16_t sys_logger_space_count(void);

/**
 * @brief Get number of bytes currently stored in the buffer
 * @return Number of bytes waiting to be transmitted
 */
uint16_t sys_logger_data_count(void);

/**
 * @brief Non-destructive peek: copy up to @p max_len bytes from the tail of
 *        the circular buffer into @p out without removing them.
 *
 * @param[out] out     Destination buffer
 * @param[in]  max_len Maximum bytes to copy
 * @return Number of bytes actually copied (0 if buffer empty)
 * @note  May return fewer bytes than available when the data wraps around
 *        the circular buffer end.  Call again after consuming the first chunk.
 */
uint16_t sys_logger_peek(uint8_t *out, uint16_t max_len);

/**
 * @brief Remove @p len bytes from the tail of the circular buffer.
 *
 * @param[in] len Number of bytes to discard
 * @note  Typically called after the bytes peeked by sys_logger_peek() have
 *        been successfully persisted or transmitted.
 */
void sys_logger_consume(uint16_t len);

/* ── Flash persistence API (HAVE_FLASH_STORAGE only) ───────────────────────
 *
 *  Flash log sub-partition layout (64 KB, append-only):
 *    Records stored sequentially:
 *    [LEN_LO(1)][LEN_HI(1)][LOG_TYPE(1)][OBJ_CODE(1)][TIMESTAMP(6)][DATA_LEN(1)][MSG...][PAD to 4B]
 *
 *  Two-index model:
 *    g_flash_log_write_pos  — next byte to write (owner: sys_logger_flash_persist)
 *    g_flash_log_read_pos   — next byte host has NOT yet confirmed receiving
 *                             (owner: sys_logger_flash_consume)
 *
 *  Read flow (non-destructive):
 *    1. sys_logger_flash_persist()        — flush RAM → flash
 *    2. sys_logger_flash_read_chunk()     — send pending bytes from read_pos
 *    3. sys_logger_flash_consume(length)  — host confirms receipt; advance read_pos
 *
 *  On boot, write_pos is recovered by scanning flash.  read_pos always resets
 *  to 0 — unconfirmed data is re-sent to the host on next request.
 * ───────────────────────────────────────────────────────────────────────── */
#ifdef HAVE_FLASH_STORAGE

/**
 * @brief  Flush pending RAM log records to the flash log sub-partition.
 *
 *         Reads all available data from the RAM circular buffer and writes it
 *         to flash at the current write position.  Advances write_pos by the
 *         number of bytes persisted.  Stops when the RAM buffer is empty or
 *         the flash partition is full.
 *
 * @return Number of RAM bytes actually flushed to flash (0 if nothing to do).
 */
uint32_t sys_logger_flash_persist(void);

/**
 * @brief  Number of bytes in flash that the host has not yet confirmed.
 *         Equal to (write_pos - read_pos).
 * @return Pending byte count (0 = nothing to send).
 */
uint32_t sys_logger_flash_pending_bytes(void);

/**
 * @brief  Current value of the flash log read cursor.
 *         Returned alongside log_data so the host can echo it in log_clear.
 * @return Byte offset of the next unconfirmed byte (== g_flash_log_read_pos).
 */
uint32_t sys_logger_flash_read_pos(void);

/**
 * @brief  Read a chunk of un-confirmed flash log data.
 *
 *         Reads up to @p max_len bytes starting at g_flash_log_read_pos.
 *         Does NOT advance the read cursor — call sys_logger_flash_consume()
 *         after the host confirms receipt.
 *
 * @param[out] out      Destination buffer (must be at least max_len bytes)
 * @param[in]  max_len  Maximum bytes to copy
 * @return Number of bytes copied (0 if no pending data or error).
 */
uint32_t sys_logger_flash_read_chunk(uint8_t *out, uint16_t max_len);

/**
 * @brief  Advance the read cursor by @p length bytes.
 *
 *         Called when the host confirms it has successfully received @p length
 *         bytes starting at the current read_pos.  When read_pos catches up
 *         with write_pos both pointers are reset to 0 (log is empty).
 *
 * @param[in] length Number of bytes the host confirmed receiving.
 */
void sys_logger_flash_consume(uint32_t length);

#endif /* HAVE_FLASH_STORAGE */

/* -------------------------------------------------------------------------- */

#endif /* __SYS_LOGGER_H */

/* End of file -------------------------------------------------------------- */
