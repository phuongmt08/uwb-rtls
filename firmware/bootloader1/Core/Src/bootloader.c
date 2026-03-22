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
  const uint32_t app_hdr_magic = *(uint32_t *)(MEM_APP_HEADER_ADDR + 0U);
  const uint32_t app_hdr_ver   = *(uint32_t *)(MEM_APP_HEADER_ADDR + 4U);
  const uint32_t app_hdr_size  = *(uint32_t *)(MEM_APP_HEADER_ADDR + 8U);
  const uint32_t msp = *(uint32_t*)MEM_APP_START;
  const uint32_t reset_handler = *(uint32_t*)(MEM_APP_START + 4U);

  if (app_hdr_magic != APP_IMAGE_HEADER_MAGIC) {
    return false;
  }

  if (app_hdr_ver != APP_IMAGE_HEADER_VERSION ||
      app_hdr_size == 0U ||
      app_hdr_size > MEM_APP_HEADER_SIZE) {
    return false;
  }

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
  __disable_irq();

  SysTick->CTRL = 0;
  SysTick->LOAD = 0;
  SysTick->VAL  = 0;

  USBD_Stop(&hUsbDeviceFS);
  USBD_DeInit(&hUsbDeviceFS);

  HAL_DeInit();
  HAL_RCC_DeInit();

  for (uint32_t i = 0; i < 8; i++) {
    NVIC->ICER[i] = 0xFFFFFFFFU;
    NVIC->ICPR[i] = 0xFFFFFFFFU;
  }

  SCB->VTOR = MEM_APP_START;
  __DSB();
  __ISB();

  __set_MSP(*(uint32_t*)MEM_APP_START);

  ((void (*)(void))(*(uint32_t*)(MEM_APP_START + 4U)))();
}
