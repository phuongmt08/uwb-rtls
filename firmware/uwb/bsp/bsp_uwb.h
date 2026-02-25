/**
 * @file       bsp_uwb.h
 * @copyright
 * @license
 * @version    0.4.0
 * @date       2025-12-11
 * @author     Phuong Mai
 * @brief      Board Support Package for UWB (DW1000)
 * @note       None
 * @example    None
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

/**
 * @brief Enable RX with specific timeout
 * @param[in] timeout_ms Timeout in milliseconds (0 = continuous)
 * @return BSP_OK on success, BSP_ERR on failure
 */
bsp_err_t bsp_uwb_enable_rx(uint32_t timeout_ms);

/**
 * @brief Force DW1000 to idle state (turn off TX/RX)
 * @note Call this when stopping ranging to turn off RX/TX LEDs
 */
void bsp_uwb_idle(void);

/**
 * @brief Read RSSI of last received frame
 * @return RSSI value in dBm (negative value, e.g., -70 dBm)
 *         Returns 0 if no valid RX or error
 */
int8_t bsp_uwb_get_rssi(void);

/**
 * @brief Get configured TX antenna delay
 * @return TX antenna delay in DW1000 time units
 */
uint16_t bsp_uwb_get_tx_antenna_delay(void);

/**
 * @brief Transmit frame at specific delayed time (scheduled TX)
 * @param[in] data Frame data
 * @param[in] length Frame length in bytes
 * @param[in] tx_timestamp 40-bit DW1000 timestamp when to transmit
 * @return BSP_OK on success, BSP_ERR on failure
 * @note Uses DW1000 delayed TX feature to transmit at precise time
 */
bsp_err_t bsp_uwb_tx_delayed(const void *data, uint16_t length, uint64_t tx_timestamp);
bool bsp_uwb_is_rx_ready(void);
uint64_t bsp_uwb_get_current_time_dw(void);
bsp_err_t bsp_uwb_validate_delayed_tx(uint64_t tx_timestamp_dw, uint64_t min_guard_dw);
uint16_t bsp_uwb_get_rx_antenna_delay(void);

/**
 * @brief Notify BSP about UWB IRQ edge (call from EXTI callback)
 */
void bsp_uwb_on_irq(void);

/**
 * @brief Clear pending UWB IRQ event flag
 */
void bsp_uwb_clear_irq_event(void);

/**
 * @brief Wait for UWB IRQ event flag with timeout
 * @param[in] timeout_ms Timeout in milliseconds
 * @return true if IRQ event occurred, false on timeout
 */
bool bsp_uwb_wait_for_irq_event(uint32_t timeout_ms);

#endif /* __BSP_UWB_H */
/* End of file -------------------------------------------------------- */