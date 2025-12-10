/* ============================== sys_ranging.c ==============================
 * @file       sys_ranging.c
 * @brief      System-level ranging with runtime method selection
 * @version    3.0.0
 * @date       2025-11-15
 */

/* Includes ----------------------------------------------------------- */
#include "sys_ranging.h"
#include "sys_config.h"
#include "sys_logger.h"
#include "bsp_uwb.h"
#include "mw_ds_twr.h"

#include "stm32f4xx_hal.h"
#include <stdint.h>
#include <stddef.h>

/* DecaWave driver for direct register access */
#include "deca_driver/deca_device_api.h"
#include "deca_driver/deca_regs.h"

/* Private defines ---------------------------------------------------- */
#define UWB_RX_BUFFER_SIZE (128u)

/* Private variables -------------------------------------------------- */
static uint8_t s_rx_buffer[UWB_RX_BUFFER_SIZE];

/* Private function prototypes --------------------------------------- */
static int hal_uwb_tx(const void *data, uint16_t length);
static int hal_uwb_rx_with_timeout(uint8_t *buffer, uint16_t buffer_size,
                                   uint16_t *received_length, uint32_t timeout_us);
static int hal_uwb_read_timestamp(uint8_t reg_addr, uint8_t sub_addr, uint64_t *timestamp);
static uint32_t hal_get_tick_ms(void);

/* HAL callbacks for ranging middleware ------------------------------ */
static const mw_dstwr_hal_t s_hal_callbacks = {
  .tx = hal_uwb_tx,
  .rx_with_timeout = hal_uwb_rx_with_timeout,
  .read_timestamp = hal_uwb_read_timestamp,
  .get_tick_ms = hal_get_tick_ms
};

/* Public function definitions ---------------------------------------- */

sys_ranging_err_t sys_ranging_tag_once(const sys_ranging_config_t *config,
                                       sys_ranging_result_t *result)
{
  if (!config)
    return SYS_RANGING_ERR_PARAM;

  sys_config_t *sys_cfg = sys_config_get();
  const char *method_name = (sys_cfg->method == RANGING_DS_TWR) ? "DS-TWR" : "TDoA";
  
  RLOG_D(LOG_OBJECT_CODE_RANGING, "[TAG] Start %s seq=%u", method_name, config->sequence_num);

  if (sys_cfg->method == RANGING_DS_TWR)
  {
    /* DS-TWR Tag */
    mw_dstwr_config_t mw_config = {
      .sequence_num = config->sequence_num,
      .rx_timeout_us = config->rx_timeout_us,
      .hal = &s_hal_callbacks
    };

    mw_dstwr_result_t mw_result = {0};
    mw_dstwr_err_t mw_err = mw_dstwr_execute_tag(&mw_config, &mw_result);

  if (mw_err == MW_DSTWR_OK)
  {
    RLOG_D(LOG_OBJECT_CODE_TWR, "[TAG] Complete T1=0x%010llX T4=0x%010llX T5=0x%010llX",
           mw_result.timestamps.t1, mw_result.timestamps.t4, mw_result.timestamps.t5);

    if (result)
    {
      result->distance_m = mw_result.distance_m;
      result->t1 = mw_result.timestamps.t1;
      result->t2 = mw_result.timestamps.t2;
      result->t3 = mw_result.timestamps.t3;
      result->t4 = mw_result.timestamps.t4;
      result->t5 = mw_result.timestamps.t5;
      result->t6 = mw_result.timestamps.t6;
      result->valid = mw_result.valid;
    }
    return SYS_RANGING_OK;
  }
  else if (mw_err == MW_DSTWR_ERR_TIMEOUT)
  {
    RLOG_W(LOG_OBJECT_CODE_TWR, "[TAG] Timeout");
    return SYS_RANGING_ERR_TIMEOUT;
  }
  else if (mw_err == MW_DSTWR_ERR_INVALID_MSG)
  {
    RLOG_W(LOG_OBJECT_CODE_TWR, "[TAG] Invalid message");
    return SYS_RANGING_ERR_PROTO;
  }
  else
  {
    RLOG_E(LOG_OBJECT_CODE_TWR, ERR_UWB_RANGING, "[TAG] Error %d", mw_err);
      return SYS_RANGING_ERR;
    }
  }
  else /* TDoA */
  {
    RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UNDEFINED, "TDoA not implemented");
    return SYS_RANGING_ERR;
  }
}

sys_ranging_err_t sys_ranging_anchor_once(const sys_ranging_config_t *config,
                                          sys_ranging_result_t *result)
{
  if (!config)
    return SYS_RANGING_ERR_PARAM;

  sys_config_t *sys_cfg = sys_config_get();
  const char *method_name = (sys_cfg->method == RANGING_DS_TWR) ? "DS-TWR" : "TDoA";
  
  RLOG_D(LOG_OBJECT_CODE_RANGING, "[ANCHOR] Waiting %s", method_name);

  if (sys_cfg->method == RANGING_DS_TWR)
  {
    /* DS-TWR Anchor */
    mw_dstwr_config_t mw_config = {
      .sequence_num = config->sequence_num,
      .rx_timeout_us = config->rx_timeout_us,
      .hal = &s_hal_callbacks
    };

    mw_dstwr_result_t mw_result = {0};
    mw_dstwr_err_t mw_err = mw_dstwr_execute_anchor(&mw_config, &mw_result);

  if (mw_err == MW_DSTWR_OK && mw_result.valid)
  {
    RLOG_I(LOG_OBJECT_CODE_TWR, "[ANCHOR] Distance: %.3f m", mw_result.distance_m);
    RLOG_D(LOG_OBJECT_CODE_TWR, "[ANCHOR] T1=0x%010llX T2=0x%010llX T3=0x%010llX",
           mw_result.timestamps.t1, mw_result.timestamps.t2, mw_result.timestamps.t3);
    RLOG_D(LOG_OBJECT_CODE_TWR, "[ANCHOR] T4=0x%010llX T5=0x%010llX T6=0x%010llX",
           mw_result.timestamps.t4, mw_result.timestamps.t5, mw_result.timestamps.t6);

    if (result)
    {
      result->distance_m = mw_result.distance_m;
      result->t1 = mw_result.timestamps.t1;
      result->t2 = mw_result.timestamps.t2;
      result->t3 = mw_result.timestamps.t3;
      result->t4 = mw_result.timestamps.t4;
      result->t5 = mw_result.timestamps.t5;
      result->t6 = mw_result.timestamps.t6;
      result->valid = mw_result.valid;
    }
    return SYS_RANGING_OK;
  }
  else if (mw_err == MW_DSTWR_ERR_TIMEOUT)
  {
    RLOG_W(LOG_OBJECT_CODE_TWR, "[ANCHOR] Timeout");
    return SYS_RANGING_ERR_TIMEOUT;
  }
  else if (mw_err == MW_DSTWR_ERR_INVALID_MSG)
  {
    RLOG_W(LOG_OBJECT_CODE_TWR, "[ANCHOR] Invalid message");
    return SYS_RANGING_ERR_PROTO;
  }
  else
  {
    RLOG_E(LOG_OBJECT_CODE_TWR, ERR_UWB_RANGING, "[ANCHOR] Error %d", mw_err);
    return SYS_RANGING_ERR;
  }
  }
  else /* TDoA */
  {
    RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UNDEFINED, "TDoA not implemented");
    return SYS_RANGING_ERR;
  }
}

/* Private function definitions --------------------------------------- */

/* Hardware abstraction layer implementations ------------------------ */

static int hal_uwb_tx(const void *data, uint16_t length)
{
  bsp_err_t bsp_err = bsp_uwb_tx(data, length);
  if (bsp_err != BSP_OK)
  {
    RLOG_E(LOG_OBJECT_CODE_UWB_DRIVER, ERR_UWB_TX, "UWB TX failed");
    return -1;
  }
  return 0;
}

static int hal_uwb_rx_with_timeout(uint8_t *buffer, uint16_t buffer_size,
                                   uint16_t *received_length, uint32_t timeout_us)
{
  if (!buffer || !received_length || buffer_size == 0)
    return -1;

  uint32_t start_ms = HAL_GetTick();
  
  for (;;)
  {
    if (bsp_uwb_rx(buffer, buffer_size, received_length) == BSP_OK)
      return 0;

    if ((HAL_GetTick() - start_ms) * 1000UL > timeout_us)
    {
      RLOG_D(LOG_OBJECT_CODE_UWB_DRIVER, "UWB RX timeout");
      return -1;
    }
  }
}

static int hal_uwb_read_timestamp(uint8_t reg_addr, uint8_t sub_addr, uint64_t *timestamp)
{
  if (!timestamp)
    return -1;

  /* Use deca driver API directly to read 40-bit timestamp */
  uint8_t buf[5];
  dwt_readfromdevice(reg_addr, sub_addr, 5, buf);
  
  *timestamp = ((uint64_t)buf[0]) |
               ((uint64_t)buf[1] << 8) |
               ((uint64_t)buf[2] << 16) |
               ((uint64_t)buf[3] << 24) |
               ((uint64_t)buf[4] << 32);
  
  return 0;
}

static uint32_t hal_get_tick_ms(void)
{
  return HAL_GetTick();
}

/* End of file -------------------------------------------------------- */
