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
#include "positioning_config.h"

#if ENABLE_SYS_FUSION
#include "sys_sensor_fusion.h"
#endif

/* Public enumerate/structure ----------------------------------------- */

typedef enum {
    APP_TAG_MODE_TRILATERATION = 0,
    APP_TAG_MODE_SENSOR_FUSION = 1,
} app_tag_output_mode_t;

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
 * @brief Set the output mode of the ranging pipeline.
 * @param mode APP_TAG_MODE_TRILATERATION or APP_TAG_MODE_SENSOR_FUSION
 */
void app_tag_set_output_mode(app_tag_output_mode_t mode);

/**
 * @brief Get the last trilateration position (valid after ≥1 successful cycle).
 * @param[out] x_m  X coordinate in metres
 * @param[out] y_m  Y coordinate in metres
 * @return true if position has been computed at least once
 */
bool app_tag_get_last_position(float *x_m, float *y_m);

/**
 * @brief Get latest UWB trilateration position and current ranging error count.
 * @param x Latest trilateration X position in meters, can be NULL
 * @param y Latest trilateration Y position in meters, can be NULL
 * @param err_count Current consecutive/accumulated ranging error count, can be NULL
 * @return true if a valid trilateration position is available
 */
bool app_tag_get_latest_fusion_data(float *x, float *y, uint32_t *err_count);

#if ENABLE_SYS_FUSION || ENABLE_SYS_FUSION_LOG
/**
 * @brief Set latest UWB trilateration position and current ranging error count.
 * @param x Latest trilateration X position in meters
 * @param y Latest trilateration Y position in meters
 * @param valid True if position is valid
 * @param err_count Current consecutive/accumulated ranging error count
 */
void app_tag_set_latest_fusion_data(float x, float y, bool valid, uint32_t err_count);
#endif

#if ENABLE_SYS_FUSION_LOG
/**
 * @brief Get latest data needed by the SensorFusion task for fusion log frames.
 * @param[out] out Latest selected anchor mask, trilateration position,
 *                 distances, first-path quality, and ranging error count.
 * @return true if a valid fusion log snapshot is available
 */
bool app_tag_get_latest_fusion_log_data(app_tag_fusion_log_data_t *out);

/**
 * @brief Set latest data needed for fusion log frames passively.
 */
void app_tag_set_latest_fusion_log_data(uint8_t mask, uint32_t seq, float ranging_dt, float tril_x, float tril_y, const float *distances, const double *fp_amp_norm, const double *fp_snr);
#endif

/**
 * @brief Reset the sensor fusion filters, flags, and states when ranging stops.
 */
void app_tag_reset_fusion(void);

#if ENABLE_SYS_FUSION
extern sys_sensor_fusion_data_t ukf_data;
#endif

#endif /* __APP_TAG_H */

/* End of file -------------------------------------------------------- */
