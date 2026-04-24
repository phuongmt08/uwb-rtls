/**
 * @file       sys_logger.c
 * @copyright  Copyright (C) 2019 ITRVN.
 * @license    This project is released under the Fiot License.
 * @version    1.0.0
 * @date       2025
 * @author     Phuong Mai
 * @brief      Simple RAM logger with USB CDC output
 */
/* Public includes ---------------------------------------------------------- */
#include "sys_logger.h"

#include "log_config.h"
#include "usbd_cdc_if.h"
#include "stm32f4xx_hal.h"
#include <stdarg.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>
#include "config.h"
#include "bsp_util.h"
#include "config.h"

#ifdef HAVE_FLASH_STORAGE
#include "sys_flash_storage.h"
#endif

/* Private includes --------------------------------------------------------- */
/* Private defines ---------------------------------------------------------- */
#define LOGGER_MAGIC (0xA5C3E91F)  // Magic number for initialized state detection
#define LOGGER_FLASH_SYNC_PERIOD_MS (10000u)
#define LOGGER_STUB_PERIOD_MS (500u)

/* Flash log entry on-disk format:
 *   [LEN_LO (1)][LEN_HI (1)][RAW_RECORD (LEN bytes)][PAD to 4-byte boundary]
 *   LEN = 0xFFFF means erased flash = end of written data.
 *   LEN = 0x0000 is also treated as invalid (should never be a valid record size).
 *   RAW_RECORD bytes are identical to the RAM circular buffer format:
 *     [LOG_TYPE(1)][OBJ_CODE(1)][TIMESTAMP(6)][DATA_LEN(1)][MESSAGE(DATA_LEN)]
 */
#define FLASH_LOG_LEN_FIELD   2u
#define FLASH_LOG_BATCH_SIZE  1024u

/* Private enumerate/structure ---------------------------------------------- */
/**
 * @brief Logger structure containing circular buffer and metadata
 */
typedef struct
{
  uint32_t magic;                        // Magic number to detect if initialized
  uint8_t  buffer[SYS_LOGGER_BUF_SIZE];  // Circular buffer storage
  uint16_t head;                         // Write index
  uint16_t tail;                         // Read index
} sys_logger_t;

/* Private macros ----------------------------------------------------------- */
/* Public variables --------------------------------------------------------- */
/* Private variables -------------------------------------------------------- */
static sys_logger_t g_logger;             // Global logger instance
static bool         initialized = false;  // Initialization flag
#ifndef HAVE_RTC
static uint32_t log_seq_num = 0;  // Sequence number for logs when no RTC
#endif

#ifdef HAVE_FLASH_STORAGE
/** Byte offset of the next write in the log flash sub-partition (0-based). */
static uint32_t g_flash_log_write_pos = 0u;
/** Byte offset of the next byte the host has NOT yet confirmed receiving. */
static uint32_t g_flash_log_read_pos  = 0u;
/** Timestamp of last RAM->flash sync. */
static uint32_t g_flash_sync_last_ms  = 0u;
/** Temporary write buffer (static to avoid large stack allocs). */
static uint8_t  s_flash_batch[FLASH_LOG_BATCH_SIZE];
#endif

/* Private prototypes ------------------------------------------------------- */
/**
 * @brief Calculate free space in circular buffer
 * @return Number of free bytes
 * @note Returns (size - used - 1) because we keep one byte empty to distinguish
 *       full from empty state (head == tail means empty)
 */
static uint16_t logger_space_count(void);

/**
 * @brief Calculate used space in circular buffer
 * @return Number of bytes containing data
 */
static uint16_t logger_data_count(void);

/**
 * @brief Write single byte to circular buffer
 * @param[in] byte Byte to write
 * @note Automatically overwrites oldest data if buffer is full
 */
static void logger_write_byte(uint8_t byte);

/**
 * @brief Write multiple bytes to circular buffer
 * @param[in] data Pointer to data buffer
 * @param[in] len Number of bytes to write
 */
static void logger_write_data(const uint8_t *data, uint16_t len);

/**
 * @brief Read contiguous chunk from circular buffer without removing it
 * @param[out] out Output buffer
 * @param[in] max_len Maximum bytes to read
 * @return Number of bytes read (may be less than max_len due to wrap-around)
 * @note Only reads up to the end of buffer or wrap point, ensuring linear chunk
 */
static uint16_t logger_read_linear(uint8_t *out, uint16_t max_len);

/**
 * @brief Remove specified number of bytes from circular buffer
 * @param[in] len Number of bytes to remove from tail
 * @note Used after successful transmission to free up buffer space
 */
static void logger_pop_data(uint16_t len);

/**
 * @brief Internal initialization routine
 * @note Checks magic number to determine if this is first boot or reset
 */
static void logger_init(void);
static void logger_test_stub(void);

/* Private implementations -------------------------------------------------- */
static uint16_t logger_space_count(void)
{
  uint16_t count;
  if (g_logger.head >= g_logger.tail)
  {
    count = SYS_LOGGER_BUF_SIZE - (g_logger.head - g_logger.tail) - 1;
  }
  else
  {
    count = g_logger.tail - g_logger.head - 1;
  }
  return count;
}

static uint16_t logger_data_count(void)
{
  uint16_t count;
  if (g_logger.head >= g_logger.tail)
  {
    count = g_logger.head - g_logger.tail;
  }
  else
  {
    count = SYS_LOGGER_BUF_SIZE - g_logger.tail + g_logger.head;
  }
  return count;
}

static void logger_write_byte(uint8_t byte)
{
  g_logger.buffer[g_logger.head] = byte;
  g_logger.head                  = (g_logger.head + 1) % SYS_LOGGER_BUF_SIZE;

  // Overwrite oldest data if buffer full
  if (g_logger.head == g_logger.tail)
  {
    g_logger.tail = (g_logger.tail + 1) % SYS_LOGGER_BUF_SIZE;
  }
}

static void logger_write_data(const uint8_t *data, uint16_t len)
{
  for (uint16_t i = 0; i < len; i++)
  {
    logger_write_byte(data[i]);
  }
}

static uint16_t logger_read_linear(uint8_t *out, uint16_t max_len)
{
  CHECK(out != NULL, 0);
  CHECK(max_len > 0, 0);

  if (g_logger.tail == g_logger.head)
  {
    return 0;  // Buffer empty
  }

  uint16_t len;
  if (g_logger.tail < g_logger.head)
  {
    // Contiguous block: tail -> head
    len = g_logger.head - g_logger.tail;
    if (len > max_len)
    {
      len = max_len;
    }
    memcpy(out, &g_logger.buffer[g_logger.tail], len);
  }
  else
  {
    // Wrap around: read from tail to buffer end only
    len = SYS_LOGGER_BUF_SIZE - g_logger.tail;
    if (len > max_len)
    {
      len = max_len;
    }
    memcpy(out, &g_logger.buffer[g_logger.tail], len);
  }

  return len;
}

static void logger_pop_data(uint16_t len)
{
  for (uint16_t i = 0; i < len; i++)
  {
    if (g_logger.tail == g_logger.head)
    {
      break;  // Nothing left to pop
    }
    g_logger.tail = (g_logger.tail + 1) % SYS_LOGGER_BUF_SIZE;
  }
}

static void logger_init(void)
{
  if (g_logger.magic != LOGGER_MAGIC)
  {
    // First time initialization
    memset(&g_logger, 0, sizeof(g_logger));
    g_logger.magic = LOGGER_MAGIC;
  }
  // If magic matches, preserve existing buffer (useful after reset)
  initialized = true;
}

static void logger_test_stub(void)
{
  static uint8_t  tick_init = 0u;
  static uint32_t last_tick_ms = 0u;
  static uint32_t seq = 0u;
  uint32_t now_ms = HAL_GetTick();

  if (tick_init == 0u)
  {
    last_tick_ms = now_ms;
    tick_init = 1u;
    return;
  }

  uint32_t elapsed_ms = (uint32_t)(now_ms - last_tick_ms);
  if (elapsed_ms < LOGGER_STUB_PERIOD_MS)
    return;

  last_tick_ms = now_ms;
  (void)RLOG_I(LOG_OBJECT_CODE_TASK,
               "stub-log seq=%lu dt=%lums",
               (unsigned long)seq,
               (unsigned long)elapsed_ms);
  seq++;
}

/* Public implementations --------------------------------------------------- */
void sys_logger_init(void)
{
  logger_init();

#ifdef HAVE_FLASH_STORAGE
  /* Recover write_pos and read_pos from flash metadata — O(N) over metadata
   * entries, runs once at boot.  No raw-byte scan needed. */
  g_flash_log_write_pos = 0u;
  g_flash_log_read_pos  = 0u;
  g_flash_sync_last_ms  = HAL_GetTick();
  (void)sys_flash_log_get_positions(&g_flash_log_write_pos, &g_flash_log_read_pos);
#endif
}

void sys_logger_clear(void)
{
  g_logger.head = 0;
  g_logger.tail = 0;
}

uint16_t sys_logger_space_count(void)
{
  return logger_space_count();
}

uint16_t sys_logger_data_count(void)
{
  return logger_data_count();
}

uint16_t sys_logger_peek(uint8_t *out, uint16_t max_len)
{
  if (!initialized)
    return 0u;
  return logger_read_linear(out, max_len);
}

void sys_logger_consume(uint16_t len)
{
  if (!initialized)
    return;
  logger_pop_data(len);
}

#ifdef HAVE_FLASH_STORAGE

/* -----------------------------------------------------------------------
 * Flash persistence implementation
 * --------------------------------------------------------------------- */

uint32_t sys_logger_flash_persist(void)
{
  if (!initialized)
    return 0u;

  uint32_t total_flushed = 0u;

  while (logger_data_count() >= LOG_HEADER_LEN)
  {
    uint16_t temp_tail = g_logger.tail;
    uint32_t available = logger_data_count();
    uint32_t ram_consume = 0u;
    uint32_t batch_len = 0u;

    while (available >= LOG_HEADER_LEN)
    {
      uint8_t hdr[LOG_HEADER_LEN];
      uint16_t htail = temp_tail;
      for (uint16_t i = 0u; i < LOG_HEADER_LEN; i++) {
        hdr[i] = g_logger.buffer[htail];
        htail = (uint16_t)((htail + 1u) % SYS_LOGGER_BUF_SIZE);
      }

      uint8_t data_len = hdr[LOG_HEADER_IDX_DATA_LEN];
      uint16_t rec_total = (uint16_t)(LOG_HEADER_LEN + data_len);
      if (data_len > SYS_LOGGER_MAX_MSG_LEN)
        break;
      if (available < rec_total)
        break;

      uint32_t entry_len = FLASH_LOG_LEN_FIELD + rec_total;
      uint32_t padded = (entry_len + 3u) & ~3u;
      if ((batch_len + padded) > FLASH_LOG_BATCH_SIZE)
        break;

      s_flash_batch[batch_len + 0u] = (uint8_t)(rec_total & 0xFFu);
      s_flash_batch[batch_len + 1u] = (uint8_t)((rec_total >> 8) & 0xFFu);

      uint16_t rtail = temp_tail;
      for (uint16_t i = 0u; i < rec_total; i++) {
        s_flash_batch[batch_len + FLASH_LOG_LEN_FIELD + i] = g_logger.buffer[rtail];
        rtail = (uint16_t)((rtail + 1u) % SYS_LOGGER_BUF_SIZE);
      }

      if (padded > entry_len) {
        memset(&s_flash_batch[batch_len + entry_len], 0x00u, padded - entry_len);
      }

      temp_tail = rtail;
      available -= rec_total;
      ram_consume += rec_total;
      batch_len += padded;
    }

    if ((batch_len == 0u) || (ram_consume == 0u))
      break;

    uint32_t actual_pos;
    if (sys_flash_log_write_at(g_flash_log_read_pos,
                               s_flash_batch, batch_len, &actual_pos)
        != BSP_FLASH_OK)
      break;

    (void)actual_pos;
    (void)sys_flash_log_get_positions(&g_flash_log_write_pos, &g_flash_log_read_pos);
    if (g_flash_log_read_pos > g_flash_log_write_pos)
      g_flash_log_read_pos = g_flash_log_write_pos;

    /* Consume only after successful flash write to avoid silent data loss. */
    logger_pop_data((uint16_t)ram_consume);
    total_flushed += ram_consume;
  }

  return total_flushed;
}

uint32_t sys_logger_flash_pending_bytes(void)
{
  if (g_flash_log_write_pos > g_flash_log_read_pos)
    return g_flash_log_write_pos - g_flash_log_read_pos;
  return 0u;
}

uint32_t sys_logger_flash_read_pos(void)
{
  return g_flash_log_read_pos;
}

uint32_t sys_logger_flash_read_chunk(uint8_t *out, uint16_t max_len)
{
  if (!out || max_len == 0u)
    return 0u;
  if (g_flash_log_read_pos >= g_flash_log_write_pos)
    return 0u;

  /* Clamp to available pending bytes */
  uint32_t avail = g_flash_log_write_pos - g_flash_log_read_pos;
  uint16_t n     = (max_len < (uint16_t)avail) ? max_len : (uint16_t)avail;

  return sys_flash_log_read(out, g_flash_log_read_pos, n);
}

uint32_t sys_logger_flash_read_packet(uint8_t *out, uint16_t max_len)
{
  if (!out || max_len < (FLASH_LOG_LEN_FIELD + LOG_HEADER_LEN))
    return 0u;
  if (g_flash_log_read_pos >= g_flash_log_write_pos)
    return 0u;

  uint32_t cursor = g_flash_log_read_pos;
  uint32_t copied = 0u;

  while ((copied + FLASH_LOG_LEN_FIELD + LOG_HEADER_LEN) <= max_len)
  {
    if ((cursor + FLASH_LOG_LEN_FIELD) > g_flash_log_write_pos)
      break;

    uint8_t len_buf[FLASH_LOG_LEN_FIELD];
    if (sys_flash_log_read(len_buf, cursor, FLASH_LOG_LEN_FIELD) < FLASH_LOG_LEN_FIELD)
      break;

    uint16_t rec_len = (uint16_t)len_buf[0] | ((uint16_t)len_buf[1] << 8u);
    if (rec_len == 0u || rec_len > RLOG_MAX_RECORD_SIZE)
      break;

    uint32_t entry_padded = ((uint32_t)FLASH_LOG_LEN_FIELD + rec_len + 3u) & ~3u;
    if ((copied + entry_padded) > max_len)
      break;
    if ((cursor + entry_padded) > g_flash_log_write_pos)
      break;

    if (sys_flash_log_read(out + copied, cursor, (uint16_t)entry_padded) < (uint16_t)entry_padded)
      break;

    copied += entry_padded;
    cursor += entry_padded;
  }

  return copied;
}

void sys_logger_flash_consume(uint32_t length)
{
  if (length == 0u)
    return;

  g_flash_log_read_pos += length;

  if (g_flash_log_read_pos > g_flash_log_write_pos)
    g_flash_log_read_pos = g_flash_log_write_pos;

  /* Persist the updated read cursor to flash metadata so it survives reset */
  (void)sys_flash_log_update_read_pos(g_flash_log_read_pos);
}

#endif /* HAVE_FLASH_STORAGE */

bool sys_logger_write_record(uint8_t log_type, log_object_code_t obj_code, const char *format, ...)
{
  CHECK(format != NULL, false);

  if (!initialized)
  {
    logger_init();
  }

  // Format message using vsnprintf
  char    msg[SYS_LOGGER_MAX_MSG_LEN];
  va_list args;

  va_start(args, format);
  int n = vsnprintf(msg, sizeof(msg), format, args);
  va_end(args);

  CHECK(n > 0, false);

  // Clamp message length
  uint16_t msg_len = (n >= SYS_LOGGER_MAX_MSG_LEN) ? (SYS_LOGGER_MAX_MSG_LEN - 1) : n;

  // Build log record: [LOG_TYPE][OBJ_CODE][TIMESTAMP(6)][LEN][DATA...]
  uint8_t record[RLOG_MAX_RECORD_SIZE];

  // Log type
  record[LOG_HEADER_IDX_LOG_TYPE] = log_type;

  // Object code (set error source bit if applicable)
  record[LOG_HEADER_IDX_OBJ_CODE] = (uint8_t) obj_code;
#ifdef UWB_DEVICE_TAG
  record[LOG_HEADER_IDX_OBJ_CODE] |= LOG_ERR_SOURCE_TAG;
#else
  record[LOG_HEADER_IDX_OBJ_CODE] |= LOG_ERR_SOURCE_ANCHOR;
#endif

// Timestamp (6 bytes)
#ifdef HAVE_RTC
  uint64_t timestamp_ms = (uint64_t) bsp_rtc_get_timestamp_ms();  // Use RTC timestamp
#else
  uint64_t timestamp_ms = (uint64_t) log_seq_num++;  // Use sequence number
#endif
  memcpy(&record[LOG_HEADER_IDX_TIMESTAMP], &timestamp_ms, 6);

  // Data length
  record[LOG_HEADER_IDX_DATA_LEN] = (uint8_t) msg_len;

  // Message data
  memcpy(&record[LOG_HEADER_IDX_DATA], msg, msg_len);

  uint16_t record_len = LOG_HEADER_LEN + msg_len;

  // Make space if needed by removing oldest records
  for (uint16_t retry = 0; retry < 10; retry++)
  {
    if (logger_space_count() >= record_len)
    {
      logger_write_data(record, record_len);
      return true;
    }

    // Not enough space, pop oldest record
    if (logger_data_count() < LOG_HEADER_LEN)
    {
      break;  // Buffer corrupted or empty
    }

    // Read header of oldest record
    uint8_t  old_hdr[LOG_HEADER_LEN];
    uint16_t old_tail = g_logger.tail;

    for (uint16_t i = 0; i < LOG_HEADER_LEN; i++)
    {
      old_hdr[i] = g_logger.buffer[old_tail];
      old_tail   = (old_tail + 1) % SYS_LOGGER_BUF_SIZE;
    }

    uint16_t old_len = old_hdr[LOG_HEADER_IDX_DATA_LEN];
    CHECK(old_len > 0 && old_len <= SYS_LOGGER_MAX_MSG_LEN, false);

    // Remove entire old record (header + data)
    logger_pop_data(LOG_HEADER_LEN + old_len);
  }

  return false;
}

void sys_logger_task(void)
{
  if (!initialized)
    return;

  logger_test_stub();

#ifdef HAVE_FLASH_STORAGE
  uint32_t now_ms = HAL_GetTick();
  if ((uint32_t)(now_ms - g_flash_sync_last_ms) >= LOGGER_FLASH_SYNC_PERIOD_MS) {
    (void)sys_logger_flash_persist();
    g_flash_sync_last_ms = now_ms;
  }

#endif
}

/* End of file -------------------------------------------------------------- */
