/**
 * @file       app_anchor.c
 * @brief      Long-Turn A2A Calibration (Burst Mode) - Fixed Prototype Version
 * 
 * Each anchor stays as Initiator for up to 4 seconds or until it collects 20 samples.
 * Responders stay active and advance their turn counter based on a 4s silence timeout.
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
#include "mw_filter.h"
#include "positioning_config.h"
#include <stdint.h>
#include <math.h>
#include <string.h>

/* Private defines ---------------------------------------------------- */
#define CALIB_TURN_TIMEOUT_MS   4000U /* Max time for one anchor to hold the floor */
#define CALIB_DONE_GRACE_MS     60000U /* After DONE, keep ranging so master sees status */
#define ANCHOR_MASTER           4     /* Anchor ID that starts the calibration sequence */

/* Private types ------------------------------------------------------ */
typedef enum {
  ANCHOR_STATE_IDLE = 0,
  ANCHOR_STATE_NORMAL,
#if ENABLE_ANCHOR_AUTO_CALIB
  ANCHOR_STATE_CALIB_COLLECTING,
  ANCHOR_STATE_CALIB_CALCULATE,
  ANCHOR_STATE_CALIB_DONE
#endif
} anchor_app_state_t;

#if ENABLE_ANCHOR_AUTO_CALIB
static const mw_calib_config_t s_anchor_calib_cfg = {
  .samples_per_round    = CALIB_ANCHOR_SAMPLES,
  .min_valid_distance_m = 0.01f,
  .max_valid_distance_m = 50.0f,
  .max_std_m            = CALIB_ANCHOR_MAX_STD_M,
};

static mw_calib_ctx_t     s_peer_calib[MAX_ANCHORS_SUPPORTED] = {0};
static mw_calib_a2a_ctx_t s_a2a = {0};
static uint8_t            s_peer_ready_mask = 0;
static uint8_t            s_peer_expected_mask = 0;

/* --- NEW ARCHITECTURE COUNTERS --- */
static uint8_t            s_current_turn   = ANCHOR_MASTER;  /* Who is currently initiating */
static uint8_t            s_round_seq      = 0;  /* TDMA seq per POLL — tăng mỗi POLL */
static uint32_t           s_turn_start_ms  = 0;  /* When current turn started */
static uint32_t           s_last_act_ms    = 0;  /* Last time we heard/did something */
static bool               s_heard_poll     = false; /* Only advance turn if we heard a poll */
static uint32_t           s_done_start_ms  = 0;  /* For grace period */
static bool               s_system_started = false; /* Has heard at least one poll since reset */
static uint8_t            s_peer_done_mask = 0;
static median_filter_1d_t s_calib_medians[MAX_ANCHORS_SUPPORTED] = {0};
#endif

static bool s_ranging_active     = false;
static bool s_anchor_resp_active = false;

static anchor_app_state_t s_app_state = ANCHOR_STATE_IDLE;

/* Private functions -------------------------------------------------- */
#if ENABLE_ANCHOR_AUTO_CALIB
static float calib_get_ref_distance_3d(uint8_t my_id, uint8_t peer_id) {
    sys_config_t *cfg = sys_config_get();
    float x1 = 0, y1 = 0, z1 = 0;
    float x2 = 0, y2 = 0, z2 = 0;
    bool found1 = false;
    bool found2 = false;
    for (uint32_t i = 0; i < cfg->anchor_count; i++) {
        if (cfg->anchor_layout[i].anchor_id == my_id) {
            x1 = cfg->anchor_layout[i].x_m;
            y1 = cfg->anchor_layout[i].y_m;
            z1 = cfg->anchor_layout[i].z_m;
            found1 = true;
        }
        if (cfg->anchor_layout[i].anchor_id == peer_id) {
            x2 = cfg->anchor_layout[i].x_m;
            y2 = cfg->anchor_layout[i].y_m;
            z2 = cfg->anchor_layout[i].z_m;
            found2 = true;
        }
    }
    if (found1 && found2) {
        float dx = x2 - x1;
        float dy = y2 - y1;
        float dz = z2 - z1;
        return sqrtf(dx*dx + dy*dy + dz*dz);
    }
    return 1.0f; /* fallback */
}

static void calib_reset(void) {
  for (uint8_t i = 0; i < MAX_ANCHORS_SUPPORTED; i++) {
    mw_calib_reset(&s_peer_calib[i], &s_anchor_calib_cfg, 0U);
    memset(&s_calib_medians[i], 0, sizeof(median_filter_1d_t));
  }
  s_peer_ready_mask = 0;
  s_system_started = false;
  s_turn_start_ms = HAL_GetTick();
  s_last_act_ms = HAL_GetTick();
}

static void calib_calculate_and_adjust(void) {
  if (s_app_state == ANCHOR_STATE_CALIB_DONE) return;
  bool converged = mw_calib_a2a_apply_gradient(&s_a2a);
  
  sys_config_t *cfg = sys_config_get();
  cfg->uwb.tx_antenna_delay = (uint16_t)(s_a2a.combined_delay / 2);
  cfg->uwb.rx_antenna_delay = (uint16_t)(s_a2a.combined_delay / 2);
  
  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[CALIB] Iter %u complete. Error=%+.3fm NewDelay=%u",
         s_a2a.iter, s_a2a.last_avg_error, s_a2a.combined_delay);
         
  if (converged || s_a2a.iter >= CALIB_A2A_ITERATIONS) {
      RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[CALIB] SUCCESS! Converged. Setting DONE status.");
      cfg->calib.enable_anchor_auto_calib = false;
      sys_config_save();
      /* Set DONE so anchor master sees it in UWB ranging packets */
      sys_ranging_set_calib_status(SYS_CALIB_STATUS_DONE);
      s_app_state = ANCHOR_STATE_CALIB_DONE;
      s_done_start_ms = HAL_GetTick();
      /* Reset ranging state so we can immediately start as normal responder */
      s_ranging_active = false;
      s_anchor_resp_active = false;
  } else {
      calib_reset();
  }
}

static void calib_next_turn(void) {
  /* Abort current ranging to clear hardware and state machine for next turn */
  sys_ranging_abort();

  s_current_turn = (s_current_turn % NUM_ANCHORS) + 1;
  
  if (s_current_turn == ANCHOR_MASTER) {
      calib_calculate_and_adjust();
  }

  s_turn_start_ms = HAL_GetTick();
  s_last_act_ms = HAL_GetTick();
  s_heard_poll = false;
  s_ranging_active = false;
  s_anchor_resp_active = false;
}

static void calib_process_round(uint32_t rx_timeout_ms) {
  uint8_t my_id = sys_config_get()->uwb.device_id;
  bool is_my_turn = (my_id == s_current_turn);
  
  /* 1. TURN WATCHDOG */
  uint32_t now = HAL_GetTick();
  if (is_my_turn) {
      /* Add ID-based jitter to Initiator timeout to break permanent collisions */
      uint32_t initiator_timeout = CALIB_TURN_TIMEOUT_MS + (my_id * 100U);
      if ((now - s_turn_start_ms > initiator_timeout) || 
          ((s_peer_ready_mask & s_peer_expected_mask) == s_peer_expected_mask)) {
          RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[CALIB] My turn %u finished. Next...", s_current_turn);
          calib_next_turn();
          return;
      }
  } else {
      /* RESPONDER MODE: 
       * 1. If we haven't started yet, only wait forever if we are in the MASTER turn.
       * 2. If we've started or we are in a non-master turn, timeout after 4s to recover from skipped turns.
       */
      bool can_timeout = s_system_started || s_heard_poll || (s_current_turn != ANCHOR_MASTER);
      if (can_timeout && (now - s_last_act_ms > CALIB_TURN_TIMEOUT_MS)) {
          RLOG_W(LOG_OBJECT_CODE_ANCHOR, "[CALIB] Turn %u Silence -> Next %u (gap=%ums tick=%u)", 
                 s_current_turn, (uint8_t)((s_current_turn % NUM_ANCHORS) + 1),
                 (uint32_t)(now - s_last_act_ms), (uint32_t)now);
          calib_next_turn();
          return;
      }
  }

  /* 2. RANGING EXECUTION 
   * Use CONSTANT IDs list [1, 2, 3, 4] to ensure Slot X always belongs to Anchor X.
   * This prevents "slot mismatch" errors if nodes are temporarily out of turn sync.
   */
  uint8_t all_ids[MAX_ANCHORS_SUPPORTED];
  uint8_t n_all = NUM_ANCHORS;
  for (uint8_t i = 0; i < n_all; i++) {
    all_ids[i] = i + 1;
  }

  if (is_my_turn) {
    if (!s_ranging_active) {
      /* Added missing sequence_num param */
      sys_ranging_err_t start_err = sys_ranging_tag_start_tdma(n_all, all_ids, s_round_seq, rx_timeout_ms);
      if (start_err == SYS_RANGING_OK) {
        s_ranging_active = true;
        s_last_act_ms = now;
        s_done_start_ms = 0; /* Reuse s_done_start_ms or define a new static for watchdog. Let's use a local static */
      } else if (start_err == SYS_RANGING_ERR_BUSY) {
        static uint32_t s_tag_wd_ms = 0;
        if (s_tag_wd_ms == 0) s_tag_wd_ms = now;
        else if (now - s_tag_wd_ms > 1000) {
            sys_ranging_abort();
            s_tag_wd_ms = 0;
            RLOG_W(LOG_OBJECT_CODE_ANCHOR, "[CALIB] Hard watchdog triggered for TAG start");
        }
      }
      return;
    }
    
    sys_ranging_err_t err = sys_ranging_tag_process_tdma(n_all, all_ids, rx_timeout_ms);
    if (err != SYS_RANGING_ERR_BUSY) {
      s_ranging_active = false;
      s_round_seq++; /* Increment TDMA seq after each POLL round finishes */
      if (err == SYS_RANGING_OK) {
        s_last_act_ms = HAL_GetTick();
        sys_ranging_multi_result_t multi_results;
        if (sys_ranging_tag_get_results_tdma(&multi_results) == SYS_RANGING_OK) {
          for (uint8_t i = 0; i < multi_results.count; i++) {
            sys_ranging_result_t *res = &multi_results.results[i];
            bool process = res->valid;
#if CALIB_STUB_MODE
            process = true;
#endif
            if (process && res->anchor_id > 0 && res->anchor_id <= MAX_ANCHORS_SUPPORTED) {
              uint8_t idx = res->anchor_id - 1;
              if (res->calib_status == SYS_CALIB_STATUS_DONE) {
                s_peer_done_mask |= (1 << idx);
              }

              if (!(s_peer_ready_mask & (1 << idx))) {
                float known = calib_get_ref_distance_3d(my_id, res->anchor_id);
                float dist = res->distance_m;
#if CALIB_STUB_MODE
                if (!res->valid || dist < 0.01f) dist = 1.0f;
#endif
                /* Apply public median filter before adding to calibration batch */
                float d_filtered = mw_filter_median_update(&s_calib_medians[idx], dist);

                if (mw_calib_add_sample(&s_peer_calib[idx], d_filtered)) {
                  if (s_peer_calib[idx].count > 15) {
                    RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[CALIB] Peer %u Sample %u: %.3fm", 
                           res->anchor_id, s_peer_calib[idx].count, dist);
                  }
                  float m, s;
                  if (mw_calib_compute_stats(&s_peer_calib[idx], &m, &s)
#if CALIB_STUB_MODE
                      || true
#endif
                  ) {
                    RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[CALIB] Peer %u READY. mean=%.3fm std=%.3fm", res->anchor_id, m, s);
                    mw_calib_a2a_accum_pair(&s_a2a, m, known);
                    s_peer_ready_mask |= (1 << idx);
                  } else {
                    RLOG_W(LOG_OBJECT_CODE_ANCHOR, "[CALIB] Peer %u REJECTED (std=%.3fm). Restarting.", res->anchor_id, s);
                    mw_calib_reset(&s_peer_calib[idx], &s_anchor_calib_cfg, 0U);
                  }
                }
              }
            }
          }
        }
      }
    }
  } else {
    /* RESPONDER MODE */
    if (!s_anchor_resp_active) {
      sys_ranging_err_t start_err = sys_ranging_anchor_start_tdma(my_id, n_all, all_ids, 60U);
      if (start_err == SYS_RANGING_OK) {
        s_anchor_resp_active = true;
      } else if (start_err == SYS_RANGING_ERR_BUSY) {
        static uint32_t s_anc_wd_ms = 0;
        if (s_anc_wd_ms == 0) s_anc_wd_ms = now;
        else if (now - s_anc_wd_ms > 1000) {
            sys_ranging_abort();
            s_anc_wd_ms = 0;
            RLOG_W(LOG_OBJECT_CODE_ANCHOR, "[CALIB] Hard watchdog triggered for ANCHOR start");
        }
      }
      return;
    }
    sys_ranging_err_t err = sys_ranging_anchor_process_tdma(n_all, all_ids, rx_timeout_ms);
    if (err != SYS_RANGING_ERR_BUSY) {
      s_anchor_resp_active = false;
      if (err == SYS_RANGING_OK) {
        s_last_act_ms = HAL_GetTick();
        s_heard_poll = true;
        s_system_started = true;
        sys_ranging_result_t res;
        sys_ranging_anchor_get_result_tdma(&res); /* Clear state machine */
      }
    }
  }
}
#endif

app_err_t app_anchor_init(void) {
  sys_config_t *cfg = sys_config_get();
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "===== ANCHOR #%u =====", cfg->uwb.device_id);
  
#if ENABLE_ANCHOR_AUTO_CALIB
  if (cfg->calib.enable_anchor_auto_calib) {
    s_app_state = ANCHOR_STATE_CALIB_COLLECTING;
    s_peer_expected_mask = 0;
    for (uint8_t i = 1; i <= NUM_ANCHORS; i++) {
      if (i != cfg->uwb.device_id) s_peer_expected_mask |= (1 << (i - 1));
    }
    
    /* Fixed a2a init with config struct */
    mw_calib_a2a_config_t a2a_cfg = {
      .m_to_dw_units = CALIB_A2A_M_TO_DW_UNITS,
      .damping       = CALIB_A2A_DAMPING,
      .ant_min       = 10000,
      .ant_max       = 40000,
      .iterations    = CALIB_A2A_ITERATIONS
    };
    mw_calib_a2a_init(&s_a2a, &a2a_cfg, (uint16_t)(cfg->uwb.tx_antenna_delay + cfg->uwb.rx_antenna_delay));
    
    calib_reset();
    RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[CALIB] Mode ACTIVE. Iter=1 Mask=0x%02X", s_peer_expected_mask);
  } else {
    s_app_state = ANCHOR_STATE_NORMAL;
  }
#else
  s_app_state = ANCHOR_STATE_NORMAL;
#endif
  return APP_OK;
}

void app_anchor_process(void *arg) {
  (void)arg;
  uint32_t rx_timeout_ms = 75U;

#if ENABLE_ANCHOR_AUTO_CALIB
  if (s_app_state == ANCHOR_STATE_CALIB_COLLECTING || s_app_state == ANCHOR_STATE_CALIB_DONE) {
    calib_process_round(rx_timeout_ms);

    if (s_app_state == ANCHOR_STATE_CALIB_DONE) {
      uint32_t now = HAL_GetTick();
      uint32_t elapsed = now - s_done_start_ms;
      bool all_peers_done = ((s_peer_done_mask & s_peer_expected_mask) == s_peer_expected_mask);

      /* Reset if:
       * 1. All peers are DONE (Master checks this to sync the whole network)
       * 2. OR 60s grace period passed (safety timeout)
       */
      bool should_reset = (elapsed > CALIB_DONE_GRACE_MS);
      
      /* Special case: Master waits for everyone then triggers its own reset.
       * Regular anchors will see Master is DONE and eventually reset by timeout or by seeing others. */
      if (all_peers_done) {
          static uint32_t s_all_done_tick = 0;
          if (s_all_done_tick == 0) s_all_done_tick = now;
          
          /* Wait 5 more seconds after everyone is done just to be extra sure */
          if (now - s_all_done_tick > 5000) {
              should_reset = true;
              RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[CALIB] All peers DONE. Resetting network...");
          }
      }

      if (should_reset) {
        RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[CALIB] Resetting...");
        HAL_NVIC_SystemReset();
      }
    }
    return;
  }
#endif

  if (s_app_state == ANCHOR_STATE_NORMAL) {
    uint8_t my_id = sys_config_get()->uwb.device_id;
    uint8_t all_ids[MAX_ANCHORS_SUPPORTED];
    uint8_t n_all = 0;
    for (uint8_t i = 1; i <= NUM_ANCHORS; i++) {
        all_ids[n_all++] = i;
    }
    if (!s_ranging_active) {
      if (sys_ranging_anchor_start_tdma(my_id, n_all, all_ids, rx_timeout_ms) == SYS_RANGING_OK) {
        uint8_t slot = sys_ranging_get_current_slot();
        RLOG_I(LOG_OBJECT_CODE_ANCHOR, "Ranging started (Slot %u)", slot);
        s_ranging_active = true;
      }
      return;
    }
    sys_ranging_err_t err = sys_ranging_anchor_process_tdma(n_all, all_ids, rx_timeout_ms);
    if (err != SYS_RANGING_ERR_BUSY) {
      if (err == SYS_RANGING_OK) {
        sys_ranging_result_t res;
        sys_ranging_anchor_get_result_tdma(&res); /* Reset state machine */
      }
      s_ranging_active = false;
    }
  }
}

void app_anchor_on_button(bsp_io_button_event_t event) {
  if (event == BSP_IO_EVENT_DOUBLE_CLICK) {
    sys_config_get()->calib.enable_anchor_auto_calib = true;
    sys_config_save();
    HAL_NVIC_SystemReset();
  }
}