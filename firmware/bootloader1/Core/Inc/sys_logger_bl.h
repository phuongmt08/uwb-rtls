/**
 * @file    sys_logger_bl.h
 * @brief   Bootloader logger — RAM circular buffer, output via network_core
 *
 * @details
 *   Drop-in replacement for sys_logger.h so common source
 *   (e.g. network_cmd.c) compiles unchanged in bootloader builds.
 *   Differences from the app logger:
 *     - No flash storage, no USB CDC.
 *     - TIMESTAMP field filled with a monotonic sequence counter.
 *     - sys_logger_task() is a no-op (no flash sync, no test stub).
 *     - Log data is drained by network_cmd via sys_logger_peek/consume,
 *       same ACK-tracked flow as the app.
 */

#pragma once

#include <stdint.h>
#include <stdbool.h>
#include <stdarg.h>
#include "log_config.h"
#include "err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define SYS_LOGGER_BUF_SIZE     2048U
#define SYS_LOGGER_MAX_MSG_LEN  120U
#define RLOG_MAX_RECORD_SIZE    (LOG_HEADER_LEN + SYS_LOGGER_MAX_MSG_LEN)


#define RLOG_I(_OBJ_CODE, _FORMAT, ...) \
    sys_logger_write_record(INFO_LOG,    _OBJ_CODE, _FORMAT, ##__VA_ARGS__)

#define RLOG_D(_OBJ_CODE, _FORMAT, ...) \
    sys_logger_write_record(DEBUG_LOG,   _OBJ_CODE, _FORMAT, ##__VA_ARGS__)

#define RLOG_W(_OBJ_CODE, _FORMAT, ...) \
    sys_logger_write_record(WARNING_LOG, _OBJ_CODE, _FORMAT, ##__VA_ARGS__)

#define RLOG_E(_OBJ_CODE, _ERR_CODE, _FORMAT, ...) \
    sys_logger_write_record(_ERR_CODE,   _OBJ_CODE, _FORMAT, ##__VA_ARGS__)


void     sys_logger_init(void);
void     sys_logger_clear(void);
bool     sys_logger_write_record(uint8_t log_type, log_object_code_t obj_code,
                                  const char *format, ...);
void     sys_logger_task(void);

uint16_t sys_logger_space_count(void);
uint16_t sys_logger_data_count(void);
uint16_t sys_logger_peek(uint8_t *out, uint16_t max_len);
void     sys_logger_consume(uint16_t len);

#ifdef __cplusplus
}
#endif