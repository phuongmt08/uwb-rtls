/* ============================== config.h ==================================
 * @file       config.h
 * @brief      Minimal build feature flags
 */

#ifndef __CONFIG_H
#define __CONFIG_H

#define HAVE_FLASH_STORAGE
#define ENABLE_FLASH_LOG
#define HAVE_RTC

#define MULTIPLE_ANCHOR

#define ENABLE_RSSI
#define HAVE_TX_DELAY
#define ENABLE_DEBUG_LOGGING
#define HAVE_BLE_PERIPHERAL

/* Experimental features */
#define UWB_EVENT_DRIVEN 1

#endif /* __CONFIG_H */
