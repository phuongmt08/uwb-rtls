/**
 * @file       SRV_TASK.h
 * @copyright  Copyright (C) 2019 ITRVN.
 * @license    This project is released under the Fiot License.
 * @version    0.1.0
 * @date       2025-08-26
 * @author     Phuong Mai
 *
 * @brief      System Services Task Scheduler using TIM11 interrupt.
 *             Provides periodic and free-run task scheduling.
/* Define to prevent recursive inclusion ------------------------------ */
#ifndef __SYS_TASK_H
#define __SYS_TASK_H
/* Includes ----------------------------------------------------------- */

#include <stdbool.h>
#include <stdint.h>
/* Public defines ----------------------------------------------------- */
/* Public enumerate/structure ----------------------------------------- */

/* Error codes */
typedef enum
{
  SRV_TASK_OK = 0,
  SRV_TASK_ERR,
  SRV_TASK_ERR_PARAM
} sys_task_err_t;

typedef enum
{
  SYS_TASK_TYPE_PERIODIC = 0,
  SYS_TASK_TYPE_FREERUN
} sys_task_type_t;
typedef void (*sys_task_cb_t)(void *arg);

/* Public macros ------------------------------------------------------ */
/* Public variables --------------------------------------------------- */
/* Public function prototypes ----------------------------------------- */

sys_task_err_t sys_task_init(void);
sys_task_err_t sys_task_process(void);

int            sys_task_add(sys_task_cb_t cb, void *arg, sys_task_type_t type, uint32_t period_ms, uint32_t delay_ms);
sys_task_err_t sys_task_del(int id);
sys_task_err_t sys_task_start(int id);
sys_task_err_t sys_task_stop(int id);
sys_task_err_t sys_task_reset(int id);
sys_task_err_t sys_task_set_period(int id, uint32_t period_ms);
sys_task_err_t sys_task_run_now(int id);
bool           sys_task_is_running(int id);
#endif  //__SYS_TASK_H

/* End of file -------------------------------------------------------- */
