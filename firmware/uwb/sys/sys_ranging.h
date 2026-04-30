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

/* Public enumerate/structure ---------------------------------------- */

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
 * @brief Ranging mode
 */
typedef enum {
  SYS_RANGING_MODE_SINGLE = 0,    /* Single anchor (legacy) */
  SYS_RANGING_MODE_TDMA = 1       /* Multi-anchor TDMA */
} sys_ranging_mode_t;

/**
 * @brief Local calibration state advertised in UWB packets.
 *
 * This is intentionally 1 byte so it can fit into existing packet padding.
 */
typedef enum {
  SYS_CALIB_STATUS_NORMAL = 0,
  SYS_CALIB_STATUS_COLLECTING = 1,
  SYS_CALIB_STATUS_CALCULATING = 2,
  SYS_CALIB_STATUS_PENDING_ACCEPT = 3,
  SYS_CALIB_STATUS_DONE = 4,
  SYS_CALIB_STATUS_FACTORY_RESET = 5
} sys_calib_status_t;

/**
 * @brief Ranging result
 */
typedef struct
{
  float    distance_m;
  uint64_t t1, t2, t3, t4, t5, t6;
  uint8_t  anchor_id;
  int8_t   rssi;
  uint8_t  quality;
  uint8_t  calib_status;
  bool     valid;
} sys_ranging_result_t;

/**
 * @brief Multi-anchor ranging results
 */

typedef struct
{
  sys_ranging_result_t results[NUM_ANCHORS];
  uint8_t count;          /* Number of valid results */
  uint8_t sequence_num;   /* Sequence number */
} sys_ranging_multi_result_t;

/**
 * @brief Ranging configuration
 */
typedef struct
{
  /* Mode selection */
  sys_ranging_mode_t mode;          /* SINGLE or TDMA */
  
  /* Common parameters */
  uint8_t  sequence_num;
  uint32_t rx_timeout_ms;
  
  /* Single-anchor mode */
  uint8_t  target_anchor_id;        /* Target anchor (0xFF = any) */
  
  /* TDMA multi-anchor mode */
  uint8_t  num_anchors;             /* Number of anchors (1-8) */
  uint8_t  anchor_ids[NUM_ANCHORS]; /* List of anchor IDs */
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
 * @brief Process Anchor TDMA ranging (call frequently in loop)
 * @param num_anchors Total number of anchors in network
 * @param anchor_ids Array of all anchor IDs in network
 * @param rx_timeout_ms RX timeout in milliseconds
 * @return
 *   - SYS_RANGING_OK: Ranging complete
 *   - SYS_RANGING_ERR: Error occurred
 *   - SYS_RANGING_ERR_TIMEOUT: Timeout (normal, no TAG poll)
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
 * @brief Reset ranging statistics
 */
void sys_ranging_reset_stats(void);

#endif /* __SYS_RANGING_H */