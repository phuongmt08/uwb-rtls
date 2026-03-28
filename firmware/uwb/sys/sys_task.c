/**
 * @file       sys_task.c
 * @copyright  Copyright (C) 2019 ITRVN.
 * @license    This project is released under the Fiot License.
 * @version    0.1.0
 * @date       2025-08-26
 * @author     Phuong Mai
 *
 * @brief      System Services Task Scheduler using TIM11 interrupt.
 *             Provides periodic task scheduling with start, stop, reset and run-now control.
 */
/* Includes ----------------------------------------------------------- */

#include "sys_task.h"

#include "tim.h"
/* Private defines ---------------------------------------------------- */
#define SYS_TASK_MAX_TASKS 8
/* Private enumerate/structure ---------------------------------------- */
typedef struct
{
  sys_task_cb_t   cb;
  void           *arg;
  sys_task_type_t type;
  uint32_t        period_ms;
  uint32_t        next_ms;
  uint8_t         used;
  uint8_t         running;
} sys_task_cxt_t;
/* Private variables -------------------------------------------------- */

static volatile uint32_t s_tick_ms   = 1;
static volatile uint32_t s_now_ms    = 0;
static volatile uint8_t  s_pend_tick = 0;
static sys_task_cxt_t    s_tasks[SYS_TASK_MAX_TASKS];

static sys_task_cxt_t   *s_freerun_ptrs[SYS_TASK_MAX_TASKS];
static uint8_t           s_freerun_cnt = 0;

static sys_task_cxt_t   *s_periodic_ptrs[SYS_TASK_MAX_TASKS];
static uint8_t           s_periodic_cnt = 0;
/* Private function prototypes ---------------------------------------- */

static void sys_task_on_tick_isr(void);
/* Function definitions ----------------------------------------------- */

sys_task_err_t sys_task_init(void)
{
    s_tick_ms   = 1u;   // fixed 1 ms
    s_now_ms    = 0;
    s_pend_tick = 0;
    
    s_freerun_cnt  = 0;
    s_periodic_cnt = 0;

    for (int i = 0; i < SYS_TASK_MAX_TASKS; ++i)
    {
        s_tasks[i].used      = 0;
        s_tasks[i].running   = 0;
        s_tasks[i].cb        = NULL;
        s_tasks[i].arg       = NULL;
        s_tasks[i].type      = SYS_TASK_TYPE_PERIODIC;
        s_tasks[i].period_ms = 0;
        s_tasks[i].next_ms   = 0;
    }

    if (HAL_TIM_Base_Start_IT(&htim11) != HAL_OK)
        return SRV_TASK_ERR;

    return SRV_TASK_OK;
}
int sys_task_add(sys_task_cb_t cb, void *arg, sys_task_type_t type, uint32_t period_ms, uint32_t delay_ms)
{
  if (cb == NULL || (type == SYS_TASK_TYPE_PERIODIC && period_ms == 0u))
    return -1;

  for (int i = 0; i < SYS_TASK_MAX_TASKS; ++i)
  {
    if (!s_tasks[i].used)
    {
      s_tasks[i].used      = 1;
      s_tasks[i].running   = 0;
      s_tasks[i].cb        = cb;
      s_tasks[i].arg       = arg;
      s_tasks[i].type      = type;
      s_tasks[i].period_ms = period_ms;
      s_tasks[i].next_ms   = s_now_ms + delay_ms;
      if (type == SYS_TASK_TYPE_FREERUN)
      {
          s_freerun_ptrs[s_freerun_cnt++] = &s_tasks[i];
      }
      else
      {
          s_periodic_ptrs[s_periodic_cnt++] = &s_tasks[i];
      }
      return i;
    }
  }
  return -1; /* full */
}

sys_task_err_t sys_task_del(int id)
{
  if (id < 0 || id >= SYS_TASK_MAX_TASKS)
    return SRV_TASK_ERR_PARAM;
  if (!s_tasks[id].used)
    return SRV_TASK_ERR;

  sys_task_cxt_t *target = &s_tasks[id];
  if (target->type == SYS_TASK_TYPE_FREERUN) {
      for (int i = 0; i < s_freerun_cnt; ++i) {
          if (s_freerun_ptrs[i] == target) {
              for (int j = i; j < s_freerun_cnt - 1; ++j) {
                  s_freerun_ptrs[j] = s_freerun_ptrs[j+1];
              }
              s_freerun_cnt--;
              break;
          }
      }
  } else {
      for (int i = 0; i < s_periodic_cnt; ++i) {
          if (s_periodic_ptrs[i] == target) {
              for (int j = i; j < s_periodic_cnt - 1; ++j) {
                  s_periodic_ptrs[j] = s_periodic_ptrs[j+1];
              }
              s_periodic_cnt--;
              break;
          }
      }
  }

  s_tasks[id].used      = 0;
  s_tasks[id].running   = 0;
  s_tasks[id].cb        = NULL;
  s_tasks[id].arg       = NULL;
  s_tasks[id].period_ms = 0;
  s_tasks[id].next_ms   = 0;
  return SRV_TASK_OK;
}

sys_task_err_t sys_task_start(int id)
{
  if (id < 0 || id >= SYS_TASK_MAX_TASKS)
    return SRV_TASK_ERR_PARAM;
  if (!s_tasks[id].used)
    return SRV_TASK_ERR;

  s_tasks[id].running = 1;
  return SRV_TASK_OK;
}

sys_task_err_t sys_task_stop(int id)
{
  if (id < 0 || id >= SYS_TASK_MAX_TASKS)
    return SRV_TASK_ERR_PARAM;
  if (!s_tasks[id].used)
    return SRV_TASK_ERR;

  s_tasks[id].running = 0;
  return SRV_TASK_OK;
}

sys_task_err_t sys_task_reset(int id)
{
  if (id < 0 || id >= SYS_TASK_MAX_TASKS)
    return SRV_TASK_ERR_PARAM;
  if (!s_tasks[id].used)
    return SRV_TASK_ERR;

  s_tasks[id].next_ms = s_now_ms;
  return SRV_TASK_OK;
}

sys_task_err_t sys_task_set_period(int id, uint32_t period_ms)
{
  if (id < 0 || id >= SYS_TASK_MAX_TASKS)
    return SRV_TASK_ERR_PARAM;
  if (!s_tasks[id].used)
    return SRV_TASK_ERR;
  if (period_ms == 0u)
    return SRV_TASK_ERR_PARAM;

  s_tasks[id].period_ms = period_ms;
  return SRV_TASK_OK;
}

sys_task_err_t sys_task_run_now(int id)
{
  if (id < 0 || id >= SYS_TASK_MAX_TASKS)
    return SRV_TASK_ERR_PARAM;
  if (!s_tasks[id].used)
    return SRV_TASK_ERR;

  s_tasks[id].next_ms = s_now_ms;
  return SRV_TASK_OK;
}

bool sys_task_is_running(int id)
{
  if (id < 0 || id >= SYS_TASK_MAX_TASKS)
    return false;
  return s_tasks[id].used && s_tasks[id].running;
}

sys_task_err_t sys_task_process(void)
{
  /* PROCESS FREERUN TASKS: O(1) Array Iteration */
  for (uint8_t i = 0; i < s_freerun_cnt; ++i)
  {
    sys_task_cxt_t *t = s_freerun_ptrs[i];
    if (t->running && t->cb)
    {
      t->cb(t->arg);
    }
  }

  /* PROCESS PERIODIC TASKS */
  if (!s_pend_tick)
    return SRV_TASK_OK;

  s_pend_tick = 0;

  for (uint8_t i = 0; i < s_periodic_cnt; ++i)
  {
    sys_task_cxt_t *t = s_periodic_ptrs[i];
    if (t->running)
    {
      while ((int32_t) (s_now_ms - t->next_ms) >= 0)
      {
        if (t->cb) t->cb(t->arg);
        t->next_ms += t->period_ms;
      }
    }
  }

  return SRV_TASK_OK;
}

/* Callback definitions ----------------------------------------------- */

void HAL_TIM_PeriodElapsedCallback(TIM_HandleTypeDef *htim)
{
  if (htim->Instance == TIM11)
  {
    sys_task_on_tick_isr();
  }
}

/* Private definitions ----------------------------------------------- */

static void sys_task_on_tick_isr(void)
{
  s_now_ms += s_tick_ms;
  s_pend_tick = 1u; /* Notification of new ticks */
}
/* End of file -------------------------------------------------------- */
