/**
 * @file       bsp_battery.c
 * @copyright
 * @license
 * @version    1.0.0
 * @date       2026-03-14
 * @author     Trung Quan
 * @brief      BSP layer for MAX17048 fuel gauge
 * @note       Hardware: STM32F411CEUx
 *             I2C3  — SCL: PA8 | SDA: PC9
 *             LED   — PWM on PC13 via TIM1_CH3
 */

/* Includes ----------------------------------------------------------------- */
#include "bsp_battery.h"

/* Public variables --------------------------------------------------------- */
extern I2C_HandleTypeDef BSP_BATTERY_I2C_HANDLE;   /* hi2c3 from main.c  */
extern TIM_HandleTypeDef BSP_BATTERY_TIM_HANDLE;   /* htim1 from main.c  */

/* Private variables -------------------------------------------------------- */
static max17048_dev_t s_battery_dev;

static const max17048_config_t s_lipo_cfg =
{
  .rcomp               = 0x97,
  .empty_alert         = BSP_BATTERY_EMPTY_ALERT,
  .valrt_min_mv        = BSP_BATTERY_VALRT_MIN_MV,
  .valrt_max_mv        = BSP_BATTERY_VALRT_MAX_MV,
  .vreset_mv           = BSP_BATTERY_VRESET_MV,
  .en_soc_change_alert = false,
  .en_vreset_alert     = true,   /* alert when battery is swapped */
  .dis_hibernate_comp  = false,
};

/* Private function prototypes ---------------------------------------------- */
static int32_t  s_i2c_write   (uint8_t dev_addr, uint8_t reg_addr,
                                const uint8_t *data, uint16_t len);
static int32_t  s_i2c_read    (uint8_t dev_addr, uint8_t reg_addr,
                                uint8_t *data, uint16_t len);
static void     s_pwm_set_duty(uint8_t duty_pct);

/* Public function implementation ------------------------------------------- */

bsp_battery_err_t bsp_battery_init(void)
{
  /* Bind HAL functions to driver interface */
  s_battery_dev.bus.i2c_write    = s_i2c_write;
  s_battery_dev.bus.i2c_read     = s_i2c_read;
  s_battery_dev.bus.pwm_set_duty = s_pwm_set_duty;
  s_battery_dev.bus.get_tick_ms  = HAL_GetTick;

  /* Start PWM on PC13 before init so LED can indicate status */
  HAL_TIM_PWM_Start(&BSP_BATTERY_TIM_HANDLE, BSP_BATTERY_TIM_CHANNEL);

  /* Initialize driver with lipo 1-cell config */
  return max17048_init(&s_battery_dev, &s_lipo_cfg);
}

bsp_battery_err_t bsp_battery_read(bsp_battery_data_t *data)
{
  if (!data)
    return BSP_BATTERY_ERR_PARAM;

  return max17048_read_all(&s_battery_dev, data);
}

bsp_battery_err_t bsp_battery_update_led(void)
{
  return max17048_update_led(&s_battery_dev);
}

bsp_battery_err_t bsp_battery_update_temp(int8_t temp_degc)
{
  return max17048_update_temp_comp(&s_battery_dev, temp_degc);
}

bsp_battery_err_t bsp_battery_clear_alert(void)
{
  return max17048_clear_alert(&s_battery_dev);
}

bool bsp_battery_is_present(void)
{
  return max17048_is_present(&s_battery_dev);
}

/* Private function implementation ------------------------------------------ */

static int32_t s_i2c_write(uint8_t dev_addr, uint8_t reg_addr,
                            const uint8_t *data, uint16_t len)
{
  /*
   * HAL expects 8-bit address (7-bit << 1)
   * Driver passes 7-bit address (0x36), shift left 1 here
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

static void s_pwm_set_duty(uint8_t duty_pct)
{
  /*
   * TIM1_CH3 on PC13
   * pulse = (ARR + 1) * duty / 100
   */
  uint32_t period = BSP_BATTERY_TIM_HANDLE.Init.Period + 1;
  uint32_t pulse  = (period * duty_pct) / 100;

  __HAL_TIM_SET_COMPARE(&BSP_BATTERY_TIM_HANDLE,
                         BSP_BATTERY_TIM_CHANNEL,
                         pulse);
}

/* End of file -------------------------------------------------------------- */