/* ============================== sys_ranging.h ==============================
 * @file       sys_ranging.h
 * @brief      System-level ranging with compile-time method selection
 * @version    2.0.0
 * @date       2025-11-15
 *
 * @details    Simple ranging API. Method selected at compile-time via defines:
 *             - RANGING_METHOD_DS_TWR (Double-Sided TWR)
 *             - RANGING_METHOD_SS_TWR (Single-Sided TWR)
 *             - RANGING_METHOD_ALT_DS_TWR (Alternative DS-TWR)
 *             - RANGING_METHOD_TDOA (Time Difference of Arrival)
 */

#ifndef __SYS_RANGING_H
#define __SYS_RANGING_H

/* Includes ----------------------------------------------------------- */
#include <stdint.h>
#include <stdbool.h>

/* Public enumerate/structure ---------------------------------------- */
typedef enum
{
  SYS_RANGING_OK = 0,
  SYS_RANGING_ERR = -1,
  SYS_RANGING_ERR_PARAM = -2,
  SYS_RANGING_ERR_TIMEOUT = -3,
  SYS_RANGING_ERR_PROTO = -4
} sys_ranging_err_t;

/**
 * @brief Ranging configuration
 */
typedef struct
{
  uint8_t  sequence_num;        /*!< Sequence number */
  uint32_t rx_timeout_us;       /*!< RX timeout (microseconds) */
} sys_ranging_config_t;

/**
 * @brief Ranging result
 */
typedef struct
{
  float    distance_m;              /*!< Distance in meters */
  uint64_t t1, t2, t3, t4, t5, t6;  /*!< Timestamps (40-bit in LSBs) */
  bool     valid;                   /*!< Result validity */
} sys_ranging_result_t;

/* Public function prototypes ---------------------------------------- */

/**
 * @brief Execute ranging as Tag (initiator)
 * @param config Configuration (sequence, timeout)
 * @param result Result structure (optional, can be NULL)
 * @return SYS_RANGING_OK on success
 */
sys_ranging_err_t sys_ranging_tag_once(const sys_ranging_config_t *config,
                                       sys_ranging_result_t *result);

/**
 * @brief Execute ranging as Anchor (responder)
 * @param config Configuration (sequence, timeout)
 * @param result Result structure (optional, can be NULL)
 * @return SYS_RANGING_OK on success
 */
sys_ranging_err_t sys_ranging_anchor_once(const sys_ranging_config_t *config,
                                          sys_ranging_result_t *result);

#endif /* __SYS_RANGING_H */

/* End of file -------------------------------------------------------- */
