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

#ifdef HAVE_RTC
#include "bsp_util.h"  // For bsp_rtc_get_timestamp()
#endif

#ifdef HAVE_FLASH_STORAGE
#include "sys_flash_storage.h"
#endif

/* Private includes --------------------------------------------------------- */
/* Private defines ---------------------------------------------------------- */
#define LOGGER_MAGIC (0xA5C3E91F)  // Magic number for initialized state detection

/* Flash log entry on-disk format:
 *   [LEN_LO (1)][LEN_HI (1)][RAW_RECORD (LEN bytes)][PAD to 4-byte boundary]
 *   LEN = 0xFFFF means erased flash = end of written data.
 *   LEN = 0x0000 is also treated as invalid (should never be a valid record size).
 *   RAW_RECORD bytes are identical to the RAM circular buffer format:
 *     [LOG_TYPE(1)][OBJ_CODE(1)][TIMESTAMP(6)][DATA_LEN(1)][MESSAGE(DATA_LEN)]
 */
#define FLASH_LOG_LEN_FIELD  2u                              /* bytes for the LEN prefix  */
#define FLASH_LOG_MAX_ENTRY  (FLASH_LOG_LEN_FIELD + RLOG_MAX_RECORD_SIZE + 4u) /* worst-case with pad */

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
/** Temporary write buffer (static to avoid large stack allocs). */
static uint8_t  s_flash_batch[FLASH_LOG_MAX_ENTRY];
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

  if ((uint32_t)(now_ms - last_tick_ms) < 100u)
    return;

  last_tick_ms = now_ms;
  (void)RLOG_I(LOG_OBJECT_CODE_TASK, "stub-log seq=%lu", (unsigned long)seq);
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
    /* Check if we have space for at least one minimal entry */
    if ((g_flash_log_write_pos + FLASH_LOG_LEN_FIELD + LOG_HEADER_LEN + 4u)
        > BSP_FLASH_LOG_DATA_LENGTH)
      break;  /* flash log partition full */

    /* -- Step 1: peek the next record header to learn its total length -- */
    uint8_t  hdr[LOG_HEADER_LEN];
    uint16_t got = sys_logger_peek(hdr, LOG_HEADER_LEN);
    if (got < LOG_HEADER_LEN)
      break;  /* not enough data for a full header */

    uint8_t  data_len  = hdr[LOG_HEADER_IDX_DATA_LEN];
    uint16_t rec_total = (uint16_t)(LOG_HEADER_LEN + data_len);

    if (data_len > SYS_LOGGER_MAX_MSG_LEN)
      break;  /* corrupted record in RAM buffer */
    if (logger_data_count() < rec_total)
      break;  /* record not fully in buffer yet */

    /* -- Step 2: peek the complete record into s_flash_batch[2..] -- */
    s_flash_batch[0] = (uint8_t)(rec_total & 0xFFu);        /* LEN low  */
    s_flash_batch[1] = (uint8_t)((rec_total >> 8) & 0xFFu); /* LEN high */

    uint16_t n = 0u;
    while (n < rec_total)
    {
      uint16_t chunk = logger_read_linear(s_flash_batch + FLASH_LOG_LEN_FIELD + n,
                                          rec_total - n);
      if (chunk == 0u)
        break;
      logger_pop_data(chunk);
      n += chunk;
    }
    if (n < rec_total)
      break;  /* failed to read complete record */

    /* -- Step 3: pad entry to 4-byte boundary, fill with zeros -- */
    uint32_t entry_len = FLASH_LOG_LEN_FIELD + rec_total;
    uint32_t padded    = (entry_len + 3u) & ~3u;
    if (padded > entry_len)
      memset(s_flash_batch + entry_len, 0x00u, padded - entry_len);

    /* -- Step 4: write to flash, embedding current read_pos in metadata -- */
    uint32_t actual_pos;
    if (sys_flash_log_write_at(g_flash_log_read_pos,
                               s_flash_batch, padded, &actual_pos)
        != BSP_FLASH_OK)
      break;

    if (actual_pos == 0u && g_flash_log_write_pos != 0u) {
      /* Real sector wrap: append restarted from offset 0 in new active sector. */
      g_flash_log_read_pos  = 0u;
      g_flash_log_write_pos = padded;
    } else {
      /* Normal append (or cursor resync after reboot): trust actual_pos. */
      g_flash_log_write_pos = actual_pos + padded;
      if (g_flash_log_read_pos > g_flash_log_write_pos)
        g_flash_log_read_pos = g_flash_log_write_pos;
    }
    total_flushed += rec_total;
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
  uint64_t timestamp_ms = (uint64_t) bsp_rtc_get_timestamp();  // Use RTC timestamp
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
  sys_logger_flash_persist();

  /* Need at least the 2-byte length prefix + one full header in flash */
  if (sys_logger_flash_pending_bytes() < (FLASH_LOG_LEN_FIELD + LOG_HEADER_LEN))
    return;

  /* Read the 2-byte on-disk length prefix at current read_pos */
  uint8_t len_buf[FLASH_LOG_LEN_FIELD];
  if (sys_flash_log_read(len_buf, g_flash_log_read_pos, FLASH_LOG_LEN_FIELD)
      < FLASH_LOG_LEN_FIELD)
    return;

  uint16_t rec_len = (uint16_t)len_buf[0] | ((uint16_t)len_buf[1] << 8u);

  /* Sanity check: skip a corrupted prefix */
  if (rec_len == 0u || rec_len > RLOG_MAX_RECORD_SIZE)
  {
    sys_logger_flash_consume(FLASH_LOG_LEN_FIELD);
    return;
  }

  /* Full on-disk entry (length-prefix + record + padding) must be present */
  uint32_t entry_padded = ((uint32_t)FLASH_LOG_LEN_FIELD + rec_len + 3u) & ~3u;
  if (sys_logger_flash_pending_bytes() < entry_padded)
    return;

  /* Read the raw record (after the 2-byte prefix) into s_flash_batch */
  if (sys_flash_log_read(s_flash_batch, g_flash_log_read_pos + FLASH_LOG_LEN_FIELD, rec_len)
      < rec_len)
    return;

  /* Parse record fields and format as human-readable text */
  uint64_t    timestamp = 0u;
  memcpy(&timestamp, &s_flash_batch[LOG_HEADER_IDX_TIMESTAMP], 6u);

  uint8_t     log_type  = s_flash_batch[LOG_HEADER_IDX_LOG_TYPE];
  uint8_t     obj_code  = s_flash_batch[LOG_HEADER_IDX_OBJ_CODE];
  uint8_t     data_len  = s_flash_batch[LOG_HEADER_IDX_DATA_LEN];

  if (data_len > SYS_LOGGER_MAX_MSG_LEN)
    data_len = SYS_LOGGER_MAX_MSG_LEN;

  char msg[SYS_LOGGER_MAX_MSG_LEN + 1u];
  memcpy(msg, &s_flash_batch[LOG_HEADER_IDX_DATA], data_len);
  msg[data_len] = '\0';

  const char *level_str;
  if      (log_type == INFO_LOG)    level_str = "INFO";
  else if (log_type == DEBUG_LOG)   level_str = "DEBUG";
  else if (log_type == WARNING_LOG) level_str = "WARN";
  else                              level_str = "ERROR";

  char output[SYS_LOGGER_MAX_MSG_LEN + 50u];
  int  out_len = snprintf(output, sizeof(output), "[%lu][%s][%02X] %s\r\n",
                          (uint32_t)timestamp, level_str, obj_code, msg);

#ifdef USE_BLE_LOGGER
  /* TODO: send via BLE */
  (void)out_len;
  sys_logger_flash_consume(entry_padded);
#else
  if (CDC_Transmit_FS((uint8_t *)output, (uint16_t)out_len) == USBD_OK)
    sys_logger_flash_consume(entry_padded);
#endif

#else  /* !HAVE_FLASH_STORAGE — fallback: direct RAM → USB */

  if (logger_data_count() < LOG_HEADER_LEN)
    return;

  /* Peek header from RAM circular buffer */
  uint8_t  header[LOG_HEADER_LEN];
  uint16_t tail_temp = g_logger.tail;
  for (uint16_t i = 0u; i < LOG_HEADER_LEN; i++)
  {
    header[i]  = g_logger.buffer[tail_temp];
    tail_temp  = (uint16_t)((tail_temp + 1u) % SYS_LOGGER_BUF_SIZE);
  }

  uint8_t msg_len = header[LOG_HEADER_IDX_DATA_LEN];
  if (logger_data_count() < (LOG_HEADER_LEN + msg_len))
    return;

  char message[SYS_LOGGER_MAX_MSG_LEN + 1u];
  tail_temp = (uint16_t)((g_logger.tail + LOG_HEADER_LEN) % SYS_LOGGER_BUF_SIZE);
  for (uint16_t i = 0u; i < msg_len; i++)
  {
    message[i] = g_logger.buffer[tail_temp];
    tail_temp  = (uint16_t)((tail_temp + 1u) % SYS_LOGGER_BUF_SIZE);
  }
  message[msg_len] = '\0';

  uint64_t    timestamp = 0u;
  memcpy(&timestamp, &header[LOG_HEADER_IDX_TIMESTAMP], 6u);
  uint8_t     log_type  = header[LOG_HEADER_IDX_LOG_TYPE];
  uint8_t     obj_code  = header[LOG_HEADER_IDX_OBJ_CODE];

  const char *level_str;
  if      (log_type == INFO_LOG)    level_str = "INFO";
  else if (log_type == DEBUG_LOG)   level_str = "DEBUG";
  else if (log_type == WARNING_LOG) level_str = "WARN";
  else                              level_str = "ERROR";

  char output[SYS_LOGGER_MAX_MSG_LEN + 50u];
  int  out_len = snprintf(output, sizeof(output), "[%lu][%s][%02X] %s\r\n",
                          (uint32_t)timestamp, level_str, obj_code, message);

#ifdef USE_BLE_LOGGER
  (void)out_len;
  logger_pop_data(LOG_HEADER_LEN + msg_len);
#else
  // if (CDC_Transmit_FS((uint8_t *)output, (uint16_t)out_len) == USBD_OK)
  //   logger_pop_data(LOG_HEADER_LEN + msg_len);
#endif

#endif /* HAVE_FLASH_STORAGE */
}

/* End of file -------------------------------------------------------------- */
