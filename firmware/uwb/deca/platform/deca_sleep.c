/*! ----------------------------------------------------------------------------
 * @file    deca_sleep.c
 * @brief   platform dependent sleep implementation
 *
 * @attention
 *
 * Copyright 2015 (c) DecaWave Ltd, Dublin, Ireland.
 *
 * All rights reserved.
 *
 * @author DecaWave
 */

#include "deca_sleep.h"
#include "bsp_util.h"

void deca_sleep(unsigned int time_ms)
{
    bsp_delay_ms(time_ms);
}
