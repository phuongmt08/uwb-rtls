/**
 * @file       bsp_uwb.c
 * @copyright
 * @license
 * @version    0.4.0
 * @date       2025-12-11
 * @author     Phuong Mai
 * @brief      Board Support Package for UWB (DW1000)
 * @note       None
 * @example    None
 */
/* Includes ----------------------------------------------------------- */
#include "bsp_uwb.h"
#include "bsp_util.h"
#include "err.h"
#include "spi.h"

/* DecaWave driver */
#include "deca_device_api.h"
#include "deca_regs.h"
#include "sys_logger.h"
#include <string.h>
#include <stdio.h>
#include <math.h>
/* Private defines ---------------------------------------------------- */
#define DW1000_DEVICE_ID 0xDECA0130UL
#define RX_TIMEOUT_MS    1000
#ifndef DWT_START_RX_IMMEDIATE
#define DWT_START_RX_IMMEDIATE 0
#endif
#define TX_MAX_PAYLOAD 120

#define DW1000_CRC_LENGTH 2

#define RX_TIME_ID 0x15
#define TX_TIME_ID 0x17

/* Private variables -------------------------------------------------- */
static bool s_initialized = false;
static uint64_t s_last_rx_timestamp = 0;  /* Cached RX timestamp */
static uint64_t s_last_tx_timestamp = 0;  /* Cached TX timestamp */
static uint16_t s_tx_antenna_delay = 0;   /* Cached TX antenna delay */
static volatile uint8_t s_irq_event_pending = 0;
/* Public variables --------------------------------------------------- */
extern SPI_HandleTypeDef hspi1;

/* Private function prototypes ---------------------------------------- */
static void reset_DW1000(void);
static void port_set_dw1000_slowrate(void);
static void port_set_dw1000_fastrate(void);

/* SPI implementation for deca_driver --------------------------------- */
static void dw1000_softreset(void)
{
    dwt_softreset();
    HAL_Delay(2);

    uint32_t status;
    uint32_t timeout = HAL_GetTick() + 10;

    do {
        status = dwt_read32bitreg(SYS_STATUS_ID);
        if (!(status & SYS_STATUS_CLKPLL_LL))
            break;
    } while (HAL_GetTick() < timeout);
}

static void print_hexdump(const char *tag, const uint8_t *buf, uint16_t len)
{
    const int BYTES_PER_LINE = 16;
    char line[128];
    int i, j;

    if (len == 0) {
        RLOG_D(LOG_OBJECT_CODE_RANGING, "%s: <zero length>", tag);
        return;
    }

    RLOG_D(LOG_OBJECT_CODE_RANGING, "%s: len=%u", tag, (unsigned) len);

    for (i = 0; i < len; i += BYTES_PER_LINE) {
        int n = snprintf(line, sizeof(line), "%04X: ", i);
        for (j = 0; j < BYTES_PER_LINE && (i + j) < len; ++j) {
            int wrote = snprintf(line + n, sizeof(line) - n, "%02X ", buf[i + j]);
            if (wrote < 0) break;
            n += wrote;
        }
        line[sizeof(line) - 1] = '\0';
        RLOG_D(LOG_OBJECT_CODE_RANGING, "%s", line);
    }
}

/**
 * Write to SPI - Single continuous transaction
 */
int writetospi(uint16 headerLength, const uint8 *headerBuffer,
               uint32 bodylength, const uint8 *bodyBuffer)
{
    HAL_StatusTypeDef status;

    HAL_GPIO_WritePin(UWB_CS_PORT, UWB_CS_PIN, GPIO_PIN_RESET);

    if (headerLength > 0) {
        status = HAL_SPI_Transmit(&hspi1, (uint8_t *)headerBuffer,
                                  headerLength, HAL_MAX_DELAY);
        if (status != HAL_OK) {
            HAL_GPIO_WritePin(UWB_CS_PORT, UWB_CS_PIN, GPIO_PIN_SET);
            return -1;
        }
    }

    if (bodylength > 0) {
        status = HAL_SPI_Transmit(&hspi1, (uint8_t *)bodyBuffer,
                                  bodylength, HAL_MAX_DELAY);
        if (status != HAL_OK) {
            HAL_GPIO_WritePin(UWB_CS_PORT, UWB_CS_PIN, GPIO_PIN_SET);
            return -1;
        }
    }

    HAL_GPIO_WritePin(UWB_CS_PORT, UWB_CS_PIN, GPIO_PIN_SET);
    return 0;
}

int readfromspi(uint16 headerLength, const uint8 *headerBuffer,
                uint32 readlength, uint8 *readBuffer)
{
    HAL_StatusTypeDef status;

    HAL_GPIO_WritePin(UWB_CS_PORT, UWB_CS_PIN, GPIO_PIN_RESET);

    if (headerLength > 0) {
        status = HAL_SPI_Transmit(&hspi1, (uint8_t *)headerBuffer,
                                  headerLength, HAL_MAX_DELAY);
        if (status != HAL_OK) {
            HAL_GPIO_WritePin(UWB_CS_PORT, UWB_CS_PIN, GPIO_PIN_SET);
            return -1;
        }
    }

    if (readlength > 0) {
        status = HAL_SPI_Receive(&hspi1, readBuffer,
                                 readlength, HAL_MAX_DELAY);
        if (status != HAL_OK) {
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
    hspi1.Init.BaudRatePrescaler = SPI_BAUDRATEPRESCALER_128;
    HAL_SPI_Init(&hspi1);
    RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "[SPI] Set to SLOW rate (prescaler=128)");
}

static void port_set_dw1000_fastrate(void)
{
    hspi1.Init.BaudRatePrescaler = SPI_BAUDRATEPRESCALER_8;
    HAL_SPI_Init(&hspi1);
    RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "[SPI] Set to FAST rate (prescaler=8, ~5.25MHz)");
}

static uint16_t ms_to_dw1000_rxtimeout_units(uint32_t timeout_ms)
{
    /* 1 unit = ~1.0256 μs => units = (ms * 1000000) / 10256 */
    uint32_t units = (timeout_ms * 1000u * 1000u) / 10256u;
    if (units > 0xFFFFu) {
        units = 0xFFFFu;  /* Max 16-bit value */
    }
    return (uint16_t)units;
}

/* Public functions --------------------------------------------------- */

bsp_err_t bsp_uwb_init(void)
{
    uint32_t dev_id;

    bsp_util_init();
    reset_DW1000();
    port_set_dw1000_slowrate();

    /* Load LDE microcode - CRITICAL for accurate RX timestamps */
    if (dwt_initialise(DWT_LOADUCODE) != DWT_SUCCESS) {
        return BSP_ERR;
    }

        dev_id = dwt_readdevid();
    if (dev_id != DW1000_DEVICE_ID) {
        return BSP_ERR;
    }

    port_set_dw1000_fastrate();
    dwt_setleds(1);

    s_initialized = true;
    return BSP_OK;
}

bsp_err_t bsp_uwb_configure(const bsp_uwb_config_t *cfg)
{
    CHECK_PARAM(cfg != NULL, BSP_ERR_PARAM);
    CHECK_PARAM(s_initialized, BSP_ERR);

    dwt_config_t dw_cfg = {
        .chan           = cfg->channel,
        .prf            = (cfg->prf == 64) ? DWT_PRF_64M : DWT_PRF_16M,
        .txPreambLength = DWT_PLEN_1024,
        .rxPAC          = DWT_PAC32,
        .txCode         = 9,
        .rxCode         = 9,
        .nsSFD          = 0,
        .dataRate       = cfg->data_rate,
        .phrMode        = DWT_PHRMODE_STD,
        .sfdTO          = (1024 + 64)   
    };
    
    RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "[BSP][CFG] CH=%u PRF=%uMHz DR=%u PCode=%u",
           dw_cfg.chan, cfg->prf, dw_cfg.dataRate, dw_cfg.txCode);
    RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "[BSP][CFG] PLEN=256 PAC=16 SFD=%u nsSFD=%u PHR=%u",
           dw_cfg.sfdTO, dw_cfg.nsSFD, dw_cfg.phrMode);

    if (dwt_configure(&dw_cfg, DWT_LOADNONE) != DWT_SUCCESS) {
        RLOG_W(LOG_OBJECT_CODE_UWB_DRIVER, "[BSP][CFG] dwt_configure() failed");
        return BSP_ERR;
    }

    dwt_txconfig_t tx_cfg;
    tx_cfg.power = cfg->tx_power;
    tx_cfg.PGdly = 0xC2;
    dwt_configuretxrf(&tx_cfg);

    dwt_setrxantennadelay(cfg->rx_antenna_delay);
    dwt_settxantennadelay(cfg->tx_antenna_delay);
    
    /* Cache TX antenna delay for later use in delayed TX calculations */
    s_tx_antenna_delay = cfg->tx_antenna_delay;

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

    dwt_writetxdata(length, (uint8_t*)data, 0);  /* Write payload only */
    dwt_writetxfctrl(length + DW1000_CRC_LENGTH, 0);  /* Total on-air length including CRC */

    /* Start TX immediately */
    if (dwt_starttx(DWT_START_TX_IMMEDIATE) != DWT_SUCCESS) {
        RLOG_W(LOG_OBJECT_CODE_UWB_DRIVER, "[TX] dwt_starttx() failed");
        return BSP_ERR;
    }

    /* Wait TX complete */
    uint32_t timeout = HAL_GetTick() + 10;
    uint32_t status = 0;

    while (!(status & SYS_STATUS_TXFRS)) {
        status = dwt_read32bitreg(SYS_STATUS_ID);

        if (status & SYS_STATUS_CLKPLL_LL) {
            RLOG_W(LOG_OBJECT_CODE_UWB_DRIVER, "[TX] PLL lock lost");
            return BSP_ERR;
        }

        if (HAL_GetTick() > timeout) {
            RLOG_W(LOG_OBJECT_CODE_UWB_DRIVER, "[TX] Timeout waiting for TXFRS");
            return BSP_ERR;
        }
    }

    /* Cache TX timestamp immediately using DecaWave API */
    uint8_t ts_buf[5];
    dwt_readtxtimestamp(ts_buf);
    s_last_tx_timestamp = ((uint64_t)ts_buf[0]) |
                          ((uint64_t)ts_buf[1] << 8) |
                          ((uint64_t)ts_buf[2] << 16) |
                          ((uint64_t)ts_buf[3] << 24) |
                          ((uint64_t)ts_buf[4] << 32);

    /* TX success */
    dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_TXFRS);

    return BSP_OK;
}

bsp_err_t bsp_uwb_rx(void *data, uint16_t length, uint16_t *out_len)
{
    CHECK_PARAM(data && out_len, BSP_ERR_PARAM);
    CHECK_PARAM(s_initialized, BSP_ERR);

    uint8_t rx_buf[256];
    uint32_t status = dwt_read32bitreg(SYS_STATUS_ID);

    /* Debug status flags */
    // if (status & SYS_STATUS_RXFCG)
    //     RLOG_D(LOG_OBJECT_CODE_UWB_DRIVER, "  RXFCG: Good frame received");
    if (status & SYS_STATUS_RXFCE)
        RLOG_D(LOG_OBJECT_CODE_UWB_DRIVER, "  RXFCE: CRC failed");
    if (status & SYS_STATUS_RXPHE)
        RLOG_D(LOG_OBJECT_CODE_UWB_DRIVER, "  RXPHE: Preamble/PHR error");
    if (status & SYS_STATUS_CLKPLL_LL)
        RLOG_W(LOG_OBJECT_CODE_UWB_DRIVER, "  CLKPLL_LL: PLL losing lock");

    /* Good frame received */
    if (status & SYS_STATUS_RXFCG) {
        /* Wait for LDE processing done before reading RX timestamp */
        uint32_t lde_timeout = HAL_GetTick() + 5;
        while (!(dwt_read32bitreg(SYS_STATUS_ID) & SYS_STATUS_LDEDONE)) {
            if (HAL_GetTick() > lde_timeout) {
                RLOG_W(LOG_OBJECT_CODE_UWB_DRIVER, "[RX] LDE timeout");
                break;
            }
        }
        /* Read RX timestamp after LDE done */
        uint8_t ts_buf[5];
        dwt_readrxtimestamp(ts_buf);
        s_last_rx_timestamp = ((uint64_t)ts_buf[0]) |
                              ((uint64_t)ts_buf[1] << 8) |
                              ((uint64_t)ts_buf[2] << 16) |
                              ((uint64_t)ts_buf[3] << 24) |
                              ((uint64_t)ts_buf[4] << 32);
        uint32_t rxfi = dwt_read32bitreg(RX_FINFO_ID);
        uint16_t frame_len_onair = (uint16_t)(rxfi & 0x03FF);  /* On-air length WITH CRC */
        if (frame_len_onair > sizeof(rx_buf)) {
            frame_len_onair = sizeof(rx_buf);
        }
        /* Read full frame including CRC */
        dwt_readrxdata(rx_buf, frame_len_onair, 0);
        uint16_t payload_len = 0;
        if (frame_len_onair >= DW1000_CRC_LENGTH) {
            payload_len = frame_len_onair - DW1000_CRC_LENGTH;
        }
        /* Copy payload to user buffer */
        uint16_t copy_len = (payload_len < length) ? payload_len : length;
        if (copy_len > 0) {
            memcpy((uint8_t *)data, rx_buf, copy_len);
        }
        *out_len = payload_len;
        /* Clear RX flags */
        dwt_write32bitreg(SYS_STATUS_ID, 
                          SYS_STATUS_RXFCG | SYS_STATUS_RXDFR | 
                          SYS_STATUS_RXPRD | SYS_STATUS_RXSFDD | 
                          SYS_STATUS_RXPHD | SYS_STATUS_LDEDONE);
        return BSP_OK;
    }

    /* RX timeout (RXRFTO/RXPTO): only re-enable RX, do not reset */
    if (status & (SYS_STATUS_RXRFTO | SYS_STATUS_RXPTO)) {
        dwt_write32bitreg(SYS_STATUS_ID,
                          SYS_STATUS_RXRFTO | SYS_STATUS_RXPTO |
                          SYS_STATUS_RXDFR  | SYS_STATUS_RXPRD |
                          SYS_STATUS_RXSFDD | SYS_STATUS_RXPHD |
                          SYS_STATUS_LDEDONE);
        dwt_rxenable(DWT_START_RX_IMMEDIATE);
        s_last_rx_timestamp = 0; // Invalidate cached RX timestamp on timeout
        *out_len = 0;
        return BSP_ERR_TIMEOUT;
    }

    /* RX frame errors (CRC, PHR, RFSL): force reset and re-enable RX */
    if (status & (SYS_STATUS_RXFCE | SYS_STATUS_RXPHE | SYS_STATUS_RXRFSL)) {
        dwt_write32bitreg(SYS_STATUS_ID, 
                          SYS_STATUS_RXFCE | SYS_STATUS_RXPHE | SYS_STATUS_RXRFSL |
                          SYS_STATUS_RXDFR | SYS_STATUS_RXSFDD | SYS_STATUS_RXPRD |
                          SYS_STATUS_LDEDONE);
        dwt_forcetrxoff();
        dwt_rxreset();     
        dwt_rxenable(DWT_START_RX_IMMEDIATE);
        s_last_rx_timestamp = 0; // Invalidate cached RX timestamp on RX error
        *out_len = 0;
        return BSP_ERR;
    }

    /* No event yet - RX still active */
    return BSP_ERR;
}

bsp_err_t bsp_uwb_read_40bit(uint8_t reg_addr, uint8_t sub_addr, uint64_t *timestamp)
{
    CHECK_PARAM(timestamp, BSP_ERR_PARAM);
    CHECK_PARAM(s_initialized, BSP_ERR);

    /* Only allow reading RX timestamp if valid (last RX was good) */
    if (reg_addr == RX_TIME_ID && sub_addr == 0) {
        if (s_last_rx_timestamp == 0) {
            return BSP_ERR; // No valid RX timestamp available
        }
        *timestamp = s_last_rx_timestamp;
    } else if (reg_addr == TX_TIME_ID && sub_addr == 0) {
        *timestamp = s_last_tx_timestamp;
    } else {
        /* Fallback for other registers */
        uint8_t buf[5];
        dwt_readfromdevice(reg_addr, sub_addr, 5, buf);
        *timestamp = ((uint64_t)buf[0]) | 
                     ((uint64_t)buf[1] << 8) | 
                     ((uint64_t)buf[2] << 16) |
                     ((uint64_t)buf[3] << 24) | 
                     ((uint64_t)buf[4] << 32);
    }

    return BSP_OK;
}

void bsp_uwb_reset(bool active)
{
    if (active) {
        HAL_GPIO_WritePin(UWB_RST_PORT, UWB_RST_PIN, GPIO_PIN_RESET);
    } else {
        HAL_GPIO_WritePin(UWB_RST_PORT, UWB_RST_PIN, GPIO_PIN_SET);
    }
}
bsp_err_t bsp_uwb_enable_rx(uint32_t timeout_ms)
{
    CHECK_PARAM(s_initialized, BSP_ERR);
    
    /* CRITICAL: Force idle state first to ensure clean RX start */
    dwt_forcetrxoff();
    
    /* Clear ALL status flags - both TX and RX */
    dwt_write32bitreg(SYS_STATUS_ID, 0xFFFFFFFFUL);
    
    /* Set RX timeout */
    if (timeout_ms > 0 && timeout_ms <= 67) {
        uint16_t timeout_units = ms_to_dw1000_rxtimeout_units(timeout_ms);
        dwt_setrxtimeout(timeout_units);
    } else {
        dwt_setrxtimeout(0);  /* 0 = continuous RX mode */
    }
    
    /* Enable RX */
    if (dwt_rxenable(DWT_START_RX_IMMEDIATE) != DWT_SUCCESS) {
        RLOG_W(LOG_OBJECT_CODE_UWB_DRIVER, "[RX] dwt_rxenable failed");
        return BSP_ERR;
    }

    s_irq_event_pending = 0;
    
    return BSP_OK;
}

void bsp_uwb_idle(void)
{
  /* Force RX/TX off */
  dwt_forcetrxoff();
  
  /* Clear all status flags to prevent stale error flags */
  dwt_write32bitreg(SYS_STATUS_ID, 0xFFFFFFFFUL);
}

int8_t bsp_uwb_get_rssi(void)
{
    /* Read receive power diagnostics from DW1000 using proper API
     * Based on DW1000 User Manual section 4.7.1 & 4.7.2
     */
    dwt_rxdiag_t diag;
    dwt_readdignostics(&diag);
    
    /* Calculate Receive Signal Power using DW1000 formula:
     * RSL (dBm) = 10*log10((CIR_PWR * 2^17) / N^2) - A
     * Where:
     *   CIR_PWR = firstPathAmp1^2 (Channel Impulse Response Power at first path)
     *   N = rxPreamCount (number of preamble symbols accumulated)
     *   A = 113.77 dB for PRF64, 121.74 dB for PRF16
     * 
     * Simplified for PRF64 (typical configuration):
     * RSL ≈ 10*log10(F1^2 / N^2) + 10*log10(2^17) - 113.77
     * RSL ≈ 20*log10(F1/N) + 51.35 - 113.77
     * RSL ≈ 20*log10(F1/N) - 62.42
     */
    
    if (diag.firstPathAmp1 == 0 || diag.rxPreamCount == 0) {
        return -100;  /* Invalid/weak signal */
    }
    
    /* Calculate ratio F1/N (avoid float division in embedded) */
    float ratio = (float)diag.firstPathAmp1 / (float)diag.rxPreamCount;
    
    /* Calculate 20*log10(ratio) using approximation or math library */
    float log_ratio = 20.0f * log10f(ratio);
    
    /* Apply DW1000 formula for PRF64 */
    int8_t rssi_dbm = (int8_t)(log_ratio - 62.0f);
    
    // /* Clamp to realistic UWB range */
    // if (rssi_dbm > -30) rssi_dbm = -30;   /* Very strong signal */
    // if (rssi_dbm < -100) rssi_dbm = -100; /* Very weak signal */
    
    return rssi_dbm;
}

uint16_t bsp_uwb_get_tx_antenna_delay(void)
{
    return s_tx_antenna_delay;
}

bsp_err_t bsp_uwb_tx_delayed(const void *data, uint16_t length, uint64_t tx_timestamp)
{
    CHECK_PARAM(s_initialized, BSP_ERR);
    CHECK_PARAM(data != NULL, BSP_ERR);
    CHECK_PARAM(length > 0 && length <= TX_MAX_PAYLOAD, BSP_ERR);

    /* Ensure idle and clear all flags */
    dwt_forcetrxoff();
    dwt_write32bitreg(SYS_STATUS_ID, 0xFFFFFFFFUL);

    /* Write frame data to TX buffer */
    dwt_writetxdata(length, (uint8_t *)data, 0);
    dwt_writetxfctrl(length + DW1000_CRC_LENGTH, 0);  /* Include CRC in on-air length */

    /* Set delayed transmission time
     * DW1000 uses upper 32 bits of 40-bit timestamp for delayed TX
     * Shift right by 8 bits to get the value for DX_TIME register
     * Must subtract antenna delay for correct scheduling
     * Add guard: scheduled_time must be sufficiently in the future
     */
    if (tx_timestamp < s_tx_antenna_delay) {
        RLOG_W(LOG_OBJECT_CODE_UWB_DRIVER, "[TX_DELAYED] tx_timestamp < antenna_delay, cannot schedule");
        return BSP_ERR;
    }
    uint64_t scheduled_time = tx_timestamp - s_tx_antenna_delay;
    // Guard: scheduled_time must be at least MIN_GUARD ticks in the future
    const uint32_t MIN_GUARD = 1024; // ~10us (1 tick = ~10ns)
    uint64_t now;
    dwt_readtxtimestamp((uint8_t*)&now); // Read current TX timestamp (raw)
    now &= 0xFFFFFFFFFFULL; // Mask to 40 bits
    if (scheduled_time <= now || (scheduled_time - now) < MIN_GUARD) {
        RLOG_W(LOG_OBJECT_CODE_UWB_DRIVER, "[TX_DELAYED] scheduled_time too close or in the past (now=0x%llx, sched=0x%llx)", now, scheduled_time);
        return BSP_ERR;
    }
    uint32_t dx_time = (uint32_t)(scheduled_time >> 8);
    dwt_setdelayedtrxtime(dx_time);

    /* Start delayed transmission (NO auto-RX enable) */
    int ret = dwt_starttx(DWT_START_TX_DELAYED);
    if (ret != DWT_SUCCESS) {
        RLOG_W(LOG_OBJECT_CODE_UWB_DRIVER, "[TX_DELAYED] dwt_starttx failed");
        return BSP_ERR;
    }

    /* Wait for TX complete */
    uint32_t timeout_ms = 100;
    uint32_t start = HAL_GetTick();

    while ((HAL_GetTick() - start) < timeout_ms) {
        uint32_t status = dwt_read32bitreg(SYS_STATUS_ID);
        
        if (status & SYS_STATUS_TXFRS) {
            /* Cache TX timestamp immediately */
            uint8_t ts_buf[5];
            dwt_readtxtimestamp(ts_buf);
            s_last_tx_timestamp = ((uint64_t)ts_buf[0]) |
                                  ((uint64_t)ts_buf[1] << 8) |
                                  ((uint64_t)ts_buf[2] << 16) |
                                  ((uint64_t)ts_buf[3] << 24) |
                                  ((uint64_t)ts_buf[4] << 32);
            
            /* TX complete - clear flag */
            dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_TXFRS);
            
            return BSP_OK;
        }
        
        /* Check for delayed TX error (too late) */
        if (status & SYS_STATUS_HPDWARN) {
            RLOG_W(LOG_OBJECT_CODE_UWB_DRIVER, "[TX_DELAYED] Half period warning - timing too tight");
            dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_HPDWARN);
            dwt_forcetrxoff();
            return BSP_ERR;
        }
    }
    
    /* Timeout */
    RLOG_E(LOG_OBJECT_CODE_UWB_DRIVER, ERR_UWB_TX, "[TX_DELAYED] Timeout waiting for TXFRS");
    dwt_forcetrxoff();
    return BSP_ERR;
}
bool bsp_uwb_is_rx_ready(void)
{
    if (!s_initialized) {
        return false;
    }

    uint32_t status = dwt_read32bitreg(SYS_STATUS_ID);
    
    /*
     * RXFCG: Receiver Functional Control Good
     * RXRFTO: Receiver RF Timeout
     * RXPTO: Preamble Detection Timeout
     * RXFCE: Receiver Frame Check Error
     * RXPHE: Receiver PHR Error
     * RXRFSL: Receiver Reed Solomon Error/Sync Loss
     */
    uint32_t mask = SYS_STATUS_RXFCG | 
                    SYS_STATUS_RXRFTO | SYS_STATUS_RXPTO | 
                    SYS_STATUS_RXFCE | SYS_STATUS_RXPHE | SYS_STATUS_RXRFSL;

    return (status & mask) != 0;
}

void bsp_uwb_on_irq(void)
{
    s_irq_event_pending = 1;
}

void bsp_uwb_clear_irq_event(void)
{
    s_irq_event_pending = 0;
}

bool bsp_uwb_wait_for_irq_event(uint32_t timeout_ms)
{
    uint32_t start_tick = HAL_GetTick();

    while ((HAL_GetTick() - start_tick) < timeout_ms) {
        if (s_irq_event_pending) {
            s_irq_event_pending = 0;
            return true;
        }
    }

    return false;
}
/* End of file -------------------------------------------------------- */
