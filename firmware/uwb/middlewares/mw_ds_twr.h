/* ============================== mw_ds_twr.h ================================
 * @file       mw_ds_twr.h
 * @brief      DS-TWR with proper T5 correction message
 * @version    2.0.0
 * @date       2025-12-13
 */

#ifndef __MW_DS_TWR_H
#define __MW_DS_TWR_H

#include <stdint.h>
#include <stdbool.h>

/* Public defines ----------------------------------------------------- */
#define MW_DSTWR_MSG_TYPE_POLL        (0xEEu)
#define MW_DSTWR_MSG_TYPE_RESP        (0xE2u)
#define MW_DSTWR_MSG_TYPE_FINAL       (0xE3u)
#define MW_DSTWR_MSG_TYPE_CORRECTION  (0xE4u)  // NEW!

/* Minimum frame size for reliable transmission (including CRC) */
#define MW_DSTWR_MIN_FRAME_SIZE   (12u)

/* Public enumerate/structure ----------------------------------------- */
typedef enum
{
  MW_DSTWR_OK = 0,
  MW_DSTWR_ERR = -1,
  MW_DSTWR_ERR_PARAM = -2,
  MW_DSTWR_ERR_TIMEOUT = -3,
  MW_DSTWR_ERR_INVALID_MSG = -4
} mw_dstwr_err_t;

typedef enum
{
  MW_DSTWR_ROLE_TAG = 0,
  MW_DSTWR_ROLE_ANCHOR = 1
} mw_dstwr_role_t;

/**
 * @brief DS-TWR message structures
 */
#pragma pack(push, 1)

/**
 * @brief POLL message - PADDED to 12 bytes
 * Format: [msg_type][seq][padding x10]
 */
typedef struct
{
  uint8_t msg_type;      /* 0xEE */
  uint8_t sequence_num;  /* Sequence number */
  uint8_t padding[10];   /* Padding to reach 12 bytes */
} mw_dstwr_poll_msg_t;

/**
 * @brief RESPONSE message - PADDED to 12 bytes
 * Format: [msg_type][seq][padding x10]
 */
typedef struct
{
  uint8_t msg_type;      /* 0xE2 */
  uint8_t sequence_num;  /* Sequence number */
  uint8_t padding[10];   /* Padding to reach 12 bytes */
} mw_dstwr_resp_msg_t;

/**
 * @brief FINAL message (26 bytes)
 * Format: [msg_type][seq][T1:8][T4:8][T5:8]
 * NOTE: T5 will be 0 initially, real value comes in CORRECTION message
 */
typedef struct
{
  uint8_t  msg_type;          /* 0xE3 */
  uint8_t  sequence_num;      /* Sequence number */
  uint64_t poll_tx_timestamp; /* T1: 40-bit in LSBs */
  uint64_t resp_rx_timestamp; /* T4: 40-bit in LSBs */
  uint64_t final_tx_timestamp;/* T5: 40-bit in LSBs (will be 0) */
} mw_dstwr_final_msg_t;

/**
 * @brief CORRECTION message - PADDED to 12 bytes
 * Format: [msg_type][seq][T5:8][padding:2]
 * Contains the actual T5 timestamp read after FINAL transmission
 */
typedef struct
{
  uint8_t  msg_type;          /* 0xE4 */
  uint8_t  sequence_num;      /* Sequence number */
  uint64_t final_tx_timestamp;/* T5: 40-bit in LSBs (CORRECT VALUE) */
  uint8_t  padding[2];        /* Padding to reach 12 bytes minimum */
} mw_dstwr_correction_msg_t;

#pragma pack(pop)

/**
 * @brief DS-TWR timestamps structure
 */
typedef struct
{
  uint64_t t1, t2, t3, t4, t5, t6;
} mw_dstwr_timestamps_t;

/**
 * @brief Hardware abstraction callbacks
 */
typedef struct
{
  int (*tx)(const void *data, uint16_t length);
  int (*rx_with_timeout)(uint8_t *buffer, uint16_t buffer_size,
                         uint16_t *received_length, uint32_t timeout_us);
  int (*read_timestamp)(uint8_t reg_addr, uint8_t sub_addr, uint64_t *timestamp);
  uint32_t (*get_tick_ms)(void);
} mw_dstwr_hal_t;

/**
 * @brief DS-TWR configuration
 */
typedef struct
{
  uint8_t  sequence_num;
  uint32_t rx_timeout_us;
  const mw_dstwr_hal_t *hal;
} mw_dstwr_config_t;

/**
 * @brief DS-TWR result
 */
typedef struct
{
  mw_dstwr_timestamps_t timestamps;
  float distance_m;
  bool  valid;
} mw_dstwr_result_t;

/* Public function prototypes ----------------------------------------- */
mw_dstwr_err_t mw_dstwr_execute_tag(const mw_dstwr_config_t *config,
                                    mw_dstwr_result_t *result);

mw_dstwr_err_t mw_dstwr_execute_anchor(const mw_dstwr_config_t *config,
                                       mw_dstwr_result_t *result);

float mw_dstwr_calculate_distance(const mw_dstwr_timestamps_t *timestamps);

bool mw_dstwr_validate_message(const uint8_t *data, uint16_t length,
                               uint8_t expected_type, uint8_t expected_seq);

#endif /* __MW_DS_TWR_H */