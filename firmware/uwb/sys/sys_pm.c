/**
 * @file       sys_pm.c
 * @brief      System Power Management & Hardware Telemetry Service
 * @version    1.0.0
 * @date       2026-05-20
 * @author     Phuong Mai
 */

#include "sys_pm.h"
#include "bsp_battery.h"
#include "bsp_uwb.h"
#include "bsp_imu.h"
#include "sys_logger.h"
#include <string.h>

static sys_pm_status_t s_pm_status;

void sys_pm_init(void)
{
    memset(&s_pm_status, 0, sizeof(s_pm_status));
    
    // Initialize internal ADC
    bsp_adc_init();
    
    // Check if fuel gauge is present initially
    s_pm_status.fuel_gauge_present = bsp_battery_is_present();
    
    RLOG_I(LOG_OBJECT_CODE_PM, "Power Management Service Initialized");
}

void sys_pm_task(void *arg)
{
    (void)arg;

    // 1. Run battery task to update MAX17048 telemetry
    bsp_battery_task();

    // 2. Fetch battery info snapshot
    bsp_battery_info_t bat_info = {0};
    if (bsp_battery_get_info(&bat_info) == BSP_BATTERY_OK)
    {
        s_pm_status.voltage_mv = bat_info.voltage_mv;
        s_pm_status.soc_pct = bat_info.soc_pct;
        s_pm_status.remaining_min = bat_info.remaining_min;
        s_pm_status.is_charging = bat_info.is_charging;
        s_pm_status.fuel_gauge_present = bat_info.is_present;
    }
    else
    {
        s_pm_status.fuel_gauge_present = false;
    }

    // 3. Read internal MCU channels (Temp and VDDA)
    bsp_adc_read_all(&s_pm_status.adc);

    // 4. Read UWB DW1000 sensor data (temperature & vbat)
    float uwb_temp = 0.0f;
    float uwb_vbat = 0.0f;
    if (bsp_uwb_read_temp_vbat(&uwb_temp, &uwb_vbat) == BSP_OK)
    {
        s_pm_status.uwb_temp_c = uwb_temp;
        s_pm_status.uwb_vbat_mv = (uint32_t)(uwb_vbat * 1000.0f);
        s_pm_status.uwb_sensor_valid = true;
    }
    else
    {
        s_pm_status.uwb_temp_c = 0.0f;
        s_pm_status.uwb_vbat_mv = 0;
        s_pm_status.uwb_sensor_valid = false;
    }

    // 5. Read IMU sensor data (temperature)
    float imu_temp = 0.0f;
    if (bsp_imu_get_temp(&imu_temp) == BSP_IMU_OK)
    {
        s_pm_status.imu_temp_c = imu_temp;
        s_pm_status.imu_sensor_valid = true;
    }
    else
    {
        s_pm_status.imu_temp_c = 0.0f;
        s_pm_status.imu_sensor_valid = false;
    }

    // Log aggregated status for debugging/monitoring
    RLOG_I(LOG_OBJECT_CODE_PM, "PM Status - Bat: %lu mV (%u%%), MCU: %.1f C (%lu mV), UWB: %.1f C (%lu mV), IMU: %.1f C",
           (unsigned long)s_pm_status.voltage_mv, s_pm_status.soc_pct,
           s_pm_status.adc.temp_c, (unsigned long)s_pm_status.adc.vdda_mv,
           s_pm_status.uwb_temp_c, (unsigned long)s_pm_status.uwb_vbat_mv,
           s_pm_status.imu_temp_c);

    // 6. Safe Power Shutdown Check:
    // If fuel gauge is present and battery SOC is extremely low (< 10%),
    // automatically put DW1000 into IDLE state to prevent battery damage.
    if (s_pm_status.fuel_gauge_present && s_pm_status.soc_pct < 10)
    {
        static bool s_low_power_asserted = false;
        if (!s_low_power_asserted)
        {
            RLOG_W(LOG_OBJECT_CODE_PM, "CRITICAL: Battery SOC < 10%%! Putting UWB to IDLE.");
            bsp_uwb_idle();
            s_low_power_asserted = true;
        }
    }
}

void sys_pm_get_status(sys_pm_status_t *status)
{
    if (status)
    {
        *status = s_pm_status;
    }
}
