/**
 * @file       bsp_adc.h
 * @brief      Board Support Package for STM32 Internal ADC channels
 * @version    1.0.0
 * @date       2026-05-20
 * @author     Phuong Mai
 */

#ifndef BSP_ADC_H
#define BSP_ADC_H

#include "main.h"
#include <stdint.h>

typedef struct {
    uint16_t temp_raw;
    uint16_t vref_raw;
    
    uint32_t vdda_mv; /* VDDA voltage in mV (3.3V line) */
    float    temp_c;  /* Internal MCU temperature in degrees Celsius */
} bsp_adc_data_t;

/**
 * @brief Initialize ADC1 and internal Vrefint / Temperature channels.
 */
void bsp_adc_init(void);

/**
 * @brief Read internal temperature and reference voltage, and perform calibration.
 * @param[out] data Pointer to structure to hold measured and calculated values.
 */
void bsp_adc_read_all(bsp_adc_data_t *data);

#endif /* BSP_ADC_H */
