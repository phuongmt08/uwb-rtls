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
 *             in RAM and transmits them via USB CDC (or other interfaces like BLE).
 * 
 *             Log Record Format:
 *             +----------+----------+-------------+--------+-------------+
 *             | LOG_TYPE | OBJ_CODE | TIMESTAMP   | LEN    | MESSAGE     |
 *             | 1 byte   | 1 byte   | 6 bytes     | 1 byte | LEN bytes   |
 *             +----------+----------+-------------+--------+-------------+
 *             Total Header: 9 bytes (LOG_HEADER_LEN)
 * 
 *             Flow Diagram:
 *             
 *             User Code                RAM Buffer              USB CDC / BLE
 *                |                          |                        |
 *                |  LOGI("hello")           |                        |
 *                |------------------------->|                        |
 *                |   [Pack record]          |                        |
 *                |   [Write to buffer]      |                        |
 *                |                          |                        |
 *                |                          |                        |
 *                |  sys_logger_task()       |                        |
 *                |------------------------->|                        |
 *                |                          |  Read chunk            |
 *                |                          |----------------------->|
 *                |                          |    CDC_Transmit_FS()   |
 *                |                          |<-----------------------|
 *                |                          |  [Pop sent data]       |
 *                |                          |                        |
 * 
 */
/* Define to prevent recursive inclusion ------------------------------------ */
#ifndef __SYS_LOGGER_H
#define __SYS_LOGGER_H

/* Includes ----------------------------------------------------------------- */
#include <stdint.h>
#include <stdbool.h>
#include <stdarg.h>
#include "log_config.h"

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

/* -------------------------------------------------------------------------- */

#endif /* __SYS_LOGGER_H */

/* End of file -------------------------------------------------------------- */
