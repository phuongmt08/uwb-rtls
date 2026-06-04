/*
 * common.h
 *
 */

#ifndef INC_COMMON_H_
#define INC_COMMON_H_

#include <stdint.h>
#include "config.h"
#include "stm32f4xx_hal.h"
#ifdef DEVELOPER_MODE
#include "SEGGER_SYSVIEW.h"
#endif

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

#define SYSVIEW_MARK_UWB_ISR_DISPATCH       10U
#define SYSVIEW_MARK_FUSION_PREDICT         20U
#define SYSVIEW_MARK_FUSION_TRILATERATION   21U
#define SYSVIEW_MARK_FUSION_UKF_UPDATE      22U
#define SYSVIEW_MARKERS_DESC "Markers: 10=UWB_ISR_DISPATCH 20=FUSION_PREDICT 21=FUSION_TRILATERATION 22=FUSION_UKF_UPDATE"

#ifdef DEVELOPER_MODE
#define SYSVIEW_INIT()    SEGGER_SYSVIEW_Conf()
#define SYSVIEW_RECORD_START() \
  do {                         \
    SEGGER_SYSVIEW_Start();    \
  } while (0)
#define SYSVIEW_START(id)  SEGGER_SYSVIEW_MarkStart((id))
#define SYSVIEW_STOP(id)   SEGGER_SYSVIEW_MarkStop((id))
#define SYSVIEW_PRINTF(s)  SEGGER_SYSVIEW_PrintfTarget((s))
#else
#define SYSVIEW_INIT()    ((void)0)
#define SYSVIEW_RECORD_START() ((void)0)
#define SYSVIEW_START(id)  ((void)0)
#define SYSVIEW_STOP(id)   ((void)0)
#define SYSVIEW_PRINTF(s)  ((void)0)
#endif

#endif /* INC_COMMON_H_ */
