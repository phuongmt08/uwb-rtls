#ifndef V_RTOS_H_
#define V_RTOS_H_

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    osOK = 0,
    osError = -1,
    osErrorTimeout = -2,
    osErrorResource = -3,
    osErrorParameter = -4,
    osErrorNoMemory = -5,
    osStatus_Reserved = 0x7FFFFFFF
} osStatus_t;

typedef void* osMessageQueueId_t;
typedef void* osThreadId_t;

#define osWaitForever 0xFFFFFFFFU
#define pdMS_TO_TICKS(ms) (ms)

/* Virtual RTOS clock & scheduling */
uint32_t osKernelGetTickCount(void);
osStatus_t osDelayUntil(uint32_t ticks);
osStatus_t osDelay(uint32_t ms);
void osThreadExit(void);

/* Virtual Message Queue API */
osMessageQueueId_t osMessageQueueNew(uint32_t msg_count, uint32_t msg_size, const void *attr);
osStatus_t osMessageQueuePut(osMessageQueueId_t mq_id, const void *msg_ptr, uint8_t msg_prio, uint32_t timeout);
osStatus_t osMessageQueueGet(osMessageQueueId_t mq_id, void *msg_ptr, uint8_t *msg_prio, uint32_t timeout);
uint32_t osMessageQueueGetCount(osMessageQueueId_t mq_id);
osStatus_t osMessageQueueReset(osMessageQueueId_t mq_id);

/* Testing control helpers */
void v_rtos_init(void);
void v_rtos_advance_ticks(uint32_t ticks);
void v_rtos_set_tick(uint32_t tick);

#ifdef __cplusplus
}
#endif

#endif /* V_RTOS_H_ */
