/**
 * @file       mw_ds_twr.h
 * @copyright
 * @license
 * @version    2.2.1
 * @date       2025-12-28
 * @author     Phuong Mai
 * @brief      FIXED: Added MW_DSTWR_ERR_BUSY error code for state guard
 * @note       FIX: New error type to indicate ranging sequence not yet complete
 */
#ifndef MW_DS_TWR_H
#define MW_DS_TWR_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stdbool.h>

/* ====================================================================
 * ERROR CODES
 * ==================================================================== */
typedef enum {
  MW_DSTWR_OK = 0,
  MW_DSTWR_ERR = -1,
  MW_DSTWR_ERR_TIMEOUT = -2,
  MW_DSTWR_ERR_INVALID_MSG = -3,
  MW_DSTWR_ERR_PARAM = -4,
  MW_DSTWR_ERR_BUSY = -5  /* NEW: Previous ranging sequence not yet complete */
} mw_dstwr_err_t;

/* ====================================================================
 * MESSAGE TYPES
 * ==================================================================== */
typedef enum {
  MW_DSTWR_MSG_TYPE_POLL       = 0xE1,
  MW_DSTWR_MSG_TYPE_RESP       = 0xE2,
  MW_DSTWR_MSG_TYPE_FINAL      = 0xE3,
  MW_DSTWR_MSG_TYPE_RESULT     = 0xE5,
  MW_DSTWR_MSG_TYPE_CORRECTION = 0xE6
} mw_dstwr_msg_type_t;

/* ====================================================================
 * MESSAGE STRUCTURES
 * ==================================================================== */
typedef struct __attribute__((packed)) {
  uint8_t msg_type;        /* MW_DSTWR_MSG_TYPE_POLL */
  uint8_t sequence_num;
  uint8_t target_anchor;   /* 0xFF = any anchor, or specific anchor ID */
  uint8_t rssi_last;       /* RSSI of last received message (optional) */
  uint8_t padding[8];
} mw_dstwr_poll_msg_t;

typedef struct __attribute__((packed)) {
  uint8_t msg_type;        /* MW_DSTWR_MSG_TYPE_RESP */
  uint8_t sequence_num;
  uint8_t anchor_id;       /* Responding anchor's ID */
  uint8_t rssi_last;
  uint8_t padding[8];
} mw_dstwr_resp_msg_t;

typedef struct __attribute__((packed)) {
  uint8_t msg_type;        /* MW_DSTWR_MSG_TYPE_FINAL */
  uint8_t sequence_num;
  uint64_t poll_tx_timestamp;  /* T1 */
  uint64_t resp_rx_timestamp;  /* T4 */
  uint64_t final_tx_timestamp; /* T5 (placeholder for HAVE_TX_DELAY) */
} mw_dstwr_final_msg_t;

typedef struct __attribute__((packed)) {
  uint8_t msg_type;        /* MW_DSTWR_MSG_TYPE_CORRECTION */
  uint8_t sequence_num;
  uint64_t final_tx_timestamp;  /* Actual T5 from TX_TIME register */
  int32_t distance_mm;          /* Placeholder for anchor to fill */
} mw_dstwr_correction_msg_t;

typedef struct __attribute__((packed)) {
  uint8_t msg_type;        /* MW_DSTWR_MSG_TYPE_RESULT */
  uint8_t sequence_num;
  int32_t distance_mm;     /* Distance in millimeters */
  uint8_t anchor_id;       /* Anchor that calculated this distance */
  uint8_t rssi_final;      /* RSSI of FINAL message */
  uint8_t padding[4];      /* Increased to 4 bytes to match validation (total 12 bytes) */
} mw_dstwr_result_msg_t;

/* ====================================================================
 * TIMESTAMP STRUCTURE
 * ==================================================================== */
typedef struct {
  uint64_t t1;  /* Tag: POLL TX */
  uint64_t t2;  /* Anchor: POLL RX */
  uint64_t t3;  /* Anchor: RESPONSE TX */
  uint64_t t4;  /* Tag: RESPONSE RX */
  uint64_t t5;  /* Tag: FINAL TX */
  uint64_t t6;  /* Anchor: FINAL RX */
} mw_dstwr_timestamps_t;

/* ====================================================================
 * RESULT STRUCTURE
 * ==================================================================== */
typedef struct {
  mw_dstwr_timestamps_t timestamps;
  float distance_m;
  uint8_t anchor_id;
  int8_t rssi;
  bool valid;
} mw_dstwr_result_t;

/* ====================================================================
 * HAL INTERFACE
 * ==================================================================== */
typedef struct {
  /* Required callbacks */
  int (*tx)(const void *data, uint16_t length);
  int (*rx_with_timeout)(void *buffer, uint16_t buffer_size, uint16_t *received_length, uint32_t timeout_us);
  int (*read_timestamp)(uint8_t reg, uint16_t offset, uint64_t *timestamp);
  
  /* Optional callbacks */
  int (*tx_delayed)(const void *data, uint16_t length, uint64_t tx_timestamp);
  uint16_t (*get_tx_antenna_delay)(void);
  int (*get_rssi)(void);
} mw_dstwr_hal_t;

/* ====================================================================
 * CONFIGURATION
 * ==================================================================== */
typedef struct {
  const mw_dstwr_hal_t *hal;
  uint32_t rx_timeout_us;
  uint8_t sequence_num;
  uint8_t target_anchor_id;  /* 0xFF = any anchor */
} mw_dstwr_config_t;

/* ====================================================================
 * PUBLIC API
 * ==================================================================== */

/**
 * @brief Execute DS-TWR as TAG
 * @param config Configuration (timeout, target anchor, etc.)
 * @param result Result structure to fill (can be NULL)
 * @return MW_DSTWR_OK on success, error code otherwise
 *         MW_DSTWR_ERR_BUSY if previous ranging sequence not yet complete
 */
mw_dstwr_err_t mw_dstwr_execute_tag(const mw_dstwr_config_t *config,
                                    mw_dstwr_result_t *result);

/**
 * @brief Execute DS-TWR as ANCHOR
 * @param config Configuration (timeout, etc.)
 * @param result Result structure to fill (can be NULL)
 * @return MW_DSTWR_OK on success, error code otherwise
 */
mw_dstwr_err_t mw_dstwr_execute_anchor(const mw_dstwr_config_t *config,
                                       mw_dstwr_result_t *result);

/**
 * @brief Calculate distance from timestamps
 * @param timestamps Pointer to timestamp structure
 * @return Distance in meters, or negative on error
 */
float mw_dstwr_calculate_distance(const mw_dstwr_timestamps_t *timestamps);

/**
 * @brief Validate received message
 * @param data Message buffer
 * @param length Message length
 * @param expected_type Expected message type
 * @param expected_seq Expected sequence number (0xFF = don't check)
 * @return true if valid, false otherwise
 */
bool mw_dstwr_validate_message(const uint8_t *data, uint16_t length,
                               uint8_t expected_type, uint8_t expected_seq);

/**
 * @brief Reset ranging state machine (for external use)
 * @note Call this if you need to force-reset the state (e.g., after timeout)
 */
void mw_dstwr_reset_state(void);

#ifdef __cplusplus
}
#endif

#endif /* MW_DS_TWR_H */