/* ============================== mw_ds_twr.c (FINAL FIX) ====================
 * @file       mw_ds_twr.c
 * @brief      DS-TWR with proper inter-message delays
 * @version    2.1.0
 * @date       2025-12-13
 */

#include "mw_ds_twr.h"
#include "mw_time_utils.h"
#include <stddef.h>
#include <string.h>
#include "sys_config.h"

/* Private defines ---------------------------------------------------- */
#define DW1000_REG_RX_TIME     (0x15)
#define DW1000_REG_TX_TIME     (0x17)
#define TIMESTAMP_40BIT_MASK   (0x000000FFFFFFFFFFULL)
#define RX_BUFFER_SIZE         (128u)

#define DWT_TIME_UNITS (1.0/499.2e6/128.0)
#define SPEED_OF_LIGHT 299792458.0

/* Inter-message delays to ensure receiver is ready */
#define INTER_MSG_DELAY_MS     (5)   // 5ms delay between messages

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
    .padding = {0}
  };

  if (hal->tx(&poll_msg, sizeof(poll_msg)) != 0)
    return MW_DSTWR_ERR;

  /* Wait for TX to complete */
  uint32_t tx_wait_start = hal->get_tick_ms();
  while ((hal->get_tick_ms() - tx_wait_start) < 2) {
    /* Wait 2ms for TX completion */
  }

  if (hal->read_timestamp(DW1000_REG_TX_TIME, 0x00, &t1) != 0)
    return MW_DSTWR_ERR;

  /* Step 2: Wait for RESPONSE */
  if (hal->rx_with_timeout(rx_buffer, sizeof(rx_buffer), &rx_length, config->rx_timeout_us) != 0)
    return MW_DSTWR_ERR_TIMEOUT;

  if (!mw_dstwr_validate_message(rx_buffer, rx_length, MW_DSTWR_MSG_TYPE_RESP, config->sequence_num))
    return MW_DSTWR_ERR_INVALID_MSG;

  if (hal->read_timestamp(DW1000_REG_RX_TIME, 0x00, &t4) != 0)
    return MW_DSTWR_ERR;

  /* Step 3: Send FINAL */
  mw_dstwr_final_msg_t final_msg = {
    .msg_type = MW_DSTWR_MSG_TYPE_FINAL,
    .sequence_num = config->sequence_num,
    .poll_tx_timestamp = t1,
    .resp_rx_timestamp = t4,
    .final_tx_timestamp = 0
  };

  if (hal->tx(&final_msg, sizeof(final_msg)) != 0)
    return MW_DSTWR_ERR;

  /* Wait for TX to complete */
  tx_wait_start = hal->get_tick_ms();
  while ((hal->get_tick_ms() - tx_wait_start) < 2) {
    /* Wait 2ms for TX completion */
  }

  if (hal->read_timestamp(DW1000_REG_TX_TIME, 0x00, &t5) != 0)
    return MW_DSTWR_ERR;

  /* CRITICAL FIX: Wait for Anchor to be ready for CORRECTION */
  uint32_t delay_start = hal->get_tick_ms();
  while ((hal->get_tick_ms() - delay_start) < INTER_MSG_DELAY_MS) {
    /* Wait */
  }

  /* Step 4: Send CORRECTION with real T5 */
  mw_dstwr_correction_msg_t correction_msg = {
    .msg_type = MW_DSTWR_MSG_TYPE_CORRECTION,
    .sequence_num = config->sequence_num,
    .final_tx_timestamp = t5
  };

  if (hal->tx(&correction_msg, sizeof(correction_msg)) != 0)
    return MW_DSTWR_ERR;

  if (result) {
    result->timestamps.t1 = t1;
    result->timestamps.t2 = 0;
    result->timestamps.t3 = 0;
    result->timestamps.t4 = t4;
    result->timestamps.t5 = t5;
    result->timestamps.t6 = 0;
    result->distance_m = 0.0f;
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
  if (hal->rx_with_timeout(rx_buffer, sizeof(rx_buffer), &rx_length, config->rx_timeout_us) != 0)
    return MW_DSTWR_ERR_TIMEOUT;

  if (!mw_dstwr_validate_message(rx_buffer, rx_length, MW_DSTWR_MSG_TYPE_POLL, 0xFF))
    return MW_DSTWR_ERR_INVALID_MSG;

  const mw_dstwr_poll_msg_t *poll_msg = (const mw_dstwr_poll_msg_t *)rx_buffer;
  poll_seq = poll_msg->sequence_num;

  if (hal->read_timestamp(DW1000_REG_RX_TIME, 0x00, &t2) != 0)
    return -10;

  /* Step 2: Send RESPONSE */
  mw_dstwr_resp_msg_t resp_msg = {
    .msg_type = MW_DSTWR_MSG_TYPE_RESP,
    .sequence_num = poll_seq,
    .padding = {0}
  };

  if (hal->tx(&resp_msg, sizeof(resp_msg)) != 0)
    return -20;

  /* Wait for TX to complete and timestamp to be written */
  uint32_t tx_wait_start = hal->get_tick_ms();
  while ((hal->get_tick_ms() - tx_wait_start) < 2) {
    /* Wait 2ms for TX completion */
  }

  if (hal->read_timestamp(DW1000_REG_TX_TIME, 0x00, &t3) != 0)
    return -30;

  /* Step 3: Wait for FINAL */
  if (hal->rx_with_timeout(rx_buffer, sizeof(rx_buffer), &rx_length, config->rx_timeout_us) != 0)
    return MW_DSTWR_ERR_TIMEOUT;

  if (!mw_dstwr_validate_message(rx_buffer, rx_length, MW_DSTWR_MSG_TYPE_FINAL, poll_seq))
    return MW_DSTWR_ERR_INVALID_MSG;

  if (hal->read_timestamp(DW1000_REG_RX_TIME, 0x00, &t6) != 0)
    return -40;

  /* DEBUG: Manual parse FINAL message to avoid alignment issues */
  /* FINAL message format: [type:1][seq:1][T1:8][T4:8][T5:8] = 26 bytes */
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

  /* Step 4: Wait for CORRECTION (Tag will wait INTER_MSG_DELAY_MS before sending) */
  uint32_t correction_timeout = config->rx_timeout_us + (INTER_MSG_DELAY_MS * 2000); // Extra margin
  
  if (hal->rx_with_timeout(rx_buffer, sizeof(rx_buffer), &rx_length, correction_timeout) != 0)
    return MW_DSTWR_ERR_TIMEOUT;

  if (!mw_dstwr_validate_message(rx_buffer, rx_length, MW_DSTWR_MSG_TYPE_CORRECTION, poll_seq))
    return MW_DSTWR_ERR_INVALID_MSG;

  /* DEBUG: Manual parse CORRECTION message to avoid alignment issues */
  /* CORRECTION message format: [type:1][seq:1][T5:8][padding:2] = 12 bytes */
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
  
  // if (distance < 0.0f || distance > 1000.0f)
  //   return MW_DSTWR_ERR;

  if (result) {
    result->timestamps = timestamps;
    result->distance_m = distance;
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

  uint32_t t1_32 = (uint32_t)timestamps->t1;
  uint32_t t2_32 = (uint32_t)timestamps->t2;
  uint32_t t3_32 = (uint32_t)timestamps->t3;
  uint32_t t4_32 = (uint32_t)timestamps->t4;
  uint32_t t5_32 = (uint32_t)timestamps->t5;
  uint32_t t6_32 = (uint32_t)timestamps->t6;

  double Ra = (double)(t4_32 - t1_32);
  double Rb = (double)(t6_32 - t3_32);
  double Da = (double)(t5_32 - t4_32);
  double Db = (double)(t3_32 - t2_32);

  double denominator = Ra + Rb + Da + Db;
  if (denominator == 0.0) return -1.0f;

  double tof_dtu = (Ra * Rb - Da * Db) / denominator;
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

  uint8_t msg_type = data[0];
  uint8_t msg_seq = data[1];

  if (msg_type != expected_type)
    return false;

  if (expected_seq != 0xFF && msg_seq != expected_seq)
    return false;

  switch (expected_type)
  {
    case MW_DSTWR_MSG_TYPE_POLL:
      return length >= sizeof(mw_dstwr_poll_msg_t);
      
    case MW_DSTWR_MSG_TYPE_RESP:
      return length >= sizeof(mw_dstwr_resp_msg_t);
      
    case MW_DSTWR_MSG_TYPE_FINAL:
      return length >= sizeof(mw_dstwr_final_msg_t);
      
    case MW_DSTWR_MSG_TYPE_CORRECTION:
      return length >= sizeof(mw_dstwr_correction_msg_t);
      
    default:
      return false;
  }
}

static inline bool is_valid_hal(const mw_dstwr_hal_t *hal)
{
  return (hal->tx != NULL &&
          hal->rx_with_timeout != NULL &&
          hal->read_timestamp != NULL &&
          hal->get_tick_ms != NULL);
}

/* End of file -------------------------------------------------------- */