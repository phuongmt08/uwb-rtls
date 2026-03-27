/**
 * @file usb_cdc_acm.h
 * @brief USB CDC ACM (Communications Device Class - Abstract Control Model) handler
 * 
 * This module handles USB CDC ACM initialization and provides serial communication
 * over USB. It integrates with the nRF5 SDK USBD library.
 */

#ifndef USB_CDC_ACM_H
#define USB_CDC_ACM_H

#include <stdint.h>
#include <stdbool.h>
#include "nrf_drv_usbd.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Initialize USB CDC ACM device
 * 
 * Sets up the USB peripheral, initializes CDC ACM class, and enables USB power events.
 * 
 * @return ret_code_t NRF_SUCCESS if initialization was successful
 */
ret_code_t usb_cdc_acm_init(void);

/**
 * @brief Put a character to USB CDC ACM
 * 
 * Sends a single character over USB CDC ACM.
 * 
 * @param[in] character Character to send
 * @return ret_code_t NRF_SUCCESS if character was queued successfully
 */
ret_code_t usb_cdc_acm_putchar(char character);

/**
 * @brief Get a character from USB CDC ACM
 * 
 * Receives a single character from USB CDC ACM if available.
 * 
 * @param[out] character Pointer to store received character
 * @return ret_code_t NRF_SUCCESS if character was received, NRF_ERROR_NOT_FOUND if no data available
 */
ret_code_t usb_cdc_acm_getchar(char *character);

/**
 * @brief Put a string to USB CDC ACM
 * 
 * Sends a string over USB CDC ACM.
 * 
 * @param[in] data Pointer to data buffer
 * @param[in] length Number of bytes to send
 * @return ret_code_t NRF_SUCCESS if data was queued successfully
 */
ret_code_t usb_cdc_acm_write(const uint8_t *data, size_t length);

/**
 * @brief Check if USB CDC ACM is connected
 * 
 * @return bool true if device is connected, false otherwise
 */
bool usb_cdc_acm_is_connected(void);

/**
 * @brief Process USB events
 * 
 * This function should be called periodically in the main loop to process USB events.
 * 
 * @return bool true if events were processed, false if queue is empty
 */
bool usb_cdc_acm_process(void);

#ifdef __cplusplus
}
#endif

#endif // USB_CDC_ACM_H
