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
#define CALIB_SUMMARY_COLLECTOR_ID 4U
#define CALIB_SUMMARY_RX_WINDOW_MS 300U

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
typedef struct {
  uint8_t  peer_id;
  float    known_m;
  float    mean_m;
  float    last_filtered_m;
  float    std_m;
  float    err_m;
  float    timeout_rate;
  uint16_t sample_count;
  uint16_t valid_count;
  uint16_t timeout_count;
  bool     ready;
  bool     usable;
} calib_pair_result_t;

static const mw_calib_config_t s_anchor_calib_cfg = {
  .samples_per_round    = CALIB_ANCHOR_SAMPLES,
  .min_valid_distance_m = 0.01f,
  .max_valid_distance_m = 50.0f,
  .max_std_m            = CALIB_ANCHOR_MAX_STD_M,
};

static mw_calib_ctx_t     s_peer_calib[MAX_ANCHORS_SUPPORTED] = {0};
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
static uint8_t            s_summary_epoch_id = 1U;
static uint8_t            s_summary_ready_mask = 0U;
static bool               s_summary_done = false;
static sys_calib_pair_summary_msg_t s_summary_by_anchor[MAX_ANCHORS_SUPPORTED] = {0};
static median_filter_1d_t s_calib_medians[MAX_ANCHORS_SUPPORTED] = {0};
static calib_pair_result_t s_pair_results[MAX_ANCHORS_SUPPORTED] = {0};
#endif

#if ENABLE_ANCHOR_AUTO_CALIB
static bool s_ranging_active     = false;
static bool s_anchor_resp_active = false;
#endif

static anchor_app_state_t s_app_state = ANCHOR_STATE_IDLE;

/* Private functions -------------------------------------------------- */
#if ENABLE_ANCHOR_AUTO_CALIB
static uint8_t calib_count_mask_bits(uint8_t mask) {
  uint8_t count = 0U;
  while (mask != 0U) {
    count += (uint8_t)(mask & 1U);
    mask >>= 1U;
  }
  return count;
}

static calib_pair_result_t *calib_pair_for_idx(uint8_t idx) {
  if (idx >= MAX_ANCHORS_SUPPORTED) {
    return NULL;
  }
  return &s_pair_results[idx];
}

static void calib_pair_note_valid(uint8_t idx, const sys_ranging_result_t *res) {
  calib_pair_result_t *pair = calib_pair_for_idx(idx);
  if (!pair || !res) {
    return;
  }
  if (pair->ready) {
    return;
  }

  pair->valid_count++;
}

static void calib_pair_note_missing_mask(uint8_t seen_mask) {
  uint8_t missing_mask = (uint8_t)(s_peer_expected_mask & (uint8_t)(~seen_mask));
  missing_mask &= (uint8_t)(~s_peer_ready_mask);

  for (uint8_t i = 0; i < MAX_ANCHORS_SUPPORTED; i++) {
    if (missing_mask & (uint8_t)(1U << i)) {
      s_pair_results[i].timeout_count++;
    }
  }
}

static void calib_pair_store_ready(uint8_t idx,
                                   uint8_t peer_id,
                                   float known_m,
                                   float mean_m,
                                   float last_filtered_m,
                                   float std_m) {
  calib_pair_result_t *pair = calib_pair_for_idx(idx);
  if (!pair) {
    return;
  }

  pair->peer_id = peer_id;
  pair->known_m = known_m;
  pair->mean_m = mean_m;
  pair->last_filtered_m = last_filtered_m;
  pair->std_m = std_m;
  pair->err_m = mean_m - known_m;
  pair->sample_count = s_peer_calib[idx].samples_per_round;
  uint16_t total_attempts = (uint16_t)(pair->valid_count + pair->timeout_count);
  pair->timeout_rate = (total_attempts > 0U)
                         ? ((float)pair->timeout_count / (float)total_attempts)
                         : 1.0f;
  pair->ready = true;
  pair->usable = (std_m <= CALIB_ANCHOR_MAX_STD_M)
                 && (pair->timeout_rate <= CALIB_A2A_MAX_TIMEOUT_RATE);
}

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
    return -1.0f;
}

static void calib_reset(void) {
  for (uint8_t i = 0; i < MAX_ANCHORS_SUPPORTED; i++) {
    mw_calib_reset(&s_peer_calib[i], &s_anchor_calib_cfg, 0U);
    memset(&s_calib_medians[i], 0, sizeof(median_filter_1d_t));
    memset(&s_pair_results[i], 0, sizeof(calib_pair_result_t));
    s_pair_results[i].peer_id = (uint8_t)(i + 1U);
  }
  s_peer_ready_mask = 0;
  s_summary_done = false;
  s_system_started = false;
  s_turn_start_ms = HAL_GetTick();
  s_last_act_ms = HAL_GetTick();
  s_current_turn = ANCHOR_MASTER;
}

static void calib_finish_v1(sys_calib_status_t status) {
  sys_config_get()->calib.enable_anchor_auto_calib = false;
  sys_config_save();
  sys_ranging_set_calib_status(status);
  s_app_state = ANCHOR_STATE_CALIB_DONE;
  s_done_start_ms = HAL_GetTick();
  s_ranging_active = false;
  s_anchor_resp_active = false;
}

static bool calib_build_pair_summary(uint8_t sender_id, sys_calib_pair_summary_msg_t *summary) {
  if (!summary || sender_id == 0U || sender_id > MAX_ANCHORS_SUPPORTED) {
    return false;
  }

  memset(summary, 0, sizeof(*summary));
  summary->epoch_id = s_summary_epoch_id;
  summary->sender_id = sender_id;
  sys_config_t *cfg = sys_config_get();
  summary->current_tx_delay = cfg->uwb.tx_antenna_delay;
  summary->current_rx_delay = cfg->uwb.rx_antenna_delay;
  summary->current_combined_delay = (uint16_t)(cfg->uwb.tx_antenna_delay + cfg->uwb.rx_antenna_delay);

  for (uint8_t i = 0; i < MAX_ANCHORS_SUPPORTED; i++) {
    if ((s_peer_expected_mask & (uint8_t)(1U << i)) == 0U) {
      continue;
    }
    if (summary->pair_count >= SYS_CALIB_PAIR_SUMMARY_MAX_PAIRS) {
      break;
    }

    calib_pair_result_t *pair = &s_pair_results[i];
    if (!pair->ready) {
      continue;
    }

    sys_calib_pair_summary_item_t *item = &summary->pair[summary->pair_count++];
    item->peer_id = pair->peer_id;
    item->known_m = pair->known_m;
    item->mean_m = pair->mean_m;
    item->std_m = pair->std_m;
    item->timeout_rate = pair->timeout_rate;
    item->valid_count = pair->valid_count;
  }

  return (summary->pair_count > 0U);
}

static bool calib_store_pair_summary(const sys_calib_pair_summary_msg_t *summary) {
  if (!summary || summary->epoch_id != s_summary_epoch_id ||
      summary->sender_id == 0U || summary->sender_id > NUM_ANCHORS ||
      summary->pair_count > SYS_CALIB_PAIR_SUMMARY_MAX_PAIRS) {
    return false;
  }

  s_summary_by_anchor[summary->sender_id - 1U] = *summary;
  s_summary_ready_mask |= (uint8_t)(1U << (summary->sender_id - 1U));
  return true;
}

static void calib_collect_remote_summaries(void) {
  uint32_t start_ms = HAL_GetTick();
  const uint8_t expected_mask = (uint8_t)((1U << NUM_ANCHORS) - 1U);

  while (((s_summary_ready_mask & expected_mask) != expected_mask) &&
         ((HAL_GetTick() - start_ms) < CALIB_SUMMARY_RX_WINDOW_MS)) {
    sys_calib_pair_summary_msg_t summary;
    sys_ranging_err_t err = sys_ranging_poll_calib_pair_summary(&summary, 40U);
    if (err == SYS_RANGING_OK) {
      if (calib_store_pair_summary(&summary)) {
        RLOG_I(LOG_OBJECT_CODE_ANCHOR,
               "[CALIB][SUMMARY] RX sender=A%u epoch=%u pairs=%u mask=0x%02X",
               summary.sender_id, summary.epoch_id, summary.pair_count, s_summary_ready_mask);
      } else {
        RLOG_W(LOG_OBJECT_CODE_ANCHOR,
               "[CALIB][SUMMARY] Ignored stale/invalid summary sender=A%u epoch=%u current=%u",
               summary.sender_id, summary.epoch_id, s_summary_epoch_id);
      }
    }
  }
}

static bool calib_solve_4x4(float a[4][4], float b[4], float x[4]) {
  for (uint8_t i = 0; i < 4U; i++) {
    uint8_t pivot = i;
    float pivot_abs = fabsf(a[i][i]);
    for (uint8_t r = (uint8_t)(i + 1U); r < 4U; r++) {
      float v = fabsf(a[r][i]);
      if (v > pivot_abs) {
        pivot = r;
        pivot_abs = v;
      }
    }
    if (pivot_abs < 1.0e-6f) {
      return false;
    }
    if (pivot != i) {
      for (uint8_t c = i; c < 4U; c++) {
        float tmp = a[i][c];
        a[i][c] = a[pivot][c];
        a[pivot][c] = tmp;
      }
      float tb = b[i];
      b[i] = b[pivot];
      b[pivot] = tb;
    }

    float diag = a[i][i];
    for (uint8_t c = i; c < 4U; c++) {
      a[i][c] /= diag;
    }
    b[i] /= diag;

    for (uint8_t r = 0U; r < 4U; r++) {
      if (r == i) {
        continue;
      }
      float factor = a[r][i];
      for (uint8_t c = i; c < 4U; c++) {
        a[r][c] -= factor * a[i][c];
      }
      b[r] -= factor * b[i];
    }
  }

  for (uint8_t i = 0; i < 4U; i++) {
    x[i] = b[i];
  }
  return true;
}

static bool calib_summary_item_usable(const sys_calib_pair_summary_item_t *item) {
  return item &&
         item->peer_id > 0U && item->peer_id <= NUM_ANCHORS &&
         item->valid_count >= CALIB_ANCHOR_SAMPLES &&
         item->std_m <= CALIB_ANCHOR_MAX_STD_M &&
         item->timeout_rate <= CALIB_A2A_MAX_TIMEOUT_RATE;
}

static void calib_run_a4_solver(sys_config_t *cfg) {
  const uint8_t expected_mask = (uint8_t)((1U << NUM_ANCHORS) - 1U);
  float ata[4][4] = {0};
  float atb[4] = {0};
  float bias[4] = {0};
  float directed_error[4][4] = {0};
  bool directed_valid[4][4] = {0};
  bool undirected_valid[4][4] = {0};
  uint8_t usable_edges = 0U;
  uint8_t rejected_edges = 0U;
  uint8_t usable_pairs = 0U;
  const uint8_t required_pairs = (uint8_t)((NUM_ANCHORS * (NUM_ANCHORS - 1U)) / 2U);

  if ((s_summary_ready_mask & expected_mask) != expected_mask) {
    RLOG_W(LOG_OBJECT_CODE_ANCHOR,
           "[CALIB][SOLVER] decision FAIL missing summaries mask=0x%02X expected=0x%02X",
           s_summary_ready_mask, expected_mask);
    calib_finish_v1(SYS_CALIB_STATUS_NORMAL);
    return;
  }

  for (uint8_t sender = 1U; sender <= NUM_ANCHORS; sender++) {
    const sys_calib_pair_summary_msg_t *summary = &s_summary_by_anchor[sender - 1U];
    for (uint8_t p = 0U; p < summary->pair_count; p++) {
      const sys_calib_pair_summary_item_t *item = &summary->pair[p];
      if (!calib_summary_item_usable(item) || item->peer_id == sender) {
        rejected_edges++;
        continue;
      }

      uint8_t i = (uint8_t)(sender - 1U);
      uint8_t j = (uint8_t)(item->peer_id - 1U);
      float e = item->mean_m - item->known_m;
      directed_error[i][j] = e;
      directed_valid[i][j] = true;
      uint8_t a = (i < j) ? i : j;
      uint8_t b = (i < j) ? j : i;
      if (!undirected_valid[a][b]) {
        undirected_valid[a][b] = true;
        usable_pairs++;
      }

      ata[i][i] += 1.0f;
      ata[i][j] += 1.0f;
      ata[j][i] += 1.0f;
      ata[j][j] += 1.0f;
      atb[i] += e;
      atb[j] += e;
      usable_edges++;
    }
  }

  if (usable_pairs < required_pairs) {
    RLOG_W(LOG_OBJECT_CODE_ANCHOR,
           "[CALIB][SOLVER] decision FAIL incomplete matrix usable_pairs=%u/%u usable_edges=%u rejected_edges=%u",
           usable_pairs, required_pairs, usable_edges, rejected_edges);
    calib_finish_v1(SYS_CALIB_STATUS_NORMAL);
    return;
  }

  if (!calib_solve_4x4(ata, atb, bias)) {
    RLOG_W(LOG_OBJECT_CODE_ANCHOR,
           "[CALIB][SOLVER] decision FAIL usable_edges=%u rejected_edges=%u",
           usable_edges, rejected_edges);
    calib_finish_v1(SYS_CALIB_STATUS_NORMAL);
    return;
  }

  float residual_sq_sum = 0.0f;
  float residual_max = 0.0f;
  for (uint8_t sender = 1U; sender <= NUM_ANCHORS; sender++) {
    const sys_calib_pair_summary_msg_t *summary = &s_summary_by_anchor[sender - 1U];
    for (uint8_t p = 0U; p < summary->pair_count; p++) {
      const sys_calib_pair_summary_item_t *item = &summary->pair[p];
      if (!calib_summary_item_usable(item) || item->peer_id == sender) {
        continue;
      }

      float e = item->mean_m - item->known_m;
      float pred = bias[sender - 1U] + bias[item->peer_id - 1U];
      float residual_abs = fabsf(e - pred);
      residual_sq_sum += residual_abs * residual_abs;
      if (residual_abs > residual_max) {
        residual_max = residual_abs;
      }
    }
  }

  float residual_rms = sqrtf(residual_sq_sum / (float)usable_edges);
  float limit = (cfg->calib.error_threshold_m > 0.0f)
                ? cfg->calib.error_threshold_m
                : CALIB_A2A_CONVERGENCE_MAX_ABS_M;
  bool pass = (residual_rms <= limit) && (residual_max <= limit);
  uint16_t base_combined = (uint16_t)(cfg->uwb.tx_antenna_delay + cfg->uwb.rx_antenna_delay);

  cfg->calib.last_pair_error_rms_m = residual_rms;
  cfg->calib.last_pair_error_max_abs_m = residual_max;
  cfg->calib.last_usable_pair_count = usable_edges;
  cfg->calib.last_rejected_pair_count = rejected_edges;

  RLOG_I(LOG_OBJECT_CODE_ANCHOR,
         "[CALIB][SOLVER] b1=%+.4fm b2=%+.4fm b3=%+.4fm b4=%+.4fm",
         bias[0], bias[1], bias[2], bias[3]);
  RLOG_I(LOG_OBJECT_CODE_ANCHOR,
         "[CALIB][SOLVER] residual_rms=%.4fm residual_max=%.4fm usable_pairs=%u/%u usable_edges=%u rejected_edges=%u",
         residual_rms, residual_max, usable_pairs, required_pairs, usable_edges, rejected_edges);

  for (uint8_t i = 0U; i < NUM_ANCHORS; i++) {
    for (uint8_t j = (uint8_t)(i + 1U); j < NUM_ANCHORS; j++) {
      if (directed_valid[i][j] && directed_valid[j][i]) {
        float symmetry_error = fabsf(directed_error[i][j] - directed_error[j][i]);
        RLOG_I(LOG_OBJECT_CODE_ANCHOR,
               "[CALIB][SOLVER] symmetry_error A%u-A%u=%.4fm forward=%+.4fm reverse=%+.4fm",
               (unsigned)(i + 1U), (unsigned)(j + 1U), symmetry_error,
               directed_error[i][j], directed_error[j][i]);
      }
    }
  }

  for (uint8_t i = 0U; i < NUM_ANCHORS; i++) {
    int32_t delta_dw = (int32_t)(bias[i] * CALIB_A2A_M_TO_DW_UNITS);
    uint16_t anchor_combined = s_summary_by_anchor[i].current_combined_delay;
    if (anchor_combined == 0U) {
      anchor_combined = base_combined;
    }
    int32_t suggested_combined = (int32_t)anchor_combined + delta_dw;
    if (suggested_combined < (int32_t)CALIB_A2A_ANT_MIN) {
      suggested_combined = (int32_t)CALIB_A2A_ANT_MIN;
    }
    if (suggested_combined > (int32_t)CALIB_A2A_ANT_MAX) {
      suggested_combined = (int32_t)CALIB_A2A_ANT_MAX;
    }
    RLOG_I(LOG_OBJECT_CODE_ANCHOR,
           "[CALIB][SOLVER] candidate_A%u current_tx=%u current_rx=%u current_combined=%u bias=%+.4fm delta_dw=%ld suggested_combined=%ld suggested_tx_rx=%ld",
           (unsigned)(i + 1U),
           s_summary_by_anchor[i].current_tx_delay,
           s_summary_by_anchor[i].current_rx_delay,
           anchor_combined,
           bias[i],
           (long)delta_dw,
           (long)suggested_combined,
           (long)(suggested_combined / 2));
  }

  RLOG_I(LOG_OBJECT_CODE_ANCHOR,
         "[CALIB][SOLVER] decision %s limit=%.4fm sign_note=\"V1 uses same sign as mw_calib_a2a_apply_gradient: combined += bias_m * factor; verify before V2 apply\"",
         pass ? "PASS" : "FAIL", limit);
  calib_finish_v1(pass ? SYS_CALIB_STATUS_DONE : SYS_CALIB_STATUS_NORMAL);
}

static void calib_calculate_and_adjust(void) {
  if (s_app_state == ANCHOR_STATE_CALIB_DONE) return;

  sys_config_t *cfg = sys_config_get();
  uint8_t expected_pair_count = calib_count_mask_bits(s_peer_expected_mask);
  uint8_t ready_pair_count = calib_count_mask_bits((uint8_t)(s_peer_ready_mask & s_peer_expected_mask));
  if (ready_pair_count == 0U) {
      RLOG_W(LOG_OBJECT_CODE_ANCHOR,
             "[CALIB] No valid peer batches ready; continuing collection.");
      return;
  }
  if (ready_pair_count < expected_pair_count) {
      RLOG_W(LOG_OBJECT_CODE_ANCHOR,
             "[CALIB] Incomplete batch: pairs=%u/%u ready_mask=0x%02X expected=0x%02X. Continuing collection.",
             (unsigned)ready_pair_count, (unsigned)expected_pair_count,
             (unsigned)s_peer_ready_mask, (unsigned)s_peer_expected_mask);
      return;
  }

  if (s_summary_done) {
      return;
  }

  sys_calib_pair_summary_msg_t summary;
  if (!calib_build_pair_summary(cfg->uwb.device_id, &summary)) {
      RLOG_W(LOG_OBJECT_CODE_ANCHOR, "[CALIB][SUMMARY] Failed to build local summary");
      calib_finish_v1(SYS_CALIB_STATUS_NORMAL);
      return;
  }

  if (cfg->uwb.device_id == CALIB_SUMMARY_COLLECTOR_ID) {
      memset(s_summary_by_anchor, 0, sizeof(s_summary_by_anchor));
      s_summary_ready_mask = 0U;
      calib_store_pair_summary(&summary);
      RLOG_I(LOG_OBJECT_CODE_ANCHOR,
             "[CALIB][SUMMARY] A4 local summary epoch=%u pairs=%u",
             summary.epoch_id, summary.pair_count);
      calib_collect_remote_summaries();
      calib_run_a4_solver(cfg);
  } else {
      uint8_t slot_id = cfg->uwb.device_id;
      sys_ranging_err_t err = sys_ranging_send_calib_pair_summary(&summary, slot_id);
      if (err == SYS_RANGING_OK) {
        RLOG_I(LOG_OBJECT_CODE_ANCHOR,
               "[CALIB][SUMMARY] Sent to A4 epoch=%u sender=A%u pairs=%u slot=%u",
               summary.epoch_id, summary.sender_id, summary.pair_count, slot_id);
        calib_finish_v1(SYS_CALIB_STATUS_DONE);
      } else {
        RLOG_W(LOG_OBJECT_CODE_ANCHOR,
               "[CALIB][SUMMARY] Send failed err=%d sender=A%u",
               (int)err, summary.sender_id);
        calib_finish_v1(SYS_CALIB_STATUS_NORMAL);
      }
  }
  s_summary_done = true;
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
  if (s_app_state == ANCHOR_STATE_CALIB_DONE) {
      is_my_turn = false;
  }
  
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
          uint8_t seen_mask = 0U;
          for (uint8_t i = 0; i < multi_results.count; i++) {
            sys_ranging_result_t *res = &multi_results.results[i];
            bool process = res->valid;
#if CALIB_STUB_MODE
            process = true;
#endif
            if (process && res->anchor_id > 0 && res->anchor_id <= MAX_ANCHORS_SUPPORTED) {
              uint8_t idx = res->anchor_id - 1;
              seen_mask |= (uint8_t)(1U << idx);
              calib_pair_note_valid(idx, res);
              if (res->calib_status == SYS_CALIB_STATUS_DONE) {
                s_peer_done_mask |= (1 << idx);
              }

              if (!(s_peer_ready_mask & (1 << idx))) {
                float known = calib_get_ref_distance_3d(my_id, res->anchor_id);
                if (known <= 0.0f) {
                  RLOG_W(LOG_OBJECT_CODE_ANCHOR, "[CALIB] Peer %u skipped: missing reference distance", res->anchor_id);
                  continue;
                }
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
                    calib_pair_store_ready(idx, res->anchor_id, known, m, d_filtered, s);
                    s_peer_ready_mask |= (1 << idx);
                  } else {
                    RLOG_W(LOG_OBJECT_CODE_ANCHOR, "[CALIB] Peer %u REJECTED (std=%.3fm). Restarting.", res->anchor_id, s);
                    mw_calib_reset(&s_peer_calib[idx], &s_anchor_calib_cfg, 0U);
                  }
                }
              }
            }
          }
          calib_pair_note_missing_mask(seen_mask);
        } else {
          calib_pair_note_missing_mask(0U);
        }
      } else {
        calib_pair_note_missing_mask(0U);
      }
    }
  } else {
    /* RESPONDER MODE */
    if (!s_anchor_resp_active) {
      sys_ranging_err_t start_err = sys_ranging_anchor_start_tdma(my_id, n_all, all_ids, rx_timeout_ms);
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
      if (i != cfg->uwb.device_id) {
        s_peer_expected_mask |= (1 << (i - 1));
      }
    }
    
    calib_reset();
    RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[CALIB] Mutual A2A V1 ACTIVE. Epoch=%u Mask=0x%02X",
           s_summary_epoch_id, s_peer_expected_mask);
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
  sys_config_t *cfg = sys_config_get();
  uint32_t rx_timeout_ms = cfg->uwb.rx_timeout_ms;

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
    uint8_t all_ids[MAX_ANCHORS_SUPPORTED];
    uint8_t n_all = 0;
    for (uint8_t i = 1; i <= NUM_ANCHORS; i++) {
        all_ids[n_all++] = i;
    }
    sys_ranging_err_t err = sys_ranging_anchor_process_tdma(n_all, all_ids, rx_timeout_ms);
    if (err == SYS_RANGING_OK) {
      sys_ranging_result_t res;
      if (sys_ranging_anchor_get_result_tdma(&res) == SYS_RANGING_OK && res.valid) {
        RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[DIST] Anchor #%u: tag_distance=%.3fm",
               cfg->uwb.device_id,
               res.distance_m);
      }
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
