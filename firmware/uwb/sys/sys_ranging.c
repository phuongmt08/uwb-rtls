/* ============================== sys_ranging.c ==============================
 * @file       sys_ranging.c
 * @author     Phuong Mai
 * @brief      DS-TWR + TDMA Multi-Anchor Ranging
 * @version    4.1.0
 * @date       2026-02-01
 */

/* Includes ----------------------------------------------------------- */
#include "sys_ranging.h"

#include "bsp_util.h"
#include "bsp_uwb.h"
#include "mw_tdma_scheduler.h"
#include "smf.h"
#include "sys_config.h"
#include "sys_logger.h"

#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

/* Common protocol constants ----------------------------------------- */
#define DWT_TIME_UNITS                       (1.0 / 499.2e6 / 128.0)
#define SPEED_OF_LIGHT                       299702547.0
#define DSTWR_MAX_INTERVAL_US                50000U

/* DS-TWR message types */
#define MW_DSTWR_MSG_TYPE_POLL               0xE1
#define MW_DSTWR_MSG_TYPE_RESP               0xE2
#define MW_DSTWR_MSG_TYPE_FINAL              0xE3
#define MW_DSTWR_MSG_TYPE_RESULT             0xE4 /* Anchor sends distance to TAG */

/* Anchor Smart-RX constants ----------------------------------------- */
#define ANCHOR_SMART_DISCOVERY_ON_MS         70U
#define ANCHOR_SMART_DISCOVERY_BALANCED_MS   120U
#define ANCHOR_SMART_DISCOVERY_ECO_MS        220U
#define ANCHOR_SMART_DISCOVERY_DEEP_ECO_MS   420U
#define ANCHOR_SMART_DISCOVERY_JITTER_MS     20U
#define ANCHOR_SMART_TRACK_PRE_POLL_MS       20U
#define ANCHOR_SMART_TRACK_LATE_MARGIN_MS    25U
#define ANCHOR_SMART_TRACK_MISS_PRE_STEP_MS  5U
#define ANCHOR_SMART_TRACK_MISS_LATE_STEP_MS 8U
#define ANCHOR_SMART_TRACK_MAX_PRE_POLL_MS   45U
#define ANCHOR_SMART_TRACK_MAX_MISSES        20U
#define ANCHOR_SMART_TRACK_MIN_WINDOW_MS     40U
#define ANCHOR_SMART_TRACK_MAX_WINDOW_MS     90U
#define ANCHOR_SMART_LEVEL_STABLE_SUCCESSES  10U
#define ANCHOR_SMART_DISCOVERY_DECAY_MISSES  25
#define ANCHOR_SMART_TRACK_REARM_GAP_MS      10U
#define ANCHOR_SMART_POLL_WATCHDOG_GUARD_MS  5U

// Smart sleep/standby parameters
#define ANCHOR_SMART_STANDBY_SLEEP_ENABLE    UWB_SLEEP_ENABLE
#define ANCHOR_SMART_SLEEP_MIN_GAP_MS        60U
#define ANCHOR_SMART_SLEEP_WAKE_GUARD_MS     40U
#define ANCHOR_SMART_SLEEP_RETRY_MS          20U

/* Tag constants ----------------------------------------------------- */
#define TAG_MIN_ANCHOR_SAMPLES               3U
#define TAG_RESP_TO_FINAL_HEADROOM_US        5000U

/* TDMA scheduling constants ---------------------------------------- */
/* Software margin needed before programming DW1000 delayed TX.
 * Keep this separate from TDMA slot guard:
 * slot guard protects adjacent slots, while this only decides whether it is still worth attempting delayed
 * TX. */
#define RANGING_TX_SCHEDULE_GUARD_US         600U

#define RX_WAIT_IMMEDIATE                    false
#define RX_WAIT_DELAYED                      true
#define RX_WAIT_NO_DELAYED_TS_DW             0ULL

/* Build-time feature switches --------------------------------------- */
// SYS_RANGING_DEBUG: Enable  detailed debug logs for ranging state machine and calculations
#define SYS_RANGING_DEBUG                    0

#if SYS_RANGING_DEBUG
#define RANGING_LOG_D(...) RLOG_D(__VA_ARGS__)
#else
#define RANGING_LOG_D(...) \
  do                       \
  {                        \
  } while (0)
#endif

/* Set to 0 to force immediate RX instead of delayed RX for debugging */
#define SYS_RANGING_USE_RX_DELAYED   1

/* Set to 1 when diagnosing delayed-TX slot jitter. Keep 0 in production to
 * avoid extra SPI reads and 64-bit math in the TDMA critical path. */
#define SYS_RANGING_VERIFY_TX_TIMING 0

#if SYS_RANGING_USE_RX_DELAYED
#define RANGING_ENABLE_RX_DELAYED(ts, timeout) bsp_uwb_enable_rx_delayed(ts, timeout)
#else
#define RANGING_ENABLE_RX_DELAYED(ts, timeout) ((void) (ts), (void) (timeout), bsp_uwb_enable_rx(0U))
#endif

/* 40-bit DW1000 timestamp mask and printf helpers.
 * DW_FMT  : format specifier for a 40-bit DW timestamp (use as string literal).
 * DW_ARG  : expands to the two (unsigned long) printf arguments for DW_FMT. */
#define DW_MASK_40 0x000000FFFFFFFFFFULL
#define DW_FMT     "0x%08lX%08lX"
#define DW_ARG(x)  (unsigned long) ((x) >> 32), (unsigned long) ((x) & 0xFFFFFFFFUL)

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
  uint8_t  padding[4];
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
  uint8_t  msg_type;
  uint8_t  sequence_num;
  uint8_t  anchor_id;
  uint8_t  slot_id;    /* TDMA slot ID - TAG uses this to detect slot mismatches */
  uint8_t  valid;      /* 1 = valid distance, 0 = error */
  float    distance_m; /* Calculated distance */
  uint16_t fp_amp_norm_q8;
  uint16_t fp_snr_q8;
  uint8_t  fp_confidence_q8;
  uint8_t  quality_flags;
} result_msg_t;

/* Legacy RESULT packets end immediately after fp_snr_q8. Keep this boundary
 * explicit so a new tag can range with anchors that have not yet received the
 * optional confidence extension. */
#define RESULT_MSG_LEGACY_SIZE ((uint16_t) offsetof(result_msg_t, fp_confidence_q8))

/* Compile-time wire-layout guards. */
typedef char result_msg_size_check_t[(sizeof(result_msg_t) == 15U) ? 1 : -1];
typedef char result_msg_legacy_size_check_t[(RESULT_MSG_LEGACY_SIZE == 13U) ? 1 : -1];

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

typedef struct
{
  /* Runtime timing/recovery data only. The current lifecycle state lives in SMF. */
  uint32_t next_window_tick;
  uint32_t next_poll_tick;
  uint32_t last_poll_tick;
  uint64_t next_poll_dw;
  uint32_t active_power_mode;
  uint8_t  track_misses;
  uint8_t  discovery_misses;
  uint8_t  stable_successes;
  bool     initialized;
} anchor_smart_runtime_t;

typedef struct
{
  bool     enabled;
  uint64_t rx_start_dw;
  uint32_t timeout_ms;
} anchor_poll_rx_plan_t;

typedef struct
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
} anchor_diag_t;

typedef struct
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
} tag_diag_t;

typedef struct
{
  uint32_t total_count;
  uint32_t success_count;
  uint32_t error_count;
} ranging_stats_t;

typedef struct
{
  uint32_t poll_wait_deadline_tick;
  uint64_t deadline_dw;

  uint64_t poll_tx_ts;  // T1
  uint64_t poll_rx_ts;  // T2
  uint64_t resp_tx_ts;  // T3
  uint64_t expected_final_dw;
  uint64_t planned_tx_dw;
  uint64_t predicted_tx_dw;

  uint8_t              num_responses;
  uint8_t              my_slot_id;
  bsp_uwb_rx_quality_t poll_quality;
  bool                 pending_final_valid;
  bsp_uwb_event_t      pending_final_evt;

  struct
  {
    uint8_t  anchor_id;
    uint64_t resp_rx_ts;
    uint64_t poll_rx_ts;
    uint64_t resp_tx_ts;
    bool     valid;
  } anchor_resp[8];
} dstwr_session_ctx_t;

typedef enum
{
  UWB_SESSION_PROTOCOL_NONE = 0,
  UWB_SESSION_PROTOCOL_DSTWR,
  UWB_SESSION_PROTOCOL_TDOA /* Reserved for the future TDoA state subtree. */
} uwb_session_protocol_t;

typedef enum
{
  UWB_SESSION_ROLE_NONE = 0,
  UWB_SESSION_ROLE_INITIATOR,
  UWB_SESSION_ROLE_RESPONDER,
  UWB_SESSION_ROLE_BEACON_TX,
  UWB_SESSION_ROLE_SYNC_ANCHOR,
  UWB_SESSION_ROLE_LISTENER,
  UWB_SESSION_ROLE_COORDINATOR
} uwb_session_role_t;

typedef struct
{
  /* Required by SMF_CTX(): the framework context must be first. */
  smf_ctx_t smf;

  uwb_session_protocol_t protocol;
  uwb_session_role_t     role;

  /* Inputs are borrowed only for the duration of smf_run_state(). */
  const uint8_t   *peer_ids;
  bsp_uwb_event_t *radio_event;
  uint32_t         timeout_ms;
  uint8_t          peer_count;
  bool             has_radio_event;
  bool             transaction_initialized;

  /*
   * Only the selected protocol context occupies RAM. A future TDoA context
   * belongs in this union
   * instead of being mixed with DS-TWR timestamps.
   */
  union
  {
    dstwr_session_ctx_t dstwr;
  } protocol_ctx;

  sys_ranging_err_t run_result;
} uwb_session_sm_t;

typedef enum
{
  UWB_SM_ACTIVE = 0,
  UWB_SM_DSTWR,
  UWB_SM_DSTWR_INITIATOR,
  UWB_SM_DSTWR_INITIATOR_TX_POLL,
  UWB_SM_DSTWR_INITIATOR_WAIT_POLL_TX,
  UWB_SM_DSTWR_INITIATOR_WAIT_RESP,
  UWB_SM_DSTWR_INITIATOR_WAIT_FINAL_TX,
  UWB_SM_DSTWR_INITIATOR_WAIT_RESULT,
  UWB_SM_DSTWR_RESPONDER,
  UWB_SM_DSTWR_RESPONDER_DISCOVERY,
  UWB_SM_DSTWR_RESPONDER_TRACKING,
  UWB_SM_DSTWR_RESPONDER_EXCHANGE,
  UWB_SM_DSTWR_RESPONDER_WAIT_RESP_TX,
  UWB_SM_DSTWR_RESPONDER_WAIT_FINAL,
  UWB_SM_DSTWR_RESPONDER_WAIT_RESULT_TX,
  UWB_SM_STATE_COUNT
} uwb_sm_state_id_t;

/* State handler declarations ---------------------------------------- */
static smf_state_result_t tag_tx_poll_run(void *obj);
static smf_state_result_t tag_wait_poll_tx_run(void *obj);
static smf_state_result_t tag_wait_resp_run(void *obj);
static smf_state_result_t tag_wait_final_tx_run(void *obj);
static smf_state_result_t tag_wait_result_run(void *obj);
static void               anchor_discovery_entry(void *obj);
static smf_state_result_t anchor_discovery_run(void *obj);
static void               anchor_tracking_entry(void *obj);
static smf_state_result_t anchor_tracking_run(void *obj);
static void               anchor_search_exit(void *obj);
static void               anchor_exchange_entry(void *obj);
static void               anchor_exchange_exit(void *obj);
static smf_state_result_t anchor_search_run(void *obj);
static smf_state_result_t anchor_wait_resp_tx_run(void *obj);
static smf_state_result_t anchor_wait_final_run(void *obj);
static smf_state_result_t anchor_wait_result_tx_run(void *obj);

#define UWB_REF(_state) SMF_REF(s_uwb_states, _state)

/* DISCOVERY and TRACKING acquire POLL. EXCHANGE handles the active transaction. */
/* clang-format off */
static const smf_state_t s_uwb_states[UWB_SM_STATE_COUNT] = {
  /* State ID                                    Entry                              Run                        Exit                  Parent state                              Initial child */
  [UWB_SM_ACTIVE]                              = SMF_STATE(NULL,                    NULL,                      NULL,                 NULL,                                     NULL),
  [UWB_SM_DSTWR]                               = SMF_STATE(NULL,                    NULL,                      NULL,                 UWB_REF(UWB_SM_ACTIVE),                   NULL),

  [UWB_SM_DSTWR_INITIATOR]                     = SMF_STATE(NULL,                    NULL,                      NULL,                 UWB_REF(UWB_SM_DSTWR),                    UWB_REF(UWB_SM_DSTWR_INITIATOR_TX_POLL)),
  [UWB_SM_DSTWR_INITIATOR_TX_POLL]             = SMF_STATE(NULL,                    tag_tx_poll_run,           NULL,                 UWB_REF(UWB_SM_DSTWR_INITIATOR),          NULL),
  [UWB_SM_DSTWR_INITIATOR_WAIT_POLL_TX]        = SMF_STATE(NULL,                    tag_wait_poll_tx_run,      NULL,                 UWB_REF(UWB_SM_DSTWR_INITIATOR),          NULL),
  [UWB_SM_DSTWR_INITIATOR_WAIT_RESP]           = SMF_STATE(NULL,                    tag_wait_resp_run,         NULL,                 UWB_REF(UWB_SM_DSTWR_INITIATOR),          NULL),
  [UWB_SM_DSTWR_INITIATOR_WAIT_FINAL_TX]       = SMF_STATE(NULL,                    tag_wait_final_tx_run,     NULL,                 UWB_REF(UWB_SM_DSTWR_INITIATOR),          NULL),
  [UWB_SM_DSTWR_INITIATOR_WAIT_RESULT]         = SMF_STATE(NULL,                    tag_wait_result_run,       NULL,                 UWB_REF(UWB_SM_DSTWR_INITIATOR),          NULL),

  [UWB_SM_DSTWR_RESPONDER]                     = SMF_STATE(NULL,                    NULL,                      NULL,                 UWB_REF(UWB_SM_DSTWR),                    UWB_REF(UWB_SM_DSTWR_RESPONDER_DISCOVERY)),
  [UWB_SM_DSTWR_RESPONDER_DISCOVERY]           = SMF_STATE(anchor_discovery_entry,  anchor_discovery_run,      anchor_search_exit,   UWB_REF(UWB_SM_DSTWR_RESPONDER),          NULL),
  [UWB_SM_DSTWR_RESPONDER_TRACKING]            = SMF_STATE(anchor_tracking_entry,   anchor_tracking_run,       anchor_search_exit,   UWB_REF(UWB_SM_DSTWR_RESPONDER),          NULL),
  [UWB_SM_DSTWR_RESPONDER_EXCHANGE]            = SMF_STATE(anchor_exchange_entry,   NULL,                      anchor_exchange_exit, UWB_REF(UWB_SM_DSTWR_RESPONDER),          NULL),
  [UWB_SM_DSTWR_RESPONDER_WAIT_RESP_TX]        = SMF_STATE(NULL,                    anchor_wait_resp_tx_run,   NULL,                 UWB_REF(UWB_SM_DSTWR_RESPONDER_EXCHANGE), NULL),
  [UWB_SM_DSTWR_RESPONDER_WAIT_FINAL]          = SMF_STATE(NULL,                    anchor_wait_final_run,     NULL,                 UWB_REF(UWB_SM_DSTWR_RESPONDER_EXCHANGE), NULL),
  [UWB_SM_DSTWR_RESPONDER_WAIT_RESULT_TX]      = SMF_STATE(NULL,                    anchor_wait_result_tx_run, NULL,                 UWB_REF(UWB_SM_DSTWR_RESPONDER_EXCHANGE), NULL),
};
/* clang-format on */

#undef UWB_REF

/* Private variables -------------------------------------------------- */
static ranging_ctx_t          s_ctx                           = { 0 };
static uwb_session_sm_t       s_uwb_session                   = { 0 };
static anchor_diag_t          s_anchor_diag                   = { 0 };
static tag_diag_t             s_tag_diag                      = { 0 };
static tdma_scheduler_t       s_tdma_tag                      = { 0 };
static tdma_scheduler_t       s_tdma_anchor                   = { 0 };
static ranging_stats_t        s_stats                         = { 0 };
static anchor_smart_runtime_t s_anchor_smart                  = { 0 };
static anchor_poll_rx_plan_t  s_anchor_poll_rx_plan           = { 0 };
static uint32_t               s_anchor_discovery_jitter_state = 0U;
static bool                   s_anchor_transaction_poll_seen  = false;

/* Context accessors -------------------------------------------------- */
static dstwr_session_ctx_t *dstwr_ctx(void)
{
  return &s_uwb_session.protocol_ctx.dstwr;
}

/* Private function declarations ------------------------------------- */
static void     anchor_smart_enter_discovery(uint32_t now_tick, bool log_transition);
static void     anchor_smart_update_tracking_phase(const sys_ranging_result_t *result, uint32_t now_tick);
static bool     anchor_smart_tick_due(uint32_t now, uint32_t due);
static uint32_t anchor_smart_discovery_interval_ms(uint32_t power_mode);
static uint32_t anchor_smart_tracking_window_ms(void);
static uint32_t anchor_smart_tracking_half_window_ms(void);

/* Anchor Smart-RX policy helpers ------------------------------------ */

static bool anchor_smart_state_is(uwb_sm_state_id_t state)
{
  return smf_get_current_leaf_state(SMF_CTX(&s_uwb_session)) == &s_uwb_states[state];
}

static bool anchor_smart_is_exchange(void)
{
  return anchor_smart_state_is(UWB_SM_DSTWR_RESPONDER_EXCHANGE)
         || anchor_smart_state_is(UWB_SM_DSTWR_RESPONDER_WAIT_RESP_TX)
         || anchor_smart_state_is(UWB_SM_DSTWR_RESPONDER_WAIT_FINAL)
         || anchor_smart_state_is(UWB_SM_DSTWR_RESPONDER_WAIT_RESULT_TX);
}

static bool anchor_smart_is_tracking(void)
{
  return anchor_smart_state_is(UWB_SM_DSTWR_RESPONDER_TRACKING) || anchor_smart_is_exchange();
}

static bool anchor_smart_is_discovery(void)
{
  return anchor_smart_state_is(UWB_SM_DSTWR_RESPONDER_DISCOVERY);
}

static void anchor_poll_rx_plan_reset(void)
{
  memset(&s_anchor_poll_rx_plan, 0, sizeof(s_anchor_poll_rx_plan));
}

static uint32_t anchor_smart_clamp_power_mode(uint32_t power_mode)
{
  if (power_mode > ANCHOR_POWER_MODE_DEEP_ECO)
  {
    return ANCHOR_POWER_MODE_BALANCED;
  }
  return power_mode;
}

#if SYS_RANGING_DEBUG
static const char *anchor_smart_power_mode_name(uint32_t power_mode)
{
  switch (anchor_smart_clamp_power_mode(power_mode))
  {
  case ANCHOR_POWER_MODE_PERFORMANCE: return "PERFORMANCE";
  case ANCHOR_POWER_MODE_BALANCED: return "BALANCED";
  case ANCHOR_POWER_MODE_ECO: return "ECO";
  case ANCHOR_POWER_MODE_DEEP_ECO: return "DEEP_ECO";
  default: return "BALANCED";
  }
}
#endif

static uint32_t anchor_smart_active_power_mode(uint32_t configured_mode)
{
  uint32_t target = anchor_smart_clamp_power_mode(configured_mode);
  uint32_t active = anchor_smart_clamp_power_mode(s_anchor_smart.active_power_mode);

  if (anchor_smart_is_tracking())
  {
    target = ANCHOR_POWER_MODE_PERFORMANCE;
  }

  if (!s_anchor_smart.initialized)
  {
    return target;
  }

  if (active > target)
  {
    active = target;
  }

  return active;
}

#if SYS_RANGING_DEBUG
static const char *anchor_smart_state_name(void)
{
  if (anchor_smart_is_discovery())
  {
    return "DISCOVERY";
  }
  if (anchor_smart_state_is(UWB_SM_DSTWR_RESPONDER_TRACKING))
  {
    return "TRACKING";
  }
  if (anchor_smart_is_exchange())
  {
    return "EXCHANGE";
  }
  return "INACTIVE";
}
#endif

static void anchor_smart_log_state(bool force)
{
#if SYS_RANGING_DEBUG
  static bool                    valid           = false;
  static const smf_state_t *last_state      = NULL;
  static uint32_t                last_power_mode = ANCHOR_POWER_MODE_BALANCED;
  const smf_state_t *current_state = smf_get_current_leaf_state(SMF_CTX(&s_uwb_session));
  uint32_t active_mode = anchor_smart_active_power_mode(sys_config_get()->uwb.power_mode);

  if (force || !valid || last_state != current_state || last_power_mode != active_mode)
  {
    RANGING_LOG_D(LOG_OBJECT_CODE_RANGING, "[ANCHOR] state=%s power=%s", anchor_smart_state_name(),
                  anchor_smart_power_mode_name(active_mode));
    last_state      = current_state;
    last_power_mode = active_mode;
    valid           = true;
  }
#else
  (void) force;
#endif
}

static void anchor_smart_set_active_level(uint32_t power_mode, bool log_transition)
{
  uint32_t level = anchor_smart_clamp_power_mode(power_mode);

  if (s_anchor_smart.active_power_mode != level)
  {
    s_anchor_smart.active_power_mode = level;
    if (log_transition)
      anchor_smart_log_state(false);
  }
}

/* Common protocol helpers ------------------------------------------- */
static inline bool dstwr_forward_interval_40(uint64_t later, uint64_t earlier, uint64_t *out_delta)
{
  const uint64_t MAX_INTERVAL_DW = tdma_us_to_dw(DSTWR_MAX_INTERVAL_US);
  uint64_t       delta           = (later - earlier) & DW_MASK_40;
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
  (void) seq;
  (void) anchor_id;
  (void) ts;
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
  float raw_dist       = tof_dw * (float) DWT_TIME_UNITS * (float) SPEED_OF_LIGHT;
  float bias           = bsp_uwb_get_range_bias(raw_dist);
  float corrected_dist = raw_dist - bias;
  if (corrected_dist < 0.0f)
  {
    corrected_dist = 0.0f;
  }
  return corrected_dist;
}

static inline bool validate_msg_type(const uint8_t *data, uint16_t len, uint8_t expected_type)
{
  if (!data || data[0] != expected_type)
  {
    return false;
  }

  uint16_t min_len = 0U;
  switch (expected_type)
  {
  case MW_DSTWR_MSG_TYPE_POLL: min_len = sizeof(poll_msg_t); break;
  case MW_DSTWR_MSG_TYPE_RESP: min_len = sizeof(resp_msg_t); break;
  case MW_DSTWR_MSG_TYPE_FINAL: min_len = sizeof(final_msg_t); break;
  case MW_DSTWR_MSG_TYPE_RESULT: min_len = RESULT_MSG_LEGACY_SIZE; break;
  default: return false;
  }
  return len >= min_len;
}

static void ranging_transaction_reset(void)
{
  /* Clear one DS-TWR exchange without discarding the anchor search policy. */
  s_ctx.state            = STATE_IDLE;
  s_ctx.state_entry_tick = 0U;
  s_ctx.anchor_id        = 0U;
  s_ctx.has_result       = false;
  memset(&s_ctx.result_multi, 0, sizeof(s_ctx.result_multi));
  memset(&s_ctx.result_single, 0, sizeof(s_ctx.result_single));
  s_uwb_session.transaction_initialized = false;
  bsp_uwb_clear_event();
  bsp_uwb_idle();
}

static void anchor_smart_reset_runtime(void)
{
  memset(&s_anchor_smart, 0, sizeof(s_anchor_smart));
  anchor_poll_rx_plan_reset();
  s_anchor_transaction_poll_seen = false;
}

static bool anchor_smart_wake_or_recover(uint32_t now_tick)
{
  if (!bsp_uwb_is_sleeping())
  {
    return true;
  }

  if (bsp_uwb_sleep_wake() == BSP_OK)
  {
    return true;
  }

  RLOG_W(LOG_OBJECT_CODE_RANGING, "[ANCHOR] DW1000 wake failed; reinitializing UWB");
  sys_config_t *cfg = sys_config_get();
  if (cfg == NULL || bsp_uwb_init() != BSP_OK || bsp_uwb_configure(&cfg->uwb) != BSP_OK)
  {
    s_anchor_smart.next_window_tick = now_tick + ANCHOR_SMART_SLEEP_RETRY_MS;
    return false;
  }

  anchor_smart_reset_runtime();
  anchor_smart_enter_discovery(now_tick, true);
  return true;
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
  if (a == 0U)
    return b;
  if (b == 0U)
    return a;
  return (a < b) ? a : b;
}

static inline bool dw_time_before_deadline(uint64_t now_dw, uint64_t deadline_dw);

/* Event ingestion and diagnostics ----------------------------------- */
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
         "[ANCHOR_DIAG] dt=%lums poll=%lu resp_tx=%lu final=%lu final_poll=%lu for_me=%lu no_final=%lu "
         "result_tx=%lu",
         (unsigned long) elapsed_ms, (unsigned long) s_anchor_diag.poll_rx,
         (unsigned long) s_anchor_diag.resp_tx_done, (unsigned long) s_anchor_diag.final_rx,
         (unsigned long) s_anchor_diag.final_poll_fallback, (unsigned long) s_anchor_diag.final_for_me,
         (unsigned long) s_anchor_diag.final_timeout, (unsigned long) s_anchor_diag.result_tx_done);

  RLOG_W(LOG_OBJECT_CODE_RANGING,
         "[ANCHOR_IRQ] irq_tx=%lu irq_rx=%lu irq_drop=%lu irq_extra=%lu rx_rearm_fail=%lu",
         (unsigned long) ev_stats.tx_done, (unsigned long) ev_stats.rx_ok,
         (unsigned long) ev_stats.queue_overflow, (unsigned long) ev_stats.irq_extra_pass,
         (unsigned long) ev_stats.rx_rearm_fail);

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
         "[TAG_RESP] dt=%lums poll_tx=%lu resp_f=%lu resp_p=%lu resp_n=%lu resp_pkt=%lu/%lu resp_poll=%lu "
         "resp_all=%lu resp_spin=%lu resp_rxerr=%lu resp_rej=%lu final_d=%lu final_fail=%lu final_miss=%lu",
         (unsigned long) elapsed_ms, (unsigned long) s_tag_diag.poll_tx, (unsigned long) s_tag_diag.resp_full,
         (unsigned long) s_tag_diag.resp_partial, (unsigned long) s_tag_diag.resp_none,
         (unsigned long) s_tag_diag.resp_packets, (unsigned long) s_tag_diag.resp_expected_packets,
         (unsigned long) s_tag_diag.resp_poll_fallback, (unsigned long) s_tag_diag.resp_all_configured,
         (unsigned long) s_tag_diag.resp_wait_spins, (unsigned long) s_tag_diag.resp_rx_errors,
         (unsigned long) s_tag_diag.resp_rejects, (unsigned long) s_tag_diag.final_tx_done,
         (unsigned long) s_tag_diag.final_tx_fail, (unsigned long) s_tag_diag.final_slot_missed);

  RLOG_W(LOG_OBJECT_CODE_RANGING,
         "[TAG_RESULT] dt=%lums result_f=%lu result_p=%lu result_pkt=%lu/%lu result_poll=%lu result_all=%lu "
         "result_spin=%lu result_rxerr=%lu result_rej=%lu",
         (unsigned long) elapsed_ms, (unsigned long) s_tag_diag.result_full,
         (unsigned long) s_tag_diag.result_partial, (unsigned long) s_tag_diag.result_packets,
         (unsigned long) s_tag_diag.result_expected_packets, (unsigned long) s_tag_diag.result_poll_fallback,
         (unsigned long) s_tag_diag.result_all_configured, (unsigned long) s_tag_diag.result_wait_spins,
         (unsigned long) s_tag_diag.result_rx_errors, (unsigned long) s_tag_diag.result_rejects);

  RLOG_W(
    LOG_OBJECT_CODE_RANGING, "[TAG_IRQ] irq_tx=%lu irq_rx=%lu irq_drop=%lu irq_extra=%lu rx_rearm_fail=%lu",
    (unsigned long) ev_stats.tx_done, (unsigned long) ev_stats.rx_ok, (unsigned long) ev_stats.queue_overflow,
    (unsigned long) ev_stats.irq_extra_pass, (unsigned long) ev_stats.rx_rearm_fail);

  uint32_t last_tick = now;
  memset(&s_tag_diag, 0, sizeof(s_tag_diag));
  s_tag_diag.last_tick = last_tick;
}

static uint8_t event_anchor_resp_mask(void)
{
  uint8_t mask = 0;
  for (uint8_t i = 0; i < 8; i++)
  {
    uint8_t anchor_id = dstwr_ctx()->anchor_resp[i].anchor_id;
    if (dstwr_ctx()->anchor_resp[i].valid && anchor_id > 0U && anchor_id <= 8U)
    {
      mask |= (uint8_t) (1U << (anchor_id - 1U));
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
      mask |= (uint8_t) (1U << (anchor_id - 1U));
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
      mask |= (uint8_t) (1U << (anchor_id - 1U));
    }
  }
  return mask;
}

static bool event_result_anchor_expected(uint8_t anchor_id)
{
  for (uint8_t i = 0; i < 8; i++)
  {
    if (dstwr_ctx()->anchor_resp[i].valid && dstwr_ctx()->anchor_resp[i].anchor_id == anchor_id)
    {
      return true;
    }
  }
  return false;
}

static bool event_tag_ingest_resp_payload(const uint8_t              *data,
                                          uint16_t                    len,
                                          uint64_t                    rx_ts,
                                          const bsp_uwb_rx_quality_t *quality,
                                          uint8_t                     num_anchors,
                                          const uint8_t              *anchor_ids)
{
  (void) quality;

  if (!validate_msg_type(data, len, MW_DSTWR_MSG_TYPE_RESP))
  {
    return false;
  }

  resp_msg_t *resp = (resp_msg_t *) data;
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

  if (idx < 0 || dstwr_ctx()->anchor_resp[idx].valid)
  {
    s_tag_diag.resp_rejects++;
    return false;
  }

  dstwr_ctx()->anchor_resp[idx].anchor_id  = resp->anchor_id;
  dstwr_ctx()->anchor_resp[idx].resp_rx_ts = rx_ts & DW_MASK_40;
  memcpy(&dstwr_ctx()->anchor_resp[idx].poll_rx_ts, &resp->poll_rx_ts, sizeof(uint64_t));
  memcpy(&dstwr_ctx()->anchor_resp[idx].resp_tx_ts, &resp->resp_tx_ts, sizeof(uint64_t));
  dstwr_ctx()->anchor_resp[idx].poll_rx_ts &= DW_MASK_40;
  dstwr_ctx()->anchor_resp[idx].resp_tx_ts &= DW_MASK_40;
  dstwr_ctx()->anchor_resp[idx].valid = true;
  dstwr_ctx()->num_responses++;
  RANGING_LOG_D(LOG_OBJECT_CODE_RANGING, "[TAG] Got RESP from anchor %u", resp->anchor_id);
  return true;
}

static bool
event_tag_ingest_resp_event(const bsp_uwb_event_t *evt, uint8_t num_anchors, const uint8_t *anchor_ids)
{
  if (!evt || evt->type != BSP_UWB_EVENT_RX_OK)
  {
    return false;
  }

  return event_tag_ingest_resp_payload(evt->rx_data, evt->rx_len, evt->rx_ts, &evt->rx_quality, num_anchors,
                                       anchor_ids);
}

static bool event_tag_ingest_result_payload(const uint8_t *data, uint16_t len)
{
  if (!validate_msg_type(data, len, MW_DSTWR_MSG_TYPE_RESULT))
  {
    return false;
  }

  const result_msg_t *res                   = (const result_msg_t *) data;
  const bool          has_quality_extension = (len >= sizeof(result_msg_t));
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

  bool expected_anchor = event_result_anchor_expected(res->anchor_id);
  if (!expected_anchor)
  {
    RLOG_W(LOG_OBJECT_CODE_RANGING, "[TAG] Unexpected RESULT anchor=%u seq=%u ignored", res->anchor_id,
           res->sequence_num);
  }

  if (!expected_anchor || duplicate || res->valid != 1U || s_ctx.result_multi.count >= 8U)
  {
    s_tag_diag.result_rejects++;
    return false;
  }

  sys_ranging_result_t *tr = &s_ctx.result_multi.results[s_ctx.result_multi.count];
  tr->anchor_id            = res->anchor_id;
  tr->distance_m           = res->distance_m;
  tr->fp_amp_norm_q8       = res->fp_amp_norm_q8;
  tr->fp_snr_q8            = res->fp_snr_q8;
  if (has_quality_extension)
  {
    tr->fp_confidence_q8 = res->fp_confidence_q8;
    tr->quality          = ((res->quality_flags & 0x01U) != 0U) ? 1U : 0U;
  }
  else
  {
    /* A legacy packet contains a valid range but no confidence contract.
     * Preserve the range and mark quality unknown so downstream weighting is
     * conservative instead of reading beyond the received payload. */
    tr->fp_confidence_q8 = 0U;
    tr->quality          = 0U;

    static bool legacy_result_reported = false;
    if (!legacy_result_reported)
    {
      legacy_result_reported = true;
      RLOG_W(LOG_OBJECT_CODE_RANGING, "[TAG] Legacy 13-byte RESULT accepted; confidence unavailable");
    }
  }
  tr->valid = (res->valid == 1);
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
    uint8_t   rx_buf[128] = { 0 };
    uint16_t  rx_len      = 0U;
    bsp_err_t err         = bsp_uwb_rx(rx_buf, sizeof(rx_buf), &rx_len);
    if (err != BSP_OK || rx_len == 0U)
    {
      s_tag_diag.resp_rx_errors++;
      continue;
    }

    uint64_t             rx_ts   = 0U;
    bsp_uwb_rx_quality_t quality = { 0 };
    (void) bsp_uwb_get_last_rx_timestamp(&rx_ts);
    (void) bsp_uwb_get_last_rx_quality(&quality);
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
    (void) event_tag_ingest_resp_event(&evt, num_anchors, anchor_ids);
  }
}

static void event_tag_collect_resps_until_deadline(uint8_t num_anchors, const uint8_t *anchor_ids)
{
  while (dstwr_ctx()->num_responses < num_anchors
         && dw_time_before_deadline(bsp_uwb_get_current_time_dw(), dstwr_ctx()->deadline_dw))
  {
    uint8_t before = dstwr_ctx()->num_responses;
    event_tag_drain_resp_events(num_anchors, anchor_ids);
    (void) event_tag_poll_ready_resp(num_anchors, anchor_ids);
    s_tag_diag.resp_wait_spins++;

    if (dstwr_ctx()->num_responses == before && !bsp_uwb_is_rx_ready())
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
    uint8_t   rx_buf[128] = { 0 };
    uint16_t  rx_len      = 0U;
    bsp_err_t err         = bsp_uwb_rx(rx_buf, sizeof(rx_buf), &rx_len);
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
    (void) event_tag_ingest_result_event(&evt);
  }
}

static void event_tag_collect_results_until_deadline(void)
{
  while (s_ctx.result_multi.count < dstwr_ctx()->num_responses
         && dw_time_before_deadline(bsp_uwb_get_current_time_dw(), dstwr_ctx()->deadline_dw))
  {
    uint8_t before = s_ctx.result_multi.count;
    event_tag_drain_result_events();
    (void) event_tag_poll_ready_result();
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
    uint8_t   rx_buf[128] = { 0 };
    uint16_t  rx_len      = 0U;
    bsp_err_t err         = bsp_uwb_rx(rx_buf, sizeof(rx_buf), &rx_len);
    if (err != BSP_OK || rx_len == 0U)
    {
      break;
    }

    if (!validate_msg_type(rx_buf, rx_len, MW_DSTWR_MSG_TYPE_FINAL))
    {
      continue;
    }

    final_msg_t *fmsg = (final_msg_t *) rx_buf;
    if (fmsg->sequence_num != s_ctx.sequence_num)
    {
      continue;
    }

    memset(out_evt, 0, sizeof(*out_evt));
    out_evt->type   = BSP_UWB_EVENT_RX_OK;
    out_evt->rx_len = rx_len;
    memcpy(out_evt->rx_data, rx_buf, rx_len);
    (void) bsp_uwb_get_last_rx_timestamp(&out_evt->rx_ts);
    (void) bsp_uwb_get_last_rx_quality(&out_evt->rx_quality);
    s_anchor_diag.final_poll_fallback++;
    return true;
  }

  return false;
}

static sys_ranging_err_t event_tag_complete_with_results(void)
{
  s_ctx.has_result                = true;
  s_ctx.state                     = STATE_TAG_COMPLETE;
  s_ctx.result_multi.sequence_num = s_ctx.sequence_num;
  return SYS_RANGING_OK;
}

/* TDMA timing helpers ------------------------------------------------ */
static uint64_t ensure_future_tx(uint64_t tx_time_dw, uint32_t schedule_guard_us)
{
  uint64_t now      = bsp_uwb_get_current_time_dw();
  uint64_t guard_dw = tdma_us_to_dw(schedule_guard_us);

  uint64_t ahead_dw = (tx_time_dw - now) & DW_MASK_40;
  if (ahead_dw == 0ULL || ahead_dw >= (1ULL << 39))
  {
    uint32_t behind_us = tdma_dw_to_us((now - tx_time_dw) & DW_MASK_40);
    RLOG_W(LOG_OBJECT_CODE_RANGING,
           "[TX] Slot already passed - aborting TX (tx=" DW_FMT " now=" DW_FMT " behind=%luus)",
           DW_ARG(tx_time_dw), DW_ARG(now), (unsigned long) behind_us);
    return 0ULL;
  }

  if (ahead_dw <= guard_dw)
  {
    uint32_t ahead_us = tdma_dw_to_us(ahead_dw);
    RLOG_W(LOG_OBJECT_CODE_RANGING,
           "[TX] Slot too close: ahead=%luus guard=%luus - aborting TX (tx=" DW_FMT " now=" DW_FMT ")",
           (unsigned long) ahead_us, (unsigned long) schedule_guard_us, DW_ARG(tx_time_dw), DW_ARG(now));
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

/* SMF session helpers ------------------------------------------------ */
static void uwb_session_start(uwb_session_protocol_t  protocol,
                              uwb_session_role_t      role,
                              const smf_state_t       *initial_state)
{
  s_uwb_session.protocol                = protocol;
  s_uwb_session.role                    = role;
  s_uwb_session.peer_ids                = NULL;
  s_uwb_session.radio_event             = NULL;
  s_uwb_session.timeout_ms              = 0U;
  s_uwb_session.peer_count              = 0U;
  s_uwb_session.has_radio_event         = false;
  s_uwb_session.transaction_initialized = false;
  s_uwb_session.run_result              = SYS_RANGING_ERR_BUSY;
  smf_set_initial(SMF_CTX(&s_uwb_session), initial_state);
}

static smf_state_result_t uwb_session_finish(uwb_session_sm_t *session, sys_ranging_err_t result)
{
  session->run_result = result;
  smf_set_terminate(SMF_CTX(session), 1);
  return SMF_EVENT_HANDLED;
}

static void uwb_session_transition(uwb_session_sm_t *session, uwb_sm_state_id_t destination)
{
  smf_set_state(SMF_CTX(session), &s_uwb_states[destination]);
}

static bool uwb_session_is_state(const uwb_session_sm_t *session, uwb_sm_state_id_t state)
{
  return smf_get_current_leaf_state(SMF_CTX(session)) == &s_uwb_states[state];
}

/* Ranging watchdog helper ------------------------------------------- */
static uint32_t tdma_cycle_watchdog_ms(uint8_t num_anchors, uint32_t configured_timeout_ms)
{
  uint32_t n                 = (num_anchors == 0U) ? 1U : (uint32_t) num_anchors;
  uint32_t effective_slot_us = TDMA_DEFAULT_SLOT_DURATION_US + TDMA_DEFAULT_GUARD_TIME_US;
  uint32_t active_us         = TDMA_DEFAULT_POLL_TO_RESP_DELAY_US + (n * effective_slot_us)
                               + TDMA_DEFAULT_RESP_TO_FINAL_DELAY_US + TDMA_DEFAULT_SLOT_DURATION_US
                               + TDMA_DEFAULT_FINAL_TO_RESULT_DELAY_US + (n * effective_slot_us)
                               + TDMA_DEFAULT_SLOT_DURATION_US;
  /* Preserve 10 ms for foreground/ISR scheduling jitter. Six anchors need
   * about 53.5 ms on air, so the minimum whole-cycle watchdog becomes 64 ms. */
  uint32_t required_ms = (active_us + 10000U + 999U) / 1000U;
  return (configured_timeout_ms < required_ms) ? required_ms : configured_timeout_ms;
}

/* Tag state handlers ------------------------------------------------ */
static smf_state_result_t tag_tx_poll_run(void *obj)
{
  uwb_session_sm_t *session = obj;

  if (!session->transaction_initialized)
  {
    memset(dstwr_ctx(), 0, sizeof(*dstwr_ctx()));
    if (!tdma_tag_config_matches(session->peer_count, session->peer_ids))
    {
      tdma_init(&s_tdma_tag, TDMA_ROLE_TAG, 0, session->peer_count, session->peer_ids);
    }
    session->transaction_initialized = true;
  }

  poll_msg_t poll_msg   = { 0 };
  poll_msg.msg_type     = MW_DSTWR_MSG_TYPE_POLL;
  poll_msg.sequence_num = s_ctx.sequence_num;
  poll_msg.tag_id       = 0;
  poll_msg.num_anchors  = session->peer_count;
  for (uint8_t i = 0; i < session->peer_count; i++)
  {
    if (session->peer_ids[i] > 0U && session->peer_ids[i] <= 8U)
    {
      poll_msg.anchor_mask |= (uint8_t) (1U << (session->peer_ids[i] - 1U));
    }
  }

  if (bsp_uwb_tx(&poll_msg, sizeof(poll_msg)) != BSP_OK)
  {
    ranging_transaction_reset();
    return uwb_session_finish(session, SYS_RANGING_ERR);
  }

  s_tag_diag.poll_tx++;
  uwb_session_transition(session, UWB_SM_DSTWR_INITIATOR_WAIT_POLL_TX);
  return SMF_EVENT_HANDLED;
}

static smf_state_result_t tag_wait_poll_tx_run(void *obj)
{
  uwb_session_sm_t *session      = obj;
  bsp_uwb_event_t  *evt          = session->radio_event;
  bool              poll_tx_done = false;

  if (session->has_radio_event && evt->type == BSP_UWB_EVENT_TX_DONE)
  {
    dstwr_ctx()->poll_tx_ts = evt->tx_ts & DW_MASK_40;
    poll_tx_done            = true;
  }
  else if (session->has_radio_event)
  {
    uint64_t last_tx = 0;
    if (bsp_uwb_get_last_tx_timestamp(&last_tx) == BSP_OK)
    {
      dstwr_ctx()->poll_tx_ts = last_tx & DW_MASK_40;
      poll_tx_done            = true;
      RLOG_W(LOG_OBJECT_CODE_RANGING, "[TAG] POLL TX_DONE overwritten - recovered via cached ts");
    }
  }

  if (!poll_tx_done)
  {
    return SMF_EVENT_HANDLED;
  }

  s_tag_diag.poll_tx_done++;
  tdma_start_superframe(&s_tdma_tag, dstwr_ctx()->poll_tx_ts);
  dstwr_ctx()->deadline_dw = tdma_compute_resp_rx_window_end(
    &s_tdma_tag, session->peer_ids, session->peer_count, bsp_uwb_get_current_time_dw());

  uint64_t final_tx_planned_dw = 0;
  if (tdma_calculate_final_time(&s_tdma_tag, session->peer_count, &final_tx_planned_dw) == TDMA_OK)
  {
    uint64_t final_tx_headroom_dw =
      (final_tx_planned_dw - tdma_us_to_dw(TAG_RESP_TO_FINAL_HEADROOM_US)) & DW_MASK_40;
    if (dw_time_before_deadline(final_tx_headroom_dw, dstwr_ctx()->deadline_dw))
    {
      dstwr_ctx()->deadline_dw = final_tx_headroom_dw;
    }
  }

  /*
   * RX was armed by uwb_tx_cb immediately after POLL TX. Re-enabling it here
   * would force TRX
   * off and could abort an in-progress RESP.
   */
  uwb_session_transition(session, UWB_SM_DSTWR_INITIATOR_WAIT_RESP);
  if (session->has_radio_event && evt->type == BSP_UWB_EVENT_RX_OK)
  {
    (void) event_tag_ingest_resp_event(evt, session->peer_count, session->peer_ids);
  }
  return SMF_EVENT_HANDLED;
}

static smf_state_result_t tag_wait_resp_run(void *obj)
{
  uwb_session_sm_t *session = obj;

  if (session->has_radio_event)
  {
    (void) event_tag_ingest_resp_event(session->radio_event, session->peer_count, session->peer_ids);
  }
  event_tag_collect_resps_until_deadline(session->peer_count, session->peer_ids);

  if (dw_time_before_deadline(bsp_uwb_get_current_time_dw(), dstwr_ctx()->deadline_dw)
      && dstwr_ctx()->num_responses < session->peer_count)
  {
    return SMF_EVENT_HANDLED;
  }

  uint8_t resp_mask       = event_anchor_resp_mask();
  uint8_t configured_mask = event_configured_anchor_mask(session->peer_count, session->peer_ids);
  s_tag_diag.resp_packets += dstwr_ctx()->num_responses;
  s_tag_diag.resp_expected_packets += session->peer_count;
  if (resp_mask == configured_mask)
  {
    s_tag_diag.resp_all_configured++;
  }

  if (SYS_RANGING_REQUIRE_MIN_ANCHOR_SAMPLES && dstwr_ctx()->num_responses < TAG_MIN_ANCHOR_SAMPLES)
  {
    if (dstwr_ctx()->num_responses == 0U)
    {
      s_tag_diag.resp_none++;
    }
    else
    {
      s_tag_diag.resp_partial++;
    }
    s_ctx.result_multi.count = 0;
    RLOG_W(LOG_OBJECT_CODE_RANGING,
           "[TAG] RESP insufficient seq=%u resp=%u/%u min=%u resp_mask=0x%02X - abort before FINAL",
           s_ctx.sequence_num, dstwr_ctx()->num_responses, session->peer_count, TAG_MIN_ANCHOR_SAMPLES,
           resp_mask);
    sys_ranging_abort();
    return uwb_session_finish(session, SYS_RANGING_ERR_PARTIAL);
  }

  if (dstwr_ctx()->num_responses < session->peer_count)
  {
    s_tag_diag.resp_partial++;
  }
  else
  {
    s_tag_diag.resp_full++;
  }

  uint64_t final_tx_time_dw = 0;
  tdma_calculate_final_time(&s_tdma_tag, session->peer_count, &final_tx_time_dw);
  final_tx_time_dw = ensure_future_tx(final_tx_time_dw, RANGING_TX_SCHEDULE_GUARD_US);
  if (final_tx_time_dw == 0ULL)
  {
    s_tag_diag.final_slot_missed++;
    s_ctx.result_multi.count = 0;
    RLOG_W(LOG_OBJECT_CODE_RANGING, "[TAG] FINAL slot missed (seq=%u resp=%u/%u resp_mask=0x%02X)",
           s_ctx.sequence_num, dstwr_ctx()->num_responses, session->peer_count, resp_mask);
    return uwb_session_finish(session, event_tag_complete_with_results());
  }

  uint64_t     t5_payload     = predict_delayed_tx_antenna_time(final_tx_time_dw);
  uint8_t      final_buf[256] = { 0 };
  final_msg_t *fmsg           = (final_msg_t *) final_buf;
  fmsg->msg_type              = MW_DSTWR_MSG_TYPE_FINAL;
  fmsg->sequence_num          = s_ctx.sequence_num;
  fmsg->num_responses         = dstwr_ctx()->num_responses;
  fmsg->anchor_resp_mask      = resp_mask;
  uint64_t ptx_pay            = dstwr_ctx()->poll_tx_ts & DW_MASK_40;
  memcpy(&fmsg->poll_tx_ts, &ptx_pay, sizeof(ptx_pay));

  uint8_t fidx = 0;
  for (uint8_t i = 0; i < 8U; i++)
  {
    if (dstwr_ctx()->anchor_resp[i].valid)
    {
      uint8_t *entry   = final_buf + sizeof(final_msg_t) + (fidx * sizeof(final_anchor_data_t));
      uint64_t rrx_pay = dstwr_ctx()->anchor_resp[i].resp_rx_ts & DW_MASK_40;
      uint64_t ftx_pay = t5_payload & DW_MASK_40;
      entry[0]         = dstwr_ctx()->anchor_resp[i].anchor_id;
      memcpy(entry + 1, &rrx_pay, sizeof(rrx_pay));
      memcpy(entry + 1 + sizeof(uint64_t), &ftx_pay, sizeof(ftx_pay));
      fidx++;
    }
  }

  uint16_t flen = sizeof(final_msg_t) + (dstwr_ctx()->num_responses * sizeof(final_anchor_data_t));
  if (bsp_uwb_tx_delayed(final_buf, flen, final_tx_time_dw) != BSP_OK)
  {
    s_tag_diag.final_tx_fail++;
    s_ctx.result_multi.count = 0;
    RLOG_W(LOG_OBJECT_CODE_RANGING, "[TAG] FINAL TX failed (seq=%u resp=%u/%u resp_mask=0x%02X)",
           s_ctx.sequence_num, dstwr_ctx()->num_responses, session->peer_count, resp_mask);
    return uwb_session_finish(session, event_tag_complete_with_results());
  }

  uwb_session_transition(session, UWB_SM_DSTWR_INITIATOR_WAIT_FINAL_TX);
  return SMF_EVENT_HANDLED;
}

static smf_state_result_t tag_wait_final_tx_run(void *obj)
{
  uwb_session_sm_t *session       = obj;
  bsp_uwb_event_t  *evt           = session->radio_event;
  uint64_t          final_tx_ts   = 0;
  bool              final_tx_done = false;

  if (session->has_radio_event && evt->type == BSP_UWB_EVENT_TX_DONE)
  {
    final_tx_ts   = evt->tx_ts;
    final_tx_done = true;
  }
  else if (session->has_radio_event)
  {
    uint64_t last_tx = 0;
    if (bsp_uwb_get_last_tx_timestamp(&last_tx) == BSP_OK)
    {
      uint64_t expected_final = 0;
      tdma_calculate_final_time(&s_tdma_tag, session->peer_count, &expected_final);
      uint64_t diff = (last_tx - expected_final) & DW_MASK_40;
      if (diff < tdma_us_to_dw(10000U))
      {
        final_tx_ts   = last_tx;
        final_tx_done = true;
        RLOG_W(LOG_OBJECT_CODE_RANGING, "[TAG] FINAL TX_DONE overwritten by RX - recovered via cached ts");
      }
    }
  }

  if (!final_tx_done)
  {
    return SMF_EVENT_HANDLED;
  }

  s_tag_diag.final_tx_done++;
  s_ctx.result_multi.count        = 0;
  s_ctx.result_multi.sequence_num = s_ctx.sequence_num;
  uint8_t max_slot                = 1;
  for (uint8_t i = 0; i < 8U; i++)
  {
    if (dstwr_ctx()->anchor_resp[i].valid)
    {
      tdma_slot_t slot = { 0 };
      if (tdma_get_slot_for_anchor(&s_tdma_tag, dstwr_ctx()->anchor_resp[i].anchor_id, &slot) == TDMA_OK
          && slot.slot_id > max_slot)
      {
        max_slot = slot.slot_id;
      }
    }
  }
  dstwr_ctx()->deadline_dw = tdma_compute_result_rx_window_end(&s_tdma_tag, final_tx_ts, max_slot);

  if (session->has_radio_event)
  {
    (void) event_tag_ingest_result_event(evt);
  }
  if (s_ctx.result_multi.count < dstwr_ctx()->num_responses)
  {
    (void) event_tag_poll_ready_result();
  }

  uwb_session_transition(session, UWB_SM_DSTWR_INITIATOR_WAIT_RESULT);
  event_tag_collect_results_until_deadline();
  return SMF_EVENT_HANDLED;
}

static smf_state_result_t tag_wait_result_run(void *obj)
{
  uwb_session_sm_t *session = obj;

  if (session->has_radio_event)
  {
    (void) event_tag_ingest_result_event(session->radio_event);
  }
  event_tag_collect_results_until_deadline();

  if (dw_time_before_deadline(bsp_uwb_get_current_time_dw(), dstwr_ctx()->deadline_dw)
      && s_ctx.result_multi.count < dstwr_ctx()->num_responses)
  {
    return SMF_EVENT_HANDLED;
  }

  uint8_t resp_mask       = event_anchor_resp_mask();
  uint8_t result_mask     = event_result_mask();
  uint8_t configured_mask = event_configured_anchor_mask(session->peer_count, session->peer_ids);
  s_tag_diag.result_packets += s_ctx.result_multi.count;
  s_tag_diag.result_expected_packets += dstwr_ctx()->num_responses;
  if (result_mask == configured_mask)
  {
    s_tag_diag.result_all_configured++;
  }
  for (uint8_t i = 0; i < s_ctx.result_multi.count; i++)
  {
    log_ranging_result(&s_ctx.result_multi.results[i], "TAG");
  }

  if (SYS_RANGING_REQUIRE_MIN_ANCHOR_SAMPLES && s_ctx.result_multi.count < TAG_MIN_ANCHOR_SAMPLES)
  {
    s_tag_diag.result_partial++;
    RLOG_W(LOG_OBJECT_CODE_RANGING,
           "[TAG] RESULT insufficient seq=%u got=%u/%u resp_mask=0x%02X result_mask=0x%02X - abort cycle",
           s_ctx.sequence_num, s_ctx.result_multi.count, TAG_MIN_ANCHOR_SAMPLES, resp_mask, result_mask);
    sys_ranging_abort();
    return uwb_session_finish(session, SYS_RANGING_ERR_PARTIAL);
  }

  if (s_ctx.result_multi.count < dstwr_ctx()->num_responses || result_mask != resp_mask)
  {
    s_tag_diag.result_partial++;
    RLOG_W(LOG_OBJECT_CODE_RANGING,
           "[TAG] RESULT partial seq=%u result=%u/%u resp_mask=0x%02X result_mask=0x%02X", s_ctx.sequence_num,
           s_ctx.result_multi.count, dstwr_ctx()->num_responses, resp_mask, result_mask);
  }
  else
  {
    s_tag_diag.result_full++;
  }
  RANGING_LOG_D(LOG_OBJECT_CODE_RANGING, "[TAG] Received %u RESULT messages", s_ctx.result_multi.count);
  return uwb_session_finish(session, event_tag_complete_with_results());
}

/* Anchor state handlers --------------------------------------------- */

static smf_state_result_t anchor_search_complete(uwb_session_sm_t *session, sys_ranging_err_t result)
{
  session->run_result = result;
  return SMF_EVENT_HANDLED;
}

static smf_state_result_t anchor_exchange_complete(uwb_session_sm_t *session, sys_ranging_err_t result)
{
  /* A valid POLL keeps tracking phase even if a later exchange step fails. */
  uwb_session_transition(session, UWB_SM_DSTWR_RESPONDER_TRACKING);
  session->run_result = result;
  return SMF_EVENT_HANDLED;
}

/* DISCOVERY listens until a valid POLL establishes timing. */
static void anchor_discovery_entry(void *obj)
{
  (void) obj;
  anchor_smart_log_state(false);
}

static smf_state_result_t anchor_discovery_run(void *obj)
{
  return anchor_search_run(obj);
}

/* TRACKING: POLL phase is known; receive around the predicted POLL time. */
static void anchor_tracking_entry(void *obj)
{
  (void) obj;
  anchor_smart_log_state(false);
}

static smf_state_result_t anchor_tracking_run(void *obj)
{
  return anchor_search_run(obj);
}

static void anchor_search_exit(void *obj)
{
  (void) obj;
  anchor_poll_rx_plan_reset();
}

/* EXCHANGE owns RESP, FINAL and RESULT after a valid POLL was received. */
static void anchor_exchange_entry(void *obj)
{
  (void) obj;
  anchor_smart_log_state(false);
}

static void anchor_exchange_exit(void *obj)
{
  (void) obj;
  dstwr_ctx()->pending_final_valid = false;
}

static smf_state_result_t anchor_search_run(void *obj)
{
  uwb_session_sm_t *session                = obj;
  bsp_uwb_event_t  *evt                    = session->radio_event;
  bool              has_evt                = session->has_radio_event;
  bool              poll_received          = false;
  bool              window_timeout         = false;
  bool              windowed_frame_ignored = false;

  do
  {
    if (has_evt && evt->type == BSP_UWB_EVENT_RX_OK
        && validate_msg_type(evt->rx_data, evt->rx_len, MW_DSTWR_MSG_TYPE_POLL))
    {
      poll_received = true;
      break;
    }
    if (has_evt && evt->type == BSP_UWB_EVENT_RX_OK && evt->rx_windowed)
    {
      windowed_frame_ignored = true;
    }
    if (has_evt && evt->type == BSP_UWB_EVENT_RX_TIMEOUT)
    {
      window_timeout = true;
      break;
    }
  } while ((has_evt = bsp_uwb_get_event(evt)) != false);

  if (poll_received)
  {
    s_anchor_diag.poll_rx++;
    s_anchor_transaction_poll_seen = true;
    s_anchor_smart.last_poll_tick  = HAL_GetTick();
    poll_msg_t *poll               = (poll_msg_t *) evt->rx_data;
    s_ctx.sequence_num             = poll->sequence_num;
    dstwr_ctx()->poll_rx_ts        = evt->rx_ts;
    dstwr_ctx()->poll_quality      = evt->rx_quality;

    uwb_session_transition(session, UWB_SM_DSTWR_RESPONDER_EXCHANGE);
    sys_ranging_result_t poll_sync = { 0 };
    poll_sync.t2                   = evt->rx_ts;
    anchor_smart_update_tracking_phase(&poll_sync, HAL_GetTick());
    tdma_sync_to_poll(&s_tdma_anchor, dstwr_ctx()->poll_rx_ts);

    uint64_t rtx_dw = 0;
    tdma_calculate_response_time(&s_tdma_anchor, s_ctx.anchor_id, &rtx_dw);
    rtx_dw = ensure_future_tx(rtx_dw, RANGING_TX_SCHEDULE_GUARD_US);
    if (rtx_dw == 0ULL)
    {
      return anchor_exchange_complete(session, SYS_RANGING_ERR);
    }
    dstwr_ctx()->predicted_tx_dw = predict_delayed_tx_antenna_time(rtx_dw);
    dstwr_ctx()->resp_tx_ts      = dstwr_ctx()->predicted_tx_dw;

    resp_msg_t rmsg   = { 0 };
    rmsg.msg_type     = MW_DSTWR_MSG_TYPE_RESP;
    rmsg.sequence_num = s_ctx.sequence_num;
    rmsg.anchor_id    = s_ctx.anchor_id;
    rmsg.slot_id      = dstwr_ctx()->my_slot_id;
    uint64_t p_rx     = dstwr_ctx()->poll_rx_ts & DW_MASK_40;
    uint64_t r_tx     = dstwr_ctx()->resp_tx_ts & DW_MASK_40;
    memcpy(&rmsg.poll_rx_ts, &p_rx, sizeof(p_rx));
    memcpy(&rmsg.resp_tx_ts, &r_tx, sizeof(r_tx));

    dstwr_ctx()->planned_tx_dw = rtx_dw;
    if (bsp_uwb_tx_delayed(&rmsg, sizeof(rmsg), rtx_dw) != BSP_OK)
    {
      return anchor_exchange_complete(session, SYS_RANGING_ERR);
    }
    s_ctx.state_entry_tick = HAL_GetTick();
    uwb_session_transition(session, UWB_SM_DSTWR_RESPONDER_WAIT_RESP_TX);
    return SMF_EVENT_HANDLED;
  }

  if (window_timeout)
  {
    return anchor_search_complete(session, SYS_RANGING_ERR_TIMEOUT);
  }

  if (windowed_frame_ignored)
  {
    if (bsp_uwb_enable_rx(session->timeout_ms) != BSP_OK)
    {
      return anchor_search_complete(session, SYS_RANGING_ERR);
    }
  }
  else if (dstwr_ctx()->poll_wait_deadline_tick != 0U
           && anchor_smart_tick_due(HAL_GetTick(), dstwr_ctx()->poll_wait_deadline_tick))
  {
    return anchor_search_complete(session, SYS_RANGING_ERR_TIMEOUT);
  }

  return SMF_EVENT_HANDLED;
}

static smf_state_result_t anchor_wait_resp_tx_run(void *obj)
{
  uwb_session_sm_t *session = obj;
  bsp_uwb_event_t  *evt     = session->radio_event;
  bool              has_evt = session->has_radio_event;
  bool              tx_done = false;

  do
  {
    if (has_evt && evt->type == BSP_UWB_EVENT_TX_DONE)
    {
      dstwr_ctx()->resp_tx_ts = evt->tx_ts & DW_MASK_40;
#if SYS_RANGING_VERIFY_TX_TIMING
      verify_tx_timing("RESP", s_ctx.anchor_id, dstwr_ctx()->my_slot_id, s_ctx.sequence_num,
                       dstwr_ctx()->planned_tx_dw, dstwr_ctx()->predicted_tx_dw, evt->tx_ts, true);
#endif
      tx_done = true;
      break;
    }
    if (has_evt && evt->type == BSP_UWB_EVENT_RX_OK
        && validate_msg_type(evt->rx_data, evt->rx_len, MW_DSTWR_MSG_TYPE_FINAL))
    {
      final_msg_t *fmsg = (final_msg_t *) evt->rx_data;
      if (fmsg->sequence_num == s_ctx.sequence_num)
      {
        dstwr_ctx()->pending_final_evt   = *evt;
        dstwr_ctx()->pending_final_valid = true;
        s_anchor_diag.final_rx_early++;
      }
    }
  } while ((has_evt = bsp_uwb_get_event(evt)) != false);

  if (!tx_done)
  {
    uint64_t last_tx = 0;
    if (bsp_uwb_get_last_tx_timestamp(&last_tx) == BSP_OK)
    {
      uint64_t diff = (last_tx - dstwr_ctx()->planned_tx_dw) & DW_MASK_40;
      if (diff < tdma_us_to_dw(5000U))
      {
        dstwr_ctx()->resp_tx_ts = last_tx & DW_MASK_40;
        tx_done                 = true;
      }
    }
  }

  if (!tx_done)
  {
    return SMF_EVENT_HANDLED;
  }

  s_anchor_diag.resp_tx_done++;
  s_ctx.state_entry_tick     = HAL_GetTick();
  uint64_t expected_final_dw = 0;
  tdma_calculate_final_time(&s_tdma_anchor, session->peer_count, &expected_final_dw);
  uint64_t rx_start_dw      = (expected_final_dw - tdma_us_to_dw(1000U)) & DW_MASK_40;
  uint32_t final_timeout_us = tdma_compute_final_wait_timeout_us(&s_tdma_anchor, session->peer_count);
  dstwr_ctx()->deadline_dw  = (rx_start_dw + tdma_us_to_dw(final_timeout_us)) & DW_MASK_40;

  /*
   * TX callback already re-arms RX. Do not force TRX off here: FINAL may
   * already be on air
   * while the foreground processes TX_DONE.
   */
  uwb_session_transition(session, UWB_SM_DSTWR_RESPONDER_WAIT_FINAL);
  return SMF_EVENT_HANDLED;
}

static smf_state_result_t anchor_wait_final_run(void *obj)
{
  uwb_session_sm_t *session        = obj;
  bsp_uwb_event_t  *evt            = session->radio_event;
  bool              has_evt        = session->has_radio_event;
  bool              final_received = false;
  bsp_uwb_event_t   final_evt      = { 0 };

  if (dstwr_ctx()->pending_final_valid)
  {
    final_evt                        = dstwr_ctx()->pending_final_evt;
    dstwr_ctx()->pending_final_valid = false;
    final_received                   = true;
  }
  else
  {
    do
    {
      if (has_evt && evt->type == BSP_UWB_EVENT_RX_OK
          && validate_msg_type(evt->rx_data, evt->rx_len, MW_DSTWR_MSG_TYPE_FINAL))
      {
        final_evt      = *evt;
        final_received = true;
        break;
      }
    } while ((has_evt = bsp_uwb_get_event(evt)) != false);
  }

  if (!final_received && event_anchor_poll_ready_final(&final_evt))
  {
    final_received = true;
  }

  if (!final_received)
  {
    if (!dw_time_before_deadline(bsp_uwb_get_current_time_dw(), dstwr_ctx()->deadline_dw))
    {
      s_anchor_diag.final_timeout++;
      return anchor_exchange_complete(session, SYS_RANGING_ERR_TIMEOUT);
    }
    return SMF_EVENT_HANDLED;
  }

  s_anchor_diag.final_rx++;
  final_msg_t *fmsg = (final_msg_t *) final_evt.rx_data;
  if (fmsg->sequence_num != s_ctx.sequence_num)
  {
    return SMF_EVENT_HANDLED;
  }

  uint64_t ptx_tag = 0;
  memcpy(&ptx_tag, &fmsg->poll_tx_ts, sizeof(ptx_tag));
  ptx_tag &= DW_MASK_40;
  uint64_t rrx_tag = 0;
  uint64_t ftx_tag = 0;
  bool     found   = false;
  uint8_t  n_resp  = fmsg->num_responses;
  uint8_t  max_fit = 0U;
  if (final_evt.rx_len > sizeof(final_msg_t))
  {
    max_fit = (uint8_t) ((final_evt.rx_len - sizeof(final_msg_t)) / sizeof(final_anchor_data_t));
  }
  if (n_resp > max_fit)
    n_resp = max_fit;
  if (n_resp > MAX_ANCHORS_SUPPORTED)
    n_resp = MAX_ANCHORS_SUPPORTED;

  for (uint8_t i = 0; i < n_resp; i++)
  {
    uint8_t *entry = final_evt.rx_data + sizeof(final_msg_t) + (i * sizeof(final_anchor_data_t));
    if (entry[0] == s_ctx.anchor_id)
    {
      memcpy(&rrx_tag, entry + 1, sizeof(rrx_tag));
      memcpy(&ftx_tag, entry + 1 + sizeof(uint64_t), sizeof(ftx_tag));
      found = true;
      break;
    }
  }

  if (!found)
  {
    s_anchor_diag.final_not_for_me++;
    return SMF_EVENT_HANDLED;
  }

  s_anchor_diag.final_for_me++;
  dstwr_timestamps_t ts;
  ts.t1 = ptx_tag;
  ts.t2 = dstwr_ctx()->poll_rx_ts;
  ts.t3 = dstwr_ctx()->resp_tx_ts;
  ts.t4 = rrx_tag & DW_MASK_40;
  ts.t5 = ftx_tag & DW_MASK_40;
  ts.t6 = final_evt.rx_ts;

  float dist                     = calculate_distance(&ts);
  s_ctx.result_single.distance_m = dist;
  s_ctx.result_single.anchor_id  = s_ctx.anchor_id;
  s_ctx.result_single.fp_amp_norm_q8 =
    min_nonzero_u16(dstwr_ctx()->poll_quality.fp_amp_norm_q8, final_evt.rx_quality.fp_amp_norm_q8);
  s_ctx.result_single.fp_snr_q8 =
    min_nonzero_u16(dstwr_ctx()->poll_quality.fp_snr_q8, final_evt.rx_quality.fp_snr_q8);
  if (dstwr_ctx()->poll_quality.confidence_valid && final_evt.rx_quality.confidence_valid)
  {
    s_ctx.result_single.fp_confidence_q8 =
      (dstwr_ctx()->poll_quality.fp_confidence_q8 < final_evt.rx_quality.fp_confidence_q8)
        ? dstwr_ctx()->poll_quality.fp_confidence_q8
        : final_evt.rx_quality.fp_confidence_q8;
  }
  else
  {
    s_ctx.result_single.fp_confidence_q8 = 0U;
  }
  s_ctx.result_single.quality =
    (dstwr_ctx()->poll_quality.valid && final_evt.rx_quality.valid
     && dstwr_ctx()->poll_quality.confidence_valid && final_evt.rx_quality.confidence_valid)
      ? 1U
      : 0U;
  s_ctx.result_single.valid = (dist > 0.0f && dist < 100.0f);

  uint64_t expected_final_tx_dw = 0;
  if (tdma_calculate_final_time(&s_tdma_anchor, session->peer_count, &expected_final_tx_dw) != TDMA_OK)
  {
    expected_final_tx_dw = final_evt.rx_ts;
  }
  uint32_t rofs      = s_tdma_anchor.schedule.final_to_result_delay_us
                       + (dstwr_ctx()->my_slot_id * tdma_effective_slot_us(&s_tdma_anchor));
  uint64_t res_tx_dw = (expected_final_tx_dw + tdma_us_to_dw(rofs)) & DW_MASK_40;
  res_tx_dw          = ensure_future_tx(res_tx_dw, RANGING_TX_SCHEDULE_GUARD_US);
  if (res_tx_dw == 0ULL)
  {
    s_anchor_diag.result_slot_missed++;
    return anchor_exchange_complete(session, SYS_RANGING_ERR);
  }

  result_msg_t res     = { 0 };
  res.msg_type         = MW_DSTWR_MSG_TYPE_RESULT;
  res.sequence_num     = s_ctx.sequence_num;
  res.anchor_id        = s_ctx.anchor_id;
  res.slot_id          = dstwr_ctx()->my_slot_id;
  res.valid            = s_ctx.result_single.valid ? 1U : 0U;
  res.distance_m       = s_ctx.result_single.distance_m;
  res.fp_amp_norm_q8   = s_ctx.result_single.fp_amp_norm_q8;
  res.fp_snr_q8        = s_ctx.result_single.fp_snr_q8;
  res.fp_confidence_q8 = s_ctx.result_single.fp_confidence_q8;
  res.quality_flags    = s_ctx.result_single.quality ? 0x01U : 0U;

  dstwr_ctx()->planned_tx_dw = res_tx_dw;
#if SYS_RANGING_VERIFY_TX_TIMING
  dstwr_ctx()->predicted_tx_dw = predict_delayed_tx_antenna_time(res_tx_dw);
#endif
  if (bsp_uwb_tx_delayed(&res, sizeof(res), res_tx_dw) != BSP_OK)
  {
    s_anchor_diag.result_tx_fail++;
    return anchor_exchange_complete(session, SYS_RANGING_ERR);
  }

  log_dstwr_debug(s_ctx.sequence_num, s_ctx.anchor_id, &ts);
  RANGING_LOG_D(LOG_OBJECT_CODE_RANGING, "[ANCHOR] DIST: seq=%u anchor=%u d=%.3fm valid=%u",
                s_ctx.sequence_num, s_ctx.anchor_id, s_ctx.result_single.distance_m,
                (unsigned) s_ctx.result_single.valid);
  s_ctx.state_entry_tick = HAL_GetTick();
  uwb_session_transition(session, UWB_SM_DSTWR_RESPONDER_WAIT_RESULT_TX);
  return SMF_EVENT_HANDLED;
}

static smf_state_result_t anchor_wait_result_tx_run(void *obj)
{
  uwb_session_sm_t *session = obj;
  bsp_uwb_event_t  *evt     = session->radio_event;
  bool              has_evt = session->has_radio_event;
  bool              tx_done = false;

  do
  {
    if (has_evt && evt->type == BSP_UWB_EVENT_TX_DONE)
    {
#if SYS_RANGING_VERIFY_TX_TIMING
      verify_tx_timing("RESULT", s_ctx.anchor_id, dstwr_ctx()->my_slot_id, s_ctx.sequence_num,
                       dstwr_ctx()->planned_tx_dw, dstwr_ctx()->predicted_tx_dw, evt->tx_ts, true);
#endif
      tx_done = true;
      break;
    }
  } while ((has_evt = bsp_uwb_get_event(evt)) != false);

  if (!tx_done)
  {
    uint64_t last_tx = 0;
    if (bsp_uwb_get_last_tx_timestamp(&last_tx) == BSP_OK)
    {
      uint64_t diff = (last_tx - dstwr_ctx()->planned_tx_dw) & DW_MASK_40;
      if (diff < tdma_us_to_dw(5000U))
      {
        tx_done = true;
      }
    }
  }

  if (!tx_done)
  {
    return SMF_EVENT_HANDLED;
  }

  s_anchor_diag.result_tx_done++;
  s_ctx.has_result = true;
  s_ctx.state      = STATE_ANCHOR_COMPLETE;
  log_ranging_result(&s_ctx.result_single, "ANCHOR");
  return anchor_exchange_complete(session, SYS_RANGING_OK);
}

/* Anchor event-driven processing ------------------------------------ */
static sys_ranging_err_t
anchor_process_tdma_event(uint8_t num_anchors, const uint8_t *anchor_ids, uint32_t rx_timeout_ms)
{
  event_anchor_diag_maybe_log();

  if (s_ctx.state == STATE_IDLE)
    return SYS_RANGING_ERR_NOT_STARTED;
  if (s_ctx.state != STATE_ANCHOR_RANGING_TDMA)
    return SYS_RANGING_ERR;
  if (s_uwb_session.protocol != UWB_SESSION_PROTOCOL_DSTWR
      || s_uwb_session.role != UWB_SESSION_ROLE_RESPONDER)
  {
    return SYS_RANGING_ERR;
  }

  uint32_t timeout_ms     = (rx_timeout_ms == 0U) ? DEFAULT_RX_TIMEOUT_MS : rx_timeout_ms;
  uint32_t sm_watchdog_ms = tdma_cycle_watchdog_ms(num_anchors, timeout_ms);

  s_uwb_session.peer_ids   = anchor_ids;
  s_uwb_session.peer_count = num_anchors;
  s_uwb_session.timeout_ms = timeout_ms;
  s_uwb_session.run_result = SYS_RANGING_ERR_BUSY;

  if (s_uwb_session.transaction_initialized
      && (anchor_smart_is_discovery() || anchor_smart_state_is(UWB_SM_DSTWR_RESPONDER_TRACKING))
      && dstwr_ctx()->poll_wait_deadline_tick != 0U)
  {
    if (anchor_smart_tick_due(HAL_GetTick(), dstwr_ctx()->poll_wait_deadline_tick))
    {
      return SYS_RANGING_ERR_TIMEOUT;
    }
  }
  else if (s_uwb_session.transaction_initialized && (HAL_GetTick() - s_ctx.state_entry_tick) > sm_watchdog_ms)
  {
    if (anchor_smart_is_exchange())
    {
      uwb_session_transition(&s_uwb_session, UWB_SM_DSTWR_RESPONDER_TRACKING);
    }
    return SYS_RANGING_ERR_TIMEOUT;
  }

  if (!s_uwb_session.transaction_initialized)
  {
    /* RX is armed once per transaction; later calls only deliver queued events to SMF. */
    s_ctx.state_entry_tick = HAL_GetTick();
    memset(dstwr_ctx(), 0, sizeof(*dstwr_ctx()));
    s_anchor_transaction_poll_seen = false;

    if (!tdma_anchor_config_matches(s_ctx.anchor_id, num_anchors, anchor_ids))
    {
      tdma_init(&s_tdma_anchor, TDMA_ROLE_ANCHOR, s_ctx.anchor_id, num_anchors, anchor_ids);
    }

    tdma_slot_t my_slot = { 0 };
    tdma_get_slot_for_anchor(&s_tdma_anchor, s_ctx.anchor_id, &my_slot);
    dstwr_ctx()->my_slot_id = my_slot.slot_id;
    uint32_t poll_wait_ms   = timeout_ms;

    if (s_anchor_poll_rx_plan.enabled)
    {
      uint64_t rx_start = s_anchor_poll_rx_plan.rx_start_dw;
      uint64_t now_dw   = bsp_uwb_get_current_time_dw();
      uint64_t ahead_dw = (rx_start - now_dw) & DW_MASK_40;
      if (ahead_dw >= (DW_MASK_40 / 2ULL))
      {
        if (bsp_uwb_enable_rx(0U) != BSP_OK)
        {
          ranging_transaction_reset();
          return SYS_RANGING_ERR;
        }
      }
      else
      {
        uint32_t ahead_us = tdma_dw_to_us(ahead_dw);
        poll_wait_ms      = ((ahead_us + 999U) / 1000U) + s_anchor_poll_rx_plan.timeout_ms;
        if (RANGING_ENABLE_RX_DELAYED(rx_start, s_anchor_poll_rx_plan.timeout_ms) != BSP_OK)
        {
          ranging_transaction_reset();
          return SYS_RANGING_ERR;
        }
      }
    }
    else if (bsp_uwb_enable_rx(0U) != BSP_OK)
    {
      ranging_transaction_reset();
      return SYS_RANGING_ERR;
    }

    dstwr_ctx()->poll_wait_deadline_tick = HAL_GetTick() + poll_wait_ms + ANCHOR_SMART_POLL_WATCHDOG_GUARD_MS;
    s_uwb_session.transaction_initialized = true;
  }

  bsp_uwb_event_t evt;
  bool            has_evt       = bsp_uwb_get_event(&evt);
  s_uwb_session.radio_event     = &evt;
  s_uwb_session.has_radio_event = has_evt;

  (void) smf_run_state(SMF_CTX(&s_uwb_session));

  s_uwb_session.radio_event     = NULL;
  s_uwb_session.has_radio_event = false;
  return s_uwb_session.run_result;
}

/* Anchor Smart-RX scheduling helpers -------------------------------- */
static uint32_t anchor_smart_clamp_u32(uint32_t value, uint32_t min_value, uint32_t max_value)
{
  if (value < min_value)
    return min_value;
  if (value > max_value)
    return max_value;
  return value;
}

static bool anchor_smart_tick_due(uint32_t now, uint32_t due)
{
  return ((int32_t) (now - due) >= 0);
}

static uint32_t anchor_smart_discovery_interval_ms(uint32_t power_mode)
{
  power_mode = anchor_smart_clamp_power_mode(power_mode);
  if (power_mode == ANCHOR_POWER_MODE_PERFORMANCE)
    return 0U;
  if (power_mode == ANCHOR_POWER_MODE_ECO)
    return ANCHOR_SMART_DISCOVERY_ECO_MS;
  if (power_mode == ANCHOR_POWER_MODE_DEEP_ECO)
    return ANCHOR_SMART_DISCOVERY_DEEP_ECO_MS;
  return ANCHOR_SMART_DISCOVERY_BALANCED_MS;
}

static uint32_t anchor_smart_discovery_interval_jittered_ms(uint32_t power_mode)
{
  uint32_t base_ms = anchor_smart_discovery_interval_ms(power_mode);
  if (base_ms == 0U)
    return 0U;

  if (s_anchor_discovery_jitter_state == 0U)
  {
    s_anchor_discovery_jitter_state =
      0x9E3779B9UL ^ HAL_GetTick() ^ ((uint32_t) sys_config_get()->uwb.device_id * 0x45D9F3BUL);
    if (s_anchor_discovery_jitter_state == 0U)
      s_anchor_discovery_jitter_state = 1U;
  }

  uint32_t x = s_anchor_discovery_jitter_state;
  x ^= x << 13;
  x ^= x >> 17;
  x ^= x << 5;
  s_anchor_discovery_jitter_state = x;

  uint32_t span      = (2U * ANCHOR_SMART_DISCOVERY_JITTER_MS) + 1U;
  int32_t  jitter_ms = (int32_t) (x % span) - (int32_t) ANCHOR_SMART_DISCOVERY_JITTER_MS;
  return (uint32_t) ((int32_t) base_ms + jitter_ms);
}

static uint32_t anchor_smart_estimate_poll_tick(const sys_ranging_result_t *result, uint32_t now_tick)
{
  if (!result || result->t2 == 0ULL)
  {
    return now_tick;
  }

  uint64_t now_dw     = bsp_uwb_get_current_time_dw();
  uint64_t elapsed_dw = (now_dw - (result->t2 & DW_MASK_40)) & DW_MASK_40;
  uint32_t elapsed_ms = tdma_dw_to_us(elapsed_dw) / 1000U;

  if (elapsed_ms > 1000U)
  {
    return now_tick;
  }

  return now_tick - elapsed_ms;
}

static void anchor_smart_enter_discovery(uint32_t now_tick, bool log_transition)
{
  s_anchor_smart.track_misses     = 0U;
  s_anchor_smart.discovery_misses = 0U;
  s_anchor_smart.stable_successes = 0U;
  anchor_smart_set_active_level(ANCHOR_POWER_MODE_PERFORMANCE, false);
  s_anchor_smart.next_window_tick = now_tick;
  s_anchor_smart.next_poll_tick   = 0U;
  s_anchor_smart.last_poll_tick   = 0U;
  s_anchor_smart.next_poll_dw     = 0ULL;
  s_anchor_smart.initialized      = true;

  if (s_uwb_session.protocol == UWB_SESSION_PROTOCOL_DSTWR && s_uwb_session.role == UWB_SESSION_ROLE_RESPONDER
      && smf_get_current_leaf_state(SMF_CTX(&s_uwb_session)) != NULL
      && !smf_is_terminated(SMF_CTX(&s_uwb_session)))
  {
    uwb_session_transition(&s_uwb_session, UWB_SM_DSTWR_RESPONDER_DISCOVERY);
  }
  anchor_smart_log_state(log_transition);
}

static uint32_t anchor_smart_tracking_pre_poll_ms(void)
{
  uint32_t active_mode = anchor_smart_active_power_mode(sys_config_get()->uwb.power_mode);
  uint32_t pre_ms      = ANCHOR_SMART_TRACK_PRE_POLL_MS;

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

  pre_ms += ((uint32_t) s_anchor_smart.track_misses * ANCHOR_SMART_TRACK_MISS_PRE_STEP_MS);
  return anchor_smart_clamp_u32(pre_ms, 8U, ANCHOR_SMART_TRACK_MAX_PRE_POLL_MS);
}

static uint32_t anchor_smart_tracking_late_margin_ms(void)
{
  uint32_t active_mode = anchor_smart_active_power_mode(sys_config_get()->uwb.power_mode);
  uint32_t late_ms     = ANCHOR_SMART_TRACK_LATE_MARGIN_MS;

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

  late_ms += ((uint32_t) s_anchor_smart.track_misses * ANCHOR_SMART_TRACK_MISS_LATE_STEP_MS);
  return late_ms;
}

static void anchor_smart_update_tracking_phase(const sys_ranging_result_t *result, uint32_t now_tick)
{
  uint32_t period_ms    = sys_config_get()->uwb.ranging_period_ms;
  uint32_t poll_tick    = anchor_smart_estimate_poll_tick(result, now_tick);
  bool     was_tracking = anchor_smart_is_tracking();

  if (period_ms == 0U)
  {
    period_ms = DEFAULT_RANGING_PERIOD_MS;
  }

  s_anchor_smart.track_misses     = 0U;
  s_anchor_smart.discovery_misses = 0U;
  if (!was_tracking)
  {
    s_anchor_smart.stable_successes = 0U;
  }

  /* Keep both RTOS and DW1000 deadlines: one schedules the task, the other delayed RX. */
  uint32_t pre_poll_ms          = anchor_smart_tracking_half_window_ms();
  s_anchor_smart.next_poll_tick = poll_tick + period_ms;
  s_anchor_smart.last_poll_tick = now_tick;
  s_anchor_smart.next_poll_dw =
    (result && result->t2 != 0ULL) ? ((result->t2 + tdma_us_to_dw(period_ms * 1000U)) & DW_MASK_40) : 0ULL;
  s_anchor_smart.next_window_tick = s_anchor_smart.next_poll_tick - pre_poll_ms;
  s_anchor_smart.initialized      = true;
}

static void anchor_smart_rearm_tracking_window(uint32_t now_tick)
{
  uint32_t period_ms = sys_config_get()->uwb.ranging_period_ms;

  if (period_ms == 0U)
  {
    period_ms = DEFAULT_RANGING_PERIOD_MS;
  }

  if (s_anchor_smart.next_poll_tick == 0U)
  {
    s_anchor_smart.next_window_tick = now_tick;
    return;
  }

  for (uint8_t i = 0U; i < 8U; i++)
  {
    uint32_t pre_poll_ms = anchor_smart_tracking_half_window_ms();
    uint32_t window_tick = s_anchor_smart.next_poll_tick - pre_poll_ms;

    if (!anchor_smart_tick_due(now_tick, window_tick))
    {
      s_anchor_smart.next_window_tick = window_tick;
      return;
    }

    s_anchor_smart.next_poll_tick += period_ms;
    if (s_anchor_smart.next_poll_dw != 0ULL)
    {
      s_anchor_smart.next_poll_dw =
        (s_anchor_smart.next_poll_dw + tdma_us_to_dw(period_ms * 1000U)) & DW_MASK_40;
    }
  }

  s_anchor_smart.next_window_tick = now_tick;
}

static uint32_t anchor_smart_tracking_window_ms(void)
{
  uint32_t period_ms     = sys_config_get()->uwb.ranging_period_ms;
  uint32_t window_ms     = anchor_smart_tracking_pre_poll_ms() + anchor_smart_tracking_late_margin_ms();
  uint32_t max_window_ms = ANCHOR_SMART_TRACK_MAX_WINDOW_MS;

  if (period_ms > ANCHOR_SMART_TRACK_REARM_GAP_MS)
  {
    uint32_t period_cap = period_ms - ANCHOR_SMART_TRACK_REARM_GAP_MS;
    if (period_cap < max_window_ms)
    {
      max_window_ms = period_cap;
    }
  }

  if (max_window_ms < ANCHOR_SMART_TRACK_MIN_WINDOW_MS)
  {
    max_window_ms = ANCHOR_SMART_TRACK_MIN_WINDOW_MS;
  }

  window_ms = anchor_smart_clamp_u32(window_ms, ANCHOR_SMART_TRACK_MIN_WINDOW_MS, max_window_ms);

  /* Keep an even window so the predicted POLL is exactly centered. */
  if ((window_ms & 1U) != 0U)
  {
    window_ms--;
  }
  return window_ms;
}

static uint32_t anchor_smart_tracking_half_window_ms(void)
{
  return anchor_smart_tracking_window_ms() / 2U;
}

static uint32_t anchor_smart_window_timeout_ms(uint32_t power_mode, uint32_t default_rx_timeout_ms)
{
  if (anchor_smart_clamp_power_mode(power_mode) == ANCHOR_POWER_MODE_PERFORMANCE)
  {
    return (default_rx_timeout_ms == 0U) ? DEFAULT_RX_TIMEOUT_MS : default_rx_timeout_ms;
  }

  if (anchor_smart_state_is(UWB_SM_DSTWR_RESPONDER_TRACKING))
  {
    return anchor_smart_tracking_window_ms();
  }

  return ANCHOR_SMART_DISCOVERY_ON_MS;
}

static void anchor_smart_note_success(uint32_t configured_mode)
{
  uint32_t target_mode = anchor_smart_clamp_power_mode(configured_mode);
  uint32_t active_mode = anchor_smart_active_power_mode(target_mode);

  if (anchor_smart_is_tracking())
  {
    target_mode = ANCHOR_POWER_MODE_PERFORMANCE;
  }

  s_anchor_smart.track_misses     = 0U;
  s_anchor_smart.discovery_misses = 0U;

  if (active_mode >= target_mode)
  {
    s_anchor_smart.stable_successes = 0U;
    anchor_smart_set_active_level(target_mode, false);
    return;
  }

  s_anchor_smart.stable_successes++;
  if (s_anchor_smart.stable_successes >= ANCHOR_SMART_LEVEL_STABLE_SUCCESSES)
  {
    anchor_smart_set_active_level(active_mode + 1U, true);
    s_anchor_smart.stable_successes = 0U;
  }
}

static void anchor_smart_note_tracking_miss(uint32_t configured_mode)
{
  uint32_t active_mode = anchor_smart_active_power_mode(configured_mode);

  s_anchor_smart.stable_successes = 0U;
  s_anchor_smart.discovery_misses = 0U;
  s_anchor_smart.track_misses++;

  if (active_mode > ANCHOR_POWER_MODE_PERFORMANCE)
  {
    anchor_smart_set_active_level(active_mode - 1U, true);
  }
}

static void anchor_smart_note_discovery_miss(uint32_t configured_mode)
{
  uint32_t target_mode = anchor_smart_clamp_power_mode(configured_mode);
  uint32_t active_mode = anchor_smart_active_power_mode(target_mode);

  s_anchor_smart.stable_successes = 0U;
  s_anchor_smart.track_misses     = 0U;
  if (target_mode == ANCHOR_POWER_MODE_PERFORMANCE)
  {
    s_anchor_smart.discovery_misses = 0U;
    return;
  }

  if (active_mode >= target_mode)
  {
    s_anchor_smart.discovery_misses = 0U;
    anchor_smart_set_active_level(target_mode, true);
    return;
  }

  if (s_anchor_smart.discovery_misses < UINT8_MAX)
  {
    s_anchor_smart.discovery_misses++;
  }

  if (s_anchor_smart.discovery_misses >= ANCHOR_SMART_DISCOVERY_DECAY_MISSES)
  {
    s_anchor_smart.discovery_misses = 0U;
    anchor_smart_set_active_level(active_mode + 1U, true);
  }
}

static void anchor_smart_prepare_poll_rx_plan(void)
{
  anchor_poll_rx_plan_reset();

  if (anchor_smart_clamp_power_mode(sys_config_get()->uwb.power_mode) == ANCHOR_POWER_MODE_PERFORMANCE
      || !anchor_smart_state_is(UWB_SM_DSTWR_RESPONDER_TRACKING) || s_anchor_smart.next_poll_dw == 0ULL)
  {
    return;
  }

  s_anchor_poll_rx_plan.enabled    = true;
  s_anchor_poll_rx_plan.timeout_ms = anchor_smart_tracking_window_ms();
  s_anchor_poll_rx_plan.rx_start_dw =
    (s_anchor_smart.next_poll_dw - tdma_us_to_dw(anchor_smart_tracking_half_window_ms() * 1000U))
    & DW_MASK_40;
}

static bool anchor_smart_standby_sleep_allowed(uint32_t power_mode)
{
#if ANCHOR_SMART_STANDBY_SLEEP_ENABLE
  uint32_t active_mode = anchor_smart_active_power_mode(power_mode);
  return anchor_smart_is_discovery() && active_mode >= ANCHOR_POWER_MODE_ECO;
#else
  (void) power_mode;
  return false;
#endif
}

static void anchor_smart_service_standby_sleep(uint32_t power_mode, uint32_t now_tick)
{
  if (!anchor_smart_standby_sleep_allowed(power_mode))
  {
    (void) anchor_smart_wake_or_recover(now_tick);
    return;
  }

  uint32_t wake_tick = s_anchor_smart.next_window_tick - ANCHOR_SMART_SLEEP_WAKE_GUARD_MS;

  if (bsp_uwb_is_sleeping())
  {
    if (anchor_smart_tick_due(now_tick, wake_tick))
    {
      if (!anchor_smart_wake_or_recover(now_tick))
      {
        s_anchor_smart.next_window_tick = HAL_GetTick() + ANCHOR_SMART_SLEEP_RETRY_MS;
      }
      else
      {
        /* Start discovery RX in this same service pass. Waiting for the old
         * next_window_tick would add another RTOS semaphore period after wake. */
        s_anchor_smart.next_window_tick = HAL_GetTick();
      }
    }
    return;
  }

  if (anchor_smart_tick_due(now_tick, wake_tick))
  {
    return;
  }

  uint32_t sleep_gap_ms = wake_tick - now_tick;
  if (sleep_gap_ms >= ANCHOR_SMART_SLEEP_MIN_GAP_MS)
  {
    (void) bsp_uwb_sleep_enter();
  }
}

/* Public API --------------------------------------------------------- */

sys_ranging_err_t sys_ranging_tag_start_tdma(uint8_t        num_anchors,
                                             const uint8_t *anchor_ids,
                                             uint8_t        sequence_num,
                                             uint32_t       rx_timeout_ms)
{
  (void) rx_timeout_ms;

  if (s_ctx.state != STATE_IDLE)
  {
    /* Tránh in log rác */
    static uint32_t last_busy = 0;
    if (HAL_GetTick() - last_busy >= 1000)
    {
      RLOG_W(LOG_OBJECT_CODE_RANGING, "[TAG] start busy: state=%d", (int) s_ctx.state);
      last_busy = HAL_GetTick();
    }
    return SYS_RANGING_ERR_BUSY;
  }
  if (num_anchors == 0 || num_anchors > 8 || !anchor_ids)
    return SYS_RANGING_ERR_PARAM;

  ranging_transaction_reset();
  s_ctx.sequence_num     = sequence_num;
  s_ctx.state            = STATE_TAG_RANGING_TDMA;
  s_ctx.state_entry_tick = HAL_GetTick();
  uwb_session_start(UWB_SESSION_PROTOCOL_DSTWR, UWB_SESSION_ROLE_INITIATOR,
                    &s_uwb_states[UWB_SM_DSTWR_INITIATOR]);
  s_stats.total_count++;

  return SYS_RANGING_OK;
}

sys_ranging_err_t
sys_ranging_tag_process_tdma(uint8_t num_anchors, const uint8_t *anchor_ids, uint32_t rx_timeout_ms)
{
  event_tag_diag_maybe_log();

  if (s_ctx.state == STATE_IDLE)
    return SYS_RANGING_ERR_NOT_STARTED;
  if (s_ctx.state != STATE_TAG_RANGING_TDMA)
    return SYS_RANGING_ERR;
  if (s_uwb_session.protocol != UWB_SESSION_PROTOCOL_DSTWR
      || s_uwb_session.role != UWB_SESSION_ROLE_INITIATOR)
  {
    return SYS_RANGING_ERR;
  }

  uint32_t timeout_ms = (rx_timeout_ms == 0U) ? DEFAULT_RX_TIMEOUT_MS : rx_timeout_ms;
  timeout_ms          = tdma_cycle_watchdog_ms(num_anchors, timeout_ms);
  if ((HAL_GetTick() - s_ctx.state_entry_tick) > timeout_ms)
  {
    ranging_transaction_reset();
    smf_set_terminate(SMF_CTX(&s_uwb_session), 1);
    return SYS_RANGING_ERR_TIMEOUT;
  }

  bsp_uwb_event_t evt;
  bool            has_evt = bsp_uwb_get_event(&evt);

  s_uwb_session.peer_ids        = anchor_ids;
  s_uwb_session.peer_count      = num_anchors;
  s_uwb_session.timeout_ms      = timeout_ms;
  s_uwb_session.radio_event     = &evt;
  s_uwb_session.has_radio_event = has_evt;
  s_uwb_session.run_result      = SYS_RANGING_ERR_BUSY;

  (void) smf_run_state(SMF_CTX(&s_uwb_session));

  s_uwb_session.radio_event     = NULL;
  s_uwb_session.has_radio_event = false;
  return s_uwb_session.run_result;
}

sys_ranging_err_t sys_ranging_tag_get_results_tdma(sys_ranging_multi_result_t *results)
{
  if (!results)
    return SYS_RANGING_ERR_PARAM;
  if (s_ctx.state != STATE_TAG_COMPLETE || !s_ctx.has_result)
    return SYS_RANGING_ERR_NO_RESULT;

  memcpy(results, &s_ctx.result_multi, sizeof(sys_ranging_multi_result_t));
  ranging_transaction_reset();
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
    if (HAL_GetTick() - last_busy >= 1000)
    {
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

  /* Preserve DISCOVERY/TRACKING across per-exchange transaction resets. */
  bool resume_anchor_policy =
    s_uwb_session.protocol == UWB_SESSION_PROTOCOL_DSTWR && s_uwb_session.role == UWB_SESSION_ROLE_RESPONDER
    && !smf_is_terminated(SMF_CTX(&s_uwb_session))
    && (anchor_smart_is_discovery() || anchor_smart_state_is(UWB_SM_DSTWR_RESPONDER_TRACKING));

  ranging_transaction_reset();
  s_ctx.anchor_id        = anchor_id;
  s_ctx.state            = STATE_ANCHOR_RANGING_TDMA;
  s_ctx.state_entry_tick = HAL_GetTick();
  if (resume_anchor_policy)
  {
    s_uwb_session.peer_ids                = NULL;
    s_uwb_session.radio_event             = NULL;
    s_uwb_session.timeout_ms              = 0U;
    s_uwb_session.peer_count              = 0U;
    s_uwb_session.has_radio_event         = false;
    s_uwb_session.transaction_initialized = false;
    s_uwb_session.run_result              = SYS_RANGING_ERR_BUSY;
  }
  else
  {
    uwb_session_start(UWB_SESSION_PROTOCOL_DSTWR, UWB_SESSION_ROLE_RESPONDER,
                      &s_uwb_states[UWB_SM_DSTWR_RESPONDER]);
  }
  s_stats.total_count++;

  return SYS_RANGING_OK;
}

sys_ranging_err_t
sys_ranging_anchor_process_tdma(uint8_t num_anchors, const uint8_t *anchor_ids, uint32_t rx_timeout_ms)
{
  uint32_t now         = HAL_GetTick();
  uint32_t power_mode  = anchor_smart_clamp_power_mode(sys_config_get()->uwb.power_mode);
  uint32_t active_mode = anchor_smart_active_power_mode(power_mode);
  uint8_t  anchor_id   = sys_config_get()->uwb.device_id;

  if (anchor_smart_state_is(UWB_SM_DSTWR_RESPONDER_TRACKING) && s_anchor_smart.last_poll_tick != 0U)
  {
    uint32_t period_ms = sys_config_get()->uwb.ranging_period_ms;
    if (period_ms == 0U)
      period_ms = DEFAULT_RANGING_PERIOD_MS;

    uint64_t loss_timeout_ms = (uint64_t) period_ms * ANCHOR_SMART_TRACK_MAX_MISSES;
    if ((uint64_t) (now - s_anchor_smart.last_poll_tick) >= loss_timeout_ms)
    {
      ranging_transaction_reset();
      anchor_smart_enter_discovery(now, true);
      active_mode = anchor_smart_active_power_mode(power_mode);
    }
  }

  bool              explicit_start = (s_ctx.state != STATE_IDLE);
  bool              poll_seen      = false;
  uint32_t          timeout_ms     = 0U;
  sys_ranging_err_t err;

  if (explicit_start)
  {
    /* Continue the exchange opened by a previous service pass. */
    timeout_ms = anchor_smart_window_timeout_ms(power_mode, rx_timeout_ms);
    err        = anchor_process_tdma_event(num_anchors, anchor_ids, timeout_ms);
    poll_seen  = s_anchor_transaction_poll_seen;
    if (err == SYS_RANGING_ERR_BUSY)
    {
      return SYS_RANGING_ERR_BUSY;
    }
  }
  else
  {
    /* No exchange is active: maintain discovery/tracking and open the next RX window. */
    if (!s_anchor_smart.initialized || s_uwb_session.protocol != UWB_SESSION_PROTOCOL_DSTWR
        || s_uwb_session.role != UWB_SESSION_ROLE_RESPONDER
        || smf_is_terminated(SMF_CTX(&s_uwb_session)))
    {
      anchor_smart_reset_runtime();
      anchor_smart_enter_discovery(now, true);
    }

    if (power_mode == ANCHOR_POWER_MODE_PERFORMANCE)
    {
      anchor_smart_set_active_level(ANCHOR_POWER_MODE_PERFORMANCE, false);
      s_anchor_smart.stable_successes = 0U;
      s_anchor_smart.track_misses     = 0U;
      s_anchor_smart.discovery_misses = 0U;
      s_anchor_smart.next_window_tick = now;
      s_anchor_smart.initialized      = true;
      anchor_smart_log_state(false);
    }

    anchor_smart_service_standby_sleep(power_mode, now);
    /* Wake may yield for several milliseconds. Never make the window decision
     * with the timestamp captured before waking the DW1000. */
    now = HAL_GetTick();

    active_mode = anchor_smart_active_power_mode(power_mode);
    if (active_mode != ANCHOR_POWER_MODE_PERFORMANCE
        && !anchor_smart_tick_due(now, s_anchor_smart.next_window_tick))
    {
      return SYS_RANGING_ERR_BUSY;
    }

    if (!anchor_smart_wake_or_recover(now))
    {
      s_anchor_smart.next_window_tick = now + ANCHOR_SMART_SLEEP_RETRY_MS;
      return SYS_RANGING_ERR_BUSY;
    }

    if (s_ctx.state == STATE_IDLE)
    {
      sys_ranging_err_t start_err =
        sys_ranging_anchor_start_tdma(anchor_id, num_anchors, anchor_ids, rx_timeout_ms);
      if (start_err != SYS_RANGING_OK)
      {
        return start_err;
      }
    }

    timeout_ms = anchor_smart_window_timeout_ms(power_mode, rx_timeout_ms);
    anchor_smart_prepare_poll_rx_plan();
    err       = anchor_process_tdma_event(num_anchors, anchor_ids, timeout_ms);
    poll_seen = s_anchor_transaction_poll_seen;
    anchor_poll_rx_plan_reset();
  }

  if (err == SYS_RANGING_OK)
  {
    if (power_mode != ANCHOR_POWER_MODE_PERFORMANCE)
    {
      /* The next-POLL estimate was already phase-locked from the actual POLL
       * RX timestamp, even if the remainder of the exchange later fails. */
      anchor_smart_note_success(power_mode);
    }
    return SYS_RANGING_OK;
  }

  if (err == SYS_RANGING_ERR_BUSY)
  {
    return SYS_RANGING_ERR_BUSY;
  }

  /* Keep the discovery/tracking phase while resetting this transaction. */
  ranging_transaction_reset();

  active_mode = anchor_smart_active_power_mode(power_mode);
  if (anchor_smart_state_is(UWB_SM_DSTWR_RESPONDER_TRACKING) && power_mode != ANCHOR_POWER_MODE_PERFORMANCE)
  {
    if (poll_seen)
    {
      s_anchor_smart.track_misses     = 0U;
      s_anchor_smart.discovery_misses = 0U;
      s_anchor_smart.next_window_tick = HAL_GetTick();
    }
    else
    {
      anchor_smart_note_tracking_miss(power_mode);
    }

    if (!poll_seen && s_anchor_smart.track_misses >= ANCHOR_SMART_TRACK_MAX_MISSES)
    {
      anchor_smart_enter_discovery(HAL_GetTick(), true);
    }
    else if (active_mode == ANCHOR_POWER_MODE_PERFORMANCE)
    {
      s_anchor_smart.next_window_tick = HAL_GetTick();
    }
    else
    {
      anchor_smart_rearm_tracking_window(HAL_GetTick());
    }
  }
  else if (anchor_smart_is_discovery() && power_mode != ANCHOR_POWER_MODE_PERFORMANCE)
  {
    anchor_smart_note_discovery_miss(power_mode);
    active_mode = anchor_smart_active_power_mode(power_mode);
    s_anchor_smart.next_window_tick =
      HAL_GetTick() + anchor_smart_discovery_interval_jittered_ms(active_mode);
  }
  else
  {
    s_anchor_smart.next_window_tick = HAL_GetTick();
  }

  return SYS_RANGING_ERR_BUSY;
}

sys_ranging_err_t sys_ranging_anchor_get_result_tdma(sys_ranging_result_t *result)
{
  if (!result)
    return SYS_RANGING_ERR_PARAM;
  if (s_ctx.state != STATE_ANCHOR_COMPLETE || !s_ctx.has_result)
    return SYS_RANGING_ERR_NO_RESULT;

  memcpy(result, &s_ctx.result_single, sizeof(sys_ranging_result_t));
  ranging_transaction_reset();
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

void sys_ranging_abort(void)
{
  ranging_transaction_reset();
  smf_set_terminate(SMF_CTX(&s_uwb_session), 1);
  anchor_smart_reset_runtime();
}

void sys_ranging_reset_stats(void)
{
  s_stats.total_count   = 0;
  s_stats.success_count = 0;
  s_stats.error_count   = 0;
}

uint32_t sys_ranging_get_ms_to_deadline(void)
{
  /* Bound the RTOS wait so timeout-driven SMF transitions are serviced on time. */
  if (s_ctx.state != STATE_TAG_RANGING_TDMA && s_ctx.state != STATE_ANCHOR_RANGING_TDMA)
  {
    return 10;
  }

  if (anchor_smart_is_discovery() || anchor_smart_state_is(UWB_SM_DSTWR_RESPONDER_TRACKING))
  {
    uint32_t deadline_tick = dstwr_ctx()->poll_wait_deadline_tick;
    if (deadline_tick == 0U)
    {
      return 1;
    }

    int32_t remaining_ms = (int32_t) (deadline_tick - HAL_GetTick());
    if (remaining_ms <= 0)
    {
      return 1;
    }
    return (remaining_ms > 10) ? 10U : (uint32_t) remaining_ms;
  }

  bool has_dw_deadline = uwb_session_is_state(&s_uwb_session, UWB_SM_DSTWR_INITIATOR_WAIT_RESP)
                         || uwb_session_is_state(&s_uwb_session, UWB_SM_DSTWR_INITIATOR_WAIT_RESULT)
                         || uwb_session_is_state(&s_uwb_session, UWB_SM_DSTWR_RESPONDER_WAIT_FINAL);
  if (!has_dw_deadline)
  {
    /* TX states must be serviced promptly when their ISR event arrives. */
    return 1;
  }

  uint64_t now_dw      = bsp_uwb_get_current_time_dw();
  uint64_t deadline_dw = dstwr_ctx()->deadline_dw;
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
  return s_ctx.state != STATE_IDLE;
}
