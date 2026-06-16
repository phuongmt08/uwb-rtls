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

/* Includes ----------------------------------------------------------------- */
#include "bsp_util.h"
#include "config.h"
#include "otp/otp.h"
#include "positioning_config.h"
#include "protos/protocol.pb.h"

#include <stdbool.h>
#include <stdint.h>

/* Type aliases ------------------------------------------------------------- */
typedef protobuf_device_role_t        device_role_t;
typedef protobuf_device_type_t        device_type_t;
typedef protobuf_host_transport_t     host_transport_t;
typedef protobuf_pos_calib_cfg_t      sys_calib_cfg_t;
typedef protobuf_anchor_layout_item_t sys_anchor_layout_t;
typedef protobuf_prefilter_cfg_t      sys_prefilter_cfg_t;

#define DEVICE_ROLE_UNSPECIFIED      protobuf_DEVICE_ROLE_UNSPECIFIED
#define DEVICE_ROLE_TAG              protobuf_DEVICE_ROLE_TAG
#define DEVICE_ROLE_ANCHOR           protobuf_DEVICE_ROLE_ANCHOR

#define DEVICE_TYPE_UNSPECIFIED      protobuf_DEVICE_TYPE_UNSPECIFIED
#define DEVICE_TYPE_TAG              protobuf_DEVICE_TYPE_TAG
#define DEVICE_TYPE_ANCHOR           protobuf_DEVICE_TYPE_ANCHOR
#define DEVICE_TYPE_GATEWAY          protobuf_DEVICE_TYPE_GATEWAY
#define DEVICE_TYPE_DEBUG_TOOL       protobuf_DEVICE_TYPE_DEBUG_TOOL

#define HOST_TRANSPORT_UNSPECIFIED   protobuf_HOST_TRANSPORT_UNSPECIFIED
#define HOST_TRANSPORT_USB           protobuf_HOST_TRANSPORT_USB
#define HOST_TRANSPORT_UART          protobuf_HOST_TRANSPORT_UART

#define SYS_CONFIG_MAX_ANCHORS       MAX_ANCHORS_SUPPORTED
#define SYS_CONFIG_CALIB_MAX_SAMPLES 64

typedef protobuf_anchor_power_mode_t anchor_power_mode_t;

#define ANCHOR_POWER_MODE_PERFORMANCE protobuf_ANCHOR_POWER_MODE_PERFORMANCE
#define ANCHOR_POWER_MODE_BALANCED    protobuf_ANCHOR_POWER_MODE_BALANCED
#define ANCHOR_POWER_MODE_ECO         protobuf_ANCHOR_POWER_MODE_ECO
#define ANCHOR_POWER_MODE_DEEP_ECO    protobuf_ANCHOR_POWER_MODE_DEEP_ECO

/**
 * @brief System configuration stored in flash and RAM.
 *        config_version and device_type are non-protobuf bookkeeping fields.
 *        uwb contains the full UWB runtime config as protobuf_uwb_cfg_t.
 */
typedef struct
{
  uint8_t             config_version; /* bump → forces flash reset on upgrade */
  uint8_t             _pad[3];
  device_type_t       device_type;
  host_transport_t    host_transport;
  protobuf_uwb_cfg_t  uwb; /* maps to sys_config_set/resp.config */
  sys_prefilter_cfg_t prefilter;
  sys_calib_cfg_t     calib;
  uint32_t            anchor_count;
  sys_anchor_layout_t anchor_layout[SYS_CONFIG_MAX_ANCHORS];

  /* Zone Profile configurations */
  uint32_t            default_zone_id;
  protobuf_zone_profile_t zone_profiles[4];
} sys_config_t;

/* Default values ----------------------------------------------------------- */
#define CONFIG_VERSION            31     /* bump -> forces flash reset on upgrade */

#define DEFAULT_DEVICE_ROLE       DEVICE_TYPE_ANCHOR
#define DEFAULT_DEVICE_TYPE       DEVICE_TYPE_ANCHOR
#define DEFAULT_HOST_TRANSPORT    HOST_TRANSPORT_USB
#define DEFAULT_DEVICE_ID         0x03
#define DEFAULT_RANGING_PERIOD_MS 75
#define DEFAULT_RX_TIMEOUT_MS     60
#define DEFAULT_UWB_CHANNEL       4
#define DEFAULT_UWB_PRF           64
#define DEFAULT_UWB_DATA_RATE     2 /* 0=110kbps, 1=850kbps, 2=6.8Mbps */
#define DEFAULT_ZONE_1_PREAMBLE_CODE 17
#define DEFAULT_ZONE_2_PREAMBLE_CODE 18
#define DEFAULT_ZONE_3_PREAMBLE_CODE 19
#define DEFAULT_ZONE_4_PREAMBLE_CODE 20
#define DEFAULT_TX_ANT_DLY        16436
#define DEFAULT_RX_ANT_DLY        16436
#define DEFAULT_TX_POWER          0x3A5A7A9AUL /* ~-14.5 dBm with smart power on */
#define DEFAULT_ANCHOR_POWER_MODE   ANCHOR_POWER_MODE_BALANCED
#define DEFAULT_UWB_PREAMBLE_LEN  0x34 /* DWT_PLEN_512 */
#define DEFAULT_UWB_RX_PAC        1    /* DWT_PAC16 */
#define DEFAULT_UWB_NS_SFD        1
#define DEFAULT_UWB_PHR_MODE      0    /* DWT_PHRMODE_STD */
#define DEFAULT_SMART_TX_POWER    true
#define DEFAULT_PG_DELAY          0xC2
#define DEFAULT_PREFILTER_ENABLE  ENABLE_MAHALANOBIS_PREFILTER

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
int                    sys_config_set_role(device_role_t role);
int                    sys_config_set_device_type(device_type_t device_type);
int                    sys_config_set_host_transport(host_transport_t host_transport);
int                    sys_config_set_device_id(uint8_t id);
device_type_t          sys_config_get_device_type(void);
host_transport_t       sys_config_get_host_transport(void);
const sys_calib_cfg_t *sys_config_get_calib(void);
int                    sys_config_set_calib(const sys_calib_cfg_t *calib);
const sys_prefilter_cfg_t *sys_config_get_prefilter(void);
int                    sys_config_set_prefilter(const sys_prefilter_cfg_t *prefilter);
void                   sys_config_get_anchor_layout(sys_anchor_layout_t *anchors, uint32_t *count);
int                    sys_config_set_anchor_layout(const sys_anchor_layout_t *anchors, uint32_t count);
int sys_config_set_power_mode(anchor_power_mode_t mode);
otp_err_t sys_config_factory_otp_write(const protobuf_factory_otp_write_t *req);
uint8_t sys_config_get_hw_rev(void);

uint32_t sys_config_get_active_zone_id(void);
void sys_config_set_active_zone_id(uint32_t zone_id);
bool sys_config_zone_profile_valid(const protobuf_zone_profile_t *profile);
int sys_config_set_zone_profile(const protobuf_zone_profile_t *profile);
bool sys_config_apply_zone_profile(uint32_t zone_id);

/* Storage */
int  sys_config_save(void);
int  sys_config_load(void);
void sys_config_reset_to_defaults(void);
void sys_config_print(void);

#endif /* __SYS_CONFIG_H */

/* End of file -------------------------------------------------------- */
