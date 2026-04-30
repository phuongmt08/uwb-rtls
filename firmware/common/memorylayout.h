#ifndef __MEMORYLAYOUT_H
#define __MEMORYLAYOUT_H

#include <stdint.h>

/*
|--------------------------------|-----------|-------|
| DATA STORAGE (S6-S7)           |   256KB   |       |
|--------------------------------|-----------|       |
| APPLICATION (S3-S5)            |   208KB   | 512KB |
|--------------------------------|-----------|       |
| BOOTLOADER (S0-S2)             |    48KB   |       |
|--------------------------------|-----------|-------|

Address range:
- BOOTLOADER : 0x08000000 -> 0x0800BFFF
- APPLICATION: 0x0800C000 -> 0x0803FFFF
- DATA STORE : 0x08040000 -> 0x0807FFFF
*/
#define BL_MAGIC_ADDR      (0x2001FFF0UL)
#define BL_MAGIC_VALUE     (0xDEADB007UL)

/* Shared RAM log area (noinit/NOLOAD): retained across soft reset and boot->app jump.
 * Must match both bootloader and app linker scripts. */
#define MEM_SHARED_LOG_RAM_START    (0x20017000UL)
#define MEM_SHARED_LOG_RAM_SIZE      (4UL * 1024UL)
#define MEM_SHARED_LOG_RAM_END      (MEM_SHARED_LOG_RAM_START + MEM_SHARED_LOG_RAM_SIZE)

#define MEM_BOOTLOADER_START         (0x08000000UL)
#define MEM_BOOTLOADER_END           (0x0800C000UL)
#define MEM_BOOTLOADER_LENGTH        (MEM_BOOTLOADER_END - MEM_BOOTLOADER_START)

#define MEM_APP_START                (0x0800C000UL)
#define MEM_APP_END                  (0x08040000UL)
#define MEM_APP_LENGTH               (MEM_APP_END - MEM_APP_START)

#define APP_IMAGE_HEADER_MAGIC       (0x41505048UL) /* 'APPH' */
#define APP_IMAGE_HEADER_VERSION     (1U)
/* Header sits at a fixed offset right after the ISR vector table (256-byte
 * aligned), at the very beginning of the application flash region.
 * Bootloader finds it by scanning from MEM_APP_START for the magic value. */
#define MEM_APP_HEADER_OFFSET        (0x200UL)              /* 512 B past app start */
#define MEM_APP_HEADER_SIZE          (0x100UL)              /* 256 B reserved */
#define MEM_APP_HEADER_ADDR          (MEM_APP_START + MEM_APP_HEADER_OFFSET)

#define MEM_DATA_STORAGE_START       (0x08040000UL)
#define MEM_DATA_STORAGE_END         (0x08080000UL)
#define MEM_DATA_STORAGE_LENGTH      (MEM_DATA_STORAGE_END - MEM_DATA_STORAGE_START)

#endif /* __MEMORYLAYOUT_H */
