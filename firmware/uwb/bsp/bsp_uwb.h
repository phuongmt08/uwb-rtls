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
#include "protos/protocol.pb.h"
#include "config.h"

#include "main.h"

typedef struct {
    uint16_t fp_amp1;
    uint16_t fp_amp2;
    uint16_t fp_amp3;
    uint16_t std_noise;
    uint16_t max_noise;
    uint16_t rx_pream_count;
    uint16_t fp_amp_norm_q8;
    uint16_t fp_snr_q8;
    bool     valid;
} bsp_uwb_rx_quality_t;

typedef enum {
    BSP_UWB_EVENT_NONE = 0,
    BSP_UWB_EVENT_RX_OK,
    BSP_UWB_EVENT_RX_TIMEOUT,
    BSP_UWB_EVENT_RX_ERROR,
    BSP_UWB_EVENT_TX_DONE
} bsp_uwb_event_type_t;

typedef struct {
    bsp_uwb_event_type_t type;
    bool                 rx_windowed;
    uint16_t             rx_len;
    uint8_t              rx_data[128];
    uint64_t             rx_ts;
    uint64_t             tx_ts;
    bsp_uwb_rx_quality_t rx_quality;
} bsp_uwb_event_t;

/* Pin definitions (based on BU01 schematic) ------------------------- */
/* SPI pins - connected to SPI1 peripheral */
#define UWB_SPI_PORT        GPIOA
#define UWB_SCK_PIN         GPIO_PIN_5   /* PA5 - SPICLK (Pin 20) */
#define UWB_MISO_PIN        GPIO_PIN_6   /* PA6 - SPIMISO (Pin 19) */
#define UWB_MOSI_PIN        GPIO_PIN_7   /* PA7 - SPIMOSI (Pin 18) */
#ifdef UWB_CS_PORT
#undef UWB_CS_PORT
#endif
#ifdef UWB_CS_PIN
#undef UWB_CS_PIN
#endif
#define UWB_CS_PORT         SPI1_CS1_GPIO_Port
#define UWB_CS_PIN          SPI1_CS1_Pin  /* PB12 - SPICS (Pin 17) */

/* Control pins */
#ifdef UWB_RST_PORT
#undef UWB_RST_PORT
#endif
#ifdef UWB_RST_PIN
#undef UWB_RST_PIN
#endif
#define UWB_RST_PORT        UWB_RST_GPIO_Port
#define UWB_RST_PIN         UWB_RST_Pin   /* PB2 - RST (Pin 3) */
#define UWB_IRQ_PORT        UWB_IRQ_GPIO_Port
#define UWB_IRQ_PIN         UWB_IRQ_Pin   /* PA4 - IRQ/GPIO8 (Pin 22) */

/* Note: RX/TX LEDs are controlled by DW1000 GPIO2/GPIO3 directly, not STM32 */

/* Public function prototypes ----------------------------------------- */
/**
 * @brief Initialize DW1000 device
 * @return BSP_OK on success, BSP_ERR on failure
 */
bsp_err_t bsp_uwb_init(void);

/**
 * @brief  Check if UWB driver was successfully initialized.
 * @retval true if initialized, false otherwise
 */
bool bsp_uwb_is_initialized(void);

/**
 * @brief Configure UWB radio parameters from protobuf UWB config.
 * @param[in] cfg  Pointer to protobuf_uwb_cfg_t (sys_config.uwb)
 * @return BSP_OK on success, BSP_ERR on failure
 */
bsp_err_t bsp_uwb_configure(const protobuf_uwb_cfg_t *cfg);

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
 * @brief Enable RX delayed
 * @param[in] rx_timestamp_dw 40-bit DW1000 timestamp when to start receiving
 * @param[in] timeout_ms Timeout in milliseconds
 * @return BSP_OK on success, BSP_ERR on failure
 */
bsp_err_t bsp_uwb_enable_rx_delayed(uint64_t rx_timestamp_dw, uint32_t timeout_ms);

/**
 * @brief Force DW1000 to idle state (turn off TX/RX)
 * @note Call this when stopping ranging to turn off RX/TX LEDs
 */
void bsp_uwb_idle(void);

/**
 * @brief Put DW1000 into low-power sleep.
 * @note Intended for long standby gaps only. Hot ranging paths should keep
 *       using bsp_uwb_idle() so TX/RX timing is not disturbed.
 */
bsp_err_t bsp_uwb_sleep_enter(void);

/**
 * @brief Wake DW1000 from sleep using the SPI CS wake mechanism.
 */
bsp_err_t bsp_uwb_sleep_wake(void);

/**
 * @brief Return true when the BSP believes DW1000 is in sleep mode.
 */
bool bsp_uwb_is_sleeping(void);

/**
 * @brief Get cached first-path quality diagnostics of the last RX frame.
 * @param[out] quality Output quality metrics.
 * @return BSP_OK when valid diagnostics are available.
 */
bsp_err_t bsp_uwb_get_last_rx_quality(bsp_uwb_rx_quality_t *quality);

/**
 * @brief Get cached RX timestamp of the last successfully received frame.
 * @param[out] timestamp 40-bit timestamp in DW units.
 * @return BSP_OK on success, BSP_ERR if unavailable.
 */
bsp_err_t bsp_uwb_get_last_rx_timestamp(uint64_t *timestamp);

/**
 * @brief Get cached TX timestamp of the last completed TX frame.
 * @param[out] timestamp 40-bit timestamp in DW units.
 * @return BSP_OK on success, BSP_ERR if unavailable.
 */
bsp_err_t bsp_uwb_get_last_tx_timestamp(uint64_t *timestamp);

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

void bsp_uwb_get_rx_error_counts(uint32_t *timeout,
                                 uint32_t *crc_err,
                                 uint32_t *phr_err,
                                 uint32_t *sync_err);
void bsp_uwb_reset_rx_error_counts(void);

/**
 * @brief Notify BSP about UWB IRQ edge (call from EXTI callback)
 */
void bsp_uwb_on_irq(void);
void bsp_uwb_dwt_isr(void);

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

typedef struct
{
  uint32_t tx_done;
  uint32_t rx_ok;
  uint32_t queue_overflow;
  uint32_t irq_extra_pass;
  uint32_t rx_rearm_fail;
} bsp_uwb_event_stats_t;

/**
 * @brief Fetch the latest event generated by the UWB ISR.
 * @param[out] out_event Pointer to destination struct
 * @return true if a new event was copied, false if no event.
 */
bool bsp_uwb_get_event(bsp_uwb_event_t *out_event);

/**
 * @brief Read and clear event-driven ISR diagnostics.
 */
void bsp_uwb_get_event_stats(bsp_uwb_event_stats_t *stats);

/**
 * @brief Discard any pending UWB events
 */
void bsp_uwb_clear_event(void);

/**
 * @brief Read internal temperature and voltage of DW1000 chip.
 * @param[out] temp  Pointer to float to store temperature in C.
 * @param[out] vbat  Pointer to float to store battery voltage in V.
 * @return BSP_OK on success, BSP_ERR on failure.
 */
bsp_err_t bsp_uwb_read_temp_vbat(float *temp, float *vbat);

#endif /* __BSP_UWB_H */
/* End of file -------------------------------------------------------- */
