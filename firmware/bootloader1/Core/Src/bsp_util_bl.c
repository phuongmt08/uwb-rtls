/**
 * @file    bsp_util_bl.c
 * @brief
 */

#include "bsp_util_bl.h"
#include "stm32f4xx_hal.h"

/* STM32F411 Unique Device ID word 0 (UID[31:0]) */
#define STM32_UID_WORD0  (*(volatile uint32_t *)0x1FFF7A10UL)

bsp_util_status_t bsp_util_device_reset(void)
{
    NVIC_SystemReset();
    /* Never reached */
    return BSP_UTIL_OK;
}

uint32_t bsp_util_get_serial_number(void)
{
    return STM32_UID_WORD0;
}
