/**
 * Copyright (c) 2014 - 2021, Nordic Semiconductor ASA
 *
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without modification,
 * are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice, this
 *    list of conditions and the following disclaimer.
 *
 * 2. Redistributions in binary form, except as embedded into a Nordic
 *    Semiconductor ASA integrated circuit in a product or a software update for
 *    such product, must reproduce the above copyright notice, this list of
 *    conditions and the following disclaimer in the documentation and/or other
 *    materials provided with the distribution.
 *
 * 3. Neither the name of Nordic Semiconductor ASA nor the names of its
 *    contributors may be used to endorse or promote products derived from this
 *    software without specific prior written permission.
 *
 * 4. This software, with or without modification, must only be used with a
 *    Nordic Semiconductor ASA integrated circuit.
 *
 * 5. Any software provided in binary form under this license must not be reverse
 *    engineered, decompiled, modified and/or disassembled.
 *
 * THIS SOFTWARE IS PROVIDED BY NORDIC SEMICONDUCTOR ASA "AS IS" AND ANY EXPRESS
 * OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES
 * OF MERCHANTABILITY, NONINFRINGEMENT, AND FITNESS FOR A PARTICULAR PURPOSE ARE
 * DISCLAIMED. IN NO EVENT SHALL NORDIC SEMICONDUCTOR ASA OR CONTRIBUTORS BE
 * LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
 * CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE
 * GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
 * HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
 * LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT
 * OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 *
 */
/**
 * @brief BLE LED Button Service central and client application main file.
 *
 * This file contains the source code for a sample client application using the LED Button service.
 */

#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
// #include "nrf_sdh_ble.h"
#include "nrf.h"
#include "nrf_drv_clock.h"
#include "nrf_sdh_soc.h"
#include "nrf_pwr_mgmt.h"
#include "app_timer.h"
#include "boards.h"
#include "bsp.h"
#include "nrf_log.h"
#include "nrf_log_ctrl.h"
#include "nrf_log_default_backends.h"
#include "app_usbd_serial_num.h"
#include "usb_cdc_acm.h"

#include "app_ble_central.h"

#define APP_ENABLE_USB_CDC_ACM 1

void assert_nrf_callback(uint16_t line_num, const uint8_t * p_file_name)
{
    app_error_handler(0xDEADBEEF, line_num, p_file_name);
}


/**@brief Function for initializing the log.
 */
static void log_init(void)
{
    ret_code_t err_code = NRF_LOG_INIT(NULL);
    APP_ERROR_CHECK(err_code);

    NRF_LOG_DEFAULT_BACKENDS_INIT();
}


/**@brief Function for initializing the timer.
 */
static void timer_init(void)
{
    ret_code_t err_code = app_timer_init();
    if (err_code != NRF_SUCCESS)
    {
        NRF_LOG_ERROR("app_timer_init failed: 0x%08x", err_code);
        NRF_LOG_FLUSH();
    }
    APP_ERROR_CHECK(err_code);
}


/**@brief Function for initializing the Power manager. */
static void power_management_init(void)
{
    ret_code_t err_code;
    err_code = nrf_pwr_mgmt_init();
    if (err_code != NRF_SUCCESS)
    {
        NRF_LOG_ERROR("nrf_pwr_mgmt_init failed: 0x%08x", err_code);
        NRF_LOG_FLUSH();
    }
    APP_ERROR_CHECK(err_code);
}

/**@brief Function for handling the idle state (main loop).
 *
 * @details Handle any pending log operation(s), then sleep until the next event occurs.
 */
static void idle_state_handle(void)
{
    NRF_LOG_FLUSH();
    nrf_pwr_mgmt_run();
}


int main(void)
{
    ret_code_t err_code;

    // Initialize basic services
    log_init();
    NRF_LOG_INFO("Boot: log_init");

    uint32_t resetreas = NRF_POWER->RESETREAS;
    NRF_POWER->RESETREAS = resetreas;
    NRF_LOG_INFO("Boot: RESETREAS=0x%08x", resetreas);

    NRF_LOG_INFO("Boot: timer_init");
    timer_init();
    NRF_LOG_INFO("Boot: power_management_init");
    power_management_init();

#if APP_ENABLE_USB_CDC_ACM
    NRF_LOG_INFO("Boot: nrf_drv_clock_init");
    err_code = nrf_drv_clock_init();
    if (err_code == NRF_ERROR_MODULE_ALREADY_INITIALIZED)
    {
        NRF_LOG_INFO("nrf_drv_clock already initialized.");
        err_code = NRF_SUCCESS;
    }
    if (err_code != NRF_SUCCESS)
    {
        NRF_LOG_ERROR("nrf_drv_clock_init failed: 0x%08x", err_code);
        NRF_LOG_FLUSH();
    }
    APP_ERROR_CHECK(err_code);

    nrf_drv_clock_lfclk_request(NULL);
    while (!nrf_drv_clock_lfclk_is_running())
    {
        // Wait for LFCLK like the Nordic USB example.
    }

    app_usbd_serial_num_generate();

    // Initialize USB CDC ACM
    NRF_LOG_INFO("Boot: usb_cdc_acm_init");
    err_code = usb_cdc_acm_init();
    if (err_code != NRF_SUCCESS)
    {
        NRF_LOG_ERROR("usb_cdc_acm_init failed: 0x%08x", err_code);
        NRF_LOG_FLUSH();
        NRF_LOG_WARNING("USB disabled, continuing BLE only.");
    }
    else
    {
        NRF_LOG_INFO("USB CDC ACM initialized - ready for communication.");
    }
#else
    NRF_LOG_INFO("Boot: USB CDC ACM disabled");
#endif

    // Initialize BLE 
    ble_central_init();
    NRF_LOG_INFO("Boot: ble_central_init done");

    // Start execution
    NRF_LOG_INFO("BLE Central application started.");

    // Enter main loop
    for (;;)
    {
#if APP_ENABLE_USB_CDC_ACM
        // Process USB CDC ACM events
        while (usb_cdc_acm_process())
        {
            // Drain queued USB events before sleeping.
        }
#endif
        idle_state_handle();
    }
}
