/**
 * @file       bb_cmd_hdl.h
 * @copyright  [Your Copyright]
 * @license    [Your License]
 * @version    1.0.0
 * @date       2026-04-08
 * @author     Dong Son
 *
 * @brief      
 */
/* Define to prevent recursive inclusion ------------------------------ */
#ifndef BB_CMD_HDL_H
#define BB_CMD_HDL_H

/* Includes ----------------------------------------------------------- */
#include <stdint.h>
#include <stdbool.h>
#include "sdk_errors.h"

/* Public defines ----------------------------------------------------- */
/* Public enumerate/structure ----------------------------------------- */
/**
 * @brief Action to take after processing a command
 */
typedef enum {
    BB_CMD_ACTION_NONE = 0,         // No action needed (Idle)
    BB_CMD_ACTION_SEND_SERIAL,      // Encode and send response via Serial (HDLC)
    BB_CMD_ACTION_SEND_BLE,         // Encode and send response via BLE
    BB_CMD_ACTION_ERROR             // Processing failed
} bb_cmd_action_t;

/* Public macros ------------------------------------------------------ */
/* Public variables --------------------------------------------------- */
/* Public function prototypes ----------------------------------------- */
/**
 * @brief Initializes the Application logic bindings.
 * @return NRF_SUCCESS on successful initialization.
 */
ret_code_t bb_cmd_hdl_init(void);

/**
 * @brief Executes the protobuf command destined for the Peripheral device.
 * It will use nanopb (pb_decode) under the hood to extract parameters like
 * `ble_scan_start_t` or `ble_conn_params_set_t`, and execute them via SoftDevice APIs.
 *
 * @param[in,out] p_buf Pointer to the raw protobuf payload byte array. Response will overwrite this.
 * @param[in,out] p_length Pointer to payload size. Overwritten with new encoded size on response.
 * @param[in]     max_len Max capacity of the payload buffer.
 * @return Action determining what the router should do next.
 */
bb_cmd_action_t bb_cmd_hdl_process(uint8_t * p_buf, uint16_t * p_length, uint16_t max_len);

void bb_cmd_notify_scan_result(const uint8_t * mac, int8_t rssi, const char * name, uint32_t serial_num);
void bb_cmd_notify_ble_status(uint8_t state,
                              int32_t rssi_dbm,
                              uint32_t disconnect_reason);

#endif // BB_CMD_HDL_H
