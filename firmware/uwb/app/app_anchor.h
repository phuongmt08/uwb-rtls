/**
 * @file       app_anchor.h
 * @copyright
 * @license
 * @version    1.1.0
 * @date       2025-12-24
 * @author     Phuong Mai
 * @brief      Non-blocking Anchor with binary search auto-calibration
 * @note       
 * Calibration Algorithm:
 * 1. Collect samples → calculate mean error
 * 2. Adjust antenna delay using binary search
 * 3. Repeat until error < threshold OR delta < min_step
 * @example    None
 */
#ifndef __APP_ANCHOR_H
#define __APP_ANCHOR_H

/* Includes ----------------------------------------------------------- */
#include "common.h"
#include "positioning_config.h"
#include "bsp_io.h"

/* Public function prototypes ----------------------------------------- */
/**
 * @brief Initialize anchor application
 */
app_err_t app_anchor_init(void);

/**
 * @brief Process anchor state machine (non-blocking)
 * @param arg Reserved for future use
 */
void app_anchor_process(void *arg);

#if ENABLE_ANCHOR_AUTO_CALIB
/**
 * @brief Handle button events in calibration mode
 * @param event Button event from bsp_io
 */
void app_anchor_on_button(bsp_io_button_event_t event);
#endif

#endif /* __APP_ANCHOR_H */

/* End of file -------------------------------------------------------- */