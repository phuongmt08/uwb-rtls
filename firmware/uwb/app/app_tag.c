/* ============================== app_tag.c ==================================
 * @file       app_tag.c
 * @brief      Non-blocking Tag application
 * @version    2.0.0
 * @date       2025-11-26
 */

/* Includes ----------------------------------------------------------- */
#include "app_tag.h"
#include "sys_ranging.h"
#include "sys_config.h"
#include "sys_logger.h"
#include "bsp_io.h"
#include "bsp_util.h"
#include <stdint.h>

/* Configuration ------------------------------------------------------ */
#define MAX_CONSECUTIVE_ERR (5)

/* Private types ------------------------------------------------------ */
typedef enum {
  TAG_STATE_IDLE = 0,
  TAG_STATE_WAIT_PERIOD,
  TAG_STATE_RANGING,
  TAG_STATE_GET_RESULT
} tag_app_state_t;

/* Private variables -------------------------------------------------- */
static uint8_t  s_sequence_num = 0;
static uint32_t s_error_count = 0;
static uint32_t s_last_ranging_tick = 0;
static tag_app_state_t s_app_state = TAG_STATE_IDLE;

/* Public function definitions ---------------------------------------- */

app_err_t app_tag_init(void)
{
  sys_config_t *cfg = sys_config_get();
  
  RLOG_I(LOG_OBJECT_CODE_TAG, "====== TAG INIT =======");
  RLOG_I(LOG_OBJECT_CODE_TAG, "Device ID: 0x%02X", cfg->device_id);
  RLOG_I(LOG_OBJECT_CODE_TAG, "Method: DS-TWR");
  RLOG_I(LOG_OBJECT_CODE_TAG, "Period: %u ms", cfg->ranging_period_ms);
  RLOG_I(LOG_OBJECT_CODE_TAG, "=======================");

  s_last_ranging_tick = HAL_GetTick();
  s_app_state = TAG_STATE_WAIT_PERIOD;
  return APP_OK;
}

void app_tag_process(void)
{
  sys_config_t *cfg = sys_config_get();
  uint32_t current_tick = HAL_GetTick();

  switch (s_app_state) {
    
    case TAG_STATE_IDLE:
      s_app_state = TAG_STATE_WAIT_PERIOD;
      break;
    
    case TAG_STATE_WAIT_PERIOD:
      /* Check if it's time for next ranging */
      if ((current_tick - s_last_ranging_tick) >= cfg->ranging_period_ms) {
        s_last_ranging_tick = current_tick;
        sys_ranging_err_t err = sys_ranging_tag_start(s_sequence_num++, 100);
        if (err == SYS_RANGING_OK) {
          s_app_state = TAG_STATE_RANGING;
          RLOG_D(LOG_OBJECT_CODE_TAG, "[TAG] Ranging started seq=%u", s_sequence_num - 1);
        } else if (err == SYS_RANGING_ERR_BUSY) {
          RLOG_W(LOG_OBJECT_CODE_TAG, "[TAG] Ranging busy, skip this cycle");
        } else {
          RLOG_E(LOG_OBJECT_CODE_TAG, ERR_UWB_RANGING, "[TAG] Start failed: %d", err);
          s_error_count++;
        }
      }
      break;
    
    case TAG_STATE_RANGING: {
      /* Process state machine */
      sys_ranging_err_t err = sys_ranging_tag_process();
      
      if (err == SYS_RANGING_OK) {
        /* Ranging complete */
        s_app_state = TAG_STATE_GET_RESULT;
      } else if (err == SYS_RANGING_ERR_BUSY) {
        /* Still processing - do nothing, will call again next cycle */
      } else {
        /* Error occurred */
        if (err == SYS_RANGING_ERR_TIMEOUT) {
          RLOG_W(LOG_OBJECT_CODE_TAG, "[TAG] Timeout");
        } else {
          RLOG_E(LOG_OBJECT_CODE_TAG, ERR_UWB_RANGING, "[TAG] Error: %d", err);
        }
        s_error_count++;
        s_app_state = TAG_STATE_WAIT_PERIOD;
        /* Check error count */
        if (s_error_count >= MAX_CONSECUTIVE_ERR) {
          RLOG_E(LOG_OBJECT_CODE_TAG, ERR_TIMEOUT, 
                 "Too many errors (%lu)", s_error_count);
          s_error_count = 0;
        }
      }
      break;
    }
    
    case TAG_STATE_GET_RESULT: {
      sys_ranging_result_t result;
      sys_ranging_err_t err = sys_ranging_tag_get_result(&result);
      
      if (err == SYS_RANGING_OK && result.valid) {
        /* Success */
        s_error_count = 0;
        /* LED blink */
        bsp_io_led_on();
        bsp_delay_ms(50);
        bsp_io_led_off();
      } else {
        RLOG_W(LOG_OBJECT_CODE_TAG, "[TAG] Result invalid or not available");
      }
      
      s_app_state = TAG_STATE_WAIT_PERIOD;
      break;
    }
    
    default:
      s_app_state = TAG_STATE_WAIT_PERIOD;
      break;
  }
}

/* End of file -------------------------------------------------------- */