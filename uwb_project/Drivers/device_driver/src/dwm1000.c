/**
 * @file       dwm1000.c
 * @copyright
 * @license
 * @version    0.1.0
 * @date       2025
 * @author
 * @brief      Minimal DW1000/DWM1000 driver for PoC ranging
 * @example    None
 */

/* Public includes ---------------------------------------------------------- */
#include "dwm1000.h"

#include "err.h"
/* Private includes --------------------------------------------------------- */
#include <string.h>

/* Private defines ---------------------------------------------------------- */
/* Register addresses (subset) */
#define REG_DEV_ID             0x00
#define REG_EUI                0x01
#define REG_PANADR             0x03
#define REG_SYS_CFG            0x04
#define REG_SYS_TIME           0x06 /* 40-bit */
#define REG_TX_FCTRL           0x08
#define REG_TX_BUFFER          0x09
#define REG_DX_TIME            0x0A /* 40-bit */
#define REG_SYS_CTRL           0x0D
#define REG_SYS_MASK           0x0E
#define REG_SYS_STATUS         0x0F
#define REG_RX_FINFO           0x10
#define REG_RX_BUFFER          0x11
#define REG_RX_TIME            0x15 /* 40-bit */
#define REG_TX_TIME            0x17 /* 40-bit */
#define REG_CHAN_CTRL          0x1F
#define REG_RX_FWTO            0x0C /* subaddress 0 */

/* SYS_CTRL bits (subset) */
#define SYS_CTRL_RXENAB        (1u << 0)
#define SYS_CTRL_TXSTRT        (1u << 1)
#define SYS_CTRL_TXDLYS        (1u << 0) /* used with TXSTRT */
#define SYS_CTRL_TRXOFF        (1u << 6)
#define SYS_CTRL_WAIT4RESP     (1u << 7)

/* SYS_STATUS bits (subset) */
#define SYS_STATUS_RXFCG       (1u << 6)
#define SYS_STATUS_TXFRS       (1u << 7)
#define SYS_STATUS_RXFTO       (1u << 30)
#define SYS_STATUS_RX_ERR_MASK ((1u << 5) | (1u << 4) | (1u << 3) | (1u << 2))

/* Private macros ----------------------------------------------------------- */
/* None */

/* Private variables -------------------------------------------------------- */
/* None */

/* Private function prototypes --------------------------------------------- */
static uint8_t build_spi_header(uint8_t reg, int32_t sub, bool is_read, uint8_t *hdr);

/* Private functions -------------------------------------------------------- */
static inline void cs_assert(dwm1000_t *dev)
{
  dev->bus.set_cs(true);
}
static inline void cs_deassert(dwm1000_t *dev)
{
  dev->bus.set_cs(false);
}
/*
 * Transaction Header (DW1000 SPI)
 *
 * Octet 1
 * ---------------------------------------------------------------------------
 * | Bit 7      | Bit 6             | Bits 5..0                              |
 * ---------------------------------------------------------------------------
 * | Operation  | Sub-index present | Register file ID (0x00..0x3F, 64 regs) |
 * | 0=Read     | 1 = yes           |                                        |
 * | 1=Write    |                   |                                        |
 *
 * Octet 2  (present if Octet1.Bit6 = 1)
 * ---------------------------------------------------------------------------
 * | Bit 7                 | Bits 6..0                                       |
 * ---------------------------------------------------------------------------
 * | Extended address flag | Low 7 bits of 15-bit sub-address                |
 * | 1 = yes (use Octet 3) | Range 0x0000..0x7FFF                            |
 *
 * Octet 3  (present if Octet2.Bit7 = 1)
 * ---------------------------------------------------------------------------
 * | Bits 7..0 : High 8 bits of 15-bit sub-address                           |
 * ---------------------------------------------------------------------------
*/

static uint8_t build_spi_header(uint8_t register_id, int32_t subaddress, bool is_read, uint8_t *header_out)
{
  uint8_t header_len = 1;
  header_out[0]      = (is_read ? 0x80 : 0x00) | ((subaddress >= 0) ? 0x40 : 0x00) | (register_id & 0x3F);
  if (subaddress >= 0)
  {
    if (subaddress < 0x80)
    {
      header_out[1] = (uint8_t) (subaddress & 0x7F); /* no extension */
      header_len    = 2;
    }
    else
    {
      header_out[1] = 0x80 | (uint8_t) (subaddress & 0x7F); /* extension present */
      header_out[2] = (uint8_t) ((uint32_t) subaddress >> 7);
      header_len    = 3;
    }
  }
  return header_len;
}

/* Public functions --------------------------------------------------------- */
dwm_err_t dwm_read_register(dwm1000_t *dev, uint8_t reg, int32_t sub, void *buf, uint16_t len)
{
  CHECK_PARAM(dev && buf, DWM_ERR_PARAM);
  CHECK_PARAM(dev->bus.spi_transfer && dev->bus.set_cs, DWM_ERR_PARAM);

  uint8_t hdr[3];
  uint8_t hlen = build_spi_header(reg, sub, true, hdr);

  cs_assert(dev);
  CHECK_ERR(dev->bus.spi_transfer(hdr, NULL, hlen), DWM_ERR);
  CHECK_ERR(dev->bus.spi_transfer(NULL, (uint8_t *)buf, len), DWM_ERR);
  cs_deassert(dev);

  return DWM_OK;
}

dwm_err_t dwm_write_register(dwm1000_t *dev, uint8_t reg, int32_t sub, const void *buf, uint16_t len)
{
  CHECK_PARAM(dev, DWM_ERR_PARAM);
  CHECK_PARAM((buf != NULL) || (len == 0), DWM_ERR_PARAM);
  CHECK_PARAM(dev->bus.spi_transfer && dev->bus.set_cs, DWM_ERR_PARAM);

  uint8_t hdr[3];
  uint8_t hlen = build_spi_header(reg, sub, false, hdr);

  cs_assert(dev);
  CHECK_ERR(dev->bus.spi_transfer(hdr, NULL, hlen), DWM_ERR);
  bool ok = true;
  if (len) ok = dev->bus.spi_transfer((const uint8_t *)buf, NULL, len);
  cs_deassert(dev);

  CHECK_ERR(ok, DWM_ERR);
  return DWM_OK;
}

dwm_err_t dwm_init(dwm1000_t *dev)
{
  CHECK_PARAM(dev, DWM_ERR_PARAM);
  CHECK_PARAM(dev->bus.spi_transfer && dev->bus.set_cs && dev->bus.delay_us, DWM_ERR_PARAM);

  if (dev->bus.set_reset)       /* optional hardware reset pulse */
  {
    dev->bus.set_reset(true);
    dev->bus.delay_us(10);
    dev->bus.set_reset(false);
  }
  dev->bus.delay_us(20);        /* allow INIT -> IDLE; keep host SCLK ≤3 MHz during INIT */

  /* Clear pending status (write-1-to-clear) */
  uint8_t clr[5] = {0xFF,0xFF,0xFF,0xFF,0xFF};
  CHECK_ERR(dwm_write_register(dev, REG_SYS_STATUS, -1, clr, 5) == DWM_OK, DWM_ERR);

  return DWM_OK;
}

dwm_err_t dwm_read_device_id(dwm1000_t *dev, uint32_t *device_id)
{
  CHECK_PARAM(dev && device_id, DWM_ERR_PARAM);

  uint8_t b[4] = {0};
  CHECK_ERR(dwm_read_register(dev, REG_DEV_ID, -1, b, 4) == DWM_OK, DWM_ERR);

  *device_id = (uint32_t)b[0] | ((uint32_t)b[1] << 8) | ((uint32_t)b[2] << 16) | ((uint32_t)b[3] << 24);
  return DWM_OK;
}

dwm_err_t dwm_read_40bit(dwm1000_t *dev, uint8_t reg, int32_t sub, uint64_t *value)
{
  CHECK_PARAM(dev && value, DWM_ERR_PARAM);

  uint8_t b[5];
  CHECK_ERR(dwm_read_register(dev, reg, sub, b, 5) == DWM_OK, DWM_ERR);

  *value =  (uint64_t)b[0]        |
           ((uint64_t)b[1] << 8 ) |
           ((uint64_t)b[2] << 16) |
           ((uint64_t)b[3] << 24) |
           ((uint64_t)b[4] << 32);
  return DWM_OK;
}

dwm_err_t dwm_write_40bit(dwm1000_t *dev, uint8_t reg, int32_t sub, uint64_t value)
{
  CHECK_PARAM(dev, DWM_ERR_PARAM);

  uint8_t b[5];
  b[0] = (uint8_t)( value        & 0xFF);
  b[1] = (uint8_t)((value >> 8 ) & 0xFF);
  b[2] = (uint8_t)((value >> 16) & 0xFF);
  b[3] = (uint8_t)((value >> 24) & 0xFF);
  b[4] = (uint8_t)((value >> 32) & 0xFF);
  return dwm_write_register(dev, reg, sub, b, 5);
}
dwm_err_t dwm_write_tx_buffer(dwm1000_t *dev, const void *psdu, uint16_t length_bytes)
{
  CHECK_PARAM(dev && psdu && length_bytes > 0, DWM_ERR_PARAM);

  /* Write PSDU into TX buffer (subaddress 0) */
  CHECK_ERR(dwm_write_register(dev, REG_TX_BUFFER, 0x00, psdu, length_bytes) == DWM_OK, DWM_ERR);

  /* Program TX_FCTRL length field (little-endian) — only length for now */
  /* Note: keep data rate, ranging bit, etc. handled by higher layer later */
  uint32_t tx_fctrl = (uint32_t)length_bytes & 0x7FFu; /* TXFLEN[10:0] */
  return dwm_write_register(dev, REG_TX_FCTRL, -1, &tx_fctrl, 4);
}

dwm_err_t dwm_read_rx_buffer(dwm1000_t *dev, void *buffer, uint16_t length_bytes)
{
  CHECK_PARAM(dev && buffer && length_bytes > 0, DWM_ERR_PARAM);

  /* Read from start of RX buffer */
  return dwm_read_register(dev, REG_RX_BUFFER, 0x00, buffer, length_bytes);
}
dwm_err_t dwm_stop_transmitt(dwm1000_t *dev)
{
  CHECK_PARAM(dev, DWM_ERR_PARAM);
  uint32_t v = SYS_CTRL_TRXOFF;
  return dwm_write_register(dev, REG_SYS_CTRL, -1, &v, 4);
}

dwm_err_t dwm_write_system_control(dwm1000_t *dev, uint32_t value_le)
{
  CHECK_PARAM(dev, DWM_ERR_PARAM);
  return dwm_write_register(dev, REG_SYS_CTRL, -1, &value_le, 4);
}

dwm_err_t dwm_read_system_status(dwm1000_t *dev, uint32_t *status_low32_le)
{
  CHECK_PARAM(dev && status_low32_le, DWM_ERR_PARAM);

  uint8_t b[5] = {0};
  CHECK_ERR(dwm_read_register(dev, REG_SYS_STATUS, -1, b, 5) == DWM_OK, DWM_ERR);

  *status_low32_le =  (uint32_t)b[0] |
                      ((uint32_t)b[1] << 8) |
                      ((uint32_t)b[2] << 16) |
                      ((uint32_t)b[3] << 24);
  return DWM_OK;
}

dwm_err_t dwm_clear_system_status(dwm1000_t *dev, uint32_t mask_le)
{
  CHECK_PARAM(dev, DWM_ERR_PARAM);
  uint8_t b[5] = {
    (uint8_t)( mask_le        & 0xFF),
    (uint8_t)((mask_le >> 8 ) & 0xFF),
    (uint8_t)((mask_le >> 16) & 0xFF),
    (uint8_t)((mask_le >> 24) & 0xFF),
    0x00
  };
  return dwm_write_register(dev, REG_SYS_STATUS, -1, b, 5);
}
/* End of file -------------------------------------------------------------- */
