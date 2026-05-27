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

/*
 * Force this firmware image to run as a TAG even when flash config storage
 * cannot be erased/written or contains an invalid role.
 */
#define FORCE_DEVICE_TAG_MODE 0

/* Experimental features */
#undef UWB_EVENT_DRIVEN

#endif /* __CONFIG_H */
