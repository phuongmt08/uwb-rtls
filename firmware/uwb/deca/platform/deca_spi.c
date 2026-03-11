/*! ----------------------------------------------------------------------------
 * @file	deca_spi.c
 * @brief	SPI access functions - STM32 HAL implementation
 *
 * @attention
 *
 * Copyright 2013 (c) DecaWave Ltd, Dublin, Ireland.
 *
 * All rights reserved.
 *
 * @author DecaWave
 */


/* NOTE: writetospi() and readfromspi() are implemented in bsp_uwb.c using STM32 HAL */

int openspi(void)
{
	/* SPI initialization done by HAL */
	return 0;
}

int closespi(void)
{
	return 0;
}
