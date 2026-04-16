/**
 * @file       logger.h
 * @brief      System Logger
 */

#ifndef LOGGER_H
#define LOGGER_H

#include "app_error.h"
#include "nrf_log.h"
#include "nrf_log_ctrl.h"
#include "nrf_log_default_backends.h"

void logger_init(void);

#endif // LOGGER_H