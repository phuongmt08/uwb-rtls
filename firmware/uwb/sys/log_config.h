/**
 * @file       log_config.h
 * @copyright
 * @license
 * @version    1.0.0
 * @date       2025
 * @author
 * @brief      Log configuration and error definitions for UWB positioning system
 */
/* Define to prevent recursive inclusion ------------------------------------ */
#ifndef __LOG_CONFIG_H
#define __LOG_CONFIG_H

/* Includes ----------------------------------------------------------------- */
#include <stdint.h>

/* Public defines ----------------------------------------------------------- */
/**
 * @brief Log header structure indices
 */
#define LOG_HEADER_IDX_LOG_TYPE  (0)
#define LOG_HEADER_IDX_OBJ_CODE  (1)
#define LOG_HEADER_IDX_TIMESTAMP (2)
#define LOG_HEADER_IDX_DATA_LEN  (8)
#define LOG_HEADER_IDX_DATA      (9)
#define LOG_HEADER_LEN           (9)

/**
 * @brief Error ID is a 16-bit integer formed by
 * Object Code and Error Code: 0x<ObjectCode><ErrorCode>
 *
 * <ObjectCode>:
 * 		-Bit [7]: Error source: 0=Anchor, 1=Tag
 * 		-[6:0]:
 * 			+ 0x00 to 0x7E: ID of the component that causes error
 * 			+ 0x7F is used for special records
 * 				+ 0x7F00 (or 0xFF00 in Tag): Debug Message
 * 				+ 0x7F01 (or 0xFF01 in Tag): Timestamp Mark
 */

/**
 * @brief Error source identifiers
 */
#define LOG_ERR_SOURCE_ANCHOR (0x00)
#define LOG_ERR_SOURCE_TAG    (0x80)

/**
 * @brief Special Object Code & Error Code
 */
#define LOG_OBJECT_CODE_SPECIAL             (0x7F)
#define LOG_ERR_CODE_SPECIAL_DEBUG_MESSAGE  (0x00)
#define LOG_ERR_CODE_SPECIAL_TIMESTAMP_MARK (0x01)

/* Public enumerate/structure ----------------------------------------------- */
/**
 * @brief Object code enumeration for UWB system components
 */
typedef enum {
	LOG_OBJECT_CODE_BOOTLOADER = 0x00,
	LOG_OBJECT_CODE_APPLICATION = 0x01,
	LOG_OBJECT_CODE_NETWORK = 0x02,
	LOG_OBJECT_CODE_UWB_DRIVER = 0x03,
	LOG_OBJECT_CODE_RANGING = 0x04,
	LOG_OBJECT_CODE_POSITIONING = 0x05,
	LOG_OBJECT_CODE_SERIAL = 0x06,
	LOG_OBJECT_CODE_IO = 0x07,
	LOG_OBJECT_CODE_IMU = 0x08,
	LOG_OBJECT_CODE_BLE = 0x09,
	LOG_OBJECT_CODE_FLASH = 0x0D,
	LOG_OBJECT_CODE_TASK = 0x0F,
	LOG_OBJECT_CODE_ANCHOR = 0x10,
	LOG_OBJECT_CODE_TAG = 0x11,
	LOG_OBJECT_CODE_GATEWAY = 0x12,
	LOG_OBJECT_CODE_PM = 0x13,
	LOG_OBJECT_CODE_FUSION = 0x14,
    LOG_OBJECT_CODE_SYS_CFG = 0x15,
	LOG_OBJECT_CODE_BATTERY = 0x16,
    LOG_OBJECT_CODE_MAX = 0x17
} log_object_code_t;

/**
 * @brief Log level enumeration
 */
typedef enum
{
	LOG_LEVEL_INFO,
	LOG_LEVEL_DEBUG,
	LOG_LEVEL_WARN,
	LOG_LEVEL_ERR,
	LOG_LEVEL_MAX
} log_level_t;

/* Public error codes ------------------------------------------------------- */
/**
 * @brief Common error codes (0x00-0x0F)
 */
#define ERR_UNDEFINED          (0x00)  // Undefined error
#define WARNING_LOG            (0xFD)  // Warning log
#define INFO_LOG               (0xFE)  // Info log
#define DEBUG_LOG              (0xFF)  // Debug log

/**
 * @brief Bootloader and Application error codes (0x01-0x09)
 */
#define ERR_BAD_MAGIC          (0x01)  // Invalid magic number in firmware
#define ERR_WRONG_DEVICE       (0x02)  // Device address mismatch
#define ERR_WRONG_HARDWARE     (0x03)  // Hardware version mismatch
#define ERR_BAD_VALIDITY_MAKER (0x04)  // Invalid validation stamp
#define ERR_IMAGE_TOO_BIG      (0x05)  // Firmware image too large
#define ERR_IMAGE_BAD_CRC      (0x06)  // CRC check failed
#define ERR_INIT_FAILED        (0x07)  // Initialization failed
#define ERR_CONFIG_INVALID     (0x08)  // Invalid configuration
#define ERR_TIMEOUT            (0x09)  // Operation timeout

/**
 * @brief Network/Communication error codes (0x10-0x1F)
 */
#define ERR_ACK_TIMEOUT        (0x10)  // ACK timeout
#define ERR_PACKET_TIMEOUT     (0x11)  // Packet timeout
#define ERR_INVALID_PACKET_LEN (0x12)  // Invalid packet length
#define ERR_PACKET_BAD_CRC     (0x13)  // Packet CRC error
#define ERR_NO_CONNECTION      (0x14)  // No connection established
#define ERR_TX_FAILED          (0x15)  // Transmission failed
#define ERR_RX_FAILED          (0x16)  // Reception failed

/**
 * @brief Hardware/Driver error codes (0x20-0x2F)
 */
#define ERR_INVALID_PARAM      (0x20)  // Invalid parameter
#define ERR_READ               (0x21)  // Read operation failed
#define ERR_WRITE              (0x22)  // Write operation failed
#define ERR_POST               (0x23)  // Power-On Self Test failed
#define ERR_BIST               (0x24)  // Built-In Self Test failed
#define ERR_NOT_READY          (0x25)  // Device not ready
#define ERR_BUSY               (0x26)  // Device busy
#define ERR_OVERFLOW           (0x27)  // Buffer overflow
#define ERR_UNDERFLOW          (0x28)  // Buffer underflow
#define ERR_HAL                (0x29)  // HAL driver error
#define ERR_NOT_INIT           (0x2A)  // Component not initialized
#define ERR_CRC                (0x2B)  // CRC mismatch error

/**
 * @brief UWB specific error codes (0x30-0x3F)
 */
#define ERR_UWB_INIT           (0x30)  // UWB initialization failed
#define ERR_UWB_CONFIG         (0x31)  // UWB configuration error
#define ERR_UWB_TX             (0x32)  // UWB transmission error
#define ERR_UWB_RX             (0x33)  // UWB reception error
#define ERR_UWB_RANGING        (0x34)  // Ranging calculation error
#define ERR_UWB_TIMESTAMP      (0x35)  // Timestamp error
#define ERR_UWB_CALIBRATION    (0x36)  // Calibration error
#define ERR_UWB_ANTENNA        (0x37)  // Antenna delay error

/**
 * @brief Positioning error codes (0x40-0x4F)
 */
#define ERR_POS_NO_ANCHORS     (0x40)  // Insufficient anchors
#define ERR_POS_CALCULATION    (0x41)  // Position calculation failed
#define ERR_POS_OUT_OF_RANGE   (0x42)  // Position out of valid range
#define ERR_POS_INVALID_DATA   (0x43)  // Invalid positioning data

/**
 * @brief Storage error codes (0x50-0x5F)
 */
#define ERR_FLASH_PROGRAM      (0x50)  // Flash programming error
#define ERR_FLASH_ERASE        (0x51)  // Flash erase error
#define ERR_FLASH_VERIFY       (0x52)  // Flash verify error

/**
 * @brief Battery error codes (0x60-0x6F)
 */
#define ERR_BATTERY_INIT       (0x60)  // Battery initialization failed
#define ERR_BATTERY_I2C        (0x61)  // I2C communication error
#define ERR_BATTERY_READ       (0x62)  // Battery data read failed
#define ERR_BATTERY_CRITICAL   (0x63)  // Battery critically low
#define ERR_BATTERY_LOW        (0x64)  // Battery low voltage
#define ERR_BATTERY_OVERVOLT   (0x65)  // Battery overvoltage
#define ERR_BATTERY_OVERCHARGE_RATE (0x66)  // Overcharge rate
#define ERR_BATTERY_OVERDISCHARGE_RATE (0x67)  // Overdischarge rate
#define ERR_BATTERY_SLOW_CHARGE (0x68)  // Slow charge

/* Public macros ------------------------------------------------------------ */
/* Public variables --------------------------------------------------------- */
/* -------------------------------------------------------------------------- */

#endif /* __LOG_CONFIG_H */

/* End of file -------------------------------------------------------------- */
