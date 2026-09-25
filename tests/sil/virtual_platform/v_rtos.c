#include "v_rtos.h"
#include <string.h>
#include <stdlib.h>

#define MAX_VIRTUAL_QUEUES 8
#define MAX_QUEUE_BUFFER_SIZE 4096

typedef struct {
    bool in_use;
    uint32_t msg_count;
    uint32_t msg_size;
    uint32_t head;
    uint32_t tail;
    uint32_t count;
    uint8_t buffer[MAX_QUEUE_BUFFER_SIZE];
} v_queue_t;

static v_queue_t s_queues[MAX_VIRTUAL_QUEUES];
static uint32_t s_virtual_tick = 0U;

void v_rtos_init(void)
{
    s_virtual_tick = 0U;
    memset(s_queues, 0, sizeof(s_queues));
}

void v_rtos_advance_ticks(uint32_t ticks)
{
    s_virtual_tick += ticks;
}

void v_rtos_set_tick(uint32_t tick)
{
    s_virtual_tick = tick;
}

uint32_t osKernelGetTickCount(void)
{
    return s_virtual_tick;
}

osStatus_t osDelayUntil(uint32_t ticks)
{
    if ((int32_t)(ticks - s_virtual_tick) > 0)
    {
        s_virtual_tick = ticks;
    }
    return osOK;
}

osStatus_t osDelay(uint32_t ms)
{
    s_virtual_tick += ms;
    return osOK;
}

void osThreadExit(void)
{
}

osMessageQueueId_t osMessageQueueNew(uint32_t msg_count, uint32_t msg_size, const void *attr)
{
    (void)attr;
    for (uint32_t i = 0U; i < MAX_VIRTUAL_QUEUES; i++)
    {
        if (!s_queues[i].in_use)
        {
            if (msg_count * msg_size > MAX_QUEUE_BUFFER_SIZE)
            {
                return NULL;
            }
            s_queues[i].in_use = true;
            s_queues[i].msg_count = msg_count;
            s_queues[i].msg_size = msg_size;
            s_queues[i].head = 0U;
            s_queues[i].tail = 0U;
            s_queues[i].count = 0U;
            return (osMessageQueueId_t)&s_queues[i];
        }
    }
    return NULL;
}

osStatus_t osMessageQueuePut(osMessageQueueId_t mq_id, const void *msg_ptr, uint8_t msg_prio, uint32_t timeout)
{
    (void)msg_prio;
    (void)timeout;
    if (mq_id == NULL || msg_ptr == NULL) return osErrorParameter;
    v_queue_t *q = (v_queue_t *)mq_id;
    if (!q->in_use) return osErrorParameter;

    if (q->count >= q->msg_count)
    {
        return osErrorResource;
    }

    uint8_t *dest = &q->buffer[q->tail * q->msg_size];
    memcpy(dest, msg_ptr, q->msg_size);
    q->tail = (q->tail + 1U) % q->msg_count;
    q->count++;
    return osOK;
}

osStatus_t osMessageQueueGet(osMessageQueueId_t mq_id, void *msg_ptr, uint8_t *msg_prio, uint32_t timeout)
{
    (void)msg_prio;
    (void)timeout;
    if (mq_id == NULL || msg_ptr == NULL) return osErrorParameter;
    v_queue_t *q = (v_queue_t *)mq_id;
    if (!q->in_use) return osErrorParameter;

    if (q->count == 0U)
    {
        return osErrorTimeout;
    }

    const uint8_t *src = &q->buffer[q->head * q->msg_size];
    memcpy(msg_ptr, src, q->msg_size);
    q->head = (q->head + 1U) % q->msg_count;
    q->count--;
    return osOK;
}

uint32_t osMessageQueueGetCount(osMessageQueueId_t mq_id)
{
    if (mq_id == NULL) return 0U;
    v_queue_t *q = (v_queue_t *)mq_id;
    return q->in_use ? q->count : 0U;
}

osStatus_t osMessageQueueReset(osMessageQueueId_t mq_id)
{
    if (mq_id == NULL) return osErrorParameter;
    v_queue_t *q = (v_queue_t *)mq_id;
    q->head = 0U;
    q->tail = 0U;
    q->count = 0U;
    return osOK;
}
