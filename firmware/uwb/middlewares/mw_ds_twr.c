/* ============================== mw_ds_twr.c (FINAL FIX) ====================
 * @file       mw_ds_twr.c
 * @brief      DS-TWR with proper inter-message delays
 * @version    2.2.0
 * @date       2025-12-15
 */

#include "mw_ds_twr.h"
#include <stddef.h>
#include <string.h>
#include "sys_config.h"
#include "platform_config.h"
#include "sys_logger.h"
/* Private defines ---------------------------------------------------- */
#define DW1000_REG_RX_TIME     (0x15)
#define DW1000_REG_TX_TIME     (0x17)
#define TIMESTAMP_40BIT_MASK   (0x000000FFFFFFFFFFULL)
#define RX_BUFFER_SIZE         (128u)

#define DWT_TIME_UNITS (1.0/499.2e6/128.0)
#define SPEED_OF_LIGHT 299792458.0

/* Protocol mode selection */
// #define HAVE_TX_DELAY  /* Define to use delayed TX, comment out to use CORRECTION message */
#define ENABLE_RSSI       /* Define to enable RSSI measurements */

/* Inter-message delays to ensure receiver is ready */
#define INTER_MSG_DELAY_MS     (2)   // 2ms delay (DW1000 RX turnaround ~300us + margin)

/* DW1000 TX delay constraints (based on datasheet) */
#define DW1000_TURNAROUND_US   (300)  // TX->RX or RX->TX switching time (~200-300us)
#define MCU_PROCESSING_US      (500)  // MCU processing + frame preparation + logging
#define ANTENNA_DELAY_US       (100)  // Antenna delay compensation
#define SAFETY_MARGIN_US       (1100) // Safety margin for clock drift & jitter & OS delays

/* Total minimum delay = 300 + 500 + 100 + 1100 = 2000us = 2ms */
#define MIN_FINAL_TX_DELAY_US  (DW1000_TURNAROUND_US + MCU_PROCESSING_US + ANTENNA_DELAY_US + SAFETY_MARGIN_US)
#define FINAL_TX_DELAY_US      (3000) // 3ms - safe margin for MCU processing

/* Internal timeouts for message exchange (shorter than config timeout) */
#define WAIT_FINAL_TIMEOUT_US  (15000)  // 15ms - Tag sends FINAL after 3ms delay
#define WAIT_RESULT_TIMEOUT_US (15000)  // 15ms - Anchor sends RESULT quickly after FINAL

#define CHECK_PARAM(cond, ret) do { if (!(cond)) return (ret); } while(0)

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
 * TAG IMPLEMENTATION
 * ==================================================================== */
mw_dstwr_err_t mw_dstwr_execute_tag(const mw_dstwr_config_t *config,
                                    mw_dstwr_result_t *result)
{
  CHECK_PARAM(config && config->hal, MW_DSTWR_ERR_PARAM);
  CHECK_PARAM(is_valid_hal(config->hal), MW_DSTWR_ERR_PARAM);

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
    return MW_DSTWR_ERR;
  }

  /* DW1000 automatically completes TX before timestamp read */
  if (hal->read_timestamp(DW1000_REG_TX_TIME, 0x00, &t1) != 0)
    return MW_DSTWR_ERR;

  /* Step 2: Wait for RESPONSE with SHORT timeout
   * Anchor responds quickly (~2-5ms), no need to wait 250ms
   * Using short timeout allows fast retry if missed
   */
  if (hal->rx_with_timeout(rx_buffer, sizeof(rx_buffer), &rx_length, WAIT_FINAL_TIMEOUT_US) != 0) {
    return MW_DSTWR_ERR_TIMEOUT;
  }

  if (!mw_dstwr_validate_message(rx_buffer, rx_length, MW_DSTWR_MSG_TYPE_RESP, config->sequence_num)) {
    return MW_DSTWR_ERR_INVALID_MSG;
  }

  /* Parse RESPONSE to get anchor_id */
  const mw_dstwr_resp_msg_t *resp_msg = (const mw_dstwr_resp_msg_t *)rx_buffer;
  uint8_t anchor_id = resp_msg->anchor_id;

  if (hal->read_timestamp(DW1000_REG_RX_TIME, 0x00, &t4) != 0)
    return MW_DSTWR_ERR;
  
#ifdef ENABLE_RSSI
  /* Read RSSI of RESPONSE message */
  int rssi_resp = 0;
  if (hal->get_rssi) {
    rssi_resp = hal->get_rssi();
  }
#endif

#ifdef HAVE_TX_DELAY
  /* Step 3: Send FINAL with delayed TX (T5 known in advance) */
  
  /* ┌─────────────────────────────────────────────────────────────────────────┐
   * │             DS-TWR DELAYED TX TIMING TABLE (HAVE_TX_DELAY)              │
   * ├─────────────┬──────────────┬──────────────┬─────────────────────────────┤
   * │ Timestamp   │ Device       │ Event        │ Timing Constraints          │
   * ├─────────────┼──────────────┼──────────────┼─────────────────────────────┤
   * │ T1          │ Tag          │ POLL TX      │ -                           │
   * │ T2          │ Anchor       │ POLL RX      │ T2 = T1 + ToF               │
   * │ T3          │ Anchor       │ RESPONSE TX  │ T3 = T2 + Anchor_delay      │
   * │ T4          │ Tag          │ RESPONSE RX  │ T4 = T3 + ToF               │
   * │ **T5**      │ **Tag**      │ **FINAL TX** │ **T5 = T4 + DELAY (known)** │
   * │ T6          │ Anchor       │ FINAL RX     │ T6 = T5 + ToF               │
   * ├─────────────┴──────────────┴──────────────┴─────────────────────────────┤
   * │ DELAY CALCULATION (T5 - T4):                                            │
   * │   • DW1000 turnaround:    300 µs  (TX→RX switching)                     │
   * │   • MCU processing:       200 µs  (frame preparation)                   │
   * │   • Antenna delay:        100 µs  (signal propagation)                  │
   * │   • Safety margin:        400 µs  (clock drift + jitter)                │
   * │   ─────────────────────────────────────────────────────                 │
   * │   • MINIMUM required:    1000 µs  (1 ms)                                │
   * │   • RECOMMENDED value:   3000 µs  (3 ms) ← Current setting              │
   * │   • MAXIMUM allowed:   100000 µs  (100 ms)                              │
   * ├──────────────────────────────────────────────────────────────────────────┤
   * │ DW1000 TIME UNITS CONVERSION:                                            │
   * │   • 1 unit ≈ 15.65 ps  (picoseconds)                                     │
   * │   • DWT_TIME_UNITS = 1/(499.2 MHz / 128) ≈ 2.565×10⁻¹⁰ seconds           │
   * │   • 3000 µs = 3ms / 2.565e-10 ≈ 11,695,906 DW1000 units                 │
   * └──────────────────────────────────────────────────────────────────────────┘
   */
  
  double delay_seconds = FINAL_TX_DELAY_US * 1e-6;  // Convert us to seconds
  uint64_t delay_units = (uint64_t)(delay_seconds / DWT_TIME_UNITS);
  
  /* Validate delay is above minimum threshold */
  uint64_t min_delay_units = (uint64_t)(MIN_FINAL_TX_DELAY_US * 1e-6 / DWT_TIME_UNITS);
  if (delay_units < min_delay_units) {
    delay_units = min_delay_units;  // Enforce minimum
  }
  
  /* Get TX antenna delay for compensation
   * DW1000 delayed TX: Actual TX time = Scheduled time + TX_antenna_delay
   * We must send the ACTUAL TX time (T5) in the message, not scheduled time
   */
  uint16_t tx_ant_delay = 0;
  if (hal->get_tx_antenna_delay) {
    tx_ant_delay = hal->get_tx_antenna_delay();
  }
  
  /* Calculate scheduled TX time (what we pass to DW1000) */
  uint64_t scheduled_tx_time = ts40_add(t4, (uint32_t)delay_units);
  
  /* Calculate T5 = Actual TX time = scheduled + tx_antenna_delay
   * This is what we send in the FINAL message for accurate ranging
   */
  t5 = ts40_add(scheduled_tx_time, tx_ant_delay);

  mw_dstwr_final_msg_t final_msg = {
    .msg_type = MW_DSTWR_MSG_TYPE_FINAL,
    .sequence_num = config->sequence_num,
    .poll_tx_timestamp = t1,
    .resp_rx_timestamp = t4,
    .final_tx_timestamp = t5  /* T5 = actual TX time (scheduled + antenna delay) */
  };

  /* Execute delayed transmission at scheduled time (DW1000 will add antenna delay internally) */
  if (hal->tx_delayed(&final_msg, sizeof(final_msg), scheduled_tx_time) != 0) {
    RLOG_W(LOG_OBJECT_CODE_RANGING, "[TAG] FINAL TX FAILED!");
    return MW_DSTWR_ERR;
  }

  /* Step 4: Wait for distance result from Anchor with SHORT timeout
   * Anchor calculates and sends RESULT quickly (~5ms)
   */
  if (hal->rx_with_timeout(rx_buffer, sizeof(rx_buffer), &rx_length, WAIT_RESULT_TIMEOUT_US) != 0) {
    return MW_DSTWR_ERR_TIMEOUT;
  }

  if (!mw_dstwr_validate_message(rx_buffer, rx_length, MW_DSTWR_MSG_TYPE_RESULT, config->sequence_num)) {
    return MW_DSTWR_ERR_INVALID_MSG;
  }

  /* Parse distance from Anchor's RESULT */
  const mw_dstwr_result_msg_t *result_msg = (const mw_dstwr_result_msg_t *)rx_buffer;
  float distance_m = (float)result_msg->distance_mm / 1000.0f;

#else
  /* Step 3: Send FINAL with T5=0 (will be corrected later) */
  mw_dstwr_final_msg_t final_msg = {
    .msg_type = MW_DSTWR_MSG_TYPE_FINAL,
    .sequence_num = config->sequence_num,
    .poll_tx_timestamp = t1,
    .resp_rx_timestamp = t4,
    .final_tx_timestamp = 0
  };

  if (hal->tx(&final_msg, sizeof(final_msg)) != 0)
    return MW_DSTWR_ERR;

  /* DW1000 automatically completes TX before timestamp read */
  if (hal->read_timestamp(DW1000_REG_TX_TIME, 0x00, &t5) != 0)
    return MW_DSTWR_ERR;

  /* Step 4: Send CORRECTION with real T5 immediately */
  mw_dstwr_correction_msg_t tx_correction = {
    .msg_type = MW_DSTWR_MSG_TYPE_CORRECTION,
    .sequence_num = config->sequence_num,
    .final_tx_timestamp = t5,
    .distance_mm = 0  /* Not used in TX direction */
  };

  if (hal->tx(&tx_correction, sizeof(tx_correction)) != 0)
    return MW_DSTWR_ERR;

  /* Step 5: Wait for RESULT (0xE5) from Anchor (ANCHOR sends immediately after processing) */
  if (hal->rx_with_timeout(rx_buffer, sizeof(rx_buffer), &rx_length, config->rx_timeout_us) != 0)
    return MW_DSTWR_ERR_TIMEOUT;

  if (!mw_dstwr_validate_message(rx_buffer, rx_length, MW_DSTWR_MSG_TYPE_RESULT, config->sequence_num))
    return MW_DSTWR_ERR_INVALID_MSG;

  /* Parse distance (16-bit) from bytes 2-3 (after type and seq) */
  int32_t distance_mm_raw = ((int32_t)rx_buffer[2]) |
                            ((int32_t)rx_buffer[3] << 8);
  
  float distance_m = (float)distance_mm_raw / 1000.0f;
#endif

  /* Fill result structure (common for both modes) */
  if (result) {
    result->timestamps.t1 = t1;
    result->timestamps.t2 = 0;
    result->timestamps.t3 = 0;
    result->timestamps.t4 = t4;
    result->timestamps.t5 = t5;
    result->timestamps.t6 = 0;
    result->distance_m = distance_m;
    result->anchor_id = anchor_id;  /* From RESPONSE message */
#ifdef ENABLE_RSSI
    result->rssi = (int8_t)(rssi_resp & 0xFF);
#else
    result->rssi = 0;
#endif
    result->valid = true;
  }

  return MW_DSTWR_OK;
}

/* ====================================================================
 * ANCHOR IMPLEMENTATION
 * ==================================================================== */
mw_dstwr_err_t mw_dstwr_execute_anchor(const mw_dstwr_config_t *config,
                                       mw_dstwr_result_t *result)
{
  CHECK_PARAM(config && config->hal, MW_DSTWR_ERR_PARAM);
  CHECK_PARAM(is_valid_hal(config->hal), MW_DSTWR_ERR_PARAM);

  sys_config_t *sys_cfg = sys_config_get();
  const mw_dstwr_hal_t *hal = config->hal;
  uint64_t t1 = 0, t2 = 0, t3 = 0, t4 = 0, t5 = 0, t6 = 0;
  uint8_t rx_buffer[RX_BUFFER_SIZE];
  uint16_t rx_length = 0;
  uint8_t poll_seq = 0;

  /* Step 1: Wait for POLL */
  if (hal->rx_with_timeout(rx_buffer, sizeof(rx_buffer), &rx_length, config->rx_timeout_us) != 0) {
    return MW_DSTWR_ERR_TIMEOUT;
  }

  /* Quick check: is this a POLL message? */
  if (rx_buffer[0] != MW_DSTWR_MSG_TYPE_POLL || rx_length < 12) {
    return MW_DSTWR_ERR_INVALID_MSG;
  }

  const mw_dstwr_poll_msg_t *poll_msg = (const mw_dstwr_poll_msg_t *)rx_buffer;
  poll_seq = poll_msg->sequence_num;
  
  /* Check if this POLL is for us (or broadcast) */
  uint8_t target_anchor = poll_msg->target_anchor;
  if (target_anchor != ANCHOR_ID_BROADCAST && target_anchor != sys_cfg->device_id) {
    /* Not for us, ignore silently */
    return MW_DSTWR_ERR_INVALID_MSG;
  }

  if (hal->read_timestamp(DW1000_REG_RX_TIME, 0x00, &t2) != 0)
    return -10;
  
#ifdef ENABLE_RSSI
  /* Read RSSI of POLL message */
  int rssi_poll = 0;
  if (hal->get_rssi) {
    rssi_poll = hal->get_rssi();
  }
#endif

  /* Step 2: Send RESPONSE */
  mw_dstwr_resp_msg_t resp_msg = {
    .msg_type = MW_DSTWR_MSG_TYPE_RESP,
    .sequence_num = poll_seq,
    .anchor_id = sys_cfg->device_id,  /* Always send device ID */
#ifdef ENABLE_RSSI
    .rssi_poll = (uint8_t)(rssi_poll & 0xFF),
#else
    .rssi_poll = 0,
#endif
    .padding = {0}
  };

  if (hal->tx(&resp_msg, sizeof(resp_msg)) != 0) {
    return -20;
  }

  /* DW1000 automatically completes TX before timestamp read */
  if (hal->read_timestamp(DW1000_REG_TX_TIME, 0x00, &t3) != 0)
    return -30;

  /* Step 3: Wait for FINAL with SHORT timeout
   * Tag sends FINAL after 3ms delay, so we only need ~15ms total wait
   * Using config timeout here would wait too long and miss next POLL cycle
   */
  if (hal->rx_with_timeout(rx_buffer, sizeof(rx_buffer), &rx_length, WAIT_FINAL_TIMEOUT_US) != 0) {
    return MW_DSTWR_ERR_TIMEOUT;
  }

  /* Check if we received POLL instead of FINAL (Tag missed our RESPONSE) */
  if (rx_buffer[0] == MW_DSTWR_MSG_TYPE_POLL) {
    return MW_DSTWR_ERR_SYNC_LOST;  /* Special error for retry logic */
  }

  if (!mw_dstwr_validate_message(rx_buffer, rx_length, MW_DSTWR_MSG_TYPE_FINAL, poll_seq)) {
    return MW_DSTWR_ERR_INVALID_MSG;
  }

  if (hal->read_timestamp(DW1000_REG_RX_TIME, 0x00, &t6) != 0)
    return -40;
  
#ifdef ENABLE_RSSI
  /* Read RSSI of FINAL message */
  int rssi_final = 0;
  if (hal->get_rssi) {
    rssi_final = hal->get_rssi();
  }
#endif

  /* Parse FINAL message: [type:1][seq:1][T1:8][T4:8][T5:8] = 26 bytes */
  t1 = ((uint64_t)rx_buffer[2]) |
       ((uint64_t)rx_buffer[3] << 8) |
       ((uint64_t)rx_buffer[4] << 16) |
       ((uint64_t)rx_buffer[5] << 24) |
       ((uint64_t)rx_buffer[6] << 32) |
       ((uint64_t)rx_buffer[7] << 40) |
       ((uint64_t)rx_buffer[8] << 48) |
       ((uint64_t)rx_buffer[9] << 56);
  t1 &= TIMESTAMP_40BIT_MASK;
       
  t4 = ((uint64_t)rx_buffer[10]) |
       ((uint64_t)rx_buffer[11] << 8) |
       ((uint64_t)rx_buffer[12] << 16) |
       ((uint64_t)rx_buffer[13] << 24) |
       ((uint64_t)rx_buffer[14] << 32) |
       ((uint64_t)rx_buffer[15] << 40) |
       ((uint64_t)rx_buffer[16] << 48) |
       ((uint64_t)rx_buffer[17] << 56);
  t4 &= TIMESTAMP_40BIT_MASK;

#ifdef HAVE_TX_DELAY
  /* T5 is already in FINAL message (bytes 18-25) - delayed TX mode */
  t5 = ((uint64_t)rx_buffer[18]) |
       ((uint64_t)rx_buffer[19] << 8) |
       ((uint64_t)rx_buffer[20] << 16) |
       ((uint64_t)rx_buffer[21] << 24) |
       ((uint64_t)rx_buffer[22] << 32) |
       ((uint64_t)rx_buffer[23] << 40) |
       ((uint64_t)rx_buffer[24] << 48) |
       ((uint64_t)rx_buffer[25] << 56);
  t5 &= TIMESTAMP_40BIT_MASK;

  /* Calculate distance using DS-TWR formula */
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
    .anchor_id = sys_cfg->device_id,
    .rssi_final = (uint8_t)(rssi_final & 0xFF),
#else
    .anchor_id = 0,
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
    result->anchor_id = sys_cfg->device_id;  /* Always set anchor ID */
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
      return length >= 12;  /* RESULT: type+seq+dist+anchor+rssi+pad = 12 bytes */
      
    case MW_DSTWR_MSG_TYPE_CORRECTION:
      return length >= 12;  /* CORRECTION: type+seq+T5+distance(16-bit) = 12 bytes payload */
      
    default:
      return false;
  }
}

static inline bool is_valid_hal(const mw_dstwr_hal_t *hal)
{
  /* Basic callbacks required for all modes */
  if (!hal->tx || !hal->rx_with_timeout || !hal->read_timestamp || 
      !hal->get_tick_ms) {
    return false;
  }
  
#ifdef ENABLE_RSSI
  if (!hal->get_rssi) {
    return false;
  }
#endif
  
#ifdef HAVE_TX_DELAY
  /* tx_delayed required only in HAVE_TX_DELAY mode */
  if (!hal->tx_delayed) {
    return false;
  }
#endif
  
  return true;
}

/* End of file -------------------------------------------------------- */
