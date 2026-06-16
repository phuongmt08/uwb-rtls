/**
 * @file       sys_sensor_fusion.h
 * @copyright  [Your Copyright]
 * @license    [Your License]
 * @version    1.0.0
 * @date       25/03/26
 * @author     Dong Son
 *
 * @brief
 */
/* Define to prevent recursive inclusion ------------------------------ */
#ifndef SYS_SENSOR_FUSION_H_
#define SYS_SENSOR_FUSION_H_

/* Includes ----------------------------------------------------------- */
#include <stdint.h>
#include <stdbool.h>
#include "arm_math.h"
#include <math.h>
#include "mw_trilateration.h"
#include "network/network_core.h"

/* Public defines ----------------------------------------------------- */
#define SYS_SENSOR_FUSION_PI 3.14159265358979323846f

#ifndef UKF_BLE_STREAM_TEST_ENABLE
#define UKF_BLE_STREAM_TEST_ENABLE false
#endif

/* Public enumerate/structure ----------------------------------------- */
typedef enum
{
	SYS_SENSOR_FUSION_OK = 0,
	SYS_SENSOR_FUSION_ERR,
} sys_sensor_fusion_err_t;

typedef struct
{
    float px;       // Vị trí X (m)
    float py;       // Vị trí Y (m)
    float vx;       // Vận tốc X (m/s)
    float vy;       // Vận tốc Y (m/s)
    float theta;    // Góc Yaw (rad)
    float b_ax;     // Bias Gia tốc X
    float b_ay;     // Bias Gia tốc Y
    float b_gz;     // Bias Gyro Z
} sys_sensor_fusion_data_t;

/* Public macros ------------------------------------------------------ */
/* Public variables --------------------------------------------------- */
/* Public function prototypes ----------------------------------------- */
sys_sensor_fusion_err_t sys_sensor_fusion_init(sys_sensor_fusion_data_t *p_ukf);

sys_sensor_fusion_err_t sys_sensor_fusion_set_initial_position(sys_sensor_fusion_data_t *p_ukf, float x0, float y0);

sys_sensor_fusion_err_t sys_sensor_fusion_predict(sys_sensor_fusion_data_t *p_ukf);

sys_sensor_fusion_err_t sys_sensor_fusion_update(sys_sensor_fusion_data_t *p_ukf, float d0, float d1, float d2, uint8_t mask);

bool sys_sensor_fusion_is_initialized(void);

bool sys_sensor_fusion_apply_trilateration_result(sys_sensor_fusion_data_t *p_ukf,
                                                  const vec2d_t *tril_position,
                                                  const mw_tril_anchor_t best_3_anchors[3],
                                                  const mw_tril_anchor_t *anchors_by_id,
                                                  const mw_tril_anchor_t *anchors_compact,
                                                  uint8_t compact_count,
                                                  uint8_t selected_anchor_mask);

void sys_sensor_fusion_report_error(void);

uint32_t sys_sensor_fusion_get_error_count(void);

void sys_sensor_fusion_reset(void);

void sys_sensor_fusion_stream_test_init(network_core_t *stream);

void sys_sensor_fusion_test_stream_result(network_core_t *stream, bool ranging_enabled);

float sys_sensor_fusion_get_ukf_yaw_deg();

float sys_sensor_fusion_get_yaw_deg();

sys_sensor_fusion_err_t sys_sensor_fusion_set_update_flag();

sys_sensor_fusion_err_t sys_sensor_fusion_clear_update_flag();

sys_sensor_fusion_err_t sys_sensor_fusion_set_predict_flag();

sys_sensor_fusion_err_t sys_sensor_fusion_clear_predict_flag();

bool sys_sensor_fusion_check_update_flag();

bool sys_sensor_fusion_check_predict_flag();

#endif /* SYS_SENSOR_FUSION_H_ */

/* End of file -------------------------------------------------------- */
