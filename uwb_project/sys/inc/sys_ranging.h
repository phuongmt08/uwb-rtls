/* ============================== sys_ranging.h ==============================
 * @file       sys_ranging.h
 * @brief      System-level ranging (DS-TWR state machine)
 * @version    1.0.0
 * @date       2025-09-28
 *
 * Notes:
 *  - Uses BSP UWB I/O (bsp_uwb_tx / bsp_uwb_rx).
 *  - Uses DW1000 time readouts and math helpers (mw_range_math).
 *  - DS-TWR implemented here; TDoA hooks reserved.
 */

#ifndef __SYS_RANGING_H
#define __SYS_RANGING_H

/* Includes ----------------------------------------------------------- */
#include <stdint.h>
#include <stdbool.h>
#include "err.h"
#include "bsp_uwb.h"
#include "dwm1000.h"
#include "mw_time_utils.h"

/* Public enumerate/structure ---------------------------------------- */
typedef enum
{
  SYS_RANGING_OK = 0,
  SYS_RANGING_ERR = -1,
  SYS_RANGING_ERR_PARAM = -2,
  SYS_RANGING_ERR_TIMEOUT = -3,
  SYS_RANGING_ERR_PROTO = -4
} sys_ranging_err_t;

typedef struct
{
  uint8_t  seq;             /*!< Sequence number */
  uint32_t rx_timeout_us;   /*!< Per-leg RX timeout (us) */
  dwm_config_t uwb_cfg;     /*!< UWB radio configuration */
} sys_ranging_cfg_t;
typedef struct
{
  float    distance_m;      /*!< DS-TWR distance (meters), valid on Anchor */
  uint64_t t1, t2, t3, t4, t5, t6; /* 40-bit device times in u64 LSBs */
} sys_ranging_result_t;

/* Public function prototypes ---------------------------------------- */
/**
 * @brief  Run one DS-TWR transaction as TAG (initiator).
 */
sys_ranging_err_t sys_ranging_ds_twr_tag_once(const sys_ranging_cfg_t *cfg,
                                              sys_ranging_result_t *res);

/**
 * @brief  Run one DS-TWR transaction as ANCHOR (responder).
 */
sys_ranging_err_t sys_ranging_ds_twr_anchor_once(const sys_ranging_cfg_t *cfg,
                                                 sys_ranging_result_t *res);

/* TDoA entry points (skeletons reserved) ---------------------------- */
/* sys_ranging_err_t sys_ranging_tdoa_listen(...); */

#endif /* __SYS_RANGING_H */

/* End of file -------------------------------------------------------- */
