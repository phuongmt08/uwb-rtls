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

/* Experimental features */
#define UWB_EVENT_DRIVEN 0

#undef USE_DIP_SWITCH   /* NOTE: Define this if you are running anchor mode in tag hardware */

#endif /* __CONFIG_H */
