/* ============================== sys_ranging.c ==============================
 * @file       sys_ranging.c
 * @brief      Non-blocking ranging with unified logging and calibration
 * @version    5.2.0
 * @date       2025-12-11
 */

/* Includes ----------------------------------------------------------- */
#include "sys_ranging.h"
#include "sys_config.h"
#include "sys_logger.h"
#include "bsp_uwb.h"
#include "mw_ds_twr.h"
#include "stm32f4xx_hal.h"
#include <stdint.h>
#include <string.h>
#include <stdio.h>

/* Private defines ---------------------------------------------------- */
#define UWB_RX_BUFFER_SIZE (128u)
#define RX_POLL_INTERVAL_MS (5)

/* Private types ------------------------------------------------------ */
typedef enum {
  STATE_IDLE = 0,
  STATE_TAG_RANGING,
  STATE_TAG_COMPLETE,
  STATE_ANCHOR_RANGING,
  STATE_ANCHOR_COMPLETE,
  STATE_ERROR
} ranging_state_t;

typedef struct {
  ranging_state_t state;
  uint32_t state_entry_tick;
  uint8_t sequence_num;
  
  mw_dstwr_config_t mw_config;
  mw_dstwr_result_t mw_result;
  
  sys_ranging_result_t result;
  bool has_result;
} ranging_ctx_t;

/* Private variables -------------------------------------------------- */
static ranging_ctx_t s_ctx = {0};
static uint32_t s_success_count = 0;  // Unified counter

/* Private function prototypes ---------------------------------------- */
static void state_machine_reset(void);
static void format_distance_m(char *buf, size_t len, float distance_m);
static inline void format_ts(char *buf, uint64_t timestamp);
static void log_ranging_result(const sys_ranging_result_t *result, const char *role);

/* HAL Implementation for mw_ds_twr ----------------------------------- */

static int hal_tx(const void *data, uint16_t length)
{
  bsp_err_t err = bsp_uwb_tx(data, length);
  return (err == BSP_OK) ? 0 : -1;
}

static int hal_rx_with_timeout(uint8_t *buffer, uint16_t buffer_size,
                               uint16_t *received_length, uint32_t timeout_us)
{
  uint32_t timeout_ms = (timeout_us + 999) / 1000;
  uint32_t start_tick = HAL_GetTick();
  
  while (1) {
    bsp_err_t err = bsp_uwb_rx(buffer, buffer_size, received_length);
    
    if (err == BSP_OK && *received_length > 0) {
      return 0;
    }
    
    if ((HAL_GetTick() - start_tick) >= timeout_ms) {
      return -1;
    }
    
    HAL_Delay(1);
  }
}

static int hal_read_timestamp(uint8_t reg_addr, uint8_t sub_addr, uint64_t *timestamp)
{
  bsp_err_t err = bsp_uwb_read_40bit(reg_addr, sub_addr, timestamp);
  return (err == BSP_OK) ? 0 : -1;
}

static uint32_t hal_get_tick_ms(void)
{
  return HAL_GetTick();
}

/* HAL structure */
static const mw_dstwr_hal_t s_hal = {
  .tx = hal_tx,
  .rx_with_timeout = hal_rx_with_timeout,
  .read_timestamp = hal_read_timestamp,
  .get_tick_ms = hal_get_tick_ms
};

/* Private function implementations ----------------------------------- */

static void state_machine_reset(void)
{
  s_ctx.state = STATE_IDLE;
  s_ctx.has_result = false;
  memset(&s_ctx.result, 0, sizeof(s_ctx.result));
  memset(&s_ctx.mw_result, 0, sizeof(s_ctx.mw_result));
}

static void format_distance_m(char *buf, size_t len, float distance_m)
{
    int32_t mm = (int32_t)(distance_m * 1000.0f + (distance_m >= 0.0f ? 0.5f : -0.5f));
    int32_t abs_mm = (mm >= 0) ? mm : -mm;

    uint32_t m_part = (uint32_t)(abs_mm / 1000);
    uint32_t frac_part = (uint32_t)(abs_mm % 1000);

    if (mm < 0) {
        snprintf(buf, len, "-%lu.%03lu", (unsigned long)m_part, (unsigned long)frac_part);
    } else {
        snprintf(buf, len, "%lu.%03lu", (unsigned long)m_part, (unsigned long)frac_part);
    }
}

static inline void format_ts(char *buf, uint64_t timestamp)
{
    const char hex[] = "0123456789ABCDEF";
    uint64_t ts = timestamp & 0x000000FFFFFFFFFFULL;
    
    buf[0] = '0';
    buf[1] = 'x';
    buf[10] = '\0';
    
    for (int i = 9; i >= 2; i--) {
        buf[i] = hex[ts & 0x0F];
        ts >>= 4;
    }
}

/**
 * @brief Unified logging for both Tag and Anchor
 * @param result Ranging result
 * @param role "TAG" or "ANCHOR"
 */
static void log_ranging_result(const sys_ranging_result_t *result, const char *role)
{
    if (!result || !result->valid) return;
    
    s_success_count++;
    char dist_str[16];
    char t1[11], t2[11], t3[11], t4[11], t5[11], t6[11];
    
    format_distance_m(dist_str, sizeof(dist_str), result->distance_m);
    format_ts(t1, result->t1);
    format_ts(t2, result->t2);
    format_ts(t3, result->t3);
    format_ts(t4, result->t4);
    format_ts(t5, result->t5);
    format_ts(t6, result->t6);
    
    RLOG_I(LOG_OBJECT_CODE_RANGING, "========== %s DS-TWR #%lu ==========", role, s_success_count);
    RLOG_I(LOG_OBJECT_CODE_RANGING, "Distance: %s m", dist_str);
    RLOG_I(LOG_OBJECT_CODE_RANGING, "T1:%s T2:%s T3:%s", t1, t2, t3);
    RLOG_I(LOG_OBJECT_CODE_RANGING, "T4:%s T5:%s T6:%s", t4, t5, t6);
    RLOG_I(LOG_OBJECT_CODE_RANGING, "====================================");
}

/* Public API - Non-blocking Tag -------------------------------------- */

sys_ranging_err_t sys_ranging_tag_start(uint8_t sequence_num, uint32_t rx_timeout_ms)
{
  if (s_ctx.state != STATE_IDLE) {
    return SYS_RANGING_ERR_BUSY;
  }
  
  /* Get timeout from config if not specified */
  if (rx_timeout_ms == 0) {
    sys_config_t *cfg = sys_config_get();
    rx_timeout_ms = cfg->rx_timeout_ms;
  }
  
  state_machine_reset();
  s_ctx.sequence_num = sequence_num;
  s_ctx.state_entry_tick = HAL_GetTick();
  
  s_ctx.mw_config.sequence_num = sequence_num;
  s_ctx.mw_config.rx_timeout_us = rx_timeout_ms * 1000;
  s_ctx.mw_config.hal = &s_hal;
  
  s_ctx.state = STATE_TAG_RANGING;
  
  RLOG_D(LOG_OBJECT_CODE_RANGING, "[TAG] Starting DS-TWR seq=%u", sequence_num);
  
  return SYS_RANGING_OK;
}

sys_ranging_err_t sys_ranging_tag_process(void)
{
  switch (s_ctx.state) {
    
    case STATE_IDLE:
      return SYS_RANGING_ERR_NOT_STARTED;
    
    case STATE_TAG_RANGING: {
      mw_dstwr_err_t mw_err = mw_dstwr_execute_tag(&s_ctx.mw_config, &s_ctx.mw_result);
      
      if (mw_err == MW_DSTWR_OK) {
        /* Success - convert result */
        s_ctx.result.t1 = s_ctx.mw_result.timestamps.t1;
        s_ctx.result.t2 = s_ctx.mw_result.timestamps.t2;
        s_ctx.result.t3 = s_ctx.mw_result.timestamps.t3;
        s_ctx.result.t4 = s_ctx.mw_result.timestamps.t4;
        s_ctx.result.t5 = s_ctx.mw_result.timestamps.t5;
        s_ctx.result.t6 = s_ctx.mw_result.timestamps.t6;
        s_ctx.result.distance_m = s_ctx.mw_result.distance_m;
        s_ctx.result.valid = s_ctx.mw_result.valid;
        s_ctx.has_result = true;
        
        /* LOG RESULT */
        log_ranging_result(&s_ctx.result, "TAG");
        
        s_ctx.state = STATE_TAG_COMPLETE;
        return SYS_RANGING_OK;
      }
      else if (mw_err == MW_DSTWR_ERR_TIMEOUT) {
        RLOG_W(LOG_OBJECT_CODE_RANGING, "[TAG] Timeout");
        state_machine_reset();
        return mw_err;
      }
      else if (mw_err == MW_DSTWR_ERR_INVALID_MSG) {
        RLOG_W(LOG_OBJECT_CODE_RANGING, "[TAG] Invalid message");
        state_machine_reset();
        return mw_err;
      }
      else {
        RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[TAG] Error: %d", mw_err);
        s_ctx.state = STATE_ERROR;
        state_machine_reset();
        return mw_err;
      }
    }
    
    case STATE_TAG_COMPLETE:
      return SYS_RANGING_OK;
    
    case STATE_ERROR:
      return SYS_RANGING_ERR;
    
    default:
      state_machine_reset();
      return SYS_RANGING_ERR;
  }
}

sys_ranging_err_t sys_ranging_tag_get_result(sys_ranging_result_t *result)
{
  if (!result) return SYS_RANGING_ERR_PARAM;
  
  if (s_ctx.state == STATE_TAG_COMPLETE && s_ctx.has_result) {
    memcpy(result, &s_ctx.result, sizeof(sys_ranging_result_t));
    state_machine_reset();
    return SYS_RANGING_OK;
  }
  
  return SYS_RANGING_ERR_NO_RESULT;
}

/* Public API - Non-blocking Anchor ----------------------------------- */

sys_ranging_err_t sys_ranging_anchor_start(uint32_t rx_timeout_ms)
{
  if (s_ctx.state != STATE_IDLE) {
    return SYS_RANGING_ERR_BUSY;
  }
  
  state_machine_reset();
  s_ctx.state_entry_tick = HAL_GetTick();
  
  s_ctx.mw_config.sequence_num = 0;
  s_ctx.mw_config.rx_timeout_us = rx_timeout_ms * 1000;
  s_ctx.mw_config.hal = &s_hal;
  
  s_ctx.state = STATE_ANCHOR_RANGING;
  
  return SYS_RANGING_OK;
}

sys_ranging_err_t sys_ranging_anchor_process(void)
{
  switch (s_ctx.state) {
    
    case STATE_IDLE:
      return SYS_RANGING_ERR_NOT_STARTED;
    
    case STATE_ANCHOR_RANGING: {
      mw_dstwr_err_t mw_err = mw_dstwr_execute_anchor(&s_ctx.mw_config, &s_ctx.mw_result);
      if (mw_err == MW_DSTWR_OK) {
        /* Success - convert result */
        s_ctx.result.t1 = s_ctx.mw_result.timestamps.t1;
        s_ctx.result.t2 = s_ctx.mw_result.timestamps.t2;
        s_ctx.result.t3 = s_ctx.mw_result.timestamps.t3;
        s_ctx.result.t4 = s_ctx.mw_result.timestamps.t4;
        s_ctx.result.t5 = s_ctx.mw_result.timestamps.t5;
        s_ctx.result.t6 = s_ctx.mw_result.timestamps.t6;
        s_ctx.result.distance_m = s_ctx.mw_result.distance_m;
        s_ctx.result.valid = s_ctx.mw_result.valid;
        s_ctx.has_result = true;

        /* LOG RESULT */
        log_ranging_result(&s_ctx.result, "ANCHOR");

        s_ctx.state = STATE_ANCHOR_COMPLETE;
        return SYS_RANGING_OK;
      }
      else if (mw_err == MW_DSTWR_ERR_TIMEOUT) {
        state_machine_reset();
        return SYS_RANGING_ERR_TIMEOUT;
      }
      else if (mw_err == MW_DSTWR_ERR_INVALID_MSG) {
        RLOG_W(LOG_OBJECT_CODE_RANGING, "[ANCHOR] Invalid message");
        state_machine_reset();
        return SYS_RANGING_ERR_PROTO;
      }
      else {
        RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[ANCHOR] Error: %d", mw_err);
        s_ctx.state = STATE_ERROR;
        state_machine_reset();
        return SYS_RANGING_ERR;
      }
    }
    
    case STATE_ANCHOR_COMPLETE:
      return SYS_RANGING_OK;
    
    case STATE_ERROR:
      return SYS_RANGING_ERR;
    
    default:
      state_machine_reset();
      return SYS_RANGING_ERR;
  }
}

sys_ranging_err_t sys_ranging_anchor_get_result(sys_ranging_result_t *result)
{
  if (!result) return SYS_RANGING_ERR_PARAM;
  
  if (s_ctx.state == STATE_ANCHOR_COMPLETE && s_ctx.has_result) {
    memcpy(result, &s_ctx.result, sizeof(sys_ranging_result_t));
    state_machine_reset();
    return SYS_RANGING_OK;
  }
  
  return SYS_RANGING_ERR_NO_RESULT;
}
