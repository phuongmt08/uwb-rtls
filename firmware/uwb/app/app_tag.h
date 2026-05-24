/**
 * @file       app_tag.h
 * @copyright
 * @license
 * @version    3.2.0
 * @date       2025-12-24
 * @author     Phuong Mai
 * @brief      Non-blocking Tag with filtering and trilateration
 * @note       
 * Pipeline:
 *   1. Raw 3D distance → Convert to 2D planar distance (height compensation)
 *   2. 2D distance → EMA filter (optional)
 *   3. Raw RSSI → EMA filter (optional)
 *   4. Filtered 2D distance + RSSI → Trilateration (auto-select best 3)
 *   5. Trilateration position → Kalman 2D
 *   6. Kalman R: Fixed tuning OR adaptive from RSSI
 * @example    None
 */
#ifndef __APP_TAG_H
#define __APP_TAG_H

/* Includes ----------------------------------------------------------- */
#include <stdint.h>
#include <stdbool.h>
#include "common.h"

/* Public enumerate/structure ----------------------------------------- */


/* Public function prototypes ----------------------------------------- */

/**
 * @brief Initialize Tag application
 * @return APP_OK on success
 */
app_err_t app_tag_init(void);

/**
 * @brief Main Tag process loop (never returns)
 */
void app_tag_process(void);

/**
 * @brief Get latest UWB trilateration position and current ranging error count.
 * @param x Latest trilateration X position in meters, can be NULL
 * @param y Latest trilateration Y position in meters, can be NULL
 * @param err_count Current consecutive/accumulated ranging error count, can be NULL
 * @return true if a valid trilateration position is available
 */
bool app_tag_get_latest_fusion_data(float *x, float *y, uint32_t *err_count);

#endif /* __APP_TAG_H */

/* End of file -------------------------------------------------------- */
