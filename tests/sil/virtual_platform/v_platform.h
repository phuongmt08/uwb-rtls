#ifndef V_PLATFORM_H_
#define V_PLATFORM_H_

#include "v_rtos.h"
#include "v_hal.h"
#include "bsp_imu.h"
#include "sys_config.h"
#include "sys_logger.h"
#include "app_rtos_handles.h"
#include "network/network_core.h"

#ifdef __cplusplus
extern "C" {
#endif

void v_platform_init(void);

#ifdef __cplusplus
}
#endif

#endif /* V_PLATFORM_H_ */
