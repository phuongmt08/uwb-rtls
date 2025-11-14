/**
 * @file       cbuffer.h
 * @copyright
 * @license
 * @version    1.0.0
 * @date
 * @author     Triet Luu
 * @brief      Circular Buffer
 *             This Circular Buffer is safe to use in IRQ with single reader,
 *             single writer. No need to disable any IRQ.
 *
 *             Capacity = <size> - 1
 * @note       None
 * @example    None
 */
/* Define to prevent recursive inclusion ------------------------------ */
#ifndef __CBUFFER_H
#define __CBUFFER_H

/* Includes ----------------------------------------------------------- */
#include <stdio.h>
#include <stdlib.h>
#include <stdbool.h>
#include <string.h>
#include "cmsis_compiler.h"

/* Public defines ----------------------------------------------------- */
#define CB_MAX_SIZE (0x00800000)

/* Public enumerate/structure ----------------------------------------- */
typedef struct
{
    uint8_t *data;
    uint32_t size;
    uint32_t writer;
    uint32_t reader;
    uint32_t overflow;
    bool active;
} cbuffer_t;

/* Public macros ------------------------------------------------------ */
/* Public variables --------------------------------------------------- */
/* Public function prototypes ----------------------------------------- */
void cb_init(cbuffer_t *cb, void *buf, uint32_t size);
void cb_clear(cbuffer_t *cb);
uint32_t cb_top(cbuffer_t *cb, uint32_t index, void *buf, uint32_t nbytes);
uint32_t cb_read(cbuffer_t *cb, void *buf, uint32_t nbytes);
uint32_t cb_write(cbuffer_t *cb, void *buf, uint32_t nbytes);
uint32_t cb_data_count(cbuffer_t *cb);
uint32_t cb_space_count(cbuffer_t *cb);

#endif // __CBUFFER_H

/* End of file -------------------------------------------------------- */
