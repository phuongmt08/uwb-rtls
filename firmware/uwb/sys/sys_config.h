/**
 * @file       sys_config.h
 * @copyright
 * @license
 * @version    1.2.0
 * @date       2026-03-05
 * @author     Phuong Mai
 * @brief      System configuration — runtime config IS protobuf_sys_config_t
 * @note       sys_config_t is a typedef for protobuf_sys_config_t.
 *             device_type is stored separately (device_type_set/get commands).
 */
#ifndef __SYS_CONFIG_H
#define __SYS_CONFIG_H

#ifdef __cplusplus
extern "C" {
#endif

/* Includes ----------------------------------------------------------------- */
#include <stdint.h>
#include <stdbool.h>
#include "config.h"
#include "protos/protocol.pb.h"

/* Type aliases ------------------------------------------------------------- */
typedef protobuf_device_role_t device_role_t;
typedef protobuf_device_type_t device_type_t;

#define DEVICE_ROLE_UNSPECIFIED protobuf_DEVICE_ROLE_UNSPECIFIED
#define DEVICE_ROLE_TAG         protobuf_DEVICE_ROLE_TAG
#define DEVICE_ROLE_ANCHOR      protobuf_DEVICE_ROLE_ANCHOR

#define DEVICE_TYPE_UNSPECIFIED protobuf_DEVICE_TYPE_UNSPECIFIED
#define DEVICE_TYPE_TAG         protobuf_DEVICE_TYPE_TAG
#define DEVICE_TYPE_ANCHOR      protobuf_DEVICE_TYPE_ANCHOR
#define DEVICE_TYPE_GATEWAY     protobuf_DEVICE_TYPE_GATEWAY
#define DEVICE_TYPE_DEBUG_TOOL  protobuf_DEVICE_TYPE_DEBUG_TOOL

/**
 * @brief System configuration stored in flash and RAM.
 *        config_version and device_type are non-protobuf bookkeeping fields.
 *        uwb contains the full UWB runtime config as protobuf_uwb_cfg_t.
 */
typedef struct {
    uint8_t             config_version; /* bump → forces flash reset on upgrade */
    uint8_t             _pad[3];
    device_type_t       device_type;
    protobuf_uwb_cfg_t  uwb;            /* maps to sys_config_set/resp.config */
} sys_config_t;

/* Default values ----------------------------------------------------------- */
#define CONFIG_VERSION              12  /* bump → forces flash reset on upgrade */

#define DEFAULT_DEVICE_ROLE         DEVICE_ROLE_ANCHOR
#define DEFAULT_DEVICE_TYPE         DEVICE_TYPE_ANCHOR
#define DEFAULT_DEVICE_ID           0x01

#define DEFAULT_RANGING_PERIOD_MS   150
#define DEFAULT_RX_TIMEOUT_MS       75

#define DEFAULT_UWB_CHANNEL         5
#define DEFAULT_UWB_PRF             64
#define DEFAULT_UWB_DATA_RATE       0   /* 0=110kbps, 1=850kbps, 2=6.8Mbps */
#define DEFAULT_UWB_PREAMBLE_CODE   10
#define DEFAULT_TX_ANT_DLY          16436
#define DEFAULT_RX_ANT_DLY          16436
#define DEFAULT_TX_POWER            0x1F1F1F1FUL

/* ========================================================================== */
/*                         PUBLIC FUNCTIONS                                  */
/* ========================================================================== */

void sys_config_init(void);

/**
 * @brief Get pointer to live config struct (protobuf_sys_config_t).
 *        Callers may read/write fields directly — call sys_config_save() to persist.
 */
sys_config_t *sys_config_get(void);

/* Identity setters (validated) */
int sys_config_set_role(device_role_t role);
int sys_config_set_device_type(device_type_t device_type);
int sys_config_set_device_id(uint8_t id);
device_type_t sys_config_get_device_type(void);

/* Storage */
int  sys_config_save(void);
int  sys_config_load(void);
void sys_config_reset_to_defaults(void);
void sys_config_print(void);

/* Protobuf helpers — export/import only the uwb field (protobuf_uwb_cfg_t) */
void sys_config_export_protobuf(protobuf_uwb_cfg_t *dst);
int  sys_config_import_protobuf(const protobuf_uwb_cfg_t *src);

#ifdef __cplusplus
}
#endif

#endif /* __SYS_CONFIG_H */

/* End of file -------------------------------------------------------- */