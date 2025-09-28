/**
 * @file       bsp_uwb.c
 * @brief      BSP layer for DW1000/DWM1000 module
 * @version    0.1.0
 * @date       2025
 */

/* Includes ----------------------------------------------------------- */
#include "bsp_uwb.h"
#include "bsp_delay.h"
#include "spi.h"
#include "err.h"
/* Private defines ---------------------------------------------------- */
#define EXPECTED_DEV_ID   0xDECA0130u  /* Typical DW1000 device ID */

/* Private variables -------------------------------------------------- */
static dwm1000_t dwm1000;
/* Public variables --------------------------------------------------- */
extern SPI_HandleTypeDef hspi1;
/* Private functions -------------------------------------------------------- */
static bool bsp_uwb_spi_transfer(const uint8_t *tx, uint8_t *rx, uint16_t len)
{
  CHECK_ERR((HAL_SPI_TransmitReceive(&hspi1, (uint8_t *)tx, rx, len, HAL_MAX_DELAY) == HAL_OK), false);
  return true;
}

static void bsp_uwb_cs(bool enable)
{
  if (enable)
    HAL_GPIO_WritePin(UWB_CS_PORT, UWB_CS_PIN, GPIO_PIN_RESET); /* CS low */
  else
    HAL_GPIO_WritePin(UWB_CS_PORT, UWB_CS_PIN, GPIO_PIN_SET);   /* CS high */
}

static void bsp_uwb_reset(bool active)
{
  HAL_GPIO_WritePin(UWB_RST_PORT, UWB_RST_PIN,
                    active ? GPIO_PIN_RESET : GPIO_PIN_SET);
}

/* Public functions --------------------------------------------------------- */
extern void bsp_delay_us(uint32_t us);

bsp_err_t bsp_uwb_init(void)
{
  dwm1000.bus.spi_transfer = bsp_uwb_spi_transfer;
  dwm1000.bus.set_cs       = bsp_uwb_cs;
  dwm1000.bus.set_reset    = bsp_uwb_reset;
  dwm1000.bus.delay_us     = bsp_delay_us;

  CHECK_ERR(dwm_init(&dwm1000) == DWM_OK, BSP_ERR);

  uint32_t device_id = 0;
  CHECK_ERR(dwm_read_device_id(&dwm1000, &device_id) == DWM_OK, BSP_ERR);
  if (device_id != EXPECTED_DEV_ID) return BSP_ERR;

  (void)dwm_clear_system_status(&dwm1000, 0xFFFFFFFFu);
  return BSP_OK;
}
bsp_err_t bsp_uwb_configure(const dwm_config_t *cfg)
{
  CHECK_PARAM(cfg, BSP_ERR_PARAM);

  /* Store for later; RF tune to be added in middleware/radio cfg */
  dwm1000.channel   = cfg->channel;
  dwm1000.prf       = cfg->prf;
  dwm1000.data_rate = cfg->data_rate;

  /* Minimal CHAN_CTRL programming can be added when you finalize RF table */
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
  do {
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
  for (;;)
  {
    CHECK_ERR(dwm_read_system_status(&dwm1000, &status) == DWM_OK, BSP_ERR);
    if (status & (1u << 6)) break; /* RXFCG */
    if (status & ((1u << 30) | (0x3Cu))) { /* RXFTO or RX errors */
      (void)dwm_clear_system_status(&dwm1000, status);
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

/* End of file -------------------------------------------------------- */
