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
#define HAVE_UWB                       /* UWB radio (DW1000) */
#define HAVE_USB_CDC                   /* USB CDC for logging */
#undef  HAVE_FLASH_STORAGE             /* Flash storage for config */
#undef  HAVE_RTC
#undef  HAVE_IMU
#undef  HAVE_LED
#undef  HAVE_SWITCH
#undef  HAVE_AUTH
#endif /* __PLATFORM_CONFIG_H */

/* End of file -------------------------------------------------------- */
