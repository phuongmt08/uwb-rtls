/* ============================== mw_ds_twr.c ================================
 * @file       mw_ds_twr.c
 * @brief      Middleware - Double-Sided Two-Way Ranging implementation
 * @version    1.0.0
 * @date       2025-11-15
 */

/* Includes ----------------------------------------------------------- */
#include "mw_ds_twr.h"
#include "mw_time_utils.h"

#include <stddef.h>
#include <string.h>

/* Private defines ---------------------------------------------------- */
#define DW1000_REG_RX_TIME     (0x15)
#define DW1000_REG_TX_TIME     (0x17)
#define TIMESTAMP_40BIT_MASK   (0x000000FFFFFFFFFFULL)
#define RX_BUFFER_SIZE         (128u)

/* Private macros ----------------------------------------------------- */
#define CHECK_PARAM(cond, ret) do { if (!(cond)) return (ret); } while(0)

/* Private function prototypes ---------------------------------------- */
static inline bool is_valid_hal(const mw_dstwr_hal_t *hal);

/* Public function definitions ---------------------------------------- */

mw_dstwr_err_t mw_dstwr_execute_tag(const mw_dstwr_config_t *config,
                                    mw_dstwr_result_t *result)
{
  CHECK_PARAM(config && config->hal, MW_DSTWR_ERR_PARAM);
  CHECK_PARAM(is_valid_hal(config->hal), MW_DSTWR_ERR_PARAM);

  const mw_dstwr_hal_t *hal = config->hal;
  uint64_t t1 = 0, t4 = 0, t5 = 0;
  uint8_t rx_buffer[RX_BUFFER_SIZE];
  uint16_t rx_length = 0;

  /* Step 1: Send POLL message */
  mw_dstwr_poll_msg_t poll_msg = {
    .msg_type = MW_DSTWR_MSG_TYPE_POLL,
    .sequence_num = config->sequence_num
  };

  if (hal->tx(&poll_msg, sizeof(poll_msg)) != 0)
    return MW_DSTWR_ERR;

  if (hal->read_timestamp(DW1000_REG_TX_TIME, 0x00, &t1) != 0)
    return MW_DSTWR_ERR;

  /* Step 2: Wait for RESPONSE */
  if (hal->rx_with_timeout(rx_buffer, sizeof(rx_buffer), &rx_length, config->rx_timeout_us) != 0)
    return MW_DSTWR_ERR_TIMEOUT;

  if (!mw_dstwr_validate_message(rx_buffer, rx_length, MW_DSTWR_MSG_TYPE_RESP, config->sequence_num))
    return MW_DSTWR_ERR_INVALID_MSG;

  if (hal->read_timestamp(DW1000_REG_RX_TIME, 0x00, &t4) != 0)
    return MW_DSTWR_ERR;

  /* Step 3: Send FINAL message */
  mw_dstwr_final_msg_t final_msg = {
    .msg_type = MW_DSTWR_MSG_TYPE_FINAL,
    .sequence_num = config->sequence_num,
    .poll_tx_timestamp = t1,
    .resp_rx_timestamp = t4,
    .final_tx_timestamp = 0
  };

  /* Send partial FINAL (without T5) */
  if (hal->tx(&final_msg, offsetof(mw_dstwr_final_msg_t, final_tx_timestamp)) != 0)
    return MW_DSTWR_ERR;

  /* Read T5 timestamp */
  if (hal->read_timestamp(DW1000_REG_TX_TIME, 0x00, &t5) != 0)
    return MW_DSTWR_ERR;

  final_msg.final_tx_timestamp = t5;

  /* Send complete FINAL */
  if (hal->tx(&final_msg, sizeof(final_msg)) != 0)
    return MW_DSTWR_ERR;

  /* Store results */
  if (result)
  {
    result->timestamps.t1 = t1;
    result->timestamps.t2 = 0;
    result->timestamps.t3 = 0;
    result->timestamps.t4 = t4;
    result->timestamps.t5 = t5;
    result->timestamps.t6 = 0;
    result->distance_m = -1.0f; /* Distance calculated on Anchor side */
    result->valid = true;
  }

  return MW_DSTWR_OK;
}

mw_dstwr_err_t mw_dstwr_execute_anchor(const mw_dstwr_config_t *config,
                                       mw_dstwr_result_t *result)
{
  CHECK_PARAM(config && config->hal, MW_DSTWR_ERR_PARAM);
  CHECK_PARAM(is_valid_hal(config->hal), MW_DSTWR_ERR_PARAM);

  const mw_dstwr_hal_t *hal = config->hal;
  uint64_t t2 = 0, t3 = 0, t6 = 0;
  uint8_t rx_buffer[RX_BUFFER_SIZE];
  uint16_t rx_length = 0;
  uint8_t poll_seq = 0;

  /* Step 1: Wait for POLL message */
  if (hal->rx_with_timeout(rx_buffer, sizeof(rx_buffer), &rx_length, config->rx_timeout_us) != 0)
    return MW_DSTWR_ERR_TIMEOUT;

  if (!mw_dstwr_validate_message(rx_buffer, rx_length, MW_DSTWR_MSG_TYPE_POLL, 0xFF))
    return MW_DSTWR_ERR_INVALID_MSG;

  const mw_dstwr_poll_msg_t *poll_msg = (const mw_dstwr_poll_msg_t *)rx_buffer;
  poll_seq = poll_msg->sequence_num;

  if (hal->read_timestamp(DW1000_REG_RX_TIME, 0x00, &t2) != 0)
    return MW_DSTWR_ERR;

  /* Step 2: Send RESPONSE message */
  mw_dstwr_resp_msg_t resp_msg = {
    .msg_type = MW_DSTWR_MSG_TYPE_RESP,
    .sequence_num = poll_seq
  };

  if (hal->tx(&resp_msg, sizeof(resp_msg)) != 0)
    return MW_DSTWR_ERR;

  if (hal->read_timestamp(DW1000_REG_TX_TIME, 0x00, &t3) != 0)
    return MW_DSTWR_ERR;

  /* Step 3: Wait for FINAL message (with retry logic) */
  uint32_t start_ms = hal->get_tick_ms();
  
  for (;;)
  {
    if (hal->rx_with_timeout(rx_buffer, sizeof(rx_buffer), &rx_length, config->rx_timeout_us) != 0)
      return MW_DSTWR_ERR_TIMEOUT;

    /* Check if valid FINAL message */
    if (mw_dstwr_validate_message(rx_buffer, rx_length, MW_DSTWR_MSG_TYPE_FINAL, poll_seq))
    {
      if (hal->read_timestamp(DW1000_REG_RX_TIME, 0x00, &t6) != 0)
        return MW_DSTWR_ERR;

      const mw_dstwr_final_msg_t *final_msg = (const mw_dstwr_final_msg_t *)rx_buffer;

      /* Extract 40-bit timestamps from Tag */
      uint64_t t1 = final_msg->poll_tx_timestamp & TIMESTAMP_40BIT_MASK;
      uint64_t t4 = final_msg->resp_rx_timestamp & TIMESTAMP_40BIT_MASK;
      uint64_t t5 = final_msg->final_tx_timestamp & TIMESTAMP_40BIT_MASK;

      /* Calculate distance */
      mw_dstwr_timestamps_t timestamps = {
        .t1 = t1, .t2 = t2, .t3 = t3,
        .t4 = t4, .t5 = t5, .t6 = t6
      };

      float distance = mw_dstwr_calculate_distance(&timestamps);
      
      if (distance < 0.0f)
        return MW_DSTWR_ERR;

      /* Store results */
      if (result)
      {
        result->timestamps = timestamps;
        result->distance_m = distance;
        result->valid = true;
      }

      return MW_DSTWR_OK;
    }

    /* Check overall timeout */
    if ((hal->get_tick_ms() - start_ms) * 1000UL > config->rx_timeout_us)
      return MW_DSTWR_ERR_TIMEOUT;
  }
}

float mw_dstwr_calculate_distance(const mw_dstwr_timestamps_t *timestamps)
{
  if (!timestamps)
    return -1.0f;

  return mw_ds_twr_calc(timestamps->t1, timestamps->t2, timestamps->t3,
                        timestamps->t4, timestamps->t5, timestamps->t6);
}

bool mw_dstwr_validate_message(const uint8_t *data, uint16_t length,
                               uint8_t expected_type, uint8_t expected_seq)
{
  if (!data || length < 2)
    return false;

  uint8_t msg_type = data[0];
  uint8_t msg_seq = data[1];

  /* Check message type */
  if (msg_type != expected_type)
    return false;

  /* Check sequence number (0xFF means skip check) */
  if (expected_seq != 0xFF && msg_seq != expected_seq)
    return false;

  /* Check minimum length based on message type */
  switch (expected_type)
  {
    case MW_DSTWR_MSG_TYPE_POLL:
      return length >= sizeof(mw_dstwr_poll_msg_t);
    case MW_DSTWR_MSG_TYPE_RESP:
      return length >= sizeof(mw_dstwr_resp_msg_t);
    case MW_DSTWR_MSG_TYPE_FINAL:
      return length >= sizeof(mw_dstwr_final_msg_t);
    default:
      return false;
  }
}

/* Private function definitions --------------------------------------- */

static inline bool is_valid_hal(const mw_dstwr_hal_t *hal)
{
  return (hal->tx != NULL &&
          hal->rx_with_timeout != NULL &&
          hal->read_timestamp != NULL &&
          hal->get_tick_ms != NULL);
}

/* End of file -------------------------------------------------------- */
