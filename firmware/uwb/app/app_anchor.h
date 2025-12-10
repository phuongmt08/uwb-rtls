#ifndef __APP_ANCHOR_H
#define __APP_ANCHOR_H

#include <stdint.h>
#include "common.h"
#include "sys_ranging.h"

typedef enum {
  APP_OK = 0,
  APP_ERR,
  APP_TIMEOUT
} app_err_t;

app_err_t app_anchor_init(void);
void      app_anchor_run(void);

#endif /* __APP_ANCHOR_H */
