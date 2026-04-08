/**
 * @file       bsp_uart.h
 * @copyright  [Your Copyright]
 * @license    [Your License]
 * @version    1.0.0
 * @date       2026-04-08
 * @author     Dong Son
 *
 * @brief      
 */
/* Define to prevent recursive inclusion ------------------------------ */
#ifndef BSP_UART_H
#define BSP_UART_H

/* Includes ----------------------------------------------------------- */
#include <stdint.h>
#include <stdbool.h>

/* Public defines ----------------------------------------------------- */

/* Public enumerate/structure ----------------------------------------- */
/* Public macros ------------------------------------------------------ */
/* Public variables --------------------------------------------------- */
/* Public function prototypes ----------------------------------------- */
uint32_t bsp_uart_init(void);

uint32_t bsp_uart_send_data(const uint8_t *p_data, uint16_t size);

void bsp_uart_receive_enable(uint8_t * p_buffer, char eof);

void bsp_uart_callback(uint16_t size);

#endif // BSP_UART_H
