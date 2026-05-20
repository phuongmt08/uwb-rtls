/**
 * Copyright (c) 2014 - 2021, Nordic Semiconductor ASA 
 *
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice,
 *    this list of conditions and the following disclaimer.
 *
 * 2. Redistributions in binary form, except as embedded into a Nordic
 *    Semiconductor ASA integrated circuit in a product or a software update
 *    for such product, must reproduce the above copyright notice, this list
 *    of conditions and the following disclaimer in the documentation and/or
 *    other materials provided with the distribution.
 *
 * 3. Neither the name of Nordic Semiconductor ASA nor the names of its
 *    contributors may be used to endorse or promote products derived from
 *    this software without specific prior written permission.
 *
 * 4. This software, with or without modification, must only be used with a
 *    Nordic Semiconductor ASA integrated circuit.
 *
 * 5. Any software provided in binary form under this license must not be
 *    reverse engineered, decompiled, modified and/or disassembled.
 *
 * THIS SOFTWARE IS PROVIDED BY NORDIC SEMICONDUCTOR ASA "AS IS" AND ANY
 * EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
 * WARRANTIES OF MERCHANTABILITY, NONINFRINGEMENT, AND FITNESS FOR A
 * PARTICULAR PURPOSE ARE DISCLAIMED.  IN NO EVENT SHALL NORDIC SEMICONDUCTOR
 * ASA OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
 * SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED
 * TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
 * PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
 * LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
 * NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
 * SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */

/**
 * @file    main.c
 * @brief   BLE Central — application entry point.
 *
 * Responsible for system-level initialization only.  All hardware and
 * peripheral details are delegated to the BSP layer; all BLE logic lives
 * in app_ble_central.
 *
 * Initialization order:
 *  1. Logging
 *  2. Clock driver  (required by app_timer & USBD)
 *  3. Timer module
 *  4. Power management
 *  5. USB CDC ACM   (USB serial over bsp_usbd)
 *  6. BLE Central   (scan + connect via app_ble_central)
 */

#include <stdint.h>
#include <stdbool.h>

#include "nrf.h"
#include "nrf_log.h"
#include "nrf_log_ctrl.h"
#include "nrf_log_default_backends.h"
#include "app_error.h"
#include "app_usbd_serial_num.h"

#include "../bsp/bsp_io.h"
#include "../bsp/bsp_usbd.h"
#include "app_ble_central.h"
#include "bb_router.h"

/* -------------------------------------------------------------------------
 * Compile-time feature flags
 * ---------------------------------------------------------------------- */
#define APP_ENABLE_USB_CDC_ACM  1  /**< Set to 0 to build without USB CDC. */

/* -------------------------------------------------------------------------
 * Callbacks required by the SDK
 * ---------------------------------------------------------------------- */

void assert_nrf_callback(uint16_t line_num, const uint8_t *p_file_name)
{
    app_error_handler(0xDEADBEEF, line_num, p_file_name);
}

/* -------------------------------------------------------------------------
 * main
 * ---------------------------------------------------------------------- */

int main(void)
{
    ret_code_t err_code;

    /* ----- Logging ------------------------------------------------------- */
    err_code = NRF_LOG_INIT(NULL);
    APP_ERROR_CHECK(err_code);
    NRF_LOG_DEFAULT_BACKENDS_INIT();
    NRF_LOG_INFO("Boot: log initialized");

    /* Log the reset reason for diagnostics, then clear the register. */
    uint32_t resetreas = NRF_POWER->RESETREAS;
    NRF_POWER->RESETREAS = resetreas;
    NRF_LOG_INFO("Boot: RESETREAS=0x%08x", resetreas);

    /* ----- Clock --------------------------------------------------------- */
    NRF_LOG_INFO("Boot: bsp_io_clock_init");
    err_code = bsp_io_clock_init();
    APP_ERROR_CHECK(err_code);

    /* ----- Timer --------------------------------------------------------- */
    NRF_LOG_INFO("Boot: bsp_io_timer_init");
    err_code = bsp_io_timer_init();
    APP_ERROR_CHECK(err_code);

    /* ----- Power management ---------------------------------------------- */
    NRF_LOG_INFO("Boot: bsp_io_power_mgmt_init");
    err_code = bsp_io_power_mgmt_init();
    APP_ERROR_CHECK(err_code);

    /* ----- USB CDC ACM --------------------------------------------------- */
#if APP_ENABLE_USB_CDC_ACM
    bb_router_init();
    app_usbd_serial_num_generate();

    NRF_LOG_INFO("Boot: bsp_usbd_init");
    NRF_LOG_FLUSH();
    // err_code = bsp_usbd_init();
    // if (err_code != NRF_SUCCESS)
    // {
    //     NRF_LOG_WARNING("USB init failed (0x%08x) - continuing BLE-only.", err_code);
    // }
    // else
    // {
    //     NRF_LOG_INFO("Boot: USB CDC ACM ready");
    // }
    // NRF_LOG_FLUSH(); /* Flush before BLE init so USB events don't drop BLE logs */
#else
    NRF_LOG_INFO("Boot: USB CDC ACM disabled");
#endif /* APP_ENABLE_USB_CDC_ACM */

    /* ----- BLE Central --------------------------------------------------- */
    NRF_LOG_INFO("Boot: ble_central_init");
    ble_central_init();
    NRF_LOG_INFO("Boot: BLE Central application started");

    /* ----- Main loop ----------------------------------------------------- */
    for (;;)
    {
        bb_router_process(); /* Check for incoming data from UART and handle state transitions */
#if APP_ENABLE_USB_CDC_ACM
        /* Drain the USB event queue before sleeping. */
        while (bsp_usbd_process())
        {
        }
#endif
        bsp_io_idle(); /* Flush logs, then wait for the next event (WFE). */
    }
}
