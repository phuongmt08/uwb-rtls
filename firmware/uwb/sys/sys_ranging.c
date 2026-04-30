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

/* Message type constants */
#define MW_DSTWR_MSG_TYPE_POLL   0xE1
#define MW_DSTWR_MSG_TYPE_RESP   0xE2
#define MW_DSTWR_MSG_TYPE_FINAL  0xE3
#define MW_DSTWR_MSG_TYPE_RESULT 0xE4 /* Anchor sends distance to TAG */
/* Macro definitions -------------------------------------------------- */
// SYS_RANGING_DEBUG: Enable  detailed debug logs for ranging state machine and calculations
#define SYS_RANGING_DEBUG     1

#if SYS_RANGING_DEBUG
#define RANGING_LOG_D(...) RLOG_D(__VA_ARGS__)
#else
#define RANGING_LOG_D(...) \
  do                       \
  {                        \
  } while (0)
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
  uint8_t rssi_last;
  uint8_t padding[7];
} poll_msg_t;

typedef struct __attribute__((packed))
{
  uint8_t  msg_type;
  uint8_t  sequence_num;
  uint8_t  anchor_id;
  uint8_t  slot_id;
  uint64_t poll_rx_ts;
  uint64_t resp_tx_ts;
  uint8_t  rssi_poll;
  uint8_t  calib_status;
  uint8_t  padding[2];
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

/* RESULT message: Anchor sends calculated distance to TAG */
/* FIX Bug-C: added slot_id field (symmetric with resp_msg_t) so TAG can
 * validate that each anchor transmitted in the correct TDMA slot. */
typedef struct __attribute__((packed))
{
  uint8_t msg_type;
  uint8_t sequence_num;
  uint8_t anchor_id;
  uint8_t slot_id;    /* TDMA slot ID - TAG uses this to detect slot mismatches */
  uint8_t valid;      /* 1 = valid distance, 0 = error */
  float   distance_m; /* Calculated distance */
  int8_t  rssi;
  uint8_t padding[1];
} result_msg_t;
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

#if UWB_EVENT_DRIVEN
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
    int8_t                   poll_rssi_dbm;
    
    struct {
        uint8_t  anchor_id;
        uint64_t resp_rx_ts;
        uint64_t poll_rx_ts;
        uint64_t resp_tx_ts;
        int8_t   rssi;
      uint8_t  calib_status;
        bool     valid;
    } anchor_resp[8];
} sys_ranging_event_ctx_t;
#endif

/* Private variables -------------------------------------------------- */
static ranging_ctx_t    s_ctx         = { 0 };
#if UWB_EVENT_DRIVEN
static sys_ranging_event_ctx_t s_sys_ranging_ev = {0};
#endif
static tdma_scheduler_t s_tdma_tag    = { 0 };
static tdma_scheduler_t s_tdma_anchor = { 0 };
static sys_calib_status_t s_calib_status = SYS_CALIB_STATUS_NORMAL;
static struct
{
  uint32_t total_count;
  uint32_t success_count;
  uint32_t error_count;
} s_stats = { 0 };

/* Static guard */
static bool s_ranging_busy = false;

/* Private functions --------------------------------------------------- */

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

static int
hal_rx_with_timeout(uint8_t *buffer, uint16_t buffer_size, uint16_t *received_length, uint32_t timeout_us)
{
  uint32_t timeout_ms = (timeout_us + 999) / 1000;

  if (!buffer || !received_length)
  {
    return -1;
  }
  *received_length = 0;

  bsp_uwb_clear_irq_event();

  if (bsp_uwb_enable_rx(0) != BSP_OK)
  {
    return -1;
  }

  if (timeout_ms == 0)
  {
    timeout_ms = 1;
  }

  if (!bsp_uwb_wait_for_irq_event(timeout_ms))
  {
    return -1;
  }

  {
    bsp_err_t err = bsp_uwb_rx(buffer, buffer_size, received_length);
    if (err == BSP_OK && *received_length > 0)
    {
      return 0;
    }
  }

  return -1;
}

static void format_distance_m(char *buf, size_t len, float distance_m)
{
  if (len == 0) return;
  int32_t  mm        = (int32_t) (distance_m * 1000.0f + (distance_m >= 0.0f ? 0.5f : -0.5f));
  int32_t  abs_mm    = (mm >= 0) ? mm : -mm;
  uint32_t m_part    = (uint32_t) (abs_mm / 1000);
  uint32_t frac_part = (uint32_t) (abs_mm % 1000);
  char     tmp[16];
  int      pos = 0;
  if (mm < 0) tmp[pos++] = '-';
  /* Integer part */
  if (m_part == 0) { tmp[pos++] = '0'; }
  else {
    char ibuf[8]; int ilen = 0;
    uint32_t v = m_part;
    while (v > 0) { ibuf[ilen++] = '0' + (v % 10); v /= 10; }
    for (int k = ilen - 1; k >= 0; k--) tmp[pos++] = ibuf[k];
  }
  tmp[pos++] = '.';
  /* 3 fractional digits, zero-padded */
  tmp[pos++] = '0' + (frac_part / 100);
  tmp[pos++] = '0' + ((frac_part / 10) % 10);
  tmp[pos++] = '0' + (frac_part % 10);
  tmp[pos]   = '\0';
  size_t copy = (size_t) pos < len ? (size_t) pos : len - 1;
  for (size_t i = 0; i < copy; i++) buf[i] = tmp[i];
  buf[copy] = '\0';
}

static inline bool validate_msg_type(const uint8_t *data, uint16_t len, uint8_t expected_type)
{
  if (!data || data[0] != expected_type) return false;
  uint16_t min_len = 0;
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

static int hal_rx_wait_valid_msg(uint8_t  *buffer,
                                 uint16_t  buffer_size,
                                 uint16_t *received_length,
                                 uint8_t   expected_type,
                                 uint32_t  timeout_us)
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
  if (bsp_uwb_enable_rx(0) != BSP_OK)
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

static int hal_rx_wait_valid_msg_delayed(uint8_t  *buffer,
                                         uint16_t  buffer_size,
                                         uint16_t *received_length,
                                         uint8_t   expected_type,
                                         uint64_t  rx_timestamp_dw,
                                         uint32_t  timeout_us)
{
  static uint32_t s_unexpected_type_log_tick_delayed = 0;
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

  bsp_uwb_clear_irq_event();
  if (bsp_uwb_enable_rx_delayed(rx_timestamp_dw, 0) != BSP_OK)
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

      if ((HAL_GetTick() - s_unexpected_type_log_tick_delayed) >= 1000U)
      {
        RLOG_W(LOG_OBJECT_CODE_RANGING, "[RXWAIT] Unexpected frame type=0x%02X len=%u expected=0x%02X",
               (unsigned) buffer[0], (unsigned) *received_length, (unsigned) expected_type);
        s_unexpected_type_log_tick_delayed = HAL_GetTick();
      }
    }
    __NOP();
  }

  *received_length = 0;
  return -1;
}

static void state_machine_reset(void)
{
  s_ctx.state            = STATE_IDLE;
  s_ctx.state_entry_tick = 0U;
  s_ctx.anchor_id        = 0U;
  s_ctx.has_result       = false;
  memset(&s_ctx.result_multi, 0, sizeof(s_ctx.result_multi));
  memset(&s_ctx.result_single, 0, sizeof(s_ctx.result_single));
#if UWB_EVENT_DRIVEN
  s_sys_ranging_ev.step = SYS_RANGING_EV_SYS_IDLE;
#endif
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
  char dist_str[16];
  format_distance_m(dist_str, sizeof(dist_str), result->distance_m);
  RLOG_I(LOG_OBJECT_CODE_RANGING, "[%s] Distance: %s m [A:%u RSSI:%ddBm]", role, dist_str, result->anchor_id,
         result->rssi);
}

static uint64_t ensure_future_tx(uint64_t tx_time_dw, uint32_t guard_us)
{
  uint64_t now      = bsp_uwb_get_current_time_dw();
  uint64_t guard_dw = tdma_us_to_dw(guard_us);

 
  uint64_t ahead_dw = (tx_time_dw - now) & DW_MASK_40;
  if (ahead_dw == 0ULL || ahead_dw <= guard_dw || ahead_dw >= (1ULL << 39))
  {
    uint32_t late_us = (ahead_dw == 0ULL || ahead_dw >= (1ULL << 39)) ? 0U : (uint32_t) (guard_us - tdma_dw_to_us(ahead_dw));
    RLOG_W(LOG_OBJECT_CODE_RANGING, "[TX] Slot missed by ~%luus - ABORTING TO PREVENT COLLISION",
           (unsigned long) late_us);
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
 *   - HPDWARN fired and chip fell back to immediate TX (large delta)
 *   - SPI latency caused scheduler to be called too late
 *   - ensure_future_tx had to push the time forward
 *
 * Severity thresholds (1 tick ≈ 15.65ps, 63898 ticks ≈ 1µs):
 *   |delta| ≤ 1024 ticks  (~16µs)  → OK, normal quantization noise
 *   |delta| ≤ 63898 ticks (~1ms)   → WARN, jitter or minor slip
 *   |delta| >  63898 ticks          → ERROR, slot was missed
 * ---------------------------------------------------------------- */
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

static int
ds_twr_anchor_tdma(uint8_t anchor_id, uint8_t num_anchors, const uint8_t *anchor_ids, uint32_t rx_timeout_us)
{
  if (s_ranging_busy)
    return -1;
  s_ranging_busy = true;

  if (!tdma_anchor_config_matches(anchor_id, num_anchors, anchor_ids))
  {
    if (tdma_init(&s_tdma_anchor, TDMA_ROLE_ANCHOR, anchor_id, num_anchors, anchor_ids) != TDMA_OK)
    {
      s_ranging_busy = false;
      return -1;
    }
    /* Keep scheduler defaults as single source of timing truth. */
  }

  tdma_slot_t my_slot = { 0 };
  if (tdma_get_slot_for_anchor(&s_tdma_anchor, anchor_id, &my_slot) != TDMA_OK)
  {
    RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[ANCHOR] anchor_id %u not in anchor_ids list",
           anchor_id);
    s_ranging_busy = false;
    return -1;
  }
  uint8_t my_slot_id = my_slot.slot_id;

  /* 1. Receive POLL */
  uint8_t  poll_buf[128];
  uint16_t poll_len = 0;

  if (hal_rx_wait_valid_msg(poll_buf, sizeof(poll_buf), &poll_len, MW_DSTWR_MSG_TYPE_POLL, rx_timeout_us)
      != 0)
  {
    s_ranging_busy = false;
    return -2;
  }

  poll_msg_t *poll = (poll_msg_t *) poll_buf;

  (void) poll_len;

  {
    uint8_t my_mask_bit = (uint8_t) (1U << (anchor_id - 1U));
    bool poll_targets_me = ((poll->anchor_mask & my_mask_bit) != 0U);
    RANGING_LOG_D(LOG_OBJECT_CODE_RANGING,
                  "[ANCHOR%u] RX POLL seq=%u n=%u mask=0x%02X target_me=%u",
                  anchor_id,
                  (unsigned) poll->sequence_num,
                  (unsigned) poll->num_anchors,
                  (unsigned) poll->anchor_mask,
                  poll_targets_me ? 1U : 0U);

    if (!poll_targets_me)
    {
      RLOG_W(LOG_OBJECT_CODE_RANGING,
             "[ANCHOR%u] POLL mask does not include this anchor (seq=%u mask=0x%02X)",
             anchor_id,
             (unsigned) poll->sequence_num,
             (unsigned) poll->anchor_mask);
    }

    if (poll->num_anchors != num_anchors)
    {
      RLOG_W(LOG_OBJECT_CODE_RANGING,
             "[ANCHOR%u] POLL num_anchors mismatch: poll=%u local=%u (seq=%u). Adopting POLL num_anchors.",
             anchor_id,
             (unsigned) poll->num_anchors,
             (unsigned) num_anchors,
             (unsigned) poll->sequence_num);

      /* Dynamically adopt POLL config to ensure expected_final_tx_dw aligns with TAG's timeline */
      s_tdma_anchor.schedule.num_anchors = poll->num_anchors;
      num_anchors = poll->num_anchors;
    }
  }

  /* Read T2 (POLL RX timestamp on anchor) */
  uint64_t poll_rx_ts = 0;
  if (bsp_uwb_get_last_rx_timestamp(&poll_rx_ts) != BSP_OK)
  {
    RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[ANCHOR] Failed to read T2 timestamp");
    s_ranging_busy = false;
    return -1;
  }

  /* Use RSSI captured at RX moment of POLL frame (no delayed diagnostic read). */
  int8_t poll_rssi_dbm = bsp_uwb_get_last_rx_rssi();

  if (tdma_sync_to_poll(&s_tdma_anchor, poll_rx_ts) != TDMA_OK)
  {
    s_ranging_busy = false;
    return -1;
  }

  uint64_t resp_tx_time_dw = 0;
  if (tdma_calculate_response_time(&s_tdma_anchor, anchor_id, &resp_tx_time_dw) != TDMA_OK)
  {
    s_ranging_busy = false;
    return -1;
  }
  // NOTE: Deprecated
  uint32_t resp_offset_us = tdma_dw_to_us((resp_tx_time_dw - poll_rx_ts) & DW_MASK_40);

  (void) resp_offset_us;

  /* Ensure future TX - if missed, abort to avoid colliding with next anchor */
  resp_tx_time_dw = ensure_future_tx(resp_tx_time_dw, TDMA_DEFAULT_GUARD_TIME_US);
  if (resp_tx_time_dw == 0ULL)
  {
    s_ranging_busy = false;
    return -1;
  }

  /* Put actual antenna-domain delayed TX timestamp in payload (T3). */
  uint64_t t3_timestamp_pred = predict_delayed_tx_antenna_time(resp_tx_time_dw);
  uint64_t t3_timestamp_used = t3_timestamp_pred;

  /* Build and transmit response */
  resp_msg_t resp_msg   = { 0 };
  resp_msg.msg_type     = MW_DSTWR_MSG_TYPE_RESP;
  resp_msg.sequence_num = poll->sequence_num;
  resp_msg.anchor_id    = anchor_id;
  resp_msg.slot_id      = my_slot_id;
  resp_msg.calib_status = (uint8_t) s_calib_status;
  {
    uint64_t poll_rx_payload = poll_rx_ts & DW_MASK_40;
    uint64_t resp_tx_payload = t3_timestamp_pred & DW_MASK_40;
    memcpy(&resp_msg.poll_rx_ts, &poll_rx_payload, sizeof(poll_rx_payload));
    memcpy(&resp_msg.resp_tx_ts, &resp_tx_payload, sizeof(resp_tx_payload));
  }
  resp_msg.rssi_poll = (uint8_t) poll_rssi_dbm;

  if (bsp_uwb_tx_delayed(&resp_msg, sizeof(resp_msg), resp_tx_time_dw) != BSP_OK)
  {
    RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[ANCHOR] TX_DELAYED failed for RESP (time=" DW_FMT ")",
           DW_ARG(resp_tx_time_dw));
    s_ranging_busy = false;
    return -1;
  }

  /* Read actual T3 from hardware and verify planned vs actual TX timing. */
  {
    uint64_t t3_actual = 0;
    if (bsp_uwb_get_last_tx_timestamp(&t3_actual) == BSP_OK)
    {
      t3_timestamp_used = t3_actual & DW_MASK_40;
    }
    /* verify_tx_timing: always logs - DEBUG if OK, WARN if jitter, ERROR if slot missed.
     * resp_tx_time_dw here is post-ensure_future_tx (the time we actually asked for).
     * t3_timestamp_pred is the quantized antenna-domain prediction of that time. */
    verify_tx_timing("RESP", anchor_id, my_slot_id, poll->sequence_num, resp_tx_time_dw, t3_timestamp_pred, t3_actual, (t3_actual != 0));
  }

  /* 3. Wait for FINAL */
  uint8_t  final_buf[256];
  uint16_t final_len = 0;

  uint32_t effective_slot_us = tdma_effective_slot_us(&s_tdma_anchor);
  uint64_t expected_final_dw = 0;
  if (tdma_calculate_final_time(&s_tdma_anchor, num_anchors, &expected_final_dw) != TDMA_OK)
  {
    /* Fallback mirrors tdma_calculate_final_time() using superframe_start_dw. */
    expected_final_dw = s_tdma_anchor.superframe_start_dw
                        + tdma_us_to_dw(s_tdma_anchor.schedule.poll_to_resp_delay_us
                                        + ((uint32_t) num_anchors * effective_slot_us)
                                        + s_tdma_anchor.schedule.slot_duration_us
                                        + s_tdma_anchor.schedule.resp_to_final_delay_us);
    expected_final_dw &= DW_MASK_40;
  }
  uint64_t now_before_wait_dw = bsp_uwb_get_current_time_dw();
  int64_t  final_left_dw      = (int64_t) (expected_final_dw - now_before_wait_dw);
  uint32_t final_left_us = tdma_dw_to_us((uint64_t) ((final_left_dw >= 0) ? final_left_dw : -final_left_dw));
  uint32_t final_timeout_us = tdma_compute_final_wait_timeout_us(&s_tdma_anchor, num_anchors);

  uint64_t rx_start_dw = (expected_final_dw - tdma_us_to_dw(1000U)) & DW_MASK_40;
  int rx_res = -1;
  
  if (sys_config_get()->uwb.power_mode == ANCHOR_POWER_MODE_PERFORMANCE)
  {
      rx_res = hal_rx_wait_valid_msg(final_buf, sizeof(final_buf), &final_len, MW_DSTWR_MSG_TYPE_FINAL, final_timeout_us);
  }
  else
  {
      rx_res = hal_rx_wait_valid_msg_delayed(final_buf, sizeof(final_buf), &final_len, MW_DSTWR_MSG_TYPE_FINAL, rx_start_dw, final_timeout_us + 1000U);
  }

  if (rx_res != 0)
  {
    RLOG_W(LOG_OBJECT_CODE_RANGING, "[ANCHOR] No FINAL received (seq=%u left_pre=%c%luus timeout=%luus)",
           poll->sequence_num, (final_left_dw >= 0) ? '+' : '-', (unsigned long) final_left_us,
           (unsigned long) final_timeout_us);
    s_ranging_busy = false;
    return -2;
  }

  final_msg_t *final_msg = (final_msg_t *) final_buf;

  //RANGING_LOG_D(LOG_OBJECT_CODE_RANGING, "[ANCHOR] RX FINAL: len=%u seq=%u tag=%u responses=%u mask=0x%02X",
  //              final_len, final_msg->sequence_num, final_msg->tag_id, final_msg->num_responses,
  //              final_msg->anchor_resp_mask);

  /* CRITICAL: Validate FINAL sequence_num matches POLL */
  if (final_msg->sequence_num != poll->sequence_num)
  {
    RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[ANCHOR] FINAL seq=%u mismatch POLL seq=%u",
           final_msg->sequence_num, poll->sequence_num);
    s_ranging_busy = false;
    return -1;
  }

  /* Read T6 */
  uint64_t final_rx_ts = 0;
  if (bsp_uwb_get_last_rx_timestamp(&final_rx_ts) != BSP_OK)
  {
    RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[ANCHOR] Failed to read T6 timestamp");
    s_ranging_busy = false;
    return -1;
  }

  /* Extract our data safely from packed payload (avoid unaligned u64 access). */
  bool     anchor_found    = false;
  uint64_t resp_rx_ts_tag  = 0;
  uint64_t final_tx_ts_tag = 0;
  uint64_t poll_tx_ts_tag  = 0;

  memcpy(&poll_tx_ts_tag, &final_msg->poll_tx_ts, sizeof(poll_tx_ts_tag));
  poll_tx_ts_tag &= DW_MASK_40;

  for (uint8_t i = 0; i < final_msg->num_responses; i++)
  {
    uint8_t *entry             = final_buf + sizeof(final_msg_t) + (i * sizeof(final_anchor_data_t));
    uint8_t  entry_anchor_id   = entry[0];
    uint64_t entry_resp_rx_ts  = 0;
    uint64_t entry_final_tx_ts = 0;

    memcpy(&entry_resp_rx_ts, entry + 1, sizeof(entry_resp_rx_ts));
    memcpy(&entry_final_tx_ts, entry + 1 + sizeof(uint64_t), sizeof(entry_final_tx_ts));

    if (entry_anchor_id == anchor_id)
    {
      resp_rx_ts_tag  = entry_resp_rx_ts & DW_MASK_40;
      final_tx_ts_tag = entry_final_tx_ts & DW_MASK_40;
      anchor_found    = true;
      break;
    }
  }

  if (!anchor_found)
  {
    RLOG_W(LOG_OBJECT_CODE_RANGING,
           "[ANCHOR] Anchor ID %u not found in FINAL (num_responses=%u, mask=0x%02X) - skip cycle", anchor_id,
           final_msg->num_responses, final_msg->anchor_resp_mask);
    s_ranging_busy = false;
    return -2;
  }

  RANGING_LOG_D(LOG_OBJECT_CODE_RANGING, "[ANCHOR] FINAL data: anchor=%u t4=" DW_FMT " t5=" DW_FMT, anchor_id,
                DW_ARG(resp_rx_ts_tag), DW_ARG(final_tx_ts_tag));

  /* Calculate distance */
  dstwr_timestamps_t ts = { .t1 = poll_tx_ts_tag,
                            .t2 = poll_rx_ts,
                            .t3 = t3_timestamp_used,
                            .t4 = resp_rx_ts_tag,
                            .t5 = final_tx_ts_tag,
                            .t6 = final_rx_ts };

  float raw_distance_m = calculate_distance(&ts);
  bool  raw_valid      = (raw_distance_m > 0.0f && raw_distance_m < 100.0f);

  s_ctx.result_single.distance_m = raw_distance_m;
  s_ctx.result_single.anchor_id  = anchor_id;
  s_ctx.result_single.rssi       = poll_rssi_dbm;
  s_ctx.result_single.calib_status = SYS_CALIB_STATUS_NORMAL;
  s_ctx.result_single.valid      = raw_valid;
  s_ctx.result_single.t1         = ts.t1;
  s_ctx.result_single.t2         = ts.t2;
  s_ctx.result_single.t3         = ts.t3;
  s_ctx.result_single.t4         = ts.t4;
  s_ctx.result_single.t5         = ts.t5;
  s_ctx.result_single.t6         = ts.t6;

  /* 4. Send RESULT message to TAG */
  result_msg_t result_msg = { 0 };
  result_msg.msg_type     = MW_DSTWR_MSG_TYPE_RESULT;
  result_msg.sequence_num = final_msg->sequence_num;
  result_msg.anchor_id    = anchor_id;
  result_msg.slot_id      = my_slot_id;
  result_msg.valid        = raw_valid ? 1 : 0;
  result_msg.distance_m   = raw_distance_m;
  result_msg.rssi         = poll_rssi_dbm;

  /* RESULT TX time is anchored to superframe_start_dw via tdma_calculate_final_time(),
   * not to final_rx_ts. Each anchor receives FINAL at a slightly different time
   * (propagation delay), so using final_rx_ts as base would shift RESULT slots
   * differently per anchor — breaking the shared reference point guarantee.
   *
   * result_tx = expected_final_tx + final_to_result_delay + my_slot_id * effective_slot
   *
   * my_slot_id * effective_slot ensures slots are evenly separated (4000µs apart),
   * with slot 1 at +5500µs, slot 2 at +9500µs, etc. after expected FINAL TX. */
  uint64_t expected_final_tx_dw = 0;
  if (tdma_calculate_final_time(&s_tdma_anchor, num_anchors, &expected_final_tx_dw) != TDMA_OK)
  {
    expected_final_tx_dw = final_rx_ts; /* Fallback only — should not happen. */
  }

  /* Debug: verify expected_final_tx_dw is in near future (~ms), not past or far future.
   * If ahead_ms is large negative → superframe_start_dw is stale or wrong clock domain.
   * If ahead_ms > 200 → MAX_REASONABLE_AHEAD will reject the TX. */
  {
    uint64_t now_dbg    = bsp_uwb_get_current_time_dw();
    uint64_t ahead_dw   = (expected_final_tx_dw - now_dbg) & DW_MASK_40;
    int32_t  ahead_ms   = (ahead_dw < (DW_MASK_40 / 2ULL))
                          ? (int32_t)(tdma_dw_to_us(ahead_dw) / 1000U)
                          : -(int32_t)(tdma_dw_to_us((now_dbg - expected_final_tx_dw) & DW_MASK_40) / 1000U);
//    RANGING_LOG_D(LOG_OBJECT_CODE_RANGING,
//                  "[ANCHOR%u] RESULT ref: superframe=" DW_FMT " final_rx=" DW_FMT
//                  " expected_final=" DW_FMT " ahead=%ldms",
//                  anchor_id,
//                  DW_ARG(s_tdma_anchor.superframe_start_dw),
//                  DW_ARG(final_rx_ts),
//                  DW_ARG(expected_final_tx_dw),
//                  (long) ahead_ms);
  }

  uint32_t result_offset_us =
    s_tdma_anchor.schedule.final_to_result_delay_us + (my_slot_id * effective_slot_us);
  uint64_t result_tx_time_dw    = (expected_final_tx_dw + tdma_us_to_dw(result_offset_us)) & DW_MASK_40;
  uint64_t result_tx_planned_dw = result_tx_time_dw;
  result_tx_time_dw             = ensure_future_tx(result_tx_time_dw, TDMA_DEFAULT_GUARD_TIME_US);
  if (result_tx_time_dw == 0ULL)
  {
    RLOG_W(LOG_OBJECT_CODE_RANGING, "[ANCHOR] RESULT slot missed (seq=%u slot=%u) - ABORTING TX",
           final_msg->sequence_num, my_slot_id);
    /* Don't return -1 here, let anchor clean up and finish cycle normally */
  }
  else
  {
    /* Pre-compute prediction for RESULT so we can compare after TX. */
    uint64_t result_tx_predicted_dw = predict_delayed_tx_antenna_time(result_tx_time_dw);

    if (bsp_uwb_tx_delayed(&result_msg, sizeof(result_msg), result_tx_time_dw) != BSP_OK)
    {
      RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING,
             "[ANCHOR] Failed to TX RESULT (seq=%u slot=%u off=%luus planned=" DW_FMT ")",
             final_msg->sequence_num, my_slot_id, (unsigned long) result_offset_us, DW_ARG(result_tx_time_dw));
      /* Don't fail - distance already calculated */
    }
    else
    {
      /* Verify actual TX time from DW1000 TX_TIME register.
       * This is the TDMA production health-check: planned vs actual.
       * If delta > 1ms → slot was missed → investigate ensure_future_tx logs above. */
      verify_tx_timing("RESULT", anchor_id, my_slot_id, final_msg->sequence_num, result_tx_time_dw,
                       result_tx_predicted_dw, 0, false);
    }
  }

  /* Keep verbose debug after RESULT TX to avoid missing delayed-TX slot timing. */
  log_dstwr_debug(final_msg->sequence_num, anchor_id, &ts);
  RANGING_LOG_D(LOG_OBJECT_CODE_RANGING, "[ANCHOR] DIST: seq=%u anchor=%u d=%.3fm valid=%u",
                final_msg->sequence_num, anchor_id, s_ctx.result_single.distance_m,
                (unsigned) s_ctx.result_single.valid);

  log_ranging_result(&s_ctx.result_single, "ANCHOR");
  s_ranging_busy = false;
  return 0;
}

static int
ds_twr_tag_tdma(uint8_t num_anchors, const uint8_t *anchor_ids, uint8_t sequence_num, uint32_t rx_timeout_us)
{
  if (s_ranging_busy)
    return -1;
  s_ranging_busy = true;

  /* Keep TAG schedule aligned with current anchor topology. */
  if (!tdma_tag_config_matches(num_anchors, anchor_ids))
  {
    if (tdma_init(&s_tdma_tag, TDMA_ROLE_TAG, 0, num_anchors, anchor_ids) != TDMA_OK)
    {
      s_ranging_busy = false;
      return -1;
    }
    RANGING_LOG_D(LOG_OBJECT_CODE_RANGING, "[TAG] TDMA scheduler (re)init: n=%u", num_anchors);
  }

  tdma_scheduler_t *tdma = &s_tdma_tag;

  /* 1. Send POLL */
  poll_msg_t poll_msg   = { 0 };
  poll_msg.msg_type     = MW_DSTWR_MSG_TYPE_POLL;
  poll_msg.sequence_num = sequence_num;
  poll_msg.tag_id       = 0;
  poll_msg.num_anchors  = num_anchors;
  /* poll_tx_ts NOT in payload - anchor doesn't need it */

  for (uint8_t i = 0; i < num_anchors; i++)
  {
    if (anchor_ids[i] > 0 && anchor_ids[i] <= 8)
    {
      poll_msg.anchor_mask |= (1 << (anchor_ids[i] - 1));
    }
  }

  /* TX POLL (broadcast) */
  if (bsp_uwb_tx(&poll_msg, sizeof(poll_msg)) != BSP_OK)
  {
    RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[TAG] Failed to TX POLL");
    s_ranging_busy = false;
    return -1;
  }

  /* Read T1 */
  uint64_t poll_tx_ts = 0;
  if (bsp_uwb_get_last_tx_timestamp(&poll_tx_ts) != BSP_OK)
  {
    RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[TAG] Failed to read T1 timestamp");
    s_ranging_busy = false;
    return -1;
  }

  /* Sync superframe from actual POLL TX timestamp. */
  tdma_start_superframe(tdma, poll_tx_ts);

  RANGING_LOG_D(LOG_OBJECT_CODE_RANGING, "[TAG] POLL sent (seq=%u, num_anchors=%u, mask=0x%02X)",
                sequence_num, num_anchors, poll_msg.anchor_mask);

  /* 2. Receive responses from anchors */
  uint8_t  response_buf[128];
  uint16_t response_len;
  uint8_t  num_responses = 0;

  struct
  {
    uint8_t  anchor_id;
    uint64_t resp_rx_ts;
    uint64_t poll_rx_ts;
    uint64_t resp_tx_ts;
    int8_t   rssi;
    uint8_t  calib_status;
    bool     valid;
  } anchor_resp[8];
  memset(anchor_resp, 0, sizeof(anchor_resp));

  RANGING_LOG_D(LOG_OBJECT_CODE_RANGING, "[TAG] Waiting for RESP (timeout=%lums)...", rx_timeout_us / 1000);

  /* Listen continuously across the full N-slot RESP phase. */
  uint64_t resp_window_start_dw = bsp_uwb_get_current_time_dw();
  uint64_t resp_window_end_dw =
    tdma_compute_resp_rx_window_end(tdma, anchor_ids, num_anchors, resp_window_start_dw);

  /* Cap RESP window so it does NOT overrun the planned FINAL TX time.
   * Without this cap, the last slot's rx_late_margin pushes the window
   * past FINAL TX, causing TAG to miss its FINAL deadline when not all
   * anchors respond — which then causes all anchors to timeout on FINAL.
   *
   * Use tdma_calculate_final_time() as the single source of truth for when
   * FINAL is scheduled. This is critical after the superframe origin fix:
   * superframe_start_dw is now (T1 - poll_to_resp_delay), so any inline
   * formula that adds poll_to_resp_delay back will be correct, but using
   * the dedicated function avoids divergence if timing params change later. */
  {
    uint64_t final_tx_planned_dw = 0;
    if (tdma_calculate_final_time(tdma, num_anchors, &final_tx_planned_dw) != TDMA_OK)
    {
      /* Fallback must match tdma_calculate_final_time() exactly — including
       * slot_duration_us which accounts for the last RESP payload airtime. */
      uint32_t effective_slot_us = tdma_effective_slot_us(tdma);
      final_tx_planned_dw = tdma->superframe_start_dw
                            + tdma_us_to_dw(tdma->schedule.poll_to_resp_delay_us
                                            + (uint32_t) num_anchors * effective_slot_us
                                            + tdma->schedule.slot_duration_us
                                            + tdma->schedule.resp_to_final_delay_us);
      final_tx_planned_dw &= DW_MASK_40;
    }

    /* Leave processing_margin_us + 500us of headroom before FINAL to build
     * the message, copy timestamps, and call bsp_uwb_tx_delayed(). */
    uint64_t final_tx_headroom_dw =
      (final_tx_planned_dw - tdma_us_to_dw(tdma->schedule.processing_margin_us + 500U))
      & DW_MASK_40;

    if (dw_time_before_deadline(final_tx_headroom_dw, resp_window_end_dw))
    {
      RANGING_LOG_D(LOG_OBJECT_CODE_RANGING, "[TAG] RESP window capped at FINAL headroom (was +%luus over)",
                    (unsigned long) tdma_dw_to_us((resp_window_end_dw - final_tx_headroom_dw) & DW_MASK_40));
      resp_window_end_dw = final_tx_headroom_dw;
    }
  }
  uint8_t slot_mismatch_count = 0;

  if (bsp_uwb_enable_rx(0) != BSP_OK)
  {
    RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[TAG] Failed to enable RX for RESP phase");
    s_ranging_busy = false;
    return -1;
  }

  uint64_t dbg_loop_start_dw = bsp_uwb_get_current_time_dw();

  while (dw_time_before_deadline(bsp_uwb_get_current_time_dw(), resp_window_end_dw)
         && (num_responses < num_anchors))
  {
    bsp_err_t err = bsp_uwb_rx(response_buf, sizeof(response_buf), &response_len);

    if (!(err == BSP_OK && response_len > 0
          && validate_msg_type(response_buf, response_len, MW_DSTWR_MSG_TYPE_RESP)))
    {
      __NOP();
      continue;
    }

    resp_msg_t *resp = (resp_msg_t *) response_buf;
    if (resp->sequence_num != sequence_num)
    {
      continue;
    }

    int matched_index = -1;
    for (uint8_t i = 0; i < num_anchors; i++)
    {
      if (anchor_ids[i] == resp->anchor_id)
      {
        matched_index = (int) i;
        break;
      }
    }
    if (matched_index < 0 || anchor_resp[matched_index].valid)
    {
      continue;
    }

    {
      uint8_t expected_slot_id = (uint8_t) (matched_index + 1U);
      if (resp->slot_id != expected_slot_id)
      {
        slot_mismatch_count++;
        RLOG_W(LOG_OBJECT_CODE_RANGING, "[TAG] RESP slot mismatch: anchor=%u seq=%u slot=%u expected=%u",
               resp->anchor_id, resp->sequence_num, resp->slot_id, expected_slot_id);
      }
    }

    uint64_t resp_poll_rx_ts = 0;
    uint64_t resp_resp_tx_ts = 0;
    memcpy(&resp_poll_rx_ts, &resp->poll_rx_ts, sizeof(resp_poll_rx_ts));
    memcpy(&resp_resp_tx_ts, &resp->resp_tx_ts, sizeof(resp_resp_tx_ts));
    resp_poll_rx_ts &= DW_MASK_40;
    resp_resp_tx_ts &= DW_MASK_40;

    uint64_t resp_rx_ts = 0;
    if (bsp_uwb_get_last_rx_timestamp(&resp_rx_ts) != BSP_OK)
    {
      continue;
    }

    anchor_resp[matched_index].anchor_id  = resp->anchor_id;
    anchor_resp[matched_index].resp_rx_ts = resp_rx_ts;
    anchor_resp[matched_index].poll_rx_ts = resp_poll_rx_ts;
    anchor_resp[matched_index].resp_tx_ts = resp_resp_tx_ts;
    anchor_resp[matched_index].rssi       = (int8_t) resp->rssi_poll;
    anchor_resp[matched_index].calib_status = resp->calib_status;
    anchor_resp[matched_index].valid      = true;

    num_responses++;
    RANGING_LOG_D(LOG_OBJECT_CODE_RANGING, "[TAG] Got RESP from anchor %u (slot %u)", resp->anchor_id,
                  resp->slot_id);
  }

  if (slot_mismatch_count > 0U)
  {
    RLOG_W(LOG_OBJECT_CODE_RANGING, "[TAG] RESP slot mismatches in cycle: %u",
           (unsigned) slot_mismatch_count);
  }

  /* Show exactly what DW time range the loop actually covered vs what was needed. */
  {
    uint64_t dbg_loop_end_dw = bsp_uwb_get_current_time_dw();
    uint32_t covered_us = tdma_dw_to_us((dbg_loop_end_dw - dbg_loop_start_dw) & DW_MASK_40);
    uint32_t start_offset_us = tdma_dw_to_us((dbg_loop_start_dw - tdma->superframe_start_dw) & DW_MASK_40);
    uint32_t end_offset_us   = tdma_dw_to_us((dbg_loop_end_dw   - tdma->superframe_start_dw) & DW_MASK_40);
    uint32_t window_end_offset_us = tdma_dw_to_us((resp_window_end_dw - tdma->superframe_start_dw) & DW_MASK_40);
    uint32_t rx_err_timeout = 0, rx_err_crc = 0, rx_err_phr = 0, rx_err_sync = 0;
    bsp_uwb_get_rx_error_counts(&rx_err_timeout, &rx_err_crc, &rx_err_phr, &rx_err_sync);
    bsp_uwb_reset_rx_error_counts();
    RLOG_I(LOG_OBJECT_CODE_RANGING,
           "[TAG] RESP window: loop=[+%luus..+%luus] covered=%luus window_end=+%luus got=%u/%u "
           "rx_errs(to=%lu crc=%lu phr=%lu sync=%lu)",
           (unsigned long) start_offset_us, (unsigned long) end_offset_us,
           (unsigned long) covered_us,      (unsigned long) window_end_offset_us,
           num_responses, num_anchors,
           (unsigned long) rx_err_timeout, (unsigned long) rx_err_crc,
           (unsigned long) rx_err_phr,     (unsigned long) rx_err_sync);
  }

  if (num_responses == 0)
  {
    RLOG_W(LOG_OBJECT_CODE_RANGING, "[TAG] No RESP received from %u anchors", num_anchors);
    s_ranging_busy = false;
    return -1;
  }

  {
    uint8_t resp_mask = 0;
    for (uint8_t i = 0; i < 8; i++)
    {
      if (anchor_resp[i].valid && anchor_resp[i].anchor_id > 0U && anchor_resp[i].anchor_id <= 8U)
      {
        resp_mask |= (uint8_t) (1U << (anchor_resp[i].anchor_id - 1U));
      }
    }
    RANGING_LOG_D(LOG_OBJECT_CODE_RANGING, "[TAG] Received %u RESP messages (mask=0x%02X)", num_responses,
                  resp_mask);
  }

  /* 3. Send FINAL from TDMA timeline to preserve full-slot ordering. */
  uint64_t final_tx_time_dw = 0;
  if (tdma_calculate_final_time(tdma, num_anchors, &final_tx_time_dw) != TDMA_OK)
  {
    RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[TAG] Failed to calculate FINAL time");
    s_ranging_busy = false;
    return -1;
  }

  final_tx_time_dw = ensure_future_tx(final_tx_time_dw, TDMA_DEFAULT_GUARD_TIME_US);
  if (final_tx_time_dw == 0ULL)
  {
    s_ranging_busy = false;
    return -1;
  }

  /* Put actual antenna-domain delayed TX timestamp in FINAL payload (T5). */
  uint64_t t5_payload = predict_delayed_tx_antenna_time(final_tx_time_dw);

  uint8_t      final_buf[256];
  final_msg_t *final_msg = (final_msg_t *) final_buf;
  memset(final_buf, 0, sizeof(final_buf));

  final_msg->msg_type      = MW_DSTWR_MSG_TYPE_FINAL;
  final_msg->sequence_num  = sequence_num;
  final_msg->tag_id        = 0;
  final_msg->num_responses = num_responses;
  {
    uint64_t poll_tx_payload = poll_tx_ts & DW_MASK_40;
    memcpy(&final_msg->poll_tx_ts, &poll_tx_payload, sizeof(poll_tx_payload));
  }

  uint8_t final_idx  = 0;
  uint8_t final_mask = 0;
  for (uint8_t i = 0; i < 8; i++)
  {
    if (anchor_resp[i].valid)
    {
      uint8_t *entry            = final_buf + sizeof(final_msg_t) + (final_idx * sizeof(final_anchor_data_t));
      uint64_t resp_rx_payload  = anchor_resp[i].resp_rx_ts & DW_MASK_40;
      uint64_t final_tx_payload = t5_payload & DW_MASK_40;

      entry[0] = anchor_resp[i].anchor_id;
      memcpy(entry + 1, &resp_rx_payload, sizeof(resp_rx_payload));
      memcpy(entry + 1 + sizeof(uint64_t), &final_tx_payload, sizeof(final_tx_payload));
      if (anchor_resp[i].anchor_id > 0U && anchor_resp[i].anchor_id <= 8U)
      {
        final_mask |= (uint8_t) (1U << (anchor_resp[i].anchor_id - 1U));
      }
      final_idx++;
    }
  }

  final_msg->anchor_resp_mask = final_mask;

  uint16_t final_len = sizeof(final_msg_t) + (num_responses * sizeof(final_anchor_data_t));

  /* Keep FINAL scheduling path free of verbose logs to reduce timing jitter. */

  if (bsp_uwb_tx_delayed(final_buf, final_len, final_tx_time_dw) != BSP_OK)
  {
    RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[TAG] FINAL TX delayed failed (seq=%u n_resp=%u)",
           sequence_num, num_responses);
    s_ranging_busy = false;
    return -1;
  }

  (void) final_len;

  /* Read T6 */
  uint64_t final_tx_ts = 0;
  if (bsp_uwb_get_last_tx_timestamp(&final_tx_ts) != BSP_OK)
  {
    RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[TAG] Failed to read T6 timestamp");
    s_ranging_busy = false;
    return -1;
  }
  uint64_t t6_actual = final_tx_ts & DW_MASK_40;

  /* 4. Receive RESULT messages from anchors */
  uint8_t  result_buf[128];
  uint16_t result_len;
  uint8_t  max_result_slot = 1U;
  for (uint8_t i = 0; i < 8; i++)
  {
    if (!anchor_resp[i].valid)
    {
      continue;
    }
    tdma_slot_t slot = { 0 };
    if (tdma_get_slot_for_anchor(tdma, anchor_resp[i].anchor_id, &slot) == TDMA_OK)
    {
      if (slot.slot_id > max_result_slot)
      {
        max_result_slot = slot.slot_id;
      }
    }
  }

  /* 4. Receive RESULT messages from anchors */
  if (bsp_uwb_enable_rx(0) != BSP_OK)
  {
    RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[TAG] Failed to enable RX for RESULT");
    s_ranging_busy = false;
    return -1;
  }

  uint64_t result_deadline_dw = tdma_compute_result_rx_window_end(tdma, t6_actual, max_result_slot);
  uint32_t result_timeout_us  = tdma_dw_to_us((result_deadline_dw - t6_actual) & DW_MASK_40);

  RANGING_LOG_D(LOG_OBJECT_CODE_RANGING, "[TAG] Waiting RESULT: expected=%u max_slot=%u timeout=%luus",
                num_responses, max_result_slot, (unsigned long) result_timeout_us);

  s_ctx.result_multi.count        = 0;
  s_ctx.result_multi.sequence_num = sequence_num;

  /* Wait for RESULT from each anchor */
  uint8_t         results_received             = 0;
  uint8_t         result_mask                  = 0;
  uint8_t         result_slot_mismatch_count   = 0;
  static uint32_t s_result_unexpected_log_tick = 0;
  // The result_timeout_dw ensures we don't wait indefinitely if anchors fail to respond
  // but we also break early if we get all expected results before the timeout.
  while (results_received < num_responses
         && dw_time_before_deadline(bsp_uwb_get_current_time_dw(), result_deadline_dw))
  {
    bsp_err_t err = bsp_uwb_rx(result_buf, sizeof(result_buf), &result_len);
    if (err == BSP_OK && result_len > 0
        && validate_msg_type(result_buf, result_len, MW_DSTWR_MSG_TYPE_RESULT))
    {
      result_msg_t *result = (result_msg_t *) result_buf;

      if (result->sequence_num != sequence_num)
        continue;

      /* FIX Bug-C: validate RESULT slot_id (symmetric with RESP slot mismatch check).
       * A mismatched slot_id means the anchor TX'd outside its assigned window,
       * which is the same collision root cause as in the RESP phase. */
      {
        tdma_slot_t exp_slot = { 0 };
        if (tdma_get_slot_for_anchor(tdma, result->anchor_id, &exp_slot) == TDMA_OK)
        {
          if (result->slot_id != exp_slot.slot_id)
          {
            result_slot_mismatch_count++;
            RLOG_W(LOG_OBJECT_CODE_RANGING,
                   "[TAG] RESULT slot mismatch: anchor=%u seq=%u slot=%u expected=%u"
                   " (likely ensure_future_tx pushed TX out of slot)",
                   result->anchor_id, result->sequence_num, result->slot_id, exp_slot.slot_id);
            /* Still accept the result - distance is valid even if slot timing drifted */
          }
        }
      }

      /* Find matching anchor in our response list */
      for (uint8_t i = 0; i < 8; i++)
      {
        if (anchor_resp[i].valid && anchor_resp[i].anchor_id == result->anchor_id)
        {
          bool duplicate_anchor = false;
          for (uint8_t j = 0; j < s_ctx.result_multi.count; j++)
          {
            if (s_ctx.result_multi.results[j].anchor_id == result->anchor_id)
            {
              duplicate_anchor = true;
              break;
            }
          }
          if (duplicate_anchor)
          {
            break;
          }

          /* Store result from anchor */
          sys_ranging_result_t *tag_result = &s_ctx.result_multi.results[s_ctx.result_multi.count];
          tag_result->anchor_id            = result->anchor_id;
          tag_result->distance_m           = result->distance_m;
          tag_result->rssi                 = result->rssi;
          tag_result->calib_status         = anchor_resp[i].calib_status;
          tag_result->valid                = (result->valid == 1);

          /* Store timestamps for reference */
          tag_result->t1 = poll_tx_ts;
          tag_result->t2 = anchor_resp[i].poll_rx_ts;
          tag_result->t3 = anchor_resp[i].resp_tx_ts;
          tag_result->t4 = anchor_resp[i].resp_rx_ts;
          /* FIX Bug-T5: t5 = predicted FINAL TX antenna time (sent in FINAL payload),
           * NOT t6_actual. t5 and t6 are different timestamps:
           *   t5 = FINAL TX timestamp as seen by anchor (predicted, embedded in payload)
           *   t6 = actual FINAL TX timestamp read from DW1000 TX_TIME register after TX.
           * Using t6_actual for both caused DS-TWR self-validation to always show delta=0. */
          tag_result->t5 = t5_payload;
          tag_result->t6 = t6_actual;

          if (result->anchor_id > 0U && result->anchor_id <= 8U)
          {
            result_mask |= (uint8_t) (1U << (result->anchor_id - 1U));
          }

          s_ctx.result_multi.count++;
          results_received++;
          break;
        }
      }
    }
    else if (err == BSP_OK && result_len > 0)
    {
      if ((HAL_GetTick() - s_result_unexpected_log_tick) >= 1000U)
      {
        RLOG_W(LOG_OBJECT_CODE_RANGING, "[TAG] RESULT phase unexpected frame: type=0x%02X len=%u",
               (unsigned) result_buf[0], (unsigned) result_len);
        s_result_unexpected_log_tick = HAL_GetTick();
      }
    }
    __NOP();
  }

  /* Diagnostic: report why the RESULT loop exited.
   * TIMEOUT  → anchors TX'd but TAG window expired (timing mismatch or RF loss).
   * EARLY_EXIT → all expected RESULTs received, window closed early (healthy). */
  {
    uint64_t now_dw    = bsp_uwb_get_current_time_dw();
    bool     timed_out = !dw_time_before_deadline(now_dw, result_deadline_dw);
    uint32_t rem_us    = timed_out ? 0U
      : tdma_dw_to_us((result_deadline_dw - now_dw) & DW_MASK_40);
    RLOG_W(LOG_OBJECT_CODE_RANGING,
           "[TAG] RESULT loop: got=%u/%u mask=0x%02X %s remain=%luus",
           results_received, num_responses, result_mask,
           timed_out ? "TIMEOUT" : "EARLY_EXIT", (unsigned long) rem_us);
  }


  if (result_slot_mismatch_count > 0U)
  {
    RLOG_W(LOG_OBJECT_CODE_RANGING,
           "[TAG] RESULT slot mismatches in cycle: %u (Bug-B indicator: increase final_to_result_delay?)",
           (unsigned) result_slot_mismatch_count);
  }

  {
    static uint32_t s_t5_mismatch_log_tick = 0;
    int64_t         t5_diff                = (int64_t) t6_actual - (int64_t) (t5_payload & DW_MASK_40);
    if (t5_diff < -1024 || t5_diff > 1024)
    {
      if ((HAL_GetTick() - s_t5_mismatch_log_tick) >= 1000U)
      {
        RLOG_W(LOG_OBJECT_CODE_RANGING,
               "[TAG] T5 pred/actual mismatch: pred=" DW_FMT " actual=" DW_FMT " diff=%ld",
               DW_ARG(t5_payload), DW_ARG(t6_actual), (long) t5_diff);
        s_t5_mismatch_log_tick = HAL_GetTick();
      }
    }
  }

  if (s_ctx.result_multi.count == 0)
  {
    RLOG_W(LOG_OBJECT_CODE_RANGING, "[TAG] No RESULT received (got 0/%u after FINAL, cfg=%u)", num_responses,
           num_anchors);
    s_ranging_busy = false;
    return -1;
  }

  RANGING_LOG_D(LOG_OBJECT_CODE_RANGING, "[TAG] Received %u RESULT messages (mask=0x%02X)",
                s_ctx.result_multi.count, result_mask);
  s_ctx.sequence_num     = sequence_num;
  s_ctx.state            = STATE_TAG_RANGING_TDMA;
  s_ctx.state_entry_tick = HAL_GetTick();
  s_stats.total_count++;
  s_ranging_busy = false;

  return SYS_RANGING_OK;
}
/* Public functions ------------------------------------------------------ */

sys_ranging_err_t sys_ranging_tag_start_tdma(uint8_t        num_anchors,
                                             const uint8_t *anchor_ids,
                                             uint8_t        sequence_num,
                                             uint32_t       rx_timeout_ms)
{
  (void) rx_timeout_ms;

  if (s_ctx.state != STATE_IDLE)
    return SYS_RANGING_ERR_BUSY;
  if (num_anchors == 0 || num_anchors > 8 || !anchor_ids)
    return SYS_RANGING_ERR_PARAM;

  state_machine_reset();
  s_ctx.sequence_num     = sequence_num;
  s_ctx.state            = STATE_TAG_RANGING_TDMA;
  s_ctx.state_entry_tick = HAL_GetTick();
  s_stats.total_count++;

  return SYS_RANGING_OK;
}

#if UWB_EVENT_DRIVEN

sys_ranging_err_t sys_ranging_tag_process_tdma(uint8_t num_anchors, const uint8_t *anchor_ids, uint32_t rx_timeout_ms)
{
  if (s_ctx.state == STATE_IDLE) return SYS_RANGING_ERR_NOT_STARTED;
  if (s_ctx.state != STATE_TAG_RANGING_TDMA) return SYS_RANGING_ERR;
  
  uint32_t timeout_ms = (rx_timeout_ms == 0) ? 100 : rx_timeout_ms;
  /* Use the specified timeout for the entire TDMA cycle plus some overhead */
  if (HAL_GetTick() - s_ctx.state_entry_tick > timeout_ms + 500) {
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
          s_sys_ranging_ev.step = SYS_RANGING_EV_TAG_WAIT_POLL_TX;
          break;
      }
      case SYS_RANGING_EV_TAG_WAIT_POLL_TX: {
          if (has_evt && evt.type == BSP_UWB_EVENT_TX_DONE) {
              s_sys_ranging_ev.poll_tx_ts = evt.tx_ts; // DW1000 caches exact TX time
              tdma_start_superframe(&s_tdma_tag, s_sys_ranging_ev.poll_tx_ts);
              s_sys_ranging_ev.deadline_dw = tdma_compute_resp_rx_window_end(&s_tdma_tag, anchor_ids, num_anchors, bsp_uwb_get_current_time_dw());
              
              /* Cap RESP window so it does NOT overrun the planned FINAL TX time */
              uint64_t final_tx_planned_dw = 0;
              if (tdma_calculate_final_time(&s_tdma_tag, num_anchors, &final_tx_planned_dw) == TDMA_OK) {
                  uint64_t final_tx_headroom_dw = (final_tx_planned_dw - tdma_us_to_dw(s_tdma_tag.schedule.processing_margin_us + 500U)) & DW_MASK_40;
                  if (dw_time_before_deadline(final_tx_headroom_dw, s_sys_ranging_ev.deadline_dw)) {
                      s_sys_ranging_ev.deadline_dw = final_tx_headroom_dw;
                  }
              }
              
              bsp_uwb_enable_rx(0);
              s_sys_ranging_ev.step = SYS_RANGING_EV_TAG_WAIT_RESP;
          }
          break;
      }
      case SYS_RANGING_EV_TAG_WAIT_RESP: {
          if (has_evt && evt.type == BSP_UWB_EVENT_RX_OK && validate_msg_type(evt.rx_data, evt.rx_len, MW_DSTWR_MSG_TYPE_RESP)) {
              resp_msg_t *resp = (resp_msg_t*)evt.rx_data;
              if (resp->sequence_num == s_ctx.sequence_num) {
                  int idx = -1;
                  for (uint8_t i = 0; i < num_anchors; i++) if (anchor_ids[i] == resp->anchor_id) { idx = i; break; }
                  if (idx >= 0 && !s_sys_ranging_ev.anchor_resp[idx].valid) {
                      s_sys_ranging_ev.anchor_resp[idx].anchor_id = resp->anchor_id;
                      s_sys_ranging_ev.anchor_resp[idx].resp_rx_ts = evt.rx_ts;
                      memcpy(&s_sys_ranging_ev.anchor_resp[idx].poll_rx_ts, &resp->poll_rx_ts, sizeof(uint64_t));
                      memcpy(&s_sys_ranging_ev.anchor_resp[idx].resp_tx_ts, &resp->resp_tx_ts, sizeof(uint64_t));
                      s_sys_ranging_ev.anchor_resp[idx].poll_rx_ts &= DW_MASK_40;
                      s_sys_ranging_ev.anchor_resp[idx].resp_tx_ts &= DW_MASK_40;
                      s_sys_ranging_ev.anchor_resp[idx].rssi = evt.rx_rssi;
                        s_sys_ranging_ev.anchor_resp[idx].calib_status = resp->calib_status;
                      s_sys_ranging_ev.anchor_resp[idx].valid = true;
                      s_sys_ranging_ev.num_responses++;
                      RANGING_LOG_D(LOG_OBJECT_CODE_RANGING, "[TAG] Got RESP from anchor %u", resp->anchor_id);
                  }
              }
          }
          if (has_evt) bsp_uwb_enable_rx(0); // keep listening
          
          if (!dw_time_before_deadline(bsp_uwb_get_current_time_dw(), s_sys_ranging_ev.deadline_dw) || s_sys_ranging_ev.num_responses >= num_anchors) {
              if (s_sys_ranging_ev.num_responses == 0) {
                  state_machine_reset(); return SYS_RANGING_ERR;
              }
              // Send FINAL
              uint64_t final_tx_time_dw = 0;
              tdma_calculate_final_time(&s_tdma_tag, num_anchors, &final_tx_time_dw);
              final_tx_time_dw = ensure_future_tx(final_tx_time_dw, TDMA_DEFAULT_GUARD_TIME_US);
              if (final_tx_time_dw == 0ULL) {
                  state_machine_reset();
                  return SYS_RANGING_ERR;
              }
              uint64_t t5_payload = predict_delayed_tx_antenna_time(final_tx_time_dw);
              
              uint8_t final_buf[256] = {0};
              final_msg_t *fmsg = (final_msg_t*)final_buf;
              fmsg->msg_type = MW_DSTWR_MSG_TYPE_FINAL;
              fmsg->sequence_num = s_ctx.sequence_num;
              fmsg->num_responses = s_sys_ranging_ev.num_responses;
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
                  state_machine_reset();
                  return SYS_RANGING_ERR;
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
              if (has_evt && evt.type == BSP_UWB_EVENT_RX_OK &&
                  validate_msg_type(evt.rx_data, evt.rx_len, MW_DSTWR_MSG_TYPE_RESULT)) {
                  result_msg_t *res = (result_msg_t*)evt.rx_data;
                  if (res->sequence_num == s_ctx.sequence_num &&
                      s_ctx.result_multi.count < 8) {
                      sys_ranging_result_t *tr = &s_ctx.result_multi.results[s_ctx.result_multi.count];
                      tr->anchor_id   = res->anchor_id;
                      tr->distance_m  = res->distance_m;
                      tr->rssi        = res->rssi;
                        tr->calib_status = SYS_CALIB_STATUS_NORMAL;
                        for (uint8_t k = 0; k < 8; k++) {
                          if (s_sys_ranging_ev.anchor_resp[k].valid &&
                            s_sys_ranging_ev.anchor_resp[k].anchor_id == res->anchor_id) {
                            tr->calib_status = s_sys_ranging_ev.anchor_resp[k].calib_status;
                            break;
                          }
                        }
                      tr->valid       = (res->valid == 1);
                      s_ctx.result_multi.count++;
                  }
              }

              /* Enable RX for remaining RESULTs */
              if (s_ctx.result_multi.count < s_sys_ranging_ev.num_responses) {
                  bsp_uwb_enable_rx(0);
              }
              s_sys_ranging_ev.step = SYS_RANGING_EV_TAG_WAIT_RESULT;
          }
          break;
      }
      case SYS_RANGING_EV_TAG_WAIT_RESULT: {
          bool result_received = false;
          if (has_evt && evt.type == BSP_UWB_EVENT_RX_OK && validate_msg_type(evt.rx_data, evt.rx_len, MW_DSTWR_MSG_TYPE_RESULT)) {
              result_msg_t *res = (result_msg_t*)evt.rx_data;
              if (res->sequence_num == s_ctx.sequence_num) {
                  sys_ranging_result_t *tr = &s_ctx.result_multi.results[s_ctx.result_multi.count];
                  tr->anchor_id = res->anchor_id;
                  tr->distance_m = res->distance_m;
                  tr->rssi = res->rssi;
                    tr->calib_status = SYS_CALIB_STATUS_NORMAL;
                    for (uint8_t k = 0; k < 8; k++) {
                      if (s_sys_ranging_ev.anchor_resp[k].valid &&
                        s_sys_ranging_ev.anchor_resp[k].anchor_id == res->anchor_id) {
                        tr->calib_status = s_sys_ranging_ev.anchor_resp[k].calib_status;
                        break;
                      }
                    }
                  tr->valid = (res->valid == 1);
                  s_ctx.result_multi.count++;
                  result_received = true;
              }
          }
          /* Re-enable RX only when needed:
           * - After a valid RESULT if we still expect more
           * - On error/timeout events (not after good RX to avoid dwt_forcetrxoff()
           *   killing the next anchor's RESULT that is already arriving) */
          if (has_evt) {
              bool need_more = (s_ctx.result_multi.count < s_sys_ranging_ev.num_responses);
              if (result_received && need_more) {
                  bsp_uwb_enable_rx(0);
              } else if (!result_received) {
                  /* Error, timeout, or non-RESULT frame — re-enable RX */
                  bsp_uwb_enable_rx(0);
              }
              /* If result_received && !need_more: deadline check below will exit */
          }

          if (!dw_time_before_deadline(bsp_uwb_get_current_time_dw(), s_sys_ranging_ev.deadline_dw) || s_ctx.result_multi.count >= s_sys_ranging_ev.num_responses) {
              s_ctx.has_result = true;
              s_ctx.state = STATE_TAG_COMPLETE;
              for (uint8_t i = 0; i < s_ctx.result_multi.count; i++) {
                  log_ranging_result(&s_ctx.result_multi.results[i], "TAG");
              }
              RANGING_LOG_D(LOG_OBJECT_CODE_RANGING, "[TAG] Received %u RESULT messages", s_ctx.result_multi.count);
              s_sys_ranging_ev.step = SYS_RANGING_EV_SYS_IDLE;
              return SYS_RANGING_OK;
          }
          break;
      }
      default: break;
  }
  return SYS_RANGING_ERR_BUSY;
}

typedef struct {
    bool is_tracking;
    uint32_t track_failures;
    uint64_t next_poll_dw;
    uint32_t sleep_until_tick;
    uint32_t sniffer_start_tick;
    bool sniffer_rx_on;
} anchor_track_state_t;
static anchor_track_state_t s_anchor_track = {0};

sys_ranging_err_t sys_ranging_anchor_process_tdma(uint8_t num_anchors, const uint8_t *anchor_ids, uint32_t rx_timeout_ms)
{
  if (s_ctx.state == STATE_IDLE) return SYS_RANGING_ERR_NOT_STARTED;
  if (s_ctx.state != STATE_ANCHOR_RANGING_TDMA) return SYS_RANGING_ERR;
  
  uint32_t timeout_ms = (rx_timeout_ms == 0) ? 100 : rx_timeout_ms;
  uint32_t sm_watchdog_ms = timeout_ms + 500; /* Generous overarching state machine watchdog */
  
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
      if (s_anchor_track.is_tracking && mode != ANCHOR_POWER_MODE_PERFORMANCE) {
          uint64_t rx_start = (s_anchor_track.next_poll_dw - tdma_us_to_dw(5000U)) & DW_MASK_40;
          uint64_t now_dw   = bsp_uwb_get_current_time_dw();
          uint64_t ahead_dw = (rx_start - now_dw) & DW_MASK_40;
          /* If rx_start is in the past, ahead_dw wraps to > half of 40-bit range.
           * Fall back to discovery immediately instead of spamming TOO LATE. */
          if (ahead_dw >= (DW_MASK_40 / 2ULL)) {
              /* rx_start is in the past. Try advancing next_poll_dw by multiples
               * of ranging_period to re-sync without dropping to discovery. */
              uint32_t period_ms = sys_config_get()->uwb.ranging_period_ms;
              uint64_t period_dw = tdma_us_to_dw((uint32_t)period_ms * 1000U);
              bool recovered = false;
              if (period_dw > 0) {
                  for (uint8_t attempt = 0; attempt < 8; attempt++) {
                      s_anchor_track.next_poll_dw = (s_anchor_track.next_poll_dw + period_dw) & DW_MASK_40;
                      rx_start  = (s_anchor_track.next_poll_dw - tdma_us_to_dw(5000U)) & DW_MASK_40;
                      ahead_dw  = (rx_start - now_dw) & DW_MASK_40;
                      if (ahead_dw < (DW_MASK_40 / 2ULL)) {
                          recovered = true;
                          break;
                      }
                  }
              }
              if (recovered) {
                  RLOG_W(LOG_OBJECT_CODE_RANGING, "[ANCHOR] Tracking re-synced (+%u periods)", (unsigned)1);
                  bsp_uwb_enable_rx_delayed(rx_start, 0);
                  /* sleep_until_tick must cover the ACTUAL time until rx_start, not just
                   * one period. If we advanced N periods (e.g. 4 × 150ms = 600ms), setting
                   * sleep_until_tick = now+150ms causes track_failures to fire before
                   * the delayed RX window even opens, abandoning tracking. */
                  uint32_t ahead_ms = tdma_dw_to_us(ahead_dw) / 1000U;
                  s_anchor_track.sleep_until_tick = HAL_GetTick() + ahead_ms + period_ms + 10;
              } else {
                  RLOG_W(LOG_OBJECT_CODE_RANGING, "[ANCHOR] Tracking RX window stale, switching to DISCOVERY");
                  s_anchor_track.is_tracking = false;
                  s_anchor_track.sniffer_start_tick = HAL_GetTick();
                  s_anchor_track.sniffer_rx_on = true;
                  bsp_uwb_enable_rx(0);
              }
          } else {
              bsp_uwb_enable_rx_delayed(rx_start, 0);
              s_anchor_track.sleep_until_tick = HAL_GetTick() + sys_config_get()->uwb.ranging_period_ms + 10;
          }
      } else {
          s_anchor_track.is_tracking = false;
          s_anchor_track.sniffer_start_tick = HAL_GetTick();
          s_anchor_track.sniffer_rx_on = true;
          bsp_uwb_enable_rx(0);
      }
  }
  
  bsp_uwb_event_t evt;
  bool has_evt = bsp_uwb_get_event(&evt);
  
  switch(s_sys_ranging_ev.step) {
      case SYS_RANGING_EV_ANCHOR_WAIT_POLL: {
          if (has_evt && evt.type == BSP_UWB_EVENT_RX_OK && validate_msg_type(evt.rx_data, evt.rx_len, MW_DSTWR_MSG_TYPE_POLL)) {
              poll_msg_t *poll = (poll_msg_t*)evt.rx_data;
              s_ctx.sequence_num = poll->sequence_num;
              s_sys_ranging_ev.poll_rx_ts = evt.rx_ts;
              s_sys_ranging_ev.poll_rssi_dbm = evt.rx_rssi;
              s_ctx.result_single.calib_status = SYS_CALIB_STATUS_NORMAL;
              tdma_sync_to_poll(&s_tdma_anchor, s_sys_ranging_ev.poll_rx_ts);
              
              if (!s_anchor_track.is_tracking) {
                  RLOG_I(LOG_OBJECT_CODE_RANGING, "[ANCHOR] System switch to TRACKING mode");
              }
              
              s_anchor_track.is_tracking = true;
              s_anchor_track.track_failures = 0;
              s_anchor_track.next_poll_dw = (evt.rx_ts + tdma_us_to_dw(sys_config_get()->uwb.ranging_period_ms * 1000ULL)) & DW_MASK_40;
              
              uint64_t rtx_dw=0;
              tdma_calculate_response_time(&s_tdma_anchor, s_ctx.anchor_id, &rtx_dw);
              rtx_dw = ensure_future_tx(rtx_dw, TDMA_DEFAULT_GUARD_TIME_US);
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
              rmsg.rssi_poll = (uint8_t)s_sys_ranging_ev.poll_rssi_dbm;
              rmsg.calib_status = (uint8_t) s_calib_status;
              
              s_sys_ranging_ev.planned_tx_dw = rtx_dw;
              if (bsp_uwb_tx_delayed(&rmsg, sizeof(rmsg), rtx_dw) != BSP_OK) {
                  state_machine_reset();
                  s_sys_ranging_ev.step = SYS_RANGING_EV_SYS_IDLE;
                  return SYS_RANGING_ERR;
              }
              s_sys_ranging_ev.step = SYS_RANGING_EV_ANCHOR_WAIT_RESP_TX;
          } else {
              uint8_t mode = (uint8_t)sys_config_get()->uwb.power_mode;
              if (s_anchor_track.is_tracking && mode != ANCHOR_POWER_MODE_PERFORMANCE) {
                  if (HAL_GetTick() > s_anchor_track.sleep_until_tick) {
                      s_anchor_track.track_failures++;
                      if (s_anchor_track.track_failures > 3) {
                          s_anchor_track.is_tracking = false; // Fallback to discovery
                          RLOG_I(LOG_OBJECT_CODE_RANGING, "[ANCHOR] System switch to DISCOVERY mode (timeout)");
                          s_anchor_track.sniffer_rx_on = false; // Trigger new cycle next tick
                      } else {
                          // Try next cycle
                          s_anchor_track.next_poll_dw = (s_anchor_track.next_poll_dw + tdma_us_to_dw(sys_config_get()->uwb.ranging_period_ms * 1000ULL)) & DW_MASK_40;
                          uint64_t rx_start = (s_anchor_track.next_poll_dw - tdma_us_to_dw(5000U)) & DW_MASK_40;
                          bsp_uwb_enable_rx_delayed(rx_start, 0);
                          s_anchor_track.sleep_until_tick = HAL_GetTick() + sys_config_get()->uwb.ranging_period_ms + 10;
                      }
                  } else if (has_evt) {
                      // Spurious event during tracking window, keep listening immediately
                      bsp_uwb_enable_rx(0);
                  }
              } else if (!s_anchor_track.is_tracking && mode != ANCHOR_POWER_MODE_PERFORMANCE) {
                  uint32_t discovery_on_ms = 40;
                  uint32_t discovery_interval_ms = 143;
                  if (mode == ANCHOR_POWER_MODE_ECO) discovery_interval_ms = 307;
                  else if (mode == ANCHOR_POWER_MODE_DEEP_ECO) discovery_interval_ms = 503;
                  
                  uint32_t elapsed = HAL_GetTick() - s_anchor_track.sniffer_start_tick;
                  if (elapsed >= discovery_interval_ms) {
                      s_anchor_track.sniffer_start_tick = HAL_GetTick();
                      bsp_uwb_enable_rx(0);
                      s_anchor_track.sniffer_rx_on = true;
                  } else if (s_anchor_track.sniffer_rx_on && elapsed >= discovery_on_ms) {
                      bsp_uwb_idle();
                      s_anchor_track.sniffer_rx_on = false;
                  } else if (has_evt) {
                      if (s_anchor_track.sniffer_rx_on) bsp_uwb_enable_rx(0);
                  }
              } else if (has_evt) {
                  bsp_uwb_enable_rx(0);
              }
          }
          break;
      }
      case SYS_RANGING_EV_ANCHOR_WAIT_RESP_TX: {
          if (has_evt && evt.type == BSP_UWB_EVENT_TX_DONE) {
              s_sys_ranging_ev.resp_tx_ts = evt.tx_ts & DW_MASK_40;
              verify_tx_timing("RESP", s_ctx.anchor_id, s_sys_ranging_ev.my_slot_id, s_ctx.sequence_num, s_sys_ranging_ev.planned_tx_dw, s_sys_ranging_ev.predicted_tx_dw, evt.tx_ts, true);
              s_sys_ranging_ev.step = SYS_RANGING_EV_ANCHOR_WAIT_FINAL;
              
              uint64_t expected_final_dw = 0;
              tdma_calculate_final_time(&s_tdma_anchor, num_anchors, &expected_final_dw);
              uint64_t rx_start_dw = (expected_final_dw - tdma_us_to_dw(1000U)) & DW_MASK_40;
              
                if (sys_config_get()->uwb.power_mode == ANCHOR_POWER_MODE_PERFORMANCE) {
                  bsp_uwb_enable_rx(0);
              } else {
                  bsp_uwb_enable_rx_delayed(rx_start_dw, 0);
              }
          }
          break;
      }
      case SYS_RANGING_EV_ANCHOR_WAIT_FINAL: {
          if (has_evt && evt.type == BSP_UWB_EVENT_RX_OK && validate_msg_type(evt.rx_data, evt.rx_len, MW_DSTWR_MSG_TYPE_FINAL)) {
              final_msg_t *fmsg = (final_msg_t*)evt.rx_data;
              if (fmsg->sequence_num == s_ctx.sequence_num) {
                  uint64_t ptx_tag=0; memcpy(&ptx_tag, &fmsg->poll_tx_ts, sizeof(ptx_tag)); ptx_tag &= DW_MASK_40;
                  uint64_t rrx_tag=0, ftx_tag=0;
                  bool found = false;
                  for (uint8_t i=0; i<fmsg->num_responses; i++) {
                      uint8_t *entry = evt.rx_data + sizeof(final_msg_t) + (i*sizeof(final_anchor_data_t));
                      if (entry[0] == s_ctx.anchor_id) {
                          memcpy(&rrx_tag, entry+1, sizeof(rrx_tag));
                          memcpy(&ftx_tag, entry+1+sizeof(uint64_t), sizeof(ftx_tag));
                          found = true; break;
                      }
                  }
                  if (found) {
                      dstwr_timestamps_t ts;
                      ts.t1 = ptx_tag; ts.t2 = s_sys_ranging_ev.poll_rx_ts; ts.t3 = s_sys_ranging_ev.resp_tx_ts;
                      ts.t4 = rrx_tag & DW_MASK_40; ts.t5 = ftx_tag & DW_MASK_40; ts.t6 = evt.rx_ts;
                      float dist = calculate_distance(&ts);
                      s_ctx.result_single.distance_m = dist;
                      s_ctx.result_single.anchor_id = s_ctx.anchor_id;
                      s_ctx.result_single.rssi = s_sys_ranging_ev.poll_rssi_dbm;
                      s_ctx.result_single.calib_status = SYS_CALIB_STATUS_NORMAL;
                      s_ctx.result_single.valid = (dist > 0.0f && dist < 100.0f);
                      
                      uint64_t expected_final_tx_dw = 0;
                      if (tdma_calculate_final_time(&s_tdma_anchor, num_anchors, &expected_final_tx_dw) != TDMA_OK) {
                          expected_final_tx_dw = evt.rx_ts;
                      }
                      
                      uint32_t rofs = s_tdma_anchor.schedule.final_to_result_delay_us + (s_sys_ranging_ev.my_slot_id * tdma_effective_slot_us(&s_tdma_anchor));
                      uint64_t res_tx_dw = (expected_final_tx_dw + tdma_us_to_dw(rofs)) & DW_MASK_40;
                      res_tx_dw = ensure_future_tx(res_tx_dw, TDMA_DEFAULT_GUARD_TIME_US);
                      if (res_tx_dw == 0ULL) {
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
                      res.rssi = (int8_t)s_sys_ranging_ev.poll_rssi_dbm; // Pass old one
                      
                      s_sys_ranging_ev.predicted_tx_dw = predict_delayed_tx_antenna_time(res_tx_dw);
                      s_sys_ranging_ev.planned_tx_dw = res_tx_dw;
                      if (bsp_uwb_tx_delayed(&res, sizeof(res), res_tx_dw) != BSP_OK) {
                          state_machine_reset();
                          s_sys_ranging_ev.step = SYS_RANGING_EV_SYS_IDLE;
                          return SYS_RANGING_ERR;
                      }
                      log_dstwr_debug(s_ctx.sequence_num, s_ctx.anchor_id, &ts);
                      RANGING_LOG_D(LOG_OBJECT_CODE_RANGING, "[ANCHOR] DIST: seq=%u anchor=%u d=%.3fm valid=%u",
                                    s_ctx.sequence_num, s_ctx.anchor_id, s_ctx.result_single.distance_m,
                                    (unsigned) s_ctx.result_single.valid);
                      s_sys_ranging_ev.step = SYS_RANGING_EV_ANCHOR_WAIT_RESULT_TX;
                  }
              }
          } else if (has_evt) {
              bsp_uwb_enable_rx(0);
          }
          break;
      }
      case SYS_RANGING_EV_ANCHOR_WAIT_RESULT_TX: {
          if (has_evt && evt.type == BSP_UWB_EVENT_TX_DONE) {
              verify_tx_timing("RESULT", s_ctx.anchor_id, s_sys_ranging_ev.my_slot_id, s_ctx.sequence_num, s_sys_ranging_ev.planned_tx_dw, s_sys_ranging_ev.predicted_tx_dw, evt.tx_ts, true);
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
#else
sys_ranging_err_t sys_ranging_tag_process_tdma(uint8_t num_anchors, const uint8_t *anchor_ids, uint32_t rx_timeout_ms)
{
  if (s_ctx.state == STATE_IDLE)
    return SYS_RANGING_ERR_NOT_STARTED;
  if (s_ctx.state != STATE_TAG_RANGING_TDMA)
    return SYS_RANGING_ERR;

  uint32_t timeout_ms = (rx_timeout_ms == 0) ? 100 : rx_timeout_ms;
  if (HAL_GetTick() - s_ctx.state_entry_tick > timeout_ms)
  {
    state_machine_reset();
    return SYS_RANGING_ERR_TIMEOUT;
  }

  int ret = ds_twr_tag_tdma(num_anchors, anchor_ids, s_ctx.sequence_num, rx_timeout_ms * 1000);

  if (ret == 0)
  {
    s_ctx.has_result = true;
    s_ctx.state      = STATE_TAG_COMPLETE;
    for (uint8_t i = 0; i < s_ctx.result_multi.count; i++)
    {
      log_ranging_result(&s_ctx.result_multi.results[i], "TAG");
    }
    return SYS_RANGING_OK;
  }
  else
  {
    RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[TAG] TDMA Error");
    s_stats.error_count++;
    state_machine_reset();
    return SYS_RANGING_ERR;
  }
}

sys_ranging_err_t sys_ranging_anchor_process_tdma(uint8_t num_anchors, const uint8_t *anchor_ids, uint32_t rx_timeout_ms)
{
  if (s_ctx.state == STATE_IDLE)
    return SYS_RANGING_ERR_NOT_STARTED;
  if (s_ctx.state != STATE_ANCHOR_RANGING_TDMA)
    return SYS_RANGING_ERR;

  uint32_t timeout_ms = (rx_timeout_ms == 0) ? 100 : rx_timeout_ms;
  int      ret        = ds_twr_anchor_tdma(s_ctx.anchor_id, num_anchors, anchor_ids, timeout_ms * 1000);

  if (ret == -2)
  {
    /* Normal: no POLL in this window. Keep state machine armed. */
    s_ctx.state_entry_tick = HAL_GetTick();
    return SYS_RANGING_ERR_BUSY;
  }

  if (ret == 0)
  {
    s_ctx.has_result = true;
    s_ctx.state      = STATE_ANCHOR_COMPLETE;
    return SYS_RANGING_OK;
  }
  else
  {
    RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[ANCHOR] TDMA Error");
    s_stats.error_count++;
    state_machine_reset();
    return SYS_RANGING_ERR;
  }
}
#endif

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
    RLOG_W(LOG_OBJECT_CODE_RANGING, "[ANCHOR] start busy: state=%d entry=%lu now=%lu", (int) s_ctx.state,
           (unsigned long) s_ctx.state_entry_tick, (unsigned long) HAL_GetTick());
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

void sys_ranging_reset_stats(void)
{
  s_stats.total_count   = 0;
  s_stats.success_count = 0;
  s_stats.error_count   = 0;
}
