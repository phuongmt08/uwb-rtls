/**
 * @file       serial.c
 * @copyright
 * @license
 * @version    1.0.0
 * @date       2025
 * @author
 * @brief      Serial middleware implementation - pure function pointer registry
 * @note       No hardware dependencies - handlers registered externally
 */
/* Public includes ---------------------------------------------------------- */
#include "serial.h"
#include "cbuffer.h"
#include <string.h>

/* Private includes --------------------------------------------------------- */
/* Private defines ---------------------------------------------------------- */
#ifndef SERIAL_USB_RX_BUF_SIZE
#define SERIAL_USB_RX_BUF_SIZE 2048
#endif
#ifndef SERIAL_BLE_RX_BUF_SIZE
#define SERIAL_BLE_RX_BUF_SIZE 512
#endif
/* Private enumerate/structure ---------------------------------------------- */
/* Private macros ----------------------------------------------------------- */
#define CHECK(cond, ret) if (!(cond)) return (ret)

/* Public variables --------------------------------------------------------- */
/* Private variables -------------------------------------------------------- */
static serial_func_t stream[STREAM_MAX];

// RX FIFOs
static uint8_t   s_usb_rx_buf[SERIAL_USB_RX_BUF_SIZE];
static cbuffer_t s_usb_rx_cb;
static uint8_t   s_ble_rx_buf[SERIAL_BLE_RX_BUF_SIZE];
static cbuffer_t s_ble_rx_cb;

// Raw TX handlers (registered externally)
static serial_func_t usb_tx_handler = NULL;
static serial_func_t ble_tx_handler = NULL;

/* Private prototypes ------------------------------------------------------- */
static int usb_read_wrapper(int file, char *ptr, int len, uint8_t type);
static int usb_write_wrapper(int file, char *ptr, int len, uint8_t type);
static int ble_read_wrapper(int file, char *ptr, int len, uint8_t type);
static int ble_write_wrapper(int file, char *ptr, int len, uint8_t type);

/* Public implementations --------------------------------------------------- */
void serial_init(void)
{
    // Clear all stream handlers
    for (int i = 0; i < STREAM_MAX; i++)
    {
        stream[i] = NULL;
    }
    
    // Initialize FIFOs
    cb_init(&s_usb_rx_cb, s_usb_rx_buf, sizeof(s_usb_rx_buf));
    cb_init(&s_ble_rx_cb, s_ble_rx_buf, sizeof(s_ble_rx_buf));
    
    // Register internal wrappers
    stream[STREAM_USB_RX] = usb_read_wrapper;
    stream[STREAM_USB_TX] = usb_write_wrapper;
    stream[STREAM_BLE_RX] = ble_read_wrapper;
    stream[STREAM_BLE_TX] = ble_write_wrapper;
}

void serial_register_tx_handler(stream_type_t stream_id, serial_func_t func)
{
    if (stream_id == STREAM_USB_TX)
        usb_tx_handler = func;
    else if (stream_id == STREAM_BLE_TX)
        ble_tx_handler = func;
}

void serial_usb_rx_push(const uint8_t *data, uint32_t len)
{
    if (!data || len == 0) return;
    (void)cb_write(&s_usb_rx_cb, (void*)data, len);
}

void serial_ble_rx_push(const uint8_t *data, uint32_t len)
{
    if (!data || len == 0) return;
    (void)cb_write(&s_ble_rx_cb, (void*)data, len);
}

/**
 * @brief Read a character from a stream
 * @note Blocks until the number of characters have been read or FIFO is empty
 * @return Number of bytes read or -1 on error
 */
int _read(int file, char *ptr, int maxlen, uint8_t type)
{
    CHECK(file >= 0 && file < STREAM_MAX, -1);
    CHECK(stream[file] != NULL, -1);
    
    return stream[file](file, ptr, maxlen, type);
}

/**
 * @brief Write data to a stream
 * @return -1 on error or number of bytes sent
 */
int _write(int file, char *ptr, int len, uint8_t type)
{
    CHECK(file >= 0 && file < STREAM_MAX, -1);
    CHECK(stream[file] != NULL, -1);
    
    return stream[file](file, ptr, len, type);
}

/* Private implementations -------------------------------------------------- */
static int usb_read_wrapper(int file, char *ptr, int len, uint8_t type)
{
    (void)file; (void)type;
    if (!ptr || len <= 0) return -1;
    uint32_t n = cb_read(&s_usb_rx_cb, (uint8_t*)ptr, (uint32_t)len);
    return (int)n;
}

static int usb_write_wrapper(int file, char *ptr, int len, uint8_t type)
{
    if (!usb_tx_handler) return -1;
    return usb_tx_handler(file, ptr, len, type);
}

static int ble_read_wrapper(int file, char *ptr, int len, uint8_t type)
{
    (void)file; (void)type;
    if (!ptr || len <= 0) return -1;
    uint32_t n = cb_read(&s_ble_rx_cb, (uint8_t*)ptr, (uint32_t)len);
    return (int)n;
}

static int ble_write_wrapper(int file, char *ptr, int len, uint8_t type)
{
    if (!ble_tx_handler) return -1;
    return ble_tx_handler(file, ptr, len, type);
}

/* End of file -------------------------------------------------------------- */
