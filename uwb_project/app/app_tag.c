/* ============================== app_tag.c ==================================
 * @file       app_tag.c
 * @brief      Tag application - Ranging initiator
 * @version    1.0.0
 * @date       2025-11-15
 */

/* Includes ----------------------------------------------------------- */
#include "app_tag.h"
#include "sys_ranging.h"
#include "sys_config.h"
#include "sys_logger.h"
#include "sys_task.h"

#include <stdint.h>

/* Configuration ------------------------------------------------------ */
#define MAX_CONSECUTIVE_ERR (5)       /* Max errors before reset */

/* Private variables -------------------------------------------------- */
static uint8_t  s_sequence_num = 0;
static uint32_t s_error_count = 0;
static uint32_t s_success_count = 0;
static uint32_t s_last_ranging_tick = 0;

/* Public function definitions ---------------------------------------- */

app_err_t app_tag_init(void)
{
  sys_config_t *cfg = sys_config_get();
  
  RLOG_I(LOG_OBJECT_CODE_TAG, "====== TAG INIT =======");
  RLOG_I(LOG_OBJECT_CODE_TAG, "Device ID: 0x%02X", cfg->device_id);
  RLOG_I(LOG_OBJECT_CODE_TAG, "Method: %s",
         cfg->method == RANGING_DS_TWR ? "DS-TWR" : "TDoA");
  RLOG_I(LOG_OBJECT_CODE_TAG, "UWB Channel: %u", cfg->uwb_channel);
  RLOG_I(LOG_OBJECT_CODE_TAG, "Period: %u ms", cfg->ranging_period_ms);
  RLOG_I(LOG_OBJECT_CODE_TAG, "=======================");

  s_last_ranging_tick = sys_task_get_tick_ms();
  return APP_OK;
}

void app_tag_process(void)
{
  sys_config_t *cfg = sys_config_get();
  uint32_t current_tick = sys_task_get_tick_ms();

  /* Check if it's time for next ranging */
  if ((current_tick - s_last_ranging_tick) >= cfg->ranging_period_ms)
  {
    s_last_ranging_tick = current_tick;

    sys_ranging_config_t config;
    sys_ranging_result_t result;

    /* Prepare ranging config */
    config.sequence_num = s_sequence_num++;
    config.rx_timeout_us = 100000;  /* 100ms */

    /* Execute ranging */
    sys_ranging_err_t err = sys_ranging_tag_once(&config, &result);

    if (err == SYS_RANGING_OK)
    {
      /* Success */
      s_success_count++;
      s_error_count = 0;

      RLOG_I(LOG_OBJECT_CODE_TAG, "Ranging OK (seq=%u)", config.sequence_num);
    }
    else if (err == SYS_RANGING_ERR_TIMEOUT)
    {
      /* Timeout */
      RLOG_W(LOG_OBJECT_CODE_TAG, "Timeout (seq=%u)", config.sequence_num);
      s_error_count++;
    }
    else
    {
      /* Error */
      RLOG_E(LOG_OBJECT_CODE_TAG, ERR_UWB_RANGING, "Error %d (seq=%u)", 
             err, config.sequence_num);
      s_error_count++;
      s_success_count = 0;
    }

    /* Check error count */
    if (s_error_count >= MAX_CONSECUTIVE_ERR)
    {
      RLOG_E(LOG_OBJECT_CODE_TAG, ERR_TIMEOUT, 
             "Too many errors (%lu), resetting...", s_error_count);
      s_error_count = 0;
    }
  }

    /* Small delay */
    HAL_Delay(10);
  }
}

/* End of file -------------------------------------------------------- */
