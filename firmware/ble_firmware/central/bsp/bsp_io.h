/**
 * @file    bsp_io.h
 * @brief   BSP layer for general-purpose I/O initialization.
 *
 * Provides board-level hardware initialization helpers that are shared
 * across the application (clock driver, power management, timers, etc.).
 */

#ifndef BSP_IO_H
#define BSP_IO_H

#include "sdk_errors.h"

#ifdef __cplusplus
extern "C" {
#endif

/* -------------------------------------------------------------------------
 * Public API
 * ---------------------------------------------------------------------- */

/**
 * @brief Initialize the low-frequency clock and wait for it to start.
 *
 * Required by app_timer and USBD.  Safe to call multiple times — if the
 * driver is already initialized, the function returns NRF_SUCCESS.
 *
 * @return NRF_SUCCESS or a propagated SDK error code.
 */
ret_code_t bsp_io_clock_init(void);

/**
 * @brief Initialize the application timer module.
 *
 * @return NRF_SUCCESS or a propagated SDK error code.
 */
ret_code_t bsp_io_timer_init(void);

/**
 * @brief Initialize the power management module.
 *
 * @return NRF_SUCCESS or a propagated SDK error code.
 */
ret_code_t bsp_io_power_mgmt_init(void);

/**
 * @brief Run low-power idle handling (flush logs then sleep until next event).
 *
 * Call this at the end of the main loop iteration.
 */
void bsp_io_idle(void);

#ifdef __cplusplus
}
#endif

#endif /* BSP_IO_H */
