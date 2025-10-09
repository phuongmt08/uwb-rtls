/**
 * @file       sys_logger.h
 * @copyright
 * @license
 * @version    1.0.0
 * @date       2025
 * @author
 * @brief      Simple RAM logger with USB CDC output
 */
/* Define to prevent recursive inclusion ------------------------------------ */
#ifndef __SYS_LOGGER_H
#define __SYS_LOGGER_H

/* Includes ----------------------------------------------------------------- */
#include <stdint.h>
#include <stdbool.h>
#include <stdarg.h>

/* Public defines ----------------------------------------------------------- */
#define SYS_LOGGER_BUF_SIZE        (4096U)
#define SYS_LOGGER_MAX_MSG_LEN     (180U)

#define LOG_HDR_LEN                (2U)
#define LOG_MAX_RECORD_SIZE        (LOG_HDR_LEN + SYS_LOGGER_MAX_MSG_LEN)

/* Public enumerate/structure ----------------------------------------------- */
typedef enum
{
	LOG_LEVEL_INFO = 0,
	LOG_LEVEL_DEBUG,
	LOG_LEVEL_WARN,
	LOG_LEVEL_ERROR
} sys_log_level_t;

/* Public macros ------------------------------------------------------------ */
#define LOGI(_FORMAT, ...) sys_logger_write(LOG_LEVEL_INFO,  _FORMAT, ##__VA_ARGS__)
#define LOGD(_FORMAT, ...) sys_logger_write(LOG_LEVEL_DEBUG, _FORMAT, ##__VA_ARGS__)
#define LOGW(_FORMAT, ...) sys_logger_write(LOG_LEVEL_WARN,  _FORMAT, ##__VA_ARGS__)
#define LOGE(_FORMAT, ...) sys_logger_write(LOG_LEVEL_ERROR, _FORMAT, ##__VA_ARGS__)

/* Public variables --------------------------------------------------------- */
/* Public APIs -------------------------------------------------------------- */
void sys_logger_init(void);
void sys_logger_clear(void);
void sys_logger_write(sys_log_level_t level, const char *fmt, ...);
void sys_logger_task(void);

uint16_t sys_logger_space_count(void);
uint16_t sys_logger_data_count(void);

/* -------------------------------------------------------------------------- */

#endif /* __SYS_LOGGER_H */

/* End of file -------------------------------------------------------------- */