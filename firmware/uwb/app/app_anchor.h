/**
 * @file       app_anchor.h
 * @brief      Non-blocking normal Anchor ranging application
 */
#ifndef __APP_ANCHOR_H
#define __APP_ANCHOR_H

#include "common.h"

app_err_t app_anchor_init(void);
void app_anchor_process(void *arg);

#endif /* __APP_ANCHOR_H */
