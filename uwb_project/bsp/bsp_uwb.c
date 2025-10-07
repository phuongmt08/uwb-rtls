/**
 * @file       bsp_uwb.c
 * @brief      BSP layer for DW1000/DWM1000 module
 * @version    0.1.0
 * @date       2025
 */

/* Includes ----------------------------------------------------------- */
#include "bsp_uwb.h"

#include "bsp_delay.h"
#include "err.h"
#include "spi.h"
/* Private defines ---------------------------------------------------- */
#define EXPECTED_DEV_ID             0xDECA0130u

/* Register map (subset) */
#define REG_SYS_CFG                 0x04
#define REG_TX_FCTRL                0x08
#define REG_TX_POWER                0x1E
#define REG_CHAN_CTRL               0x1F
#define REG_RX_FWTO                 0x0C

/* SYS_CFG bits */
#define SYS_CFG_PHR_MODE_EXT        (1u << 18)
#define SYS_CFG_FRAME_FILTER_EN     (1u << 27)
#define SYS_CFG_AUTO_ACK_EN         (1u << 29)

/* TX_FCTRL bit fields */
#define TX_FCTRL_PREAMBLE_SHIFT     2
#define TX_FCTRL_DATARATE_SHIFT     16
/* Public enumerate/structure ----------------------------------------- */

typedef struct
{
  uint8_t  reg;
  int32_t  sub;
  uint8_t  len;
  const void *value;
  const char *desc;
} uwb_reg_cfg_t;
/* Private variables -------------------------------------------------- */
static dwm1000_t dwm1000;
/* Public variables --------------------------------------------------- */
extern SPI_HandleTypeDef hspi1;
/* Private functions -------------------------------------------------------- */
static bool bsp_uwb_spi_transfer(const uint8_t *tx, uint8_t *rx, uint16_t len)
{
  HAL_StatusTypeDef ret;
  if (tx && rx)
  {
    // Full duplex
    ret = HAL_SPI_TransmitReceive(&hspi1, (uint8_t *) tx, rx, len, HAL_MAX_DELAY);
  }
  else if (tx)
  {
    // Only transmit
    ret = HAL_SPI_Transmit(&hspi1, (uint8_t *) tx, len, HAL_MAX_DELAY);
  }
  else if (rx)
  {
    // Only receive
    ret = HAL_SPI_Receive(&hspi1, rx, len, HAL_MAX_DELAY);
  }
  else
  {
    return false;  // invalid
  }
  return (ret == HAL_OK);
}
static void bsp_uwb_cs(bool enable)
{
  if (enable)
    HAL_GPIO_WritePin(UWB_CS_PORT, UWB_CS_PIN, GPIO_PIN_RESET); /* CS low */
  else
    HAL_GPIO_WritePin(UWB_CS_PORT, UWB_CS_PIN, GPIO_PIN_SET); /* CS high */
}

void bsp_uwb_reset(bool active)
{
  HAL_GPIO_WritePin(UWB_RST_PORT, UWB_RST_PIN, active ? GPIO_PIN_RESET : GPIO_PIN_SET);
}
static void bsp_spi_set_low_speed(void)
{
  hspi1.Init.BaudRatePrescaler = SPI_BAUDRATEPRESCALER_128;  // ~3 MHz
  HAL_SPI_Init(&hspi1);
}
static void bsp_spi_set_high_speed(void)
{
  hspi1.Init.BaudRatePrescaler = SPI_BAUDRATEPRESCALER_128;  // ~6 MHz
  HAL_SPI_Init(&hspi1);
}
/* Private functions -------------------------------------------------------- */
static bsp_err_t config_table(const uwb_reg_cfg_t *table, size_t count)
{
  for (size_t i = 0; i < count; i++)
  {
#if defined(UWB_DEBUG_CONFIG)
    printf("[UWB] %-10s @0x%02X (%uB)\r\n", table[i].desc, table[i].reg, table[i].len);
#endif
    CHECK_ERR(
      dwm_write_register(&dwm1000, table[i].reg, table[i].sub,
                         table[i].value, table[i].len) == DWM_OK,
      BSP_ERR);
  }
  return BSP_OK;
}
/* Public functions --------------------------------------------------------- */
extern void bsp_delay_us(uint32_t us);

bsp_err_t bsp_uwb_init(void)
{
  bsp_delay_init();
  dwm1000.bus.spi_transfer       = bsp_uwb_spi_transfer;
  dwm1000.bus.set_cs             = bsp_uwb_cs;
  dwm1000.bus.set_reset          = bsp_uwb_reset;
  dwm1000.bus.delay_us           = bsp_delay_us;
  dwm1000.bus.set_spi_low_speed  = bsp_spi_set_low_speed;
  dwm1000.bus.set_spi_high_speed = bsp_spi_set_high_speed;

  CHECK_ERR(dwm_init(&dwm1000) == DWM_OK, BSP_ERR);

  uint32_t device_id = 0;
  CHECK_ERR(dwm_read_device_id(&dwm1000, &device_id) == DWM_OK, BSP_ERR);
  //  if (device_id != EXPECTED_DEV_ID) return BSP_ERR;

  (void) dwm_clear_system_status(&dwm1000, 0xFFFFFFFFu);
  return BSP_OK;
}

bsp_err_t bsp_uwb_tx(const void *data, uint16_t length)
{
  CHECK_PARAM(data && length > 0, BSP_ERR_PARAM);

  CHECK_ERR(dwm_write_tx_buffer(&dwm1000, data, length) == DWM_OK, BSP_ERR);

  /* Start TX */
  uint32_t ctrl = (1u << 1); /* TXSTRT */
  CHECK_ERR(dwm_write_system_control(&dwm1000, ctrl) == DWM_OK, BSP_ERR);

  /* Wait TXFRS */
  uint32_t status = 0;
  do
  {
    CHECK_ERR(dwm_read_system_status(&dwm1000, &status) == DWM_OK, BSP_ERR);
  } while ((status & (1u << 7)) == 0);

  /* Clear TXFRS */
  CHECK_ERR(dwm_clear_system_status(&dwm1000, (1u << 7)) == DWM_OK, BSP_ERR);
  return BSP_OK;
}

bsp_err_t bsp_uwb_rx(void *data, uint16_t max_len, uint16_t *out_len)
{
  CHECK_PARAM(data && out_len && max_len > 0, BSP_ERR_PARAM);

  /* Enable RX */
  uint32_t ctrl = (1u << 0); /* RXENAB */
  CHECK_ERR(dwm_write_system_control(&dwm1000, ctrl) == DWM_OK, BSP_ERR);

  /* Wait RX good frame or error/timeout */
  uint32_t status = 0;
  while (1)
  {
    CHECK_ERR(dwm_read_system_status(&dwm1000, &status) == DWM_OK, BSP_ERR);
    if (status & (1u << 6))
      break; /* RXFCG */
    if (status & ((1u << 30) | (0x3Cu)))
    { /* RXFTO or RX errors */
      (void) dwm_clear_system_status(&dwm1000, status);
      return BSP_ERR;
    }
  }

  /* Read received length from RX_FINFO if desired later.
     For now, read up to max_len; middleware can parse actual MAC length. */
  *out_len = max_len;
  CHECK_ERR(dwm_read_rx_buffer(&dwm1000, data, *out_len) == DWM_OK, BSP_ERR);

  /* Clear RXFCG */
  CHECK_ERR(dwm_clear_system_status(&dwm1000, (1u << 6)) == DWM_OK, BSP_ERR);
  return BSP_OK;
}
bsp_err_t bsp_uwb_configure(const bsp_uwb_config_t *cfg)
{
  CHECK_PARAM(cfg, BSP_ERR_PARAM);

  /* Build register values --------------------------------------------------- */
  static uint32_t chan_ctrl;
  static uint32_t sys_cfg;
  static uint32_t tx_fctrl;

  chan_ctrl = ((cfg->channel & 0x7u) << 0) |
              ((cfg->channel & 0x7u) << 5) |
              ((cfg->prf == 64 ? 1u : 0u) << 18);

  sys_cfg = 0;
  if (cfg->phr_mode)        sys_cfg |= SYS_CFG_PHR_MODE_EXT;
  if (cfg->frame_filter_en) sys_cfg |= SYS_CFG_FRAME_FILTER_EN;
  if (cfg->auto_ack_en)     sys_cfg |= SYS_CFG_AUTO_ACK_EN;

  tx_fctrl = ((uint32_t)cfg->preamble_symbols << TX_FCTRL_PREAMBLE_SHIFT) |
             ((cfg->data_rate & 0x3u) << TX_FCTRL_DATARATE_SHIFT);

  /* Define register configuration table ------------------------------------ */
  const uwb_reg_cfg_t table[] =
  {
      { REG_CHAN_CTRL, -1, 4, &chan_ctrl,  "CHAN_CTRL" },
      { REG_SYS_CFG,   -1, 4, &sys_cfg,    "SYS_CFG"   },
      { REG_TX_FCTRL,  -1, 4, &tx_fctrl,   "TX_FCTRL"  },
      { REG_TX_POWER,  0x0A, 4, &cfg->tx_power, "TX_POWER" },
      { REG_RX_FWTO,   0x00, 2, &cfg->rx_fwto, "RX_FWTO" }
  };

  return config_table(table, sizeof(table) / sizeof(table[0]));
}
bsp_err_t bsp_uwb_write_40bit(uint8_t reg, int32_t sub, uint64_t *value)
{
	CHECK_ERR((dwm_write_40bit(&dwm1000, reg, sub, value) == DWM_OK), BSP_ERR);
	return BSP_OK;
}

bsp_err_t bsp_uwb_read_40bit(uint8_t reg, int32_t sub, uint64_t *value)
{
	CHECK_ERR((dwm_read_40bit(&dwm1000, reg, sub, value) == DWM_OK), BSP_ERR);
	return BSP_OK;
}

/* End of file -------------------------------------------------------- */
