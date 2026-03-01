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

#define MEM_BOOTLOADER_START         (0x08000000UL)
#define MEM_BOOTLOADER_END           (0x0800C000UL)
#define MEM_BOOTLOADER_LENGTH        (MEM_BOOTLOADER_END - MEM_BOOTLOADER_START)

#define MEM_APP_START                (0x0800C000UL)
#define MEM_APP_END                  (0x08040000UL)
#define MEM_APP_LENGTH               (MEM_APP_END - MEM_APP_START)

#define MEM_DATA_STORAGE_START       (0x08040000UL)
#define MEM_DATA_STORAGE_END         (0x08080000UL)
#define MEM_DATA_STORAGE_LENGTH      (MEM_DATA_STORAGE_END - MEM_DATA_STORAGE_START)

#endif /* __MEMORYLAYOUT_H */
