/**
 * @file       bsp_delay.h
 * @license    This project is for academic and educational purposes under the ITR Internship Program.
 * @version    1.0.0
 * @date       2025-08-05
 * @author      Chinh Nguyen
 * @author      Phuong Mai
 *
 * @brief      Delay functionality header file
 * @note       This file provides the interface for the delay functionality.
 */

/* Define to prevent recursive inclusion ------------------------------ */
#ifndef INC_BSP_DELAY_H_
#define INC_BSP_DELAY_H_

/* Includes ----------------------------------------------------------- */
#include <stdint.h>
typedef enum
{
  DELAY_OK = 0,
  DELAY_ERR
} delay_err_t;

delay_err_t bsp_delay_init(void);
void        bsp_delay_us(uint32_t us);
void        bsp_delay(uint32_t ms);

#endif /* INC_BSP_DELAY_H_ */

/* End of file -------------------------------------------------------- */
