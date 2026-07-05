/**
 * Copyright (c) 2015 - 2021, Nordic Semiconductor ASA
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
 * @brief Blinky Sample Application main file.
 *
 * This file contains the source code for a sample server application using the LED Button service.
 */

#include <stdint.h>
#include <string.h>
#include "nordic_common.h"
#include "nrf.h"
#include "app_error.h"
#include "boards.h"
#include "app_timer.h"
#include "nrf_pwr_mgmt.h"
#include "nrf_drv_wdt.h"

// System-wide BLE configs
#include "../ble_common/ble_config.h"

#include "../ble_common/ble_bridge/bb_router.h"
#include "../ble_common/ble_bridge/bb_cmd_hdl.h"
#include "../ble_common/ble_bridge/bb_debug.h"

#include "nrf_log.h"
#include "nrf_log_ctrl.h"

#include "bsp_uart.h"
#include "bsp_utils.h"

#include "logger.h"

#include "ble_peripheral.h"
#include "bb_transport.h"

/*
**@brief Function for initializing power management.
*/
static void power_management_init(void)
{
    ret_code_t err_code;
    err_code = nrf_pwr_mgmt_init();
    APP_ERROR_CHECK(err_code);
}

static nrf_drv_wdt_channel_id m_wdt_channel_id;

static void wdt_event_handler(void)
{
    // Minimal handler. Reset is imminent.
}

static void watchdog_init(void)
{
    ret_code_t err_code;
    nrf_drv_wdt_config_t config = NRF_DRV_WDT_DEAFULT_CONFIG;
    err_code = nrf_drv_wdt_init(&config, wdt_event_handler);
    APP_ERROR_CHECK(err_code);
    err_code = nrf_drv_wdt_channel_alloc(&m_wdt_channel_id);
    APP_ERROR_CHECK(err_code);
    nrf_drv_wdt_enable();
}

static void idle_state_handle(void)
{
    if (NRF_LOG_PROCESS() == false)
    {
        nrf_pwr_mgmt_run();
    }
}

/**@brief Function for application main entry.
 */
int main(void)
{
    // Initialize.
    logger_init();
    bsp_utils_init();
    power_management_init();

    ret_code_t err_code = bb_router_init();
    APP_ERROR_CHECK(err_code);
    err_code = bb_cmd_request_ble_adv_config();
    if (err_code != NRF_SUCCESS)
    {
        NRF_LOG_WARNING("ble_adv_config_request failed: 0x%08X", err_code);
    }
    // BLE is disabled by default, initialized later via STM32 command

    // Start execution.
    BB_DEBUG_LOG_INFO("BLE Peripheral started !");

    // Start watchdog AFTER all initialization is complete.
    watchdog_init();

    // Enter main loop.
    for (;;)
    {
        nrf_drv_wdt_channel_feed(m_wdt_channel_id);
        bb_router_process();
        ble_peripheral_process();
        bb_cmd_ble_adv_config_request_process();
        idle_state_handle();
    }
}
