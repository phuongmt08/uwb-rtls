/* ============================== sys_ranging.c ==============================
 * @file       sys_ranging.c
 * @brief      System-level ranging (DS-TWR state machine)
 * @version    1.0.0
 * @date       2025-09-28
 */

/* Includes ----------------------------------------------------------- */
#include "sys_ranging.h"

#include "stm32f4xx_hal.h" /* HAL_GetTick */

#include <stddef.h> /* offsetof */

/* Private defines ---------------------------------------------------- */
#define SYS_UWB_RXBUF_MAX (128u)
#define REG_RX_TIME       0x15 /* 40-bit */
#define REG_TX_TIME       0x17 /* 40-bit */
/* Message types */
#define MSG_POLL          (0xE1u)
#define MSG_RESP          (0xE2u)
#define MSG_FINAL         (0xE3u)

/* Private types ------------------------------------------------------ */
// Force byte alignment (no padding) to match protocol frame layout
#pragma pack(push, 1)

/* Poll message (2 bytes) */
typedef struct
{
  uint8_t type;  // Message type
  uint8_t seq;   // Sequence number
} msg_poll_t;

/* Response message (2 bytes) */
typedef struct
{
  uint8_t type;  // Message type
  uint8_t seq;   // Sequence number
} msg_resp_t;

/* Final message (26 bytes total)
 * Note: each timestamp is 40-bit (LSBs used only).
 * uint64_t is used for convenience; upper 3 bytes are zero.
 */
typedef struct
{
  uint8_t  type;   // Message type
  uint8_t  seq;    // Sequence number
  uint64_t t1_40;  // Poll TX timestamp (40-bit in LSBs)
  uint64_t t4_40;  // Poll RX timestamp (40-bit in LSBs)
  uint64_t t5_40;  // Response TX timestamp (40-bit in LSBs)
} msg_final_t;
/* Private variables -------------------------------------------------- */
sys_ranging_cfg_t cfg = {
    .seq = 1,
    .rx_timeout_us = 10000,
    .uwb_cfg = {
        .channel = 5,
        .prf = 64,
        .data_rate = 2,   // 6M8
        .preamble_symbols = 128,
        .sfd_mode = DWM_SFD_STANDARD_IEEE,
        .phr_mode = DWM_PHYSIC_STANDARD_MODE
    }
};


/* Private function prototypes --------------------------------------- */
static inline uint64_t   u64_from40(const uint8_t b[5]);
static sys_ranging_err_t rx_with_timeout(uint8_t *buf, uint16_t bufsz, uint16_t *out_len, uint32_t tout_us);

/* Function definitions ----------------------------------------------- */
sys_ranging_err_t sys_ranging_ds_twr_tag_once(const sys_ranging_cfg_t *cfg, sys_ranging_result_t *res)
{
  CHECK_PARAM(cfg, SYS_RANGING_ERR_PARAM);
  CHECK_ERR(bsp_uwb_configure(&cfg->uwb_cfg) == BSP_OK, SYS_RANGING_ERR_PARAM);

  uint64_t t1 = 0, t4 = 0, t5 = 0;

  /* 1) POLL */
  msg_poll_t poll = { .type = MSG_POLL, .seq = cfg->seq };
  CHECK_ERR(bsp_uwb_tx(&poll, (uint16_t) sizeof(poll)) == BSP_OK, SYS_RANGING_ERR);

  CHECK_ERR(bsp_uwb_read_40bit(REG_TX_TIME, 0x00, &t1) == BSP_OK, SYS_RANGING_ERR);

  /* 2) Wait RESP */
  uint8_t  rx[SYS_UWB_RXBUF_MAX] = { 0 };
  uint16_t rlen = 0;
  CHECK_ERR(rx_with_timeout(rx, sizeof(rx), &rlen, cfg->rx_timeout_us) == SYS_RANGING_OK,
            SYS_RANGING_ERR_TIMEOUT);
  CHECK_ERR(rlen >= sizeof(msg_resp_t), SYS_RANGING_ERR_PROTO);
  const msg_resp_t *resp = (const msg_resp_t *) rx;
  CHECK_ERR(resp->type == MSG_RESP && resp->seq == cfg->seq, SYS_RANGING_ERR_PROTO);

  CHECK_ERR(bsp_uwb_read_40bit(REG_RX_TIME, 0x00, &t4) == BSP_OK, SYS_RANGING_ERR);

  /* 3) FINAL */
  msg_final_t fin = { .type = MSG_FINAL, .seq = cfg->seq, .t1_40 = t1, .t4_40 = t4, .t5_40 = 0 };
  CHECK_ERR(bsp_uwb_tx(&fin, (uint16_t) offsetof(msg_final_t, t5_40)) == BSP_OK, SYS_RANGING_ERR);

  CHECK_ERR(bsp_uwb_read_40bit(REG_TX_TIME, 0x00, &t5) == BSP_OK, SYS_RANGING_ERR);
  fin.t5_40 = t5;

  CHECK_ERR(bsp_uwb_tx(&fin, (uint16_t) sizeof(fin)) == BSP_OK, SYS_RANGING_ERR);

  if (res)
  {
    res->t1         = t1;
    res->t4         = t4;
    res->t5         = t5;
    res->t2         = 0;
    res->t3         = 0;
    res->t6         = 0;
    res->distance_m = -1.0f;
  }
  return SYS_RANGING_OK;
}

sys_ranging_err_t sys_ranging_ds_twr_anchor_once(const sys_ranging_cfg_t *cfg, sys_ranging_result_t *res)
{
  CHECK_PARAM(cfg, SYS_RANGING_ERR_PARAM);
  CHECK_ERR(bsp_uwb_configure(&cfg->uwb_cfg) == BSP_OK, SYS_RANGING_ERR_PARAM);

  uint8_t  rx[SYS_UWB_RXBUF_MAX] = { 0 };
  uint16_t rlen                  = 0;

  uint64_t t2 = 0, t3 = 0, t6 = 0;

  /* 1) Wait POLL */
  CHECK_ERR(rx_with_timeout(rx, sizeof(rx), &rlen, cfg->rx_timeout_us) == SYS_RANGING_OK,
            SYS_RANGING_ERR_TIMEOUT);
  CHECK_ERR(rlen >= sizeof(msg_poll_t), SYS_RANGING_ERR_PROTO);
  const msg_poll_t *poll = (const msg_poll_t *) rx;
  CHECK_ERR(poll->type == MSG_POLL, SYS_RANGING_ERR_PROTO);

  CHECK_ERR(bsp_uwb_read_40bit(REG_RX_TIME, 0x00, &t2) == BSP_OK, SYS_RANGING_ERR);

  /* 2) RESP */
  msg_resp_t resp = { .type = MSG_RESP, .seq = poll->seq };
  CHECK_ERR(bsp_uwb_tx(&resp, (uint16_t) sizeof(resp)) == BSP_OK, SYS_RANGING_ERR);

  CHECK_ERR(bsp_uwb_read_40bit(REG_TX_TIME, 0x00, &t3) == BSP_OK, SYS_RANGING_ERR);

  /* 3) Wait FINAL */
  uint32_t start_ms = HAL_GetTick();
  for (;;)
  {
    CHECK_ERR(rx_with_timeout(rx, sizeof(rx), &rlen, cfg->rx_timeout_us) == SYS_RANGING_OK,
              SYS_RANGING_ERR_TIMEOUT);

    if (rlen >= sizeof(msg_final_t))
    {
      const msg_final_t *fin = (const msg_final_t *) rx;
      if (fin->type == MSG_FINAL && fin->seq == poll->seq)
      {
	  CHECK_ERR(bsp_uwb_read_40bit(REG_RX_TIME, 0x00, &t6) == BSP_OK, SYS_RANGING_ERR);

        uint64_t t1 = fin->t1_40 & 0x000000FFFFFFFFULL;
        uint64_t t4 = fin->t4_40 & 0x000000FFFFFFFFULL;
        uint64_t t5 = fin->t5_40 & 0x000000FFFFFFFFULL;

        float distance = mw_ds_twr_calc(t1, t2, t3, t4, t5, t6);
        CHECK_ERR(distance >= 0.0f, SYS_RANGING_ERR_PROTO);

        if (res)
        {
          res->t1         = t1;
          res->t2         = t2;
          res->t3         = t3;
          res->t4         = t4;
          res->t5         = t5;
          res->t6         = t6;
          res->distance_m = distance;
        }
        return SYS_RANGING_OK;
      }
    }

    if ((HAL_GetTick() - start_ms) * 1000UL > cfg->rx_timeout_us)
      return SYS_RANGING_ERR_TIMEOUT;
  }
}

/* Private definitions ----------------------------------------------- */
static inline uint64_t u64_from40(const uint8_t b[5])
{
  return (uint64_t) b[0] | ((uint64_t) b[1] << 8) | ((uint64_t) b[2] << 16) | ((uint64_t) b[3] << 24)
         | ((uint64_t) b[4] << 32);
}

static sys_ranging_err_t rx_with_timeout(uint8_t *buf, uint16_t bufsz, uint16_t *out_len, uint32_t tout_us)
{
  CHECK_PARAM(buf && out_len && bufsz > 0, SYS_RANGING_ERR_PARAM);

  uint32_t start_ms = HAL_GetTick();
  for (;;)
  {
    if (bsp_uwb_rx(buf, bufsz, out_len) == BSP_OK)
      return SYS_RANGING_OK;

    if ((HAL_GetTick() - start_ms) * 1000UL > tout_us)
      return SYS_RANGING_ERR_TIMEOUT;
  }
}

/* End of file -------------------------------------------------------- */
