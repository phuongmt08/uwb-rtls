/**
 * @file       mw_ds_twr.c
 * @copyright
 * @license
 * @version    2.2.1
 * @date       2025-12-28
 * @author     Phuong Mai
 * @example    None
 */
#include "mw_ds_twr.h"
#include <stddef.h>
#include <string.h>
#include "sys_config.h"
#include "sys_logger.h"
/* Private defines ---------------------------------------------------- */
#define DW1000_REG_RX_TIME     (0x15)
#define DW1000_REG_TX_TIME     (0x17)
#define TIMESTAMP_40BIT_MASK   (0x000000FFFFFFFFFFULL)
#define RX_BUFFER_SIZE         (128u)

#define DWT_TIME_UNITS (1.0/499.2e6/128.0)
#define SPEED_OF_LIGHT 299792458.0


/* Inter-message delays to ensure receiver is ready */
#define INTER_MSG_DELAY_MS     (2)   // 2ms delay (DW1000 RX turnaround ~300us + margin)

/* DW1000 TX delay constraints  */
#define DW1000_TURNAROUND_US   (300)  // TX->RX or RX->TX switching time (~200-300us)
#define MCU_PROCESSING_US      (500)  // MCU processing + frame preparation + logging
#define ANTENNA_DELAY_US       (100)  // Antenna delay compensation
#define SAFETY_MARGIN_US       (1100) // Safety margin for clock drift & jitter & OS delays

/* Total minimum delay = 300 + 500 + 100 + 1100 = 2000us = 2ms */
#define MIN_FINAL_TX_DELAY_US  (DW1000_TURNAROUND_US + MCU_PROCESSING_US + ANTENNA_DELAY_US + SAFETY_MARGIN_US)
#define FINAL_TX_DELAY_US      (5000) // 3ms - safe margin for MCU processing
#define CORRECTION_TX_DELAY_US (5000) // 1ms gap between FINAL and CORRECTION

#define WAIT_FINAL_TIMEOUT_US  (30000)  // 15ms - Tag sends FINAL after 3ms delay
#define WAIT_RESULT_TIMEOUT_US (30000)  // 15ms - Anchor sends RESULT quickly after FINAL

#define CHECK_PARAM(cond, ret) do { if (!(cond)) return (ret); } while(0)

typedef enum {
  RANGING_STATE_IDLE = 0,           // Ready to start new ranging
  RANGING_STATE_POLL_SENT,          // Waiting for RESPONSE
  RANGING_STATE_RESP_RECEIVED,      // Waiting for RESULT (HAVE_TX_DELAY) or sending FINAL (no HAVE_TX_DELAY)
  RANGING_STATE_FINAL_SENT,         // Waiting for CORRECTION (HAVE_TX_DELAY)
  RANGING_STATE_CORRECTION_SENT,    // Waiting for RESULT (HAVE_TX_DELAY)
  RANGING_STATE_COMPLETE            // Sequence finished, can start new one
} ranging_state_t;

/* Global state tracker (in real implementation, should be per-tag context) */
static ranging_state_t g_ranging_state = RANGING_STATE_IDLE;

/* Helper: Check if ready to start new ranging sequence */
static inline bool can_start_new_ranging(void) {
  return (g_ranging_state == RANGING_STATE_IDLE || 
          g_ranging_state == RANGING_STATE_COMPLETE);
}

/* 40-bit timestamp arithmetic */
static inline uint64_t ts40_add(uint64_t ts, uint32_t dly)
{
  return (ts + dly) & TIMESTAMP_40BIT_MASK;
}

static inline uint64_t ts40_sub(uint64_t ts, uint32_t dly)
{
  return (ts - dly) & TIMESTAMP_40BIT_MASK;
}

static inline bool is_valid_hal(const mw_dstwr_hal_t *hal);

/* ====================================================================
 * TAG IMPLEMENTATION (WITH STATE GUARD)
 * ==================================================================== */
mw_dstwr_err_t mw_dstwr_execute_tag(const mw_dstwr_config_t *config,
                                    mw_dstwr_result_t *result)
{
  CHECK_PARAM(config && config->hal, MW_DSTWR_ERR_PARAM);
  CHECK_PARAM(is_valid_hal(config->hal), MW_DSTWR_ERR_PARAM);

  if (!can_start_new_ranging()) {
    RLOG_W(LOG_OBJECT_CODE_RANGING, "[TAG] REJECTED: Previous ranging not complete (state=%d)", g_ranging_state);
    return MW_DSTWR_ERR_BUSY;  // Return busy error instead of proceeding
  }

  sys_config_t *sys_cfg = sys_config_get();
  const mw_dstwr_hal_t *hal = config->hal;
  uint64_t t1 = 0, t4 = 0, t5 = 0;
  uint8_t rx_buffer[RX_BUFFER_SIZE];
  uint16_t rx_length = 0;

  /* Step 1: Send POLL */
  mw_dstwr_poll_msg_t poll_msg = {
    .msg_type = MW_DSTWR_MSG_TYPE_POLL,
    .sequence_num = config->sequence_num,
    .target_anchor = config->target_anchor_id,  /* Target specific anchor or 0xFF for any */
    .rssi_last = 0,  /* Could store last RSSI for diagnostics */
    .padding = {0}
  };

  if (hal->tx(&poll_msg, sizeof(poll_msg)) != 0) {
    RLOG_W(LOG_OBJECT_CODE_RANGING, "[TAG] POLL TX FAILED!");
    g_ranging_state = RANGING_STATE_IDLE;  // Reset state on failure
    return MW_DSTWR_ERR;
  }

  g_ranging_state = RANGING_STATE_POLL_SENT;  // Update state

  /* DW1000 automatically completes TX before timestamp read */
  if (hal->read_timestamp(DW1000_REG_TX_TIME, 0x00, &t1) != 0) {
    g_ranging_state = RANGING_STATE_IDLE;  // Reset on error
    return MW_DSTWR_ERR;
  }

  /* Step 2: Wait for RESPONSE with INCREASED timeout (30ms instead of 15ms) */
  if (hal->rx_with_timeout(rx_buffer, sizeof(rx_buffer), &rx_length, WAIT_FINAL_TIMEOUT_US) != 0) {
    g_ranging_state = RANGING_STATE_IDLE;  // Reset on timeout
    return MW_DSTWR_ERR_TIMEOUT;
  }

  if (!mw_dstwr_validate_message(rx_buffer, rx_length, MW_DSTWR_MSG_TYPE_RESP, config->sequence_num)) {
    g_ranging_state = RANGING_STATE_IDLE;  // Reset on invalid message
    return MW_DSTWR_ERR_INVALID_MSG;
  }

  g_ranging_state = RANGING_STATE_RESP_RECEIVED;  // Update state

  /* Parse RESPONSE to get anchor_id */
  const mw_dstwr_resp_msg_t *resp_msg = (const mw_dstwr_resp_msg_t *)rx_buffer;
  uint8_t anchor_id = resp_msg->anchor_id;

  if (hal->read_timestamp(DW1000_REG_RX_TIME, 0x00, &t4) != 0) {
    g_ranging_state = RANGING_STATE_IDLE;  // Reset on error
    return MW_DSTWR_ERR;
  }
  
#ifdef ENABLE_RSSI
  /* Read RSSI of RESPONSE message */
  int rssi_resp = 0;
  if (hal->get_rssi) {
    rssi_resp = hal->get_rssi();
  }
#endif

#ifdef HAVE_TX_DELAY
 
  double delay_seconds = FINAL_TX_DELAY_US * 1e-6;  // Convert us to seconds
  uint64_t delay_units = (uint64_t)(delay_seconds / DWT_TIME_UNITS);
  
  /* Validate delay is above minimum threshold */
  uint64_t min_delay_units = (uint64_t)(MIN_FINAL_TX_DELAY_US * 1e-6 / DWT_TIME_UNITS);
  if (delay_units < min_delay_units) {
    delay_units = min_delay_units;  // Enforce minimum
  }

  uint16_t tx_ant_delay = 0;
  if (hal->get_tx_antenna_delay) {
    tx_ant_delay = hal->get_tx_antenna_delay();
  }
  
  /* Calculate scheduled TX time (what we pass to DW1000) */
  uint64_t scheduled_tx_time = ts40_add(t4, (uint32_t)delay_units);

  mw_dstwr_final_msg_t final_msg = {
    .msg_type = MW_DSTWR_MSG_TYPE_FINAL,
    .sequence_num = config->sequence_num,
    .poll_tx_timestamp = t1,
    .resp_rx_timestamp = t4,
    .final_tx_timestamp = 0  /* Will send real T5 in CORRECTION message */
  };

  if (hal->tx_delayed(&final_msg, sizeof(final_msg), scheduled_tx_time) != 0) {
    RLOG_W(LOG_OBJECT_CODE_RANGING, "[TAG] FINAL TX FAILED!");
    g_ranging_state = RANGING_STATE_IDLE;  // Reset on failure
    return MW_DSTWR_ERR;
  }
  
  g_ranging_state = RANGING_STATE_FINAL_SENT;  // Update state

  if (hal->read_timestamp(DW1000_REG_TX_TIME, 0x00, &t5) != 0) {
    g_ranging_state = RANGING_STATE_IDLE;  // Reset on error
    return MW_DSTWR_ERR;
  }

  double corr_delay_sec = CORRECTION_TX_DELAY_US * 1e-6;
  uint64_t corr_delay_units = (uint64_t)(corr_delay_sec / DWT_TIME_UNITS);
  uint64_t corr_scheduled_time = ts40_add(t5, (uint32_t)corr_delay_units);

  mw_dstwr_correction_msg_t t5_correction = {
    .msg_type = MW_DSTWR_MSG_TYPE_CORRECTION,
    .sequence_num = config->sequence_num,
    .final_tx_timestamp = t5,
    .distance_mm = 0  /* Placeholder - will be filled by Anchor */
  };
  
  if (hal->tx_delayed(&t5_correction, sizeof(t5_correction), corr_scheduled_time) != 0) {
    RLOG_W(LOG_OBJECT_CODE_RANGING, "[TAG] CORRECTION TX FAILED!");
    g_ranging_state = RANGING_STATE_IDLE;  // Reset on failure
    return MW_DSTWR_ERR;
  }

  g_ranging_state = RANGING_STATE_CORRECTION_SENT;  // Update state

#else
  /* Step 3: Send FINAL immediately */
  mw_dstwr_final_msg_t final_msg = {
    .msg_type = MW_DSTWR_MSG_TYPE_FINAL,
    .sequence_num = config->sequence_num,
    .poll_tx_timestamp = t1,
    .resp_rx_timestamp = t4,
    .final_tx_timestamp = 0  /* Placeholder for T5 */
  };

  if (hal->tx(&final_msg, sizeof(final_msg)) != 0) {
    RLOG_W(LOG_OBJECT_CODE_RANGING, "[TAG] FINAL TX FAILED!");
    g_ranging_state = RANGING_STATE_IDLE;  // Reset on failure
    return MW_DSTWR_ERR;
  }

  g_ranging_state = RANGING_STATE_FINAL_SENT;  // Update state

  if (hal->read_timestamp(DW1000_REG_TX_TIME, 0x00, &t5) != 0) {
    g_ranging_state = RANGING_STATE_IDLE;  // Reset on error
    return MW_DSTWR_ERR;
  }

  mw_dstwr_correction_msg_t t5_correction = {
    .msg_type = MW_DSTWR_MSG_TYPE_CORRECTION,
    .sequence_num = config->sequence_num,
    .final_tx_timestamp = t5,
    .distance_mm = 0
  };

  if (hal->tx(&t5_correction, sizeof(t5_correction)) != 0) {
    RLOG_W(LOG_OBJECT_CODE_RANGING, "[TAG] CORRECTION TX FAILED!");
    g_ranging_state = RANGING_STATE_IDLE;  // Reset on failure
    return MW_DSTWR_ERR;
  }

  g_ranging_state = RANGING_STATE_CORRECTION_SENT;  // Update state
#endif

  if (hal->rx_with_timeout(rx_buffer, sizeof(rx_buffer), &rx_length, WAIT_RESULT_TIMEOUT_US) != 0) {
    g_ranging_state = RANGING_STATE_IDLE;  // Reset on timeout
    return MW_DSTWR_ERR_TIMEOUT;
  }

  if (!mw_dstwr_validate_message(rx_buffer, rx_length, MW_DSTWR_MSG_TYPE_RESULT, config->sequence_num)) {
    g_ranging_state = RANGING_STATE_IDLE;  // Reset on invalid message
    return MW_DSTWR_ERR_INVALID_MSG;
  }

  /* Parse distance from RESULT */
  const mw_dstwr_result_msg_t *result_msg = (const mw_dstwr_result_msg_t *)rx_buffer;
  float distance = result_msg->distance_mm / 1000.0f;

  /* Fill result structure */
  if (result) {
    result->timestamps.t1 = t1;
    result->timestamps.t2 = 0;  /* Unknown at Tag */
    result->timestamps.t3 = 0;  /* Unknown at Tag */
    result->timestamps.t4 = t4;
    result->timestamps.t5 = t5;
    result->timestamps.t6 = 0;  /* Unknown at Tag */
    result->distance_m = distance;
#ifdef ENABLE_RSSI
    result->anchor_id = result_msg->anchor_id;
    result->rssi = result_msg->rssi_final;
#else
    result->anchor_id = anchor_id;
    result->rssi = 0;
#endif
    result->valid = true;
  }
 
  g_ranging_state = RANGING_STATE_COMPLETE;

  return MW_DSTWR_OK;
}

/* ====================================================================
 * ANCHOR IMPLEMENTATION (WITH STATE GUARD)
 * ==================================================================== */
mw_dstwr_err_t mw_dstwr_execute_anchor(const mw_dstwr_config_t *config,
                                       mw_dstwr_result_t *result)
{
  CHECK_PARAM(config && config->hal, MW_DSTWR_ERR_PARAM);
  CHECK_PARAM(is_valid_hal(config->hal), MW_DSTWR_ERR_PARAM);

  sys_config_t *sys_cfg = sys_config_get();
  const mw_dstwr_hal_t *hal = config->hal;
  uint64_t t2 = 0, t3 = 0, t6 = 0, t1 = 0, t4 = 0, t5 = 0;
  uint8_t rx_buffer[RX_BUFFER_SIZE];
  uint16_t rx_length = 0;

  /* Step 1: Wait for POLL (blocking with timeout) */
  if (hal->rx_with_timeout(rx_buffer, sizeof(rx_buffer), &rx_length, config->rx_timeout_us) != 0)
    return MW_DSTWR_ERR_TIMEOUT;

  /* Validate POLL message (accept any sequence number if 0xFF) */
  if (!mw_dstwr_validate_message(rx_buffer, rx_length, MW_DSTWR_MSG_TYPE_POLL, 0xFF)) {
    RLOG_W(LOG_OBJECT_CODE_RANGING, "[ANCHOR] Invalid POLL: type=0x%02X len=%u", rx_buffer[0], rx_length);
    return MW_DSTWR_ERR_INVALID_MSG;
  }

  /* Extract sequence number and target anchor ID from POLL */
  const mw_dstwr_poll_msg_t *poll = (const mw_dstwr_poll_msg_t *)rx_buffer;
  uint8_t poll_seq = poll->sequence_num;
  uint8_t target_anchor = poll->target_anchor;

  /* Check if this POLL is for us (0xFF means broadcast) */
  if (target_anchor != 0xFF && target_anchor != sys_cfg->uwb.device_id) {
    /* Not for us, ignore silently */
    return MW_DSTWR_OK;
  }

  if (hal->read_timestamp(DW1000_REG_RX_TIME, 0x00, &t2) != 0)
    return MW_DSTWR_ERR;
  
#ifdef ENABLE_RSSI
  /* Read RSSI of POLL message */
  int rssi_poll = 0;
  if (hal->get_rssi) {
    rssi_poll = hal->get_rssi();
  }
#endif

  /* Step 2: Send RESPONSE with embedded anchor_id */
  mw_dstwr_resp_msg_t resp_msg = {
    .msg_type = MW_DSTWR_MSG_TYPE_RESP,
    .sequence_num = poll_seq,
    .anchor_id = sys_cfg->uwb.device_id,  /* Send our anchor ID */
    .rssi_last = 0,
    .padding = {0}
  };

  if (hal->tx(&resp_msg, sizeof(resp_msg)) != 0)
    return MW_DSTWR_ERR;

  if (hal->read_timestamp(DW1000_REG_TX_TIME, 0x00, &t3) != 0)
    return MW_DSTWR_ERR;

  /* Step 3: Wait for FINAL with INCREASED timeout */
  if (hal->rx_with_timeout(rx_buffer, sizeof(rx_buffer), &rx_length, WAIT_FINAL_TIMEOUT_US) != 0)
    return MW_DSTWR_ERR_TIMEOUT;

  if (!mw_dstwr_validate_message(rx_buffer, rx_length, MW_DSTWR_MSG_TYPE_FINAL, poll_seq)) {
    RLOG_W(LOG_OBJECT_CODE_RANGING, "[ANCHOR] Invalid FINAL: type=0x%02X len=%u", rx_buffer[0], rx_length);
    return MW_DSTWR_ERR_INVALID_MSG;
  }

  /* Parse T1 and T4 from FINAL message */
  const mw_dstwr_final_msg_t *final = (const mw_dstwr_final_msg_t *)rx_buffer;
  t1 = final->poll_tx_timestamp & TIMESTAMP_40BIT_MASK;
  t4 = final->resp_rx_timestamp & TIMESTAMP_40BIT_MASK;

  if (hal->read_timestamp(DW1000_REG_RX_TIME, 0x00, &t6) != 0)
    return MW_DSTWR_ERR;
  
#ifdef ENABLE_RSSI
  /* Read RSSI of FINAL message */
  int rssi_final = 0;
  if (hal->get_rssi) {
    rssi_final = hal->get_rssi();
  }
#endif

#ifdef HAVE_TX_DELAY
  /* Step 4: Wait for CORRECTION with T5 with INCREASED timeout (30ms) */
  if (hal->rx_with_timeout(rx_buffer, sizeof(rx_buffer), &rx_length, WAIT_RESULT_TIMEOUT_US) != 0) {
    RLOG_W(LOG_OBJECT_CODE_RANGING, "[ANCHOR] CORRECTION TIMEOUT");
    return MW_DSTWR_ERR_TIMEOUT;
  }
  
  if (!mw_dstwr_validate_message(rx_buffer, rx_length, MW_DSTWR_MSG_TYPE_CORRECTION, poll_seq)) {
    RLOG_W(LOG_OBJECT_CODE_RANGING, "[ANCHOR] Invalid CORRECTION: type=0x%02X len=%u", rx_buffer[0], rx_length);
    return MW_DSTWR_ERR_INVALID_MSG;
  }
  
  /* Parse actual T5 from CORRECTION (overwrites placeholder from FINAL) */
  const mw_dstwr_correction_msg_t *corr = (const mw_dstwr_correction_msg_t *)rx_buffer;
  t5 = corr->final_tx_timestamp & TIMESTAMP_40BIT_MASK;

  /* Calculate distance with actual T5 */
  mw_dstwr_timestamps_t timestamps = {
    .t1 = t1, .t2 = t2, .t3 = t3,
    .t4 = t4, .t5 = t5, .t6 = t6
  };

  float distance = mw_dstwr_calculate_distance(&timestamps);
  
  /* Validate calculated distance */
  if (distance < 0.0f || distance > 1000.0f)
    return MW_DSTWR_ERR;

  /* Send distance result back to Tag */
  int32_t distance_mm = (int32_t)(distance * 1000.0f);
  mw_dstwr_result_msg_t result_msg = {
    .msg_type = MW_DSTWR_MSG_TYPE_RESULT,
    .sequence_num = poll_seq,
    .distance_mm = distance_mm,
#ifdef ENABLE_RSSI
    .anchor_id = sys_cfg->uwb.device_id,
    .rssi_final = (uint8_t)(rssi_final & 0xFF),
#else
    .anchor_id = sys_cfg->uwb.device_id,
    .rssi_final = 0,
#endif
    .padding = {0}
  };

  if (hal->tx(&result_msg, sizeof(result_msg)) != 0)
    return MW_DSTWR_ERR;

#else
  /* Step 4: Wait for CORRECTION with T5 */
  uint32_t correction_timeout = config->rx_timeout_us + (INTER_MSG_DELAY_MS * 1000);
  
  if (hal->rx_with_timeout(rx_buffer, sizeof(rx_buffer), &rx_length, correction_timeout) != 0)
    return MW_DSTWR_ERR_TIMEOUT;

  if (!mw_dstwr_validate_message(rx_buffer, rx_length, MW_DSTWR_MSG_TYPE_CORRECTION, poll_seq))
    return MW_DSTWR_ERR_INVALID_MSG;

  /* Parse T5 from CORRECTION message */
  t5 = ((uint64_t)rx_buffer[2]) |
       ((uint64_t)rx_buffer[3] << 8) |
       ((uint64_t)rx_buffer[4] << 16) |
       ((uint64_t)rx_buffer[5] << 24) |
       ((uint64_t)rx_buffer[6] << 32) |
       ((uint64_t)rx_buffer[7] << 40) |
       ((uint64_t)rx_buffer[8] << 48) |
       ((uint64_t)rx_buffer[9] << 56);
  t5 &= TIMESTAMP_40BIT_MASK;

  /* Calculate distance */
  mw_dstwr_timestamps_t timestamps = {
    .t1 = t1, .t2 = t2, .t3 = t3,
    .t4 = t4, .t5 = t5, .t6 = t6
  };

  float distance = mw_dstwr_calculate_distance(&timestamps);
  
  if (distance < 0.0f || distance > 1000.0f) {
    /* Log invalid distance for debugging */
    return MW_DSTWR_ERR;
  }

  /* Send distance result immediately - Put distance at bytes 2-3 (not at end - DW1000 strips last 2 bytes as CRC) */
  int32_t distance_mm = (int32_t)(distance * 1000.0f);
  if (distance_mm > 65535) distance_mm = 65535;
  if (distance_mm < 0) distance_mm = 0;
  
  uint8_t response_buffer[12];
  response_buffer[0] = MW_DSTWR_MSG_TYPE_RESULT;  /* 0xE5 */
  response_buffer[1] = poll_seq;
  response_buffer[2] = (uint8_t)(distance_mm & 0xFF);        /* distance LOW */
  response_buffer[3] = (uint8_t)((distance_mm >> 8) & 0xFF); /* distance HIGH */
  response_buffer[4] = 0;
  response_buffer[5] = 0;
  response_buffer[6] = 0;
  response_buffer[7] = 0;
  response_buffer[8] = 0;
  response_buffer[9] = 0;
  response_buffer[10] = 0;
  response_buffer[11] = 0;

  if (hal->tx(response_buffer, 12) != 0)
    return MW_DSTWR_ERR;
#endif

  /* Fill result structure */
  if (result) {
    result->timestamps.t1 = t1;
    result->timestamps.t2 = t2;
    result->timestamps.t3 = t3;
    result->timestamps.t4 = t4;
    result->timestamps.t5 = t5;
    result->timestamps.t6 = t6;
    result->distance_m = distance;
    result->anchor_id = sys_cfg->uwb.device_id;  /* Always set anchor ID */
#ifdef ENABLE_RSSI
    result->rssi = (int8_t)(rssi_final & 0xFF);
#else
    result->rssi = 0;
#endif
    result->valid = true;
  }

  return MW_DSTWR_OK;
}

/* ====================================================================
 * DISTANCE CALCULATION 
 * ==================================================================== */
float mw_dstwr_calculate_distance(const mw_dstwr_timestamps_t *timestamps)
{
  if (!timestamps) return -1.0f;

  const uint64_t MASK_40BIT = 0x000000FFFFFFFFFFULL;
  
  /* Wrap-safe 40-bit subtraction */
  uint64_t Ra = (timestamps->t4 - timestamps->t1) & MASK_40BIT;
  uint64_t Rb = (timestamps->t6 - timestamps->t3) & MASK_40BIT;
  uint64_t Da = (timestamps->t5 - timestamps->t4) & MASK_40BIT;
  uint64_t Db = (timestamps->t3 - timestamps->t2) & MASK_40BIT;

  /* Convert to double for calculation (avoid overflow) */
  double Ra_d = (double)Ra;
  double Rb_d = (double)Rb;
  double Da_d = (double)Da;
  double Db_d = (double)Db;

  double denominator = Ra_d + Rb_d + Da_d + Db_d;
  if (denominator <= 0.0) return -1.0f;

  /* DS-TWR formula: ToF = (Ra*Rb - Da*Db) / (Ra + Rb + Da + Db) */
  double tof_dtu = (Ra_d * Rb_d - Da_d * Db_d) / denominator;
  
  /* ToF should be positive and reasonable (max ~3.3ms for 1km range) */
  if (tof_dtu < 0.0 || tof_dtu > 1e9) return -1.0f;
  
  double tof_sec = tof_dtu * DWT_TIME_UNITS;
  float distance = (float)(tof_sec * SPEED_OF_LIGHT);

  return distance;
}

/* ====================================================================
 * MESSAGE VALIDATION
 * ==================================================================== */
bool mw_dstwr_validate_message(const uint8_t *data, uint16_t length,
                               uint8_t expected_type, uint8_t expected_seq)
{
  if (!data || length < 2)
    return false;

  if (data[0] != expected_type)
    return false;

  if (expected_seq != 0xFF && data[1] != expected_seq)
    return false;

  /* Minimum length check per message type */
  switch (expected_type)
  {
    case MW_DSTWR_MSG_TYPE_POLL:
      return length >= 12;  /* POLL: type+seq+target+rssi+padding = 12 bytes */
      
    case MW_DSTWR_MSG_TYPE_RESP:
      return length >= 12;  /* RESP: type+seq+anchor+rssi+padding = 12 bytes */
      
    case MW_DSTWR_MSG_TYPE_FINAL:
      return length >= 26;  /* FINAL: type+seq+T1+T4+T5 = 26 bytes */
      
    case MW_DSTWR_MSG_TYPE_RESULT:
      return length >= 12;  /* RESULT: type+seq+dist(4)+anchor+rssi+pad(4) = 12 bytes */
      
    case MW_DSTWR_MSG_TYPE_CORRECTION:
      return length >= 14;  /* CORRECTION: type+seq+T5(8)+distance_mm(4) = 14 bytes */
      
    default:
      return false;
  }
}

/* ====================================================================
 * STATE RESET (for external use)
 * ==================================================================== */
void mw_dstwr_reset_state(void)
{
  g_ranging_state = RANGING_STATE_IDLE;
}

static inline bool is_valid_hal(const mw_dstwr_hal_t *hal)
{
  /* Basic callbacks required for all modes */
  if (!hal->tx || !hal->rx_with_timeout || !hal->read_timestamp) {
    return false;
  }
  
#ifdef ENABLE_RSSI
  if (!hal->get_rssi) {
    return false;
  }
#endif
  
#ifdef HAVE_TX_DELAY
  if (!hal->tx_delayed) {
    return false;
  }
#endif
  return true;
}

/* End of file -------------------------------------------------------- */