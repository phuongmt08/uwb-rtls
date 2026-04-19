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

#include <math.h>
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
static int8_t   s_last_rx_rssi      = -100; /* Cached RSSI for last good RX frame */
static uint16_t s_last_diag_f1      = 0;    /* Raw diagnostics for deferred RSSI calc */
static uint16_t s_last_diag_n       = 0;
static uint16_t s_tx_antenna_delay  = 0;    /* Cached TX antenna delay */
static uint16_t s_rx_antenna_delay  = 0;    /* Cached RX antenna delay */

static volatile uint8_t s_irq_event_pending = 0;

/* RX error counters — incremented in bsp_uwb_rx(), read via bsp_uwb_get_rx_error_counts(). */
static uint32_t s_rx_timeout_count  = 0;
static uint32_t s_rx_crc_err_count  = 0;
static uint32_t s_rx_phr_err_count  = 0;
static uint32_t s_rx_sync_err_count = 0;

#if UWB_EVENT_DRIVEN
static volatile bool s_isr_event_ready = false;
static volatile uint8_t s_event_overflow_count = 0;
static bsp_uwb_event_t s_isr_event;
static void uwb_tx_cb(const dwt_callback_data_t *cb_data);
static void uwb_rx_cb(const dwt_callback_data_t *cb_data);

bool bsp_uwb_get_event(bsp_uwb_event_t *out_event)
{
    if (!s_isr_event_ready) return false;
    __disable_irq();
    *out_event = s_isr_event;
    s_isr_event_ready = false;
    __enable_irq();
    return true;
}

void bsp_uwb_clear_event(void)
{
    __disable_irq();
    s_isr_event_ready = false;
    __enable_irq();
}
#endif

/* Public variables --------------------------------------------------- */
extern SPI_HandleTypeDef hspi1;

/* Private function prototypes ---------------------------------------- */
static void     reset_DW1000(void);
static void     port_set_dw1000_slowrate(void);
static void     port_set_dw1000_fastrate(void);
static uint16_t ms_to_dw1000_rxtimeout_units(uint32_t timeout_ms);

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
  HAL_Delay(2);
  HAL_GPIO_WritePin(UWB_RST_PORT, UWB_RST_PIN, GPIO_PIN_SET);
  HAL_Delay(2);
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

#if UWB_EVENT_DRIVEN
  /* Register UWB callbacks for foreground event processing */
  dwt_setcallbacks(uwb_tx_cb, uwb_rx_cb);
  /* Enable interrupts for TX done, RX good, RX timeout, RX preamble timeout, RX overflow, RX frame check error, SFD detection, RX preamble header error */
  dwt_setinterrupt(DWT_INT_TFRS | DWT_INT_RFCG | DWT_INT_RFTO | DWT_INT_RXPTO |
                   DWT_INT_RXOVRR | DWT_INT_RFCE | DWT_INT_SFDT | DWT_INT_RPHE, 1);
#endif

  s_initialized = true;
  return BSP_OK;
}

bsp_err_t bsp_uwb_configure(const bsp_uwb_config_t *cfg)
{
  CHECK_PARAM(cfg != NULL, BSP_ERR_PARAM);
  CHECK_PARAM(s_initialized, BSP_ERR);

  dwt_config_t dw_cfg = { .chan           = cfg->channel,
                          .prf            = (cfg->prf == 64) ? DWT_PRF_64M : DWT_PRF_16M,
                          .txPreambLength = DWT_PLEN_1024,
                          .rxPAC          = DWT_PAC32,
                          .txCode         = 9,
                          .rxCode         = 9,
                          .nsSFD          = 0,
                          .dataRate       = cfg->data_rate,
                          .phrMode        = DWT_PHRMODE_STD,
                          .sfdTO          = (1024 + 64) };

  if (dwt_configure(&dw_cfg, DWT_LOADNONE) != DWT_SUCCESS)
  {
    return BSP_ERR;
  }

  dwt_txconfig_t tx_cfg;
  tx_cfg.power = cfg->tx_power;
  tx_cfg.PGdly = 0xC2;
  dwt_configuretxrf(&tx_cfg);

  dwt_setrxantennadelay(cfg->rx_antenna_delay);
  dwt_settxantennadelay(cfg->tx_antenna_delay);

  s_tx_antenna_delay = cfg->tx_antenna_delay;
  s_rx_antenna_delay = cfg->rx_antenna_delay;
  /* Start fresh */

  /* Enable DW1000 IRQ sources used by RX path (required for IRQ pin assertion). */
  dwt_setinterrupt(
    (uint32) (DWT_INT_RFCG | DWT_INT_RFTO | DWT_INT_RXPTO | DWT_INT_RFCE | DWT_INT_RPHE | DWT_INT_RFSL), 1);

  dwt_write32bitreg(SYS_STATUS_ID, 0xFFFFFFFFUL);
  dwt_forcetrxoff();
  s_irq_event_pending = 0;

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

#if UWB_EVENT_DRIVEN
  return BSP_OK;
#else
  /* Wait TX complete (Blocking) */
  /* NOTE: Consider using interrupts or OS semaphores in future */
  uint32_t timeout = HAL_GetTick() + 10;
  uint32_t status  = 0;

  while (!(status & SYS_STATUS_TXFRS))
  {
    status = dwt_read32bitreg(SYS_STATUS_ID);

    if (status & SYS_STATUS_CLKPLL_LL)
    {
      return BSP_ERR;
    }

    if (HAL_GetTick() > timeout)
    {
      return BSP_ERR;
    }
  }

  /* Cache TX timestamp */
  uint8_t ts_buf[5];
  dwt_readtxtimestamp(ts_buf);
  s_last_tx_timestamp = dw_read_timestamp(ts_buf);

  dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_TXFRS);

  return BSP_OK;
#endif
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

    /* Capture RSSI immediately while diagnostics still match this frame. */
    {
      dwt_rxdiag_t diag;
      dwt_readdignostics(&diag);
      if (diag.firstPathAmp1 == 0 || diag.rxPreamCount == 0)
      {
        s_last_rx_rssi = -100;
      }
      else
      {
        float ratio    = (float) diag.firstPathAmp1 / (float) diag.rxPreamCount;
        float log_val  = 20.0f * log10f(ratio);
        s_last_rx_rssi = (int8_t) (log_val - 62.0f);
      }
    }

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
    s_last_rx_rssi      = -100;
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
    s_last_rx_rssi      = -100;
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

  /* Clear SW IRQ latch before enabling RX to avoid stale event. */
  s_irq_event_pending = 0;

  /* Enable RX */
  if (dwt_rxenable(DWT_START_RX_IMMEDIATE) != DWT_SUCCESS)
  {
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

  /* Clear SW IRQ latch before enabling RX to avoid stale event. */
  s_irq_event_pending = 0;

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

int8_t bsp_uwb_get_rssi(void)
{
  if (s_last_diag_f1 == 0 || s_last_diag_n == 0)
  {
    return -100;
  }

  /* RSSI Formula for PRF64 (from DW1000 User Manual):
   * RSL ≈ 20*log10(F1/N) - 62.42
   * Move heavy log10f calculation here, out of the interrupt/poll RX path. */
  float  ratio   = (float) s_last_diag_f1 / (float) s_last_diag_n;
  float  log_val = 20.0f * log10f(ratio);
  int8_t rssi    = (int8_t) (log_val - 62.0f);

  /* Cache the result for bsp_uwb_get_last_rx_rssi() */
  s_last_rx_rssi = rssi;
  return rssi;
}

int8_t bsp_uwb_get_last_rx_rssi(void)
{
  return s_last_rx_rssi;
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

#if UWB_EVENT_DRIVEN
  return BSP_OK;
#else
  /* Wait for TX complete */
  uint32_t timeout_ms = 100;
  uint32_t start      = HAL_GetTick();
  uint32_t status     = 0;

  while ((HAL_GetTick() - start) < timeout_ms)
  {
    status = dwt_read32bitreg(SYS_STATUS_ID);

    if (status & SYS_STATUS_TXFRS)
    {
      uint8_t ts_buf[5];
      dwt_readtxtimestamp(ts_buf);
      s_last_tx_timestamp = dw_read_timestamp(ts_buf);

      dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_TXFRS);
      return BSP_OK;
    }

    /* HPDWARN: Half Period Warning - Chip rejected time because it's too late */
    if (status & SYS_STATUS_HPDWARN)
    {
      RLOG_W(LOG_OBJECT_CODE_UWB_DRIVER, "[TX_DELAY] HPDWARN (Late TX)");
      dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_HPDWARN);
      dwt_forcetrxoff();
      return BSP_ERR;
    }
  }

  RLOG_E(LOG_OBJECT_CODE_UWB_DRIVER, ERR_UWB_TIMESTAMP, "[TX_DELAY] TXFRS timeout (status=0x%08lX)",
         (unsigned long) status);

  dwt_forcetrxoff();
  return BSP_ERR;
#endif
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
#if UWB_EVENT_DRIVEN
  dwt_isr();
#else
  s_irq_event_pending = 1;
#endif
}

#if UWB_EVENT_DRIVEN
static void uwb_tx_cb(const dwt_callback_data_t *cb_data)
{
  (void)cb_data;
  if (s_isr_event_ready) s_event_overflow_count++;
  s_isr_event.type = BSP_UWB_EVENT_TX_DONE;
  s_isr_event.rx_len = 0;
  
  uint64_t actual_dw = 0;
  uint8_t ts[5];
  dwt_readtxtimestamp(ts);
  actual_dw = ((uint64_t)ts[0]) | ((uint64_t)ts[1] << 8) | ((uint64_t)ts[2] << 16)
         | ((uint64_t)ts[3] << 24) | ((uint64_t)ts[4] << 32);
  s_isr_event.tx_ts = actual_dw;
  
  s_isr_event_ready = true;
}

static void uwb_rx_cb(const dwt_callback_data_t *cb_data)
{
  if (cb_data->event == DWT_SIG_RX_OKAY)
  {
      if (s_isr_event_ready) s_event_overflow_count++;
      s_isr_event.type = BSP_UWB_EVENT_RX_OK;
      s_isr_event.rx_len = cb_data->datalength;
      if (s_isr_event.rx_len > sizeof(s_isr_event.rx_data)) {
          s_isr_event.rx_len = sizeof(s_isr_event.rx_data);
      }
      dwt_readrxdata(s_isr_event.rx_data, s_isr_event.rx_len, 0);
      
      uint8_t ts[5];
      dwt_readrxtimestamp(ts);
      s_isr_event.rx_ts = ((uint64_t)ts[0]) | ((uint64_t)ts[1] << 8) | ((uint64_t)ts[2] << 16)
             | ((uint64_t)ts[3] << 24) | ((uint64_t)ts[4] << 32);
      
      // We skip RSSI read in ISR to save time, main loop can approximate or we just use -100
      s_isr_event.rx_rssi = -100;
  }
  else if (cb_data->event == DWT_SIG_RX_TIMEOUT || cb_data->event == DWT_SIG_RX_PTOTIMEOUT)
  {
      s_isr_event.type = BSP_UWB_EVENT_RX_TIMEOUT;
      s_isr_event.rx_len = 0;
  }
  else
  {
      s_isr_event.type = BSP_UWB_EVENT_RX_ERROR;
      s_isr_event.rx_len = 0;
  }
  
  s_isr_event_ready = true;
}
#endif

void bsp_uwb_clear_irq_event(void)
{
  s_irq_event_pending = 0;
}

bool bsp_uwb_wait_for_irq_event(uint32_t timeout_ms)
{
  uint32_t start_tick = HAL_GetTick();

  while ((HAL_GetTick() - start_tick) < timeout_ms)
  {
    if (s_irq_event_pending)
    {
      s_irq_event_pending = 0;
      return true;
    }

    /* Fallback path: if EXTI edge was missed, poll DW1000 status bits. */
    if (bsp_uwb_is_rx_ready())
    {
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

/* End of file -------------------------------------------------------- */