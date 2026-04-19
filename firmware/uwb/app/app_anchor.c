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

/* Calibration Constants */
#define CALIB_ERROR_THRESHOLD_M  0.02f  // 2cm tolerance
#define CALIB_MIN_DELTA_STEP     3      // Stop if step < 3
#define CALIB_MAX_ROUNDS         10     // Max iterations
#define CALIB_SAMPLES_PER_ROUND  25     // Samples per iteration
#define CALIB_MAX_STD_M          0.1f   // Max standard deviation
#endif

/* Private variables -------------------------------------------------- */
static uint32_t s_error_count = 0;
static uint32_t s_success_count = 0;
static anchor_app_state_t s_app_state = ANCHOR_STATE_IDLE;
static bool s_anchor_ranging_started = false;

#if ENABLE_ANCHOR_AUTO_CALIB
static calib_state_t s_calib = {0};
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
    *my_id = cfg->device_id;
    
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
  memset(&s_calib, 0, sizeof(s_calib));
  
  sys_config_t *cfg = sys_config_get();
  s_calib.current_delay = cfg->tx_antenna_delay;
  s_calib.delta_step = 100;
  s_calib.last_error = 999.0f;
  s_calib.converged = false;
  
    s_app_state = ANCHOR_STATE_CALIB_COLLECTING;
    RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[CALIB] Start: delay=%u target=%.3fm", 
      s_calib.current_delay, calib_get_ref_distance_3d());
}

static bool calib_add_sample(float distance)
{
  if (s_calib.count >= CALIB_SAMPLES_PER_ROUND) {
    return true; /* Full */
  }
  
  /* Filter outliers */
  if (distance < 0.1f || distance > 50.0f) {
    return false;
  }
  
  s_calib.distances[s_calib.count++] = distance;
  
  if (s_calib.count % 5 == 0) {
    bsp_io_led_toggle(); /* Visual feedback during collection */
  }
  
  return (s_calib.count >= CALIB_SAMPLES_PER_ROUND);
}

static void calib_calculate_and_adjust(void)
{
  if (s_calib.count < CALIB_SAMPLES_PER_ROUND) {
    return;
  }
  
  /* Calculate Mean */
  float sum = 0.0f;
  for (uint16_t i = 0; i < s_calib.count; i++) {
    sum += s_calib.distances[i];
  }
  s_calib.mean = sum / s_calib.count;
  
  /* Calculate StdDev */
  float variance = 0.0f;
  for (uint16_t i = 0; i < s_calib.count; i++) {
    float diff = s_calib.distances[i] - s_calib.mean;
    variance += diff * diff;
  }
  float std_dev = sqrtf(variance / s_calib.count);
  
  /* Sanity Check */
  if (std_dev > CALIB_MAX_STD_M) {
    RLOG_W(LOG_OBJECT_CODE_ANCHOR, 
           "[R%u] REJECTED std=%.3fm > %.3fm",
           s_calib.round + 1, std_dev, CALIB_MAX_STD_M);
    s_calib.count = 0; /* Retry this round */
    return;
  }
  
  /* Calculate Error */
  s_calib.error = s_calib.mean - calib_get_ref_distance_3d();
  s_calib.round++;
  
  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[R%u] mean=%.3fm std=%.3fm err=%+.3fm delay=%u step=%u", 
         s_calib.round, s_calib.mean, std_dev, s_calib.error, 
         s_calib.current_delay, s_calib.delta_step);
  
  /* Check Convergence */
  if (fabsf(s_calib.error) < CALIB_ERROR_THRESHOLD_M) {
    RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[CALIB] DONE! delay=%u err=%.3fm", 
           s_calib.current_delay, s_calib.error);
    RLOG_I(LOG_OBJECT_CODE_ANCHOR, "HOLD=accept CLICK=retry");
    s_calib.converged = true;
    bsp_io_led_on();
    return;
  }
  
  /* Check Limits */
  if (s_calib.round >= CALIB_MAX_ROUNDS || s_calib.delta_step < CALIB_MIN_DELTA_STEP) {
    RLOG_W(LOG_OBJECT_CODE_ANCHOR, "[CALIB] STOP! delay=%u err=%.3fm", 
           s_calib.current_delay, s_calib.error);
    RLOG_I(LOG_OBJECT_CODE_ANCHOR, "HOLD=accept CLICK=retry");
    s_calib.converged = true;
    bsp_io_led_on();
    return;
  }
  
  /* Binary Search Logic */
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
  
  /* Reconfigure UWB with new delay */
  bsp_uwb_config_t uwb_cfg;
  sys_config_t *cfg = sys_config_get();
  uwb_cfg.channel = cfg->uwb_channel;
  uwb_cfg.prf = cfg->uwb_prf;
  uwb_cfg.data_rate = cfg->uwb_data_rate;
  uwb_cfg.preamble_code = cfg->uwb_preamble_code;
  uwb_cfg.tx_antenna_delay = s_calib.current_delay;
  uwb_cfg.rx_antenna_delay = s_calib.current_delay;
  uwb_cfg.tx_power = cfg->tx_power;
  
  bsp_uwb_configure(&uwb_cfg); /* Hardware re-config */
  
  /* Reset samples for next round */
  s_calib.count = 0;
}

static void calib_apply_and_save(void)
{
  if (!s_calib.converged) return;
  
  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[CALIB] Saving TX delay=%u...", s_calib.current_delay);
  
  sys_config_t *cfg = sys_config_get();
  cfg->tx_antenna_delay = s_calib.current_delay;
  cfg->rx_antenna_delay = s_calib.current_delay;
  
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

/* Public functions ---------------------------------------- */

app_err_t app_anchor_init(void)
{
  sys_config_t *cfg = sys_config_get();
  
  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "===== ANCHOR #%u (TDMA Enabled) =====", cfg->device_id);
  
  /* Force antenna delay to default values if enabled */
#if ENABLE_FORCE_DEFAULT_ANT_DLY
  cfg->tx_antenna_delay = ANCHOR_DEFAULT_TX_ANT_DLY;
  cfg->rx_antenna_delay = ANCHOR_DEFAULT_RX_ANT_DLY;
  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "Ant Delay: TX=%u RX=%u (FORCED DEFAULT)",
         cfg->tx_antenna_delay, cfg->rx_antenna_delay);
  sys_config_save();
#else
  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "Ant Delay: TX=%u RX=%u",
         cfg->tx_antenna_delay, cfg->rx_antenna_delay);
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
  uint32_t rx_timeout_ms = (cfg->rx_timeout_ms < 5U) ? 5U : cfg->rx_timeout_ms;
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
    if (err == SYS_RANGING_ERR_NOT_STARTED) {
      s_anchor_ranging_started = false;
    }
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