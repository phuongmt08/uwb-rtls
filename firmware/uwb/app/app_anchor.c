/**
 * @file       app_anchor.c
 * @copyright
 * @license
 * @version    1.1.0
 * @date       2025-12-24
 * @author     Phuong Mai
 * @brief      Non-blocking Anchor with binary search auto-calibration
 * @note       
 * Calibration Algorithm:
 * 1. Collect samples → calculate mean error
 * 2. Adjust antenna delay using binary search
 * 3. Repeat until error < threshold OR delta < min_step
 * @example    None
 */
/* Includes ----------------------------------------------------------- */
#include "app_anchor.h"
#include "sys_ranging.h"
#include "sys_config.h"
#include "sys_logger.h"
#include "bsp_io.h"
#include "bsp_util.h"
#include "bsp_uwb.h"
#include "positioning_config.h"
#include <stdint.h>
#include <math.h>
#include <string.h>
/* Configuration ------------------------------------------------------ */

/* Private types ------------------------------------------------------ */
typedef enum {
  ANCHOR_STATE_IDLE = 0,
  ANCHOR_STATE_LISTENING,
  ANCHOR_STATE_GET_RESULT,
#if ENABLE_ANCHOR_AUTO_CALIB
  ANCHOR_STATE_CALIB_COLLECTING,
  ANCHOR_STATE_CALIB_CALCULATE,
  ANCHOR_STATE_CALIB_PENDING_ACCEPT,
  ANCHOR_STATE_CALIB_DONE
#endif
} anchor_app_state_t;

#if ENABLE_ANCHOR_AUTO_CALIB
typedef struct {
  float distances[CALIB_SAMPLES];
  uint16_t count;
  float mean;
  float error;
  float last_error;
  uint16_t current_delay;
  uint16_t delta_step;
  uint16_t round;
  bool converged;
} calib_state_t;
#endif

/* Private variables -------------------------------------------------- */
static uint32_t s_error_count = 0;
static uint32_t s_success_count = 0;
static anchor_app_state_t s_app_state = ANCHOR_STATE_IDLE;
static uint32_t s_last_listen_tick = 0;

#if ENABLE_ANCHOR_AUTO_CALIB
static calib_state_t s_calib = {0};
#define CALIB_ERROR_THRESHOLD_M  0.02f  // 2cm tolerance
#define CALIB_MIN_DELTA_STEP     3      // Stop if step < 3
#define CALIB_MAX_ROUNDS         10     // Max iterations
#define CALIB_SAMPLES_PER_ROUND  25     // Samples per iteration
#endif

/* Private function prototypes ---------------------------------------- */
#if ENABLE_ANCHOR_AUTO_CALIB
static void calib_reset(void);
static bool calib_add_sample(float distance);
static void calib_calculate_and_adjust(void);
static void calib_apply_and_save(void);
#endif

/* Private function implementations ----------------------------------- */

#if ENABLE_ANCHOR_AUTO_CALIB

static void calib_reset(void)
{
  memset(&s_calib, 0, sizeof(s_calib));
  
  sys_config_t *cfg = sys_config_get();
  s_calib.current_delay = cfg->tx_antenna_delay;
  s_calib.delta_step = 100;
  s_calib.last_error = 999.0f;
  s_calib.converged = false;
  
  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[CALIB] Start: delay=%u target=%.3fm", 
         s_calib.current_delay, CALIB_REF_DISTANCE_M);
}

static bool calib_add_sample(float distance)
{
  if (s_calib.count >= CALIB_SAMPLES_PER_ROUND) {
    return false;
  }
  
  if (distance < 0.1f || distance > 10.0f) {
    return false;
  }
  
  s_calib.distances[s_calib.count++] = distance;
  
  if (s_calib.count % 5 == 0) {
    bsp_io_led_toggle();
  }
  
  return (s_calib.count >= CALIB_SAMPLES_PER_ROUND);
}

static void calib_calculate_and_adjust(void)
{
  if (s_calib.count < CALIB_SAMPLES_PER_ROUND) {
    return;
  }
  
  float sum = 0.0f;
  for (uint16_t i = 0; i < s_calib.count; i++) {
    sum += s_calib.distances[i];
  }
  s_calib.mean = sum / s_calib.count;
  
  float variance = 0.0f;
  for (uint16_t i = 0; i < s_calib.count; i++) {
    float diff = s_calib.distances[i] - s_calib.mean;
    variance += diff * diff;
  }
  float std_dev = sqrtf(variance / s_calib.count);
  
  if (std_dev > CALIB_MAX_STD_M) {
    RLOG_W(LOG_OBJECT_CODE_ANCHOR, 
           "[R%u] REJECTED std=%.3fm > %.3fm",
           s_calib.round + 1, std_dev, CALIB_MAX_STD_M);
    s_calib.count = 0;
    memset(s_calib.distances, 0, sizeof(s_calib.distances));
    return;
  }
  
  s_calib.error = s_calib.mean - CALIB_REF_DISTANCE_M;
  s_calib.round++;
  
  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[R%u] mean=%.3fm std=%.3fm err=%+.3fm delay=%u step=%u", 
         s_calib.round, s_calib.mean, std_dev, s_calib.error, 
         s_calib.current_delay, s_calib.delta_step);
  
  if (fabsf(s_calib.error) < CALIB_ERROR_THRESHOLD_M) {
    RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[CALIB] DONE! delay=%u err=%.3fm", 
           s_calib.current_delay, s_calib.error);
    RLOG_I(LOG_OBJECT_CODE_ANCHOR, "HOLD=accept CLICK=retry");
    s_calib.converged = true;
    bsp_io_led_on();
    return;
  }
  
  if (s_calib.round >= CALIB_MAX_ROUNDS || s_calib.delta_step < CALIB_MIN_DELTA_STEP) {
    RLOG_W(LOG_OBJECT_CODE_ANCHOR, "[CALIB] STOP! delay=%u err=%.3fm", 
           s_calib.current_delay, s_calib.error);
    RLOG_I(LOG_OBJECT_CODE_ANCHOR, "HOLD=accept CLICK=retry");
    s_calib.converged = true;
    bsp_io_led_on();
    return;
  }
  
  if (s_calib.error * s_calib.last_error < 0.0f) {
    s_calib.delta_step = s_calib.delta_step / 2;
  }
  
  int32_t new_delay;
  if (s_calib.error > 0.0f) {
    new_delay = (int32_t)s_calib.current_delay + s_calib.delta_step;
  } else {
    new_delay = (int32_t)s_calib.current_delay - s_calib.delta_step;
  }
  
  if (new_delay < 0) new_delay = 0;
  if (new_delay > 65535) new_delay = 65535;
  
  s_calib.last_error = s_calib.error;
  s_calib.current_delay = (uint16_t)new_delay;
  
  bsp_uwb_config_t uwb_cfg;
  sys_config_t *cfg = sys_config_get();
  uwb_cfg.channel = cfg->uwb_channel;
  uwb_cfg.prf = cfg->uwb_prf;
  uwb_cfg.data_rate = cfg->uwb_data_rate;
  uwb_cfg.preamble_code = cfg->uwb_preamble_code;
  uwb_cfg.tx_antenna_delay = s_calib.current_delay;
  uwb_cfg.rx_antenna_delay = cfg->rx_antenna_delay;
  uwb_cfg.tx_power = cfg->tx_power;
  bsp_uwb_configure(&uwb_cfg);
  
  s_calib.count = 0;
  memset(s_calib.distances, 0, sizeof(s_calib.distances));
}

static void calib_apply_and_save(void)
{
  if (!s_calib.converged) return;
  
  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[CALIB] Saving TX delay=%u...", s_calib.current_delay);
  
  sys_config_t *cfg = sys_config_get();
  cfg->tx_antenna_delay = s_calib.current_delay;
  
  if (sys_config_save() == 0) {
    RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[CALIB] Saved! Restarting...");
    bsp_delay_ms(1000);
    HAL_NVIC_SystemReset();
  } else {
    RLOG_E(LOG_OBJECT_CODE_ANCHOR, ERR_HAL, "[CALIB] Save failed!");
  }
}

void app_anchor_on_button(bsp_io_button_event_t event)
{
  if (s_app_state != ANCHOR_STATE_CALIB_PENDING_ACCEPT) return;
  
  if (event == BSP_IO_EVENT_HOLD) {
    calib_apply_and_save();
    s_app_state = ANCHOR_STATE_CALIB_DONE;
  } else if (event == BSP_IO_EVENT_CLICK) {
    RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[CALIB] Retry...");
    calib_reset();
    s_app_state = ANCHOR_STATE_IDLE;
  } else if (event == BSP_IO_EVENT_DOUBLE_CLICK) {
    RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[CALIB] Reset to factory...");
    sys_config_t *cfg = sys_config_get();
    cfg->tx_antenna_delay = ANCHOR_DEFAULT_TX_ANT_DLY;
    cfg->rx_antenna_delay = ANCHOR_DEFAULT_RX_ANT_DLY;
    sys_config_save();
    s_app_state = ANCHOR_STATE_IDLE;
  }
}

#endif

/* Public function definitions ---------------------------------------- */

app_err_t app_anchor_init(void)
{
  sys_config_t *cfg = sys_config_get();
  
  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "===== ANCHOR #%u =====", cfg->device_id);
  
#if ENABLE_ANCHOR_AUTO_CALIB
  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "Calib: target=%.3fm samples=%u/round", 
         CALIB_REF_DISTANCE_M, CALIB_SAMPLES_PER_ROUND);
  calib_reset();
#else
  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "Mode: DS-TWR");
#endif
  
  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "======================");

  s_app_state = ANCHOR_STATE_IDLE;
  return APP_OK;
}

void app_anchor_process(void *arg)
{

  uint32_t current_tick = HAL_GetTick();
  
  switch (s_app_state) {
    case ANCHOR_STATE_IDLE:
      /* Start listening */
      sys_ranging_err_t err = sys_ranging_anchor_start(0);
      if (err == SYS_RANGING_OK) {
#if ENABLE_ANCHOR_AUTO_CALIB
        if (!s_calib.converged) {
          s_app_state = ANCHOR_STATE_CALIB_COLLECTING;
        } else {
          s_app_state = ANCHOR_STATE_LISTENING;
        }
#else
        s_app_state = ANCHOR_STATE_LISTENING;
#endif
        s_last_listen_tick = current_tick;
      } else if (err == SYS_RANGING_ERR_BUSY) {
        /* Already running, ignore */
      } else {
        RLOG_E(LOG_OBJECT_CODE_ANCHOR, ERR_UWB_RANGING, 
               "[ANCHOR] Start failed: %d", err);
        s_error_count++;
      }
      break;
    
#if ENABLE_ANCHOR_AUTO_CALIB
    case ANCHOR_STATE_CALIB_COLLECTING: {
      /* Process ranging and collect samples */
      sys_ranging_err_t err = sys_ranging_anchor_process();
      
      if (err == SYS_RANGING_OK) {
        /* Get result and add to calibration */
        sys_ranging_result_t result;
        if (sys_ranging_anchor_get_result(&result) == SYS_RANGING_OK && 
            result.valid) {
          bool round_complete = calib_add_sample(result.distance_m);
          
          if (round_complete) {
            s_app_state = ANCHOR_STATE_CALIB_CALCULATE;
          } else {
            s_app_state = ANCHOR_STATE_IDLE;
          }
        } else {
          s_app_state = ANCHOR_STATE_IDLE;
        }
      } else if (err == SYS_RANGING_ERR_BUSY) {
        /* Still processing */
      } else {
        /* Error - retry */
        s_app_state = ANCHOR_STATE_IDLE;
      }
      break;
    }
    
    case ANCHOR_STATE_CALIB_CALCULATE: {
      /* Calculate and adjust delay */
      calib_calculate_and_adjust();
      
      if (s_calib.converged) {
        /* Done - wait for user input */
        s_app_state = ANCHOR_STATE_CALIB_PENDING_ACCEPT;
      } else {
        /* Continue next round */
        s_app_state = ANCHOR_STATE_IDLE;
      }
      break;
    }
    
    case ANCHOR_STATE_CALIB_PENDING_ACCEPT:
    case ANCHOR_STATE_CALIB_DONE:
      break;
#endif
    
    case ANCHOR_STATE_LISTENING: {
      /* Normal ranging mode (non-calib build or after calib done) */
      sys_ranging_err_t err = sys_ranging_anchor_process();
      
      if (err == SYS_RANGING_OK) {
        s_app_state = ANCHOR_STATE_GET_RESULT;
      } else if (err == SYS_RANGING_ERR_BUSY) {
        /* Still processing */
      } else if (err == SYS_RANGING_ERR_TIMEOUT) {
        /* Timeout is normal - restart */
        s_app_state = ANCHOR_STATE_IDLE;
      } else {
        RLOG_E(LOG_OBJECT_CODE_ANCHOR, ERR_UWB_RANGING, 
               "[ANCHOR] Error: %d", err);
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
        s_success_count++;
        s_error_count = 0;
        
        /* LED blink */
        bsp_io_led_on();
        bsp_delay_ms(20);
        bsp_io_led_off();
      }
      
      s_app_state = ANCHOR_STATE_IDLE;
      break;
    }
    
    default:
      s_app_state = ANCHOR_STATE_IDLE;
      break;
  }
}

/* End of file -------------------------------------------------------- */
