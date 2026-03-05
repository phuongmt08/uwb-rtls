/**
 * @file       bsp_util.h
 * @version    1.0.0
 * @date       2025-11-18
 * @author     Phuong Mai
 * @brief      BSP Utilities: CRC, RTC, Delay (STM32F4)
 */

#ifndef __BSP_UTIL_H
#define __BSP_UTIL_H

#ifdef __cplusplus
extern "C" {
#endif

/* Includes ----------------------------------------------------------- */
#include <stdint.h>
#include <stdbool.h>

/* Public defines ----------------------------------------------------- */
/* Public enumerate/structure ----------------------------------------- */
typedef enum
{
  BSP_UTIL_OK = 0,
  BSP_UTIL_ERR
} bsp_util_status_t;

/**
 * @brief RTC date/time structure
 */
typedef struct
{
  uint8_t  year;    /**< Year (0-99, offset from 2000) */
  uint8_t  month;   /**< Month (1-12) */
  uint8_t  day;     /**< Day (1-31) */
  uint8_t  hour;    /**< Hour (0-23) */
  uint8_t  minute;  /**< Minute (0-59) */
  uint8_t  second;  /**< Second (0-59) */
} bsp_rtc_time_t;

/* Public macros ------------------------------------------------------ */
/* Public variables --------------------------------------------------- */
/* Public function prototypes ----------------------------------------- */

/**
 * @brief Initialize BSP utilities (CRC, RTC, Delay timer)
 * @return BSP_UTIL_OK on success, BSP_UTIL_ERR on failure
 */
bsp_util_status_t bsp_util_init(void);

/* ===================================================================== */
/*                          CRC FUNCTIONS                                */
/* ===================================================================== */

/**
 * @brief Calculate CRC32 using hardware CRC peripheral
 * @param data Pointer to data buffer
 * @param len  Length of data in bytes
 * @return CRC32 checksum value
 * @note Uses STM32 hardware CRC (polynomial 0x04C11DB7, init 0xFFFFFFFF)
 */
uint32_t bsp_crc32(const void *data, uint32_t len);

/**
 * @brief Reset CRC calculation unit
 */
void bsp_crc_reset(void);

/* ===================================================================== */
/*                          RTC FUNCTIONS                                */
/* ===================================================================== */

/**
 * @brief Set RTC date and time
 * @param time Pointer to time structure
 * @return BSP_UTIL_OK on success, BSP_UTIL_ERR on failure
 */
bsp_util_status_t bsp_rtc_set_time(const bsp_rtc_time_t *time);

/**
 * @brief Get current RTC date and time
 * @param time Pointer to time structure to store result
 * @return BSP_UTIL_OK on success, BSP_UTIL_ERR on failure
 */
bsp_util_status_t bsp_rtc_get_time(bsp_rtc_time_t *time);

/**
 * @brief Get RTC timestamp as Unix epoch time
 * @return Unix timestamp (seconds since 1970-01-01 00:00:00 UTC)
 */
uint32_t bsp_rtc_get_timestamp(void);

/* ===================================================================== */
/*                         DELAY FUNCTIONS                               */
/* ===================================================================== */

/**
 * @brief Delay for specified microseconds
 * @param us Microseconds to delay
 * @note Uses TIM9 for accurate microsecond timing
 */
void bsp_delay_us(uint32_t us);

/**
 * @brief Delay for specified milliseconds
 * @param ms Milliseconds to delay
 */
void bsp_delay_ms(uint32_t ms);

/**
 * @brief Get compact serial number derived from chip unique ID.
 * @param serial_number Output serial number (32-bit)
 * @return BSP_UTIL_OK on success
 */
bsp_util_status_t bsp_util_get_serial_number(uint32_t *serial_number);

#ifdef __cplusplus
}
#endif

#endif /* __BSP_UTIL_H */

/* End of file -------------------------------------------------------- */
