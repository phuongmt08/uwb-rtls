/* ============================== platform_config.h ==========================
 * @file       platform_config.h
 * @brief      Platform configuration - Firmware & Hardware info
 * @version    1.0.0
 * @date       2025-11-15
 */

#ifndef __PLATFORM_CONFIG_H
#define __PLATFORM_CONFIG_H

/* ========================================================================== */
/*                         FIRMWARE VERSION                                  */
/* ========================================================================== */
#define FW_VERSION_MAJOR        0
#define FW_VERSION_MINOR        0
#define FW_VERSION_PATCH        0

/* ========================================================================== */
/*                    HARDWARE REVISION COMPATIBILITY                        */
/* ========================================================================== */
/* Supported hardware revisions (v0.1, v0.2, v1.0, etc.) */
#define HW_REV_MAJOR_MIN        0    /* Minimum major version */
#define HW_REV_MINOR_MIN        1    /* Minimum minor version */
#define HW_REV_MAJOR_MAX        1    /* Maximum major version */
#define HW_REV_MINOR_MAX        0    /* Maximum minor version */

/* Current hardware revision (update based on actual board) */
#define HW_REV_MAJOR            1
#define HW_REV_MINOR            0

/* ========================================================================== */
/*                          FEATURES                                          */
/* ========================================================================== */
/* Available hardware peripherals */
#define HAVE_FLASH_STORAGE      /* Flash storage for config persistence */
#undef  HAVE_RTC
#undef  HAVE_IMU
#undef  HAVE_AUTH

/* UWB Ranging features */
// #define MULTIPLE_ANCHOR         /* Enable multiple anchor ranging support */
#define MAX_ANCHORS            8   /* Maximum number of anchors (1-8) */

/* Advanced features (disable for basic operation) */
#define ENABLE_RSSI             /* Enable RSSI measurement and reporting */
#define HAVE_TX_DELAY           /* Enable delayed TX mode (faster, 4 msgs instead of 5) */

#endif /* __PLATFORM_CONFIG_H */

/* End of file -------------------------------------------------------- */
