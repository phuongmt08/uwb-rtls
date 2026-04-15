/**
 * @file    bsp_io.c
 * @brief   BSP layer for general-purpose I/O initialization.
 *
 * Collects all low-level hardware-init boilerplate (clock, timer, power
 * management, idle) so that main.c stays clean and high-level.
 */

#include "bsp_io.h"

#include "nrf_drv_clock.h"
#include "nrf_pwr_mgmt.h"
#include "app_timer.h"
#include "nrf_log.h"
#include "nrf_log_ctrl.h"

/* -------------------------------------------------------------------------
 * Public API implementation
 * ---------------------------------------------------------------------- */

ret_code_t bsp_io_clock_init(void)
{
    ret_code_t err_code = nrf_drv_clock_init();

    if (err_code == NRF_ERROR_MODULE_ALREADY_INITIALIZED)
    {
        NRF_LOG_INFO("Clock: already initialized");
        err_code = NRF_SUCCESS;
    }

    if (err_code != NRF_SUCCESS)
    {
        NRF_LOG_ERROR("Clock: nrf_drv_clock_init failed: 0x%08x", err_code);
        return err_code;
    }

    /* Request LFCLK and wait until it is running (required by app_timer & USBD). */
    nrf_drv_clock_lfclk_request(NULL);
    while (!nrf_drv_clock_lfclk_is_running())
    {
        /* Spin until the low-frequency clock is stable. */
    }

    NRF_LOG_INFO("Clock: LFCLK running");
    return NRF_SUCCESS;
}

ret_code_t bsp_io_timer_init(void)
{
    ret_code_t err_code = app_timer_init();

    if (err_code != NRF_SUCCESS)
    {
        NRF_LOG_ERROR("Timer: app_timer_init failed: 0x%08x", err_code);
        return err_code;
    }

    NRF_LOG_INFO("Timer: initialized");
    return NRF_SUCCESS;
}

ret_code_t bsp_io_power_mgmt_init(void)
{
    ret_code_t err_code = nrf_pwr_mgmt_init();

    if (err_code != NRF_SUCCESS)
    {
        NRF_LOG_ERROR("Power: nrf_pwr_mgmt_init failed: 0x%08x", err_code);
        return err_code;
    }

    NRF_LOG_INFO("Power: management initialized");
    return NRF_SUCCESS;
}

void bsp_io_idle(void)
{
    NRF_LOG_FLUSH();
    nrf_pwr_mgmt_run();
}
