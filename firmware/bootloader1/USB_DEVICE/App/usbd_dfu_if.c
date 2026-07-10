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
volatile uint32_t g_dfu_last_activity = 0;
static uint8_t g_erased_sector_mask = 0;
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

/* DFU descriptor string (DfuSe memory map format).
 * Expose only the application partition. Keeping S0..S2 and S6..S7 out of
 * the USB DFU map avoids accidental bootloader/storage erase from host tools.
 */
#define FLASH_DESC_STR      "@App Flash       /0x0800C000/01*016Kg,01*064Kg,01*128Kg"

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
static uint16_t DFU_Erase_AppSectors(void);
static uint16_t DFU_Erase_UserSectors(void);
static uint32_t DFU_GetSectorFromAddress(uint32_t address);
static uint8_t DFU_SectorMask(uint32_t sector);
static uint16_t DFU_Erase_Sector(uint32_t sector);
static void DFU_SetPollTimeout(uint8_t *buffer, uint32_t timeout_ms);
static uint8_t DFU_AppSectorsErased(void);

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
  g_erased_sector_mask = 0;
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

static uint16_t DFU_Erase_AppSectors(void)
{
  if (DFU_Erase_Sector(FLASH_SECTOR_3) != USBD_OK)
  {
    return USBD_FAIL;
  }

  if (DFU_Erase_Sector(FLASH_SECTOR_4) != USBD_OK)
  {
    return USBD_FAIL;
  }

  if (DFU_Erase_Sector(FLASH_SECTOR_5) != USBD_OK)
  {
    return USBD_FAIL;
  }

  return USBD_OK;
}

static uint16_t DFU_Erase_UserSectors(void)
{
  return DFU_Erase_AppSectors();
}

static uint8_t DFU_SectorMask(uint32_t sector)
{
  switch (sector)
  {
    case FLASH_SECTOR_3: return (1U << 0);
    case FLASH_SECTOR_4: return (1U << 1);
    case FLASH_SECTOR_5: return (1U << 2);
    case FLASH_SECTOR_6: return (1U << 3);
    case FLASH_SECTOR_7: return (1U << 4);
    default:             return 0U;
  }
}

static uint16_t DFU_Erase_Sector(uint32_t sector)
{
  uint32_t SectorError = 0;
  FLASH_EraseInitTypeDef EraseInitStruct;

  __HAL_FLASH_CLEAR_FLAG(FLASH_FLAG_EOP | FLASH_FLAG_OPERR | FLASH_FLAG_WRPERR |
                         FLASH_FLAG_PGAERR | FLASH_FLAG_PGPERR | FLASH_FLAG_PGSERR);

  EraseInitStruct.TypeErase = FLASH_TYPEERASE_SECTORS;
  EraseInitStruct.VoltageRange = FLASH_VOLTAGE_RANGE_3;
  EraseInitStruct.NbSectors = 1;
  EraseInitStruct.Sector = sector;

  if (HAL_FLASHEx_Erase(&EraseInitStruct, &SectorError) != HAL_OK)
  {
    return USBD_FAIL;
  }

  g_erased_sector_mask |= DFU_SectorMask(sector);
  return USBD_OK;
}

static void DFU_SetPollTimeout(uint8_t *buffer, uint32_t timeout_ms)
{
  buffer[1] = (uint8_t)(timeout_ms & 0xFFU);
  buffer[2] = (uint8_t)((timeout_ms >> 8) & 0xFFU);
  buffer[3] = (uint8_t)((timeout_ms >> 16) & 0xFFU);
}

static uint8_t DFU_AppSectorsErased(void)
{
  const uint8_t app_erased_mask =
      DFU_SectorMask(FLASH_SECTOR_3) |
      DFU_SectorMask(FLASH_SECTOR_4) |
      DFU_SectorMask(FLASH_SECTOR_5);

  return ((g_erased_sector_mask & app_erased_mask) == app_erased_mask) ? 1U : 0U;
}

static uint32_t DFU_GetSectorFromAddress(uint32_t address)
{
  if (address < MEM_APP_START || address >= MEM_APP_END)
  {
    return 0xFFFFFFFFUL;
  }
  if (address < 0x08010000UL)
  {
    return FLASH_SECTOR_3;
  }
  if (address < 0x08020000UL)
  {
    return FLASH_SECTOR_4;
  }
  if (address < 0x08040000UL)
  {
    return FLASH_SECTOR_5;
  }
  return 0xFFFFFFFFUL;
}

/**
  * @brief  Erase sector.
  * @param  Add: Address of sector to be erased.
  * @retval 0 if operation is successful, MAL_FAIL else.
  */
uint16_t MEM_If_Erase_FS(uint32_t Add)
{
  /* USER CODE BEGIN 2 */
  g_dfu_last_activity = HAL_GetTick();

  HAL_FLASH_Unlock();

  uint16_t status = USBD_FAIL;

  /* Mass erase from host tool: erase the application partition only. */
  if ((Add == 0xFFFFFFFFUL) ||
      (Add == 0x00000000UL) ||
      (Add == 0x08000000UL))
  {
    status = DFU_Erase_UserSectors();
  }
  /* Erase application sectors specifically when MEM_APP_START is targeted */
  else if (Add == MEM_APP_START)
  {
    status = DFU_Erase_AppSectors();
  }
  /* Selected erase: erase the addressed app sector only. */
  else if ((Add >= MEM_APP_START) && (Add < MEM_APP_END))
  {
    uint32_t sector = DFU_GetSectorFromAddress(Add);
    if (sector != 0xFFFFFFFFUL && DFU_Erase_Sector(sector) == USBD_OK)
    {
      status = USBD_OK;
    }
  }

  HAL_FLASH_Lock();
  return status;
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
  g_dfu_last_activity = HAL_GetTick();

  if (!src || Len == 0U)
  {
    return USBD_FAIL;
  }

  /* Verify destination is inside the app partition only. */
  uint32_t addr = (uint32_t)dest;
  uint32_t end = addr + Len;
  if (end < addr || addr < MEM_APP_START || end > MEM_APP_END) {
    return USBD_FAIL;
  }

  uint32_t padded_len = (Len + 3U) & ~3U;
  uint32_t padded_end = addr + padded_len;
  if (padded_end < addr || padded_end > MEM_APP_END) {
    return USBD_FAIL;
  }

  HAL_FLASH_Unlock();
  
  /* Erase once per DFU session. Re-erasing on a repeated MEM_APP_START block
   * can destroy chunks that were already programmed.
   */
  if (!DFU_AppSectorsErased()) {
    if (DFU_Erase_AppSectors() != USBD_OK) {
      HAL_FLASH_Lock();
      return USBD_FAIL;
    }
  }

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

    uint32_t target = addr + data_offset;
    if ((*(const uint32_t *)target & data) != data) {
      HAL_FLASH_Lock();
      return USBD_FAIL;
    }

    /* Program the word */
    __HAL_FLASH_CLEAR_FLAG(FLASH_FLAG_EOP | FLASH_FLAG_OPERR | FLASH_FLAG_WRPERR |
                           FLASH_FLAG_PGAERR | FLASH_FLAG_PGPERR | FLASH_FLAG_PGSERR);
    if (HAL_FLASH_Program(FLASH_TYPEPROGRAM_WORD, 
                          target, 
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
  g_dfu_last_activity = HAL_GetTick();
  
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
  const uint32_t program_timeout_ms = 1U;
  const uint32_t erase_timeout_ms = 5000U;

  switch (Cmd)
  {
    case DFU_MEDIA_PROGRAM:
      if (!DFU_AppSectorsErased())
      {
        DFU_SetPollTimeout(buffer, erase_timeout_ms);
      }
      else
      {
        DFU_SetPollTimeout(buffer, program_timeout_ms);
      }
      break;

    case DFU_MEDIA_ERASE:
      DFU_SetPollTimeout(buffer, erase_timeout_ms);
      break;
      
    default:
      DFU_SetPollTimeout(buffer, 0U);
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

