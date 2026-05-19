/**
 * @file       bsp_adc.h
 * @brief      Pure Driver for ADC + DMA + Analog Watchdog
 */

#ifndef BSP_ADC_H
#define BSP_ADC_H

#include "main.h"
#include <stdint.h>
#include <stdbool.h>

/* Private configuration for the driver */
#define ADC_CHANNEL_COUNT      3    /* CH8, VREFINT, TEMPSENSOR */
#define ADC_AVG_SAMPLES        16   

typedef struct {
    uint16_t raw_avg[ADC_CHANNEL_COUNT]; /* Average raw values from buffer */
    bool     watchdog_fired;             /* Hardware watchdog flag */
} bsp_adc_raw_data_t;

/**
 * @brief  Initialize ADC driver.
 */
void bsp_adc_init(void);

/**
 * @brief  Read latest averaged raw data.
 */
void bsp_adc_read_raw(bsp_adc_raw_data_t *data);

/**
 * @brief  Set Analog Watchdog thresholds for a specific channel.
 * @param  low_threshold   Lower ADC value (0-4095)
 * @param  high_threshold  Upper ADC value (0-4095)
 */
void bsp_adc_set_watchdog(uint32_t low_threshold, uint32_t high_threshold);

/**
 * @brief  Clear the hardware watchdog flag.
 */
void bsp_adc_clear_watchdog(void);

#endif /* BSP_ADC_H */
