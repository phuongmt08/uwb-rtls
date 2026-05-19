/**
 * @file       sys_pm.h
 * @brief      System Power Management & Hardware Telemetry Service
 * @version    1.0.0
 * @date       2026-05-20
 * @author     Phuong Mai
 */

#ifndef SYS_PM_H
#define SYS_PM_H

#include "bsp_adc.h"
#include <stdint.h>
#include <stdbool.h>

typedef struct {
    uint32_t voltage_mv;
    uint8_t  soc_pct;
    int32_t  remaining_min;
    bool     is_charging;
    bool     fuel_gauge_present;
    
    bsp_adc_data_t adc;
    
    /* DW1000 monitoring telemetry */
    float    uwb_temp_c;
    uint32_t uwb_vbat_mv;
    bool     uwb_sensor_valid;

    /* IMU monitoring telemetry */
    float    imu_temp_c;
    bool     imu_sensor_valid;
} sys_pm_status_t;

/**
 * @brief Initialize Power Management and ADC hardware.
 */
void sys_pm_init(void);

/**
 * @brief 1Hz periodic task to collect battery, internal ADC, DW1000, and IMU status.
 * @param[in] arg Optional task argument.
 */
void sys_pm_task(void *arg);

/**
 * @brief Get the latest aggregated power management and hardware monitoring snapshot.
 * @param[out] status Pointer to destination structure.
 */
void sys_pm_get_status(sys_pm_status_t *status);

#endif /* SYS_PM_H */
