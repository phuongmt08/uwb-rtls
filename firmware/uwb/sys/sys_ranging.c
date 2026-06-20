/* ============================== sys_ranging.c ==============================
 * @file       sys_ranging.c
 * @author
 * @brief      DS-TWR + TDMA Multi-Anchor Ranging
 * @version    4.1.0
 * @date       2026-02-01
 */

/* Includes ----------------------------------------------------------- */
#include "sys_ranging.h"

#include "bsp_util.h"
#include "bsp_uwb.h"
#include "mw_tdma_scheduler.h"
#include "sys_config.h"
#include "sys_logger.h"

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

/* Constants ---------------------------------------------------------- */
#define DWT_TIME_UNITS        (1.0 / 499.2e6 / 128.0)
#define SPEED_OF_LIGHT        299702547.0
#define DSTWR_MAX_INTERVAL_US 50000U

/* DS-TWR message types */
#define MW_DSTWR_MSG_TYPE_POLL   0xE1
#define MW_DSTWR_MSG_TYPE_RESP   0xE2
#define MW_DSTWR_MSG_TYPE_FINAL  0xE3
#define MW_DSTWR_MSG_TYPE_RESULT 0xE4 /* Anchor sends distance to TAG */

#define CONTROL_MSG_SLOT_MS  100U
#define CONTROL_MSG_MAX_SIZE 127U

#define ANCHOR_SMART_DISCOVERY_ON_MS       70U
#define ANCHOR_SMART_DISCOVERY_BALANCED_MS 120U
#define ANCHOR_SMART_DISCOVERY_ECO_MS      220U
#define ANCHOR_SMART_DISCOVERY_DEEP_ECO_MS 420U
#define ANCHOR_SMART_TRACK_PRE_POLL_MS     20U
#define ANCHOR_SMART_TRACK_LATE_MARGIN_MS  25U
#define ANCHOR_SMART_TRACK_MISS_PRE_STEP_MS 5U
#define ANCHOR_SMART_TRACK_MISS_LATE_STEP_MS 8U
#define ANCHOR_SMART_TRACK_MAX_PRE_POLL_MS 45U
#define ANCHOR_SMART_TRACK_MAX_MISSES      5U
#define ANCHOR_SMART_TRACK_MIN_WINDOW_MS   40U
#define ANCHOR_SMART_TRACK_MAX_WINDOW_MS   90U
#define ANCHOR_SMART_LEVEL_STABLE_SUCCESSES 3U
#define ANCHOR_SMART_DISCOVERY_DECAY_MISSES 3U
#define ANCHOR_SMART_TRACK_REARM_GAP_MS    10U

#define TAG_MIN_ANCHOR_SAMPLES             3U
/* Temporary diagnostic mode: complete the ranging cycle even when fewer than
 * TAG_MIN_ANCHOR_SAMPLES anchors respond. Keep the warnings so the degraded
 * cycles remain visible in logs. Set to 1U to restore the original aborts. */
#define TAG_ABORT_ON_INSUFFICIENT_SAMPLES  0U

/* Software margin needed before programming DW1000 delayed TX.
 * Keep this separate from TDMA slot guard: slot guard protects adjacent slots,
 * while this only decides whether it is still worth attempting delayed TX. */
#define RANGING_TX_SCHEDULE_GUARD_US 600U

#define RX_WAIT_IMMEDIATE          false
#define RX_WAIT_DELAYED            true
#define RX_WAIT_NO_DELAYED_TS_DW   0ULL

/* Macro definitions -------------------------------------------------- */
// SYS_RANGING_DEBUG: Enable  detailed debug logs for ranging state machine and calculations
#define SYS_RANGING_DEBUG     0

#if SYS_RANGING_DEBUG
#define RANGING_LOG_D(...) RLOG_D(__VA_ARGS__)
#else
#define RANGING_LOG_D(...) \
  do                       \
  {                        \
  } while (0)
#endif

/* Set to 0 to force immediate RX instead of delayed RX for debugging */
#define SYS_RANGING_USE_RX_DELAYED 0

/* Set to 1 when diagnosing delayed-TX slot jitter. Keep 0 in production to
 * avoid extra SPI reads and 64-bit math in the TDMA critical path. */
#define SYS_RANGING_VERIFY_TX_TIMING 0

#if SYS_RANGING_USE_RX_DELAYED
#define RANGING_ENABLE_RX_DELAYED(ts, timeout) bsp_uwb_enable_rx_delayed(ts, timeout)
#else
#define RANGING_ENABLE_RX_DELAYED(ts, timeout) ((void)(ts), bsp_uwb_enable_rx(timeout))
#endif

/* 40-bit DW1000 timestamp mask and printf helpers.
 * DW_FMT  : format specifier for a 40-bit DW timestamp (use as string literal).
 * DW_ARG  : expands to the two (unsigned long) printf arguments for DW_FMT. */
#define DW_MASK_40               0x000000FFFFFFFFFFULL
#define DW_FMT                   "0x%08lX%08lX"
#define DW_ARG(x)                (unsigned long) ((x) >> 32), (unsigned long) ((x) & 0xFFFFFFFFUL)

/* Private types ------------------------------------------------------ */
typedef enum
{
  STATE_IDLE = 0,
  STATE_TAG_RANGING_TDMA,
  STATE_TAG_COMPLETE,
  STATE_ANCHOR_RANGING_TDMA,
  STATE_ANCHOR_COMPLETE,
  STATE_ERROR
} ranging_state_t;

typedef struct __attribute__((packed))
{
  uint8_t msg_type;
  uint8_t sequence_num;
  uint8_t tag_id;
  uint8_t num_anchors;
  uint8_t anchor_mask;
  uint8_t padding[8];
} poll_msg_t;

typedef struct __attribute__((packed))
{
  uint8_t  msg_type;
  uint8_t  sequence_num;
  uint8_t  anchor_id;
  uint8_t  slot_id;
  uint64_t poll_rx_ts;
  uint64_t resp_tx_ts;
  uint8_t  calib_status;
  uint8_t  padding[3];
} resp_msg_t;

typedef struct __attribute__((packed))
{
  uint8_t  msg_type;
  uint8_t  sequence_num;
  uint8_t  tag_id;
  uint8_t  num_responses;
  uint64_t poll_tx_ts;
  uint8_t  anchor_resp_mask;
  uint8_t  padding[3];
} final_msg_t;

typedef struct __attribute__((packed))
{
  uint8_t  anchor_id;
  uint64_t resp_rx_ts;
  uint64_t final_tx_ts;
} final_anchor_data_t;

typedef struct __attribute__((packed))
{
  uint8_t msg_type;
  uint8_t sequence_num;
  uint8_t anchor_id;
  uint8_t slot_id;    /* TDMA slot ID - TAG uses this to detect slot mismatches */
  uint8_t valid;      /* 1 = valid distance, 0 = error */
  float   distance_m; /* Calculated distance */
  uint16_t fp_amp_norm_q8;
  uint16_t fp_snr_q8;
} result_msg_t;

typedef struct
{
  sys_uwb_control_msg_type_t type;
  uint16_t                   size;
} control_msg_desc_t;

typedef char calib_pair_summary_fits_control_frame_t[
  (sizeof(sys_calib_pair_summary_msg_t) <= CONTROL_MSG_MAX_SIZE) ? 1 : -1
];

typedef struct
{
  ranging_state_t state;
  uint32_t        state_entry_tick;
  uint8_t         sequence_num;

  /* Results */
  sys_ranging_multi_result_t result_multi;
  sys_ranging_result_t       result_single;
  bool                       has_result;

  /* State for anchor */
  uint8_t anchor_id;

} ranging_ctx_t;

/* DS-TWR timestamp structure */
typedef struct
{
  uint64_t t1, t2, t3, t4, t5, t6;
} dstwr_timestamps_t;

typedef enum {
  ANCHOR_RX_DISCOVERY = 0,
  ANCHOR_RX_TRACKING,
  ANCHOR_RX_PERFORMANCE
} anchor_rx_mode_t;

typedef struct {
  anchor_rx_mode_t mode;
  uint32_t next_window_tick;
  uint32_t next_poll_tick;
  uint64_t next_poll_dw;
  uint32_t active_power_mode;
  uint8_t  track_misses;
  uint8_t  discovery_misses;
  uint8_t  stable_successes;
  bool     initialized;
} anchor_smart_rx_state_t;

typedef struct {
  bool     enabled;
  uint64_t rx_start_dw;
} anchor_poll_rx_plan_t;

typedef enum {
    SYS_RANGING_EV_SYS_IDLE = 0,
    
    SYS_RANGING_EV_TAG_TX_POLL,
    SYS_RANGING_EV_TAG_WAIT_POLL_TX,
    SYS_RANGING_EV_TAG_WAIT_RESP,
    SYS_RANGING_EV_TAG_WAIT_FINAL_TX,
    SYS_RANGING_EV_TAG_WAIT_RESULT,

    SYS_RANGING_EV_ANCHOR_WAIT_POLL,
    SYS_RANGING_EV_ANCHOR_WAIT_RESP_TX,
    SYS_RANGING_EV_ANCHOR_WAIT_FINAL,
    SYS_RANGING_EV_ANCHOR_WAIT_RESULT_TX
} sys_ranging_event_step_t;

typedef struct {
    sys_ranging_event_step_t step;
    uint32_t                 step_start_tick;
    uint64_t                 deadline_dw;
    
    uint64_t                 poll_tx_ts;     // T1
    uint64_t                 poll_rx_ts;     // T2
    uint64_t                 resp_tx_ts;     // T3
    uint64_t                 expected_final_dw;
    uint64_t                 planned_tx_dw;
    uint64_t                 predicted_tx_dw;
    
    uint8_t                  num_responses;
    uint8_t                  my_slot_id;
    bsp_uwb_rx_quality_t     poll_quality;
    bool                     pending_final_valid;
    bsp_uwb_event_t          pending_final_evt;
    
    struct {
        uint8_t  anchor_id;
        uint64_t resp_rx_ts;
        uint64_t poll_rx_ts;
        uint64_t resp_tx_ts;
        uint8_t  calib_status;
        bool     valid;
    } anchor_resp[8];
} sys_ranging_event_ctx_t;

/* Private variables -------------------------------------------------- */
static ranging_ctx_t    s_ctx         = { 0 };
static sys_ranging_event_ctx_t s_sys_ranging_ev = {0};
static struct
{
  uint32_t last_tick;
  uint32_t poll_rx;
  uint32_t resp_tx_done;
  uint32_t final_rx_early;
  uint32_t final_rx;
  uint32_t final_poll_fallback;
  uint32_t final_for_me;
  uint32_t final_not_for_me;
  uint32_t final_timeout;
  uint32_t result_slot_missed;
  uint32_t result_tx_fail;
  uint32_t result_tx_done;
} s_anchor_diag = {0};

static struct
{
  uint32_t last_tick;
  uint32_t poll_tx;
  uint32_t poll_tx_done;
  uint32_t resp_full;
  uint32_t resp_partial;
  uint32_t resp_none;
  uint32_t resp_packets;
  uint32_t resp_expected_packets;
  uint32_t resp_poll_fallback;
  uint32_t resp_all_configured;
  uint32_t resp_wait_spins;
  uint32_t resp_rx_errors;
  uint32_t resp_rejects;
  uint32_t final_tx_done;
  uint32_t final_tx_fail;
  uint32_t final_slot_missed;
  uint32_t result_full;
  uint32_t result_partial;
  uint32_t result_packets;
  uint32_t result_expected_packets;
  uint32_t result_poll_fallback;
  uint32_t result_all_configured;
  uint32_t result_wait_spins;
  uint32_t result_rx_errors;
  uint32_t result_rejects;
} s_tag_diag = {0};
static tdma_scheduler_t s_tdma_tag    = { 0 };
static tdma_scheduler_t s_tdma_anchor = { 0 };
static sys_calib_status_t s_calib_status = SYS_CALIB_STATUS_NORMAL;
static struct
{
  uint32_t total_count;
  uint32_t success_count;
  uint32_t error_count;
} s_stats = { 0 };
static anchor_smart_rx_state_t s_anchor_smart_rx = {0};
static anchor_poll_rx_plan_t   s_anchor_poll_rx_plan = {0};

static const control_msg_desc_t s_control_msg_descs[] = {
  { SYS_UWB_CTRL_CALIB_PAIR_SUMMARY, sizeof(sys_calib_pair_summary_msg_t) },
  { SYS_UWB_CTRL_ACK,                sizeof(sys_uwb_control_ack_msg_t) },
  { SYS_UWB_CTRL_SURVEY_FINISH,      sizeof(sys_survey_finish_msg_t) },
};

/* Static guard */
static bool s_ranging_busy = false;

/* Private functions --------------------------------------------------- */

static uint32_t anchor_smart_clamp_power_mode(uint32_t power_mode)
{
  if (power_mode > ANCHOR_POWER_MODE_DEEP_ECO)
  {
    return ANCHOR_POWER_MODE_BALANCED;
  }
  return power_mode;
}

static const char *anchor_smart_power_mode_name(uint32_t power_mode)
{
  switch (anchor_smart_clamp_power_mode(power_mode))
  {
  case ANCHOR_POWER_MODE_PERFORMANCE: return "PERFORMANCE";
  case ANCHOR_POWER_MODE_BALANCED:    return "BALANCED";
  case ANCHOR_POWER_MODE_ECO:         return "ECO";
  case ANCHOR_POWER_MODE_DEEP_ECO:    return "DEEP_ECO";
  default:                            return "BALANCED";
  }
}

static uint32_t anchor_smart_active_power_mode(uint32_t configured_mode)
{
  uint32_t target = anchor_smart_clamp_power_mode(configured_mode);
  uint32_t active = anchor_smart_clamp_power_mode(s_anchor_smart_rx.active_power_mode);

  if (!s_anchor_smart_rx.initialized)
  {
    return target;
  }

  if (active > target)
  {
    active = target;
  }

  return active;
}

static void anchor_smart_set_active_level(uint32_t power_mode, bool log_transition)
{
  uint32_t level = anchor_smart_clamp_power_mode(power_mode);

  if (s_anchor_smart_rx.active_power_mode != level)
  {
    if (log_transition)
    {
      RLOG_I(LOG_OBJECT_CODE_RANGING, "[ANCHOR] RX level -> %s",
             anchor_smart_power_mode_name(level));
    }
    s_anchor_smart_rx.active_power_mode = level;
  }
}

static inline bool dstwr_forward_interval_40(uint64_t later, uint64_t earlier, uint64_t *out_delta)
{
  const uint64_t MAX_INTERVAL_DW = tdma_us_to_dw(DSTWR_MAX_INTERVAL_US);
  uint64_t delta = (later - earlier) & DW_MASK_40;
  if (delta == 0ULL || delta > MAX_INTERVAL_DW)
  {
    RLOG_W(LOG_OBJECT_CODE_RANGING, "Invalid DW interval: delta=%lu", (unsigned long) delta);
    return false;
  }
  *out_delta = delta;
  return true;
}

static void log_dstwr_debug(uint8_t seq, uint8_t anchor_id, const dstwr_timestamps_t *ts)
{
#if SYS_RANGING_DEBUG
  uint64_t t1 = ts->t1 & DW_MASK_40;
  uint64_t t2 = ts->t2 & DW_MASK_40;
  uint64_t t3 = ts->t3 & DW_MASK_40;
  uint64_t t4 = ts->t4 & DW_MASK_40;
  uint64_t t5 = ts->t5 & DW_MASK_40;
  uint64_t t6 = ts->t6 & DW_MASK_40;

  uint64_t Ra = 0, Rb = 0, Da = 0, Db = 0;
  bool     ok = dstwr_forward_interval_40(t4, t1, &Ra) && dstwr_forward_interval_40(t6, t3, &Rb)
                && dstwr_forward_interval_40(t5, t4, &Da) && dstwr_forward_interval_40(t3, t2, &Db);

  if (!ok)
  {
    RANGING_LOG_D(LOG_OBJECT_CODE_RANGING, "[ANCHOR][DBG] seq=%u a=%u invalid 40-bit interval ordering/wrap",
                  (unsigned) seq, (unsigned) anchor_id);
    return;
  }

  double   num_d    = ((double) Ra * (double) Rb) - ((double) Da * (double) Db);
  uint64_t den_u64  = (Ra + Rb + Da + Db);
  double   den_d    = (double) den_u64;
  double   tof_dw   = (den_u64 > 0ULL) ? (num_d / den_d) : -1.0;
  int64_t  e_tag    = (int64_t) Ra - (int64_t) Da;
  int64_t  e_anchor = (int64_t) Rb - (int64_t) Db;

  RANGING_LOG_D(LOG_OBJECT_CODE_RANGING, "[ANCHOR][DBG] seq=%u a=%u t1=" DW_FMT " t2=" DW_FMT " t3=" DW_FMT,
                (unsigned) seq, (unsigned) anchor_id, DW_ARG(t1), DW_ARG(t2), DW_ARG(t3));

  RANGING_LOG_D(LOG_OBJECT_CODE_RANGING, "[ANCHOR][DBG] seq=%u a=%u t4=" DW_FMT " t5=" DW_FMT " t6=" DW_FMT,
                (unsigned) seq, (unsigned) anchor_id, DW_ARG(t4), DW_ARG(t5), DW_ARG(t6));

  RANGING_LOG_D(
    LOG_OBJECT_CODE_RANGING,
    "[ANCHOR][DBG] seq=%u a=%u Ra=%lu Rb=%lu Da=%lu Db=%lu Etag=%ld Eanc=%ld num=%.3e den=%.3e tof_dw=%.3e",
    (unsigned) seq, (unsigned) anchor_id, (unsigned long) Ra, (unsigned long) Rb, (unsigned long) Da,
    (unsigned long) Db, (long) e_tag, (long) e_anchor, num_d, den_d, tof_dw);
#else
  (void)seq;
  (void)anchor_id;
  (void)ts;
#endif
}

static float calculate_distance(const dstwr_timestamps_t *ts)
{
  uint64_t t1 = ts->t1 & DW_MASK_40;
  uint64_t t2 = ts->t2 & DW_MASK_40;
  uint64_t t3 = ts->t3 & DW_MASK_40;
  uint64_t t4 = ts->t4 & DW_MASK_40;
  uint64_t t5 = ts->t5 & DW_MASK_40;
  uint64_t t6 = ts->t6 & DW_MASK_40;

  uint64_t Ra = 0, Rb = 0, Da = 0, Db = 0;
  if (!dstwr_forward_interval_40(t4, t1, &Ra) || !dstwr_forward_interval_40(t6, t3, &Rb)
      || !dstwr_forward_interval_40(t5, t4, &Da) || !dstwr_forward_interval_40(t3, t2, &Db))
  {
    RLOG_W(LOG_OBJECT_CODE_RANGING, "[ANCHOR] Invalid interval ordering/wrap in DS-TWR timestamps");
    return -1.0f;
  }

  if (Ra < 1000ULL || Rb < 1000ULL)
  {
    RLOG_W(LOG_OBJECT_CODE_RANGING, "[ANCHOR] Invalid timestamps: Ra=%lu, Rb=%lu", (unsigned long) Ra,
           (unsigned long) Rb);
    return -1.0f;
  }

  uint64_t den = Ra + Rb + Da + Db;
  if (den == 0ULL)
  {
    RLOG_W(LOG_OBJECT_CODE_RANGING, "[ANCHOR] Zero denominator in distance calculation");
    return -1.0f;
  }

  float fRa = (float) Ra, fRb = (float) Rb, fDa = (float) Da, fDb = (float) Db;
  float num    = (fRa * fRb) - (fDa * fDb);
  float tof_dw = num / (float) den;
  if (tof_dw <= 0.0f)
  {
    RLOG_W(LOG_OBJECT_CODE_RANGING, "[ANCHOR] Negative tof_dw");
    return -1.0f;
  }
  return tof_dw * (float) DWT_TIME_UNITS * (float) SPEED_OF_LIGHT;
}

static uint16_t control_msg_size(uint8_t type)
{
  const uint8_t desc_count =
      (uint8_t)(sizeof(s_control_msg_descs) / sizeof(s_control_msg_descs[0]));

  for (uint8_t i = 0U; i < desc_count; i++)
  {
    if ((uint8_t)s_control_msg_descs[i].type == type)
    {
      return s_control_msg_descs[i].size;
    }
  }
  return 0U;
}

static inline bool validate_msg_type(const uint8_t *data, uint16_t len, uint8_t expected_type)
{
  if (!data || data[0] != expected_type)
  {
    return false;
  }

  uint16_t min_len = control_msg_size(expected_type);
  if (min_len != 0U)
  {
    return len >= min_len;
  }

  switch (expected_type)
  {
  case MW_DSTWR_MSG_TYPE_POLL:   min_len = sizeof(poll_msg_t);   break;
  case MW_DSTWR_MSG_TYPE_RESP:   min_len = sizeof(resp_msg_t);   break;
  case MW_DSTWR_MSG_TYPE_FINAL:  min_len = sizeof(final_msg_t);  break;
  case MW_DSTWR_MSG_TYPE_RESULT: min_len = sizeof(result_msg_t); break;
  default: return false;
  }
  return len >= min_len;
}

static int hal_rx_wait_valid_msg_at(uint8_t  *buffer,
                                    uint16_t  buffer_size,
                                    uint16_t *received_length,
                                    uint8_t   expected_type,
                                    uint32_t  timeout_us,
                                    bool      use_delayed_rx,
                                    uint64_t  rx_timestamp_dw)
{
  static uint32_t s_unexpected_type_log_tick = 0;
  uint32_t        timeout_ms                 = (timeout_us + 999U) / 1000U;
  uint32_t        start_tick                 = HAL_GetTick();

  if (timeout_ms == 0U)
  {
    timeout_ms = 1U;
  }

  if (!buffer || !received_length)
  {
    return -1;
  }
  *received_length = 0;

  /* Keep RX armed continuously during the whole wait window. */
  bsp_uwb_clear_irq_event();
  if (use_delayed_rx)
  {
    if (RANGING_ENABLE_RX_DELAYED(rx_timestamp_dw, 0) != BSP_OK)
    {
      return -1;
    }
  }
  else if (bsp_uwb_enable_rx(0) != BSP_OK)
  {
    return -1;
  }

  while ((HAL_GetTick() - start_tick) < timeout_ms)
  {
    bsp_err_t rx_err = bsp_uwb_rx(buffer, buffer_size, received_length);
    if (rx_err == BSP_OK && *received_length > 0U)
    {
      if (validate_msg_type(buffer, *received_length, expected_type))
      {
        return 0;
      }

      if ((HAL_GetTick() - s_unexpected_type_log_tick) >= 1000U)
      {
        RLOG_W(LOG_OBJECT_CODE_RANGING, "[RXWAIT] Unexpected frame type=0x%02X len=%u expected=0x%02X",
               (unsigned) buffer[0], (unsigned) *received_length, (unsigned) expected_type);
        s_unexpected_type_log_tick = HAL_GetTick();
      }
    }
    __NOP();
  }

  *received_length = 0;
  return -1;
}

static sys_ranging_err_t hal_tx_immediate_wait_done(const void *data,
                                                    uint16_t length,
                                                    uint32_t timeout_ms)
{
  bsp_uwb_clear_event();
  if (bsp_uwb_tx(data, length) != BSP_OK) {
    return SYS_RANGING_ERR;
  }

  uint32_t start_ms = HAL_GetTick();
  while ((HAL_GetTick() - start_ms) < timeout_ms) {
    /*
     * Summary exchange runs inside the UWB task while it owns the SPI mutex,
     * so service the pending DW1000 IRQ here instead of waiting for the task
     * loop to dispatch it.
     */
    bsp_uwb_dwt_isr();

    bsp_uwb_event_t event;
    while (bsp_uwb_get_event(&event)) {
      if (event.type == BSP_UWB_EVENT_TX_DONE) {
        return SYS_RANGING_OK;
      }
    }
    __NOP();
  }
  return SYS_RANGING_ERR_TIMEOUT;
}

static void state_machine_reset(void)
{
  s_ctx.state            = STATE_IDLE;
  s_ctx.state_entry_tick = 0U;
  s_ctx.anchor_id        = 0U;
  s_ctx.has_result       = false;
  memset(&s_ctx.result_multi, 0, sizeof(s_ctx.result_multi));
  memset(&s_ctx.result_single, 0, sizeof(s_ctx.result_single));
  s_sys_ranging_ev.step = SYS_RANGING_EV_SYS_IDLE;
  bsp_uwb_clear_event();
  bsp_uwb_idle();
}

static void log_ranging_result(const sys_ranging_result_t *result, const char *role)
{
  if (!result || !result->valid)
    return;
  if (result->distance_m > 100.0f || result->distance_m < 0.0f)
  {
    RLOG_W(LOG_OBJECT_CODE_RANGING, "[%s] Invalid distance: %.3f m - REJECTED", role, result->distance_m);
    return;
  }

  s_stats.success_count++;
}

static uint16_t min_nonzero_u16(uint16_t a, uint16_t b)
{
  if (a == 0U) return b;
  if (b == 0U) return a;
  return (a < b) ? a : b;
}

static inline bool dw_time_before_deadline(uint64_t now_dw, uint64_t deadline_dw);

#define TAG_RESP_TO_FINAL_HEADROOM_US 5000U

static void event_anchor_diag_maybe_log(void)
{
  uint32_t now = HAL_GetTick();
  if (s_anchor_diag.last_tick == 0U)
  {
    s_anchor_diag.last_tick = now;
    return;
  }

  uint32_t elapsed_ms = now - s_anchor_diag.last_tick;
  if (elapsed_ms < 1000U)
  {
    return;
  }

  bsp_uwb_event_stats_t ev_stats;
  bsp_uwb_get_event_stats(&ev_stats);

  RLOG_W(LOG_OBJECT_CODE_RANGING,
         "[ANCHOR_DIAG] dt=%lums poll=%lu resp_tx=%lu final=%lu final_poll=%lu for_me=%lu no_final=%lu result_tx=%lu",
         (unsigned long)elapsed_ms,
         (unsigned long)s_anchor_diag.poll_rx,
         (unsigned long)s_anchor_diag.resp_tx_done,
         (unsigned long)s_anchor_diag.final_rx,
         (unsigned long)s_anchor_diag.final_poll_fallback,
         (unsigned long)s_anchor_diag.final_for_me,
         (unsigned long)s_anchor_diag.final_timeout,
         (unsigned long)s_anchor_diag.result_tx_done);

  RLOG_W(LOG_OBJECT_CODE_RANGING,
         "[ANCHOR_IRQ] irq_tx=%lu irq_rx=%lu irq_drop=%lu irq_extra=%lu rx_rearm_fail=%lu",
         (unsigned long)ev_stats.tx_done,
         (unsigned long)ev_stats.rx_ok,
         (unsigned long)ev_stats.queue_overflow,
         (unsigned long)ev_stats.irq_extra_pass,
         (unsigned long)ev_stats.rx_rearm_fail);

  uint32_t last_tick = now;
  memset(&s_anchor_diag, 0, sizeof(s_anchor_diag));
  s_anchor_diag.last_tick = last_tick;
}

static void event_tag_diag_maybe_log(void)
{
  uint32_t now = HAL_GetTick();
  if (s_tag_diag.last_tick == 0U)
  {
    s_tag_diag.last_tick = now;
    return;
  }

  uint32_t elapsed_ms = now - s_tag_diag.last_tick;
  if (elapsed_ms < 1000U)
  {
    return;
  }

  bsp_uwb_event_stats_t ev_stats;
  bsp_uwb_get_event_stats(&ev_stats);

  RLOG_W(LOG_OBJECT_CODE_RANGING,
         "[TAG_RESP] dt=%lums poll_tx=%lu resp_f=%lu resp_p=%lu resp_n=%lu resp_pkt=%lu/%lu resp_poll=%lu resp_all=%lu resp_spin=%lu resp_rxerr=%lu resp_rej=%lu final_d=%lu final_fail=%lu final_miss=%lu",
         (unsigned long)elapsed_ms,
         (unsigned long)s_tag_diag.poll_tx,
         (unsigned long)s_tag_diag.resp_full,
         (unsigned long)s_tag_diag.resp_partial,
         (unsigned long)s_tag_diag.resp_none,
         (unsigned long)s_tag_diag.resp_packets,
         (unsigned long)s_tag_diag.resp_expected_packets,
         (unsigned long)s_tag_diag.resp_poll_fallback,
         (unsigned long)s_tag_diag.resp_all_configured,
         (unsigned long)s_tag_diag.resp_wait_spins,
         (unsigned long)s_tag_diag.resp_rx_errors,
         (unsigned long)s_tag_diag.resp_rejects,
         (unsigned long)s_tag_diag.final_tx_done,
         (unsigned long)s_tag_diag.final_tx_fail,
         (unsigned long)s_tag_diag.final_slot_missed);

  RLOG_W(LOG_OBJECT_CODE_RANGING,
         "[TAG_RESULT] dt=%lums result_f=%lu result_p=%lu result_pkt=%lu/%lu result_poll=%lu result_all=%lu result_spin=%lu result_rxerr=%lu result_rej=%lu",
         (unsigned long)elapsed_ms,
         (unsigned long)s_tag_diag.result_full,
         (unsigned long)s_tag_diag.result_partial,
         (unsigned long)s_tag_diag.result_packets,
         (unsigned long)s_tag_diag.result_expected_packets,
         (unsigned long)s_tag_diag.result_poll_fallback,
         (unsigned long)s_tag_diag.result_all_configured,
         (unsigned long)s_tag_diag.result_wait_spins,
         (unsigned long)s_tag_diag.result_rx_errors,
         (unsigned long)s_tag_diag.result_rejects);

  RLOG_W(LOG_OBJECT_CODE_RANGING,
         "[TAG_IRQ] irq_tx=%lu irq_rx=%lu irq_drop=%lu irq_extra=%lu rx_rearm_fail=%lu",
         (unsigned long)ev_stats.tx_done,
         (unsigned long)ev_stats.rx_ok,
         (unsigned long)ev_stats.queue_overflow,
         (unsigned long)ev_stats.irq_extra_pass,
         (unsigned long)ev_stats.rx_rearm_fail);

  uint32_t last_tick = now;
  memset(&s_tag_diag, 0, sizeof(s_tag_diag));
  s_tag_diag.last_tick = last_tick;
}

static uint8_t event_anchor_resp_mask(void)
{
  uint8_t mask = 0;
  for (uint8_t i = 0; i < 8; i++)
  {
    uint8_t anchor_id = s_sys_ranging_ev.anchor_resp[i].anchor_id;
    if (s_sys_ranging_ev.anchor_resp[i].valid && anchor_id > 0U && anchor_id <= 8U)
    {
      mask |= (uint8_t)(1U << (anchor_id - 1U));
    }
  }
  return mask;
}

static uint8_t event_configured_anchor_mask(uint8_t num_anchors, const uint8_t *anchor_ids)
{
  uint8_t mask = 0;
  if (!anchor_ids)
  {
    return mask;
  }

  for (uint8_t i = 0; i < num_anchors; i++)
  {
    uint8_t anchor_id = anchor_ids[i];
    if (anchor_id > 0U && anchor_id <= 8U)
    {
      mask |= (uint8_t)(1U << (anchor_id - 1U));
    }
  }
  return mask;
}

static uint8_t event_result_mask(void)
{
  uint8_t mask = 0;
  for (uint8_t i = 0; i < s_ctx.result_multi.count; i++)
  {
    uint8_t anchor_id = s_ctx.result_multi.results[i].anchor_id;
    if (anchor_id > 0U && anchor_id <= 8U)
    {
      mask |= (uint8_t)(1U << (anchor_id - 1U));
    }
  }
  return mask;
}

static bool event_result_anchor_expected(uint8_t anchor_id, uint8_t *calib_status)
{
  for (uint8_t i = 0; i < 8; i++)
  {
    if (s_sys_ranging_ev.anchor_resp[i].valid &&
        s_sys_ranging_ev.anchor_resp[i].anchor_id == anchor_id)
    {
      if (calib_status)
      {
        *calib_status = s_sys_ranging_ev.anchor_resp[i].calib_status;
      }
      return true;
    }
  }
  return false;
}

static bool event_tag_ingest_resp_payload(const uint8_t *data,
                                          uint16_t len,
                                          uint64_t rx_ts,
                                          const bsp_uwb_rx_quality_t *quality,
                                          uint8_t num_anchors,
                                          const uint8_t *anchor_ids)
{
  (void)quality;

  if (!validate_msg_type(data, len, MW_DSTWR_MSG_TYPE_RESP))
  {
    return false;
  }

  resp_msg_t *resp = (resp_msg_t *)data;
  if (resp->sequence_num != s_ctx.sequence_num)
  {
    s_tag_diag.resp_rejects++;
    return false;
  }

  int idx = -1;
  for (uint8_t i = 0; i < num_anchors; i++)
  {
    if (anchor_ids[i] == resp->anchor_id)
    {
      idx = i;
      break;
    }
  }

  if (idx < 0 || s_sys_ranging_ev.anchor_resp[idx].valid)
  {
    s_tag_diag.resp_rejects++;
    return false;
  }

  s_sys_ranging_ev.anchor_resp[idx].anchor_id = resp->anchor_id;
  s_sys_ranging_ev.anchor_resp[idx].resp_rx_ts = rx_ts & DW_MASK_40;
  memcpy(&s_sys_ranging_ev.anchor_resp[idx].poll_rx_ts, &resp->poll_rx_ts, sizeof(uint64_t));
  memcpy(&s_sys_ranging_ev.anchor_resp[idx].resp_tx_ts, &resp->resp_tx_ts, sizeof(uint64_t));
  s_sys_ranging_ev.anchor_resp[idx].poll_rx_ts &= DW_MASK_40;
  s_sys_ranging_ev.anchor_resp[idx].resp_tx_ts &= DW_MASK_40;
  s_sys_ranging_ev.anchor_resp[idx].calib_status = resp->calib_status;
  s_sys_ranging_ev.anchor_resp[idx].valid = true;
  s_sys_ranging_ev.num_responses++;
  RANGING_LOG_D(LOG_OBJECT_CODE_RANGING, "[TAG] Got RESP from anchor %u", resp->anchor_id);
  return true;
}

static bool event_tag_ingest_resp_event(const bsp_uwb_event_t *evt,
                                        uint8_t num_anchors,
                                        const uint8_t *anchor_ids)
{
  if (!evt || evt->type != BSP_UWB_EVENT_RX_OK)
  {
    return false;
  }

  return event_tag_ingest_resp_payload(evt->rx_data,
                                       evt->rx_len,
                                       evt->rx_ts,
                                       &evt->rx_quality,
                                       num_anchors,
                                       anchor_ids);
}

static bool event_tag_ingest_result_payload(const uint8_t *data, uint16_t len)
{
  if (!validate_msg_type(data, len, MW_DSTWR_MSG_TYPE_RESULT))
  {
    return false;
  }

  result_msg_t *res = (result_msg_t *)data;
  if (res->sequence_num != s_ctx.sequence_num)
  {
    s_tag_diag.result_rejects++;
    return false;
  }

  bool duplicate = false;
  for (uint8_t i = 0; i < s_ctx.result_multi.count; i++)
  {
    if (s_ctx.result_multi.results[i].anchor_id == res->anchor_id)
    {
      duplicate = true;
      break;
    }
  }

  uint8_t calib_status = SYS_CALIB_STATUS_NORMAL;
  bool expected_anchor = event_result_anchor_expected(res->anchor_id, &calib_status);
  if (!expected_anchor)
  {
    RLOG_W(LOG_OBJECT_CODE_RANGING,
           "[TAG] Unexpected RESULT anchor=%u seq=%u ignored",
           res->anchor_id, res->sequence_num);
  }

  if (!expected_anchor || duplicate || res->valid != 1U || s_ctx.result_multi.count >= 8U)
  {
    s_tag_diag.result_rejects++;
    return false;
  }

  sys_ranging_result_t *tr = &s_ctx.result_multi.results[s_ctx.result_multi.count];
  tr->anchor_id        = res->anchor_id;
  tr->distance_m       = res->distance_m;
  tr->fp_amp_norm_q8   = res->fp_amp_norm_q8;
  tr->fp_snr_q8        = res->fp_snr_q8;
  tr->quality          = (res->fp_amp_norm_q8 > 0U && res->fp_snr_q8 > 0U) ? 1U : 0U;
  tr->calib_status     = calib_status;
  tr->valid            = (res->valid == 1);
  s_ctx.result_multi.count++;
  return true;
}

static bool event_tag_ingest_result_event(const bsp_uwb_event_t *evt)
{
  if (!evt || evt->type != BSP_UWB_EVENT_RX_OK)
  {
    return false;
  }

  return event_tag_ingest_result_payload(evt->rx_data, evt->rx_len);
}

static uint8_t event_tag_poll_ready_resp(uint8_t num_anchors, const uint8_t *anchor_ids)
{
  uint8_t accepted = 0U;

  for (uint8_t i = 0; i < 8U && bsp_uwb_is_rx_ready(); i++)
  {
    uint8_t rx_buf[128] = {0};
    uint16_t rx_len = 0U;
    bsp_err_t err = bsp_uwb_rx(rx_buf, sizeof(rx_buf), &rx_len);
    if (err != BSP_OK || rx_len == 0U)
    {
      s_tag_diag.resp_rx_errors++;
      continue;
    }

    uint64_t rx_ts = 0U;
    bsp_uwb_rx_quality_t quality = {0};
    (void)bsp_uwb_get_last_rx_timestamp(&rx_ts);
    (void)bsp_uwb_get_last_rx_quality(&quality);
    if (event_tag_ingest_resp_payload(rx_buf, rx_len, rx_ts, &quality, num_anchors, anchor_ids))
    {
      accepted++;
    }
  }

  if (accepted > 0U)
  {
    s_tag_diag.resp_poll_fallback += accepted;
  }
  return accepted;
}

static void event_tag_drain_resp_events(uint8_t num_anchors, const uint8_t *anchor_ids)
{
  bsp_uwb_event_t evt;
  while (bsp_uwb_get_event(&evt))
  {
    (void)event_tag_ingest_resp_event(&evt, num_anchors, anchor_ids);
  }
}

static void event_tag_collect_resps_until_deadline(uint8_t num_anchors, const uint8_t *anchor_ids)
{
  while (s_sys_ranging_ev.num_responses < num_anchors &&
         dw_time_before_deadline(bsp_uwb_get_current_time_dw(), s_sys_ranging_ev.deadline_dw))
  {
    uint8_t before = s_sys_ranging_ev.num_responses;
    event_tag_drain_resp_events(num_anchors, anchor_ids);
    (void)event_tag_poll_ready_resp(num_anchors, anchor_ids);
    s_tag_diag.resp_wait_spins++;

    if (s_sys_ranging_ev.num_responses == before && !bsp_uwb_is_rx_ready())
    {
      break;
    }
  }
}

static uint8_t event_tag_poll_ready_result(void)
{
  uint8_t accepted = 0U;

  for (uint8_t i = 0; i < 8U && bsp_uwb_is_rx_ready(); i++)
  {
    uint8_t rx_buf[128] = {0};
    uint16_t rx_len = 0U;
    bsp_err_t err = bsp_uwb_rx(rx_buf, sizeof(rx_buf), &rx_len);
    if (err != BSP_OK || rx_len == 0U)
    {
      s_tag_diag.result_rx_errors++;
      continue;
    }

    if (event_tag_ingest_result_payload(rx_buf, rx_len))
    {
      accepted++;
    }
  }

  if (accepted > 0U)
  {
    s_tag_diag.result_poll_fallback += accepted;
  }
  return accepted;
}

static void event_tag_drain_result_events(void)
{
  bsp_uwb_event_t evt;
  while (bsp_uwb_get_event(&evt))
  {
    (void)event_tag_ingest_result_event(&evt);
  }
}

static void event_tag_collect_results_until_deadline(void)
{
  while (s_ctx.result_multi.count < s_sys_ranging_ev.num_responses &&
         dw_time_before_deadline(bsp_uwb_get_current_time_dw(), s_sys_ranging_ev.deadline_dw))
  {
    uint8_t before = s_ctx.result_multi.count;
    event_tag_drain_result_events();
    (void)event_tag_poll_ready_result();
    s_tag_diag.result_wait_spins++;

    if (s_ctx.result_multi.count == before && !bsp_uwb_is_rx_ready())
    {
      break;
    }
  }
}

static bool event_anchor_poll_ready_final(bsp_uwb_event_t *out_evt)
{
  if (!out_evt)
  {
    return false;
  }

  for (uint8_t i = 0; i < 8U && bsp_uwb_is_rx_ready(); i++)
  {
    uint8_t rx_buf[128] = {0};
    uint16_t rx_len = 0U;
    bsp_err_t err = bsp_uwb_rx(rx_buf, sizeof(rx_buf), &rx_len);
    if (err != BSP_OK || rx_len == 0U)
    {
      break;
    }

    if (!validate_msg_type(rx_buf, rx_len, MW_DSTWR_MSG_TYPE_FINAL))
    {
      continue;
    }

    final_msg_t *fmsg = (final_msg_t *)rx_buf;
    if (fmsg->sequence_num != s_ctx.sequence_num)
    {
      continue;
    }

    memset(out_evt, 0, sizeof(*out_evt));
    out_evt->type = BSP_UWB_EVENT_RX_OK;
    out_evt->rx_len = rx_len;
    memcpy(out_evt->rx_data, rx_buf, rx_len);
    (void)bsp_uwb_get_last_rx_timestamp(&out_evt->rx_ts);
    (void)bsp_uwb_get_last_rx_quality(&out_evt->rx_quality);
    s_anchor_diag.final_poll_fallback++;
    return true;
  }

  return false;
}

static sys_ranging_err_t event_tag_complete_with_results(void)
{
  s_ctx.has_result = true;
  s_ctx.state = STATE_TAG_COMPLETE;
  s_ctx.result_multi.sequence_num = s_ctx.sequence_num;
  s_sys_ranging_ev.step = SYS_RANGING_EV_SYS_IDLE;
  return SYS_RANGING_OK;
}

static uint64_t ensure_future_tx(uint64_t tx_time_dw, uint32_t schedule_guard_us)
{
  uint64_t now      = bsp_uwb_get_current_time_dw();
  uint64_t guard_dw = tdma_us_to_dw(schedule_guard_us);

 
  uint64_t ahead_dw = (tx_time_dw - now) & DW_MASK_40;
  if (ahead_dw == 0ULL || ahead_dw >= (1ULL << 39)) {
    uint32_t behind_us = tdma_dw_to_us((now - tx_time_dw) & DW_MASK_40);
    RLOG_W(LOG_OBJECT_CODE_RANGING, "[TX] Slot already passed - aborting TX (tx=" DW_FMT " now=" DW_FMT " behind=%luus)",
           DW_ARG(tx_time_dw), DW_ARG(now), (unsigned long)behind_us);
    return 0ULL;
  }

  if (ahead_dw <= guard_dw) {
    uint32_t ahead_us = tdma_dw_to_us(ahead_dw);
    RLOG_W(LOG_OBJECT_CODE_RANGING,
           "[TX] Slot too close: ahead=%luus guard=%luus - aborting TX (tx=" DW_FMT " now=" DW_FMT ")",
           (unsigned long) ahead_us,
           (unsigned long) schedule_guard_us,
           DW_ARG(tx_time_dw), DW_ARG(now));
    return 0ULL;
  }
  return tx_time_dw;
} 

/* Predict actual antenna-domain TX time used by DW1000 delayed TX.
 * DW1000 schedules on a quantized chip-time grid (9 LSB dropped overall), then
 * TX antenna delay is applied to produce the TX timestamp domain used by DS-TWR. */
static inline uint64_t predict_delayed_tx_antenna_time(uint64_t requested_tx_time_dw)
{
  uint64_t tx_ant_dly = (uint64_t) bsp_uwb_get_tx_antenna_delay();
  uint64_t chip_time  = (requested_tx_time_dw - tx_ant_dly) & DW_MASK_40;
  uint32_t dx_time    = (uint32_t) (chip_time >> 8);
  dx_time &= 0xFFFFFFFEUL;
  return ((((uint64_t) dx_time) << 8) + tx_ant_dly) & DW_MASK_40;
}

static inline uint32_t tdma_effective_slot_us(const tdma_scheduler_t *tdma)
{
  return tdma->schedule.slot_duration_us + tdma->schedule.guard_time_us;
}

/* ----------------------------------------------------------------
 * TX Timing Verifier
 * Called after every bsp_uwb_tx_delayed() to compare planned vs
 * actual TX timestamp from DW1000 TX_TIME register.
 *
 * Why this matters for TDMA:
 *   planned_dw  = what we asked the scheduler to fire
 *   predicted_dw = predict_delayed_tx_antenna_time(planned)
 *                  = planned after 9-bit DX_TIME quantization
 *   actual_dw   = what DW1000 actually used (from TX_TIME register)
 *
 * Expected delta: 0 ticks (actual == predicted after quantization).
 * If delta ≠ 0, the DW1000 rescheduled the TX, meaning:
 *   - HPDWARN fired and chip fell back to immediate TX (large delta)b
 *   - SPI latency caused scheduler to be called too late
 *   - ensure_future_tx had to push the time forward
 *
 * Severity thresholds (1 tick ≈ 15.65ps, 63898 ticks ≈ 1µs):
 *   |delta| ≤ 1024 ticks  (~16µs)  → OK, normal quantization noise
 *   |delta| ≤ 63898 ticks (~1ms)   → WARN, jitter or minor slip
 *   |delta| >  63898 ticks          → ERROR, slot was missed
 * ---------------------------------------------------------------- */
#if SYS_RANGING_VERIFY_TX_TIMING
static void verify_tx_timing(const char *label,
                             uint8_t     anchor_id,
                             uint8_t     slot_id,
                             uint8_t     seq,
                             uint64_t    planned_dw,
                             uint64_t    predicted_dw,
                             uint64_t    actual_dw,
                             bool        actual_valid)
{
  const int64_t WARN_TICKS = 63898LL; /* 1ms */
  const int64_t OK_TICKS   = 1024LL;  /* ~16µs, covers 9-bit quantization */

  if (!actual_valid && actual_dw == 0)
  {
    if (bsp_uwb_get_last_tx_timestamp(&actual_dw) != BSP_OK)
    {
      RLOG_W(LOG_OBJECT_CODE_RANGING, "[ANCHOR%u] %s TX_TIME unreadable seq=%u slot=%u planned=" DW_FMT,
             anchor_id, label, seq, slot_id, DW_ARG(planned_dw));
      return;
    }
  }

  actual_dw &= DW_MASK_40;
  predicted_dw &= DW_MASK_40;
  planned_dw &= DW_MASK_40;

  /* Delta: actual − predicted (modular, signed interpretation).
   * Using predicted (not planned) as baseline because DX_TIME
   * quantization is deterministic and expected. */
  uint64_t raw_diff = (actual_dw - predicted_dw) & DW_MASK_40;
  int64_t  delta_ticks;
  if (raw_diff > (DW_MASK_40 / 2ULL))
  {
    delta_ticks = (int64_t) (raw_diff) - (int64_t) (DW_MASK_40 + 1ULL);
  }
  else
  {
    delta_ticks = (int64_t) raw_diff;
  }

  int32_t  delta_us  = (int32_t) (delta_ticks / (int64_t) DW_TICKS_PER_US);
  char     sign      = (delta_ticks >= 0) ? '+' : '-';
  uint32_t abs_ticks = (uint32_t) ((delta_ticks >= 0) ? delta_ticks : -delta_ticks);
  uint32_t abs_us    = (uint32_t) ((delta_us >= 0) ? delta_us : -delta_us);

  /* Also show how far planned was pushed by ensure_future_tx */
  int64_t push_ticks = 0;
  {
    uint64_t push_raw = (predicted_dw - planned_dw) & DW_MASK_40;
    if (push_raw > (DW_MASK_40 / 2ULL))
    {
      push_ticks = (int64_t) push_raw - (int64_t) (DW_MASK_40 + 1ULL);
    }
    else
    {
      push_ticks = (int64_t) push_raw;
    }
  }
  int32_t push_us = (int32_t) (push_ticks / (int64_t) DW_TICKS_PER_US);

  if (delta_ticks > WARN_TICKS || delta_ticks < -WARN_TICKS)
  {
    /* > 1ms delta: slot was effectively missed by DW1000 */
    RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING,
           "[ANCHOR%u] %s TX SLOT MISSED seq=%u slot=%u"
           " planned=" DW_FMT " predicted=" DW_FMT " actual=" DW_FMT " delta=%c%lu ticks(%c%luus) push=%ldus",
           anchor_id, label, seq, slot_id, DW_ARG(planned_dw), DW_ARG(predicted_dw), DW_ARG(actual_dw), sign,
           (unsigned long) abs_ticks, sign, (unsigned long) abs_us, (long) push_us);
  }
  else if (delta_ticks > OK_TICKS || delta_ticks < -OK_TICKS)
  {
    /* 16µs–1ms: jitter or minor scheduling slip, worth watching */
    RLOG_W(LOG_OBJECT_CODE_RANGING,
           "[ANCHOR%u] %s TX JITTER seq=%u slot=%u"
           " planned=" DW_FMT " predicted=" DW_FMT " actual=" DW_FMT " delta=%c%lu ticks(%c%luus) push=%ldus",
           anchor_id, label, seq, slot_id, DW_ARG(planned_dw), DW_ARG(predicted_dw), DW_ARG(actual_dw), sign,
           (unsigned long) abs_ticks, sign, (unsigned long) abs_us, (long) push_us);
  }
  else
  {
    /* ≤ 16µs: scheduler is working correctly, log at DEBUG level */
    RANGING_LOG_D(LOG_OBJECT_CODE_RANGING,
                  "[ANCHOR%u] %s TX OK seq=%u slot=%u"
                  " planned=" DW_FMT " actual=" DW_FMT " delta=%c%lu ticks",
                  anchor_id, label, seq, slot_id, DW_ARG(planned_dw), DW_ARG(actual_dw), sign,
                  (unsigned long) abs_ticks);
  }
}
#endif

static uint32_t tdma_compute_final_wait_timeout_us(const tdma_scheduler_t *tdma, uint8_t num_anchors)
{
  uint32_t final_timeout_us = tdma->schedule.poll_to_resp_delay_us
                              + ((uint32_t) num_anchors * tdma_effective_slot_us(tdma))
                              + tdma->schedule.resp_to_final_delay_us + 20000U;
  if (final_timeout_us > 100000U)
  {
    final_timeout_us = 100000U;
  }
  return final_timeout_us;
}

void sys_ranging_set_calib_status(sys_calib_status_t status)
{
  s_calib_status = status;
}

sys_calib_status_t sys_ranging_get_calib_status(void)
{
  return s_calib_status;
}

sys_ranging_err_t sys_ranging_control_send(sys_uwb_control_msg_type_t type,
                                           const void *msg,
                                           uint16_t msg_size,
                                           uint8_t slot_id)
{
  uint16_t registered_size = control_msg_size((uint8_t)type);
  if (!msg || registered_size == 0U || msg_size != registered_size ||
      slot_id > MAX_ANCHORS_SUPPORTED)
  {
    return SYS_RANGING_ERR_PARAM;
  }

  if (msg_size > CONTROL_MSG_MAX_SIZE)
  {
    return SYS_RANGING_ERR_PARAM;
  }

  uint8_t tx_buf[CONTROL_MSG_MAX_SIZE] = { 0 };
  memcpy(tx_buf, msg, msg_size);
  tx_buf[0] = (uint8_t)type;
  if (slot_id != 0U)
  {
    bsp_delay_ms((uint32_t)slot_id * CONTROL_MSG_SLOT_MS);
  }

  return hal_tx_immediate_wait_done(tx_buf, msg_size, 20U);
}

sys_ranging_err_t sys_ranging_control_receive(sys_uwb_control_msg_type_t type,
                                              void *msg,
                                              uint16_t msg_size,
                                              uint32_t timeout_ms)
{
  uint16_t rx_len = 0U;
  uint16_t registered_size = control_msg_size((uint8_t)type);
  if (!msg || registered_size == 0U || msg_size != registered_size)
  {
    return SYS_RANGING_ERR_PARAM;
  }

  if (hal_rx_wait_valid_msg_at((uint8_t *)msg, msg_size, &rx_len,
                               (uint8_t)type,
                               timeout_ms * 1000U,
                               RX_WAIT_IMMEDIATE,
                               RX_WAIT_NO_DELAYED_TS_DW) != 0)
  {
    return SYS_RANGING_ERR_TIMEOUT;
  }

  return (rx_len == msg_size) ? SYS_RANGING_OK : SYS_RANGING_ERR_PROTO;
}

sys_ranging_err_t sys_ranging_control_send_ack(uint8_t epoch_id,
                                               uint8_t sender_id,
                                               sys_uwb_control_msg_type_t acked_type,
                                               uint8_t acked_value,
                                               uint8_t slot_id)
{
  if (sender_id == 0U || acked_type == SYS_UWB_CTRL_ACK ||
      control_msg_size((uint8_t)acked_type) == 0U)
  {
    return SYS_RANGING_ERR_PARAM;
  }

  const sys_uwb_control_ack_msg_t ack = {
    .epoch_id = epoch_id,
    .sender_id = sender_id,
    .acked_msg_type = (uint8_t)acked_type,
    .acked_value = acked_value,
  };
  return sys_ranging_control_send(SYS_UWB_CTRL_ACK, &ack, sizeof(ack), slot_id);
}

sys_ranging_err_t sys_ranging_control_receive_ack(sys_uwb_control_msg_type_t acked_type,
                                                  sys_uwb_control_ack_msg_t *ack,
                                                  uint32_t timeout_ms)
{
  if (acked_type == SYS_UWB_CTRL_ACK ||
      control_msg_size((uint8_t)acked_type) == 0U)
  {
    return SYS_RANGING_ERR_PARAM;
  }

  sys_ranging_err_t err =
      sys_ranging_control_receive(SYS_UWB_CTRL_ACK, ack, sizeof(*ack), timeout_ms);
  if (err != SYS_RANGING_OK)
  {
    return err;
  }

  return (ack->acked_msg_type == (uint8_t)acked_type)
           ? SYS_RANGING_OK
           : SYS_RANGING_ERR_PROTO;
}

sys_ranging_err_t sys_ranging_control_send_wait_ack(sys_uwb_control_msg_type_t type,
                                                    const void *msg,
                                                    uint16_t msg_size,
                                                    uint8_t slot_id,
                                                    uint8_t expected_ack_sender,
                                                    uint8_t expected_ack_value,
                                                    uint32_t ack_timeout_ms)
{
  if (!msg || msg_size < 2U || expected_ack_sender == 0U ||
      type == SYS_UWB_CTRL_ACK)
  {
    return SYS_RANGING_ERR_PARAM;
  }

  sys_ranging_err_t err = sys_ranging_control_send(type, msg, msg_size, slot_id);
  if (err != SYS_RANGING_OK)
  {
    return err;
  }

  sys_uwb_control_ack_msg_t ack;
  err = sys_ranging_control_receive_ack(type, &ack, ack_timeout_ms);
  if (err != SYS_RANGING_OK)
  {
    return err;
  }

  const uint8_t *msg_bytes = (const uint8_t *)msg;
  if (ack.epoch_id != msg_bytes[1] ||
      ack.sender_id != expected_ack_sender ||
      ack.acked_value != expected_ack_value)
  {
    return SYS_RANGING_ERR_PROTO;
  }

  return SYS_RANGING_OK;
}

uint8_t sys_ranging_get_current_slot(void)
{
  if (s_tdma_tag.synchronized)
    return s_tdma_tag.current_slot;
  if (s_tdma_anchor.synchronized)
    return s_tdma_anchor.current_slot;
  return 0;
}

uint32_t sys_ranging_get_superframe_count(void)
{
  if (s_tdma_tag.synchronized)
    return s_tdma_tag.superframe_counter;
  if (s_tdma_anchor.synchronized)
    return s_tdma_anchor.superframe_counter;
  return 0;
}

static uint64_t tdma_compute_resp_rx_window_end(const tdma_scheduler_t *tdma,
                                                const uint8_t          *anchor_ids,
                                                uint8_t                 num_anchors,
                                                uint64_t                fallback_start_dw)
{
  uint64_t max_rx_end_dw = 0;
  bool     have_window   = false;

  for (uint8_t i = 0; i < num_anchors; i++)
  {
    uint64_t rx_start_dw = 0;
    uint64_t rx_end_dw   = 0;
    if (tdma_get_slot_rx_window(tdma, anchor_ids[i], &rx_start_dw, &rx_end_dw) == TDMA_OK)
    {
      if (!have_window || (int64_t) (rx_end_dw - max_rx_end_dw) > 0)
      {
        max_rx_end_dw = rx_end_dw;
        have_window   = true;
      }
    }
  }

  if (!have_window)
  {
    uint32_t fallback_us =
      tdma->schedule.poll_to_resp_delay_us + ((uint32_t) num_anchors * tdma_effective_slot_us(tdma)) + 2000U;
    return fallback_start_dw + tdma_us_to_dw(fallback_us);
  }

  /* tdma_get_slot_rx_window already includes guard + processing margin.
   * The old +2000us was an unexplained extra layer on top — removed. */
  return max_rx_end_dw & DW_MASK_40;
}

static uint64_t tdma_compute_result_rx_window_end(const tdma_scheduler_t *tdma,
                                                  uint64_t                final_tx_ts_dw,
                                                  uint8_t                 max_result_slot)
{
  uint32_t late_margin_us =
    tdma->schedule.guard_time_us + tdma->schedule.processing_margin_us + TDMA_CLOCK_GUARD_US + 2500U;
  /* FIX: after Bug-B fix, anchor RESULT offset = final_to_result_delay + slot_id * effective_slot
   * (slot_id, NOT slot_id-1). So the last slot fires at:
   *   final + final_to_result_delay + max_result_slot * effective_slot
   * We add one extra effective_slot of window to absorb ensure_future_tx slippage. */
  uint32_t result_phase_us = tdma->schedule.final_to_result_delay_us
                             + (((uint32_t) max_result_slot + 1U) * tdma_effective_slot_us(tdma))
                             + late_margin_us;
  return (final_tx_ts_dw + tdma_us_to_dw(result_phase_us)) & DW_MASK_40;
}

static inline bool dw_time_before_deadline(uint64_t now_dw, uint64_t deadline_dw)
{
  const uint64_t HALF_RANGE_40 = (1ULL << 39);
  uint64_t       remaining     = (deadline_dw - now_dw) & DW_MASK_40;
  return (remaining > 0ULL) && (remaining < HALF_RANGE_40);
}

static bool tdma_anchor_config_matches(uint8_t anchor_id, uint8_t num_anchors, const uint8_t *anchor_ids)
{
  if (!s_tdma_anchor.initialized)
  {
    return false;
  }
  if (s_tdma_anchor.role != TDMA_ROLE_ANCHOR)
  {
    return false;
  }
  if (s_tdma_anchor.device_id != anchor_id)
  {
    return false;
  }
  if (s_tdma_anchor.schedule.num_anchors != num_anchors)
  {
    return false;
  }
  if (!anchor_ids)
  {
    return false;
  }

  for (uint8_t i = 0; i < num_anchors; i++)
  {
    if (s_tdma_anchor.schedule.anchor_ids[i] != anchor_ids[i])
    {
      return false;
    }
  }

  return true;
}

static bool tdma_tag_config_matches(uint8_t num_anchors, const uint8_t *anchor_ids)
{
  if (!s_tdma_tag.initialized)
  {
    return false;
  }
  if (s_tdma_tag.role != TDMA_ROLE_TAG)
  {
    return false;
  }
  if (s_tdma_tag.schedule.num_anchors != num_anchors)
  {
    return false;
  }
  if (!anchor_ids)
  {
    return false;
  }

  for (uint8_t i = 0; i < num_anchors; i++)
  {
    if (s_tdma_tag.schedule.anchor_ids[i] != anchor_ids[i])
    {
      return false;
    }
  }

  return true;
}

/* Public functions ------------------------------------------------------ */
sys_ranging_err_t sys_ranging_tag_start_tdma(uint8_t        num_anchors,
                                             const uint8_t *anchor_ids,
                                             uint8_t        sequence_num,
                                             uint32_t       rx_timeout_ms)
{
  (void) rx_timeout_ms;

  if (s_ctx.state != STATE_IDLE) {
    /* Tránh in log rác */
    static uint32_t last_busy = 0;
    if (HAL_GetTick() - last_busy >= 1000) {
      RLOG_W(LOG_OBJECT_CODE_RANGING, "[TAG] start busy: state=%d", (int) s_ctx.state);
      last_busy = HAL_GetTick();
    }
    return SYS_RANGING_ERR_BUSY;
  }
  if (num_anchors == 0 || num_anchors > 8 || !anchor_ids)
    return SYS_RANGING_ERR_PARAM;

  state_machine_reset();
  s_ctx.sequence_num     = sequence_num;
  s_ctx.state            = STATE_TAG_RANGING_TDMA;
  s_ctx.state_entry_tick = HAL_GetTick();
  s_stats.total_count++;

  return SYS_RANGING_OK;
}

static uint32_t anchor_smart_discovery_interval_ms(uint32_t power_mode);

sys_ranging_err_t sys_ranging_tag_process_tdma(uint8_t num_anchors, const uint8_t *anchor_ids, uint32_t rx_timeout_ms)
{
  event_tag_diag_maybe_log();

  if (s_ctx.state == STATE_IDLE) return SYS_RANGING_ERR_NOT_STARTED;
  if (s_ctx.state != STATE_TAG_RANGING_TDMA) return SYS_RANGING_ERR;
  
  uint32_t timeout_ms = (rx_timeout_ms == 0) ? DEFAULT_RX_TIMEOUT_MS : rx_timeout_ms;
  /* Use the configured timeout as the whole-cycle watchdog. */
  if (HAL_GetTick() - s_ctx.state_entry_tick > timeout_ms) {
    state_machine_reset();
    s_sys_ranging_ev.step = SYS_RANGING_EV_SYS_IDLE;
    return SYS_RANGING_ERR_TIMEOUT;
  }
  
  // Initialization
  if (s_sys_ranging_ev.step == SYS_RANGING_EV_SYS_IDLE) {
      memset(&s_sys_ranging_ev, 0, sizeof(s_sys_ranging_ev));
      if (!tdma_tag_config_matches(num_anchors, anchor_ids)) {
          tdma_init(&s_tdma_tag, TDMA_ROLE_TAG, 0, num_anchors, anchor_ids);
      }
      s_sys_ranging_ev.step = SYS_RANGING_EV_TAG_TX_POLL;
  }
  
  bsp_uwb_event_t evt;
  bool has_evt = bsp_uwb_get_event(&evt);
  
  switch(s_sys_ranging_ev.step) {
      case SYS_RANGING_EV_TAG_TX_POLL: {
          poll_msg_t poll_msg = {0};
          poll_msg.msg_type = MW_DSTWR_MSG_TYPE_POLL;
          poll_msg.sequence_num = s_ctx.sequence_num;
          poll_msg.tag_id = 0;
          poll_msg.num_anchors = num_anchors;
          for (uint8_t i = 0; i < num_anchors; i++) {
              if (anchor_ids[i] > 0 && anchor_ids[i] <= 8) poll_msg.anchor_mask |= (1 << (anchor_ids[i] - 1));
          }
          if (bsp_uwb_tx(&poll_msg, sizeof(poll_msg)) != BSP_OK) {
              state_machine_reset();
              s_sys_ranging_ev.step = SYS_RANGING_EV_SYS_IDLE;
              return SYS_RANGING_ERR;
          }
          s_tag_diag.poll_tx++;
          s_sys_ranging_ev.step = SYS_RANGING_EV_TAG_WAIT_POLL_TX;
          break;
      }
      case SYS_RANGING_EV_TAG_WAIT_POLL_TX: {
          bool poll_tx_done = false;
          if (has_evt && evt.type == BSP_UWB_EVENT_TX_DONE) {
              s_sys_ranging_ev.poll_tx_ts = evt.tx_ts & DW_MASK_40;
              poll_tx_done = true;
          } else if (has_evt) {
              uint64_t last_tx = 0;
              if (bsp_uwb_get_last_tx_timestamp(&last_tx) == BSP_OK) {
                  s_sys_ranging_ev.poll_tx_ts = last_tx & DW_MASK_40;
                  poll_tx_done = true;
                  RLOG_W(LOG_OBJECT_CODE_RANGING,
                         "[TAG] POLL TX_DONE overwritten - recovered via cached ts");
              }
          }

          if (poll_tx_done) {
              s_tag_diag.poll_tx_done++;
              tdma_start_superframe(&s_tdma_tag, s_sys_ranging_ev.poll_tx_ts);
              s_sys_ranging_ev.deadline_dw = tdma_compute_resp_rx_window_end(&s_tdma_tag, anchor_ids, num_anchors, bsp_uwb_get_current_time_dw());

              /* Cap RESP collection well before FINAL TX. The focused RESP loop
               * can otherwise wait for a missing anchor until the edge and leave
               * too little guard for bsp_uwb_tx_delayed(FINAL). */
              uint64_t final_tx_planned_dw = 0;
              if (tdma_calculate_final_time(&s_tdma_tag, num_anchors, &final_tx_planned_dw) == TDMA_OK) {
                  uint64_t final_tx_headroom_dw =
                      (final_tx_planned_dw - tdma_us_to_dw(TAG_RESP_TO_FINAL_HEADROOM_US)) & DW_MASK_40;
                  if (dw_time_before_deadline(final_tx_headroom_dw, s_sys_ranging_ev.deadline_dw)) {
                      s_sys_ranging_ev.deadline_dw = final_tx_headroom_dw;
                  }
              }

              /* RX already armed by uwb_tx_cb ISR immediately after POLL TX.
               * DO NOT call bsp_uwb_enable_rx() here — it calls dwt_forcetrxoff()
               * which aborts any RESP frame currently being received. */
              s_sys_ranging_ev.step = SYS_RANGING_EV_TAG_WAIT_RESP;
              if (has_evt && evt.type == BSP_UWB_EVENT_RX_OK) {
                  (void)event_tag_ingest_resp_event(&evt, num_anchors, anchor_ids);
              }
          }
          break;
      }
      case SYS_RANGING_EV_TAG_WAIT_RESP: {
          if (has_evt) {
              (void)event_tag_ingest_resp_event(&evt, num_anchors, anchor_ids);
          }
          event_tag_collect_resps_until_deadline(num_anchors, anchor_ids);
          /* RX is automatically re-enabled in uwb_rx_cb (BSP) for continuous listening */

          if (!dw_time_before_deadline(bsp_uwb_get_current_time_dw(), s_sys_ranging_ev.deadline_dw) || s_sys_ranging_ev.num_responses >= num_anchors) {
              uint8_t resp_mask = event_anchor_resp_mask();
              uint8_t configured_mask = event_configured_anchor_mask(num_anchors, anchor_ids);
              s_tag_diag.resp_packets += s_sys_ranging_ev.num_responses;
              s_tag_diag.resp_expected_packets += num_anchors;
              if (resp_mask == configured_mask) {
                  s_tag_diag.resp_all_configured++;
              }
              if (SYS_RANGING_REQUIRE_MIN_ANCHOR_SAMPLES &&
                  s_sys_ranging_ev.num_responses < TAG_MIN_ANCHOR_SAMPLES) {
                  if (s_sys_ranging_ev.num_responses == 0) {
                      s_tag_diag.resp_none++;
                  } else {
                      s_tag_diag.resp_partial++;
                  }
                  s_ctx.result_multi.count = 0;
#if TAG_ABORT_ON_INSUFFICIENT_SAMPLES
                  RLOG_W(LOG_OBJECT_CODE_RANGING,
                         "[TAG] RESP insufficient seq=%u resp=%u/%u min=%u resp_mask=0x%02X - abort before FINAL",
                         s_ctx.sequence_num,
                         s_sys_ranging_ev.num_responses,
                         num_anchors,
                         TAG_MIN_ANCHOR_SAMPLES,
                         resp_mask);
                  sys_ranging_abort();
                  return SYS_RANGING_ERR_PARTIAL;
#else
                  RLOG_W(LOG_OBJECT_CODE_RANGING,
                         "[TAG] RESP insufficient seq=%u resp=%u/%u min=%u resp_mask=0x%02X - continuing to FINAL",
                         s_ctx.sequence_num,
                         s_sys_ranging_ev.num_responses,
                         num_anchors,
                         TAG_MIN_ANCHOR_SAMPLES,
                         resp_mask);
#endif
              }
              if (s_sys_ranging_ev.num_responses < num_anchors) {
                  if (s_sys_ranging_ev.num_responses >= TAG_MIN_ANCHOR_SAMPLES) {
                      s_tag_diag.resp_partial++;
                  }
                  /* Commented out to prevent blocking print from causing us to miss the FINAL TX slot */
                  /*
                  RLOG_W(LOG_OBJECT_CODE_RANGING,
                         "[TAG] RESP partial seq=%u resp=%u/%u resp_mask=0x%02X",
                         s_ctx.sequence_num,
                         s_sys_ranging_ev.num_responses,
                         num_anchors,
                         resp_mask);
                  */
              } else {
                  s_tag_diag.resp_full++;
              }
              // Send FINAL
              uint64_t final_tx_time_dw = 0;
              tdma_calculate_final_time(&s_tdma_tag, num_anchors, &final_tx_time_dw);
              final_tx_time_dw = ensure_future_tx(final_tx_time_dw, RANGING_TX_SCHEDULE_GUARD_US);
              if (final_tx_time_dw == 0ULL) {
                  s_tag_diag.final_slot_missed++;
                  s_ctx.result_multi.count = 0;
                  RLOG_W(LOG_OBJECT_CODE_RANGING,
                         "[TAG] FINAL slot missed (seq=%u resp=%u/%u resp_mask=0x%02X)",
                         s_ctx.sequence_num,
                         s_sys_ranging_ev.num_responses,
                         num_anchors,
                         resp_mask);
                  return event_tag_complete_with_results();
              }
              uint64_t t5_payload = predict_delayed_tx_antenna_time(final_tx_time_dw);
              
              uint8_t final_buf[256] = {0};
              final_msg_t *fmsg = (final_msg_t*)final_buf;
              fmsg->msg_type = MW_DSTWR_MSG_TYPE_FINAL;
              fmsg->sequence_num = s_ctx.sequence_num;
              fmsg->num_responses = s_sys_ranging_ev.num_responses;
              fmsg->anchor_resp_mask = resp_mask;
              uint64_t ptx_pay = s_sys_ranging_ev.poll_tx_ts & DW_MASK_40;
              memcpy(&fmsg->poll_tx_ts, &ptx_pay, sizeof(ptx_pay));
              
              uint8_t fidx = 0;
              for (uint8_t i = 0; i < 8; i++) {
                  if (s_sys_ranging_ev.anchor_resp[i].valid) {
                      uint8_t *entry = final_buf + sizeof(final_msg_t) + (fidx * sizeof(final_anchor_data_t));
                      uint64_t rrx_pay = s_sys_ranging_ev.anchor_resp[i].resp_rx_ts & DW_MASK_40;
                      uint64_t ftx_pay = t5_payload & DW_MASK_40;
                      entry[0] = s_sys_ranging_ev.anchor_resp[i].anchor_id;
                      memcpy(entry+1, &rrx_pay, sizeof(rrx_pay));
                      memcpy(entry+1+sizeof(uint64_t), &ftx_pay, sizeof(ftx_pay));
                      fidx++;
                  }
              }
              uint16_t flen = sizeof(final_msg_t) + (s_sys_ranging_ev.num_responses * sizeof(final_anchor_data_t));
              if (bsp_uwb_tx_delayed(final_buf, flen, final_tx_time_dw) != BSP_OK) {
                  s_tag_diag.final_tx_fail++;
                  s_ctx.result_multi.count = 0;
                  RLOG_W(LOG_OBJECT_CODE_RANGING,
                         "[TAG] FINAL TX failed (seq=%u resp=%u/%u resp_mask=0x%02X)",
                         s_ctx.sequence_num,
                         s_sys_ranging_ev.num_responses,
                         num_anchors,
                         resp_mask);
                  return event_tag_complete_with_results();
              }
              s_sys_ranging_ev.step = SYS_RANGING_EV_TAG_WAIT_FINAL_TX;
          }
          break;
      }
      case SYS_RANGING_EV_TAG_WAIT_FINAL_TX: {
          uint64_t final_tx_ts = 0;
          bool final_tx_done = false;

          if (has_evt && evt.type == BSP_UWB_EVENT_TX_DONE) {
              final_tx_ts   = evt.tx_ts;
              final_tx_done = true;
          } else if (has_evt) {
              /* TX_DONE may have been overwritten in the single-slot event buffer
               * by an incoming RX_OK (e.g. anchor RESULT arriving before main loop
               * processed TX_DONE due to debug logging overhead).
               * Recover: check if the cached last TX timestamp matches the planned
               * FINAL TX window (within ±10ms). */
              uint64_t last_tx = 0;
              if (bsp_uwb_get_last_tx_timestamp(&last_tx) == BSP_OK) {
                  uint64_t expected_final = 0;
                  tdma_calculate_final_time(&s_tdma_tag, num_anchors, &expected_final);
                  uint64_t diff = (last_tx - expected_final) & DW_MASK_40;
                  if (diff < tdma_us_to_dw(10000U)) {
                      final_tx_ts   = last_tx;
                      final_tx_done = true;
                      RLOG_W(LOG_OBJECT_CODE_RANGING,
                             "[TAG] FINAL TX_DONE overwritten by RX - recovered via cached ts");
                  }
              }
          }

          if (final_tx_done) {
              s_tag_diag.final_tx_done++;
              s_ctx.result_multi.count = 0;
              s_ctx.result_multi.sequence_num = s_ctx.sequence_num;
              uint8_t max_slot = 1;
              for (uint8_t i=0; i<8; i++) if (s_sys_ranging_ev.anchor_resp[i].valid) {
                  tdma_slot_t slot={0};
                  if (tdma_get_slot_for_anchor(&s_tdma_tag, s_sys_ranging_ev.anchor_resp[i].anchor_id, &slot)==TDMA_OK) {
                      if (slot.slot_id > max_slot) max_slot = slot.slot_id;
                  }
              }
              s_sys_ranging_ev.deadline_dw = tdma_compute_result_rx_window_end(&s_tdma_tag, final_tx_ts, max_slot);

              /* If the current event is already a RESULT (TX_DONE was overwritten),
               * process it immediately instead of discarding it. */
              if (has_evt) {
                  (void)event_tag_ingest_result_event(&evt);
              }
              if (s_ctx.result_multi.count < s_sys_ranging_ev.num_responses) {
                  (void)event_tag_poll_ready_result();
              }

              /* RX is already re-enabled by BSP */
              s_sys_ranging_ev.step = SYS_RANGING_EV_TAG_WAIT_RESULT;
              event_tag_collect_results_until_deadline();
          }
          break;
      }
      case SYS_RANGING_EV_TAG_WAIT_RESULT: {
          /* Drain all queued RESULT events in one call — same latency issue as WAIT_RESP. */
          if (has_evt) {
              (void)event_tag_ingest_result_event(&evt);
          }
          event_tag_collect_results_until_deadline();
          /* RX is automatically re-enabled in uwb_rx_cb (BSP) */

          if (!dw_time_before_deadline(bsp_uwb_get_current_time_dw(), s_sys_ranging_ev.deadline_dw) || s_ctx.result_multi.count >= s_sys_ranging_ev.num_responses) {
              uint8_t resp_mask = event_anchor_resp_mask();
              uint8_t result_mask = event_result_mask();
              uint8_t configured_mask = event_configured_anchor_mask(num_anchors, anchor_ids);
              s_tag_diag.result_packets += s_ctx.result_multi.count;
              s_tag_diag.result_expected_packets += s_sys_ranging_ev.num_responses;
              if (result_mask == configured_mask) {
                  s_tag_diag.result_all_configured++;
              }
              for (uint8_t i = 0; i < s_ctx.result_multi.count; i++) {
                  log_ranging_result(&s_ctx.result_multi.results[i], "TAG");
              }
              if (SYS_RANGING_REQUIRE_MIN_ANCHOR_SAMPLES &&
                  s_ctx.result_multi.count < TAG_MIN_ANCHOR_SAMPLES) {
                  s_tag_diag.result_partial++;
#if TAG_ABORT_ON_INSUFFICIENT_SAMPLES
                  RLOG_W(LOG_OBJECT_CODE_RANGING,
                         "[TAG] RESULT insufficient seq=%u got=%u/%u resp_mask=0x%02X result_mask=0x%02X - abort cycle",
                         s_ctx.sequence_num,
                         s_ctx.result_multi.count,
                         TAG_MIN_ANCHOR_SAMPLES,
                         resp_mask,
                         result_mask);
                  sys_ranging_abort();
                  return SYS_RANGING_ERR_PARTIAL;
#else
                  RLOG_W(LOG_OBJECT_CODE_RANGING,
                         "[TAG] RESULT insufficient seq=%u got=%u/%u resp_mask=0x%02X result_mask=0x%02X - completing partial cycle",
                         s_ctx.sequence_num,
                         s_ctx.result_multi.count,
                         TAG_MIN_ANCHOR_SAMPLES,
                         resp_mask,
                         result_mask);
#endif
              } else if (s_ctx.result_multi.count < s_sys_ranging_ev.num_responses || result_mask != resp_mask) {
                  s_tag_diag.result_partial++;
                  RLOG_W(LOG_OBJECT_CODE_RANGING,
                         "[TAG] RESULT partial seq=%u result=%u/%u resp_mask=0x%02X result_mask=0x%02X",
                         s_ctx.sequence_num,
                         s_ctx.result_multi.count,
                         s_sys_ranging_ev.num_responses,
                         resp_mask,
                         result_mask);
              } else {
                  s_tag_diag.result_full++;
              }
              RANGING_LOG_D(LOG_OBJECT_CODE_RANGING, "[TAG] Received %u RESULT messages", s_ctx.result_multi.count);
              return event_tag_complete_with_results();
          }
          break;
      }
      default: break;
  }
  return SYS_RANGING_ERR_BUSY;
}


static sys_ranging_err_t anchor_process_tdma_event(uint8_t num_anchors,
                                                   const uint8_t *anchor_ids,
                                                   uint32_t rx_timeout_ms)
{
  event_anchor_diag_maybe_log();

  if (s_ctx.state == STATE_IDLE) return SYS_RANGING_ERR_NOT_STARTED;
  if (s_ctx.state != STATE_ANCHOR_RANGING_TDMA) return SYS_RANGING_ERR;
  
  uint32_t timeout_ms = (rx_timeout_ms == 0) ? DEFAULT_RX_TIMEOUT_MS : rx_timeout_ms;
  uint32_t sm_watchdog_ms = timeout_ms;
  
  if (s_sys_ranging_ev.step == SYS_RANGING_EV_SYS_IDLE || s_sys_ranging_ev.step == SYS_RANGING_EV_ANCHOR_WAIT_POLL) {
      s_ctx.state_entry_tick = HAL_GetTick(); // keep state machine armed
  } else if (HAL_GetTick() - s_ctx.state_entry_tick > sm_watchdog_ms) {
    state_machine_reset();
    s_sys_ranging_ev.step = SYS_RANGING_EV_SYS_IDLE;
    return SYS_RANGING_ERR_TIMEOUT;
  }
  
  if (s_sys_ranging_ev.step == SYS_RANGING_EV_SYS_IDLE) {
      memset(&s_sys_ranging_ev, 0, sizeof(s_sys_ranging_ev));
      if (!tdma_anchor_config_matches(s_ctx.anchor_id, num_anchors, anchor_ids)) {
          tdma_init(&s_tdma_anchor, TDMA_ROLE_ANCHOR, s_ctx.anchor_id, num_anchors, anchor_ids);
      }
      tdma_slot_t my_slot = {0};
      tdma_get_slot_for_anchor(&s_tdma_anchor, s_ctx.anchor_id, &my_slot);
      s_sys_ranging_ev.my_slot_id = my_slot.slot_id;
      s_sys_ranging_ev.step = SYS_RANGING_EV_ANCHOR_WAIT_POLL;
      
      uint8_t mode = (uint8_t)sys_config_get()->uwb.power_mode;
      if (s_anchor_poll_rx_plan.enabled && mode != ANCHOR_POWER_MODE_PERFORMANCE) {
          uint64_t rx_start = s_anchor_poll_rx_plan.rx_start_dw;
          uint64_t now_dw   = bsp_uwb_get_current_time_dw();
          uint64_t ahead_dw = (rx_start - now_dw) & DW_MASK_40;
          if (ahead_dw >= (DW_MASK_40 / 2ULL)) {
              /* Already too late, enable standard RX immediately to catch any emergency Polls */
              bsp_uwb_enable_rx(0);
          } else {
              RANGING_ENABLE_RX_DELAYED(rx_start, 0);
          }
      } else {
          bsp_uwb_enable_rx(0);
      }
  }
  
  bsp_uwb_event_t evt;
  bool has_evt = bsp_uwb_get_event(&evt);
  
  switch(s_sys_ranging_ev.step) {
      case SYS_RANGING_EV_ANCHOR_WAIT_POLL: {
          bool poll_received = false;
          do {
              if (has_evt && evt.type == BSP_UWB_EVENT_RX_OK && validate_msg_type(evt.rx_data, evt.rx_len, MW_DSTWR_MSG_TYPE_POLL)) {
                  poll_received = true;
                  break;
              }
          } while ((has_evt = bsp_uwb_get_event(&evt)) != false);

          if (poll_received) {
              s_anchor_diag.poll_rx++;
              poll_msg_t *poll = (poll_msg_t*)evt.rx_data;
              s_ctx.sequence_num = poll->sequence_num;
              s_sys_ranging_ev.poll_rx_ts = evt.rx_ts;
              s_sys_ranging_ev.poll_quality = evt.rx_quality;
              s_ctx.result_single.calib_status = SYS_CALIB_STATUS_NORMAL;
              tdma_sync_to_poll(&s_tdma_anchor, s_sys_ranging_ev.poll_rx_ts);
              
              uint64_t rtx_dw=0;
              tdma_calculate_response_time(&s_tdma_anchor, s_ctx.anchor_id, &rtx_dw);
              rtx_dw = ensure_future_tx(rtx_dw, RANGING_TX_SCHEDULE_GUARD_US);
              if (rtx_dw == 0ULL) {
                  state_machine_reset();
                  return SYS_RANGING_ERR;
              }
              s_sys_ranging_ev.predicted_tx_dw = predict_delayed_tx_antenna_time(rtx_dw);
              s_sys_ranging_ev.resp_tx_ts = s_sys_ranging_ev.predicted_tx_dw;
              
              resp_msg_t rmsg = {0};
              rmsg.msg_type = MW_DSTWR_MSG_TYPE_RESP;
              rmsg.sequence_num = s_ctx.sequence_num;
              rmsg.anchor_id = s_ctx.anchor_id;
              rmsg.slot_id = s_sys_ranging_ev.my_slot_id;
              uint64_t p_rx = s_sys_ranging_ev.poll_rx_ts & DW_MASK_40;
              uint64_t r_tx = s_sys_ranging_ev.resp_tx_ts & DW_MASK_40;
              memcpy(&rmsg.poll_rx_ts, &p_rx, sizeof(p_rx));
              memcpy(&rmsg.resp_tx_ts, &r_tx, sizeof(r_tx));
              rmsg.calib_status = (uint8_t) s_calib_status;
              
              s_sys_ranging_ev.planned_tx_dw = rtx_dw;
              if (bsp_uwb_tx_delayed(&rmsg, sizeof(rmsg), rtx_dw) != BSP_OK) {
                  state_machine_reset();
                  s_sys_ranging_ev.step = SYS_RANGING_EV_SYS_IDLE;
                  return SYS_RANGING_ERR;
              }
              s_sys_ranging_ev.step = SYS_RANGING_EV_ANCHOR_WAIT_RESP_TX;
          } else {
              if (HAL_GetTick() - s_ctx.state_entry_tick > timeout_ms) {
                  state_machine_reset();
                  s_sys_ranging_ev.step = SYS_RANGING_EV_SYS_IDLE;
                  return SYS_RANGING_ERR_TIMEOUT;
              }
          }
          break;
      }
      case SYS_RANGING_EV_ANCHOR_WAIT_RESP_TX: {
          bool tx_done = false;
          do {
              if (has_evt && evt.type == BSP_UWB_EVENT_TX_DONE) {
                  s_sys_ranging_ev.resp_tx_ts = evt.tx_ts & DW_MASK_40;
#if SYS_RANGING_VERIFY_TX_TIMING
                  verify_tx_timing("RESP", s_ctx.anchor_id, s_sys_ranging_ev.my_slot_id, s_ctx.sequence_num, s_sys_ranging_ev.planned_tx_dw, s_sys_ranging_ev.predicted_tx_dw, evt.tx_ts, true);
#endif
                  tx_done = true;
                  break;
              }
              if (has_evt && evt.type == BSP_UWB_EVENT_RX_OK &&
                  validate_msg_type(evt.rx_data, evt.rx_len, MW_DSTWR_MSG_TYPE_FINAL)) {
                  final_msg_t *fmsg = (final_msg_t *)evt.rx_data;
                  if (fmsg->sequence_num == s_ctx.sequence_num) {
                      s_sys_ranging_ev.pending_final_evt = evt;
                      s_sys_ranging_ev.pending_final_valid = true;
                      s_anchor_diag.final_rx_early++;
                  }
              }
          } while ((has_evt = bsp_uwb_get_event(&evt)) != false);

          if (!tx_done) {
              uint64_t last_tx=0;
              if (bsp_uwb_get_last_tx_timestamp(&last_tx)==BSP_OK) {
                  uint64_t diff = (last_tx - s_sys_ranging_ev.planned_tx_dw) & DW_MASK_40;
                  if (diff < tdma_us_to_dw(5000U)) {
                      s_sys_ranging_ev.resp_tx_ts = last_tx & DW_MASK_40;
                      tx_done = true;
                  }
              }
          }

          if (tx_done) {
              s_anchor_diag.resp_tx_done++;
              s_sys_ranging_ev.step = SYS_RANGING_EV_ANCHOR_WAIT_FINAL;
              
              uint64_t expected_final_dw = 0;
              tdma_calculate_final_time(&s_tdma_anchor, num_anchors, &expected_final_dw);
              uint64_t rx_start_dw = (expected_final_dw - tdma_us_to_dw(1000U)) & DW_MASK_40;
              (void)rx_start_dw;
              
              /* Deadline for WAIT_FINAL timeout */
              uint32_t final_timeout_us = tdma_compute_final_wait_timeout_us(&s_tdma_anchor, num_anchors);
              s_sys_ranging_ev.deadline_dw = (rx_start_dw + tdma_us_to_dw(final_timeout_us)) & DW_MASK_40;

              /* BSP TX callback already re-arms RX immediately after RESP TX.
               * Calling bsp_uwb_enable_rx()/delayed here would force TRX off and
               * can abort a FINAL frame that arrived while this state machine was
               * still processing TX_DONE. */
          }
          break;
      }
      case SYS_RANGING_EV_ANCHOR_WAIT_FINAL: {
          bool final_received = false;
          bsp_uwb_event_t final_evt;
          memset(&final_evt, 0, sizeof(final_evt));
          if (s_sys_ranging_ev.pending_final_valid) {
              final_evt = s_sys_ranging_ev.pending_final_evt;
              s_sys_ranging_ev.pending_final_valid = false;
              final_received = true;
          } else {
              do {
                  if (has_evt && evt.type == BSP_UWB_EVENT_RX_OK && validate_msg_type(evt.rx_data, evt.rx_len, MW_DSTWR_MSG_TYPE_FINAL)) {
                      final_evt = evt;
                      final_received = true;
                      break;
                  }
              } while ((has_evt = bsp_uwb_get_event(&evt)) != false);
          }

          if (!final_received && event_anchor_poll_ready_final(&final_evt)) {
              final_received = true;
          }

          if (final_received) {
              s_anchor_diag.final_rx++;
              final_msg_t *fmsg = (final_msg_t*)final_evt.rx_data;
              if (fmsg->sequence_num == s_ctx.sequence_num) {
                  uint64_t ptx_tag=0; memcpy(&ptx_tag, &fmsg->poll_tx_ts, sizeof(ptx_tag)); ptx_tag &= DW_MASK_40;
                  uint64_t rrx_tag=0, ftx_tag=0;
                  bool found = false;
                  /* fmsg->num_responses comes off-air and is untrusted; rx_data is a
                   * fixed 128-byte buffer. Bound the parse by BOTH the logical max and
                   * the bytes actually received so an inflated count cannot walk past
                   * the buffer (OOB read). */
                  uint8_t n_resp = fmsg->num_responses;
                  uint8_t max_fit = 0U;
                  if (final_evt.rx_len > sizeof(final_msg_t)) {
                      max_fit = (uint8_t)((final_evt.rx_len - sizeof(final_msg_t)) / sizeof(final_anchor_data_t));
                  }
                  if (n_resp > max_fit) n_resp = max_fit;
                  if (n_resp > MAX_ANCHORS_SUPPORTED) n_resp = MAX_ANCHORS_SUPPORTED;
                  for (uint8_t i=0; i<n_resp; i++) {
                      uint8_t *entry = final_evt.rx_data + sizeof(final_msg_t) + (i*sizeof(final_anchor_data_t));
                      if (entry[0] == s_ctx.anchor_id) {
                          memcpy(&rrx_tag, entry+1, sizeof(rrx_tag));
                          memcpy(&ftx_tag, entry+1+sizeof(uint64_t), sizeof(ftx_tag));
                          found = true; break;
                      }
                  }
                  if (found) {
                      s_anchor_diag.final_for_me++;
                      dstwr_timestamps_t ts;
                      ts.t1 = ptx_tag; ts.t2 = s_sys_ranging_ev.poll_rx_ts; ts.t3 = s_sys_ranging_ev.resp_tx_ts;
                      ts.t4 = rrx_tag & DW_MASK_40; ts.t5 = ftx_tag & DW_MASK_40; ts.t6 = final_evt.rx_ts;
                      float dist = calculate_distance(&ts);
                      s_ctx.result_single.distance_m = dist;
                      s_ctx.result_single.anchor_id = s_ctx.anchor_id;
                      s_ctx.result_single.fp_amp_norm_q8 =
                          min_nonzero_u16(s_sys_ranging_ev.poll_quality.fp_amp_norm_q8,
                                          final_evt.rx_quality.fp_amp_norm_q8);
                      s_ctx.result_single.fp_snr_q8 =
                          min_nonzero_u16(s_sys_ranging_ev.poll_quality.fp_snr_q8,
                                          final_evt.rx_quality.fp_snr_q8);
                      s_ctx.result_single.quality =
                          (s_sys_ranging_ev.poll_quality.valid && final_evt.rx_quality.valid) ? 1U : 0U;
                      s_ctx.result_single.calib_status = SYS_CALIB_STATUS_NORMAL;
                      s_ctx.result_single.valid = (dist > 0.0f && dist < 100.0f);
                      
                      uint64_t expected_final_tx_dw = 0;
                      if (tdma_calculate_final_time(&s_tdma_anchor, num_anchors, &expected_final_tx_dw) != TDMA_OK) {
                          expected_final_tx_dw = final_evt.rx_ts;
                      }
                      
                      uint32_t rofs = s_tdma_anchor.schedule.final_to_result_delay_us + (s_sys_ranging_ev.my_slot_id * tdma_effective_slot_us(&s_tdma_anchor));
                      uint64_t res_tx_dw = (expected_final_tx_dw + tdma_us_to_dw(rofs)) & DW_MASK_40;
                      res_tx_dw = ensure_future_tx(res_tx_dw, RANGING_TX_SCHEDULE_GUARD_US);
                      if (res_tx_dw == 0ULL) {
                          s_anchor_diag.result_slot_missed++;
                          state_machine_reset();
                          return SYS_RANGING_ERR;
                      }
                      
                      result_msg_t res = {0};
                      res.msg_type = MW_DSTWR_MSG_TYPE_RESULT;
                      res.sequence_num = s_ctx.sequence_num;
                      res.anchor_id = s_ctx.anchor_id;
                      res.slot_id = s_sys_ranging_ev.my_slot_id;
                      res.valid = s_ctx.result_single.valid ? 1 : 0;
                      res.distance_m = s_ctx.result_single.distance_m;
                      res.fp_amp_norm_q8 = s_ctx.result_single.fp_amp_norm_q8;
                      res.fp_snr_q8      = s_ctx.result_single.fp_snr_q8;
                      
                      s_sys_ranging_ev.planned_tx_dw = res_tx_dw;
#if SYS_RANGING_VERIFY_TX_TIMING
                      s_sys_ranging_ev.predicted_tx_dw = predict_delayed_tx_antenna_time(res_tx_dw);
#endif
                      if (bsp_uwb_tx_delayed(&res, sizeof(res), res_tx_dw) != BSP_OK) {
                          s_anchor_diag.result_tx_fail++;
                          state_machine_reset();
                          s_sys_ranging_ev.step = SYS_RANGING_EV_SYS_IDLE;
                          return SYS_RANGING_ERR;
                      }
                      log_dstwr_debug(s_ctx.sequence_num, s_ctx.anchor_id, &ts);
                      RANGING_LOG_D(LOG_OBJECT_CODE_RANGING, "[ANCHOR] DIST: seq=%u anchor=%u d=%.3fm valid=%u",
                                    s_ctx.sequence_num, s_ctx.anchor_id, s_ctx.result_single.distance_m,
                                    (unsigned) s_ctx.result_single.valid);
                      s_sys_ranging_ev.step = SYS_RANGING_EV_ANCHOR_WAIT_RESULT_TX;
                  } else {
                      s_anchor_diag.final_not_for_me++;
                  }
              }
          } else {
              if (!dw_time_before_deadline(bsp_uwb_get_current_time_dw(), s_sys_ranging_ev.deadline_dw)) {
                  s_anchor_diag.final_timeout++;
                  state_machine_reset();
                  return SYS_RANGING_ERR_TIMEOUT;
              }
          }
          break;
      }
      case SYS_RANGING_EV_ANCHOR_WAIT_RESULT_TX: {
          bool tx_done = false;
          do {
              if (has_evt && evt.type == BSP_UWB_EVENT_TX_DONE) {
#if SYS_RANGING_VERIFY_TX_TIMING
                  verify_tx_timing("RESULT", s_ctx.anchor_id, s_sys_ranging_ev.my_slot_id, s_ctx.sequence_num, s_sys_ranging_ev.planned_tx_dw, s_sys_ranging_ev.predicted_tx_dw, evt.tx_ts, true);
#endif
                  tx_done = true;
                  break;
              }
          } while ((has_evt = bsp_uwb_get_event(&evt)) != false);

          if (!tx_done) {
              uint64_t last_tx=0;
              if (bsp_uwb_get_last_tx_timestamp(&last_tx)==BSP_OK) {
                  uint64_t diff = (last_tx - s_sys_ranging_ev.planned_tx_dw) & DW_MASK_40;
                  if (diff < tdma_us_to_dw(5000U)) {
                      tx_done = true;
                  }
              }
          }

          if (tx_done) {
              s_anchor_diag.result_tx_done++;
              s_ctx.has_result = true;
              s_ctx.state = STATE_ANCHOR_COMPLETE;
              log_ranging_result(&s_ctx.result_single, "ANCHOR");
              s_sys_ranging_ev.step = SYS_RANGING_EV_SYS_IDLE;
              return SYS_RANGING_OK;
          }
          break;
      }
      default: break;
  }
  return SYS_RANGING_ERR_BUSY;
}

static uint32_t anchor_smart_clamp_u32(uint32_t value, uint32_t min_value, uint32_t max_value)
{
  if (value < min_value) return min_value;
  if (value > max_value) return max_value;
  return value;
}

static bool anchor_smart_tick_due(uint32_t now, uint32_t due)
{
  return ((int32_t)(now - due) >= 0);
}

static uint32_t anchor_smart_discovery_interval_ms(uint32_t power_mode)
{
  power_mode = anchor_smart_clamp_power_mode(power_mode);
  if (power_mode == ANCHOR_POWER_MODE_PERFORMANCE) return 0U;
  if (power_mode == ANCHOR_POWER_MODE_ECO) return ANCHOR_SMART_DISCOVERY_ECO_MS;
  if (power_mode == ANCHOR_POWER_MODE_DEEP_ECO) return ANCHOR_SMART_DISCOVERY_DEEP_ECO_MS;
  return ANCHOR_SMART_DISCOVERY_BALANCED_MS;
}

static uint32_t anchor_smart_estimate_poll_tick(const sys_ranging_result_t *result, uint32_t now_tick)
{
  if (!result || result->t2 == 0ULL) {
    return now_tick;
  }

  uint64_t now_dw = bsp_uwb_get_current_time_dw();
  uint64_t elapsed_dw = (now_dw - (result->t2 & DW_MASK_40)) & DW_MASK_40;
  uint32_t elapsed_ms = tdma_dw_to_us(elapsed_dw) / 1000U;

  if (elapsed_ms > 1000U) {
    return now_tick;
  }

  return now_tick - elapsed_ms;
}

static void anchor_smart_switch_discovery(uint32_t now_tick, bool log_transition)
{
  if (log_transition && s_anchor_smart_rx.mode != ANCHOR_RX_DISCOVERY) {
    RLOG_I(LOG_OBJECT_CODE_RANGING, "[ANCHOR] RX policy -> DISCOVERY");
  }
  s_anchor_smart_rx.mode = ANCHOR_RX_DISCOVERY;
  s_anchor_smart_rx.track_misses = 0U;
  s_anchor_smart_rx.discovery_misses = 0U;
  s_anchor_smart_rx.stable_successes = 0U;
  anchor_smart_set_active_level(ANCHOR_POWER_MODE_PERFORMANCE, log_transition);
  s_anchor_smart_rx.next_window_tick = now_tick;
  s_anchor_smart_rx.next_poll_tick = 0U;
  s_anchor_smart_rx.next_poll_dw = 0ULL;
  s_anchor_smart_rx.initialized = true;
}

static uint32_t anchor_smart_tracking_pre_poll_ms(void)
{
  uint32_t active_mode = anchor_smart_active_power_mode(sys_config_get()->uwb.power_mode);
  uint32_t pre_ms = ANCHOR_SMART_TRACK_PRE_POLL_MS;

  if (active_mode == ANCHOR_POWER_MODE_PERFORMANCE)
  {
    pre_ms += 10U;
  }
  else if (active_mode == ANCHOR_POWER_MODE_ECO)
  {
    pre_ms -= 3U;
  }
  else if (active_mode == ANCHOR_POWER_MODE_DEEP_ECO)
  {
    pre_ms -= 5U;
  }

  pre_ms += ((uint32_t)s_anchor_smart_rx.track_misses * ANCHOR_SMART_TRACK_MISS_PRE_STEP_MS);
  return anchor_smart_clamp_u32(pre_ms, 8U, ANCHOR_SMART_TRACK_MAX_PRE_POLL_MS);
}

static uint32_t anchor_smart_tracking_late_margin_ms(void)
{
  uint32_t active_mode = anchor_smart_active_power_mode(sys_config_get()->uwb.power_mode);
  uint32_t late_ms = ANCHOR_SMART_TRACK_LATE_MARGIN_MS;

  if (active_mode == ANCHOR_POWER_MODE_PERFORMANCE)
  {
    late_ms += 20U;
  }
  else if (active_mode == ANCHOR_POWER_MODE_ECO)
  {
    late_ms -= 5U;
  }
  else if (active_mode == ANCHOR_POWER_MODE_DEEP_ECO)
  {
    late_ms -= 10U;
  }

  late_ms += ((uint32_t)s_anchor_smart_rx.track_misses * ANCHOR_SMART_TRACK_MISS_LATE_STEP_MS);
  return late_ms;
}

static void anchor_smart_switch_tracking(const sys_ranging_result_t *result, uint32_t now_tick)
{
  uint32_t period_ms = sys_config_get()->uwb.ranging_period_ms;
  uint32_t poll_tick = anchor_smart_estimate_poll_tick(result, now_tick);
  uint32_t pre_poll_ms = anchor_smart_tracking_pre_poll_ms();
  bool was_tracking = (s_anchor_smart_rx.mode == ANCHOR_RX_TRACKING);

  if (period_ms == 0U) {
    period_ms = DEFAULT_RANGING_PERIOD_MS;
  }

  if (s_anchor_smart_rx.mode != ANCHOR_RX_TRACKING) {
    RLOG_I(LOG_OBJECT_CODE_RANGING, "[ANCHOR] RX policy -> TRACKING");
  }

  s_anchor_smart_rx.mode = ANCHOR_RX_TRACKING;
  s_anchor_smart_rx.track_misses = 0U;
  s_anchor_smart_rx.discovery_misses = 0U;
  if (!was_tracking) {
    s_anchor_smart_rx.stable_successes = 0U;
  }
  s_anchor_smart_rx.next_poll_tick = poll_tick + period_ms;
  s_anchor_smart_rx.next_poll_dw = (result && result->t2 != 0ULL)
      ? ((result->t2 + tdma_us_to_dw(period_ms * 1000U)) & DW_MASK_40)
      : 0ULL;
  s_anchor_smart_rx.next_window_tick = s_anchor_smart_rx.next_poll_tick - pre_poll_ms;
  s_anchor_smart_rx.initialized = true;
}

static void anchor_smart_rearm_tracking_window(uint32_t now_tick)
{
  uint32_t period_ms = sys_config_get()->uwb.ranging_period_ms;

  if (period_ms == 0U) {
    period_ms = DEFAULT_RANGING_PERIOD_MS;
  }

  if (s_anchor_smart_rx.next_poll_tick == 0U) {
    s_anchor_smart_rx.next_window_tick = now_tick;
    return;
  }

  for (uint8_t i = 0U; i < 8U; i++)
  {
    uint32_t pre_poll_ms = anchor_smart_tracking_pre_poll_ms();
    uint32_t window_tick = s_anchor_smart_rx.next_poll_tick - pre_poll_ms;

    if (!anchor_smart_tick_due(now_tick, window_tick)) {
      s_anchor_smart_rx.next_window_tick = window_tick;
      return;
    }

    s_anchor_smart_rx.next_poll_tick += period_ms;
    if (s_anchor_smart_rx.next_poll_dw != 0ULL) {
      s_anchor_smart_rx.next_poll_dw =
          (s_anchor_smart_rx.next_poll_dw + tdma_us_to_dw(period_ms * 1000U)) & DW_MASK_40;
    }
  }

  s_anchor_smart_rx.next_window_tick = now_tick;
}

static uint32_t anchor_smart_tracking_window_ms(void)
{
  uint32_t period_ms = sys_config_get()->uwb.ranging_period_ms;
  uint32_t window_ms = anchor_smart_tracking_pre_poll_ms() + anchor_smart_tracking_late_margin_ms();
  uint32_t max_window_ms = ANCHOR_SMART_TRACK_MAX_WINDOW_MS;

  if (period_ms > ANCHOR_SMART_TRACK_REARM_GAP_MS) {
    uint32_t period_cap = period_ms - ANCHOR_SMART_TRACK_REARM_GAP_MS;
    if (period_cap < max_window_ms) {
      max_window_ms = period_cap;
    }
  }

  if (max_window_ms < ANCHOR_SMART_TRACK_MIN_WINDOW_MS) {
    max_window_ms = ANCHOR_SMART_TRACK_MIN_WINDOW_MS;
  }

  return anchor_smart_clamp_u32(window_ms, ANCHOR_SMART_TRACK_MIN_WINDOW_MS, max_window_ms);
}

static uint32_t anchor_smart_window_timeout_ms(uint32_t power_mode, uint32_t default_rx_timeout_ms)
{
  uint32_t active_mode = anchor_smart_active_power_mode(power_mode);

  if (active_mode == ANCHOR_POWER_MODE_PERFORMANCE &&
      s_anchor_smart_rx.mode != ANCHOR_RX_TRACKING) {
    return (default_rx_timeout_ms == 0U) ? DEFAULT_RX_TIMEOUT_MS : default_rx_timeout_ms;
  }

  if (s_anchor_smart_rx.mode == ANCHOR_RX_TRACKING) {
    return anchor_smart_tracking_window_ms();
  }

  return ANCHOR_SMART_DISCOVERY_ON_MS;
}

static void anchor_smart_note_success(uint32_t configured_mode)
{
  uint32_t target_mode = anchor_smart_clamp_power_mode(configured_mode);
  uint32_t active_mode = anchor_smart_active_power_mode(target_mode);

  s_anchor_smart_rx.track_misses = 0U;
  s_anchor_smart_rx.discovery_misses = 0U;

  if (active_mode >= target_mode) {
    s_anchor_smart_rx.stable_successes = 0U;
    anchor_smart_set_active_level(target_mode, false);
    return;
  }

  s_anchor_smart_rx.stable_successes++;
  if (s_anchor_smart_rx.stable_successes >= ANCHOR_SMART_LEVEL_STABLE_SUCCESSES)
  {
    anchor_smart_set_active_level(active_mode + 1U, true);
    s_anchor_smart_rx.stable_successes = 0U;
  }
}

static void anchor_smart_note_tracking_miss(uint32_t configured_mode)
{
  uint32_t active_mode = anchor_smart_active_power_mode(configured_mode);

  s_anchor_smart_rx.stable_successes = 0U;
  s_anchor_smart_rx.discovery_misses = 0U;
  s_anchor_smart_rx.track_misses++;

  if (active_mode > ANCHOR_POWER_MODE_PERFORMANCE)
  {
    anchor_smart_set_active_level(active_mode - 1U, true);
  }
}

static void anchor_smart_note_discovery_miss(uint32_t configured_mode)
{
  uint32_t target_mode = anchor_smart_clamp_power_mode(configured_mode);
  uint32_t active_mode = anchor_smart_active_power_mode(target_mode);

  s_anchor_smart_rx.stable_successes = 0U;
  s_anchor_smart_rx.track_misses = 0U;
  if (target_mode == ANCHOR_POWER_MODE_PERFORMANCE) {
    s_anchor_smart_rx.discovery_misses = 0U;
    return;
  }

  if (active_mode >= target_mode) {
    s_anchor_smart_rx.discovery_misses = 0U;
    anchor_smart_set_active_level(target_mode, true);
    return;
  }

  if (s_anchor_smart_rx.discovery_misses < UINT8_MAX) {
    s_anchor_smart_rx.discovery_misses++;
  }

  if (s_anchor_smart_rx.discovery_misses >= ANCHOR_SMART_DISCOVERY_DECAY_MISSES)
  {
    s_anchor_smart_rx.discovery_misses = 0U;
    anchor_smart_set_active_level(active_mode + 1U, true);
  }
}

static void anchor_smart_prepare_poll_rx_plan(void)
{
  s_anchor_poll_rx_plan.enabled = false;
  s_anchor_poll_rx_plan.rx_start_dw = 0ULL;

  if (s_anchor_smart_rx.mode != ANCHOR_RX_TRACKING ||
      s_anchor_smart_rx.next_poll_dw == 0ULL) {
    return;
  }

  s_anchor_poll_rx_plan.enabled = true;
  s_anchor_poll_rx_plan.rx_start_dw =
      (s_anchor_smart_rx.next_poll_dw -
       tdma_us_to_dw(anchor_smart_tracking_pre_poll_ms() * 1000U)) & DW_MASK_40;
}

sys_ranging_err_t sys_ranging_anchor_process_tdma(uint8_t num_anchors,
                                                  const uint8_t *anchor_ids,
                                                  uint32_t rx_timeout_ms)
{
  uint32_t now = HAL_GetTick();
  uint32_t power_mode = anchor_smart_clamp_power_mode(sys_config_get()->uwb.power_mode);
  uint8_t anchor_id = sys_config_get()->uwb.device_id;
  bool explicit_start = (s_ctx.state != STATE_IDLE);
  bool auto_started = false;
  uint32_t timeout_ms = 0U;

  if (explicit_start) {
    return anchor_process_tdma_event(num_anchors, anchor_ids, rx_timeout_ms);
  }

  if (!s_anchor_smart_rx.initialized) {
    anchor_smart_switch_discovery(now, false);
  }

  if (power_mode == ANCHOR_POWER_MODE_PERFORMANCE) {
    s_anchor_smart_rx.mode = ANCHOR_RX_PERFORMANCE;
    anchor_smart_set_active_level(ANCHOR_POWER_MODE_PERFORMANCE, false);
    s_anchor_smart_rx.stable_successes = 0U;
    s_anchor_smart_rx.track_misses = 0U;
    s_anchor_smart_rx.discovery_misses = 0U;
    s_anchor_smart_rx.next_window_tick = now;
  } else if (s_anchor_smart_rx.mode == ANCHOR_RX_PERFORMANCE) {
    anchor_smart_switch_discovery(now, true);
  }

  if (power_mode != ANCHOR_POWER_MODE_PERFORMANCE &&
      !anchor_smart_tick_due(now, s_anchor_smart_rx.next_window_tick)) {
    return SYS_RANGING_ERR_BUSY;
  }

  if (s_ctx.state == STATE_IDLE) {
    sys_ranging_err_t start_err = sys_ranging_anchor_start_tdma(anchor_id, num_anchors, anchor_ids, rx_timeout_ms);
    if (start_err != SYS_RANGING_OK) {
      return start_err;
    }
    auto_started = true;
  }

  timeout_ms = anchor_smart_window_timeout_ms(power_mode, rx_timeout_ms);
  anchor_smart_prepare_poll_rx_plan();
  sys_ranging_err_t err = anchor_process_tdma_event(num_anchors, anchor_ids, timeout_ms);
  s_anchor_poll_rx_plan.enabled = false;
  s_anchor_poll_rx_plan.rx_start_dw = 0ULL;

  if (err == SYS_RANGING_OK) {
    if (power_mode != ANCHOR_POWER_MODE_PERFORMANCE) {
      anchor_smart_switch_tracking(&s_ctx.result_single, HAL_GetTick());
      anchor_smart_note_success(power_mode);
    }
    return SYS_RANGING_OK;
  }

  if (err == SYS_RANGING_ERR_BUSY) {
    return SYS_RANGING_ERR_BUSY;
  }

  if (auto_started) {
    sys_ranging_abort();
    bsp_uwb_idle();
  }

  if (power_mode == ANCHOR_POWER_MODE_PERFORMANCE) {
    s_anchor_smart_rx.next_window_tick = HAL_GetTick();
  } else if (s_anchor_smart_rx.mode == ANCHOR_RX_TRACKING) {
    anchor_smart_note_tracking_miss(power_mode);
    if (s_anchor_smart_rx.track_misses > ANCHOR_SMART_TRACK_MAX_MISSES) {
      anchor_smart_switch_discovery(HAL_GetTick(), true);
    } else {
      anchor_smart_rearm_tracking_window(HAL_GetTick());
    }
  } else {
    anchor_smart_note_discovery_miss(power_mode);
    uint32_t active_mode = anchor_smart_active_power_mode(power_mode);
    s_anchor_smart_rx.next_window_tick = HAL_GetTick() + anchor_smart_discovery_interval_ms(active_mode);
  }

  return SYS_RANGING_ERR_BUSY;
}

sys_ranging_err_t sys_ranging_tag_get_results_tdma(sys_ranging_multi_result_t *results)
{
  if (!results)
    return SYS_RANGING_ERR_PARAM;
  if (s_ctx.state != STATE_TAG_COMPLETE || !s_ctx.has_result)
    return SYS_RANGING_ERR_NO_RESULT;

  memcpy(results, &s_ctx.result_multi, sizeof(sys_ranging_multi_result_t));
  state_machine_reset();
  return SYS_RANGING_OK;
}

sys_ranging_err_t sys_ranging_anchor_get_last_result(sys_ranging_result_t *result)
{
  if (!result)
    return SYS_RANGING_ERR_PARAM;
  if (!s_ctx.result_single.valid)
    return SYS_RANGING_ERR_NO_RESULT;

  memcpy(result, &s_ctx.result_single, sizeof(sys_ranging_result_t));
  return SYS_RANGING_OK;
}

sys_ranging_err_t sys_ranging_anchor_start_tdma(uint8_t        anchor_id,
                                                uint8_t        num_anchors,
                                                const uint8_t *anchor_ids,
                                                uint32_t       rx_timeout_ms)
{
  if (s_ctx.state != STATE_IDLE)
  {
    /* Tránh log rác liên tục trong vòng lặp main */
    static uint32_t last_busy = 0;
    if (HAL_GetTick() - last_busy >= 1000) {
      RLOG_W(LOG_OBJECT_CODE_RANGING, "[ANCHOR] start busy: state=%d entry=%lu now=%lu", (int) s_ctx.state,
             (unsigned long) s_ctx.state_entry_tick, (unsigned long) HAL_GetTick());
      last_busy = HAL_GetTick();
    }
    return SYS_RANGING_ERR_BUSY;
  }
  if (anchor_id == 0 || anchor_id > 8)
    return SYS_RANGING_ERR_PARAM;
  if (num_anchors == 0 || num_anchors > 8 || !anchor_ids)
    return SYS_RANGING_ERR_PARAM;

  state_machine_reset();
  s_ctx.anchor_id        = anchor_id;
  s_ctx.state            = STATE_ANCHOR_RANGING_TDMA;
  s_ctx.state_entry_tick = HAL_GetTick();
  s_stats.total_count++;

  return SYS_RANGING_OK;
}

sys_ranging_err_t sys_ranging_anchor_get_result_tdma(sys_ranging_result_t *result)
{
  if (!result)
    return SYS_RANGING_ERR_PARAM;
  if (s_ctx.state != STATE_ANCHOR_COMPLETE || !s_ctx.has_result)
    return SYS_RANGING_ERR_NO_RESULT;

  memcpy(result, &s_ctx.result_single, sizeof(sys_ranging_result_t));
  state_machine_reset();
  return SYS_RANGING_OK;
}

void sys_ranging_abort(void)
{
  s_ranging_busy = false;
  state_machine_reset();
}

void sys_ranging_reset_stats(void)
{
  s_stats.total_count   = 0;
  s_stats.success_count = 0;
  s_stats.error_count   = 0;
}

uint32_t sys_ranging_get_ms_to_deadline(void)
{
  if (s_sys_ranging_ev.step == SYS_RANGING_EV_SYS_IDLE)
  {
    return 10;
  }

  uint64_t now_dw      = bsp_uwb_get_current_time_dw();
  uint64_t deadline_dw = s_sys_ranging_ev.deadline_dw;
  uint64_t remaining   = (deadline_dw - now_dw) & DW_MASK_40;

  if (remaining == 0ULL || remaining >= (1ULL << 39))
  {
    return 1; /* Past deadline or extremely close */
  }

  uint32_t remaining_us = tdma_dw_to_us(remaining);
  uint32_t remaining_ms = remaining_us / 1000U;

  if (remaining_ms == 0)
  {
    return 1;
  }

  return (remaining_ms > 10) ? 10 : remaining_ms;
}

bool sys_ranging_is_active(void)
{
  return (s_ctx.state != STATE_IDLE) ||
         (s_sys_ranging_ev.step != SYS_RANGING_EV_SYS_IDLE);
}
