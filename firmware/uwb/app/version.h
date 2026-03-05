/*
 * version.h
 */

#ifndef APPLICATION_VERSION_H_
#define APPLICATION_VERSION_H_

#if __has_include("fw_image_meta.h")
#include "fw_image_meta.h"
#else
#define FW_VERSION_GITSHA_HEX 0x12345678ULL
#define FW_VERSION_GITSHA_NOQUOTE unknown
#define FW_IMAGE_TIMESTAMP 0U
#define FW_IMAGE_LENGTH    0U
#define FW_IMAGE_CRC       0U
#endif

#define FW_VERSION_MAJOR 1
#define FW_VERSION_MINOR 0
#define FW_VERSION_PATCH 0
#define FW_VERSION_BUILD 1

#define FW_VERSION_GITSHA FW_VERSION_GITSHA_HEX

#define FW_VERSION  FW_VERSION_MAJOR.FW_VERSION_MINOR.FW_VERSION_PATCH.FW_VERSION_BUILD.FW_VERSION_GITSHA_NOQUOTE

#define XSTR(x) STR(x)
#define STR(x) #x

#pragma message "Program Firmware version " XSTR(FW_VERSION)

#endif /* APPLICATION_VERSION_H_ */
