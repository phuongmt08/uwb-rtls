/**
 * @file       bootloader.h
 * @brief      Minimal USB DFU bootloader API for STM32F411CEU6
 * @author     Phuong Mai
 * @version    1.0.0
 * @date       2025-09-29
 */

#ifndef __BOOTLOADER_H
#define __BOOTLOADER_H

#include <stdint.h>
#include <stdbool.h>
#include "../../../common/memorylayout.h"
#include "network_core.h"

/* Flash layout (STM32F411CE, 512KB)
 * - 0x08000000..0x0800BFFF: Bootloader code (48KB, sectors 0-2)
 * - 0x0800C000..0x0803FFFF: Application image (sectors 3-5)
 * - 0x08040000..0x0807FFFF: Data storage (sectors 6-7)
 */
/* SRAM range for MSP sanity check (F411 has 96KB SRAM) */
#define SRAM_BASE_ADDR     (0x20000000UL)
#define SRAM_END_ADDR      (0x20018000UL)

/* Magic flag in SRAM to request DFU after soft reset */
#define BL_MAGIC_ADDR      (0x2001FFF0UL)
#define BL_MAGIC_VALUE     (0xDEADB007UL)


/* DFU wait timeout before jumping to app if no host activity (ms) */
#define BL_DFU_TIMEOUT_MS           (5000U)   /* 5s for normal bootup */
#define BL_DFU_EXTENDED_TIMEOUT_MS  (60000U)  /* 60s if requested via magic */

/* Inactivity timeout - time to wait after last DFU operation (ms) */
#define BL_DFU_INACTIVITY_MS  (5000U)  /* 5s should be enough for programmer to complete */

/* FOTA timeout - abort if host stops sending flash_write chunks */
#define BL_FOTA_FLASH_WRITE_TIMEOUT_MS  (5000U)

bool bl_app_vector_valid(void);
bool bl_should_enter_dfu(void);
void bl_jump_to_app(void);

void bl_fota_init(network_core_t *net_core);
void bl_fota_process(void);
bool bl_fota_is_active(void);
bool bl_fota_is_finished(void);
bool bl_fota_run(network_core_t *net_core, uint32_t timeout_ms);

/* Timestamp of last DFU activity - updated by DFU callbacks */
extern volatile uint32_t g_dfu_last_activity;


#endif /* __BOOTLOADER_H */
