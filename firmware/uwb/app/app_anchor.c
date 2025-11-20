/* ============================== app_anchor.c ===============================
 * @file       app_anchor.c
 * @brief      Anchor application - Ranging responder
 * @version    1.0.0
 * @date       2025-11-15
 */

/* Includes ----------------------------------------------------------- */
#include "app_anchor.h"
#include "sys_ranging.h"
#include "sys_config.h"
#include "sys_logger.h"

#include <stdint.h>

/* Configuration ------------------------------------------------------ */
#define MAX_CONSECUTIVE_ERR (5)       /* Max errors before reset */

/* Private variables -------------------------------------------------- */
static uint8_t  s_sequence_num = 0;
static uint32_t s_error_count = 0;
static uint32_t s_success_count = 0;

/* Public function definitions ---------------------------------------- */

app_err_t app_anchor_init(void)
{
  sys_config_t *cfg = sys_config_get();
  
  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "===== ANCHOR INIT =====");
  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "Device ID: 0x%02X", cfg->device_id);
  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "Method: %s", 
         cfg->method == RANGING_DS_TWR ? "DS-TWR" : "TDoA");
  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "UWB Channel: %u", cfg->uwb_channel);
  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "=======================");

  return APP_OK;
}

void app_anchor_process(void)
{
  sys_ranging_config_t config;
  sys_ranging_result_t result;

  /* Prepare ranging config */
  config.sequence_num = s_sequence_num++;
  config.rx_timeout_us = 100000;  /* 100ms */

  /* Execute ranging */
  sys_ranging_err_t err = sys_ranging_anchor_once(&config, &result);

  if (err == SYS_RANGING_OK && result.valid)
  {
    /* Success */
    s_success_count++;
    s_error_count = 0;

    RLOG_I(LOG_OBJECT_CODE_ANCHOR, "Distance: %.3f m (seq=%u)", 
           result.distance_m, config.sequence_num);
  }
  else if (err == SYS_RANGING_ERR_TIMEOUT)
  {
    /* Timeout (normal - waiting for tag) */
    /* Don't log to avoid spam */
  }
  else
  {
    /* Error */
    s_error_count++;
    s_success_count = 0;

    if (s_error_count >= MAX_CONSECUTIVE_ERR)
    {
      RLOG_E(LOG_OBJECT_CODE_ANCHOR, ERR_TIMEOUT, 
             "Too many errors (%lu), resetting...", s_error_count);
      
      s_error_count = 0;
    }
  }
}

/* End of file -------------------------------------------------------- */
