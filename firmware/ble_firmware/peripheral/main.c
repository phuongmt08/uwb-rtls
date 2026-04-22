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

// System-wide BLE configs
#include "../ble_common/ble_config.h"

#include "../ble_common/ble_bridge/bb_router.h"

#include "nrf_log.h"
#include "nrf_log_ctrl.h"

#include "bsp_uart.h"
#include "bsp_utils.h"

#include "logger.h"
#include "ble_peripheral.h"


#include "bb_transport.h"
#include "app_uart.h"

/*
**@brief Function for initializing power management.
 */
static void power_management_init(void)
{
    ret_code_t err_code;
    err_code = nrf_pwr_mgmt_init();
    APP_ERROR_CHECK(err_code);
}


/**@brief Function for handling the idle state (main loop).
 *
 * @details If there is no pending log operation, then sleep until next the next event occurs.
 */
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
    bsp_uart_init(NULL); // Cần truyền callback từ bb_transport (nhưng đã setup trong bb_router_init) 
    logger_init();
    bsp_utils_init();
    power_management_init();

    bb_router_init();
    ble_peripheral_init();

    // Start execution.
    NRF_LOG_INFO("Blinky example started.");
    
    ble_peripheral_advertising_start();

    // Enter main loop.
    for (;;)
    {
        bb_router_process();
         // Đọc byte từ UART để kích hoạt callback on_rx_byte trong bb_transport (được setup trong bb_router_init)
        // idle_state_handle();
    }
}
    // uint8_t cr;

/**
 * @}
 */

//         // 
//         while (app_u// art_get(&cr) != NRF_SUCCESS);
//   //       
//         // Gửi trở lại ngay kí tự
//  đó (echo/// // NRF_LOG_INFO("Received: %c bytes\n", cr);
//         // on_rx_byte(cr); // Gọi callback xử lý byte nhận được từ UART (được setup trong bb_transport_init)
// log)
//     //     while (app_uart_put(cr) != NRF_SUCCESS);// ////  
        
//                 bytebsp_uart_read_byte();