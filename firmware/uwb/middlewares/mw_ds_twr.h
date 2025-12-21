/* ============================== mw_ds_twr.h ================================
 * @file       mw_ds_twr.h
 * @brief      DS-TWR with proper T5 correction message
 * @version    2.0.0
 * @date       2025-12-13
 */

#ifndef __MW_DS_TWR_H
#define __MW_DS_TWR_H

#include <stdint.h>
#include <stdbool.h>

/* Public defines ----------------------------------------------------- */
#define MW_DSTWR_MSG_TYPE_POLL        (0xEEu)
#define MW_DSTWR_MSG_TYPE_RESP        (0xE2u)
#define MW_DSTWR_MSG_TYPE_FINAL       (0xE3u)
#define MW_DSTWR_MSG_TYPE_CORRECTION  (0xE4u)  /* T5 correction or Result with distance */
#define MW_DSTWR_MSG_TYPE_RESULT      (0xE5u)  /* Result from Anchor (delayed TX mode) */

/* Minimum frame size for reliable transmission (including CRC) */
#define MW_DSTWR_MIN_FRAME_SIZE   (12u)

/* Anchor ID constants */
#define ANCHOR_ID_BROADCAST       (0xFF)  /* Broadcast to all anchors */
#define ANCHOR_ID_ANY             (0xFF)  /* Accept from any anchor */
#define TAG_ID_DEFAULT            (0x01)  /* Default Tag ID (single tag) */

/* Public enumerate/structure ----------------------------------------- */
typedef enum
{
  MW_DSTWR_OK = 0,
  MW_DSTWR_ERR = -1,
  MW_DSTWR_ERR_PARAM = -2,
  MW_DSTWR_ERR_TIMEOUT = -3,
  MW_DSTWR_ERR_INVALID_MSG = -4,
  MW_DSTWR_ERR_SYNC_LOST = -5      /* Received POLL when expecting FINAL */
} mw_dstwr_err_t;

typedef enum
{
  MW_DSTWR_ROLE_TAG = 0,
  MW_DSTWR_ROLE_ANCHOR = 1
} mw_dstwr_role_t;

/**
 * @brief DS-TWR message structures
 */
#pragma pack(push, 1)

/**
 * @brief POLL message - 12 bytes
 * Format: [msg_type][seq][target_anchor][rssi_last][padding x8]
 */
typedef struct __attribute__((packed))
{
  uint8_t msg_type;        /* 0xEE */
  uint8_t sequence_num;    /* Sequence number */
  uint8_t target_anchor;   /* Target anchor ID (0xFF = broadcast/any) */
  uint8_t rssi_last;       /* Last RSSI from anchor (for debug, 0 if unknown) */
  uint8_t padding[8];      /* Reserved for future use */
} mw_dstwr_poll_msg_t;

/**
 * @brief RESPONSE message - 12 bytes
 * Format: [msg_type][seq][anchor_id][rssi_poll][padding x8]
 */
typedef struct __attribute__((packed))
{
  uint8_t msg_type;      /* 0xE2 */
  uint8_t sequence_num;  /* Sequence number */
  uint8_t anchor_id;     /* This anchor's ID (from sys_config) */
  uint8_t rssi_poll;     /* RSSI of received POLL message */
  uint8_t padding[8];    /* Reserved for future use */
} mw_dstwr_resp_msg_t;

/**
 * @brief FINAL message (26 bytes)
 * Format: [msg_type][seq][T1:8][T4:8][T5:8]
 * NOTE: T5 will be 0 initially, real value comes in CORRECTION message
 */
typedef struct __attribute__((packed))
{
  uint8_t  msg_type;          /* 0xE3 */
  uint8_t  sequence_num;      /* Sequence number */
  uint64_t poll_tx_timestamp; /* T1: 40-bit in LSBs */
  uint64_t resp_rx_timestamp; /* T4: 40-bit in LSBs */
  uint64_t final_tx_timestamp;/* T5: 40-bit in LSBs (will be 0) */
} mw_dstwr_final_msg_t;

/**
 * @brief CORRECTION message - T5 correction AND/OR distance result
 * Format: [msg_type][seq][T5:8][distance_mm:4] = 14 bytes
 * - From TAG: T5 valid, distance_mm = 0
 * - From ANCHOR: T5 = 0, distance_mm valid
 */
typedef struct __attribute__((packed))
{
  uint8_t  msg_type;          /* 0xE4 */
  uint8_t  sequence_num;      /* Sequence number */
  uint64_t final_tx_timestamp;/* T5: 40-bit in LSBs */
  int32_t  distance_mm;       /* Distance in millimeters (signed) */
} mw_dstwr_correction_msg_t;

/**
 * @brief RESULT message - Distance with anchor info (delayed TX mode)
 * Format: [msg_type][seq][distance_mm:4][anchor_id][rssi_final][padding:4] = 12 bytes
 */
typedef struct __attribute__((packed))
{
  uint8_t  msg_type;      /* 0xE5 */
  uint8_t  sequence_num;  /* Sequence number */
  int32_t  distance_mm;   /* Distance in millimeters (signed) */
  uint8_t  anchor_id;     /* This anchor's ID */
  uint8_t  rssi_final;    /* RSSI of received FINAL message */
  uint8_t  padding[4];    /* Reserved for future use */
} mw_dstwr_result_msg_t;

#pragma pack(pop)

/* Compile-time checks for struct sizes */
_Static_assert(sizeof(mw_dstwr_poll_msg_t) == 12, "POLL message must be 12 bytes");
_Static_assert(sizeof(mw_dstwr_resp_msg_t) == 12, "RESP message must be 12 bytes");
_Static_assert(sizeof(mw_dstwr_final_msg_t) == 26, "FINAL message must be 26 bytes");
_Static_assert(sizeof(mw_dstwr_correction_msg_t) == 14, "CORRECTION message must be 14 bytes");
_Static_assert(sizeof(mw_dstwr_result_msg_t) == 12, "RESULT message must be 12 bytes");

/**
 * @brief DS-TWR timestamps structure
 */
typedef struct
{
  uint64_t t1, t2, t3, t4, t5, t6;
} mw_dstwr_timestamps_t;

/**
 * @brief Hardware abstraction callbacks
 */
typedef struct
{
  int (*tx)(const void *data, uint16_t length);
  int (*tx_delayed)(const void *data, uint16_t length, uint64_t tx_timestamp);
  int (*rx_with_timeout)(uint8_t *buffer, uint16_t buffer_size,
                         uint16_t *received_length, uint32_t timeout_us);
  int (*read_timestamp)(uint8_t reg_addr, uint8_t sub_addr, uint64_t *timestamp);
  uint32_t (*get_tick_ms)(void);
  int (*get_rssi)(void);
  uint16_t (*get_tx_antenna_delay)(void);  /* Get TX antenna delay in DW1000 units */
} mw_dstwr_hal_t;

/**
 * @brief DS-TWR configuration
 */
typedef struct
{
  uint8_t  sequence_num;
  uint8_t  target_anchor_id;  /* Target anchor ID (0xFF = any/broadcast) */
  uint32_t rx_timeout_us;
  const mw_dstwr_hal_t *hal;
} mw_dstwr_config_t;

/**
 * @brief DS-TWR result
 */
typedef struct
{
  mw_dstwr_timestamps_t timestamps;
  float distance_m;
  uint8_t anchor_id;     /* Which anchor this result came from */
  int8_t rssi;           /* RSSI in dBm (negative value, e.g. -30 to -100) */
  bool  valid;
} mw_dstwr_result_t;

/* Public function prototypes ----------------------------------------- */
mw_dstwr_err_t mw_dstwr_execute_tag(const mw_dstwr_config_t *config,
                                    mw_dstwr_result_t *result);

mw_dstwr_err_t mw_dstwr_execute_anchor(const mw_dstwr_config_t *config,
                                       mw_dstwr_result_t *result);

float mw_dstwr_calculate_distance(const mw_dstwr_timestamps_t *timestamps);

bool mw_dstwr_validate_message(const uint8_t *data, uint16_t length,
                               uint8_t expected_type, uint8_t expected_seq);

#endif /* __MW_DS_TWR_H */