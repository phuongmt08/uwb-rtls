/**
 * @file       max17048.h
 * @copyright
 * @license
 * @version    1.1.0
 * @date       2026-03-17
 * @author
 * @brief      MAX17048 Li+ ModelGauge fuel gauge driver (I2C mode)
 * @note       I2C slave address: 0x36 (7-bit), fixed by manufacturer
 *             Hardware: STM32F411CEUx
 *             I2C3 — SCL: PA8 | SDA: PC9
 *             Based on MAX17048/MAX17049 datasheet Rev 1; 4/12
 * @example    None
 */

#ifndef __MAX17048_H
#define __MAX17048_H

/* Public includes ---------------------------------------------------------- */
#include <stdbool.h>
#include <stdint.h>

/* Public defines ----------------------------------------------------------- */

/* I2C address (7-bit, fixed by manufacturer) */
#define MAX17048_I2C_ADDR              0x36

/* Register map — all registers are 16-bit, MSB first */
#define MAX17048_REG_VCELL             0x02   /* Read cell voltage (R)              */
#define MAX17048_REG_SOC               0x04   /* Read state of charge (R)           */
#define MAX17048_REG_MODE              0x06   /* Mode control (W)              */
#define MAX17048_REG_VERSION           0x08   /* Check IC production version (R)     */
#define MAX17048_REG_HIBRT             0x0A   /* Hibernate thresholds (R/W)    */
#define MAX17048_REG_CONFIG            0x0C   /* Configuration (R/W)           */
#define MAX17048_REG_VALRT             0x14   /* Voltage alert thresholds (R/W)*/
#define MAX17048_REG_CRATE             0x16   /* Charge/discharge rate (R)     */
#define MAX17048_REG_VRESET_ID         0x18   /* VRESET & device ID (R/W)      */
#define MAX17048_REG_STATUS            0x1A   /* Alert status flags (R/W)      */
#define MAX17048_REG_TABLE_START       0x40   /* ModelGauge table start (W)    */
#define MAX17048_REG_CMD               0xFE   /* POR command register (R/W)    */

/* MODE register (0x06)
 * Datasheet p.11 — Figure 8
 * bit15    = X (don't care)
 * bit14    = QuickStart                              
 * bit13    = EnSleep
 * bit12    = HibStat (read only)
 * bit11..0 = X (don't care)
 */
#define MAX17048_MODE_QUICKSTART       0x4000  /* bit14: trigger quick-start   */
#define MAX17048_MODE_ENSLEEP          0x2000  /* bit13: enable sleep mode      */
#define MAX17048_MODE_HIBSTAT          0x1000  /* bit12: hibernate status (R)   */

/* CONFIG register (0x0C)
 * Datasheet p.12 — Figure 10
 * bit15..8 = RCOMP[7:0]
 * bit7     = SLEEP
 * bit6     = ALSC  (SOC change alert enable, 1 = enabled)
 * bit5     = ALRT  (alert flag, IC sets this, MCU clears it)
 * bit4..0  = ATHD[4:0] (empty alert threshold = 32 - ATHD %)
 */
#define MAX17048_CONFIG_SLEEP          0x0080  /* bit7: force sleep mode        */
#define MAX17048_CONFIG_ALSC           0x0040  /* bit6: SOC 1% change alert en  */
#define MAX17048_CONFIG_ALRT           0x0020  /* bit5: alert flag              */
#define MAX17048_CONFIG_ATHD_MASK      0x001F  /* bit[4:0]: ATHD field          */

/* STATUS register (0x1A)
 * Datasheet p.14 — Figure 13
 * bit15    = X
 * bit14    = EnVR  (enable voltage reset alert)
 * bit13    = MD    (SOC change alert flag)
 * bit12    = HD    (SOC low alert flag)
 * bit11    = VR    (voltage reset alert flag)
 * bit10    = VL    (voltage low alert flag)
 * bit9     = VH    (voltage high alert flag)
 * bit8     = RI    (reset indicator)
 * bit7..0  = X
 */
#define MAX17048_STATUS_ENVR           0x4000  /* bit14: enable vreset alert    */
#define MAX17048_STATUS_MD             0x2000  /* bit13: SOC 1% change flag     */
#define MAX17048_STATUS_HD             0x1000  /* bit12: SOC low flag           */
#define MAX17048_STATUS_VR             0x0800  /* bit11: voltage reset flag     */
#define MAX17048_STATUS_VL             0x0400  /* bit10: voltage low flag       */
#define MAX17048_STATUS_VH             0x0200  /* bit9:  voltage high flag      */
#define MAX17048_STATUS_RI             0x0100  /* bit8:  reset indicator        */

/* VRESET/ID register (0x18)
 * Datasheet p.13 — Figure 12
 * bit15..9 = VRESET[7:1]  (threshold, 1 LSb = 40 mV)
 * bit8     = Dis           (disable comparator in hibernate)
 * bit7..1  = ID[6:0]       (factory ID, read only)
 * bit0     = X
 */
#define MAX17048_VRESET_DIS            0x0100  /* bit8: disable comparator      */
#define MAX17048_VRESET_MASK           0xFE00  /* bit[15:9]: VRESET field       */
#define MAX17048_VRESET_SHIFT          9
#define MAX17048_ID_MASK               0x00FE  /* bit[7:1]: device ID (R only)  */
#define MAX17048_ID_SHIFT              1

/* CMD register (0xFE) */
#define MAX17048_CMD_POR               0x5400  /* power-on reset command        */

/* HIBRT register (0x0A) special values */
#define MAX17048_HIBRT_DISABLE         0x0000  /* disable hibernate mode        */
#define MAX17048_HIBRT_ALWAYS          0xFFFF  /* always stay in hibernate mode */

/* Public enumerate/structure ----------------------------------------------- */

/**
 * @brief Return codes
 */
typedef enum
{
  MAX17048_OK          =  0,
  MAX17048_ERR         = -1,
  MAX17048_ERR_BUS     = -2,
  MAX17048_ERR_TIMEOUT = -3,
  MAX17048_ERR_PARAM   = -4,
  MAX17048_ERR_NO_DEV  = -5,
} max17048_err_t;

/**
 * @brief Empty alert threshold
 * @note  Datasheet p.12: ATHD[4:0] stored as (32 - threshold_pct)
 *        Alert fires on falling edge when SOC drops below chosen level
 */
typedef enum
{
  MAX17048_EMPTY_ALERT_32PCT = 0,
  MAX17048_EMPTY_ALERT_25PCT = 7,
  MAX17048_EMPTY_ALERT_20PCT = 12,
  MAX17048_EMPTY_ALERT_15PCT = 17,
  MAX17048_EMPTY_ALERT_10PCT = 22,
  MAX17048_EMPTY_ALERT_5PCT  = 27,
  MAX17048_EMPTY_ALERT_4PCT  = 28,  /* default after POR */
  MAX17048_EMPTY_ALERT_3PCT  = 29,
  MAX17048_EMPTY_ALERT_2PCT  = 30,
  MAX17048_EMPTY_ALERT_1PCT  = 31,
} max17048_empty_alert_t;

/**
 * @brief Raw fuel-gauge data — internal use by driver and BSP only
 * @note  Upper layers should use bsp_battery_data_t, not this struct directly
 */
typedef struct
{
  uint16_t voltage_mv;     /* Cell voltage in mV                              */
  uint8_t  soc_pct;        /* State of charge, integer 0-100 %               */
  uint8_t  soc_frac;       /* SOC fractional part 0-255, unit 1/256 %        */
  int16_t  crate_phr;    /* Charge rate in m%/hr, negative = discharge */
  bool     is_hibernating; /* true when device is in hibernate mode           */
  bool     alert_active;   /* true when ALRT pin is asserted                  */
  uint16_t status_reg;     /* raw STATUS register value                       */
} max17048_data_t;

/**
 * @brief Device configuration
 */
typedef struct
{
  uint8_t                rcomp;               /* RCOMP compensation, default 0x97         */
  max17048_empty_alert_t empty_alert;         /* SOC threshold to fire empty alert        */
  uint16_t               valrt_max_mv;        /* overvoltage alert threshold in mV        */
  uint16_t               valrt_min_mv;        /* undervoltage alert threshold in mV       */
  uint16_t               vreset_mv;           /* battery swap reset threshold in mV       */
  bool                   en_soc_change_alert; /* alert on every 1% SOC change             */
  bool                   en_vreset_alert;     /* assert ALRT on voltage reset event       */
  bool                   dis_hibernate_comp;  /* disable comparator in hibernate (~0.5uA) */
} max17048_config_t;

/**
 * @brief I2C interface — fill with your BSP functions
 * @note  pwm_set_duty and get_tick_ms are NOT here — LED and timing
 *        are hardware concerns owned by the BSP layer, not the driver
 */
typedef struct
{
  /* Write len bytes to reg_addr, return 0 on success */
  int32_t (*i2c_write)(uint8_t dev_addr, uint8_t reg_addr,
                        const uint8_t *data, uint16_t len);

  /* Read len bytes from reg_addr, return 0 on success */
  int32_t (*i2c_read) (uint8_t dev_addr, uint8_t reg_addr,
                        uint8_t *data, uint16_t len);
} max17048_bus_t;

/**
 * @brief Driver instance — one per physical IC
 */
typedef struct
{
  max17048_bus_t    bus;    /* hardware interface, fill before calling init */
  max17048_config_t config; /* active configuration                         */
  bool              ready;  /* true after successful init                   */
} max17048_dev_t;

/* Public function prototypes ----------------------------------------------- */

/**
 * @brief  Initialize MAX17048, verify presence, clear reset flag, apply config
 * @param  dev     Driver instance (bus must be filled by caller)
 * @param  config  Configuration, pass NULL to use defaults
 * @return MAX17048_OK on success
 */
max17048_err_t max17048_init(max17048_dev_t *dev, const max17048_config_t *config);

/**
 * @brief  Fill config struct with safe default values
 * @param  config  Output struct to populate
 */
void max17048_default_config(max17048_config_t *config);

/**
 * @brief  Read voltage, SOC, CRATE and status in one call
 * @param  dev   Driver instance
 * @param  data  Output data struct
 * @return MAX17048_OK on success
 */
max17048_err_t max17048_read_all(max17048_dev_t *dev, max17048_data_t *data);

/**
 * @brief  Read cell voltage
 * @param  dev        Driver instance
 * @param  voltage_mv Output: voltage in mV
 * @return MAX17048_OK on success
 */
max17048_err_t max17048_read_voltage(max17048_dev_t *dev, uint16_t *voltage_mv);

/**
 * @brief  Read state of charge, integer percent
 * @param  dev     Driver instance
 * @param  soc_pct Output: SOC 0-100 %
 * @return MAX17048_OK on success
 */
max17048_err_t max17048_read_soc(max17048_dev_t *dev, uint8_t *soc_pct);

/**
 * @brief  Read SOC with 1/256% fractional resolution
 * @param  dev      Driver instance
 * @param  soc_pct  Output: integer SOC 0-100 %
 * @param  soc_frac Output: fractional part 0-255, unit 1/256 %
 * @return MAX17048_OK on success
 */
max17048_err_t max17048_read_soc_full(max17048_dev_t *dev, uint8_t *soc_pct, uint8_t *soc_frac);

/**
 * @brief  Read approximate charge/discharge rate
 * @param  dev         Driver instance
 * @param  crate_phr Output: rate in m%/hr, negative = discharging
 * @return MAX17048_OK on success
 */
max17048_err_t max17048_read_crate(max17048_dev_t *dev, int16_t *crate_phr);

/**
 * @brief  Read STATUS register, optionally clear alert bits
 * @param  dev         Driver instance
 * @param  status      Output: raw STATUS register value
 * @param  clear_flags Bitmask of bits to clear, 0 = do not clear anything
 * @return MAX17048_OK on success
 */
max17048_err_t max17048_read_status(max17048_dev_t *dev, uint16_t *status, uint16_t clear_flags);

/**
 * @brief  Check device presence by reading VERSION register
 * @param  dev  Driver instance
 * @return true if device responds with correct version
 */
bool max17048_is_present(max17048_dev_t *dev);

/**
 * @brief  Trigger quick-start, re-estimates SOC from current voltage
 * @param  dev  Driver instance
 * @note   Use with caution, see datasheet Quick-Start section
 * @return MAX17048_OK on success
 */
max17048_err_t max17048_quick_start(max17048_dev_t *dev);

/**
 * @brief  Send power-on reset command, restores all registers to defaults
 * @param  dev  Driver instance
 * @note   IC does not ACK after this command — expected behavior
 * @return MAX17048_OK on success
 */
max17048_err_t max17048_por(max17048_dev_t *dev);

/**
 * @brief  Enter sleep mode, halts IC, draws less than 1 uA
 * @param  dev  Driver instance
 * @return MAX17048_OK on success
 */
max17048_err_t max17048_enter_sleep(max17048_dev_t *dev);

/**
 * @brief  Exit sleep mode
 * @param  dev  Driver instance
 * @return MAX17048_OK on success
 */
max17048_err_t max17048_exit_sleep(max17048_dev_t *dev);

/**
 * @brief  Force device into hibernate mode (HIBRT = 0xFFFF)
 * @param  dev  Driver instance
 * @return MAX17048_OK on success
 */
max17048_err_t max17048_force_hibernate(max17048_dev_t *dev);

/**
 * @brief  Disable hibernate mode entirely (HIBRT = 0x0000)
 * @param  dev  Driver instance
 * @return MAX17048_OK on success
 */
max17048_err_t max17048_disable_hibernate(max17048_dev_t *dev);

/**
 * @brief  Update RCOMP value for temperature compensation
 * @param  dev       Driver instance
 * @param  temp_degc Battery temperature in degrees Celsius
 * @note   Call at least once per minute for best accuracy
 * @return MAX17048_OK on success
 */
max17048_err_t max17048_update_temp_comp(max17048_dev_t *dev, int8_t temp_degc);

/**
 * @brief  Set voltage alert thresholds
 * @param  dev    Driver instance
 * @param  min_mv Undervoltage threshold in mV, resolution 20 mV
 * @param  max_mv Overvoltage threshold in mV, resolution 20 mV
 * @return MAX17048_OK on success
 */
max17048_err_t max17048_set_voltage_alert(max17048_dev_t *dev, uint16_t min_mv, uint16_t max_mv);

/**
 * @brief  Clear ALRT pin by writing CONFIG.ALRT = 0
 * @param  dev  Driver instance
 * @return MAX17048_OK on success
 */
max17048_err_t max17048_clear_alert(max17048_dev_t *dev);

#endif /* __MAX17048_H */

/* End of file -------------------------------------------------------------- */