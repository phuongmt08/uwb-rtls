/**
 * @file       app_tag.h
 * @copyright
 * @license
 * @version    3.2.0
 * @date       2025-12-24
 * @author     Phuong Mai
 * @brief      Non-blocking Tag with filtering and trilateration
 */
#ifndef __APP_TAG_H
#define __APP_TAG_H

/* Includes ----------------------------------------------------------- */
#include <stdint.h>
#include <stdbool.h>
#include "common.h"
#include "positioning_config.h"
#include "sys_config.h"

#if ENABLE_SYS_FUSION
#include "sys_sensor_fusion.h"
#endif

/* Public function prototypes ----------------------------------------- */

/**
 * @brief Initialize Tag application.
 * @return APP_OK on success.
 */
app_err_t app_tag_init(void);

/**
 * @brief Main Tag process loop.
 */
void app_tag_process(void);

/**
 * @brief Process queued UWB control requests on the UwbRanging task context.
 */
void app_tag_process_uwb_control(sys_config_t *cfg);

/**
 * @brief Get current ranging error frame count owned by the Tag ranging flow.
 * @return Current ranging error count.
 */
uint32_t app_tag_get_ranging_error_count(void);

/**
 * @brief Reset the sensor fusion filters, flags, and states when ranging stops.
 */
void app_tag_reset_fusion(void);

/**
 * @brief Read the latest tag position estimate if available.
 * @param x_m Output X coordinate in meters.
 * @param y_m Output Y coordinate in meters.
 * @return true when a valid position is available.
 */
bool app_tag_get_latest_position(float *x_m, float *y_m);

#endif /* __APP_TAG_H */

/* End of file -------------------------------------------------------- */
