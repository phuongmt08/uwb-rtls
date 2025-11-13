/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : usbd_dfu_if.c
  * @brief          : Usb device for Download Firmware Update.
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2025 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */

/* Includes ------------------------------------------------------------------*/
#include "usbd_dfu_if.h"

/* USER CODE BEGIN INCLUDE */
#include "bootloader.h"
/* USER CODE END INCLUDE */

/* Private typedef -----------------------------------------------------------*/
/* Private define ------------------------------------------------------------*/
/* Private macro -------------------------------------------------------------*/

/* USER CODE BEGIN PV */
/* Private variables ---------------------------------------------------------*/
volatile uint8_t g_dfu_host_active = 0;
/* USER CODE END PV */

/** @addtogroup STM32_USB_OTG_DEVICE_LIBRARY
  * @brief Usb device.
  * @{
  */

/** @defgroup USBD_DFU
  * @brief Usb DFU device module.
  * @{
  */

/** @defgroup USBD_DFU_Private_TypesDefinitions
  * @brief Private types.
  * @{
  */

/* USER CODE BEGIN PRIVATE_TYPES */

/* USER CODE END PRIVATE_TYPES */

/**
  * @}
  */

/** @defgroup USBD_DFU_Private_Defines
  * @brief Private defines.
  * @{
  */

#define FLASH_DESC_STR      "@Internal Flash   /0x08000000/03*016Ka,01*016Kg,01*064Kg,07*128Kg,04*016Kg,01*064Kg,07*128Kg"

/* USER CODE BEGIN PRIVATE_DEFINES */

/* USER CODE END PRIVATE_DEFINES */

/**
  * @}
  */

/** @defgroup USBD_DFU_Private_Macros
  * @brief Private macros.
  * @{
  */

/* USER CODE BEGIN PRIVATE_MACRO */

/* USER CODE END PRIVATE_MACRO */

/**
  * @}
  */

/** @defgroup USBD_DFU_Private_Variables
  * @brief Private variables.
  * @{
  */

/* USER CODE BEGIN PRIVATE_VARIABLES */

/* USER CODE END PRIVATE_VARIABLES */

/**
  * @}
  */

/** @defgroup USBD_DFU_Exported_Variables
  * @brief Public variables.
  * @{
  */

extern USBD_HandleTypeDef hUsbDeviceFS;

/* USER CODE BEGIN EXPORTED_VARIABLES */

/* USER CODE END EXPORTED_VARIABLES */

/**
  * @}
  */

/** @defgroup USBD_DFU_Private_FunctionPrototypes
  * @brief Private functions declaration.
  * @{
  */

static uint16_t MEM_If_Init_FS(void);
static uint16_t MEM_If_Erase_FS(uint32_t Add);
static uint16_t MEM_If_Write_FS(uint8_t *src, uint8_t *dest, uint32_t Len);
static uint8_t *MEM_If_Read_FS(uint8_t *src, uint8_t *dest, uint32_t Len);
static uint16_t MEM_If_DeInit_FS(void);
static uint16_t MEM_If_GetStatus_FS(uint32_t Add, uint8_t Cmd, uint8_t *buffer);

/* USER CODE BEGIN PRIVATE_FUNCTIONS_DECLARATION */

/* USER CODE END PRIVATE_FUNCTIONS_DECLARATION */

/**
  * @}
  */

#if defined ( __ICCARM__ ) /* IAR Compiler */
  #pragma data_alignment=4
#endif
__ALIGN_BEGIN USBD_DFU_MediaTypeDef USBD_DFU_fops_FS __ALIGN_END =
{
   (uint8_t*)FLASH_DESC_STR,
    MEM_If_Init_FS,
    MEM_If_DeInit_FS,
    MEM_If_Erase_FS,
    MEM_If_Write_FS,
    MEM_If_Read_FS,
    MEM_If_GetStatus_FS
};

/* Private functions ---------------------------------------------------------*/
/**
  * @brief  Memory initialization routine.
  * @retval USBD_OK if operation is successful, MAL_FAIL else.
  */
uint16_t MEM_If_Init_FS(void)
{
  /* USER CODE BEGIN 0 */
  return (USBD_OK);
  /* USER CODE END 0 */
}

/**
  * @brief  De-Initializes Memory
  * @retval USBD_OK if operation is successful, MAL_FAIL else
  */
uint16_t MEM_If_DeInit_FS(void)
{
  /* USER CODE BEGIN 1 */
  return (USBD_OK);
  /* USER CODE END 1 */
}

/**
  * @brief  Erase sector.
  * @param  Add: Address of sector to be erased.
  * @retval 0 if operation is successful, MAL_FAIL else.
  */
uint16_t MEM_If_Erase_FS(uint32_t Add)
{
  /* USER CODE BEGIN 2 */
  g_dfu_host_active = 1;

  /* Only erase if address is within application space */
  if (Add < APP_ADDRESS) {
    return USBD_FAIL;
  }

  HAL_FLASH_Unlock();

  /* Determine which sector to erase based on address */
  uint32_t SectorError = 0;
  FLASH_EraseInitTypeDef EraseInitStruct;
  
  /* STM32F411 Flash sectors:
   * Sector 0-3: 16KB each (0x08000000 - 0x0800FFFF) - Bootloader area
   * Sector 4:   64KB      (0x08010000 - 0x0801FFFF)
   * Sector 5-7: 128KB each (0x08020000 - 0x0807FFFF) - Application area
   */
  
  uint8_t sector;
  if (Add >= 0x08020000) {
    sector = 5 + ((Add - 0x08020000) / 0x20000); // 128KB sectors
  } else if (Add >= 0x08010000) {
    sector = 4; // 64KB sector
  } else if (Add >= APP_ADDRESS) {
    sector = (Add - 0x08000000) / 0x4000; // 16KB sectors
  } else {
    HAL_FLASH_Lock();
    return USBD_FAIL;
  }

  EraseInitStruct.TypeErase = FLASH_TYPEERASE_SECTORS;
  EraseInitStruct.VoltageRange = FLASH_VOLTAGE_RANGE_3;
  EraseInitStruct.Sector = sector;
  EraseInitStruct.NbSectors = 1;

  if (HAL_FLASHEx_Erase(&EraseInitStruct, &SectorError) != HAL_OK) {
    HAL_FLASH_Lock();
    return USBD_FAIL;
  }

  HAL_FLASH_Lock();
  return (USBD_OK);
  /* USER CODE END 2 */
}

/**
  * @brief  Memory write routine.
  * @param  src: Pointer to the source buffer. Address to be written to.
  * @param  dest: Pointer to the destination buffer.
  * @param  Len: Number of data to be written (in bytes).
  * @retval USBD_OK if operation is successful, MAL_FAIL else.
  */
uint16_t MEM_If_Write_FS(uint8_t *src, uint8_t *dest, uint32_t Len)
{
  /* USER CODE BEGIN 3 */
  g_dfu_host_active = 1;

  /* Verify destination is in application space */
  if ((uint32_t)dest < APP_ADDRESS) {
    return USBD_FAIL;
  }

  HAL_FLASH_Unlock();

  /* Write data word by word (32-bit) */
  uint32_t data_offset = 0;
  while (data_offset < Len) {
    uint32_t data;
    
    /* Prepare 32-bit word from buffer */
    if (Len - data_offset >= 4) {
      data = *(uint32_t*)(src + data_offset);
    } else {
      /* Handle remaining bytes (less than 4) */
      data = 0xFFFFFFFF;
      for (uint32_t i = 0; i < (Len - data_offset); i++) {
        data &= ~(0xFF << (i * 8));
        data |= (src[data_offset + i] << (i * 8));
      }
    }

    /* Program the word */
    if (HAL_FLASH_Program(FLASH_TYPEPROGRAM_WORD, 
                          (uint32_t)dest + data_offset, 
                          data) != HAL_OK) {
      HAL_FLASH_Lock();
      return USBD_FAIL;
    }

    data_offset += 4;
  }

  HAL_FLASH_Lock();
  return (USBD_OK);
  /* USER CODE END 3 */
}

/**
  * @brief  Memory read routine.
  * @param  src: Pointer to the source buffer. Address to be written to.
  * @param  dest: Pointer to the destination buffer.
  * @param  Len: Number of data to be read (in bytes).
  * @retval Pointer to the physical address where data should be read.
  */
uint8_t *MEM_If_Read_FS(uint8_t *src, uint8_t *dest, uint32_t Len)
{
  /* Return a valid address to avoid HardFault */
  /* USER CODE BEGIN 4 */
  g_dfu_host_active = 1;
  
  /* DFU read expects direct pointer to flash memory */
  UNUSED(dest);
  UNUSED(Len);
  
  return src; /* Return source address for DFU to read directly */
  /* USER CODE END 4 */
}

/**
  * @brief  Get status routine
  * @param  Add: Address to be read from
  * @param  Cmd: Number of data to be read (in bytes)
  * @param  buffer: used for returning the time necessary for a program or an erase operation
  * @retval USBD_OK if operation is successful
  */
uint16_t MEM_If_GetStatus_FS(uint32_t Add, uint8_t Cmd, uint8_t *buffer)
{
  /* USER CODE BEGIN 5 */
  UNUSED(Add);

  switch (Cmd)
  {
    case DFU_MEDIA_PROGRAM:
      buffer[1] = (uint8_t)(1);      /* 1ms for programming */
      buffer[2] = (uint8_t)(0);
      buffer[3] = (uint8_t)(0);
      break;

    case DFU_MEDIA_ERASE:
      buffer[1] = (uint8_t)(50);     /* 50ms for erase */
      buffer[2] = (uint8_t)(0);
      buffer[3] = (uint8_t)(0);
      break;
      
    default:
      buffer[1] = (uint8_t)(0);
      buffer[2] = (uint8_t)(0);
      buffer[3] = (uint8_t)(0);
      break;
  }
  return (USBD_OK);
  /* USER CODE END 5 */
}

/* USER CODE BEGIN PRIVATE_FUNCTIONS_IMPLEMENTATION */

/* USER CODE END PRIVATE_FUNCTIONS_IMPLEMENTATION */

/**
  * @}
  */

/**
  * @}
  */

