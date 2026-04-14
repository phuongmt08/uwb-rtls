/**
 * @file    bsp_usbd.h
 * @brief   BSP layer for USB CDC ACM (Communications Device Class).
 *
 * This module handles USB CDC ACM initialization and provides serial
 * communication over USB. It integrates with the nRF5 SDK USBD library.
 *
 * @note    This is the BSP-level wrapper that replaces the standalone
 *          usb_cdc_acm module. Include this file instead of usb_cdc_acm.h.
 */

#ifndef BSP_USBD_H
#define BSP_USBD_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include "sdk_errors.h"

#ifdef __cplusplus
extern "C" {
#endif

/* -------------------------------------------------------------------------
 * Public API
 * ---------------------------------------------------------------------- */

/**
 * @brief Initialize USB CDC ACM peripheral.
 *
 * Sets up the USB stack, registers the CDC ACM class instance, and enables
 * power-event-driven start/stop. Must be called after app_timer_init().
 *
 * @return NRF_SUCCESS or a propagated SDK error code.
 */
ret_code_t bsp_usbd_init(void);

typedef void (*bsp_usbd_rx_line_cb_t)(const char *line);

/**
 * @brief Set the callback for complete line reception (ending with \r or \n).
 * 
 * @param cb The callback function.
 */
void bsp_usbd_rx_line_cb_set(bsp_usbd_rx_line_cb_t cb);

/**
 * @brief Process pending USB events.
 *
 * Call this in the main loop to drain the USB event queue.
 *
 * @return true  if at least one event was processed.
 * @return false if the queue was empty.
 */
bool bsp_usbd_process(void);

/**
 * @brief Check whether the host has the COM port open.
 *
 * @return true  if USB is connected AND the port is open.
 * @return false otherwise.
 */
bool bsp_usbd_is_connected(void);

/**
 * @brief Write a buffer to the USB CDC ACM TX endpoint.
 *
 * @param[in] p_data   Pointer to the data buffer.
 * @param[in] length   Number of bytes to send.
 *
 * @return NRF_SUCCESS          Data accepted by the driver.
 * @return NRF_ERROR_BUSY       Previous transfer still in progress.
 * @return NRF_ERROR_INVALID_STATE  Port is not open.
 */
ret_code_t bsp_usbd_write(const uint8_t *p_data, size_t length);

/**
 * @brief Send a single character over USB CDC ACM.
 *
 * @param[in] character  Byte to transmit.
 *
 * @return NRF_SUCCESS or an error code (see bsp_usbd_write).
 */
ret_code_t bsp_usbd_putchar(char character);

/**
 * @brief Read a single character from the USB CDC ACM RX buffer.
 *
 * @param[out] p_character  Stores the received byte.
 *
 * @return NRF_SUCCESS          A byte was read.
 * @return NRF_ERROR_NOT_FOUND  No data available.
 * @return NRF_ERROR_INVALID_STATE  Port is not open or p_character is NULL.
 */
ret_code_t bsp_usbd_getchar(char *p_character);

#ifdef __cplusplus
}
#endif

#endif /* BSP_USBD_H */
