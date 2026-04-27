/**
 * @file       app_anchor.c
 * @copyright
 * @license
 * @version    2.1.0
 * @date       2026-02-01
 * @author     Phuong Mai
 * @brief      Non-blocking Anchor with binary search auto-calibration & TDMA
 * 
 */
/* Includes ----------------------------------------------------------- */
#include "app_anchor.h"
#include "sys_ranging.h"
#include "sys_config.h"
#include "sys_logger.h"
#include "bsp_io.h"
#include "bsp_util.h"
#include "bsp_uwb.h"
#include "mw_calibration.h"
#include "positioning_config.h"
#include <stdint.h>
#include <math.h>
#include <string.h>

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
static const mw_calib_config_t s_anchor_calib_cfg = {
  .samples_per_round = CALIB_SAMPLES,
  .min_valid_distance_m = 0.1f,
  .max_valid_distance_m = 50.0f,
  .error_threshold_m = CALIB_ERROR_THRESHOLD_M,
  .min_delta_step = CALIB_MIN_DELTA_STEP,
  .max_rounds = CALIB_MAX_ROUNDS,
  .max_std_m = CALIB_MAX_STD_M,
  .initial_delta_step = 100,
  .initial_last_error = 999.0f
};
#endif

/* Private variables -------------------------------------------------- */
static uint32_t s_error_count = 0;
static uint32_t s_success_count = 0;
static anchor_app_state_t s_app_state = ANCHOR_STATE_IDLE;
static bool s_anchor_ranging_started = false;

#if ENABLE_ANCHOR_AUTO_CALIB
static mw_calib_ctx_t s_calib = {0};
#endif

/* Private function prototypes ---------------------------------------- */
#if ENABLE_ANCHOR_AUTO_CALIB
static void calib_reset(void);
static bool calib_add_sample(float distance);
static void calib_calculate_and_adjust(void);
static void calib_apply_and_save(void);
static float calib_get_ref_distance_3d(void);
#endif

/* Helper to configure TDMA network */
static void get_tdma_config(uint8_t *my_id, uint8_t *num_anchors, uint8_t *anchor_ids) {
    sys_config_t *cfg = sys_config_get();
  *my_id = cfg->uwb.device_id;
    
    /* Config network size from NUM_ANCHORS (TDMA max 8) */
    *num_anchors = (NUM_ANCHORS > 8) ? 8 : NUM_ANCHORS;
    
    /* Create linear list of Anchor IDs: 1, 2, 3... */
    for(uint8_t i=0; i<*num_anchors; i++) {
        anchor_ids[i] = i + 1;
    }
}

/* Private function implementations ----------------------------------- */

#if ENABLE_ANCHOR_AUTO_CALIB

static float calib_get_ref_distance_3d(void)
{
  float dz = (float)(CALIB_ANCHOR_HEIGHT_M - CALIB_TAG_HEIGHT_M);
  return sqrtf(CALIB_REF_DISTANCE_XY_M * CALIB_REF_DISTANCE_XY_M + dz * dz);
}

static void calib_reset(void)
{
  sys_config_t *cfg = sys_config_get();
  mw_calib_reset(&s_calib, &s_anchor_calib_cfg, cfg->uwb.tx_antenna_delay);
  
    s_app_state = ANCHOR_STATE_CALIB_COLLECTING;
    RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[CALIB] Start: delay=%u target=%.3fm", 
      s_calib.current_delay, calib_get_ref_distance_3d());
}

static bool calib_add_sample(float distance)
{
  uint16_t prev_count = s_calib.count;
  bool full = mw_calib_add_sample(&s_calib, distance);
  
  if (s_calib.count != prev_count && (s_calib.count % 5 == 0)) {
    bsp_io_led_toggle(); /* Visual feedback during collection */
  }
  
  return full;
}

static void calib_calculate_and_adjust(void)
{
  mw_calib_step_result_t step = mw_calib_calculate_and_adjust(&s_calib,
                                                               calib_get_ref_distance_3d());

  if (step == MW_CALIB_STEP_NOT_READY) {
    return;
  }

  if (step == MW_CALIB_STEP_REJECTED_STD) {
    RLOG_W(LOG_OBJECT_CODE_ANCHOR, 
           "[R%u] REJECTED std=%.3fm > %.3fm",
           s_calib.round + 1, s_calib.std_dev, CALIB_MAX_STD_M);
    return;
  }

  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[R%u] mean=%.3fm std=%.3fm err=%+.3fm delay=%u step=%u", 
         s_calib.round, s_calib.mean, s_calib.std_dev, s_calib.error, 
         s_calib.current_delay, s_calib.delta_step);

  if (step == MW_CALIB_STEP_DONE) {
    if (s_calib.done_by_threshold) {
      RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[CALIB] DONE! delay=%u err=%.3fm", 
             s_calib.current_delay, s_calib.error);
    } else {
      RLOG_W(LOG_OBJECT_CODE_ANCHOR, "[CALIB] STOP! delay=%u err=%.3fm", 
             s_calib.current_delay, s_calib.error);
    }
    RLOG_I(LOG_OBJECT_CODE_ANCHOR, "HOLD=accept CLICK=retry");
    s_app_state = ANCHOR_STATE_CALIB_PENDING_ACCEPT;
    bsp_io_led_on();
    return;
  }

  if (step == MW_CALIB_STEP_ADJUSTED) {
    sys_config_t *cfg = sys_config_get();
    protobuf_uwb_cfg_t tmp = cfg->uwb;
    tmp.tx_antenna_delay = s_calib.current_delay;
    tmp.rx_antenna_delay = CALIB_FIXED_RX_ANT_DLY;
    bsp_uwb_configure(&tmp);
    s_app_state = ANCHOR_STATE_CALIB_COLLECTING;
  }
}

static void calib_apply_and_save(void)
{
  if (!s_calib.converged) return;
  
    RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[CALIB] Saving TX delay=%u (RX fixed=%u)...",
      s_calib.current_delay,
      (unsigned)CALIB_FIXED_RX_ANT_DLY);
  
  sys_config_t *cfg = sys_config_get();
  cfg->uwb.tx_antenna_delay = s_calib.current_delay;
    cfg->uwb.rx_antenna_delay = CALIB_FIXED_RX_ANT_DLY;
  
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
  } else if (event == BSP_IO_EVENT_DOUBLE_CLICK) {
    RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[CALIB] Reset to factory...");
    sys_config_t *cfg = sys_config_get();
    cfg->uwb.tx_antenna_delay = ANCHOR_DEFAULT_TX_ANT_DLY;
    cfg->uwb.rx_antenna_delay = ANCHOR_DEFAULT_RX_ANT_DLY;
    sys_config_save();
    s_app_state = ANCHOR_STATE_IDLE;
  }
}

#endif

/* Public functions ---------------------------------------- */

app_err_t app_anchor_init(void)
{
  sys_config_t *cfg = sys_config_get();
  
  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "===== ANCHOR #%u =====", cfg->uwb.device_id);
  
  /* Force antenna delay to default values if enabled */
#if ENABLE_FORCE_DEFAULT_ANT_DLY
  cfg->uwb.tx_antenna_delay = ANCHOR_DEFAULT_TX_ANT_DLY;
  cfg->uwb.rx_antenna_delay = ANCHOR_DEFAULT_RX_ANT_DLY;
  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "Ant Delay: TX=%u RX=%u (FORCED DEFAULT)",
         cfg->uwb.tx_antenna_delay, cfg->uwb.rx_antenna_delay);
  sys_config_save();
#else
  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "Ant Delay: TX=%u RX=%u",
         cfg->uwb.tx_antenna_delay, cfg->uwb.rx_antenna_delay);
#endif
  
#if ENABLE_ANCHOR_AUTO_CALIB
  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "Calib Mode: Target=%.3fm", calib_get_ref_distance_3d());
  calib_reset();
  s_app_state = ANCHOR_STATE_CALIB_COLLECTING;
#else
  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "Normal Mode: TDMA Responder");
#endif

#if !ENABLE_ANCHOR_AUTO_CALIB
  s_app_state = ANCHOR_STATE_IDLE;
#endif
  return APP_OK;
}

void app_anchor_process(void *arg)
{
  (void)arg;
  static uint32_t s_last_diag_log = 0;
  uint32_t now = HAL_GetTick();
  sys_config_t *cfg = sys_config_get();
  uint32_t rx_timeout_ms = (cfg->uwb.rx_timeout_ms < 5U) ? 5U : cfg->uwb.rx_timeout_ms;
  uint8_t my_id = 0;
  uint8_t num_anchors = NUM_ANCHORS;
  uint8_t anchor_ids[NUM_ANCHORS] = {0};

  get_tdma_config(&my_id, &num_anchors, anchor_ids);

  if (!s_anchor_ranging_started) {
    sys_ranging_err_t start_err = sys_ranging_anchor_start_tdma(my_id, num_anchors, anchor_ids, rx_timeout_ms);
    if (start_err == SYS_RANGING_OK) {
      s_anchor_ranging_started = true;
    } else if (start_err != SYS_RANGING_ERR_BUSY && (now - s_last_diag_log) >= 1000U) {
      RLOG_W(LOG_OBJECT_CODE_ANCHOR,
             "[ANCHOR] start err=%d id=%u n=%u timeout=%lums",
             start_err,
             my_id,
             num_anchors,
             (unsigned long)rx_timeout_ms);
      s_last_diag_log = now;
      bsp_io_led_blink(5);
    }
    return;
  }

  sys_ranging_err_t err = sys_ranging_anchor_process_tdma(num_anchors, anchor_ids, rx_timeout_ms);

  if (err == SYS_RANGING_OK) {
    sys_ranging_result_t result;
    /* Always call get_result_tdma regardless of result.valid.
     * This is required to reset the state machine from STATE_ANCHOR_COMPLETE
     * back to STATE_IDLE. If skipped, the next cycle's anchor_start_tdma
     * returns SYS_RANGING_ERR_BUSY and the anchor silently drops that cycle. */
    if (sys_ranging_anchor_get_result_tdma(&result) == SYS_RANGING_OK && result.valid) {
      s_success_count++;
      s_error_count = 0;

#if ENABLE_ANCHOR_AUTO_CALIB
      /* Feed to calibration if not converged */
      if (!s_calib.converged && s_app_state == ANCHOR_STATE_CALIB_COLLECTING) {
        if (calib_add_sample(result.distance_m)) {
          s_app_state = ANCHOR_STATE_CALIB_CALCULATE;
          calib_calculate_and_adjust();
          if (s_calib.converged) {
            s_app_state = ANCHOR_STATE_CALIB_PENDING_ACCEPT;
          } else {
            s_app_state = ANCHOR_STATE_CALIB_COLLECTING;
          }
        }
      }
#endif

    }
    s_anchor_ranging_started = false;
  } else if (err == SYS_RANGING_ERR_TIMEOUT || err == SYS_RANGING_ERR_NOT_STARTED) {
    if ((now - s_last_diag_log) >= 1000U) {
      RLOG_W(LOG_OBJECT_CODE_ANCHOR,
             "[ANCHOR] process state miss err=%d timeout=%lums",
             err,
             (unsigned long)rx_timeout_ms);
      s_last_diag_log = now;
      bsp_io_led_blink(5);
    }
    /* Reset so next loop calls start_tdma again. Without this, TIMEOUT causes
     * repeated NOT_STARTED returns, delaying re-entry and making next_poll_dw stale. */
    s_anchor_ranging_started = false;
  } else if (err == SYS_RANGING_ERR_BUSY) {
    /* Still processing */
  } else {
    /* Real error */
    s_error_count++;
    s_anchor_ranging_started = false;
    bsp_io_led_blink(5);
    if (s_error_count >= MAX_CONSECUTIVE_ERR) {
      RLOG_W(LOG_OBJECT_CODE_ANCHOR, "Many errors (%lu)", s_error_count);
      s_error_count = 0;
    }
  }
}
/* End of file -------------------------------------------------------- */  