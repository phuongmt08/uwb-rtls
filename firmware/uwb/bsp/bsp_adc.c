/**
 * @file       bsp_adc.c
 * @brief      Board Support Package for STM32 Internal ADC channels
 * @version    1.0.0
 * @date       2026-05-20
 * @author     Phuong Mai
 */

#include "bsp_adc.h"

// STM32F411 internal calibration register addresses
#define VREFINT_CAL_ADDR   ((uint16_t*)0x1FFF7A2A)
#define TS_CAL1_ADDR       ((uint16_t*)0x1FFF7A2C)
#define TS_CAL2_ADDR       ((uint16_t*)0x1FFF7A2E)

void bsp_adc_init(void)
{
    // Enable ADC1 peripheral clock
    __HAL_RCC_ADC1_CLK_ENABLE();

    // Enable internal temperature sensor and Vrefint channels
    ADC->CCR |= ADC_CCR_TSVREFE;

    // Set ADC prescaler to PCLK2/4 (96MHz / 4 = 24MHz max, or 48MHz / 4 = 12MHz)
    ADC->CCR = (ADC->CCR & ~ADC_CCR_ADCPRE) | ADC_CCR_ADCPRE_0;

    // 12-bit resolution, single conversion mode
    ADC1->CR1 &= ~ADC_CR1_RES;

    // Right data alignment, software trigger
    ADC1->CR2 &= ~(ADC_CR2_ALIGN | ADC_CR2_EXTEN);

    // Set sampling time for Channel 17 (Vrefint) and 18 (Tempsensor) to 480 cycles (0x7)
    ADC1->SMPR1 |= (7UL << 21) | (7UL << 24);

    // Power on ADC1
    ADC1->CR2 |= ADC_CR2_ADON;
}

static uint16_t adc_read_channel(uint32_t channel)
{
    // Configure single channel in SQR3 (1st conversion SQ1)
    ADC1->SQR3 = channel & 0x1FUL;

    // Configure sequence length to 1 conversion
    ADC1->SQR1 &= ~ADC_SQR1_L;

    // Clear status flags
    ADC1->SR = 0;

    // Start software conversion
    ADC1->CR2 |= ADC_CR2_SWSTART;

    // Wait for the conversion to complete (with a simple timeout)
    uint32_t timeout = 10000;
    while (!(ADC1->SR & ADC_SR_EOC) && --timeout);

    if (timeout == 0)
    {
        return 0;
    }

    return (uint16_t)ADC1->DR;
}

void bsp_adc_read_all(bsp_adc_data_t *data)
{
    if (!data) return;

    data->vref_raw = adc_read_channel(17); // Channel 17 is VREFINT
    data->temp_raw = adc_read_channel(18); // Channel 18 is TEMPSENSOR

    // Read factory calibration values
    uint16_t vrefint_cal = *VREFINT_CAL_ADDR;
    uint16_t ts_cal1 = *TS_CAL1_ADDR;
    uint16_t ts_cal2 = *TS_CAL2_ADDR;

    // Calculate actual VDDA in mV
    if (data->vref_raw > 0)
    {
        data->vdda_mv = 3300UL * (uint32_t)vrefint_cal / (uint32_t)data->vref_raw;
    }
    else
    {
        data->vdda_mv = 3300;
    }

    // Scale temp raw value to 3.3V reference used during factory calibration
    float temp_raw_scaled = (float)data->temp_raw * (float)data->vdda_mv / 3300.0f;

    // Calculate temperature in degrees Celsius
    float temp_diff_cal = (float)(ts_cal2 - ts_cal1);
    if (temp_diff_cal > 0.0f)
    {
        data->temp_c = ((temp_raw_scaled - (float)ts_cal1) * (110.0f - 30.0f) / temp_diff_cal) + 30.0f;
    }
    else
    {
        data->temp_c = 0.0f;
    }
}
