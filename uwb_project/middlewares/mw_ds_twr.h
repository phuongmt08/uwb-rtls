/* ============================== mw_ds_twr.h ================================
 * @file       mw_ds_twr.h
 * @brief      Middleware - Double-Sided Two-Way Ranging (DS-TWR) protocol
 * @version    1.0.0
 * @date       2025-11-15
 * 
 * @details    Pure DS-TWR protocol implementation without system dependencies.
 *             This layer handles message formatting and protocol state machine.
 *             Does not depend on logging, hardware abstraction, or system layer.
 */

#ifndef __MW_DS_TWR_H
#define __MW_DS_TWR_H

/* Includes ----------------------------------------------------------- */
#include <stdint.h>
#include <stdbool.h>

/* Public defines ----------------------------------------------------- */
#define MW_DSTWR_MSG_TYPE_POLL    (0xE1u)
#define MW_DSTWR_MSG_TYPE_RESP    (0xE2u)
#define MW_DSTWR_MSG_TYPE_FINAL   (0xE3u)

/* Public enumerate/structure ----------------------------------------- */
typedef enum
{
  MW_DSTWR_OK = 0,
  MW_DSTWR_ERR = -1,
  MW_DSTWR_ERR_PARAM = -2,
  MW_DSTWR_ERR_TIMEOUT = -3,
  MW_DSTWR_ERR_INVALID_MSG = -4
} mw_dstwr_err_t;

/**
 * @brief DS-TWR role
 */
typedef enum
{
  MW_DSTWR_ROLE_TAG = 0,    /* Initiator */
  MW_DSTWR_ROLE_ANCHOR = 1  /* Responder */
} mw_dstwr_role_t;

/**
 * @brief DS-TWR message types (packed structs for UWB transmission)
 */
#pragma pack(push, 1)

typedef struct
{
  uint8_t msg_type;      /* Message type identifier */
  uint8_t sequence_num;  /* Sequence number */
} mw_dstwr_poll_msg_t;

typedef struct
{
  uint8_t msg_type;      /* Message type identifier */
  uint8_t sequence_num;  /* Sequence number */
} mw_dstwr_resp_msg_t;

typedef struct
{
  uint8_t  msg_type;          /* Message type identifier */
  uint8_t  sequence_num;      /* Sequence number */
  uint64_t poll_tx_timestamp; /* T1: 40-bit in LSBs */
  uint64_t resp_rx_timestamp; /* T4: 40-bit in LSBs */
  uint64_t final_tx_timestamp;/* T5: 40-bit in LSBs */
} mw_dstwr_final_msg_t;

#pragma pack(pop)

/**
 * @brief DS-TWR timestamps structure
 */
typedef struct
{
  uint64_t t1; /* Poll TX (Tag side) */
  uint64_t t2; /* Poll RX (Anchor side) */
  uint64_t t3; /* Response TX (Anchor side) */
  uint64_t t4; /* Response RX (Tag side) */
  uint64_t t5; /* Final TX (Tag side) */
  uint64_t t6; /* Final RX (Anchor side) */
} mw_dstwr_timestamps_t;

/**
 * @brief Hardware abstraction callbacks for DS-TWR
 * These function pointers decouple middleware from hardware layer
 */
typedef struct
{
  /**
   * @brief Transmit UWB frame
   * @param data Pointer to data buffer
   * @param length Length of data
   * @return 0 on success, negative on error
   */
  int (*tx)(const void *data, uint16_t length);

  /**
   * @brief Receive UWB frame with timeout
   * @param buffer Pointer to receive buffer
   * @param buffer_size Size of buffer
   * @param received_length Pointer to store received length
   * @param timeout_us Timeout in microseconds
   * @return 0 on success, negative on error/timeout
   */
  int (*rx_with_timeout)(uint8_t *buffer, uint16_t buffer_size,
                         uint16_t *received_length, uint32_t timeout_us);

  /**
   * @brief Read 40-bit timestamp from UWB chip register
   * @param reg_addr Register address
   * @param sub_addr Sub-address
   * @param timestamp Pointer to store 64-bit timestamp (40-bit in LSBs)
   * @return 0 on success, negative on error
   */
  int (*read_timestamp)(uint8_t reg_addr, uint8_t sub_addr, uint64_t *timestamp);

  /**
   * @brief Get current system tick in milliseconds
   * @return Current tick value
   */
  uint32_t (*get_tick_ms)(void);

} mw_dstwr_hal_t;

/**
 * @brief DS-TWR configuration
 */
typedef struct
{
  uint8_t  sequence_num;      /* Sequence number for this transaction */
  uint32_t rx_timeout_us;     /* RX timeout per message (microseconds) */
  const mw_dstwr_hal_t *hal;  /* Hardware abstraction layer callbacks */
} mw_dstwr_config_t;

/**
 * @brief DS-TWR result
 */
typedef struct
{
  mw_dstwr_timestamps_t timestamps; /* All 6 timestamps */
  float distance_m;                 /* Calculated distance (meters) */
  bool  valid;                      /* Result validity flag */
} mw_dstwr_result_t;

/* Public function prototypes ----------------------------------------- */

/**
 * @brief Execute one DS-TWR transaction as Tag (initiator)
 * @param config Configuration with sequence number and HAL callbacks
 * @param result Pointer to result structure (optional, can be NULL)
 * @return MW_DSTWR_OK on success, error code otherwise
 */
mw_dstwr_err_t mw_dstwr_execute_tag(const mw_dstwr_config_t *config,
                                    mw_dstwr_result_t *result);

/**
 * @brief Execute one DS-TWR transaction as Anchor (responder)
 * @param config Configuration with sequence number and HAL callbacks
 * @param result Pointer to result structure (optional, can be NULL)
 * @return MW_DSTWR_OK on success, error code otherwise
 */
mw_dstwr_err_t mw_dstwr_execute_anchor(const mw_dstwr_config_t *config,
                                       mw_dstwr_result_t *result);

/**
 * @brief Calculate distance from DS-TWR timestamps
 * @param timestamps Pointer to timestamps structure
 * @return Distance in meters, negative on error
 */
float mw_dstwr_calculate_distance(const mw_dstwr_timestamps_t *timestamps);

/**
 * @brief Validate DS-TWR message type
 * @param data Pointer to received data
 * @param length Length of data
 * @param expected_type Expected message type
 * @param expected_seq Expected sequence number (0xFF to skip check)
 * @return true if valid, false otherwise
 */
bool mw_dstwr_validate_message(const uint8_t *data, uint16_t length,
                               uint8_t expected_type, uint8_t expected_seq);

#endif /* __MW_DS_TWR_H */

/* End of file -------------------------------------------------------- */
