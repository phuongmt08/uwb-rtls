/* ============================== app_anchor.c ===============================
 * @file       app_anchor.c
 * @brief      Non-blocking Anchor application
 * @version    2.0.0
 * @date       2025-11-26
 */

/* Includes ----------------------------------------------------------- */
#include "app_anchor.h"
#include "sys_ranging.h"
#include "sys_config.h"
#include "sys_logger.h"
#include "bsp_io.h"
#include "bsp_util.h"
#include <stdint.h>

/* Configuration ------------------------------------------------------ */
#define MAX_CONSECUTIVE_ERR     (5)
#define LOG_INTERVAL_SUCCESS    (1)

/* Private types ------------------------------------------------------ */
typedef enum {
  ANCHOR_STATE_IDLE = 0,
  ANCHOR_STATE_LISTENING,
  ANCHOR_STATE_GET_RESULT
} anchor_app_state_t;

/* Private variables -------------------------------------------------- */
static uint32_t s_error_count = 0;
static uint32_t s_success_count = 0;
static anchor_app_state_t s_app_state = ANCHOR_STATE_IDLE;
static uint32_t s_last_listen_tick = 0;

/* Public function definitions ---------------------------------------- */

app_err_t app_anchor_init(void)
{
  sys_config_t *cfg = sys_config_get();
  
  /* Read DIP switch for anchor ID configuration */
  uint8_t dip_value = bsp_io_dip_read();
  
  if (dip_value == 0) {
    /* DIP = 0: Use last saved config ID */
    RLOG_I(LOG_OBJECT_CODE_ANCHOR, "DIP switch = 0, using saved ID: 0x%02X", cfg->device_id);
  } else {
    /* DIP != 0: Override with DIP switch value (1-7) */
    if (cfg->device_id != dip_value) {
      cfg->device_id = dip_value;
      RLOG_I(LOG_OBJECT_CODE_ANCHOR, "DIP switch override: ID set to 0x%02X", dip_value);
      sys_config_save();
    } else {
      RLOG_I(LOG_OBJECT_CODE_ANCHOR, "DIP switch matches saved ID: 0x%02X", dip_value);
    }
  }
  
  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "===== ANCHOR INIT =====");
  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "Anchor ID: 0x%02X", cfg->device_id);
  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "Method: DS-TWR");
  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "=======================");

  s_app_state = ANCHOR_STATE_IDLE;
  return APP_OK;
}

void app_anchor_process(void *arg)
{
  sys_config_t *cfg = sys_config_get();
  uint32_t current_tick = HAL_GetTick();
  switch (s_app_state) {
    case ANCHOR_STATE_IDLE:
      /* Chỉ khởi động lại sau mỗi interval, giống Tag */
      if ((current_tick - s_last_listen_tick) >= cfg->ranging_period_ms) {
        s_last_listen_tick = current_tick;
        sys_ranging_err_t err = sys_ranging_anchor_start(100);
        if (err == SYS_RANGING_OK) {
          s_app_state = ANCHOR_STATE_LISTENING;
          // RLOG_D(LOG_OBJECT_CODE_ANCHOR, "[ANCHOR] Started listening");
        } else if (err == SYS_RANGING_ERR_BUSY) {
          /* Không gọi lại liên tục, chỉ log cảnh báo nếu cần */
        } else {
          RLOG_E(LOG_OBJECT_CODE_ANCHOR, ERR_UWB_RANGING, "[ANCHOR] Start failed: %d", err);
          s_error_count++;
        }
      }
      break;
    
    case ANCHOR_STATE_LISTENING: {
      /* Process state machine */
      sys_ranging_err_t err = sys_ranging_anchor_process();
      
      if (err == SYS_RANGING_OK) {
        /* Ranging complete */
        s_app_state = ANCHOR_STATE_GET_RESULT;
      } else if (err == SYS_RANGING_ERR_BUSY) {
        /* Still processing - normal, do nothing */
      } else if (err == SYS_RANGING_ERR_TIMEOUT) {
        /* Timeout is normal for anchor - restart listening */
        s_app_state = ANCHOR_STATE_IDLE;
      } else {
        /* Other error */
        RLOG_E(LOG_OBJECT_CODE_ANCHOR, ERR_UWB_RANGING, "[ANCHOR] Error: %d", err);
        s_error_count++;
        s_app_state = ANCHOR_STATE_IDLE;
        
        if (s_error_count >= MAX_CONSECUTIVE_ERR) {
          RLOG_E(LOG_OBJECT_CODE_ANCHOR, ERR_TIMEOUT,
                 "Too many errors (%lu)", s_error_count);
          s_error_count = 0;
        }
      }
      break;
    }
    
    case ANCHOR_STATE_GET_RESULT: {
      sys_ranging_result_t result;
      sys_ranging_err_t err = sys_ranging_anchor_get_result(&result);
      
      if (err == SYS_RANGING_OK && result.valid) {
        /* Success */
        s_success_count++;
        s_error_count = 0;
        
        /* LED blink */
        bsp_io_led_on();
        bsp_delay_ms(20);
        bsp_io_led_off();
      } else {
        RLOG_W(LOG_OBJECT_CODE_ANCHOR, "[ANCHOR] Result invalid");
      }
      
      /* Restart listening immediately */
      s_app_state = ANCHOR_STATE_IDLE;
      break;
    }
    
    default:
      s_app_state = ANCHOR_STATE_IDLE;
      break;
  }
}

/* End of file -------------------------------------------------------- */