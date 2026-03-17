/**
 * @file       bsp_battery.c
 * @copyright
 * @license
 * @version    1.1.0
 * @date       2026-03-17
 * @author
 * @brief      BSP layer for MAX17048 fuel gauge
 * @note       Hardware: STM32F411CEUx
 *             I2C3 — SCL: PA8 | SDA: PC9
 */

/* Includes ----------------------------------------------------------------- */
#include "bsp_battery.h"
#include "max17048.h"

/* Private variables -------------------------------------------------------- */
extern I2C_HandleTypeDef BSP_BATTERY_I2C_HANDLE;   /* hi2c3 from main.c */

static max17048_dev_t s_dev;

static const max17048_config_t s_lipo_cfg =
{
  .rcomp               = 0x97,
  .empty_alert         = (max17048_empty_alert_t)(32 - BSP_BATTERY_EMPTY_ALERT_PCT),
  .valrt_min_mv        = BSP_BATTERY_VALRT_MIN_MV,
  .valrt_max_mv        = BSP_BATTERY_VALRT_MAX_MV,
  .vreset_mv           = BSP_BATTERY_VRESET_MV,
  .en_soc_change_alert = false,
  .en_vreset_alert     = true,
  .dis_hibernate_comp  = false,
};

/* Private function prototypes ---------------------------------------------- */
static int32_t s_i2c_write(uint8_t dev_addr, uint8_t reg_addr,
                            const uint8_t *data, uint16_t len);
static int32_t s_i2c_read (uint8_t dev_addr, uint8_t reg_addr,
                            uint8_t *data, uint16_t len);

static bsp_battery_err_t s_map_err(max17048_err_t err);
static void              s_handle_alerts(void);

/* Public function implementation ------------------------------------------- */

bsp_battery_err_t bsp_battery_init(void)
{
  s_dev.bus.i2c_write = s_i2c_write;
  s_dev.bus.i2c_read  = s_i2c_read;

  max17048_err_t err = max17048_init(&s_dev, &s_lipo_cfg);
  if (err != MAX17048_OK)
    return s_map_err(err);

  /*
   * Apply temperature compensation once after init
   * BSP_BATTERY_TEMP_DEGC is fixed at 40°C — nearby ICs run warm
   * Update later if a temperature sensor is added
   */
  max17048_update_temp_comp(&s_dev, BSP_BATTERY_TEMP_DEGC);

  return BSP_BATTERY_OK;
}

bsp_battery_err_t bsp_battery_read(bsp_battery_data_t *data)
{
  if (!data)
    return BSP_BATTERY_ERR_PARAM;

  /*
   * Read raw data from driver into internal buffer
   * Only copy what upper layers need into bsp_battery_data_t
   * alert_active, is_hibernating, status_reg are handled here
   */
  max17048_data_t raw;

  max17048_err_t err = max17048_read_all(&s_dev, &raw);
  if (err != MAX17048_OK)
    return s_map_err(err);

  data->voltage_mv  = raw.voltage_mv;
  data->soc_pct     = raw.soc_pct;
  data->soc_frac    = raw.soc_frac;
  data->crate_mphph = raw.crate_mphph;

  /* Handle alerts internally — upper layers do not need to know */
  if (raw.alert_active)
    s_handle_alerts();

  return BSP_BATTERY_OK;
}

bool bsp_battery_is_present(void)
{
  return max17048_is_present(&s_dev);
}

/* Private function implementation ------------------------------------------ */

static int32_t s_i2c_write(uint8_t dev_addr, uint8_t reg_addr,
                            const uint8_t *data, uint16_t len)
{
  /*
   * HAL expects 8-bit address — driver passes 7-bit (0x36)
   * Shift left 1 here: 0x36 << 1 = 0x6C
   */
  if (HAL_I2C_Mem_Write(&BSP_BATTERY_I2C_HANDLE,
                         (uint16_t)(dev_addr << 1),
                         reg_addr,
                         I2C_MEMADD_SIZE_8BIT,
                         (uint8_t *)data,
                         len,
                         BSP_BATTERY_I2C_TIMEOUT_MS) == HAL_OK)
    return 0;

  return -1;
}

static int32_t s_i2c_read(uint8_t dev_addr, uint8_t reg_addr,
                           uint8_t *data, uint16_t len)
{
  if (HAL_I2C_Mem_Read(&BSP_BATTERY_I2C_HANDLE,
                        (uint16_t)(dev_addr << 1),
                        reg_addr,
                        I2C_MEMADD_SIZE_8BIT,
                        data,
                        len,
                        BSP_BATTERY_I2C_TIMEOUT_MS) == HAL_OK)
    return 0;

  return -1;
}

static bsp_battery_err_t s_map_err(max17048_err_t err)
{
  switch (err)
  {
    case MAX17048_OK:        return BSP_BATTERY_OK;
    case MAX17048_ERR_BUS:   return BSP_BATTERY_ERR_BUS;
    case MAX17048_ERR_PARAM: return BSP_BATTERY_ERR_PARAM;
    case MAX17048_ERR_NO_DEV:return BSP_BATTERY_ERR_NO_DEV;
    default:                 return BSP_BATTERY_ERR;
  }
}

static void s_handle_alerts(void)
{
  /*
   * Read STATUS register to identify which alert fired
   * Handle each condition internally, then clear the alert flag
   */
  uint16_t status = 0;
  if (max17048_read_status(&s_dev, &status, 0) != MAX17048_OK)
    return;

  if (status & MAX17048_STATUS_VR)
  {
    /*
     * Voltage reset — battery was removed and a new one inserted
     * IC has already re-estimated SOC automatically
     * Nothing else needed here — just acknowledge
     */
  }

  if (status & MAX17048_STATUS_HD)
  {
    /*
     * SOC dropped below empty alert threshold (10%)
     * Add low battery handling here if needed
     * e.g. set a flag, trigger a callback, log an event
     */
  }

  if (status & MAX17048_STATUS_VL)
  {
    /*
     * Voltage dropped below VALRT.MIN (3000mV)
     * Battery critically low
     */
  }

  if (status & MAX17048_STATUS_VH)
  {
    /*
     * Voltage exceeded VALRT.MAX (4200mV)
     * Battery overcharge detected
     */
  }

  /* Clear ALRT flag in CONFIG register to deassert ALRT pin */
  max17048_clear_alert(&s_dev);
}

/* End of file -------------------------------------------------------------- */