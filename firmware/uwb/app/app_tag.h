/* ============================== app_tag.h ==================================
 * @file       app_tag.h
 * @brief      Tag application header
 * @version    1.0.0
 * @date       2025-11-15
 */

#ifndef __APP_TAG_H
#define __APP_TAG_H

/* Includes ----------------------------------------------------------- */
#include <stdint.h>
#include "common.h"

/* Public enumerate/structure ----------------------------------------- */


/* Public function prototypes ----------------------------------------- */

/**
 * @brief Initialize Tag application
 * @return APP_OK on success
 */
app_err_t app_tag_init(void);

/**
 * @brief Main Tag process loop (never returns)
 */
void app_tag_process(void);

#endif /* __APP_TAG_H */

/* End of file -------------------------------------------------------- */
