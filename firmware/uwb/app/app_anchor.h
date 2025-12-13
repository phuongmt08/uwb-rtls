#ifndef __APP_ANCHOR_H
#define __APP_ANCHOR_H

#include <stdint.h>
#include "common.h"
#include "sys_ranging.h"

app_err_t app_anchor_init(void);
void      app_anchor_process(void *arg);

#endif /* __APP_ANCHOR_H */
