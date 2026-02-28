/**
 * @file       bootloader.c
 * @brief      Minimal USB DFU bootloader for STM32F411CEU6
 */

#include "bootloader.h"
#include "stm32f4xx_hal.h"
#include "usb_device.h"
#include "usbd_core.h"   // USBD_DeInit, USBD_Stop

extern USBD_HandleTypeDef hUsbDeviceFS;

bool bl_app_vector_valid(void)
{
  const uint32_t msp = *(uint32_t*)MEM_APP_START;
  const uint32_t reset_handler = *(uint32_t*)(MEM_APP_START + 4U);

  if (msp < SRAM_BASE_ADDR || msp > SRAM_END_ADDR) {
    return false;
  }

  if (reset_handler < MEM_APP_START || reset_handler >= MEM_APP_END) {
    return false;
  }

  return true;
}

bool bl_should_enter_dfu(void)
{
    bool req = (*(volatile uint32_t*)BL_MAGIC_ADDR == BL_MAGIC_VALUE);
    *(volatile uint32_t*)BL_MAGIC_ADDR = 0;   // clear to avoid sticky DFU
    return req;
}
void bl_jump_to_app(void)
{

  HAL_DeInit();
  HAL_RCC_DeInit();

  __set_MSP(*(uint32_t*)MEM_APP_START);

  ((void (*)(void))(*(uint32_t*)(MEM_APP_START + 4U)))();
}
