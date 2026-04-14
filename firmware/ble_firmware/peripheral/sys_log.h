/**
 * @file       sys_log.h
 * @brief      System Logger
 */

#ifndef SYS_LOG_H
#define SYS_LOG_H

#include "app_error.h"
#include "nrf_log.h"
#include "nrf_log_ctrl.h"
#include "nrf_log_default_backends.h"

void sys_log_init(void);

#endif // SYS_LOG_H