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
#define ANCHOR_MASTER                    4U
#define CALIB_TURN_TIMEOUT_MS            4000U
#define CALIB_DONE_GRACE_MS              60000U
#define CALIB_SUMMARY_RX_WINDOW_MS       3000U
#define SURVEY_FINISH_RETRY_MS           4000U
#define SURVEY_FINISH_ACK_WINDOW_MS      500U
#define SURVEY_PEER_FINISH_GRACE_MS      6000U
#define ANCHOR_SURVEY_MAX_RESIDUAL_M     0.15f

/* Private types ------------------------------------------------------ */
typedef enum
{
  ANCHOR_STATE_IDLE = 0,
  ANCHOR_STATE_NORMAL,
  ANCHOR_STATE_CALIB_COLLECTING,
  ANCHOR_STATE_CALIB_CALCULATE,
  ANCHOR_STATE_CALIB_DONE
} anchor_app_state_t;

typedef struct
{
  uint8_t  peer_id;
  float    known_m;
  float    mean_m;
  float    std_m;
  float    timeout_rate;
  uint16_t sample_count;
  uint16_t valid_count;
  uint16_t timeout_count;
  bool     ready;
} calib_pair_result_t;

/* Private variables -------------------------------------------------- */
static const mw_calib_config_t s_anchor_calib_cfg = {
  .samples_per_round    = CALIB_ANCHOR_SAMPLES,
  .min_valid_distance_m = 0.01f,
  .max_valid_distance_m = 50.0f,
  .max_std_m            = CALIB_ANCHOR_MAX_STD_M,
};

static mw_calib_ctx_t      s_peer_calib[MAX_ANCHORS_SUPPORTED] = { 0 };
static median_filter_1d_t  s_calib_medians[MAX_ANCHORS_SUPPORTED] = { 0 };
static calib_pair_result_t s_pair_results[MAX_ANCHORS_SUPPORTED] = { 0 };

/* Survey lifecycle state */
static bool     s_ranging_active     = false;
static bool     s_anchor_resp_active = false;
static bool     s_survey_active      = false;
static bool     s_system_started     = false;
static uint32_t s_status_log_ms      = 0U;

/* Turn and local collection state */
static uint8_t  s_current_turn       = ANCHOR_MASTER;
static uint8_t  s_round_seq          = 0U;
static uint8_t  s_peer_ready_mask    = 0U;
static uint8_t  s_peer_expected_mask = 0U;
static uint8_t  s_peer_done_mask     = 0U;
static uint32_t s_turn_start_ms      = 0U;
static uint32_t s_last_act_ms        = 0U;
static bool     s_heard_poll         = false;
static bool     s_turn_ranged_once   = false;

/* Summary exchange state */
static uint8_t s_summary_epoch_id          = 1U;
static uint8_t s_summary_ready_mask        = 0U;
static uint8_t s_last_missing_summary_mask = 0xFFU;
static bool    s_summary_done              = false;
static sys_calib_pair_summary_msg_t s_summary_by_anchor[MAX_ANCHORS_SUPPORTED] = { 0 };

/* Finish handshake state */
static uint8_t                     s_finish_ack_mask   = 0U;
static uint32_t                    s_finish_last_tx_ms = 0U;
static uint32_t                    s_done_start_ms     = 0U;
static sys_survey_finish_outcome_t s_finish_outcome   = SYS_SURVEY_FINISH_ABORT;

static anchor_app_state_t s_app_state = ANCHOR_STATE_IDLE;

/* Private functions -------------------------------------------------- */
static bool calib_anchor_id_to_slot(uint8_t anchor_id, uint8_t *slot_out);
static bool calib_slot_to_anchor_id(uint8_t slot, uint8_t *anchor_id_out);
static uint8_t calib_collector_id(void);
static uint8_t calib_next_anchor_id(uint8_t current_id);
static uint8_t calib_control_slot_for_anchor(uint8_t anchor_id);

static uint8_t anchor_fill_ids(uint8_t *anchor_ids) {
  sys_config_t *cfg = sys_config_get();
  uint32_t count = cfg->anchor_count;
  if (!anchor_ids || count == 0U || count > MAX_ANCHORS_SUPPORTED) {
    return 0U;
  }

  for (uint8_t i = 0U; i < (uint8_t)count; i++) {
    anchor_ids[i] = (uint8_t)cfg->anchor_layout[i].anchor_id;
  }
  return (uint8_t)count;
}

static const char *calib_state_name(void);
static void calib_log_status(uint32_t now, uint8_t my_id);

static uint8_t calib_anchor_count(void) {
  uint32_t count = sys_config_get()->anchor_count;
  if (count == 0U || count > MAX_ANCHORS_SUPPORTED) {
    return 0U;
  }
  return (uint8_t)count;
}

static bool calib_anchor_id_to_slot(uint8_t anchor_id, uint8_t *slot_out) {
  if (!slot_out || anchor_id == 0U || anchor_id > MAX_ANCHORS_SUPPORTED) {
    return false;
  }

  sys_config_t *cfg = sys_config_get();
  uint8_t count = calib_anchor_count();
  for (uint8_t slot = 0U; slot < count; slot++) {
    if ((uint8_t)cfg->anchor_layout[slot].anchor_id == anchor_id) {
      *slot_out = slot;
      return true;
    }
  }
  return false;
}

static bool calib_slot_to_anchor_id(uint8_t slot, uint8_t *anchor_id_out) {
  if (!anchor_id_out) {
    return false;
  }

  sys_config_t *cfg = sys_config_get();
  uint8_t count = calib_anchor_count();
  if (slot >= count) {
    return false;
  }

  uint8_t anchor_id = (uint8_t)cfg->anchor_layout[slot].anchor_id;
  if (anchor_id == 0U || anchor_id > MAX_ANCHORS_SUPPORTED) {
    return false;
  }

  *anchor_id_out = anchor_id;
  return true;
}

static uint8_t calib_collector_id(void) {
  uint8_t count = calib_anchor_count();
  uint8_t collector_id = 0U;
  if (count > 0U && calib_slot_to_anchor_id((uint8_t)(count - 1U), &collector_id)) {
    return collector_id;
  }
  return ANCHOR_MASTER;
}

static uint8_t calib_next_anchor_id(uint8_t current_id) {
  uint8_t current_slot = 0U;
  uint8_t count = calib_anchor_count();
  if (count == 0U) {
    return 0U;
  }

  if (!calib_anchor_id_to_slot(current_id, &current_slot)) {
    current_slot = 0U;
  } else {
    current_slot = (uint8_t)((current_slot + 1U) % count);
  }

  uint8_t next_id = 0U;
  return calib_slot_to_anchor_id(current_slot, &next_id) ? next_id : 0U;
}

static uint8_t calib_control_slot_for_anchor(uint8_t anchor_id) {
  uint8_t slot = 0U;
  return calib_anchor_id_to_slot(anchor_id, &slot) ? (uint8_t)(slot + 1U) : anchor_id;
}

static uint8_t calib_all_anchor_mask(void) {
  sys_config_t *cfg = sys_config_get();
  uint8_t count = calib_anchor_count();
  uint8_t mask = 0U;
  for (uint8_t i = 0U; i < count; i++) {
    uint32_t anchor_id = cfg->anchor_layout[i].anchor_id;
    if (anchor_id == 0U || anchor_id > MAX_ANCHORS_SUPPORTED) {
      return 0U;
    }
    mask |= (uint8_t)(1U << (anchor_id - 1U));
  }
  return mask;
}

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
                                   float std_m) {
  calib_pair_result_t *pair = calib_pair_for_idx(idx);
  if (!pair) {
    return;
  }

  pair->peer_id = peer_id;
  pair->known_m = known_m;
  pair->mean_m = mean_m;
  pair->std_m = std_m;
  pair->sample_count = s_peer_calib[idx].samples_per_round;
  uint16_t total_attempts = (uint16_t)(pair->valid_count + pair->timeout_count);
  pair->timeout_rate = (total_attempts > 0U)
                         ? ((float)pair->timeout_count / (float)total_attempts)
                         : 1.0f;
  pair->ready = true;
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
  s_peer_done_mask = 0;
  s_summary_ready_mask = 0U;
  s_summary_done = false;
  s_turn_ranged_once = false;
  s_system_started = false;
  s_status_log_ms = 0U;
  s_last_missing_summary_mask = 0xFFU;
  s_finish_ack_mask = 0U;
  s_finish_last_tx_ms = 0U;
  s_finish_outcome = SYS_SURVEY_FINISH_ABORT;
  s_turn_start_ms = HAL_GetTick();
  s_last_act_ms = HAL_GetTick();
  s_current_turn = calib_collector_id();
}

static void calib_finish_v1(sys_calib_status_t status) {
  uint8_t my_id = sys_config_get()->uwb.device_id;
  s_finish_outcome = (status == SYS_CALIB_STATUS_DONE)
                       ? SYS_SURVEY_FINISH_COMPLETE
                       : SYS_SURVEY_FINISH_ABORT;
  RLOG_I(LOG_OBJECT_CODE_ANCHOR,
         "[SURVEY][STATE] A%u -> DONE outcome=%s ready=0x%02X/%02X done_peers=0x%02X summaries=0x%02X",
         my_id, (status == SYS_CALIB_STATUS_DONE) ? "SUCCESS" : "FAILED",
         s_peer_ready_mask, s_peer_expected_mask, s_peer_done_mask, s_summary_ready_mask);
  s_survey_active = false;
  sys_ranging_set_calib_status(status);
  s_app_state = ANCHOR_STATE_CALIB_DONE;
  s_done_start_ms = HAL_GetTick();
  s_ranging_active = false;
  s_anchor_resp_active = false;
}

static void calib_return_to_normal(const char *reason) {
  uint8_t my_id = sys_config_get()->uwb.device_id;
  RLOG_I(LOG_OBJECT_CODE_ANCHOR,
         "[SURVEY][STATE] A%u DONE -> NORMAL reason=%s outcome=%s",
         my_id, reason,
         (s_finish_outcome == SYS_SURVEY_FINISH_COMPLETE) ? "COMPLETE" : "ABORT");
  sys_ranging_abort();
  bsp_uwb_idle();
  app_anchor_set_survey_active(false);
  sys_ranging_set_calib_status(SYS_CALIB_STATUS_NORMAL);
  s_app_state = ANCHOR_STATE_NORMAL;
  app_anchor_init();
}

static void calib_peer_poll_finish(uint8_t my_id) {
  uint8_t collector_id = calib_collector_id();
  sys_survey_finish_msg_t finish;
  if (sys_ranging_control_receive(SYS_UWB_CTRL_SURVEY_FINISH,
                                  &finish, sizeof(finish), 20U) != SYS_RANGING_OK ||
      finish.epoch_id != s_summary_epoch_id ||
      finish.collector_id != collector_id ||
      finish.outcome > SYS_SURVEY_FINISH_COMPLETE) {
    return;
  }

  s_finish_outcome = (sys_survey_finish_outcome_t)finish.outcome;
  (void)sys_ranging_control_send_ack(finish.epoch_id,
                                     my_id,
                                     SYS_UWB_CTRL_SURVEY_FINISH,
                                     finish.outcome,
                                     calib_control_slot_for_anchor(my_id));
  if (s_app_state != ANCHOR_STATE_CALIB_DONE) {
    RLOG_I(LOG_OBJECT_CODE_ANCHOR,
           "[SURVEY][STATE] A%u received %s from A%u; ACKed",
           my_id,
           (s_finish_outcome == SYS_SURVEY_FINISH_COMPLETE) ? "COMPLETE" : "ABORT",
           collector_id);
    s_app_state = ANCHOR_STATE_CALIB_DONE;
    s_done_start_ms = HAL_GetTick();
    s_ranging_active = false;
    s_anchor_resp_active = false;
  }
}

static void calib_master_process_finish(void) {
  uint32_t now = HAL_GetTick();
  uint8_t collector_id = calib_collector_id();
  const uint8_t expected_ack_mask = s_peer_expected_mask;
  if ((s_finish_ack_mask & expected_ack_mask) == expected_ack_mask) {
    calib_return_to_normal("FINISH_ACKS");
    return;
  }
  if ((now - s_done_start_ms) > CALIB_DONE_GRACE_MS) {
    calib_return_to_normal("FINISH_TIMEOUT");
    return;
  }
  if (s_finish_last_tx_ms != 0U &&
      (now - s_finish_last_tx_ms) < SURVEY_FINISH_RETRY_MS) {
    return;
  }

  s_finish_last_tx_ms = now;
  const sys_survey_finish_msg_t finish = {
    .epoch_id = s_summary_epoch_id,
    .collector_id = collector_id,
    .outcome = (uint8_t)s_finish_outcome,
  };
  if (sys_ranging_control_send(SYS_UWB_CTRL_SURVEY_FINISH,
                               &finish, sizeof(finish), 0U) != SYS_RANGING_OK) {
    return;
  }

  uint32_t ack_start_ms = HAL_GetTick();
  while ((HAL_GetTick() - ack_start_ms) < SURVEY_FINISH_ACK_WINDOW_MS) {
    sys_uwb_control_ack_msg_t ack;
    if (sys_ranging_control_receive_ack(SYS_UWB_CTRL_SURVEY_FINISH,
                                        &ack, 30U) == SYS_RANGING_OK &&
        ack.epoch_id == s_summary_epoch_id &&
        ack.acked_value == (uint8_t)s_finish_outcome &&
        ack.sender_id != collector_id) {
      uint8_t ack_slot = 0U;
      if (!calib_anchor_id_to_slot(ack.sender_id, &ack_slot)) {
        continue;
      }
      uint8_t bit = (uint8_t)(1U << (ack.sender_id - 1U));
      if ((s_finish_ack_mask & bit) == 0U) {
        s_finish_ack_mask |= bit;
        RLOG_I(LOG_OBJECT_CODE_ANCHOR,
               "[SURVEY][MASTER] finish ACK A%u mask=0x%02X/%02X",
               ack.sender_id, s_finish_ack_mask, expected_ack_mask);
      }
    }
  }
}

static bool calib_build_pair_summary(uint8_t sender_id, sys_calib_pair_summary_msg_t *summary) {
  uint8_t anchor_count = calib_anchor_count();
  uint8_t expected_pairs = (anchor_count > 0U) ? (uint8_t)(anchor_count - 1U) : 0U;
  uint8_t sender_slot = 0U;
  if (!summary || !calib_anchor_id_to_slot(sender_id, &sender_slot) ||
      expected_pairs > (NUM_ANCHORS - 1U)) {
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
    if (summary->pair_count >= expected_pairs) {
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
    /*
     * Report the accepted batch size, not the lifetime raw-result counter.
     * valid_count is uint8_t on-air and a long survey can wrap the uint16_t
     * raw counter, causing the collector to reject an otherwise READY link.
     */
    item->valid_count = (pair->sample_count > UINT8_MAX)
                          ? UINT8_MAX
                          : (uint8_t)pair->sample_count;
  }

  return summary->pair_count == expected_pairs;
}

static bool calib_store_pair_summary(const sys_calib_pair_summary_msg_t *summary) {
  uint8_t anchor_count = calib_anchor_count();
  uint8_t expected_pairs = (anchor_count > 0U) ? (uint8_t)(anchor_count - 1U) : 0U;
  uint8_t sender_slot = 0U;
  uint8_t peer_mask = 0U;
  if (!summary || summary->epoch_id != s_summary_epoch_id ||
      !calib_anchor_id_to_slot(summary->sender_id, &sender_slot) ||
      summary->pair_count != expected_pairs ||
      summary->pair_count > (NUM_ANCHORS - 1U)) {
    return false;
  }
  for (uint8_t i = 0U; i < summary->pair_count; i++) {
    const sys_calib_pair_summary_item_t *item = &summary->pair[i];
    uint8_t peer_slot = 0U;
    if (!calib_anchor_id_to_slot(item->peer_id, &peer_slot) ||
        item->peer_id == summary->sender_id ||
        !isfinite(item->mean_m) || !isfinite(item->std_m) ||
        !isfinite(item->timeout_rate)) {
      return false;
    }
    uint8_t peer_bit = (uint8_t)(1U << (item->peer_id - 1U));
    if ((peer_mask & peer_bit) != 0U) {
      return false;
    }
    peer_mask |= peer_bit;
  }

  uint8_t expected_peer_mask = (uint8_t)(calib_all_anchor_mask() &
                                         (uint8_t)(~(1U << (summary->sender_id - 1U))));
  if (peer_mask != expected_peer_mask) {
    return false;
  }

  s_summary_by_anchor[summary->sender_id - 1U] = *summary;
  s_summary_ready_mask |= (uint8_t)(1U << (summary->sender_id - 1U));
  if (summary->sender_id != calib_collector_id()) {
    s_peer_done_mask |= (uint8_t)(1U << (summary->sender_id - 1U));
  }
  return true;
}

static void calib_collect_remote_summaries(void) {
  uint32_t start_ms = HAL_GetTick();
  const uint8_t expected_mask = calib_all_anchor_mask();

  while (((s_summary_ready_mask & expected_mask) != expected_mask) &&
         ((HAL_GetTick() - start_ms) < CALIB_SUMMARY_RX_WINDOW_MS)) {
    sys_calib_pair_summary_msg_t summary;
    sys_ranging_err_t err =
        sys_ranging_control_receive(SYS_UWB_CTRL_CALIB_PAIR_SUMMARY,
                                    &summary, sizeof(summary), 40U);
    if (err == SYS_RANGING_OK) {
      uint8_t sender_slot = 0U;
      uint8_t sender_bit = calib_anchor_id_to_slot(summary.sender_id, &sender_slot)
                             ? (uint8_t)(1U << (summary.sender_id - 1U))
                             : 0U;
      bool is_new_summary = (sender_bit != 0U) &&
                            ((s_summary_ready_mask & sender_bit) == 0U);
      if (calib_store_pair_summary(&summary)) {
        sys_ranging_err_t ack_err =
            sys_ranging_control_send_ack(summary.epoch_id,
                                         calib_collector_id(),
                                         SYS_UWB_CTRL_CALIB_PAIR_SUMMARY,
                                         summary.sender_id,
                                         0U);
        if (is_new_summary) {
          if (ack_err == SYS_RANGING_OK) {
            RLOG_I(LOG_OBJECT_CODE_ANCHOR,
                   "[SURVEY][MASTER] summary A%u received+ACKed mask=0x%02X/%02X",
                   summary.sender_id, s_summary_ready_mask, expected_mask);
          } else {
            RLOG_W(LOG_OBJECT_CODE_ANCHOR,
                   "[SURVEY][MASTER] summary A%u received but ACK failed err=%d",
                   summary.sender_id, (int)ack_err);
          }
        } else if (ack_err != SYS_RANGING_OK) {
          RLOG_W(LOG_OBJECT_CODE_ANCHOR,
                 "[SURVEY][MASTER] summary A%u duplicate ACK failed err=%d",
                 summary.sender_id, (int)ack_err);
        }
      } else {
        RLOG_W(LOG_OBJECT_CODE_ANCHOR,
               "[CALIB][SUMMARY] Ignored stale/invalid summary sender=A%u epoch=%u current=%u",
               summary.sender_id, summary.epoch_id, s_summary_epoch_id);
      }
    }
  }
}

static bool calib_summary_item_usable(const sys_calib_pair_summary_item_t *item) {
  uint8_t peer_slot = 0U;
  return item &&
         calib_anchor_id_to_slot(item->peer_id, &peer_slot) &&
         item->valid_count >= CALIB_ANCHOR_SAMPLES &&
         item->std_m <= CALIB_ANCHOR_MAX_STD_M;
}

extern void app_rtos_request_sensor_fusion_reset(void);

static void run_anchor_survey_solver(sys_config_t *cfg) {
  const uint8_t expected_mask = calib_all_anchor_mask();
  uint8_t active_ids[NUM_ANCHORS] = {0U};

  if (!cfg || calib_anchor_count() != NUM_ANCHORS) {
    calib_finish_v1(SYS_CALIB_STATUS_NORMAL);
    return;
  }

  for (uint8_t slot = 0U; slot < NUM_ANCHORS; slot++) {
    if (!calib_slot_to_anchor_id(slot, &active_ids[slot])) {
      calib_finish_v1(SYS_CALIB_STATUS_NORMAL);
      return;
    }
  }

  if ((s_summary_ready_mask & expected_mask) != expected_mask) {
    RLOG_W(LOG_OBJECT_CODE_ANCHOR,
           "[SURVEY] decision FAIL missing summaries mask=0x%02X expected=0x%02X",
           s_summary_ready_mask, expected_mask);
    calib_finish_v1(SYS_CALIB_STATUS_NORMAL);
    return;
  }

  // 1. Map measured distances
  float d_meas[NUM_ANCHORS][NUM_ANCHORS] = {0};
  for (uint8_t sender_slot = 0U; sender_slot < NUM_ANCHORS; sender_slot++) {
    uint8_t sender_id = active_ids[sender_slot];
    const sys_calib_pair_summary_msg_t *summary = &s_summary_by_anchor[sender_id - 1U];
    for (uint8_t p = 0U; p < summary->pair_count; p++) {
      const sys_calib_pair_summary_item_t *item = &summary->pair[p];
      if (calib_summary_item_usable(item)) {
        uint8_t peer_slot = 0U;
        if (calib_anchor_id_to_slot(item->peer_id, &peer_slot)) {
          d_meas[sender_slot][peer_slot] = item->mean_m;
        }
      }
    }
  }

  // 2. Verify bidirectional measurements and apply reciprocal filter
  float d[NUM_ANCHORS][NUM_ANCHORS] = {0};
  for (uint8_t i = 0; i < NUM_ANCHORS; i++) {
    for (uint8_t j = i + 1; j < NUM_ANCHORS; j++) {
      float d_ij = d_meas[i][j];
      float d_ji = d_meas[j][i];
      if (d_ij <= 0.0f && d_ji <= 0.0f) {
        RLOG_E(LOG_OBJECT_CODE_ANCHOR, ERR_SYSTEM,
               "[SURVEY] Missing both directions A%u-A%u",
               active_ids[i], active_ids[j]);
        calib_finish_v1(SYS_CALIB_STATUS_NORMAL);
        return;
      }

      float avg;
      if (d_ij <= 0.0f || d_ji <= 0.0f) {
        avg = (d_ij > 0.0f) ? d_ij : d_ji;
        RLOG_W(LOG_OBJECT_CODE_ANCHOR,
               "[SURVEY] Link A%u-A%u single direction: forward=%.3fm reverse=%.3fm using=%.3fm",
               active_ids[i], active_ids[j], d_ij, d_ji, avg);
      } else {
        float diff = fabsf(d_ij - d_ji);
        if (diff > 0.5f) {
          RLOG_W(LOG_OBJECT_CODE_ANCHOR,
                 "[SURVEY] Reciprocal mismatch A%u-A%u: diff=%.3fm; using average",
                 active_ids[i], active_ids[j], diff);
        }
        avg = (d_ij + d_ji) * 0.5f;
      }
      d[i][j] = avg;
      d[j][i] = avg;
      RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[SURVEY] Link A%u-A%u: forward=%.3fm, reverse=%.3fm, avg=%.3fm",
             active_ids[i], active_ids[j], d_ij, d_ji, avg);
    }
  }

  // 3. Load heights (Z coordinates) of anchors from the active zone profile
  uint32_t active_zone = sys_config_get_active_zone_id();
  if (active_zone < 1 || active_zone > 4) {
      active_zone = 1;
  }
  protobuf_zone_profile_t *active_profile = &cfg->zone_profiles[active_zone - 1];

  float z[NUM_ANCHORS];
  for (uint8_t i = 0; i < NUM_ANCHORS; i++) {
      z[i] = cfg->anchor_layout[i].z_m;
  }

  // 4. Convert to horizontal planar 2D distances
  float r[NUM_ANCHORS][NUM_ANCHORS] = {0};
  for (uint8_t i = 0; i < NUM_ANCHORS; i++) {
    for (uint8_t j = i + 1; j < NUM_ANCHORS; j++) {
      float dz = z[i] - z[j];
      float dz2 = dz * dz;
      float d2 = d[i][j] * d[i][j];
      if (d2 <= dz2) {
        RLOG_E(LOG_OBJECT_CODE_ANCHOR, ERR_SYSTEM,
               "[SURVEY] Measured distance is not physically valid A%u-A%u: d=%.3f dz=%.3f",
               active_ids[i], active_ids[j], d[i][j], dz);
        calib_finish_v1(SYS_CALIB_STATUS_NORMAL);
        return;
      }
      r[i][j] = sqrtf(d2 - dz2);
      r[j][i] = r[i][j];
      RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[SURVEY] Planar distance r_A%u-A%u = %.3fm",
             active_ids[i], active_ids[j], r[i][j]);
    }
  }

  // 5. Solve coordinates
  float x2 = r[0][1];
  if (x2 < 0.01f) {
      RLOG_E(LOG_OBJECT_CODE_ANCHOR, ERR_SYSTEM, "[SURVEY] Planar distance r12 too small: %.3fm", x2);
      calib_finish_v1(SYS_CALIB_STATUS_NORMAL);
      return;
  }

  float r13_2 = r[0][2] * r[0][2];
  float r23_2 = r[1][2] * r[1][2];
  float x3 = (r13_2 - r23_2 + x2 * x2) / (2.0f * x2);
  float y3_val = r13_2 - x3 * x3;
  if (y3_val < 0.0f) {
      RLOG_E(LOG_OBJECT_CODE_ANCHOR, ERR_SYSTEM, "[SURVEY] Impossible triangle A%u-A%u-A%u: r13=%.3f, r23=%.3f, x3=%.3f",
             active_ids[0], active_ids[1], active_ids[2], r[0][2], r[1][2], x3);
      calib_finish_v1(SYS_CALIB_STATUS_NORMAL);
      return;
  }
  float y3 = sqrtf(y3_val);
  if (y3 < 0.01f) {
      RLOG_E(LOG_OBJECT_CODE_ANCHOR, ERR_SYSTEM, "[SURVEY] Collinear layout A%u-A%u-A%u: y3=%.3fm too small",
             active_ids[0], active_ids[1], active_ids[2], y3);
      calib_finish_v1(SYS_CALIB_STATUS_NORMAL);
      return;
  }

  float r14_2 = r[0][3] * r[0][3];
  float r24_2 = r[1][3] * r[1][3];
  float r34_2 = r[2][3] * r[2][3];
  float x4 = (r14_2 - r24_2 + x2 * x2) / (2.0f * x2);
  float y4 = (r14_2 + r13_2 - r34_2 - 2.0f * x4 * x3) / (2.0f * y3);

  const float solved_x[NUM_ANCHORS] = {0.0f, x2, x3, x4};
  const float solved_y[NUM_ANCHORS] = {0.0f, 0.0f, y3, y4};
  float residual_sq_sum = 0.0f;
  float residual_max = 0.0f;
  uint8_t residual_count = 0U;
  for (uint8_t i = 0U; i < NUM_ANCHORS; i++) {
    for (uint8_t j = (uint8_t)(i + 1U); j < NUM_ANCHORS; j++) {
      float dx = solved_x[i] - solved_x[j];
      float dy = solved_y[i] - solved_y[j];
      float dz = z[i] - z[j];
      float predicted = sqrtf(dx * dx + dy * dy + dz * dz);
      float residual = fabsf(predicted - d[i][j]);
      residual_sq_sum += residual * residual;
      if (residual > residual_max) {
        residual_max = residual;
      }
      residual_count++;
    }
  }
  float residual_rms = sqrtf(residual_sq_sum / (float)residual_count);
  if (!isfinite(x4) || !isfinite(y4) || residual_max > 0.5f) {
    RLOG_E(LOG_OBJECT_CODE_ANCHOR, ERR_SYSTEM,
           "[SURVEY] solution rejected: rms=%.3fm max=%.3fm limit=%.3fm",
           residual_rms,
           residual_max,
           0.5f);
    calib_finish_v1(SYS_CALIB_STATUS_NORMAL);
    return;
  }

  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[SURVEY] Solved relative coordinates:");
  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[SURVEY] A%u: (0.000, 0.000, %.3f)", active_ids[0], z[0]);
  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[SURVEY] A%u: (%.3f, 0.000, %.3f)", active_ids[1], x2, z[1]);
  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[SURVEY] A%u: (%.3f, %.3f, %.3f)", active_ids[2], x3, y3, z[2]);
  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[SURVEY] A%u: (%.3f, %.3f, %.3f)", active_ids[3], x4, y4, z[3]);
  RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[SURVEY] residual rms=%.3fm max=%.3fm",
         residual_rms, residual_max);

  // 6. Save solved layout to the active zone profile
  active_profile->anchor_count = NUM_ANCHORS;
  active_profile->anchors_count = NUM_ANCHORS;
  for (uint8_t i = 0; i < NUM_ANCHORS; i++) {
      active_profile->anchors[i].anchor_id = active_ids[i];
      active_profile->anchors[i].z_m = z[i];
  }
  active_profile->anchors[0].x_m = 0.0f;
  active_profile->anchors[0].y_m = 0.0f;

  active_profile->anchors[1].x_m = x2;
  active_profile->anchors[1].y_m = 0.0f;

  active_profile->anchors[2].x_m = x3;
  active_profile->anchors[2].y_m = y3;

  active_profile->anchors[3].x_m = x4;
  active_profile->anchors[3].y_m = y4;

  // Sync to active live layout in RAM:
  cfg->anchor_count = NUM_ANCHORS;
  for (uint8_t i = 0; i < NUM_ANCHORS; i++) {
      cfg->anchor_layout[i] = active_profile->anchors[i];
  }

  // Reset Sensor Fusion to clear UKF states:
  app_rtos_request_sensor_fusion_reset();

  RLOG_I(LOG_OBJECT_CODE_ANCHOR,
         "[SURVEY] accepted Zone %lu layout; persisting once",
         (unsigned long)active_zone);
  if (sys_config_save() != 0) {
    RLOG_E(LOG_OBJECT_CODE_ANCHOR, ERR_SYSTEM,
           "[SURVEY] failed to persist Zone %lu layout",
           (unsigned long)active_zone);
    calib_finish_v1(SYS_CALIB_STATUS_NORMAL);
    return;
  }
  calib_finish_v1(SYS_CALIB_STATUS_DONE);
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
  uint8_t collector_id = calib_collector_id();
  if (!calib_build_pair_summary(cfg->uwb.device_id, &summary)) {
      RLOG_W(LOG_OBJECT_CODE_ANCHOR, "[CALIB][SUMMARY] Failed to build local summary");
      calib_finish_v1(SYS_CALIB_STATUS_NORMAL);
      return;
  }

  if (cfg->uwb.device_id == collector_id) {
      calib_store_pair_summary(&summary);
      calib_collect_remote_summaries();
      const uint8_t expected_mask = calib_all_anchor_mask();
      uint8_t missing_mask =
          (uint8_t)(expected_mask & (uint8_t)(~s_summary_ready_mask));
      if (missing_mask != 0U) {
        if (missing_mask != s_last_missing_summary_mask) {
          s_last_missing_summary_mask = missing_mask;
          RLOG_W(LOG_OBJECT_CODE_ANCHOR,
                 "[SURVEY][MASTER] waiting summaries missing=0x%02X received=0x%02X/%02X",
                 missing_mask, s_summary_ready_mask, expected_mask);
        }
        return;
      }
      if (s_last_missing_summary_mask != 0U) {
        s_last_missing_summary_mask = 0U;
        RLOG_I(LOG_OBJECT_CODE_ANCHOR,
               "[SURVEY][MASTER] all summaries received mask=0x%02X",
               s_summary_ready_mask);
      }
      run_anchor_survey_solver(cfg);
  } else {
      uint8_t slot_id = calib_control_slot_for_anchor(cfg->uwb.device_id);
      sys_ranging_err_t err =
          sys_ranging_control_send_wait_ack(SYS_UWB_CTRL_CALIB_PAIR_SUMMARY,
                                            &summary, sizeof(summary),
                                            slot_id,
                                            collector_id,
                                            summary.sender_id,
                                            70U);
      RLOG_I(LOG_OBJECT_CODE_ANCHOR,
             "[SURVEY][SUMMARY] A%u -> A%u result=%s pairs=%u slot=%u",
             summary.sender_id, collector_id,
             (err == SYS_RANGING_OK) ? "ACKED" : "NO_ACK",
             summary.pair_count, slot_id);
      if (err == SYS_RANGING_OK) {
        /*
         * Sending a summary is not proof that the collector received it. Keep this node
         * participating in ranging until the coordinator completes survey.
         */
        sys_ranging_set_calib_status(SYS_CALIB_STATUS_DONE);
        RLOG_I(LOG_OBJECT_CODE_ANCHOR,
               "[SURVEY][STATE] A%u summary ACKED; remaining active until master completes",
               summary.sender_id);
      } else {
        RLOG_W(LOG_OBJECT_CODE_ANCHOR,
               "[SURVEY][SUMMARY] A%u not ACKED err=%d; remaining active and retrying next cycle",
               summary.sender_id, (int)err);
        return;
      }
  }
  s_summary_done = true;
}

static void calib_next_turn(void) {
  /* Abort current ranging to clear hardware and state machine for next turn */
  sys_ranging_abort();

  uint8_t previous_turn = s_current_turn;
  uint8_t anchor_count = calib_anchor_count();
  uint8_t collector_id = calib_collector_id();
  if (anchor_count == 0U) {
    calib_finish_v1(SYS_CALIB_STATUS_NORMAL);
    return;
  }
  s_current_turn = calib_next_anchor_id(s_current_turn);
  if (s_current_turn == 0U) {
    calib_finish_v1(SYS_CALIB_STATUS_NORMAL);
    return;
  }
  uint8_t my_id = sys_config_get()->uwb.device_id;

  /*
   * A peer summary is retried at most once per complete survey cycle. This is
   * bounded recovery for a lost summary, not a continuous transmit loop.
   */
  if (previous_turn == collector_id && my_id != collector_id) {
    s_summary_done = false;
  }
  RLOG_I(LOG_OBJECT_CODE_ANCHOR,
         "[SURVEY][TURN] A%u -> A%u self=A%u next_role=%s ready=0x%02X/%02X",
         previous_turn, s_current_turn, my_id,
         (my_id == s_current_turn) ? "INITIATOR" : "RESPONDER",
         s_peer_ready_mask, s_peer_expected_mask);
  
  if (s_current_turn == collector_id) {
      calib_calculate_and_adjust();
  }

  s_turn_start_ms = HAL_GetTick();
  s_last_act_ms = HAL_GetTick();
  s_heard_poll = false;
  s_turn_ranged_once = false;
  s_ranging_active = false;
  s_anchor_resp_active = false;
}

static void calib_process_round(uint32_t rx_timeout_ms) {
  uint8_t my_id = sys_config_get()->uwb.device_id;
  bool is_my_turn = (my_id == s_current_turn);
  
  /* 1. TURN WATCHDOG */
  uint32_t now = HAL_GetTick();
  calib_log_status(now, my_id);
  if (s_app_state == ANCHOR_STATE_CALIB_DONE) {
      return;
  }
  if (is_my_turn) {
      /* Add ID-based jitter to Initiator timeout to break permanent collisions */
      uint32_t initiator_timeout = CALIB_TURN_TIMEOUT_MS + (my_id * 100U);
      if ((now - s_turn_start_ms > initiator_timeout) || 
          (s_turn_ranged_once &&
           ((s_peer_ready_mask & s_peer_expected_mask) == s_peer_expected_mask))) {
          RLOG_I(LOG_OBJECT_CODE_ANCHOR, "[CALIB] My turn %u finished. Next...", s_current_turn);
          calib_next_turn();
          return;
      }
  } else {
      /* RESPONDER MODE: 
       * 1. If we haven't started yet, only wait forever if we are in the MASTER turn.
       * 2. If we've started or we are in a non-master turn, timeout after 4s to recover from skipped turns.
       */
      bool can_timeout = s_system_started || s_heard_poll || (s_current_turn != calib_collector_id());
      if (can_timeout && (now - s_last_act_ms > CALIB_TURN_TIMEOUT_MS)) {
          RLOG_W(LOG_OBJECT_CODE_ANCHOR, "[CALIB] Turn %u Silence -> Next %u (gap=%ums tick=%u)", 
                 s_current_turn,
                 calib_next_anchor_id(s_current_turn),
                 (uint32_t)(now - s_last_act_ms), (uint32_t)now);
          calib_next_turn();
          return;
      }
  }

  /* 2. RANGING EXECUTION */
  uint8_t all_ids[MAX_ANCHORS_SUPPORTED];
  uint8_t n_all = anchor_fill_ids(all_ids);
  if (n_all == 0U) {
    calib_finish_v1(SYS_CALIB_STATUS_NORMAL);
    return;
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
      s_turn_ranged_once = true;
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
                uint8_t peer_bit = (uint8_t)(1U << idx);
                if ((s_peer_done_mask & peer_bit) == 0U) {
                  s_peer_done_mask |= peer_bit;
                  RLOG_I(LOG_OBJECT_CODE_ANCHOR,
                         "[SURVEY][PEER_DONE] self=A%u peer=A%u done_mask=0x%02X/%02X",
                         my_id, res->anchor_id, s_peer_done_mask, s_peer_expected_mask);
                }
              }

              if (!(s_peer_ready_mask & (1 << idx))) {
                float known = calib_get_ref_distance_3d(my_id, res->anchor_id);
                if (known <= 0.0f) {
                  known = 0.0f;
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
                    calib_pair_store_ready(idx, res->anchor_id, known, m, s);
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

static const char *calib_state_name(void) {
  switch (s_app_state) {
    case ANCHOR_STATE_CALIB_COLLECTING: return "COLLECTING";
    case ANCHOR_STATE_CALIB_CALCULATE:  return "CALCULATE";
    case ANCHOR_STATE_CALIB_DONE:       return "DONE";
    case ANCHOR_STATE_NORMAL:           return "NORMAL";
    default:                            return "IDLE";
  }
}

static void calib_log_status(uint32_t now, uint8_t my_id) {
  if (my_id == calib_collector_id() ||
      (now - s_status_log_ms) < 2000U) {
    return;
  }
  s_status_log_ms = now;

  const char *role = (s_app_state == ANCHOR_STATE_CALIB_DONE)
                       ? "DONE"
                       : ((my_id == s_current_turn) ? "INITIATOR" : "RESPONDER");
  uint16_t sample_counts[NUM_ANCHORS] = {0U};
  for (uint8_t slot = 0U; slot < NUM_ANCHORS; slot++) {
    uint8_t anchor_id = 0U;
    if (calib_slot_to_anchor_id(slot, &anchor_id) &&
        anchor_id > 0U &&
        anchor_id <= MAX_ANCHORS_SUPPORTED) {
      sample_counts[slot] = s_peer_calib[anchor_id - 1U].count;
    }
  }
  RLOG_I(LOG_OBJECT_CODE_ANCHOR,
         "[SURVEY][STATUS] self=A%u state=%s role=%s turn=A%u ready=0x%02X/%02X samples=[%u,%u,%u,%u]",
         my_id, calib_state_name(), role, s_current_turn,
         s_peer_ready_mask, s_peer_expected_mask,
         sample_counts[0], sample_counts[1],
         sample_counts[2], sample_counts[3]);
}

app_err_t app_anchor_init(void) {
  sys_config_t *cfg = sys_config_get();
  RLOG_I(LOG_OBJECT_CODE_APPLICATION, "===== ANCHOR #%u =====", cfg->uwb.device_id);
  sys_ranging_set_calib_status(SYS_CALIB_STATUS_NORMAL);

  if (s_survey_active) {
    uint8_t anchor_count = calib_anchor_count();
    if (anchor_count != NUM_ANCHORS) {
      RLOG_E(LOG_OBJECT_CODE_ANCHOR, ERR_SYSTEM,
             "[SURVEY] Unsupported anchor_count=%lu; solver requires %u",
             (unsigned long)cfg->anchor_count,
             NUM_ANCHORS);
      app_anchor_set_survey_active(false);
      s_app_state = ANCHOR_STATE_NORMAL;
      return APP_ERR;
    }

    s_app_state = ANCHOR_STATE_CALIB_COLLECTING;
    if (cfg->uwb.device_id == 0U || cfg->uwb.device_id > MAX_ANCHORS_SUPPORTED) {
      app_anchor_set_survey_active(false);
      s_app_state = ANCHOR_STATE_NORMAL;
      return APP_ERR;
    }
    uint8_t all_anchor_mask = calib_all_anchor_mask();
    uint8_t self_mask = (uint8_t)(1U << (cfg->uwb.device_id - 1U));
    if ((all_anchor_mask & self_mask) == 0U) {
      RLOG_E(LOG_OBJECT_CODE_ANCHOR, ERR_SYSTEM,
             "[SURVEY] Anchor A%u is missing from active layout",
             cfg->uwb.device_id);
      app_anchor_set_survey_active(false);
      s_app_state = ANCHOR_STATE_NORMAL;
      return APP_ERR;
    }
    s_peer_expected_mask = (uint8_t)(all_anchor_mask & (uint8_t)(~self_mask));
    
    calib_reset();
    RLOG_I(LOG_OBJECT_CODE_ANCHOR,
           "[SURVEY][STATE] A%u -> COLLECTING epoch=%u expected=0x%02X first_turn=A%u role=%s",
           cfg->uwb.device_id, s_summary_epoch_id, s_peer_expected_mask, s_current_turn,
           (cfg->uwb.device_id == s_current_turn) ? "INITIATOR" : "RESPONDER");
  } else {
    s_app_state = ANCHOR_STATE_NORMAL;
  }
  return APP_OK;
}

void app_anchor_process(void *arg) {
  (void)arg;
  sys_config_t *cfg = sys_config_get();
  uint32_t rx_timeout_ms = cfg->uwb.rx_timeout_ms;

  if (s_app_state == ANCHOR_STATE_CALIB_COLLECTING || s_app_state == ANCHOR_STATE_CALIB_DONE) {
    uint8_t my_id = cfg->uwb.device_id;
    uint8_t collector_id = calib_collector_id();
    uint32_t now = HAL_GetTick();

    if (s_app_state == ANCHOR_STATE_CALIB_COLLECTING) {
      if (my_id != collector_id &&
          s_summary_done && s_current_turn == collector_id) {
        calib_peer_poll_finish(my_id);
      }
      if (s_app_state == ANCHOR_STATE_CALIB_COLLECTING) {
        calib_process_round(rx_timeout_ms);
      }
    }

    if (s_app_state == ANCHOR_STATE_CALIB_DONE) {
      now = HAL_GetTick();
      if (my_id == collector_id) {
        calib_master_process_finish();
      } else {
        /* ACK duplicate FINISH packets in case the first ACK was lost. */
        calib_peer_poll_finish(my_id);
        if ((now - s_done_start_ms) > SURVEY_PEER_FINISH_GRACE_MS) {
          calib_return_to_normal("FINISH_RECEIVED");
        }
      }
    }
    return;
  }

  if (s_app_state == ANCHOR_STATE_NORMAL) {
    uint8_t all_ids[MAX_ANCHORS_SUPPORTED];
    uint8_t n_all = anchor_fill_ids(all_ids);
    if (n_all == 0U) {
      return;
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

void app_anchor_set_survey_active(bool active) {
  s_survey_active = active;
}

bool app_anchor_is_survey_active(void) {
  return s_survey_active;
}
