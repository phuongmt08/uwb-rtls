/**
 * @file       otp.h
 * @brief      One-Time Programmable (OTP) Memory Driver with Flash Mocking
 * @details    Provides a TLV-based append-only key-value storage over STM32F4
 *             OTP memory (or a simulated Flash memory block for development).
 */

#ifndef __OTP_H__
#define __OTP_H__

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stdbool.h>

/* --- OTP Configurations --- */
#define REAL_OTP_BASE_ADDR   0x1FFF7800U  /* STM32F411 OTP Base Address */
#define REAL_OTP_CELLS       64U          /* 512 bytes / 8 bytes per cell */
#define REAL_OTP_CELL_SIZE   8U           /* 8-byte alignment (Double-Word) */

/* Mock Configuration for Testing */
#define MOCK_OTP_IN_FLASH          0      /* Set to 1 to mock OTP in Sector 5 */
#define OTP_ENABLE_FLASH_SELF_TEST 0      /* Set to 1 to run destructive mock-flash self-test */
#define MOCK_OTP_BASE_ADDR         0x0803FE00U  /* Last 512 bytes of Sector 5 scratch area */

#if MOCK_OTP_IN_FLASH
  #define OTP_BASE_ADDR      MOCK_OTP_BASE_ADDR
  #define OTP_CELLS          REAL_OTP_CELLS
  #define OTP_CELL_SIZE      REAL_OTP_CELL_SIZE
#else
  #define OTP_BASE_ADDR      REAL_OTP_BASE_ADDR
  #define OTP_CELLS          REAL_OTP_CELLS
  #define OTP_CELL_SIZE      REAL_OTP_CELL_SIZE
#endif

/* --- TLV Header Structure Specifications --- */
#define OTP_MAP_VERSION      0x10         /* Written last; marks a complete TLV record */
#define OTP_HEADER_LEN       3u           /* 3 bytes: Version (1B), Type (1B), Length (1B) */
#define OTP_IDX_VERSION      0u           /* Byte index of Version */
#define OTP_IDX_TYPE         1u           /* Byte index of Type */
#define OTP_IDX_LEN          2u           /* Byte index of Length */

/* --- Error Codes --- */
typedef enum {
    OTP_OK                 =  0,
    OTP_ERR_FULL           = -1,          /* Storage is full */
    OTP_ERR_NOT_FOUND      = -2,          /* Parameter not found */
    OTP_ERR_MAP_VERSION    = -3,          /* Version field mismatch */
    OTP_ERR_FLASH_ACCESS   = -4,          /* HAL program failure */
    OTP_ERR_INVALID_ARG    = -5,          /* Invalid arguments passed */
} otp_err_t;

typedef enum {
    OTP_TYPE_DEVICE_INFO    = 0x01,       /* 5 bytes: device_type (1B) + mfg_date (3B) + hw_rev (1B) */
    OTP_TYPE_ANTENNA_DELAY  = 0x02,       /* 4 bytes: tx uint16_t + rx uint16_t */
} otp_param_type_t;

/* --- API Functions --- */

/**
 * @brief  Initialize the OTP system by finding the first unused memory cell.
 */
void otp_init(void);

/**
 * @brief  Retrieve the latest value of a parameter from OTP.
 * @param  type     The TLV parameter type (otp_param_type_t)
 * @param  value    Pointer to the buffer where the value will be copied
 * @param  value_capacity Size of the output buffer in bytes
 * @param  length   Pointer to store the returned data length in bytes
 * @return OTP_OK on success, error code otherwise.
 */
otp_err_t otp_get(uint8_t type, void *value, uint8_t value_capacity, uint8_t *length);

/**
 * @brief  Append a new parameter value to OTP.
 * @param  type     The TLV parameter type (otp_param_type_t)
 * @param  length   The length of the data to write in bytes
 * @param  value    Pointer to the buffer containing the data to write
 * @return OTP_OK on success, error code otherwise.
 */
otp_err_t otp_set(uint8_t type, uint8_t length, const void *value);

/**
 * @brief  Erase the mock Flash sector to reset all mock OTP data.
 *         Only active and permitted when MOCK_OTP_IN_FLASH is enabled.
 * @return OTP_OK on success, error code otherwise.
 */
otp_err_t otp_debug_reset_mock(void);

/**
 * @brief  Automatic test suite to verify OTP write, update, and search functionality.
 */
void otp_test_run(void);

#ifdef __cplusplus
}
#endif

#endif /* __OTP_H__ */
