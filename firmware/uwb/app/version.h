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
#define FW_VERSION_MINOR 3
#define FW_VERSION_PATCH 0

/* FW_VERSION_BUILD is auto-updated by Python programmer before each build */
#if __has_include("version_build.h")
#include "version_build.h"
#else
#define FW_VERSION_BUILD 0
#endif

#define FW_VERSION_GITSHA FW_VERSION_GITSHA_HEX

#define FW_VERSION  FW_VERSION_MAJOR.FW_VERSION_MINOR.FW_VERSION_PATCH.FW_VERSION_BUILD.FW_VERSION_GITSHA_NOQUOTE

#define XSTR(x) STR(x)
#define STR(x) #x

#pragma message "=== FW VERSION " XSTR(FW_VERSION_MAJOR) "." XSTR(FW_VERSION_MINOR) "." XSTR(FW_VERSION_PATCH) "." XSTR(FW_VERSION_BUILD) " ==="

#endif /* APPLICATION_VERSION_H_ */
