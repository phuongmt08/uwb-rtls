/**
 * @file       sys_pm.h
 * @brief      System Power Management & Hardware Telemetry Service
 * @version    1.0.0
 * @date       2026-05-20
 * @author     Phuong Mai
 */

#ifndef SYS_PM_H
#define SYS_PM_H

#include <stdint.h>
#include <stdbool.h>

/* Power Channels to monitor */
typedef enum {
    PM_CH_SOC = 0,
    PM_CH_VDDA,
    PM_CH_TEMP,
    PM_CH_VBAT,
    PM_CH_CRATE,
    PM_CH_UWB_TEMP,
    PM_CH_UWB_VBAT,
    PM_CH_IMU_TEMP,
    PM_CH_MAX
} pm_channel_t;

typedef struct {
    pm_channel_t channel;
    float        min;
    float        max;
    const char  *name;
} pm_threshold_t;

typedef struct {
    union {
        float values[PM_CH_MAX];
        struct {
            float soc;            /* PM_CH_SOC */
            float vdda_mv;        /* PM_CH_VDDA */
            float temp_degc;      /* PM_CH_TEMP */
            float bat_voltage_mv; /* PM_CH_VBAT */
            float crate;          /* PM_CH_CRATE */
            float uwb_temp_c;     /* PM_CH_UWB_TEMP */
            float uwb_vbat_mv;    /* PM_CH_UWB_VBAT */
            float imu_temp_c;     /* PM_CH_IMU_TEMP */
        };
    };
    int32_t  remaining_min;
    bool     is_safe;         /* True if no critical power fault is active */
    bool     is_charging;     /* True when the fuel gauge reports charge current */
    bool     charge_enabled;  /* Current state of the CHARGE_EN control pin */
    uint32_t error_mask;      /* Bitmask of all breached thresholds */
    uint32_t critical_mask;   /* Bitmask of faults that are allowed to halt ranging */
    uint32_t init_mask;       /* Bitmask of successfully initialized modules */
} sys_pm_status_t;

/* Initialization bits for init_mask */
#define PM_INIT_ADC_BIT       (1 << 0)
#define PM_INIT_BATTERY_BIT   (1 << 1)
#define PM_INIT_IMU_BIT       (1 << 2)
#define PM_INIT_UWB_BIT       (1 << 3)

/* Error bits for error_mask */
#define PM_ERR_SOC_BIT        (1 << PM_CH_SOC)
#define PM_ERR_VDDA_BIT       (1 << PM_CH_VDDA)
#define PM_ERR_TEMP_BIT       (1 << PM_CH_TEMP)
#define PM_ERR_VBAT_BIT       (1 << PM_CH_VBAT)
#define PM_ERR_CRATE_BIT      (1 << PM_CH_CRATE)
#define PM_ERR_UWB_TEMP_BIT   (1 << PM_CH_UWB_TEMP)
#define PM_ERR_UWB_VBAT_BIT   (1 << PM_CH_UWB_VBAT)
#define PM_ERR_IMU_TEMP_BIT   (1 << PM_CH_IMU_TEMP)

#define PM_ERR_HW_WATCHDOG     (1 << 8)
#define PM_ERR_BAT_RESET_BIT   (1 << 9)
#define PM_ERR_BAT_HW_LOW_BIT  (1 << 10)
#define PM_ERR_BAT_HW_HIGH_BIT (1 << 11)
#define PM_ERR_ADC_INIT_FAIL   (1 << 12)
#define PM_ERR_BAT_INIT_FAIL   (1 << 13)
#define PM_ERR_IMU_INIT_FAIL   (1 << 14)
#define PM_ERR_UWB_INIT_FAIL   (1 << 15)

/**
 * @brief  Initialize Power Management and GPIOs.
 */
void sys_pm_init(void);

/**
 * @brief  Process all checks, alerts, and charge control logic (runs at 10Hz).
 */
void sys_pm_process(void);

/**
 * @brief  Check if system is in a safe operating state (for UWB, etc).
 */
bool sys_pm_is_safe(void);

/**
 * @brief  Get latest PM status.
 */
void sys_pm_get_status(sys_pm_status_t *status);

/**
 * @brief  Periodic task to run PM process and battery task.
 * @param  arg  Optional const bool*: true allows direct DW1000 telemetry reads.
 */
void sys_pm_task(void *arg);

#endif /* SYS_PM_H */
