/**
 * @file       serial.h
 * @copyright
 * @license
 * @version    1.0.0
 * @date       2025
 * @author
 * @brief      Serial middleware - function pointer abstraction for streams
 * @note       Pure middleware - no hardware dependencies
 */
/* Define to prevent recursive inclusion ------------------------------------ */
#ifndef __SERIAL_H
#define __SERIAL_H
/* Includes ----------------------------------------------------------------- */
#include <stdint.h>

/* Public defines ----------------------------------------------------------- */
typedef enum
{
    STREAM_USB_RX = 0,
    STREAM_USB_TX,
    STREAM_BLE_RX,
    STREAM_BLE_TX,
    STREAM_MAX
} stream_type_t;

/**
 * @brief Serial function pointer type
 * @param file Stream identifier
 * @param ptr Data buffer
 * @param len Length of data
 * @param type Optional type parameter
 * @return Number of bytes processed or -1 on error
 */
typedef int (*serial_func_t)(int file, char *ptr, int len, uint8_t type);

/* Public macros ------------------------------------------------------------ */
/* Public variables --------------------------------------------------------- */
/* Public APIs -------------------------------------------------------------- */
/**
 * @brief Initialize serial middleware
 */
void serial_init(void);

/**
 * @brief Register TX handler (RX handled by serial with FIFO)
 * @param stream Stream identifier (STREAM_USB_TX or STREAM_BLE_TX)
 * @param func Function pointer for transmit
 */
void serial_register_tx_handler(stream_type_t stream, serial_func_t func);

/**
 * @brief Push USB RX data into FIFO
 */
void serial_usb_rx_push(const uint8_t *data, uint32_t len);

/**
 * @brief Push BLE RX data into FIFO
 */
void serial_ble_rx_push(const uint8_t *data, uint32_t len);

/**
 * @brief Read data from a stream
 * @param file Stream identifier
 * @param ptr Buffer to store read data
 * @param maxlen Maximum bytes to read
 * @param type Optional type parameter (reserved)
 * @return Number of bytes read or -1 on error
 */
int _read(int file, char *ptr, int maxlen, uint8_t type);

/**
 * @brief Write data to a stream
 * @param file Stream identifier
 * @param ptr Buffer containing data to write
 * @param len Number of bytes to write
 * @param type Optional type parameter (reserved)
 * @return Number of bytes written or -1 on error
 */
int _write(int file, char *ptr, int len, uint8_t type);

/* -------------------------------------------------------------------------- */
#endif /* __SERIAL_H */

/* End of file -------------------------------------------------------------- */
