/**
 * @file       bsp_uwb.c
 * @brief      BSP layer for DW1000 using DecaWave driver
 * @version    0.2.0
 * @date       2025
 * @note       Uses official DecaWave deca_driver instead of custom dwm1000
 */

/* Includes ----------------------------------------------------------- */
#include "bsp_uwb.h"
#include "bsp_util.h"
#include "err.h"
#include "spi.h"

/* DecaWave driver */
#include "../deca/deca_driver/deca_device_api.h"
#include "../deca/deca_driver/deca_regs.h"

/* Private defines ---------------------------------------------------- */
#define DW1000_DEVICE_ID            0xDECA0130UL

/* Private variables -------------------------------------------------- */
static bool s_initialized = false;

/* Public variables --------------------------------------------------- */
extern SPI_HandleTypeDef hspi1;

/* Private function prototypes ---------------------------------------- */
static int openspi_impl(void);
static int closespi_impl(void);
static void reset_DW1000(void);
static void port_set_dw1000_slowrate(void);
static void port_set_dw1000_fastrate(void);

/* SPI implementation for deca_driver --------------------------------- */
int writetospi(uint16 headerLength, const uint8 *headerBuffer,
               uint32 bodylength, const uint8 *bodyBuffer)
{
    /* CS Low */
    HAL_GPIO_WritePin(UWB_CS_PORT, UWB_CS_PIN, GPIO_PIN_RESET);
    
    /* Send header */
    if (headerLength > 0) {
        if (HAL_SPI_Transmit(&hspi1, (uint8_t*)headerBuffer, headerLength, HAL_MAX_DELAY) != HAL_OK) {
            HAL_GPIO_WritePin(UWB_CS_PORT, UWB_CS_PIN, GPIO_PIN_SET);
            return -1;
        }
    }
    
    /* Send body */
    if (bodylength > 0) {
        if (HAL_SPI_Transmit(&hspi1, (uint8_t*)bodyBuffer, bodylength, HAL_MAX_DELAY) != HAL_OK) {
            HAL_GPIO_WritePin(UWB_CS_PORT, UWB_CS_PIN, GPIO_PIN_SET);
            return -1;
        }
    }
    
    /* CS High */
    HAL_GPIO_WritePin(UWB_CS_PORT, UWB_CS_PIN, GPIO_PIN_SET);
    
    return 0;
}

int readfromspi(uint16 headerLength, const uint8 *headerBuffer,
                uint32 readlength, uint8 *readBuffer)
{
    /* CS Low */
    HAL_GPIO_WritePin(UWB_CS_PORT, UWB_CS_PIN, GPIO_PIN_RESET);
    
    /* Send header (read command) */
    if (headerLength > 0) {
        if (HAL_SPI_Transmit(&hspi1, (uint8_t*)headerBuffer, headerLength, HAL_MAX_DELAY) != HAL_OK) {
            HAL_GPIO_WritePin(UWB_CS_PORT, UWB_CS_PIN, GPIO_PIN_SET);
            return -1;
        }
    }
    
    /* Read data */
    if (readlength > 0) {
        if (HAL_SPI_Receive(&hspi1, readBuffer, readlength, HAL_MAX_DELAY) != HAL_OK) {
            HAL_GPIO_WritePin(UWB_CS_PORT, UWB_CS_PIN, GPIO_PIN_SET);
            return -1;
        }
    }
    
    /* CS High */
    HAL_GPIO_WritePin(UWB_CS_PORT, UWB_CS_PIN, GPIO_PIN_SET);
    
    return 0;
}

static int openspi_impl(void)
{
    /* SPI already initialized by STM32 HAL */
    return 0;
}

static int closespi_impl(void)
{
    return 0;
}

static void reset_DW1000(void)
{
    /* Assert reset (active low) */
    HAL_GPIO_WritePin(UWB_RST_PORT, UWB_RST_PIN, GPIO_PIN_RESET);
    HAL_Delay(2);
    
    /* Deassert reset */
    HAL_GPIO_WritePin(UWB_RST_PORT, UWB_RST_PIN, GPIO_PIN_SET);
    HAL_Delay(2);
}

static void port_set_dw1000_slowrate(void)
{
    hspi1.Init.BaudRatePrescaler = SPI_BAUDRATEPRESCALER_64; // ~1.5 MHz for init
    HAL_SPI_Init(&hspi1);
}

static void port_set_dw1000_fastrate(void)
{
    hspi1.Init.BaudRatePrescaler = SPI_BAUDRATEPRESCALER_64; // ~1.5 MHz (safe speed)
    HAL_SPI_Init(&hspi1);
}

/* Public functions --------------------------------------------------- */

bsp_err_t bsp_uwb_init(void)
{
    uint32_t dev_id;
    
    /* Initialize bsp_util */
    bsp_util_init();
    
    /* Hardware reset DW1000 */
    reset_DW1000();
    
    /* Setup SPI slow speed for initialization */
    port_set_dw1000_slowrate();
    
    /* Initialize deca_driver */
    if (dwt_initialise(DWT_LOADNONE) != DWT_SUCCESS) {
        return BSP_ERR;
    }
    
    /* Read and verify device ID */
    dev_id = dwt_readdevid();
    if (dev_id != DW1000_DEVICE_ID) {
        return BSP_ERR;
    }
    
    /* Switch to fast SPI speed */
    port_set_dw1000_fastrate();
    
    s_initialized = true;
    return BSP_OK;
}

bsp_err_t bsp_uwb_configure(const bsp_uwb_config_t *cfg)
{
    CHECK_PARAM(cfg != NULL, BSP_ERR_PARAM);
    CHECK_PARAM(s_initialized, BSP_ERR);
    
    /* Setup dwt_config_t structure */
    dwt_config_t dw_cfg = {
        .chan = cfg->channel,
        .prf = (cfg->prf == 64) ? DWT_PRF_64M : DWT_PRF_16M,
        .txPreambLength = DWT_PLEN_128,  /* 128 symbols */
        .rxPAC = DWT_PAC8,
        .txCode = 9,  /* Preamble code for channel 5 */
        .rxCode = 9,
        .nsSFD = 0,   /* Standard SFD */
        .dataRate = cfg->data_rate,
        .phrMode = DWT_PHRMODE_STD,
        .sfdTO = 129  /* SFD timeout */
    };
    
    /* Apply configuration */
    if (dwt_configure(&dw_cfg, DWT_LOADNONE) != DWT_SUCCESS) {
        return BSP_ERR;
    }
    
    /* Configure TX power (use default for now) */
    dwt_txconfig_t tx_cfg;
    tx_cfg.power = 0x0E082848UL;  /* Default for channel 5, PRF 64 */
    tx_cfg.PGdly = 0xC0;
    dwt_configuretxrf(&tx_cfg);
    
    return BSP_OK;
}

bsp_err_t bsp_uwb_tx(const void *data, uint16_t length)
{
    CHECK_PARAM(data && length > 0, BSP_ERR_PARAM);
    CHECK_PARAM(s_initialized, BSP_ERR);
    
    /* Write frame data to TX buffer */
    dwt_writetxdata(length, (uint8*)data, 0);
    
    /* Set frame length */
    dwt_writetxfctrl(length, 0);
    
    /* Start transmission */
    if (dwt_starttx(DWT_START_TX_IMMEDIATE) != DWT_SUCCESS) {
        return BSP_ERR;
    }
    /* Wait for TX complete */
    uint32_t status;
    while (!((status = dwt_read32bitreg(SYS_STATUS_ID)) & SYS_STATUS_TXFRS)) {
        /* Poll */
    }
    
    /* Clear TX complete flag */
    dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_TXFRS);
    
    return BSP_OK;
}

bsp_err_t bsp_uwb_rx(void *data, uint16_t length, uint16_t *out_len)
{
    CHECK_PARAM(data && out_len, BSP_ERR_PARAM);
    CHECK_PARAM(s_initialized, BSP_ERR);
    
    /* Enable RX (0 = immediate) */
    if (dwt_rxenable(0) != DWT_SUCCESS) {
        return BSP_ERR;
    }
    
    /* Wait for RX complete or timeout */
    uint32_t start_tick = HAL_GetTick();
    uint32_t status;
    
    while (1) {
        status = dwt_read32bitreg(SYS_STATUS_ID);
        
        /* Check if good frame received */
        if (status & SYS_STATUS_RXFCG) {
            break;
        }
        
        /* Check for RX errors */
		if (status & SYS_STATUS_ALL_RX_ERR) {
            /* Clear error flags */
            dwt_write32bitreg(SYS_STATUS_ID, status);
            return BSP_ERR;
        }
        
        /* Software timeout */
        if ((HAL_GetTick() - start_tick) > 1000) {
            dwt_forcetrxoff();
            return BSP_ERR;
        }
    }
    
    /* Get frame length */
    uint32_t frame_info = dwt_read32bitreg(RX_FINFO_ID);
    uint16_t frame_len = frame_info & 0x3FF;  /* Bits [9:0] */
    
    /* Read frame data */
    *out_len = (frame_len < length) ? frame_len : length;
    dwt_readrxdata((uint8*)data, *out_len, 0);
    
    /* Clear RX good frame flag */
    dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_RXFCG);
    
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

/* End of file -------------------------------------------------------- */
