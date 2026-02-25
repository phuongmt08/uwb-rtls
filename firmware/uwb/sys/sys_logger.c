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
#include <stdarg.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>

#ifdef HAVE_RTC
#include "bsp_util.h"  // For bsp_rtc_get_timestamp()
#endif

/* Private includes --------------------------------------------------------- */
/* Private defines ---------------------------------------------------------- */
#define LOGGER_MAGIC (0xA5C3E91F)  // Magic number for initialized state detection

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
/**
 * @brief Check condition and return value if false
 */
#define CHECK(condition, ret_val) \
  do                              \
  {                               \
    if (!(condition))             \
      return (ret_val);           \
  } while (0)

/**
 * @brief Check condition and return void if false
 */
#define CHECK_VOID(condition) \
  do                          \
  {                           \
    if (!(condition))         \
      return;                 \
  } while (0)

/* Public variables --------------------------------------------------------- */
/* Private variables -------------------------------------------------------- */
static sys_logger_t g_logger;             // Global logger instance
static bool         initialized = false;  // Initialization flag
#ifndef HAVE_RTC
static uint32_t log_seq_num = 0;  // Sequence number for logs when no RTC
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

/* Public implementations --------------------------------------------------- */
void sys_logger_init(void)
{
  logger_init();
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
  {
    return;
  }

  // Check if we have at least one complete record header
  if (logger_data_count() < LOG_HEADER_LEN)
  {
    return;
  }

  // Read the header to get record length
  uint8_t  header[LOG_HEADER_LEN];
  uint16_t tail_temp = g_logger.tail;

  // Peek header without removing
  for (uint16_t i = 0; i < LOG_HEADER_LEN; i++)
  {
    header[i] = g_logger.buffer[tail_temp];
    tail_temp = (tail_temp + 1) % SYS_LOGGER_BUF_SIZE;
  }

  uint8_t msg_len = header[LOG_HEADER_IDX_DATA_LEN];

  // Check if we have the complete record
  if (logger_data_count() < (LOG_HEADER_LEN + msg_len))
  {
    return;
  }

  // Read message data
  uint8_t message[SYS_LOGGER_MAX_MSG_LEN + 1];
  tail_temp = (g_logger.tail + LOG_HEADER_LEN) % SYS_LOGGER_BUF_SIZE;

  for (uint16_t i = 0; i < msg_len; i++)
  {
    message[i] = g_logger.buffer[tail_temp];
    tail_temp  = (tail_temp + 1) % SYS_LOGGER_BUF_SIZE;
  }
  message[msg_len] = '\0';

  // Format output with timestamp and level
  char     output[SYS_LOGGER_MAX_MSG_LEN + 50];
  uint64_t timestamp = 0;
  memcpy(&timestamp, &header[LOG_HEADER_IDX_TIMESTAMP], 6);

  uint8_t log_type = header[LOG_HEADER_IDX_LOG_TYPE];
  uint8_t obj_code = header[LOG_HEADER_IDX_OBJ_CODE];

  const char *level_str = "???";
  if (log_type == INFO_LOG)
    level_str = "INFO";
  else if (log_type == DEBUG_LOG)
    level_str = "DEBUG";
  else if (log_type == WARNING_LOG)
    level_str = "WARN";
  else
    level_str = "ERROR";

  int out_len = snprintf(output, sizeof(output), "[%lu][%s][%02X] %s\r\n", (uint32_t) timestamp, level_str,
                         obj_code, message);

// Transmit formatted text
#ifdef USE_BLE_LOGGER
  //TODO: implement here
#else
// Transmit via USB serial
  if (CDC_Transmit_FS((uint8_t *) output, out_len) == USBD_OK)
#endif
  {
    // Remove the record from buffer
    logger_pop_data(LOG_HEADER_LEN + msg_len);
  }
}

/* End of file -------------------------------------------------------------- */
