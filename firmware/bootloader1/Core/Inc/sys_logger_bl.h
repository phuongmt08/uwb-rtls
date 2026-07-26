/**
 * @file    sys_logger_bl.h
 * @brief   Bootloader logger — RAM circular buffer, output via network_core
 * @author  Phuong Mai
 */

#pragma once

#include <stdint.h>
#include <stdbool.h>
#include <stdarg.h>
#include "log_config.h"
#include "err.h"
#include "memorylayout.h"

#ifdef __cplusplus
extern "C" {
#endif

#define SYS_LOGGER_SHARED_META_SIZE 16U
#define SYS_LOGGER_BUF_SIZE     (MEM_SHARED_LOG_RAM_SIZE - SYS_LOGGER_SHARED_META_SIZE)
#define SYS_LOGGER_MAX_MSG_LEN  120U
#define RLOG_MAX_RECORD_SIZE    (LOG_HEADER_LEN + SYS_LOGGER_MAX_MSG_LEN)

/* Disable heavy logging strings in bootloader to prevent FLASH overflow */
#define RLOG_DISABLE_BL 1

#if RLOG_DISABLE_BL
#define RLOG_I(_OBJ_CODE, _FORMAT, ...) ((void)0)
#define RLOG_D(_OBJ_CODE, _FORMAT, ...) ((void)0)
#define RLOG_W(_OBJ_CODE, _FORMAT, ...) ((void)0)
#define RLOG_E(_OBJ_CODE, _ERR_CODE, _FORMAT, ...) ((void)0)
#else
#define RLOG_I(_OBJ_CODE, _FORMAT, ...) \
    sys_logger_write_record(INFO_LOG,    _OBJ_CODE, _FORMAT, ##__VA_ARGS__)

#define RLOG_D(_OBJ_CODE, _FORMAT, ...) \
    sys_logger_write_record(DEBUG_LOG,   _OBJ_CODE, _FORMAT, ##__VA_ARGS__)

#define RLOG_W(_OBJ_CODE, _FORMAT, ...) \
    sys_logger_write_record(WARNING_LOG, _OBJ_CODE, _FORMAT, ##__VA_ARGS__)

#define RLOG_E(_OBJ_CODE, _ERR_CODE, _FORMAT, ...) \
    sys_logger_write_record(_ERR_CODE,   _OBJ_CODE, _FORMAT, ##__VA_ARGS__)
#endif

static inline uint32_t sys_logger_get_warning_count(void) { return 0U; }
static inline uint32_t sys_logger_get_error_count(void) { return 0U; }

void     sys_logger_init(void);
void     sys_logger_clear(void);
bool     sys_logger_write_record(uint8_t log_type, log_object_code_t obj_code,
                                  const char *format, ...);
void     sys_logger_task(void);

uint16_t sys_logger_space_count(void);
uint16_t sys_logger_data_count(void);
uint16_t sys_logger_peek(uint8_t *out, uint16_t max_len);
uint16_t sys_logger_ram_peek_packet(uint8_t *out, uint16_t max_len);
void     sys_logger_ram_consume(uint16_t len);

#ifdef __cplusplus
}
#endif