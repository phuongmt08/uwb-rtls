/* ============================== config.h ==================================
 * @file       config.h
 * @brief      Minimal build feature flags
 */

#ifndef __CONFIG_H
#define __CONFIG_H

#define HAVE_FLASH_STORAGE
#undef  ENABLE_FLASH_LOG
#define HAVE_RTC

#define HAVE_TX_DELAY
#define ENABLE_DEBUG_LOGGING
#define HAVE_BLE_PERIPHERAL

/* Developer diagnostics */
// #define DEVELOPER_MODE

/*
 * Force this firmware image to run as a TAG even when flash config storage
 * cannot be erased/written or contains an invalid role.
 */
#define FORCE_DEVICE_TAG_MODE 1


#endif /* __CONFIG_H */
