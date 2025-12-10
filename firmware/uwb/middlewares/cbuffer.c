/**
 * @file       cbuffer.c
 * @copyright
 * @license
 * @version    1.0.0
 * @date
 * @author	   Triet Luu
 * @brief      Circular Buffer
 *             This Circular Buffer is safe to use in IRQ with single reader,
 *             single writer. No need to disable any IRQ.
 *
 *             Capacity = <size> - 1
 * @note       None
 * @example    None
 */

/* Includes ----------------------------------------------------------- */
#include "cbuffer.h"

/* Private defines ---------------------------------------------------- */
/* Private enumerate/structure ---------------------------------------- */
/* Private macros ----------------------------------------------------- */
/* Public variables --------------------------------------------------- */
/* Private variables -------------------------------------------------- */
/* Private function prototypes ---------------------------------------- */
/* Function definitions ----------------------------------------------- */
void cb_init(cbuffer_t *cb, void *buf, uint32_t size)
{
    // assert_param(size > 1);
    // assert_param(size <= CB_MAX_SIZE);

    cb->data = (uint8_t *)buf;
    cb->size = size;
    cb->reader = 0;
    cb->writer = 0;
    cb->overflow = 0;
    cb->active = true;
}

void cb_clear(cbuffer_t *cb)
{
    cb->active = false;
#ifndef UNIT_TEST
    __DMB();
#endif

    cb->reader = 0;
    cb->writer = 0;
    cb->overflow = 0;

#ifndef UNIT_TEST
    __DMB();
#endif
    cb->active = true;
}

uint32_t cb_data_count(cbuffer_t *cb)
{
    if (!(cb->active))
        return 0;

    uint32_t tmp_reader, tmp_writer;
    tmp_reader = cb->reader;
    tmp_writer = cb->writer;

    if (tmp_reader <= tmp_writer)
        return (tmp_writer - tmp_reader);
    else
        return (tmp_writer + cb->size - tmp_reader);
}

uint32_t cb_space_count(cbuffer_t *cb)
{
    return (cb->size - cb_data_count(cb) - 1);
}

uint32_t cb_read(cbuffer_t *cb, void *buf, uint32_t nbytes)
{
    if (!(cb->active))
        return 0;

    // Classic version ------------------------------- {
    uint32_t i;

    for (i = 0; i < nbytes; i++)
    {
        // See if any data is available
        if (cb->reader != cb->writer)
        {
            if (buf != NULL)
            {
                // Grab a byte from the internal buffer
                *((uint8_t *)buf) = cb->data[cb->reader];
                buf = (uint8_t *)buf + 1;
            }

            // Check for wrap-around
            if (cb->reader + 1 == cb->size)
                cb->reader = 0;
            else
                cb->reader = cb->reader + 1;
        }
        else
        {
            break;
        }
    }

    return i; // Number of bytes read
    // Classic version ------------------------------- }
}

uint32_t cb_top(cbuffer_t *cb, uint32_t index, void *buf, uint32_t nbytes)
{
    if (!(cb->active))
        return 0;

    uint32_t i;
    uint32_t tmp_reader = cb->reader;

    for (i = 0; i < index + nbytes; i++)
    {
        // See if any data is available
        if (tmp_reader != cb->writer)
        {
            if (i >= index)
            {
                // Grab a byte from the internal buffer
                *((uint8_t *)buf) = cb->data[tmp_reader];
                buf = (uint8_t *)buf + 1;
            }

            // Check for wrap-around
            if (tmp_reader + 1 == cb->size)
                tmp_reader = 0;
            else
                tmp_reader = tmp_reader + 1;
        }
        else
        {
            break;
        }
    }

    return i - index; // Number of bytes read
}

uint32_t cb_write(cbuffer_t *cb, void *buf, uint32_t nbytes)
{
    if (!(cb->active))
        return 0;
    if (nbytes == 0)
        return 0;
    uint32_t i;

    for (i = 0; i < nbytes; i++)
    {
        // First check to see if there is space in the buffer
        if ((cb->writer + 1 == cb->reader) || ((cb->writer + 1 == cb->size) && (cb->reader == 0)))
        {
            cb->overflow += (nbytes - i);
            break;
        }
        else
        {
            if (buf != NULL)
            {
                // Write a byte to the internal buffer
                cb->data[cb->writer] = *((uint8_t *)buf);
                buf = (uint8_t *)buf + 1;
            }

            // Check for wrap-around
            if (cb->writer + 1 == cb->size)
                cb->writer = 0;
            else
                cb->writer = cb->writer + 1;
        }
    }

    return i; // Number of bytes write
}
/* End of file -------------------------------------------------------- */
