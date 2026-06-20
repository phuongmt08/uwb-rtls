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

/**
 * @brief Enable or disable anchor survey mode at runtime.
 */
void app_anchor_set_survey_active(bool active);

/**
 * @brief Read back whether anchor survey mode is active.
 */
bool app_anchor_is_survey_active(void);

#endif /* __APP_ANCHOR_H */

/* End of file -------------------------------------------------------- */
