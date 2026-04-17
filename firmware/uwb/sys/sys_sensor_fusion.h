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

/* Public defines ----------------------------------------------------- */
#define ANCHOR_0_X  0.0f
#define ANCHOR_0_Y  0.0f
#define ANCHOR_1_X  10.0f
#define ANCHOR_1_Y  0.0f
#define ANCHOR_2_X  0.0f
#define ANCHOR_2_Y  10.0f
#define ANCHOR_3_X  10.0f
#define ANCHOR_3_Y  10.0f

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

sys_sensor_fusion_err_t sys_sensor_fusion_predict(sys_sensor_fusion_data_t *p_ukf, float dt);

sys_sensor_fusion_err_t sys_sensor_fusion_update(sys_sensor_fusion_data_t *p_ukf, float d0, float d1, float d2);

#endif /* SYS_SENSOR_FUSION_H_ */

/* End of file -------------------------------------------------------- */
