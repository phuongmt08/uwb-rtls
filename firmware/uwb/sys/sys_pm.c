/**
 * @file       sys_pm.c
 * @brief      System Power Management & Hardware Telemetry Service
 * @version    1.0.0
 * @date       2026-05-20
 * @author     Phuong Mai
 */

#include "sys_pm.h"
#include "bsp_adc.h"
#include "bsp_battery.h"
#include "bsp_uwb.h"
#include "bsp_imu.h"
#include "sys_logger.h"
#include "main.h"

/* Calibration data addresses for STM32F411 */
#ifndef VREFINT_CAL_ADDR
#define VREFINT_CAL_ADDR    ((uint16_t*) ((uint32_t) 0x1FFF7A2A))
#endif
#ifndef TEMPSENSOR_CAL1_ADDR
#define TEMPSENSOR_CAL1_ADDR ((uint16_t*) ((uint32_t) 0x1FFF7A2C))
#endif
#ifndef TEMPSENSOR_CAL2_ADDR
#define TEMPSENSOR_CAL2_ADDR ((uint16_t*) ((uint32_t) 0x1FFF7A2E))
#endif

/* Configuration Constants */
#define CHG_START_SOC_PCT   60.0f
#define CHG_STOP_SOC_PCT    95.0f
#define CHG_MAX_TEMP_DEGC   60.0f
#define BAT_CHARGING_CRATE_PHR 1.0f
#define ALERT_PERIOD_MS     5000
#define UWB_TELEMETRY_PERIOD_TICKS 50U /* sys_pm_task runs at 10 Hz -> 5 s */

/* Helper macro for the lookup table */
#define PM_LIMIT(_ch, _min, _max, _name) [_ch] = { .channel = _ch, .min = _min, .max = _max, .name = _name }

/* ===================================================================== */
/*                         THRESHOLD LOOKUP TABLE                        */
/* ===================================================================== */
static const pm_threshold_t PM_THRESHOLD_TABLE[PM_CH_MAX] = {
    //           | Power Channel        | Min      | Max      | Type String |
    PM_LIMIT( PM_CH_SOC              , 10.0f    , 100.0f   , "SOC (%)"   ),
    PM_LIMIT( PM_CH_VDDA             , 3000.0f  , 3600.0f  , "VDDA (mV)" ),
    PM_LIMIT( PM_CH_TEMP             , 20.0f    , 40.0f    , "MCU TEMP (C)"  ),
    PM_LIMIT( PM_CH_VBAT             , 3000.0f  , 4250.0f  , "VBAT (mV)" ),
    PM_LIMIT( PM_CH_CRATE            , -10000.0f, 5000.0f  , "CRATE (m%/h)" ),
    PM_LIMIT( PM_CH_UWB_TEMP         , 20.0f    , 50.0f    , "UWB TEMP (C)" ),
    PM_LIMIT( PM_CH_UWB_VBAT         , 3000.0f  , 3600.0f  , "UWB VBAT (mV)" ),
    PM_LIMIT( PM_CH_IMU_TEMP         , 20.0f    , 50.0f    , "IMU TEMP (C)" ),
};

/* Internal State */
static sys_pm_status_t s_pm_status = {0};

/* Private Helpers */
static void update_hw_watchdog(uint16_t vdda_min_mv, uint16_t vdda_max_mv);
static uint32_t sys_pm_make_critical_mask(uint32_t current_errors);
static void sys_pm_update_uwb_telemetry(void);
static void sys_pm_handle_charging(uint32_t critical_errors);
static void sys_pm_set_charge_en(bool enable);

void sys_pm_init(void)
{
    bsp_adc_init();
    (void)bsp_battery_task();
    
    /* Initial Charge State */
    sys_pm_set_charge_en(true);
    s_pm_status.is_safe = true;
    s_pm_status.values[PM_CH_UWB_TEMP] = 30.0f;
    s_pm_status.values[PM_CH_UWB_VBAT] = 3300.0f;
    s_pm_status.values[PM_CH_IMU_TEMP] = 30.0f;
    
    /* Push VDDA range to Hardware Watchdog (STM32 ADC) */
    update_hw_watchdog((uint16_t)PM_THRESHOLD_TABLE[PM_CH_VDDA].min, 
                       (uint16_t)PM_THRESHOLD_TABLE[PM_CH_VDDA].max);

    /* Push VBAT range to Hardware Watchdog (MAX17048 IC) */
    bsp_battery_set_thresholds((uint16_t)PM_THRESHOLD_TABLE[PM_CH_VBAT].min,
                               (uint16_t)PM_THRESHOLD_TABLE[PM_CH_VBAT].max);
}

void sys_pm_process(void)
{
    bsp_adc_raw_data_t raw;
    bsp_adc_read_raw(&raw);

    /* 1. Data Collection */
    s_pm_status.values[PM_CH_SOC]   = (float)bsp_battery_get_soc();
    s_pm_status.values[PM_CH_VBAT]  = (float)bsp_battery_get_voltage();
    s_pm_status.values[PM_CH_CRATE] = (float)bsp_battery_get_crate();
    s_pm_status.is_charging         = (s_pm_status.values[PM_CH_CRATE] > BAT_CHARGING_CRATE_PHR);
    
    // Calculate VDDA
    uint16_t raw_vref = raw.raw_avg[1];
    if (raw_vref > 0) {
        s_pm_status.values[PM_CH_VDDA] = (3300.0f * (float)(*VREFINT_CAL_ADDR)) / (float)raw_vref;
    } else {
        s_pm_status.values[PM_CH_VDDA] = 3300.0f;
    }

    // Calculate Temperature
    uint16_t raw_temp = raw.raw_avg[2];
    float ts_cal1 = (float)(*TEMPSENSOR_CAL1_ADDR);
    float ts_cal2 = (float)(*TEMPSENSOR_CAL2_ADDR);
    if (ts_cal2 - ts_cal1 > 0.0f) {
        float raw_temp_norm = (float)raw_temp * s_pm_status.values[PM_CH_VDDA] / 3300.0f;
        s_pm_status.values[PM_CH_TEMP] = ((110.0f - 30.0f) / (ts_cal2 - ts_cal1)) * (raw_temp_norm - ts_cal1) + 30.0f;
    } else {
        s_pm_status.values[PM_CH_TEMP] = 25.0f;
    }

    // Collect IMU Sensor Telemetry
    float imu_temp = 0.0f;
    if (bsp_imu_get_temp(&imu_temp) == BSP_IMU_OK) {
        s_pm_status.values[PM_CH_IMU_TEMP] = imu_temp;
    } else {
        s_pm_status.values[PM_CH_IMU_TEMP] = 30.0f; /* Normal fallback if not active */
    }

    // Get remaining battery time
    s_pm_status.remaining_min = bsp_battery_get_remaining_time();

    /* 2. Threshold Monitoring & Alerting */
    uint32_t current_errors = 0;
    for (int i = 0; i < PM_CH_MAX; i++) {
        float val = s_pm_status.values[i];
        if (val < PM_THRESHOLD_TABLE[i].min || val > PM_THRESHOLD_TABLE[i].max) {
            current_errors |= (1 << i);
            
            static uint32_t last_alert_tick[PM_CH_MAX] = {0};
            if (HAL_GetTick() - last_alert_tick[i] > ALERT_PERIOD_MS) {
                RLOG_E(LOG_OBJECT_CODE_PM, ERR_POS_OUT_OF_RANGE, "ALERT: %s (%.1f) out of range [%.1f, %.1f]", 
                       PM_THRESHOLD_TABLE[i].name, val, PM_THRESHOLD_TABLE[i].min, PM_THRESHOLD_TABLE[i].max);
                last_alert_tick[i] = HAL_GetTick();
            }
        }
    }

    /* 3. Hardware Watchdog Check (ADC) */
    if (raw.watchdog_fired) {
        current_errors |= PM_ERR_HW_WATCHDOG;
        bsp_adc_clear_watchdog();
        RLOG_E(LOG_OBJECT_CODE_PM, ERR_HAL, "ALERT: ADC Hardware Voltage Sag detected!");
    }

    /* 4. Battery Hardware Alerts (MAX17048 STATUS) */
    uint16_t bat_alerts = bsp_battery_get_hw_alerts();
    if (bat_alerts & BAT_HW_ALRT_RESET) {
        current_errors |= PM_ERR_BAT_RESET_BIT;
        RLOG_E(LOG_OBJECT_CODE_PM, ERR_BATTERY_INIT, "ALERT: Battery IC Reset detected!");
    }
    if (bat_alerts & (BAT_HW_ALRT_VOLT_LOW | BAT_HW_ALRT_SOC_LOW)) {
        current_errors |= PM_ERR_BAT_HW_LOW_BIT;
        RLOG_E(LOG_OBJECT_CODE_PM, ERR_BATTERY_LOW, "ALERT: Hardware Battery Low detected!");
    }
    if (bat_alerts & BAT_HW_ALRT_VOLT_HIGH) {
        current_errors |= PM_ERR_BAT_HW_HIGH_BIT;
        RLOG_E(LOG_OBJECT_CODE_PM, ERR_BATTERY_OVERVOLT, "ALERT: Hardware Battery Overvoltage detected!");
    }

    uint32_t critical_errors = sys_pm_make_critical_mask(current_errors);

    if (s_pm_status.error_mask != current_errors) {
        s_pm_status.error_mask = current_errors;
        RLOG_W(LOG_OBJECT_CODE_PM, "PM Status Changed: Flags=0x%02X", (unsigned int)current_errors);
    }

    if (s_pm_status.critical_mask != critical_errors) {
        s_pm_status.critical_mask = critical_errors;
        RLOG_W(LOG_OBJECT_CODE_PM, "PM Critical Flags=0x%02X", (unsigned int)critical_errors);
    }

    s_pm_status.is_safe = (critical_errors == 0);

    /* 5. Charge Control Logic */
    sys_pm_handle_charging(critical_errors);
}

bool sys_pm_is_safe(void)
{
    return s_pm_status.is_safe;
}

void sys_pm_get_status(sys_pm_status_t *status)
{
    if (status) *status = s_pm_status;
}

void sys_pm_task(void *arg)
{
    (void)arg;

    static uint8_t  battery_tick = 0;
    static uint16_t uwb_tick = 0;

    // Process battery fuel gauge at 1 Hz before PM consumes its snapshot.
    if (++battery_tick >= 10)
    {
        battery_tick = 0;
        bsp_battery_task();
    }

    // DW1000 temp/vbat reads touch internal radio state, so keep them slow.
    if (++uwb_tick >= UWB_TELEMETRY_PERIOD_TICKS)
    {
        uwb_tick = 0;
        sys_pm_update_uwb_telemetry();
    }

    // Process PM checks at 10 Hz
    sys_pm_process();
}

/* Private Functions -------------------------------------------------------- */

static uint32_t sys_pm_make_critical_mask(uint32_t current_errors)
{
    const uint32_t critical_sources = PM_ERR_SOC_BIT |
                                      PM_ERR_VDDA_BIT |
                                      PM_ERR_VBAT_BIT |
                                      PM_ERR_HW_WATCHDOG |
                                      PM_ERR_BAT_HW_LOW_BIT;

    return current_errors & critical_sources;
}

static void sys_pm_update_uwb_telemetry(void)
{
    float uwb_temp = 0.0f;
    float uwb_vbat = 0.0f;

    if (bsp_uwb_read_temp_vbat(&uwb_temp, &uwb_vbat) == BSP_OK) {
        s_pm_status.values[PM_CH_UWB_TEMP] = uwb_temp;
        s_pm_status.values[PM_CH_UWB_VBAT] = uwb_vbat * 1000.0f;
    }
}

static void sys_pm_handle_charging(uint32_t critical_errors)
{
    float current_soc  = s_pm_status.soc;
    float current_temp = s_pm_status.temp_degc;
    bool  is_now_safe  = (critical_errors == 0);

    /* Emergency Stop: Temperature too high */
    if (current_temp > CHG_MAX_TEMP_DEGC) {
        if (s_pm_status.charge_enabled) {
            RLOG_I(LOG_OBJECT_CODE_PM, "High Temperature (%.1f C). Charging suspended.", current_temp);
            sys_pm_set_charge_en(false);
        }
        return;
    }

    if (current_soc >= CHG_STOP_SOC_PCT) {
        if (s_pm_status.charge_enabled) {
            RLOG_I(LOG_OBJECT_CODE_PM, "Battery full (%.1f%%). Charging stopped.", current_soc);
            sys_pm_set_charge_en(false);
        }
    } 
    /* Resume charging at low SOC */
    else if (current_soc < CHG_START_SOC_PCT) {
        if (!s_pm_status.charge_enabled && is_now_safe) {
            RLOG_I(LOG_OBJECT_CODE_PM, "Battery low (%.1f%%). Charging resumed.", current_soc);
            sys_pm_set_charge_en(true);
        }
    }
}

static void sys_pm_set_charge_en(bool enable)
{
    HAL_GPIO_WritePin(CHARGE_EN_GPIO_Port, CHARGE_EN_Pin, enable ? GPIO_PIN_SET : GPIO_PIN_RESET);
    s_pm_status.charge_enabled = enable;
}

static void update_hw_watchdog(uint16_t vdda_min_mv, uint16_t vdda_max_mv)
{
    if (vdda_min_mv == 0 || vdda_max_mv == 0) return;

    /* Inverse mapping: 
       Higher VDDA Voltage -> Lower ADC Raw (VREFINT)
       Lower VDDA Voltage  -> Higher ADC Raw (VREFINT)
    */
    uint32_t adc_low_limit  = (3300UL * (uint32_t)(*VREFINT_CAL_ADDR)) / vdda_max_mv;
    uint32_t adc_high_limit = (3300UL * (uint32_t)(*VREFINT_CAL_ADDR)) / vdda_min_mv;
    
    /* Set the hardware window */
    bsp_adc_set_watchdog(adc_low_limit, adc_high_limit);
}
