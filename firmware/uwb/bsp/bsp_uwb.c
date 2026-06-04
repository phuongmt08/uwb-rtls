/**
 * @file       bsp_uwb.c
 * @brief      Board Support Package for UWB (DW1000)
 * @version    1.5.0
 * @date       2026-01-31
 */

/* Includes ----------------------------------------------------------- */
/* DecaWave driver */
#include "deca_device_api.h"
#include "deca_regs.h"

#include "bsp_uwb.h"
#include "bsp_util.h"
#include "err.h"
#include "mw_tdma_scheduler.h"
#include "spi.h"
#include "sys_logger.h"
#include "config.h"
#include "app_rtos_handles.h"

#include <stdio.h>
#include <string.h>

/* Private defines ---------------------------------------------------- */
#define DW1000_DEVICE_ID       0xDECA0130UL
#define RX_TIMEOUT_MS          1000
#define DWT_START_RX_IMMEDIATE 0
#define DWT_START_RX_DELAYED   1
#define TX_MAX_PAYLOAD         120
#define DW1000_CRC_LENGTH      2

#define DW_MASK_40             0x000000FFFFFFFFFFULL
#define DW_FMT                 "0x%08lX%08lX"
#define DW_ARG(x)              (unsigned long) ((x) >> 32), (unsigned long) ((x) & 0xFFFFFFFFUL)

static inline uint64_t dw_read_timestamp(const uint8_t *buf)
{
  return ((uint64_t) buf[0]) | ((uint64_t) buf[1] << 8) | ((uint64_t) buf[2] << 16)
         | ((uint64_t) buf[3] << 24) | ((uint64_t) buf[4] << 32);
}

#define RX_TIME_ID 0x15
#define TX_TIME_ID 0x17

/* Private variables -------------------------------------------------- */
static bool     s_initialized       = false;
static uint64_t s_last_rx_timestamp = 0;    /* Cached RX timestamp */
static uint64_t s_last_tx_timestamp = 0;    /* Cached TX timestamp */
static bsp_uwb_rx_quality_t s_last_rx_quality = {0};
static uint16_t s_tx_antenna_delay  = 0;    /* Cached TX antenna delay */
static uint16_t s_rx_antenna_delay  = 0;    /* Cached RX antenna delay */

/* RX error counters — incremented in bsp_uwb_rx(), read via bsp_uwb_get_rx_error_counts(). */
static uint32_t s_rx_timeout_count  = 0;
static uint32_t s_rx_crc_err_count  = 0;
static uint32_t s_rx_phr_err_count  = 0;
static uint32_t s_rx_sync_err_count = 0;

/* Keep enough room for TX_DONE plus all RESP/RESULT frames in one TDMA phase.
 * Ring-buffer capacity is size-1, so 32 stores up to 31 events. */
#define UWB_EVENT_QUEUE_SIZE    32
static volatile uint8_t s_ev_head           = 0; /* ISR writes here  (next write index) */
static volatile uint8_t s_ev_tail           = 0; /* foreground reads here (next read index) */
static bsp_uwb_event_t  s_ev_queue[UWB_EVENT_QUEUE_SIZE];
static volatile bsp_uwb_event_stats_t s_event_stats = {0};
static void uwb_tx_cb(const dwt_callback_data_t *cb_data);
static void uwb_rx_cb(const dwt_callback_data_t *cb_data);

bool bsp_uwb_get_event(bsp_uwb_event_t *out_event)
{
    __disable_irq();
    if (s_ev_tail == s_ev_head) {
        __enable_irq();
        return false;
    }
    *out_event = s_ev_queue[s_ev_tail];
    s_ev_tail  = (uint8_t)((s_ev_tail + 1u) % UWB_EVENT_QUEUE_SIZE);
    __enable_irq();
    return true;
}

void bsp_uwb_get_event_stats(bsp_uwb_event_stats_t *stats)
{
    if (!stats) return;
    __disable_irq();
    *stats = (bsp_uwb_event_stats_t) {
        .tx_done        = s_event_stats.tx_done,
        .rx_ok          = s_event_stats.rx_ok,
        .queue_overflow = s_event_stats.queue_overflow,
        .irq_extra_pass = s_event_stats.irq_extra_pass,
        .rx_rearm_fail  = s_event_stats.rx_rearm_fail,
    };
    memset((void *)&s_event_stats, 0, sizeof(s_event_stats));
    __enable_irq();
}

void bsp_uwb_clear_event(void)
{
    __disable_irq();
    s_ev_head = s_ev_tail; /* drop all queued events */
    __enable_irq();
}
/* Public variables --------------------------------------------------- */
extern SPI_HandleTypeDef hspi1;

/* Private function prototypes ---------------------------------------- */
static void     reset_DW1000(void);
static void     port_set_dw1000_slowrate(void);
static void     port_set_dw1000_fastrate(void);
static uint16_t ms_to_dw1000_rxtimeout_units(uint32_t timeout_ms);
static void     capture_rx_quality(bsp_uwb_rx_quality_t *out_quality);

/* Private functions ------------------------------------------------------ */
/* Note: Keep writetospi and readfromspi compatible with deca_driver */

int writetospi(uint16 headerLength, const uint8 *headerBuffer, uint32 bodylength, const uint8 *bodyBuffer)
{
  HAL_StatusTypeDef status;
  HAL_GPIO_WritePin(UWB_CS_PORT, UWB_CS_PIN, GPIO_PIN_RESET);

  if (headerLength > 0)
  {
    status = HAL_SPI_Transmit(&hspi1, (uint8_t *) headerBuffer, headerLength, HAL_MAX_DELAY);
    if (status != HAL_OK)
    {
      HAL_GPIO_WritePin(UWB_CS_PORT, UWB_CS_PIN, GPIO_PIN_SET);
      return -1;
    }
  }

  if (bodylength > 0)
  {
    status = HAL_SPI_Transmit(&hspi1, (uint8_t *) bodyBuffer, bodylength, HAL_MAX_DELAY);
    if (status != HAL_OK)
    {
      HAL_GPIO_WritePin(UWB_CS_PORT, UWB_CS_PIN, GPIO_PIN_SET);
      return -1;
    }
  }

  HAL_GPIO_WritePin(UWB_CS_PORT, UWB_CS_PIN, GPIO_PIN_SET);
  return 0;
}

int readfromspi(uint16 headerLength, const uint8 *headerBuffer, uint32 readlength, uint8 *readBuffer)
{
  HAL_StatusTypeDef status;
  HAL_GPIO_WritePin(UWB_CS_PORT, UWB_CS_PIN, GPIO_PIN_RESET);

  if (headerLength > 0)
  {
    status = HAL_SPI_Transmit(&hspi1, (uint8_t *) headerBuffer, headerLength, HAL_MAX_DELAY);
    if (status != HAL_OK)
    {
      HAL_GPIO_WritePin(UWB_CS_PORT, UWB_CS_PIN, GPIO_PIN_SET);
      return -1;
    }
  }

  if (readlength > 0)
  {
    status = HAL_SPI_Receive(&hspi1, readBuffer, readlength, HAL_MAX_DELAY);
    if (status != HAL_OK)
    {
      HAL_GPIO_WritePin(UWB_CS_PORT, UWB_CS_PIN, GPIO_PIN_SET);
      return -1;
    }
  }

  HAL_GPIO_WritePin(UWB_CS_PORT, UWB_CS_PIN, GPIO_PIN_SET);
  return 0;
}

static void reset_DW1000(void)
{
  HAL_GPIO_WritePin(UWB_RST_PORT, UWB_RST_PIN, GPIO_PIN_RESET);
  bsp_delay_ms(2);
  HAL_GPIO_WritePin(UWB_RST_PORT, UWB_RST_PIN, GPIO_PIN_SET);
  bsp_delay_ms(2);
}

static void port_set_dw1000_slowrate(void)
{
  /* DW1000 max SPI speed for init is < 3 MHz.
   * 84MHz / 128 = 656.25 kHz */
  hspi1.Init.BaudRatePrescaler = SPI_BAUDRATEPRESCALER_128;
  HAL_SPI_Init(&hspi1);
}

static void port_set_dw1000_fastrate(void)
{
  /* DW1000 max SPI speed for data is < 20 MHz.
   *  84MHz / 8 = 10.5 MHz*/
  hspi1.Init.BaudRatePrescaler = SPI_BAUDRATEPRESCALER_8;
  HAL_SPI_Init(&hspi1);
}

static uint16_t ms_to_dw1000_rxtimeout_units(uint32_t timeout_ms)
{
  /* 1 unit = ~1.0256 μs */
  uint32_t units = (timeout_ms * 1000u * 1000u) / 10256u;
  if (units > 0xFFFFu)
  {
    units = 0xFFFFu;
  }
  return (uint16_t) units;
}

static void capture_rx_quality(bsp_uwb_rx_quality_t *out_quality)
{
  memset(out_quality, 0, sizeof(*out_quality));

  /* Directly query DW1000 registers to bypass compiler strict-aliasing optimization bugs on block-reads */
  uint16_t max_noise      = dwt_read16bitoffsetreg(LDE_IF_ID, LDE_THRESH_OFFSET);
  uint16_t fp_amp1        = dwt_read16bitoffsetreg(RX_TIME_ID, 0x7);
  uint16_t std_noise      = dwt_read16bitoffsetreg(RX_FQUAL_ID, 0x0);
  uint16_t fp_amp2        = dwt_read16bitoffsetreg(RX_FQUAL_ID, 0x2);
  uint16_t fp_amp3        = dwt_read16bitoffsetreg(RX_FQUAL_ID, 0x4);
  uint16_t rx_pream_count = (uint16_t)((dwt_read32bitreg(RX_FINFO_ID) & RX_FINFO_RXPACC_MASK) >> RX_FINFO_RXPACC_SHIFT);

  out_quality->fp_amp1        = fp_amp1;
  out_quality->fp_amp2        = fp_amp2;
  out_quality->fp_amp3        = fp_amp3;
  out_quality->std_noise      = std_noise;
  out_quality->max_noise      = max_noise;
  out_quality->rx_pream_count = rx_pream_count;

  if (rx_pream_count > 0U && (fp_amp1 != 0U || fp_amp2 != 0U || fp_amp3 != 0U))
  {
    uint32_t fp_sum = (uint32_t)fp_amp1 + (uint32_t)fp_amp2 + (uint32_t)fp_amp3;
    uint32_t fp_norm_q8 = (fp_sum << 8) / (uint32_t)rx_pream_count;
    uint32_t fp_snr_q8  = (fp_sum << 8) / ((uint32_t)std_noise + 1U);

    out_quality->fp_amp_norm_q8 = (fp_norm_q8 > 0xFFFFU) ? 0xFFFFU : (uint16_t)fp_norm_q8;
    out_quality->fp_snr_q8      = (fp_snr_q8 > 0xFFFFU) ? 0xFFFFU : (uint16_t)fp_snr_q8;
    out_quality->valid          = true;
  }
}

/* Public functions --------------------------------------------------- */

bsp_err_t bsp_uwb_init(void)
{
  uint32_t dev_id;

  bsp_util_init();
  reset_DW1000();
  port_set_dw1000_slowrate();

  /* Load LDE microcode - CRITICAL for accurate RX timestamps */
  if (dwt_initialise(DWT_LOADUCODE) != DWT_SUCCESS)
  {
    RLOG_E(LOG_OBJECT_CODE_UWB_DRIVER, ERR_UWB_INIT, "dwt_initialise failed");
    return BSP_ERR;
  }

  dev_id = dwt_readdevid();
  if (dev_id != DW1000_DEVICE_ID)
  {
    RLOG_E(LOG_OBJECT_CODE_UWB_DRIVER, ERR_UWB_INIT, "Wrong Device ID: 0x%08X", dev_id);
    return BSP_ERR;
  }

  port_set_dw1000_fastrate();
  dwt_setleds(1);

  /* Register UWB callbacks for foreground event processing */
  dwt_setcallbacks(uwb_tx_cb, uwb_rx_cb);
  /* Event-driven state machine only queues TX_DONE and RX_OK events.
   * RX error IRQs are still enabled so the driver can clear/reset/re-arm RX
   * inside a multi-anchor window; they are not queued or logged as events. */
  dwt_setinterrupt(DWT_INT_TFRS | DWT_INT_RFCG | DWT_INT_RFTO |
                   DWT_INT_RXPTO | DWT_INT_RXOVRR | DWT_INT_RFCE | DWT_INT_SFDT |
                   DWT_INT_RPHE | DWT_INT_RFSL, 1);

  s_initialized = true;
  return BSP_OK;
}

static uint16_t get_sfd_timeout(uint32_t preamble_len, uint32_t ns_sfd, uint32_t rx_pac)
{
    uint32_t plen = 512;
    switch (preamble_len) {
        case 0x04: plen = 64; break;
        case 0x14: plen = 128; break;
        case 0x24: plen = 256; break;
        case 0x34: plen = 512; break;
        case 0x08: plen = 1024; break;
        case 0x18: plen = 1536; break;
        case 0x28: plen = 2048; break;
        case 0x0C: plen = 4096; break;
        default: plen = 512; break;
    }
    uint32_t pac = 16;
    switch (rx_pac) {
        case 0: pac = 8; break;
        case 1: pac = 16; break;
        case 2: pac = 32; break;
        case 3: pac = 64; break;
        default: pac = 16; break;
    }
    return (uint16_t)(plen + ns_sfd + pac - 8);
}

bsp_err_t bsp_uwb_configure(const protobuf_uwb_cfg_t *cfg)
{
  CHECK_PARAM(cfg != NULL, BSP_ERR_PARAM);
  CHECK_PARAM(s_initialized, BSP_ERR);

    dwt_config_t dw_cfg = {
        .chan           = cfg->uwb_channel,
        .prf            = (cfg->uwb_prf == 64) ? DWT_PRF_64M : DWT_PRF_16M,
        .txPreambLength = cfg->uwb_preamble_len,
        .rxPAC          = cfg->uwb_rx_pac,
        .txCode         = cfg->uwb_preamble_code,
        .rxCode         = cfg->uwb_preamble_code,
        .nsSFD          = cfg->uwb_ns_sfd,
        .dataRate       = cfg->uwb_data_rate,
        .phrMode        = cfg->uwb_phr_mode,
        .sfdTO          = get_sfd_timeout(cfg->uwb_preamble_len, cfg->uwb_ns_sfd, cfg->uwb_rx_pac)
    };
    
    RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "[BSP][CFG] CH=%u PRF=%uMHz DR=%u PCode=%u",
           dw_cfg.chan, cfg->uwb_prf, dw_cfg.dataRate, dw_cfg.txCode);
    RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "[BSP][CFG] PLEN=%u PAC=%u SFD=%u nsSFD=%u PHR=%u",
           cfg->uwb_preamble_len, cfg->uwb_rx_pac, dw_cfg.sfdTO, dw_cfg.nsSFD, dw_cfg.phrMode);

  if (dwt_configure(&dw_cfg, DWT_LOADNONE) != DWT_SUCCESS)
  {
    return BSP_ERR;
  }

  dwt_txconfig_t tx_cfg;
  tx_cfg.power = cfg->tx_power;
  tx_cfg.PGdly = cfg->pg_delay;
  dwt_configuretxrf(&tx_cfg);
  // Add smart power configuration 
  dwt_setsmarttxpower(cfg->smart_tx_power ? 1 : 0);

  dwt_setrxantennadelay(cfg->rx_antenna_delay);
  dwt_settxantennadelay(cfg->tx_antenna_delay);
  /* Without this, predict returns (chip_time << 8) + 0 instead of + delay,
   * causing T5 to be wrong by exactly tx_antenna_delay ticks → ~26m distance error. */
  s_tx_antenna_delay = cfg->tx_antenna_delay;
  s_rx_antenna_delay = cfg->rx_antenna_delay;

    /* Configure interrupt mask.
     * Event-driven TDMA needs DWT_INT_TFRS (TX frame sent) so that
     * uwb_tx_cb fires and TX_DONE events reach the queue.
     * bsp_uwb_configure() is called AFTER bsp_uwb_init() which already set the
     * interrupt mask, but dwt_configure() resets the chip config so we must
     * re-apply it here. */
    dwt_setinterrupt((uint32)(DWT_INT_TFRS |   /* TX frame sent      */
                              DWT_INT_RFCG |   /* RX good frame      */
                              DWT_INT_RFTO |   /* RX frame timeout   */
                              DWT_INT_RXPTO |  /* Preamble timeout   */
                              DWT_INT_RXOVRR | /* RX buffer overrun  */
                              DWT_INT_RFCE |   /* RX CRC error       */
                              DWT_INT_RPHE |   /* RX PHR error       */
                              DWT_INT_RFSL |   /* RX sync loss       */
                              DWT_INT_SFDT), 1); /* RX SFD timeout   */

    dwt_write32bitreg(SYS_STATUS_ID, 0xFFFFFFFFUL);
    dwt_forcetrxoff();

    RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "[BSP][CFG] Configuration complete (TX delay=%u, RX delay=%u)",
           cfg->tx_antenna_delay, cfg->rx_antenna_delay);

  return BSP_OK;
}

bsp_err_t bsp_uwb_tx(const void *data, uint16_t length)
{
  if (!data || length == 0 || length > TX_MAX_PAYLOAD)
    return BSP_ERR;

  /* Ensure idle and clear all flags */
  dwt_forcetrxoff();
  dwt_write32bitreg(SYS_STATUS_ID, 0xFFFFFFFFUL);

  dwt_writetxdata(length, (uint8_t *) data, 0);
  dwt_writetxfctrl(length + DW1000_CRC_LENGTH, 0);

  if (dwt_starttx(DWT_START_TX_IMMEDIATE) != DWT_SUCCESS)
  {
    return BSP_ERR;
  }

  return BSP_OK;
}

bsp_err_t bsp_uwb_rx(void *data, uint16_t length, uint16_t *out_len)
{
  CHECK_PARAM(data && out_len, BSP_ERR_PARAM);
  CHECK_PARAM(s_initialized, BSP_ERR);

  uint32_t status = dwt_read32bitreg(SYS_STATUS_ID);

  /* Good frame received */
  if (status & SYS_STATUS_RXFCG)
  {
    /* 1. Wait for LDE processing done */
    /* CRITICAL: LDE must complete for accurate RX timestamp */
    uint32_t lde_timeout = HAL_GetTick() + 2;
    while (!(dwt_read32bitreg(SYS_STATUS_ID) & SYS_STATUS_LDEDONE))
    {
      if (HAL_GetTick() > lde_timeout)
      {
        RLOG_W(LOG_OBJECT_CODE_UWB_DRIVER, "[RX] LDE timeout - DROP FRAME");

        /* DROP frame - timestamp would be inaccurate */
        dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_RXFCG | SYS_STATUS_RXDFR | SYS_STATUS_RXPRD
                                           | SYS_STATUS_RXSFDD | SYS_STATUS_RXPHD | SYS_STATUS_LDEDONE);
        dwt_forcetrxoff();
        dwt_rxreset();
        dwt_rxenable(DWT_START_RX_IMMEDIATE);

        s_last_rx_timestamp = 0;
        *out_len            = 0;
        return BSP_ERR; /* Frame dropped */
      }
    }

    /* 2. Read RX timestamp */
    /* Note: dwt_setrxantennadelay() is already configured in hardware.
     * Adding s_rx_antenna_delay again here causes a large positive bias in range. */
    uint8_t ts_buf[5];
    dwt_readrxtimestamp(ts_buf);
    s_last_rx_timestamp = dw_read_timestamp(ts_buf) & DW_MASK_40;

    capture_rx_quality(&s_last_rx_quality);

    /* 3. Read Frame Info to get length */
    uint32_t rxfi            = dwt_read32bitreg(RX_FINFO_ID);
    uint16_t frame_len_onair = (uint16_t) (rxfi & 0x03FF);

    /* 4. Calculate payload length (exclude CRC) */
    uint16_t payload_len = 0;
    if (frame_len_onair >= DW1000_CRC_LENGTH)
    {
      payload_len = frame_len_onair - DW1000_CRC_LENGTH;
    }

    /* 5. Copy DIRECTLY to user buffer*/
    /* Avoid double buffering on stack */
    uint16_t copy_len = (payload_len < length) ? payload_len : length;

    if (copy_len > 0)
    {
      dwt_readrxdata((uint8_t *) data, copy_len, 0);
    }

    *out_len = payload_len;

    /* 6. Clear RX flags */
    dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_RXFCG | SYS_STATUS_RXDFR | SYS_STATUS_RXPRD
                                       | SYS_STATUS_RXSFDD | SYS_STATUS_RXPHD | SYS_STATUS_LDEDONE);

    /* Keep receiver running for back-to-back frames in the same TDMA phase
     * (e.g. TAG collecting multiple RESP/RESULT packets). */
    dwt_forcetrxoff();
    dwt_rxenable(DWT_START_RX_IMMEDIATE);

    return BSP_OK;
  }

  /* RX timeout */
  if (status & (SYS_STATUS_RXRFTO | SYS_STATUS_RXPTO))
  {
    /* Clear flags */
    dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_RXRFTO | SYS_STATUS_RXPTO | SYS_STATUS_RXDFR
                                       | SYS_STATUS_RXPRD | SYS_STATUS_RXSFDD | SYS_STATUS_RXPHD
                                       | SYS_STATUS_LDEDONE);

    /* Re-enable RX */
    dwt_forcetrxoff();
    dwt_rxenable(DWT_START_RX_IMMEDIATE);

    s_last_rx_timestamp = 0;
    memset(&s_last_rx_quality, 0, sizeof(s_last_rx_quality));
    *out_len            = 0;
    s_rx_timeout_count++;
    return BSP_ERR_TIMEOUT;
  }

  /* RX frame errors */
  if (status & (SYS_STATUS_RXFCE | SYS_STATUS_RXPHE | SYS_STATUS_RXRFSL))
  {
    /* Track which error bits fired — helps distinguish CRC fail vs PHY vs sync. */
    if (status & SYS_STATUS_RXFCE)  s_rx_crc_err_count++;
    if (status & SYS_STATUS_RXPHE)  s_rx_phr_err_count++;
    if (status & SYS_STATUS_RXRFSL) s_rx_sync_err_count++;

    /* Clear flags */
    dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_RXFCE | SYS_STATUS_RXPHE | SYS_STATUS_RXRFSL
                                       | SYS_STATUS_RXDFR | SYS_STATUS_RXSFDD | SYS_STATUS_RXPRD
                                       | SYS_STATUS_LDEDONE);

    /* Reset RX logic */
    dwt_forcetrxoff();
    dwt_rxreset();
    dwt_rxenable(DWT_START_RX_IMMEDIATE);

    s_last_rx_timestamp = 0;
    memset(&s_last_rx_quality, 0, sizeof(s_last_rx_quality));
    *out_len            = 0;
    return BSP_ERR; /* Return error to indicate bad frame */
  }

  return BSP_ERR; /* Still busy receiving */
}

bsp_err_t bsp_uwb_read_40bit(uint8_t reg_addr, uint8_t sub_addr, uint64_t *timestamp)
{
  CHECK_PARAM(timestamp, BSP_ERR_PARAM);

  if (reg_addr == RX_TIME_ID && sub_addr == 0)
  {
    if (s_last_rx_timestamp == 0)
      return BSP_ERR;
    *timestamp = s_last_rx_timestamp;
  }
  else if (reg_addr == TX_TIME_ID && sub_addr == 0)
  {
    *timestamp = s_last_tx_timestamp;
  }
  else
  {
    uint8_t buf[5];
    dwt_readfromdevice(reg_addr, sub_addr, 5, buf);
    *timestamp = dw_read_timestamp(buf);
  }
  return BSP_OK;
}

void bsp_uwb_reset(bool active)
{
  if (active)
  {
    HAL_GPIO_WritePin(UWB_RST_PORT, UWB_RST_PIN, GPIO_PIN_RESET);
  }
  else
  {
    HAL_GPIO_WritePin(UWB_RST_PORT, UWB_RST_PIN, GPIO_PIN_SET);
  }
}

bsp_err_t bsp_uwb_enable_rx(uint32_t timeout_ms)
{
  CHECK_PARAM(s_initialized, BSP_ERR);

  dwt_forcetrxoff();
  dwt_write32bitreg(SYS_STATUS_ID, 0xFFFFFFFFUL);

  if (timeout_ms > 0 && timeout_ms <= 67)
  {
    uint16_t timeout_units = ms_to_dw1000_rxtimeout_units(timeout_ms);
    dwt_setrxtimeout(timeout_units);
  }
  else
  {
    dwt_setrxtimeout(0);
  }

  /* Enable RX */
  if (dwt_rxenable(DWT_START_RX_IMMEDIATE) != DWT_SUCCESS)
  {
    RLOG_W(LOG_OBJECT_CODE_UWB_DRIVER, "[RX] dwt_rxenable failed");
    return BSP_ERR;
  }

  return BSP_OK;
}

bsp_err_t bsp_uwb_enable_rx_delayed(uint64_t rx_timestamp_dw, uint32_t timeout_ms)
{
  CHECK_PARAM(s_initialized, BSP_ERR);

  dwt_forcetrxoff();
  dwt_write32bitreg(SYS_STATUS_ID, 0xFFFFFFFFUL);

  if (timeout_ms > 0 && timeout_ms <= 67)
  {
    uint16_t timeout_units = ms_to_dw1000_rxtimeout_units(timeout_ms);
    dwt_setrxtimeout(timeout_units);
  }
  else
  {
    dwt_setrxtimeout(0);
  }

  uint8_t sys_time_buf[5];
  dwt_readsystime(sys_time_buf);
  uint64_t now = dw_read_timestamp(sys_time_buf) & DW_MASK_40;

  const uint64_t DW_TICK_PER_US = 63898ULL;
  const uint32_t MIN_GUARD_US   = 400;                           /* 400µs margin */
  const uint64_t MIN_GUARD_DW   = MIN_GUARD_US * DW_TICK_PER_US;

  const uint64_t MAX_REASONABLE_AHEAD_DW = tdma_us_to_dw(1000000U); /* 1 sec limit */
  uint64_t       ahead_dw                = (rx_timestamp_dw - now) & DW_MASK_40;

  if (ahead_dw > MAX_REASONABLE_AHEAD_DW)
  {
    /* Past or too far future: fallback to immediate RX to catch frame if possible */
    RLOG_W(LOG_OBJECT_CODE_UWB_DRIVER, "[RX_DELAY] TOO LATE ahead=%lu (fallback IMMEDIATE)", (unsigned long)ahead_dw);
    if (dwt_rxenable(DWT_START_RX_IMMEDIATE) != DWT_SUCCESS) return BSP_ERR;
    return BSP_OK;
  }
  else if (ahead_dw <= MIN_GUARD_DW)
  {
    /* Too close: fallback to immediate */
    if (dwt_rxenable(DWT_START_RX_IMMEDIATE) != DWT_SUCCESS) return BSP_ERR;
    return BSP_OK;
  }

  /* Clear HPDWARN before scheduling (prevent stale warning) */
  dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_HPDWARN);

  uint32_t dx_time = (uint32_t) (rx_timestamp_dw >> 8);
  dx_time &= 0xFFFFFFFEUL;
  dwt_setdelayedtrxtime(dx_time);

  if (dwt_rxenable(DWT_START_RX_DELAYED) != DWT_SUCCESS)
  {
    uint32_t status = dwt_read32bitreg(SYS_STATUS_ID);
    if (status & SYS_STATUS_HPDWARN) {
        RLOG_W(LOG_OBJECT_CODE_UWB_DRIVER, "[RX_DELAY] HPDWARN (Late RX) - fallback IMMEDIATE");
        dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_HPDWARN);
        dwt_forcetrxoff();
        if (dwt_rxenable(DWT_START_RX_IMMEDIATE) != DWT_SUCCESS) return BSP_ERR;
        return BSP_OK;
    }
    
    RLOG_E(LOG_OBJECT_CODE_UWB_DRIVER, ERR_UWB_TIMESTAMP,
           "[RX_DELAY] dwt_rxenable delayed failed (status=0x%08lX)", (unsigned long) status);
    return BSP_ERR;
  }

  return BSP_OK;
}

void bsp_uwb_idle(void)
{
  dwt_forcetrxoff();
  dwt_write32bitreg(SYS_STATUS_ID, 0xFFFFFFFFUL);
}


bsp_err_t bsp_uwb_get_last_rx_quality(bsp_uwb_rx_quality_t *quality)
{
  CHECK_PARAM(quality != NULL, BSP_ERR_PARAM);
  if (!s_last_rx_quality.valid)
  {
    return BSP_ERR;
  }
  *quality = s_last_rx_quality;
  return BSP_OK;
}

bsp_err_t bsp_uwb_get_last_rx_timestamp(uint64_t *timestamp)
{
  CHECK_PARAM(timestamp, BSP_ERR_PARAM);
  if (s_last_rx_timestamp == 0ULL)
  {
    return BSP_ERR;
  }
  *timestamp = s_last_rx_timestamp;
  return BSP_OK;
}

bsp_err_t bsp_uwb_get_last_tx_timestamp(uint64_t *timestamp)
{
  CHECK_PARAM(timestamp, BSP_ERR_PARAM);
  if (s_last_tx_timestamp == 0ULL)
  {
    return BSP_ERR;
  }
  *timestamp = s_last_tx_timestamp;
  return BSP_OK;
}

bsp_err_t bsp_uwb_tx_delayed(const void *data, uint16_t length, uint64_t tx_timestamp)
{
  CHECK_PARAM(s_initialized, BSP_ERR);
  CHECK_PARAM(data != NULL, BSP_ERR);
  CHECK_PARAM(length > 0 && length <= TX_MAX_PAYLOAD, BSP_ERR);

  dwt_forcetrxoff();
  dwt_write32bitreg(SYS_STATUS_ID, 0xFFFFFFFFUL);

  dwt_writetxdata(length, (uint8_t *) data, 0);
  dwt_writetxfctrl(length + DW1000_CRC_LENGTH, 0);

  if (tx_timestamp < s_tx_antenna_delay)
  {
    RLOG_E(LOG_OBJECT_CODE_UWB_DRIVER, ERR_UWB_TIMESTAMP, "[TX_DELAY] tx_timestamp < antenna_delay");
    return BSP_ERR;
  }
  uint64_t scheduled_time = (tx_timestamp - s_tx_antenna_delay) & DW_MASK_40;

  uint8_t sys_time_buf[5];
  dwt_readsystime(sys_time_buf);

  uint64_t now = dw_read_timestamp(sys_time_buf) & DW_MASK_40;

  /* Minimum guard time for DW1000 to schedule TX reliably.
   * Formula: guard_dw = guard_us * (1e-6 / 15.65e-12) ≈ guard_us * 63898
   * For 400µs safety margin: 400 * 63898 ≈ 25,559,200 DW ticks
   */
  const uint64_t DW_TICK_PER_US = 63898ULL;
  const uint32_t MIN_GUARD_US   = 400;                           /* 400µs safety margin */
  const uint64_t MIN_GUARD_DW   = MIN_GUARD_US * DW_TICK_PER_US; /* ~25.5M ticks */

  /* Check if scheduled time is in valid future.
   * Direct signed subtraction is wrong near 40-bit wrap and can mark a
   * valid future timestamp as "too late", especially for later slots. */
  const uint64_t MAX_REASONABLE_AHEAD_DW = tdma_us_to_dw(200000U); /* 200 ms */
  uint64_t       ahead_dw                = (scheduled_time - now) & DW_MASK_40;

  if (ahead_dw <= MIN_GUARD_DW || ahead_dw > MAX_REASONABLE_AHEAD_DW)
  {
    uint32_t ahead_us = tdma_dw_to_us(ahead_dw);
    RLOG_E(LOG_OBJECT_CODE_UWB_DRIVER, ERR_UWB_TIMESTAMP,
           "[TX_DELAY] TOO LATE/INVALID! ahead=%lu DW (~%lu us), min=%lu DW", (unsigned long) ahead_dw,
           (unsigned long) ahead_us, (unsigned long) MIN_GUARD_DW);
    return BSP_ERR;
  }

  /* Clear HPDWARN before scheduling (prevent stale warning) */
  dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_HPDWARN);

  uint32_t dx_time = (uint32_t) (scheduled_time >> 8);
  dx_time &= 0xFFFFFFFEUL; /* DW1000 delayed-TX uses 9-bit overall quantization. */
  dwt_setdelayedtrxtime(dx_time);

  if (dwt_starttx(DWT_START_TX_DELAYED) != DWT_SUCCESS)
  {
    uint32_t status = dwt_read32bitreg(SYS_STATUS_ID);
    RLOG_E(LOG_OBJECT_CODE_UWB_DRIVER, ERR_UWB_TIMESTAMP,
           "[TX_DELAY] dwt_starttx delayed failed (status=0x%08lX, dx_time=0x%08lX)", (unsigned long) status,
           (unsigned long) dx_time);
    return BSP_ERR;
  }

  uint32_t status = dwt_read32bitreg(SYS_STATUS_ID);
  if (status & SYS_STATUS_HPDWARN) {
      RLOG_W(LOG_OBJECT_CODE_UWB_DRIVER, "[TX_DELAY] HPDWARN (Late TX) - Aborting");
      dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_HPDWARN);
      dwt_forcetrxoff();
      return BSP_ERR;
  }

  return BSP_OK;
}

bool bsp_uwb_is_rx_ready(void)
{
  if (!s_initialized)
    return false;

  uint32_t status = dwt_read32bitreg(SYS_STATUS_ID);

  /* Check for any event that stops RX: Good Frame, Error, or Timeout */
  uint32_t mask = SYS_STATUS_RXFCG | SYS_STATUS_RXRFTO | SYS_STATUS_RXPTO | SYS_STATUS_RXFCE
                  | SYS_STATUS_RXPHE | SYS_STATUS_RXRFSL;

  return (status & mask) != 0;
}

uint64_t bsp_uwb_get_current_time_dw(void)
{
  if (!s_initialized)
    return 0;

  uint8_t ts_buf[5];
  dwt_readsystime(ts_buf);  // Read SYS_TIME register

  return dw_read_timestamp(ts_buf) & DW_MASK_40;
}

bsp_err_t bsp_uwb_validate_delayed_tx(uint64_t tx_timestamp_dw, uint64_t min_guard_dw)
{
  if (!s_initialized)
    return BSP_ERR;

  /* Get current time */
  uint64_t current_time_dw = bsp_uwb_get_current_time_dw();

  /* Mask to 40 bits */
  tx_timestamp_dw &= DW_MASK_40;
  current_time_dw &= DW_MASK_40;

  /* TX must be in future */
  if (tx_timestamp_dw <= current_time_dw)
  {
    RLOG_W(LOG_OBJECT_CODE_UWB_DRIVER, "[TX_VALIDATE] TX time in past (now=" DW_FMT ", tx=" DW_FMT ")",
           DW_ARG(current_time_dw), DW_ARG(tx_timestamp_dw));
    return BSP_ERR;
  }

  /* Check minimum guard time */
  uint64_t time_diff = tx_timestamp_dw - current_time_dw;

  if (time_diff < min_guard_dw)
  {
    RLOG_W(LOG_OBJECT_CODE_UWB_DRIVER, "[TX_VALIDATE] Guard time too small (diff=%lu DW, min=%lu DW)",
           (uint32_t) time_diff, (uint32_t) min_guard_dw);
    return BSP_ERR;
  }

  return BSP_OK;
}

uint16_t bsp_uwb_get_rx_antenna_delay(void)
{
  return s_rx_antenna_delay;
}
uint16_t bsp_uwb_get_tx_antenna_delay(void)
{
  return s_tx_antenna_delay;
}
void bsp_uwb_on_irq(void)
{
  /* RTOS Hybrid Event-Driven: ISR only signals semaphore.
   * All SPI transactions (dwt_isr, read status/rx_data/tx_ts)
   * are performed in UwbRanging task context under SPI mutex. */
  if (g_uwb_isr_semHandle != NULL) {
    osSemaphoreRelease(g_uwb_isr_semHandle);
  }
}

void bsp_uwb_dwt_isr(void)
{
  /* Called from UwbRanging task under g_spi1_mutexHandle.
   * Processes all pending DW1000 interrupts via multi-pass loop. */
  const uint32_t useful_irq_mask = SYS_STATUS_TXFRS | SYS_STATUS_RXFCG;
  const uint32_t recovery_irq_mask = SYS_STATUS_RXRFTO | SYS_STATUS_RXPTO | SYS_STATUS_RXOVRR |
                                     SYS_STATUS_RXFCE | SYS_STATUS_RXPHE |
                                     SYS_STATUS_RXRFSL | SYS_STATUS_RXSFDTO;
  const uint32_t irq_mask = useful_irq_mask | recovery_irq_mask;
  for (uint8_t pass = 0; pass < 16U; pass++)
  {
    uint32_t status = dwt_read32bitreg(SYS_STATUS_ID);
    if ((status & irq_mask) == 0U)
    {
      break;
    }
    if (pass > 0U)
    {
      s_event_stats.irq_extra_pass++;
    }
    dwt_isr();
    status = dwt_read32bitreg(SYS_STATUS_ID);
    if ((status & irq_mask) == 0U)
    {
      break;
    }
  }
}

static void uwb_tx_cb(const dwt_callback_data_t *cb_data)
{
  (void)cb_data;

  uint8_t next_head = (uint8_t)((s_ev_head + 1u) % UWB_EVENT_QUEUE_SIZE);
  if (next_head == s_ev_tail) {
      /* Queue full — drop oldest by advancing tail, count overflow */
      s_event_stats.queue_overflow++;
      s_ev_tail = (uint8_t)((s_ev_tail + 1u) % UWB_EVENT_QUEUE_SIZE);
  }

  bsp_uwb_event_t *ev = &s_ev_queue[s_ev_head];
  ev->type   = BSP_UWB_EVENT_TX_DONE;
  ev->rx_len = 0;
  ev->rx_ts  = 0;
  memset(&ev->rx_quality, 0, sizeof(ev->rx_quality));

  uint8_t ts[5];
  dwt_readtxtimestamp(ts);
  ev->tx_ts = ((uint64_t)ts[0])        | ((uint64_t)ts[1] << 8)
            | ((uint64_t)ts[2] << 16)  | ((uint64_t)ts[3] << 24)
            | ((uint64_t)ts[4] << 32);
  s_last_tx_timestamp = ev->tx_ts;

  s_ev_head = next_head;
  s_event_stats.tx_done++;

  /* Re-arm receiver immediately after TX so the next incoming frame
   * (e.g. RESP after POLL, or anchor RESULT after FINAL TX) is not missed while
   * the foreground loop is still processing TX_DONE. */
  if (dwt_rxenable(DWT_START_RX_IMMEDIATE) != DWT_SUCCESS) {
      s_event_stats.rx_rearm_fail++;
  }
}

static void uwb_rx_cb(const dwt_callback_data_t *cb_data)
{
  if (cb_data->event == DWT_SIG_RX_OKAY) {
      uint8_t next_head = (uint8_t)((s_ev_head + 1u) % UWB_EVENT_QUEUE_SIZE);
      if (next_head == s_ev_tail) {
          /* Queue full — drop oldest, count overflow */
          s_event_stats.queue_overflow++;
          s_ev_tail = (uint8_t)((s_ev_tail + 1u) % UWB_EVENT_QUEUE_SIZE);
      }

      bsp_uwb_event_t *ev = &s_ev_queue[s_ev_head];
      ev->tx_ts = 0;
      ev->type    = BSP_UWB_EVENT_RX_OK;
      ev->rx_len  = cb_data->datalength;
      if (ev->rx_len > sizeof(ev->rx_data)) {
          ev->rx_len = sizeof(ev->rx_data);
      }
      dwt_readrxdata(ev->rx_data, ev->rx_len, 0);

      uint8_t ts[5];
      dwt_readrxtimestamp(ts);
      ev->rx_ts = ((uint64_t)ts[0])        | ((uint64_t)ts[1] << 8)
                | ((uint64_t)ts[2] << 16)  | ((uint64_t)ts[3] << 24)
                | ((uint64_t)ts[4] << 32);
      capture_rx_quality(&ev->rx_quality);
      s_last_rx_quality = ev->rx_quality;
      s_ev_head = next_head;
      s_event_stats.rx_ok++;

      /* Re-enable RX immediately to capture back-to-back frames */
      if (dwt_rxenable(DWT_START_RX_IMMEDIATE) != DWT_SUCCESS) {
          s_event_stats.rx_rearm_fail++;
      }
  } else {
      /* Do not queue timeouts or errors. They happen naturally during continuous
       * listening between slots and will rapidly overflow the queue, dropping
       * valid RX_OK/TX_DONE events. The TDMA state machine handles its own timeouts. */
      if (dwt_rxenable(DWT_START_RX_IMMEDIATE) != DWT_SUCCESS) {
          s_event_stats.rx_rearm_fail++;
      }
  }

}

void bsp_uwb_clear_irq_event(void)
{
  /* Kept for legacy blocking wait helpers; EXTI wakeup uses g_uwb_isr_semHandle. */
}

bool bsp_uwb_wait_for_irq_event(uint32_t timeout_ms)
{
  uint32_t start_tick = HAL_GetTick();

  while ((HAL_GetTick() - start_tick) < timeout_ms) {
    /* Fallback path: if EXTI edge was missed, poll DW1000 status bits. */
    if (bsp_uwb_is_rx_ready()) {
      return true;
    }
  }

  return false;
}
void bsp_uwb_get_rx_error_counts(uint32_t *timeout, uint32_t *crc_err,
                                  uint32_t *phr_err, uint32_t *sync_err)
{
  if (timeout)   *timeout   = s_rx_timeout_count;
  if (crc_err)   *crc_err   = s_rx_crc_err_count;
  if (phr_err)   *phr_err   = s_rx_phr_err_count;
  if (sync_err)  *sync_err  = s_rx_sync_err_count;
}

void bsp_uwb_reset_rx_error_counts(void)
{
  s_rx_timeout_count  = 0;
  s_rx_crc_err_count  = 0;
  s_rx_phr_err_count  = 0;
  s_rx_sync_err_count = 0;
}
bsp_err_t bsp_uwb_read_temp_vbat(float *temp, float *vbat)
{
    CHECK_PARAM(temp && vbat, BSP_ERR_PARAM);
    if (!s_initialized) return BSP_ERR;

    // Read raw values from the SAR ADC register of the DW1000 with fastSPI = 1
    uint16_t raw_val = dwt_readtempvbat(1);

    uint8_t raw_vbat = (uint8_t)(raw_val & 0xFF);
    uint8_t raw_temp = (uint8_t)((raw_val >> 8) & 0xFF);

    // Calculate real temperature and VBAT based on Decawave Datasheet formulas
    *vbat = (raw_vbat * 0.0057f) + 2.3f;
    *temp = (raw_temp * 1.13f) - 113.0f;

    return BSP_OK;
}

bool bsp_uwb_is_initialized(void)
{
    return s_initialized;
}

// float bsp_uwb_compensate_distance_error(float raw_dist_m, float temp_c, float vbat_v)
// {
//     const float REF_TEMP = 20.0f; 
//     const float REF_VBAT = 3.3f;

//     float temp_err_m = (temp_c - REF_TEMP) * 0.00215f;
    
//     float vbat_err_m = (REF_VBAT - vbat_v) * 0.0535f;

//     float compensated_dist = raw_dist_m - temp_err_m - vbat_err_m;
    
//     return compensated_dist;
// }
/* End of file -------------------------------------------------------- */
