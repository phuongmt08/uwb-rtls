/**
 * @file    sys_logger_bl.c
 * @brief   Bootloader logger — RAM circular buffer
 *
 * @details
 *   Implements the same API surface as sys_logger.h so that network_cmd.c
 *   compiles unchanged.  All storage is RAM-only (no flash).
 *   Log records are drained by network_cmd via sys_logger_peek / consume
 *   with the standard ACK-tracked flow.
 */

#include "sys_logger_bl.h"
#include "bsp_util_bl.h"
#include "stm32f4xx_hal.h"

#include <stdarg.h>
#include <string.h>
#include <stdio.h>

#define BL_LOG_LEN_FIELD 2u
#define LOGGER_MAGIC (0xA5C3E91Fu)
#define LOGGER_FORMAT_FRAMED (0x00010001u)


typedef struct {
    uint32_t magic;
    uint32_t format;
    uint16_t head;
    uint16_t tail;
    uint32_t reserved;
    uint8_t  buf[SYS_LOGGER_BUF_SIZE];
} bl_log_buf_t;

static bl_log_buf_t s_log __attribute__((section(".shared_log"), aligned(4), used));
static bool         s_inited  = false;

/* ─────────────────────────────────────────────
 * Ring buffer helpers
 * ───────────────────────────────────────────── */

static uint16_t buf_used(void)
{
    if (s_log.head >= s_log.tail) {
        return s_log.head - s_log.tail;
    }
    return (uint16_t)(SYS_LOGGER_BUF_SIZE - s_log.tail + s_log.head);
}

static void buf_push_byte(uint8_t b)
{
    s_log.buf[s_log.head] = b;
    s_log.head = (uint16_t)((s_log.head + 1u) % SYS_LOGGER_BUF_SIZE);

    if (s_log.head == s_log.tail) {                      /* full: drop oldest */
        s_log.tail = (uint16_t)((s_log.tail + 1u) % SYS_LOGGER_BUF_SIZE);
    }
}

static void buf_push(const uint8_t *data, uint16_t len)
{
    for (uint16_t i = 0u; i < len; i++) {
        buf_push_byte(data[i]);
    }
}

static uint8_t buf_peek_at(uint16_t offset)
{
    return s_log.buf[(s_log.tail + offset) % SYS_LOGGER_BUF_SIZE];
}

static void buf_pop(uint16_t len)
{
    uint16_t avail = buf_used();
    if (len > avail) len = avail;
    s_log.tail = (uint16_t)((s_log.tail + len) % SYS_LOGGER_BUF_SIZE);
}

/* Copy up to max_len bytes from tail into out (linear, handles wrap). */
static uint16_t buf_read(uint8_t *out, uint16_t max_len)
{
    uint16_t n = buf_used();
    if (n > max_len) n = max_len;

    for (uint16_t i = 0u; i < n; i++) {
        out[i] = s_log.buf[(s_log.tail + i) % SYS_LOGGER_BUF_SIZE];
    }
    return n;
}

/* Drop oldest complete record to make room. */
static void drop_oldest(void)
{
    if (buf_used() < BL_LOG_LEN_FIELD) {
        s_log.head = s_log.tail = 0u;
        return;
    }

    uint16_t rec_len = (uint16_t)buf_peek_at(0u) |
                       ((uint16_t)buf_peek_at(1u) << 8u);
    if (rec_len == 0u || rec_len > RLOG_MAX_RECORD_SIZE) {
        s_log.head = s_log.tail = 0u;
        return;
    }

    uint16_t entry_len  = (uint16_t)(BL_LOG_LEN_FIELD + rec_len);
    uint16_t padded_len = (uint16_t)((entry_len + 3u) & ~3u);
    if (padded_len > buf_used()) {
        s_log.head = s_log.tail = 0u;
        return;
    }

    buf_pop(padded_len);
}

/* ─────────────────────────────────────────────
 * Public API — matches sys_logger.h signatures
 * ───────────────────────────────────────────── */

void sys_logger_init(void)
{
    bool valid = (s_log.magic == LOGGER_MAGIC) &&
                 (s_log.format == LOGGER_FORMAT_FRAMED) &&
                 (s_log.head < SYS_LOGGER_BUF_SIZE) &&
                 (s_log.tail < SYS_LOGGER_BUF_SIZE);

    if (!valid) {
        memset(&s_log, 0, sizeof(s_log));
        s_log.magic  = LOGGER_MAGIC;
        s_log.format = LOGGER_FORMAT_FRAMED;
    }

    s_inited = true;
}

void sys_logger_clear(void)
{
    s_log.head = 0u;
    s_log.tail = 0u;
}

bool sys_logger_write_record(uint8_t           log_type,
                              log_object_code_t obj_code,
                              const char       *format, ...)
{
    if (!s_inited || !format) {
        return false;
    }

    /* Format message */
    char    msg[SYS_LOGGER_MAX_MSG_LEN];
    va_list args;
    va_start(args, format);
    int n = vsnprintf(msg, sizeof(msg), format, args);
    va_end(args);

    if (n <= 0) {
        return false;
    }

    uint8_t msg_len = (n >= (int)SYS_LOGGER_MAX_MSG_LEN)
                    ? (uint8_t)(SYS_LOGGER_MAX_MSG_LEN - 1u)
                    : (uint8_t)n;

    uint16_t record_len = (uint16_t)(LOG_HEADER_LEN + msg_len);
    uint16_t entry_len  = (uint16_t)(BL_LOG_LEN_FIELD + record_len);
    uint16_t padded_len = (uint16_t)((entry_len + 3u) & ~3u);

    if (padded_len >= SYS_LOGGER_BUF_SIZE) {
        return false;
    }

    /* Make room — drop oldest records until we have space */
    for (uint8_t retry = 0u; retry < 10u; retry++) {
        if ((SYS_LOGGER_BUF_SIZE - 1u - buf_used()) >= padded_len) {
            break;
        }
        drop_oldest();
    }

    if ((SYS_LOGGER_BUF_SIZE - 1u - buf_used()) < padded_len) {
        return false;
    }

    /* Build full raw record bytes. */
    uint8_t record[RLOG_MAX_RECORD_SIZE];
    memset(record, 0, sizeof(record));

    uint64_t ts64 = bsp_rtc_get_timestamp_ms();

    record[LOG_HEADER_IDX_LOG_TYPE] = log_type;
    record[LOG_HEADER_IDX_OBJ_CODE] = (uint8_t)obj_code;
    memcpy(&record[LOG_HEADER_IDX_TIMESTAMP], &ts64, 6u);  /* 6-byte timestamp field */
    record[LOG_HEADER_IDX_DATA_LEN] = msg_len;
    memcpy(&record[LOG_HEADER_IDX_DATA], msg, msg_len);

    /* Persist in framed format expected by host parser:
     * [len_lo][len_hi][record][pad to 4-byte]. */
    uint8_t len_hdr[BL_LOG_LEN_FIELD];
    len_hdr[0] = (uint8_t)(record_len & 0xFFu);
    len_hdr[1] = (uint8_t)((record_len >> 8u) & 0xFFu);

    buf_push(len_hdr, BL_LOG_LEN_FIELD);
    buf_push(record, record_len);

    uint16_t pad_len = (uint16_t)(padded_len - entry_len);
    for (uint16_t i = 0u; i < pad_len; i++) {
        buf_push_byte(0u);
    }

    return true;
}

void sys_logger_task(void)
{
    /* No-op in bootloader — no flash sync needed */
}

uint16_t sys_logger_space_count(void)
{
    return (uint16_t)(SYS_LOGGER_BUF_SIZE - 1u - buf_used());
}

uint16_t sys_logger_data_count(void)
{
    return buf_used();
}

uint16_t sys_logger_peek(uint8_t *out, uint16_t max_len)
{
    return buf_read(out, max_len);
}

void sys_logger_consume(uint16_t len)
{
    buf_pop(len);
}