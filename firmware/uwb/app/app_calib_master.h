/**
 * @file       app_calib_master.h
 * @brief      Center-tag calibration master application.
 */
#ifndef __APP_CALIB_MASTER_H
#define __APP_CALIB_MASTER_H

#include <stdbool.h>
#include "common.h"
#include "protos/protocol.pb.h"

bool app_calib_master_should_run(void);
void app_calib_master_set_active(bool active);
bool app_calib_master_is_active(void);
bool app_calib_master_set_reference_position(float x_m, float y_m, float z_m);
app_err_t app_calib_master_init(void);
void app_calib_master_process(void);
void app_calib_master_on_ranging_stopped(void);
void app_calib_master_fill_status(protobuf_calib_status_resp_t *resp);
bool app_calib_master_get_average_candidate(uint32_t anchor_mask,
                                            uint16_t *tx_delay,
                                            uint16_t *rx_delay);

#endif /* __APP_CALIB_MASTER_H */
