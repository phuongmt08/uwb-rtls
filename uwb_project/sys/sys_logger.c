/**
 * @file       sys_logger.c
 * @copyright
 * @license
 * @version    1.0.0
 * @date       2025
 * @author
 * @brief      Simple RAM logger with USB CDC output
 */
/* Public includes ---------------------------------------------------------- */
#include "sys_logger.h"
#include "usbd_cdc_if.h"
#include <string.h>
#include <stdio.h>
#include "err.h"
/* Private includes --------------------------------------------------------- */
/* Private defines ---------------------------------------------------------- */
#define LOGGER_MAGIC (0xA5C3E91F)

/* Private enumerate/structure ---------------------------------------------- */
typedef struct
{
	uint32_t magic;
	uint8_t buffer[SYS_LOGGER_BUF_SIZE];
	uint16_t head;
	uint16_t tail;
} sys_logger_t;

/* Private macros ----------------------------------------------------------- */

/* Public variables --------------------------------------------------------- */
/* Private variables -------------------------------------------------------- */
static sys_logger_t g_logger;
static bool initialized = false;

/* Private prototypes ------------------------------------------------------- */
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
	g_logger.head = (g_logger.head + 1) % SYS_LOGGER_BUF_SIZE;
	
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
	CHECK_ERR(out != NULL, 0);
	CHECK_ERR(max_len > 0, 0);
	
	if (g_logger.tail == g_logger.head)
	{
		return 0;
	}
	
	uint16_t len;
	if (g_logger.tail < g_logger.head)
	{
		// Contiguous block
		len = g_logger.head - g_logger.tail;
		if (len > max_len)
		{
			len = max_len;
		}
		memcpy(out, &g_logger.buffer[g_logger.tail], len);
	}
	else
	{
		// Wrap around: read from tail to end
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
			break;
		}
		g_logger.tail = (g_logger.tail + 1) % SYS_LOGGER_BUF_SIZE;
	}
}

static void logger_init(void)
{
	if (g_logger.magic != LOGGER_MAGIC)
	{
		// First time init
		memset(&g_logger, 0, sizeof(g_logger));
		g_logger.magic = LOGGER_MAGIC;
	}
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

void sys_logger_write(sys_log_level_t level, const char *fmt, ...)
{
	CHECK_VOID(fmt != NULL);
	
	if (!initialized)
	{
		logger_init();
	}
	
	// Prepare message buffer
	char msg[SYS_LOGGER_MAX_MSG_LEN];
	va_list args;
	
	va_start(args, fmt);
	int n = vsnprintf(msg, sizeof(msg), fmt, args);
	va_end(args);
	
	CHECK_VOID(n > 0);
	
	uint16_t msg_len = (n >= SYS_LOGGER_MAX_MSG_LEN) ? (SYS_LOGGER_MAX_MSG_LEN - 1) : n;
	
	// Write record: [LEVEL][LEN][DATA...]
	uint8_t record[LOG_MAX_RECORD_SIZE];
	record[0] = (uint8_t)level;
	record[1] = (uint8_t)msg_len;
	memcpy(&record[2], msg, msg_len);
	
	uint16_t record_len = LOG_HDR_LEN + msg_len;
	
	// Check if enough space, if not remove old records
	for (uint16_t retry = 0; retry < 10; retry++)
	{
		if (logger_space_count() >= record_len)
		{
			logger_write_data(record, record_len);
			return;
		}
		
		// Pop oldest record to make space
		if (logger_data_count() < LOG_HDR_LEN)
		{
			break;
		}
		
		uint8_t old_hdr[LOG_HDR_LEN];
		uint16_t old_tail = g_logger.tail;
		
		// Read header
		for (uint16_t i = 0; i < LOG_HDR_LEN; i++)
		{
			old_hdr[i] = g_logger.buffer[old_tail];
			old_tail = (old_tail + 1) % SYS_LOGGER_BUF_SIZE;
		}
		
		uint16_t old_len = old_hdr[1];
		CHECK_VOID(old_len > 0 && old_len <= SYS_LOGGER_MAX_MSG_LEN);
		
		// Remove entire old record
		logger_pop_data(LOG_HDR_LEN + old_len);
	}
}

void sys_logger_task(void)
{
	if (!initialized)
	{
		return;
	}
	
	// Read chunk from buffer
	uint8_t chunk[256];
	uint16_t len = logger_read_linear(chunk, sizeof(chunk));
	
	if (len == 0)
	{
		return;
	}
	
	// Try to send via USB CDC
	if (CDC_Transmit_FS(chunk, len) == USBD_OK)
	{
		// Successfully sent, remove from buffer
		logger_pop_data(len);
	}
	// If busy, keep data and retry next time
}

/* Private implementations -------------------------------------------------- */
/* End of file -------------------------------------------------------------- */