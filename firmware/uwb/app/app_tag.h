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
 * @brief Get current ranging error frame count owned by the Tag ranging flow.
 * @return Current ranging error count.
 */
uint32_t app_tag_get_ranging_error_count(void);

/**
 * @brief Reset the sensor fusion filters, flags, and states when ranging stops.
 */
void app_tag_reset_fusion(void);

#if ENABLE_SYS_FUSION
extern sys_sensor_fusion_data_t ukf_data;
#endif

#endif /* __APP_TAG_H */

/* End of file -------------------------------------------------------- */
