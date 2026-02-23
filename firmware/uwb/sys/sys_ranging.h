/* ============================== sys_ranging.h ==============================
 * @file       sys_ranging.h
 * @brief      Non-blocking ranging API with state machine
 * @version    4.0.0
 * @date       2025-11-26
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
  SYS_RANGING_ERR_NO_RESULT = -7       /* No result available */
} sys_ranging_err_t;

/**
 * @brief Ranging configuration (for legacy blocking API)
 */
typedef struct
{
  uint8_t  sequence_num;
  uint32_t rx_timeout_us;
} sys_ranging_config_t;

/**
 * @brief Ranging result
 */
typedef struct
{
  float    distance_m;
  uint64_t t1, t2, t3, t4, t5, t6;
  uint8_t  anchor_id;  /* Which anchor (for multiple anchor mode) */
  int8_t  rssi;       /* RSSI for diagnostics */
  bool     valid;
} sys_ranging_result_t;

#ifdef MULTIPLE_ANCHOR
/**
 * @brief Multiple anchor results
 */
typedef struct
{
  sys_ranging_result_t results[NUM_ANCHORS];
  uint8_t count;  /* Number of valid results */
} sys_ranging_multi_result_t;
#endif

/* Non-blocking API --------------------------------------------------- */

/**
 * @brief Start Tag ranging (non-blocking)
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

/* Multiple Anchor API (TAG only) ------------------------------------ */
#ifdef MULTIPLE_ANCHOR
/**
 * @brief Start Tag ranging with specific anchor
 * @param anchor_id Target anchor ID (or 0xFF for any/broadcast)
 * @param sequence_num Sequence number
 * @param rx_timeout_ms RX timeout in milliseconds
 * @return SYS_RANGING_OK if started successfully
 */
sys_ranging_err_t sys_ranging_tag_start_with_anchor(uint8_t anchor_id, 
                                                     uint8_t sequence_num, 
                                                     uint32_t rx_timeout_ms);

/**
 * @brief Range with multiple anchors sequentially
 * @param anchor_ids Array of anchor IDs to range with
 * @param num_anchors Number of anchors in array (max 8)
 * @param results Output array for results (must be size num_anchors)
 * @param sequence_num Starting sequence number
 * @param rx_timeout_ms RX timeout per anchor
 * @return Number of successful ranging operations (0 to num_anchors)
 */
int sys_ranging_tag_multi_anchor(const uint8_t *anchor_ids,
                                 uint8_t num_anchors,
                                 sys_ranging_result_t *results,
                                 uint8_t sequence_num,
                                 uint32_t rx_timeout_ms);
#endif

/* Legacy blocking API (compatibility) -------------------------------- */
sys_ranging_err_t sys_ranging_tag_once(const sys_ranging_config_t *config,
                                       sys_ranging_result_t *result);

sys_ranging_err_t sys_ranging_anchor_once(const sys_ranging_config_t *config,
                                          sys_ranging_result_t *result);

#endif /* __SYS_RANGING_H */