/**
 * @file       bsp_adc.c
 * @brief      Implementation of pure ADC driver with AWD support
 */

#include "bsp_adc.h"
#include "adc.h"
#include "tim.h"

/* Internal variables */
static uint16_t s_adc_raw_buf[ADC_CHANNEL_COUNT * ADC_AVG_SAMPLES];
static volatile bool s_watchdog_fired = false;

void bsp_adc_init(void)
{
    /* Start Timer 2 (100Hz trigger) */
    HAL_TIM_Base_Start(&htim2);

    /* Start ADC in DMA mode */
    HAL_ADC_Start_DMA(&hadc1, (uint32_t*)s_adc_raw_buf, ADC_CHANNEL_COUNT * ADC_AVG_SAMPLES);
}

void bsp_adc_read_raw(bsp_adc_raw_data_t *data)
{
    if (!data) return;

    for (int ch = 0; ch < ADC_CHANNEL_COUNT; ch++) {
        uint32_t sum = 0;
        for (int s = 0; s < ADC_AVG_SAMPLES; s++) {
            sum += s_adc_raw_buf[s * ADC_CHANNEL_COUNT + ch];
        }
        data->raw_avg[ch] = sum / ADC_AVG_SAMPLES;
    }
    data->watchdog_fired = s_watchdog_fired;
}

void bsp_adc_set_watchdog(uint32_t low_threshold, uint32_t high_threshold)
{
    ADC_AnalogWDGConfTypeDef AWDConfig = {0};

    AWDConfig.WatchdogMode = ADC_ANALOGWATCHDOG_SINGLE_REG;
    AWDConfig.Channel      = ADC_CHANNEL_VREFINT; /* Monitor system voltage stability */
    AWDConfig.ITMode       = ENABLE;
    AWDConfig.LowThreshold = low_threshold;
    AWDConfig.HighThreshold = high_threshold;

    HAL_ADC_AnalogWDGConfig(&hadc1, &AWDConfig);
}

void bsp_adc_clear_watchdog(void)
{
    s_watchdog_fired = false;
}

/**
 * @brief  ADC AWD Callback (called by HAL when voltage goes out of window)
 */
void HAL_ADC_LevelOutOfWindowCallback(ADC_HandleTypeDef* hadc)
{
    if (hadc->Instance == ADC1) {
        s_watchdog_fired = true;
        /* Note: You might want to stop UWB immediately here if this is ultra-critical */
    }
}
