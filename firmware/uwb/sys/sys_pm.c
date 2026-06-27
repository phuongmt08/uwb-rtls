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
#include "sys_config.h"
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
#define ALERT_PERIOD_MS     1000
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
    PM_LIMIT( PM_CH_TEMP             , 20.0f    , 50.0f    , "MCU TEMP (C)"  ),
    PM_LIMIT( PM_CH_VBAT             , 3000.0f  , 4250.0f  , "VBAT (mV)" ),
    PM_LIMIT( PM_CH_CRATE            , -10000.0f, 5000.0f  , "CRATE (m%/h)" ),
    PM_LIMIT( PM_CH_UWB_TEMP         , 20.0f    , 60.0f    , "UWB TEMP (C)" ),
    PM_LIMIT( PM_CH_UWB_VBAT         , 3000.0f  , 3600.0f  , "UWB VBAT (mV)" ),
    PM_LIMIT( PM_CH_IMU_TEMP         , 20.0f    , 45.0f    , "IMU TEMP (C)" ),
};

/* Internal State */
static sys_pm_status_t s_pm_status = {0};

/* Private Helpers */
static bool sys_pm_imu_required(void);
static void update_hw_watchdog(uint16_t vdda_min_mv, uint16_t vdda_max_mv);
static uint32_t sys_pm_make_critical_mask(uint32_t current_errors);
static void sys_pm_update_uwb_telemetry(void);
static void sys_pm_handle_charging(uint32_t critical_errors);
static void sys_pm_set_charge_en(bool enable);

void sys_pm_init(void)
{
    s_pm_status.init_mask = 0;

    /* Initialize ADC driver */
    if (bsp_adc_init()) {
        s_pm_status.init_mask |= PM_INIT_ADC_BIT;
    } else {
        RLOG_E(LOG_OBJECT_CODE_PM, ERR_HAL, "PM: ADC driver initialization failed!");
    }

    /* Check if battery fuel gauge was initialized successfully */
    if (bsp_battery_is_initialized()) {
        s_pm_status.init_mask |= PM_INIT_BATTERY_BIT;
        (void)bsp_battery_task();
    } else {
        RLOG_E(LOG_OBJECT_CODE_PM, ERR_BATTERY_INIT, "PM: Battery fuel gauge not initialized!");
    }

    /* Check if UWB was initialized successfully */
    if (bsp_uwb_is_initialized()) {
        s_pm_status.init_mask |= PM_INIT_UWB_BIT;
    } else {
        RLOG_W(LOG_OBJECT_CODE_PM, "PM: UWB driver not initialized!");
    }

    /* IMU/sensor fusion exists only on tag hardware. */
    if (sys_pm_imu_required()) {
        if (bsp_imu_is_initialized()) {
            s_pm_status.init_mask |= PM_INIT_IMU_BIT;
        } else {
            RLOG_W(LOG_OBJECT_CODE_PM, "PM: IMU driver not initialized!");
        }
    }
    
    /* Initial Charge State */
    sys_pm_set_charge_en(true);
    s_pm_status.is_safe = true;
    s_pm_status.values[PM_CH_UWB_TEMP] = 30.0f;
    s_pm_status.values[PM_CH_UWB_VBAT] = 3300.0f;
    s_pm_status.values[PM_CH_IMU_TEMP] = 30.0f;
    
    /* Push VDDA range to Hardware Watchdog (STM32 ADC) if ADC init succeeded */
    if (s_pm_status.init_mask & PM_INIT_ADC_BIT) {
        update_hw_watchdog((uint16_t)PM_THRESHOLD_TABLE[PM_CH_VDDA].min, 
                           (uint16_t)PM_THRESHOLD_TABLE[PM_CH_VDDA].max);
    }

    /* Push VBAT range to Hardware Watchdog (MAX17048 IC) if Battery init succeeded */
    if (s_pm_status.init_mask & PM_INIT_BATTERY_BIT) {
        bsp_battery_set_thresholds((uint16_t)PM_THRESHOLD_TABLE[PM_CH_VBAT].min,
                                   (uint16_t)PM_THRESHOLD_TABLE[PM_CH_VBAT].max);
    }
}

void sys_pm_process(void)
{
    uint32_t current_errors = 0;

    /* 1. Initialization status errors */
    if (!(s_pm_status.init_mask & PM_INIT_ADC_BIT)) {
        current_errors |= PM_ERR_ADC_INIT_FAIL;
    }
    if (!(s_pm_status.init_mask & PM_INIT_BATTERY_BIT)) {
        current_errors |= PM_ERR_BAT_INIT_FAIL;
    }
    if (sys_pm_imu_required() && !(s_pm_status.init_mask & PM_INIT_IMU_BIT)) {
        current_errors |= PM_ERR_IMU_INIT_FAIL;
    }
    if (!(s_pm_status.init_mask & PM_INIT_UWB_BIT)) {
        current_errors |= PM_ERR_UWB_INIT_FAIL;
    }

    /* 2. Data Collection */
    
    // ADC Telemetry (VDDA & Temperature)
    if (s_pm_status.init_mask & PM_INIT_ADC_BIT) {
        bsp_adc_raw_data_t raw;
        bsp_adc_read_raw(&raw);

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

        /* Hardware Watchdog Check (ADC) */
        if (raw.watchdog_fired) {
            current_errors |= PM_ERR_HW_WATCHDOG;
            bsp_adc_clear_watchdog();
            static uint32_t last_watchdog_tick = 0;
            if (HAL_GetTick() - last_watchdog_tick >= ALERT_PERIOD_MS) {
                RLOG_E(LOG_OBJECT_CODE_PM, ERR_HAL, "ALERT: ADC Hardware Voltage Sag detected!");
                last_watchdog_tick = HAL_GetTick();
            }
        }
    } else {
        s_pm_status.values[PM_CH_VDDA] = 3300.0f;
        s_pm_status.values[PM_CH_TEMP] = 25.0f;
    }

    // Battery Fuel Gauge Telemetry
    if (s_pm_status.init_mask & PM_INIT_BATTERY_BIT) {
        s_pm_status.values[PM_CH_SOC]   = (float)bsp_battery_get_soc();
        s_pm_status.values[PM_CH_VBAT]  = (float)bsp_battery_get_voltage();
        s_pm_status.values[PM_CH_CRATE] = (float)bsp_battery_get_crate();
        s_pm_status.is_charging         = (s_pm_status.values[PM_CH_CRATE] > BAT_CHARGING_CRATE_PHR);
        s_pm_status.remaining_min       = bsp_battery_get_remaining_time();

        /* Battery Hardware Alerts (MAX17048 STATUS) */
        uint16_t bat_alerts = bsp_battery_get_hw_alerts();
        if (bat_alerts & BAT_HW_ALRT_RESET) {
            current_errors |= PM_ERR_BAT_RESET_BIT;
            static uint32_t last_bat_reset_tick = 0;
            if (HAL_GetTick() - last_bat_reset_tick >= ALERT_PERIOD_MS) {
                RLOG_E(LOG_OBJECT_CODE_PM, ERR_BATTERY_INIT, "Battery IC Reset detected!");
                last_bat_reset_tick = HAL_GetTick();
            }
        }
        if (bat_alerts & (BAT_HW_ALRT_VOLT_LOW | BAT_HW_ALRT_SOC_LOW)) {
            current_errors |= PM_ERR_BAT_HW_LOW_BIT;
            static uint32_t last_bat_low_tick = 0;
            if (HAL_GetTick() - last_bat_low_tick >= ALERT_PERIOD_MS) {
                RLOG_E(LOG_OBJECT_CODE_PM, ERR_BATTERY_LOW, "Hardware Battery Low detected!");
                last_bat_low_tick = HAL_GetTick();
            }
        }
        if (bat_alerts & BAT_HW_ALRT_VOLT_HIGH) {
            current_errors |= PM_ERR_BAT_HW_HIGH_BIT;
            static uint32_t last_bat_high_tick = 0;
            if (HAL_GetTick() - last_bat_high_tick >= ALERT_PERIOD_MS) {
                RLOG_E(LOG_OBJECT_CODE_PM, ERR_BATTERY_OVERVOLT, "Hardware Battery Overvoltage detected! check your battery and charger.");
                last_bat_high_tick = HAL_GetTick();
            }
        }
    } else {
        s_pm_status.values[PM_CH_SOC]   = 0.0f;
        s_pm_status.values[PM_CH_VBAT]  = 0.0f;
        s_pm_status.values[PM_CH_CRATE] = 0.0f;
        s_pm_status.is_charging         = false;
        s_pm_status.remaining_min       = 0;
    }

    // IMU Sensor Telemetry
    if (sys_pm_imu_required() && (s_pm_status.init_mask & PM_INIT_IMU_BIT)) {
        float imu_temp = 0.0f;
        if (bsp_imu_get_temp(&imu_temp) == BSP_IMU_OK) {
            s_pm_status.values[PM_CH_IMU_TEMP] = imu_temp;
        } else {
            s_pm_status.values[PM_CH_IMU_TEMP] = 0.0f; /* Normal fallback if not active */
        }
    } else {
        s_pm_status.values[PM_CH_IMU_TEMP] = 0.0f;
    }

    /* 3. Threshold Monitoring & Alerting (Skip uninitialized channels) */
    for (int i = 0; i < PM_CH_MAX; i++) {
        // Skip threshold breach check if the corresponding driver failed to init
        if (i == PM_CH_SOC || i == PM_CH_VBAT || i == PM_CH_CRATE) {
            if (!(s_pm_status.init_mask & PM_INIT_BATTERY_BIT)) continue;
        } else if (i == PM_CH_VDDA || i == PM_CH_TEMP) {
            if (!(s_pm_status.init_mask & PM_INIT_ADC_BIT)) continue;
        } else if (i == PM_CH_IMU_TEMP) {
            if (!sys_pm_imu_required() || !(s_pm_status.init_mask & PM_INIT_IMU_BIT)) continue;
        } else if (i == PM_CH_UWB_TEMP || i == PM_CH_UWB_VBAT) {
            if (!(s_pm_status.init_mask & PM_INIT_UWB_BIT)) continue;
        }

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

    /* 4. Charge Control Logic */
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
    bool allow_uwb_telemetry = true;
    if (arg != NULL) {
        allow_uwb_telemetry = *((const bool *)arg);
    }

    static uint8_t  battery_tick = 0;
    static uint16_t uwb_tick = 0;

    // Process battery fuel gauge at 1 Hz before PM consumes its snapshot.
    if (++battery_tick >= 10)
    {
        battery_tick = 0;
        if (s_pm_status.init_mask & PM_INIT_BATTERY_BIT)
        {
            bsp_battery_task();
        }
    }

    // DW1000 temp/vbat reads touch internal radio state, so keep them slow.
    if (uwb_tick < UWB_TELEMETRY_PERIOD_TICKS) {
        uwb_tick++;
    }

    if (uwb_tick >= UWB_TELEMETRY_PERIOD_TICKS && allow_uwb_telemetry)
    {
        uwb_tick = 0;
        sys_pm_update_uwb_telemetry();
    }

    // Process PM checks at 10 Hz
    sys_pm_process();
}

/* Private Functions -------------------------------------------------------- */

static bool sys_pm_imu_required(void)
{
    return sys_config_get_device_type() == DEVICE_TYPE_TAG;
}

static uint32_t sys_pm_make_critical_mask(uint32_t current_errors)
{
    const uint32_t critical_sources = PM_ERR_SOC_BIT |
                                      PM_ERR_VDDA_BIT |
                                      PM_ERR_VBAT_BIT |
                                      PM_ERR_HW_WATCHDOG |
                                      PM_ERR_BAT_HW_LOW_BIT |
                                      PM_ERR_ADC_INIT_FAIL;

    return current_errors & critical_sources;
}

static void sys_pm_update_uwb_telemetry(void)
{
    if (!(s_pm_status.init_mask & PM_INIT_UWB_BIT)) {
        s_pm_status.values[PM_CH_UWB_TEMP] = 30.0f;
        s_pm_status.values[PM_CH_UWB_VBAT] = 3300.0f;
        return;
    }

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
    float current_mcu_temp = s_pm_status.temp_degc;
    float currint_uwb_temp = s_pm_status.uwb_temp_c;

    bool  is_now_safe  = (critical_errors == 0);

    /* Emergency Stop: Temperature too high */
    if (current_mcu_temp > CHG_MAX_TEMP_DEGC) {
        static uint32_t last_mcu_temp_warn = 0;
        if (HAL_GetTick() - last_mcu_temp_warn >= ALERT_PERIOD_MS) {
            RLOG_I(LOG_OBJECT_CODE_PM, "High Temperature (%.1f C). Charging suspended.", current_mcu_temp);
            last_mcu_temp_warn = HAL_GetTick();
        }
        sys_pm_set_charge_en(false);
        return;
    }
    if (currint_uwb_temp > CHG_MAX_TEMP_DEGC) {
        static uint32_t last_uwb_temp_warn = 0;
        if (HAL_GetTick() - last_uwb_temp_warn >= ALERT_PERIOD_MS) {
            RLOG_I(LOG_OBJECT_CODE_PM, "High UWB Temperature (%.1f C). Charging suspended.", currint_uwb_temp);
            last_uwb_temp_warn = HAL_GetTick();
        }
        sys_pm_set_charge_en(false);
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
