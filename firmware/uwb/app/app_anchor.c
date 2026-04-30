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
/* Sample collector config — only sample-collection fields are used.
 * Binary-search fields (delta_step, max_rounds, etc.) are irrelevant
 * in the gradient flow but must be set to harmless values.            */
static const mw_calib_config_t s_anchor_calib_cfg = {
  .samples_per_round    = CALIB_ANCHOR_SAMPLES,
  .min_valid_distance_m = 0.1f,
  .max_valid_distance_m = 50.0f,
  .max_std_m            = CALIB_ANCHOR_MAX_STD_M,
  /* unused by gradient path — set safe defaults */
  .error_threshold_m    = 0.001f,
  .min_delta_step       = 1,
  .max_rounds           = 255,
  .initial_delta_step   = 0,
  .initial_last_error   = 0.0f,
};

/* A2A gradient config — all values driven from positioning_config.h  */
static const mw_calib_a2a_config_t s_a2a_cfg = {
  .m_to_dw_units = CALIB_A2A_M_TO_DW_UNITS,
  .damping       = CALIB_A2A_DAMPING,
  .ant_min       = CALIB_A2A_ANT_MIN,
  .ant_max       = CALIB_A2A_ANT_MAX,
  .iterations    = CALIB_A2A_ITERATIONS,
};
#endif

/* Private variables -------------------------------------------------- */
static uint32_t s_error_count = 0;
static uint32_t s_success_count = 0;
static anchor_app_state_t s_app_state = ANCHOR_STATE_IDLE;
static bool s_anchor_ranging_started = false;

#if ENABLE_ANCHOR_AUTO_CALIB
static mw_calib_ctx_t     s_calib  = {0}; /* per-pair sample collector       */
static mw_calib_a2a_ctx_t s_a2a    = {0}; /* gradient control across pairs   */
static bool s_calib_ranging_started = false;
static uint8_t s_calib_sequence_num = 0;

static uint8_t s_calib_targets[8];
static uint8_t s_num_calib_targets = 0;
static uint8_t s_current_target_idx = 0;
static uint8_t s_calib_iterations = 0;
#define CALIB_MAX_ITERATIONS 2

static const calib_pair_t s_calib_pairs[] = CALIB_PAIRWISE_LIST;
#define NUM_CALIB_PAIRS (sizeof(s_calib_pairs) / sizeof(s_calib_pairs[0]))

static void get_calib_targets(uint8_t my_id) {
  s_num_calib_targets = 0;
  for (uint8_t i = 0; i < NUM_CALIB_PAIRS; i++) {
    if (s_calib_pairs[i].source_id == my_id) {
      if (s_num_calib_targets < sizeof(s_calib_targets)) {
        s_calib_targets[s_num_calib_targets++] = s_calib_pairs[i].target_id;
      }
    }
  }
}
#endif

/* Private function prototypes ---------------------------------------- */
#if ENABLE_ANCHOR_AUTO_CALIB
static void calib_reset(void);
static bool calib_add_sample(float distance);
static void calib_calculate_and_adjust(void);
static void calib_apply_and_save(void);
static bool calib_get_anchor_position(uint8_t anchor_id, float *x, float *y, float *z);
static float calib_get_ref_distance_3d(uint8_t local_anchor_id, uint8_t ref_anchor_id);
static void calib_apply_split_delay(uint16_t combined_delay);
static bool calib_start_round(uint8_t target_anchor_id, uint32_t rx_timeout_ms);
static void calib_process_round(uint32_t rx_timeout_ms);
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

static bool calib_get_anchor_position(uint8_t anchor_id, float *x, float *y, float *z)
{
  if (!x || !y || !z) {
    return false;
  }

  switch (anchor_id) {
    case 1: *x = ANCHOR_1_X; *y = ANCHOR_1_Y; *z = ANCHOR_1_Z; return true;
    case 2: *x = ANCHOR_2_X; *y = ANCHOR_2_Y; *z = ANCHOR_2_Z; return true;
    case 3: *x = ANCHOR_3_X; *y = ANCHOR_3_Y; *z = ANCHOR_3_Z; return true;
    case 4: *x = ANCHOR_4_X; *y = ANCHOR_4_Y; *z = ANCHOR_4_Z; return true;
    default: return false;
  }
}

static float calib_get_ref_distance_3d(uint8_t local_anchor_id, uint8_t ref_anchor_id)
{
  float lx = 0.0f, ly = 0.0f, lz = 0.0f;
  float rx = 0.0f, ry = 0.0f, rz = 0.0f;

  if (!calib_get_anchor_position(local_anchor_id, &lx, &ly, &lz) ||
      !calib_get_anchor_position(ref_anchor_id, &rx, &ry, &rz)) {
    return 0.0f;
  }

  float dx = lx - rx;
  float dy = ly - ry;
  float dz = lz - rz;
  return sqrtf(dx * dx + dy * dy + dz * dz);
}

static void calib_apply_split_delay(uint16_t combined_delay)
{
  sys_config_t *cfg = sys_config_get();
  protobuf_uwb_cfg_t tmp = cfg->uwb;
  uint16_t half = (uint16_t)(combined_delay / 2U);
  tmp.tx_antenna_delay = half;
  tmp.rx_antenna_delay = (uint16_t)(combined_delay - half); /* absorb odd */
  bsp_uwb_configure(&tmp);
}

static void calib_reset(void)
{
  sys_config_t *cfg = sys_config_get();
  uint16_t combined = (uint16_t)((uint32_t)cfg->uwb.tx_antenna_delay
                                 + (uint32_t)cfg->uwb.rx_antenna_delay);

  /* Reset per-pair sample collector (reuse binary-search ctx, stats only) */
  mw_calib_reset(&s_calib, &s_anchor_calib_cfg, 0U /* delay unused here */);

  /* Init gradient context once (keeps combined_delay across pair resets) */
  if (s_a2a.iter == 0U && s_a2a.pair_error_count == 0U && !s_a2a.done) {
    mw_calib_a2a_init(&s_a2a, &s_a2a_cfg, combined);
  }

  s_calib_ranging_started = false;
  s_calib_sequence_num    = 0;
  s_app_state = ANCHOR_STATE_CALIB_COLLECTING;
  sys_ranging_set_calib_status(SYS_CALIB_STATUS_COLLECTING);

  uint8_t target_id = s_calib_targets[s_current_target_idx];
  RLOG_I(LOG_OBJECT_CODE_ANCHOR,
         "[CALIB] Iter=%u pair=%u/%u target=%u ref=%.3fm delay=%u",
         s_a2a.iter + 1U,
         s_current_target_idx + 1U,
         s_num_calib_targets,
         (unsigned)target_id,
         calib_get_ref_distance_3d(cfg->uwb.device_id, target_id),
         s_a2a.combined_delay);
}

static bool calib_start_round(uint8_t target_anchor_id, uint32_t rx_timeout_ms)
{
  uint8_t anchor_ids[1] = { target_anchor_id };
  sys_ranging_err_t start_err = sys_ranging_tag_start_tdma(1,
                                                            anchor_ids,
                                                            s_calib_sequence_num,
                                                            rx_timeout_ms);
  if (start_err != SYS_RANGING_OK) {
    return false;
  }

  s_calib_sequence_num++;
  s_calib_ranging_started = true;
  return true;
}

static void calib_process_round(uint32_t rx_timeout_ms)
{
  sys_config_t *cfg = sys_config_get();
  uint8_t target_id = s_calib_targets[s_current_target_idx];
  uint8_t anchor_ids[1] = { target_id };

  if (!s_calib_ranging_started) {
    if (!calib_start_round(target_id, rx_timeout_ms)) {
      RLOG_W(LOG_OBJECT_CODE_ANCHOR, "[CALIB] Start round failed");
    }
    return;
  }

  sys_ranging_err_t err = sys_ranging_tag_process_tdma(1, anchor_ids, rx_timeout_ms);
  if (err != SYS_RANGING_OK) {
    if (err != SYS_RANGING_ERR_BUSY) {
      s_calib_ranging_started = false;
    }
    return;
  }

  sys_ranging_multi_result_t multi_results;
  if (sys_ranging_tag_get_results_tdma(&multi_results) != SYS_RANGING_OK) {
    s_calib_ranging_started = false;
    return;
  }

  bool found = false;
  for (uint8_t i = 0; i < multi_results.count; i++) {
    sys_ranging_result_t *res = &multi_results.results[i];
    if (res->valid && res->anchor_id == target_id) {
      found = true;
      if (calib_add_sample(res->distance_m)) {
        s_app_state = ANCHOR_STATE_CALIB_CALCULATE;
        calib_calculate_and_adjust();
      }
      break;
    }
  }

  if (!found) {
    RLOG_W(LOG_OBJECT_CODE_ANCHOR,
           "[CALIB] No valid result from target anchor %u",
           (unsigned)target_id);
  }

  s_calib_ranging_started = false;

  if (!s_calib.converged && s_app_state == ANCHOR_STATE_CALIB_COLLECTING) {
    calib_apply_split_delay(s_calib.current_delay);
  }
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
  uint8_t target_id = s_calib_targets[s_current_target_idx];
  mw_calib_step_result_t step = mw_calib_calculate_and_adjust(&s_calib,
                                                               calib_get_ref_distance_3d(sys_config_get()->uwb.device_id,
                                                                                         target_id));

  if (step == MW_CALIB_STEP_NOT_READY) {
    return;
  }

  if (step == MW_CALIB_STEP_REJECTED_STD) {
    RLOG_W(LOG_OBJECT_CODE_ANCHOR, 
           "[R%u] REJECTED std=%.3fm > %.3fm",
           s_calib.round + 1, s_calib.std_dev, CALIB_ANCHOR_MAX_STD_M);
    return;
  }

  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[R%u] mean=%.3fm std=%.3fm err=%+.3fm delay=%u step=%u", 
         s_calib.round, s_calib.mean, s_calib.std_dev, s_calib.error, 
         s_calib.current_delay, s_calib.delta_step);

  if (step == MW_CALIB_STEP_DONE) {
    if (s_calib.done_by_threshold) {
      RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[CALIB] DONE! target=%u delay=%u err=%.3fm", 
             target_id, s_calib.current_delay, s_calib.error);
    } else {
      RLOG_W(LOG_OBJECT_CODE_ANCHOR, "[CALIB] STOP! target=%u delay=%u err=%.3fm", 
             target_id, s_calib.current_delay, s_calib.error);
    }
    
    // Auto apply and save
    calib_apply_split_delay(s_calib.current_delay);
    sys_config_save();
    sys_ranging_set_calib_status(SYS_CALIB_STATUS_DONE);
    
    // Move to next target or next iteration
    s_current_target_idx++;
    if (s_current_target_idx >= s_num_calib_targets) {
        s_current_target_idx = 0;
        s_calib_iterations++;
        if (s_calib_iterations >= CALIB_MAX_ITERATIONS) {
            RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[CALIB] All iterations complete! Resetting...");
            bsp_delay_ms(1000);
            HAL_NVIC_SystemReset();
        } else {
            RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[CALIB] Starting iteration %u", s_calib_iterations + 1);
            calib_reset();
        }
    } else {
        RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[CALIB] Moving to next target %u", s_calib_targets[s_current_target_idx]);
        calib_reset();
    }
    return;
  }

  if (step == MW_CALIB_STEP_ADJUSTED) {
    calib_apply_split_delay(s_calib.current_delay);
    s_app_state = ANCHOR_STATE_CALIB_COLLECTING;
    sys_ranging_set_calib_status(SYS_CALIB_STATUS_COLLECTING);
  }
}

void app_anchor_on_button(bsp_io_button_event_t event)
{
  if (event == BSP_IO_EVENT_DOUBLE_CLICK) {
    RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[CALIB] Reset to factory...");
    sys_config_t *cfg = sys_config_get();
    cfg->uwb.tx_antenna_delay = ANCHOR_DEFAULT_TX_ANT_DLY;
    cfg->uwb.rx_antenna_delay = ANCHOR_DEFAULT_RX_ANT_DLY;
    sys_config_save();
    s_app_state = ANCHOR_STATE_IDLE;
    sys_ranging_set_calib_status(SYS_CALIB_STATUS_NORMAL);
    HAL_NVIC_SystemReset();
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
  get_calib_targets(cfg->uwb.device_id);
  
  if (s_num_calib_targets > 0) {
    RLOG_I(LOG_OBJECT_CODE_ANCHOR, "Calib Mode: %u targets (TX/RX split 50/50)",
           s_num_calib_targets);
    s_current_target_idx = 0;
    s_calib_iterations = 0;
    calib_reset();
  } else {
    RLOG_I(LOG_OBJECT_CODE_ANCHOR, "Calib Mode: reference anchor, no self-calib");
    sys_ranging_set_calib_status(SYS_CALIB_STATUS_NORMAL);
    s_app_state = ANCHOR_STATE_IDLE;
  }
#else
  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "Normal Mode: TDMA Responder");
  sys_ranging_set_calib_status(SYS_CALIB_STATUS_NORMAL);
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

#if ENABLE_ANCHOR_AUTO_CALIB
  if (s_app_state == ANCHOR_STATE_CALIB_COLLECTING ||
      s_app_state == ANCHOR_STATE_CALIB_CALCULATE) {
    calib_process_round(rx_timeout_ms);
    return;
  }

  if (s_app_state == ANCHOR_STATE_CALIB_PENDING_ACCEPT ||
      s_app_state == ANCHOR_STATE_CALIB_DONE) {
    return;
  }
#endif

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