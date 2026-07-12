/* ============================== sys_ranging.h ==============================
 * @file       sys_ranging.h
 * @brief      Non-blocking ranging API with TDMA support
 * @version    5.0.0
 * @date       2026-01-31
 * 
 */

#ifndef __SYS_RANGING_H
#define __SYS_RANGING_H

/* Includes ----------------------------------------------------------- */
#include <stdint.h>
#include <stdbool.h>
#include "config.h"
#include "positioning_config.h"

/* Public types ------------------------------------------------------ */
typedef enum
{
  SYS_RANGING_OK = 0,
  SYS_RANGING_ERR = -1,
  SYS_RANGING_ERR_PARAM = -2,
  SYS_RANGING_ERR_TIMEOUT = -3,
  SYS_RANGING_ERR_PROTO = -4,
  SYS_RANGING_ERR_BUSY = -5,           /* State machine busy */
  SYS_RANGING_ERR_NOT_STARTED = -6,    /* Not started yet */
  SYS_RANGING_ERR_NO_RESULT = -7,      /* No result available */
  SYS_RANGING_ERR_PARTIAL = -8,        /* Partial success (some anchors) */
  SYS_RANGING_ERR_SYNC_LOST = -9       /* TDMA sync lost */
} sys_ranging_err_t;

/**
 * @brief Local calibration state advertised in UWB packets.
 *
 * This is intentionally 1 byte so it can fit into existing packet padding.
 * Only two states matter:
 *   NORMAL = local survey data is not ready
 *   DONE   = local survey summary was acknowledged by the collector
 * Network exit is coordinated separately by SURVEY_COMPLETE/ABORT.
 */
typedef enum
{
  SYS_CALIB_STATUS_NORMAL = 0,
  SYS_CALIB_STATUS_DONE   = 1
} sys_calib_status_t;

/**
 * @brief Ranging result
 */
typedef struct
{
  float    distance_m;
  uint64_t t1, t2, t3, t4, t5, t6;
  uint8_t  anchor_id;
  uint16_t fp_amp_norm_q8;
  uint16_t fp_snr_q8;
  uint8_t  fp_confidence_q8;
  uint8_t  quality;
  uint8_t  calib_status;
  bool     valid;
} sys_ranging_result_t;

/**
 * @brief Multi-anchor ranging results
 */

typedef struct
{
  sys_ranging_result_t results[MAX_ANCHORS_SUPPORTED];
  uint8_t count;          /* Number of valid results */
  uint8_t sequence_num;   /* Sequence number */
} sys_ranging_multi_result_t;

/**
 * @brief Registered control-message types carried outside DS-TWR exchanges.
 */
typedef enum
{
  SYS_UWB_CTRL_CALIB_PAIR_SUMMARY = 0xE5,
  SYS_UWB_CTRL_ACK                = 0xE6,
  SYS_UWB_CTRL_SURVEY_FINISH      = 0xE7
} sys_uwb_control_msg_type_t;

typedef enum
{
  SYS_SURVEY_FINISH_ABORT    = 0,
  SYS_SURVEY_FINISH_COMPLETE = 1
} sys_survey_finish_outcome_t;

/* Control-message wire formats ------------------------------------- */
typedef struct __attribute__((packed))
{
  uint8_t  peer_id;
  float    known_m;
  float    mean_m;
  float    std_m;
  float    timeout_rate;
  uint8_t  valid_count;
} sys_calib_pair_summary_item_t;

typedef struct __attribute__((packed))
{
  uint8_t msg_type;
  uint8_t epoch_id;
  uint8_t sender_id;
  uint8_t pair_count;
  uint16_t current_tx_delay;
  uint16_t current_rx_delay;
  uint16_t current_combined_delay;
  sys_calib_pair_summary_item_t pair[SURVEY_NUM_ANCHORS - 1U];
} sys_calib_pair_summary_msg_t;

typedef struct __attribute__((packed))
{
  uint8_t msg_type;
  uint8_t epoch_id;
  uint8_t sender_id;
  uint8_t acked_msg_type;
  uint8_t acked_value;
} sys_uwb_control_ack_msg_t;

typedef struct __attribute__((packed))
{
  uint8_t msg_type;
  uint8_t epoch_id;
  uint8_t collector_id;
  uint8_t outcome;
} sys_survey_finish_msg_t;

/**
 * @brief Ranging configuration
 */
typedef struct
{
  /* Common parameters */
  uint8_t  sequence_num;
  uint32_t rx_timeout_ms;
  
  /* Single-anchor mode */
  uint8_t  target_anchor_id;        /* Target anchor (0xFF = any) */
  
  /* TDMA multi-anchor mode */
  uint8_t  num_anchors;             /* Number of anchors (1-8) */
  uint8_t  anchor_ids[MAX_ANCHORS_SUPPORTED]; /* List of anchor IDs */
  uint32_t slot_duration_ms;        /* TDMA slot duration (0 = default) */
} sys_ranging_config_t;

/* ====================================================================
 * NON-BLOCKING API - TDMA MULTI-ANCHOR MODE
 * ==================================================================== */

/**
 * @brief Start Tag ranging in TDMA mode (range with multiple anchors)
 * @param num_anchors Number of anchors to range with (1-8)
 * @param anchor_ids Array of anchor IDs
 * @param sequence_num Sequence number
 * @param rx_timeout_ms RX timeout in milliseconds (0 = use default)
 * @return SYS_RANGING_OK if started successfully
 */
sys_ranging_err_t sys_ranging_tag_start_tdma(uint8_t num_anchors,
                                             const uint8_t *anchor_ids,
                                             uint8_t sequence_num,
                                             uint32_t rx_timeout_ms);

/**
 * @brief Process Tag TDMA ranging (call frequently in loop)
 * @param num_anchors Number of anchors
 * @param anchor_ids Array of anchor IDs
 * @param rx_timeout_ms RX timeout in milliseconds
 * @return 
 *   - SYS_RANGING_OK: Ranging complete
 *   - SYS_RANGING_ERR: Error occurred
 *   - SYS_RANGING_ERR_TIMEOUT: Timeout
 */
sys_ranging_err_t sys_ranging_tag_process_tdma(uint8_t num_anchors,
                                               const uint8_t *anchor_ids,
                                               uint32_t rx_timeout_ms);

/**
 * @brief Get Tag TDMA ranging results (only after SYS_RANGING_OK or ERR_PARTIAL)
 * @param results Output multi-anchor results structure
 * @return SYS_RANGING_OK if results available
 */
sys_ranging_err_t sys_ranging_tag_get_results_tdma(sys_ranging_multi_result_t *results);

/**
 * @brief Get last anchor ranging result
 * @param result Pointer to result structure
 * @return SYS_RANGING_OK if valid result available
 */
sys_ranging_err_t sys_ranging_anchor_get_last_result(sys_ranging_result_t *result);

/**
 * @brief Start Anchor ranging in TDMA mode
 * @param anchor_id This anchor's ID (1-8)
 * @param num_anchors Total number of anchors in network
 * @param anchor_ids Array of all anchor IDs in network
 * @param rx_timeout_ms RX timeout in milliseconds
 * @return SYS_RANGING_OK if started successfully
 */
sys_ranging_err_t sys_ranging_anchor_start_tdma(uint8_t anchor_id,
                                                uint8_t num_anchors,
                                                const uint8_t *anchor_ids,
                                                uint32_t rx_timeout_ms);

/**
 * @brief Set the calibration state advertised by the local device in outgoing packets.
 */
void sys_ranging_set_calib_status(sys_calib_status_t status);

/**
 * @brief Read back the current outgoing calibration state.
 */
sys_calib_status_t sys_ranging_get_calib_status(void);

/**
 * @brief Get the current TDMA slot ID (0=Idle/Poll, 1-N=Anchor slots)
 */
uint8_t sys_ranging_get_current_slot(void);

/**
 * @brief Get the current superframe counter (synced across network)
 */
uint32_t sys_ranging_get_superframe_count(void);

/**
 * @brief Process Anchor TDMA ranging (call frequently in loop).
 *
 * If sys_ranging_anchor_start_tdma() was called first, this processes that
 * explicit transaction. If the anchor is idle, this function owns the normal
 * anchor receive policy: performance listens continuously, while lower-power
 * modes use discovery/tracking receive windows around expected POLL timing.
 * @param num_anchors Total number of anchors in network
 * @param anchor_ids Array of all anchor IDs in network
 * @param rx_timeout_ms RX timeout in milliseconds
 * @return
 *   - SYS_RANGING_OK: Ranging complete
 *   - SYS_RANGING_ERR_BUSY: No complete ranging result yet
 *   - SYS_RANGING_ERR: Error occurred
 *   - SYS_RANGING_ERR_TIMEOUT: Transaction timeout
 */
sys_ranging_err_t sys_ranging_anchor_process_tdma(uint8_t num_anchors,
                                                  const uint8_t *anchor_ids,
                                                  uint32_t rx_timeout_ms);

/**
 * @brief Get Anchor TDMA ranging result (only after SYS_RANGING_OK)
 * @param result Output result structure
 * @return SYS_RANGING_OK if result available
 */
sys_ranging_err_t sys_ranging_anchor_get_result_tdma(sys_ranging_result_t *result);

/* ====================================================================
 * CONTROL MESSAGE API
 * ==================================================================== */

/**
 * @brief Send or receive a registered control message.
 *
 * Control messages use byte 0 as type and byte 1 as epoch. A non-zero slot_id
 * applies the shared deterministic control-message slot delay before TX.
 */
sys_ranging_err_t sys_ranging_control_send(sys_uwb_control_msg_type_t type,
                                           const void *msg,
                                           uint16_t msg_size,
                                           uint8_t slot_id);
sys_ranging_err_t sys_ranging_control_receive(sys_uwb_control_msg_type_t type,
                                              void *msg,
                                              uint16_t msg_size,
                                              uint32_t timeout_ms);
sys_ranging_err_t sys_ranging_control_send_ack(uint8_t epoch_id,
                                               uint8_t sender_id,
                                               sys_uwb_control_msg_type_t acked_type,
                                               uint8_t acked_value,
                                               uint8_t slot_id);
sys_ranging_err_t sys_ranging_control_receive_ack(sys_uwb_control_msg_type_t acked_type,
                                                  sys_uwb_control_ack_msg_t *ack,
                                                  uint32_t timeout_ms);
sys_ranging_err_t sys_ranging_control_send_wait_ack(sys_uwb_control_msg_type_t type,
                                                    const void *msg,
                                                    uint16_t msg_size,
                                                    uint8_t slot_id,
                                                    uint8_t expected_ack_sender,
                                                    uint8_t expected_ack_value,
                                                    uint32_t ack_timeout_ms);

/* ====================================================================
 * NON-BLOCKING API - LEGACY SINGLE-ANCHOR MODE (backward compatible)
 * ==================================================================== */

/**
 * @brief Start Tag ranging (non-blocking, single anchor)
 * @param sequence_num Sequence number
 * @param rx_timeout_ms RX timeout in milliseconds
 * @return SYS_RANGING_OK if started successfully
 */
sys_ranging_err_t sys_ranging_tag_start(uint8_t sequence_num, uint32_t rx_timeout_ms);

/**
 * @brief Process Tag state machine (call frequently in loop)
 * @return 
 *   - SYS_RANGING_OK: Ranging complete
 *   - SYS_RANGING_ERR_BUSY: Still processing
 *   - Other: Error occurred
 */
sys_ranging_err_t sys_ranging_tag_process(void);

/**
 * @brief Get Tag ranging result (only after SYS_RANGING_OK)
 * @param result Output result structure
 * @return SYS_RANGING_OK if result available
 */
sys_ranging_err_t sys_ranging_tag_get_result(sys_ranging_result_t *result);

/**
 * @brief Start Anchor ranging (non-blocking)
 * @param rx_timeout_ms RX timeout in milliseconds
 * @return SYS_RANGING_OK if started successfully
 */
sys_ranging_err_t sys_ranging_anchor_start(uint32_t rx_timeout_ms);

/**
 * @brief Process Anchor state machine (call frequently in loop)
 * @return
 *   - SYS_RANGING_OK: Ranging complete
 *   - SYS_RANGING_ERR_BUSY: Still processing
 *   - SYS_RANGING_ERR_TIMEOUT: Timeout (normal for anchor)
 *   - Other: Error occurred
 */
sys_ranging_err_t sys_ranging_anchor_process(void);

/**
 * @brief Get Anchor ranging result (only after SYS_RANGING_OK)
 * @param result Output result structure
 * @return SYS_RANGING_OK if result available
 */
sys_ranging_err_t sys_ranging_anchor_get_result(sys_ranging_result_t *result);

/**
 * @brief Get remaining time in milliseconds until the active ranging deadline
 * @return Remaining time in ms (1-10 ms)
 */
uint32_t sys_ranging_get_ms_to_deadline(void);

/**
 * @brief Return true while the shared ranging state machine is inside a live
 *        TAG or ANCHOR transaction.
 */
bool sys_ranging_is_active(void);

/**
 * @brief Reset ranging statistics
 */
void sys_ranging_reset_stats(void);

/**
 * @brief Abort any ongoing ranging and reset state machine to IDLE
 */
void sys_ranging_abort(void);

#endif /* __SYS_RANGING_H */
