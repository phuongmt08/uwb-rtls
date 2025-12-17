/**
 * @file       bsp_uwb.c
 * @brief      BSP layer for DW1000 - FIXED CRC handling
 * @version    0.4.0
 * @date       2025-12-11
 */

/* Includes ----------------------------------------------------------- */
#include "bsp_uwb.h"
#include "bsp_util.h"
#include "err.h"
#include "spi.h"

/* DecaWave driver */
#include "../deca/deca_driver/deca_device_api.h"
#include "../deca/deca_driver/deca_regs.h"
#include "sys_logger.h"
#include <string.h>
#include <stdio.h>
/* Private defines ---------------------------------------------------- */
#define DW1000_DEVICE_ID 0xDECA0130UL
#define RX_TIMEOUT_MS    1000
#ifndef DWT_START_RX_IMMEDIATE
#define DWT_START_RX_IMMEDIATE 0
#endif
#define TX_MAX_PAYLOAD 120

/* DW1000 automatically appends 2-byte CRC */
#define DW1000_CRC_LENGTH 2

/* DW1000 timestamp registers */
#define RX_TIME_ID 0x15
#define TX_TIME_ID 0x17

/* Private variables -------------------------------------------------- */
static bool s_initialized = false;
static uint64_t s_last_rx_timestamp = 0;  /* Cached RX timestamp */
static uint64_t s_last_tx_timestamp = 0;  /* Cached TX timestamp */
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
    hspi1.Init.BaudRatePrescaler = SPI_BAUDRATEPRESCALER_16;
    HAL_SPI_Init(&hspi1);
    RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "[SPI] Set to FAST rate (prescaler=16)");
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

    RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "[BSP][CFG] Configuring: CH=%u PRF=%u DataRate=%u", 
           cfg->channel, cfg->prf, cfg->data_rate);

    dwt_config_t dw_cfg = {
        .chan           = cfg->channel,
        .prf            = (cfg->prf == 64) ? DWT_PRF_64M : DWT_PRF_16M,
        .txPreambLength = DWT_PLEN_128,
        .rxPAC          = DWT_PAC8,
        .txCode         = 9,
        .rxCode         = 9,
        .nsSFD          = 0,
        .dataRate       = cfg->data_rate,
        .phrMode        = DWT_PHRMODE_STD,
        .sfdTO          = 129
    };

    if (dwt_configure(&dw_cfg, DWT_LOADNONE) != DWT_SUCCESS) {
        RLOG_W(LOG_OBJECT_CODE_UWB_DRIVER, "[BSP][CFG] dwt_configure() failed");
        return BSP_ERR;
    }

    dwt_txconfig_t tx_cfg;
    tx_cfg.power = 0x0E082848UL;
    tx_cfg.PGdly = 0xC0;
    dwt_configuretxrf(&tx_cfg);

    dwt_setrxantennadelay(cfg->rx_antenna_delay);
    dwt_settxantennadelay(cfg->tx_antenna_delay);

    dwt_write32bitreg(SYS_STATUS_ID, 0xFFFFFFFFUL);
    dwt_forcetrxoff();
    
    RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER, "[BSP][CFG] Configuration complete (TX delay=%u, RX delay=%u)", 
           cfg->tx_antenna_delay, cfg->rx_antenna_delay);

    return BSP_OK;
}

/**
 * @brief TX function - FIXED
 * 
 * QUAN TRỌNG: 
 * - DW1000 tự động thêm 2 byte CRC vào cuối frame
 * - dwt_writetxfctrl() nhận (payload_length + 2) để báo tổng độ dài on-air
 * - Nhưng chỉ ghi payload_length byte vào TX buffer
 */
bsp_err_t bsp_uwb_tx(const void *data, uint16_t length)
{
    if (!data || length == 0 || length > TX_MAX_PAYLOAD)
        return BSP_ERR;

    /* Ensure idle */
    dwt_forcetrxoff();
    HAL_Delay(1);

    /* Clear previous TX flags */
    dwt_write32bitreg(SYS_STATUS_ID,
                      SYS_STATUS_TXFRB |
                      SYS_STATUS_TXPRS |
                      SYS_STATUS_TXFRS |
                      SYS_STATUS_AAT);


    /* 
     * CRITICAL FIX:
     * - Write ONLY payload bytes to TX buffer
     * - But tell DW1000 the total on-air length = payload + 2 (CRC)
     */
    dwt_writetxdata(length, (uint8_t*)data, 0);  /* Write payload only */
    dwt_writetxfctrl(length + DW1000_CRC_LENGTH, 0);  /* Total on-air length including CRC */

    /* Start TX */
    /* Start TX with RESPONSE_EXPECTED as before */
    if (dwt_starttx(DWT_START_TX_IMMEDIATE | DWT_RESPONSE_EXPECTED) != DWT_SUCCESS) {
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
        // RLOG_D(LOG_OBJECT_CODE_UWB_DRIVER, "[TX] Success - %u bytes + 2 CRC sent", length);

    return BSP_OK;
}

/**
 * @brief RX function - FIXED to properly handle CRC
 * 
 * QUAN TRỌNG:
 * - RX_FINFO chứa độ dài on-air (bao gồm CRC)
 * - Phải trừ 2 byte CRC để lấy payload thật
 * - CRC đã được DW1000 verify, không cần kiểm tra thêm
 */
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
        /* 
         * CRITICAL FIX: 
         * Calculate payload length by subtracting CRC
         */
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

    /* RX errors - clear and restart */
    if (status & SYS_STATUS_ALL_RX_ERR) {
        RLOG_W(LOG_OBJECT_CODE_UWB_DRIVER, "[BSP][RX] RX error flags: 0x%08X", status);
        dwt_write32bitreg(SYS_STATUS_ID, status & SYS_STATUS_ALL_RX_ERR);
        dwt_forcetrxoff();
        dwt_rxreset();

        if (dwt_rxenable(DWT_START_RX_IMMEDIATE) != DWT_SUCCESS) {
            RLOG_W(LOG_OBJECT_CODE_UWB_DRIVER, "[BSP][RX] dwt_rxenable() failed after RX error");
            return BSP_ERR;
        }
        return BSP_ERR;
    }

    /* No frame yet - ensure RX is enabled */
    if (!(status & SYS_STATUS_RXPRD)) {
        dwt_forcetrxoff();
        dwt_write32bitreg(SYS_STATUS_ID, 
                          SYS_STATUS_RXFCG | SYS_STATUS_RXFCE | 
                          SYS_STATUS_RXPHE | SYS_STATUS_RXRFSL | 
                          SYS_STATUS_RXRFTO | SYS_STATUS_RXPTO | 
                          SYS_STATUS_ALL_RX_ERR | SYS_STATUS_CLKPLL_LL);

        dwt_setrxtimeout(0);  /* Continuous RX */

        if (dwt_rxenable(DWT_START_RX_IMMEDIATE) != DWT_SUCCESS) {
            RLOG_W(LOG_OBJECT_CODE_UWB_DRIVER, "[BSP][RX] dwt_rxenable() failed");
            return BSP_ERR;
        }
    }

    return BSP_ERR;
}

bsp_err_t bsp_uwb_read_40bit(uint8_t reg_addr, uint8_t sub_addr, uint64_t *timestamp)
{
    CHECK_PARAM(timestamp, BSP_ERR_PARAM);
    CHECK_PARAM(s_initialized, BSP_ERR);

    /* Return cached timestamp values to avoid timing issues */
    if (reg_addr == RX_TIME_ID && sub_addr == 0) {
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
void bsp_uwb_idle(void)
{
  dwt_forcetrxoff();
}

/* End of file -------------------------------------------------------- */
