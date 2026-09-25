#ifndef APP_RTOS_HANDLES_H_
#define APP_RTOS_HANDLES_H_

#include "cmsis_os2.h"

#ifdef __cplusplus
extern "C" {
#endif

extern osMessageQueueId_t g_imu_data_queue;
extern osMessageQueueId_t g_uwb_distance_queue;

#ifdef __cplusplus
}
#endif

#endif /* APP_RTOS_HANDLES_H_ */
