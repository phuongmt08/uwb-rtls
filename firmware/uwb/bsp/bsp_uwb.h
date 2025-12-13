/**
 * @file       bsp_uwb.h
 * @brief      BSP layer for DW1000 using DecaWave driver
 * @version    0.3.0
 * @date       2025-12-10
 */

#ifndef __BSP_UWB_H
#define __BSP_UWB_H

/* Includes ----------------------------------------------------------- */
#include "common.h"
#include <stdbool.h>

/* Pin definitions (based on BU01 schematic) ------------------------- */
/* SPI pins - connected to SPI1 peripheral */
#define UWB_SPI_PORT        GPIOA
#define UWB_SCK_PIN         GPIO_PIN_5   /* PA5 - SPICLK (Pin 20) */
#define UWB_MISO_PIN        GPIO_PIN_6   /* PA6 - SPIMISO (Pin 19) */
#define UWB_MOSI_PIN        GPIO_PIN_7   /* PA7 - SPIMOSI (Pin 18) */
#define UWB_CS_PORT         GPIOB
#define UWB_CS_PIN          GPIO_PIN_12  /* PB12 - SPICS (Pin 17) */

/* Control pins */
#define UWB_RST_PORT        GPIOB
#define UWB_RST_PIN         GPIO_PIN_2   /* PB2 - RST (Pin 3) */
#define UWB_IRQ_PORT        GPIOA
#define UWB_IRQ_PIN         GPIO_PIN_4   /* PA4 - IRQ/GPIO8 (Pin 22) */

/* Note: RX/TX LEDs are controlled by DW1000 GPIO2/GPIO3 directly, not STM32 */

/* Public enumerate/structure ----------------------------------------- */
/**
 * @brief  UWB radio configuration
 */
typedef struct {
  uint8_t  channel;
  uint8_t  prf;
  uint8_t  data_rate;
  uint8_t  preamble_code;
  uint16_t tx_antenna_delay;
  uint16_t rx_antenna_delay;
  uint32_t tx_power;
} bsp_uwb_config_t;

/* Public function prototypes ----------------------------------------- */
/**
 * @brief Initialize DW1000 device
 * @return BSP_OK on success, BSP_ERR on failure
 */
bsp_err_t bsp_uwb_init(void);

/**
 * @brief Configure UWB radio parameters
 * @param[in] cfg  Pointer to configuration structure
 * @return BSP_OK on success, BSP_ERR on failure
 */
bsp_err_t bsp_uwb_configure(const bsp_uwb_config_t *cfg);

/**
 * @brief Transmit a frame (blocking until complete)
 * @param[in] data   Frame data
 * @param[in] length Frame length in bytes
 * @return BSP_OK on success, BSP_ERR on failure
 */
bsp_err_t bsp_uwb_tx(const void *data, uint16_t length);

/**
 * @brief Non-blocking RX check - returns immediately
 * @param[out] data    Receive buffer
 * @param[in]  length  Maximum buffer size
 * @param[out] out_len Actual received length
 * @return BSP_OK if frame received, BSP_ERR if no frame or error
 * @note This function does NOT block. Returns immediately with status.
 */
bsp_err_t bsp_uwb_rx(void *data, uint16_t length, uint16_t *out_len);

/**
 * @brief Read 40-bit timestamp from DW1000 register
 * @param[in]  reg_addr  Register address (e.g., 0x15 for RX, 0x17 for TX)
 * @param[in]  sub_addr  Sub-address (usually 0x00)
 * @param[out] timestamp Pointer to store 64-bit value (40-bit in LSBs)
 * @return BSP_OK on success, BSP_ERR on failure
 */
bsp_err_t bsp_uwb_read_40bit(uint8_t reg_addr, uint8_t sub_addr, uint64_t *timestamp);

/**
 * @brief Control hardware reset pin
 * @param[in] active  true = assert reset, false = deassert
 */
void bsp_uwb_reset(bool active);

#endif /* __BSP_UWB_H */
/* End of file -------------------------------------------------------- */