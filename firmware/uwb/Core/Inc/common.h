/*
 * common.h
 *
 */

#ifndef INC_COMMON_H_
#define INC_COMMON_H_

#include <stdint.h>
#include "stm32f4xx_hal.h"

typedef enum
{
  BSP_OK = 0,
  BSP_ERR,
  BSP_ERR_PARAM,
  BSP_ERR_BUSY,
  BSP_ERR_TIMEOUT,
} bsp_err_t;
typedef enum
{
  APP_OK = 0,
  APP_ERR = -1
} app_err_t;
#define UWB_RST_PIN GPIO_PIN_2
#define UWB_RST_PORT GPIOB

#define UWB_CS_PIN GPIO_PIN_12
#define UWB_CS_PORT GPIOB

#define SPI_SCK_PIN GPIO_PIN_5
#define SPI_SCK_PORT GPIOA
#define SPI_MISO_PIN GPIO_PIN_6
#define SPI_MISO_PORT GPIOA
#define SPI_MOSI_PIN GPIOPIN_7
#define SPI_MOSI_PORT GPIOA


#endif /* INC_COMMON_H_ */
