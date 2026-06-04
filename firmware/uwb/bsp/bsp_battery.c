/**
 * @file       bsp_battery.c
 * @brief      Battery BSP for MAX17048 fuel gauge (Data Provider)
 */

#include "bsp_battery.h"
#include "max17048.h"
#include "sys_logger.h"

/* I2C Configuration */
#define I2C_TIMEOUT_MS             100
#define TEMP_COMP_DEGC             40
#define EMPTY_ALERT_PCT            10
#define CRATE_IDLE_THRESHOLD       20  /* m%/h */

/* Private variables -------------------------------------------------------- */
extern I2C_HandleTypeDef hi2c1;
#define BSP_BATTERY_I2C_HANDLE hi2c1

static max17048_dev_t  s_dev;
static max17048_data_t s_bat;
static uint16_t        s_hw_alerts = 0;
static bool            s_initialized = false;

static const max17048_config_t s_lipo_cfg = {
  .rcomp               = 0x97,
  .empty_alert         = (max17048_empty_alert_t) (32 - EMPTY_ALERT_PCT),
  .valrt_min_mv        = 3000,
  .valrt_max_mv        = 4200,
  .vreset_mv           = 2400,
  .en_soc_change_alert = false,
  .en_vreset_alert     = true,
  .dis_hibernate_comp  = false,
};

/* Private function prototypes ---------------------------------------------- */
static int32_t s_i2c_write(uint8_t dev_addr, uint8_t reg_addr, const uint8_t *data, uint16_t len);
static int32_t s_i2c_read(uint8_t dev_addr, uint8_t reg_addr, uint8_t *data, uint16_t len);
static void    s_handle_hw_alerts(void);

/* Public function implementation ------------------------------------------- */

bsp_battery_err_t bsp_battery_init(void)
{
  s_dev.bus.i2c_write = s_i2c_write;
  s_dev.bus.i2c_read  = s_i2c_read;

  max17048_err_t init_err = max17048_init(&s_dev, &s_lipo_cfg);
  if (init_err != MAX17048_OK)
  {
    s_initialized = false;
    
    // diagnostic read on failure
    uint8_t ver_buf[2] = {0, 0};
    int32_t read_ret = s_i2c_read(MAX17048_I2C_ADDR, MAX17048_REG_VERSION, ver_buf, 2);
    uint16_t ver_val = ((uint16_t)ver_buf[0] << 8) | ver_buf[1];
    
    RLOG_E(LOG_OBJECT_CODE_APPLICATION, ERR_BATTERY_INIT,
           "BAT DIAG: init failed with code %d. Direct I2C VERSION read: status=%ld, val=0x%04X.",
           init_err, read_ret, ver_val);
           
    return BSP_BATTERY_ERR;
  }

  max17048_update_temp_comp(&s_dev, TEMP_COMP_DEGC);
  s_initialized = true;
  return BSP_BATTERY_OK;
}

bsp_battery_err_t bsp_battery_task(void)
{
  if (!s_initialized)
  {
    return BSP_BATTERY_ERR;
  }

  if (max17048_read_all(&s_dev, &s_bat) != MAX17048_OK)
  {
    return BSP_BATTERY_ERR;
  }

  /* Read and parse the STATUS register if ALRT pin is active */
  if (s_bat.alert_active)
  {
    s_handle_hw_alerts();
  }
  else
  {
    s_hw_alerts = 0;
  }

  return BSP_BATTERY_OK;
}

uint16_t bsp_battery_get_voltage(void)
{
  if (!s_initialized)
  {
    return 0;
  }
  return s_bat.voltage_mv;
}

uint8_t bsp_battery_get_soc(void)
{
  if (!s_initialized)
  {
    return 0;
  }
  return s_bat.soc_pct;
}

int16_t bsp_battery_get_crate(void)
{
  if (!s_initialized)
  {
    return 0;
  }
  return s_bat.crate_phr;
}

uint16_t bsp_battery_get_hw_alerts(void)
{
  if (!s_initialized)
  {
    return 0;
  }
  return s_hw_alerts;
}

void bsp_battery_set_thresholds(uint16_t min_mv, uint16_t max_mv)
{
  if (s_initialized)
  {
    max17048_set_voltage_alert(&s_dev, min_mv, max_mv);
  }
}

int32_t bsp_battery_get_remaining_time(void)
{
  if (!s_initialized)
  {
    return INT32_MIN;
  }

  int16_t crate = s_bat.crate_phr;
  uint8_t soc   = s_bat.soc_pct;

  if (crate > -CRATE_IDLE_THRESHOLD && crate < CRATE_IDLE_THRESHOLD)
    return INT32_MIN; /* Idle — indeterminate */

  if (crate > 0)
    /* Convention: positive = discharging to empty, negative = charging to full. */
    return -((int32_t) (100 - soc) * 60) / (int32_t) crate;
  else
    return ((int32_t) soc * 60) / (int32_t) (-crate);
}

bool bsp_battery_is_present(void)
{
  if (!s_initialized)
  {
    return false;
  }
  return max17048_is_present(&s_dev);
}

bool bsp_battery_is_initialized(void)
{
  return s_initialized;
}

/* Private function implementation ------------------------------------------ */

static void s_handle_hw_alerts(void)
{
  uint16_t status = 0;
  if (max17048_read_status(&s_dev, &status, 0) != MAX17048_OK)
  {
    return;
  }

  uint16_t alerts = 0;
  if (status & MAX17048_STATUS_VR) alerts |= BAT_HW_ALRT_RESET;
  if (status & MAX17048_STATUS_HD) alerts |= BAT_HW_ALRT_SOC_LOW;
  if (status & MAX17048_STATUS_VL) alerts |= BAT_HW_ALRT_VOLT_LOW;
  if (status & MAX17048_STATUS_VH) alerts |= BAT_HW_ALRT_VOLT_HIGH;

  s_hw_alerts = alerts;
  max17048_clear_alert(&s_dev);
}

static int32_t s_i2c_write(uint8_t dev_addr, uint8_t reg_addr, const uint8_t *data, uint16_t len)
{
  HAL_StatusTypeDef ret = HAL_I2C_Mem_Write(&BSP_BATTERY_I2C_HANDLE, (uint16_t) (dev_addr << 1), reg_addr,
                                            I2C_MEMADD_SIZE_8BIT, (uint8_t *) data, len, I2C_TIMEOUT_MS);
  return (ret == HAL_OK) ? 0 : -1;
}

static int32_t s_i2c_read(uint8_t dev_addr, uint8_t reg_addr, uint8_t *data, uint16_t len)
{
  HAL_StatusTypeDef ret = HAL_I2C_Mem_Read(&BSP_BATTERY_I2C_HANDLE, (uint16_t) (dev_addr << 1), reg_addr,
                                           I2C_MEMADD_SIZE_8BIT, data, len, I2C_TIMEOUT_MS);
  return (ret == HAL_OK) ? 0 : -1;
}
