/**
 * @file       bsp_uwb.h
 * @brief      BSP layer for DW1000/DWM1000 module
 * @version    0.1.0
 * @date       2025
 */

#ifndef __BSP_UWB_H
#define __BSP_UWB_H

/* Includes ----------------------------------------------------------- */
#include "common.h"
#include "dwm1000.h"

/* Public enumerate/structure ----------------------------------------- */
/**
 * @brief  Minimal radio configuration
 */
typedef struct
{
  uint8_t  channel;           /*!< Channel 1,2,3,4,5,7 */
  uint8_t  prf;               /*!< Pulse Repetition Frequency: 16 or 64 MHz */
  uint8_t  data_rate;         /*!< 0=110k,1=850k,2=6M8 */
  uint16_t preamble_symbols;  /*!< 64..4096 symbols */
  uint8_t  phr_mode;          /*!< 0=Standard, 1=Extended */
  bool     frame_filter_en;   /*!< Enable MAC frame filter */
  bool     auto_ack_en;       /*!< Enable auto acknowledgment */
  uint32_t tx_power;          /*!< TX power word */
  uint16_t rx_fwto;           /*!< RX frame wait timeout (us) */
} bsp_uwb_config_t;

/* Public function prototypes ----------------------------------------- */
/**
 * @brief Initialize DWM1000 device and verify device ID
 *
 * @return
 *  - BSP_OK on success
 *  - BSP_ERR on SPI/transfer error
 *  - BSP_ERR_PARAM on invalid parameter
 */
bsp_err_t bsp_uwb_init(void);

/**
 * @brief Apply minimal radio configuration
 *
 * @param[in] cfg  Pointer to configuration structure
 *
 * @return see bsp_err_t
 */
bsp_err_t bsp_uwb_configure(const bsp_uwb_config_t *cfg);

/**
 * @brief Transmit frame
 *
 * @param[in] data   PSDU data
 * @param[in] length Length of PSDU
 *
 * @return see bsp_err_t
 */
bsp_err_t bsp_uwb_tx(const void *data, uint16_t length);

/**
 * @brief Receive frame (blocking until frame or timeout)
 *
 * @param[out] data    Receive buffer
 * @param[in]  length  Maximum buffer size
 * @param[out] out_len Actual received length
 *
 * @return see bsp_err_t
 */
bsp_err_t bsp_uwb_rx(void *data, uint16_t length, uint16_t *out_len);

void bsp_uwb_reset(bool active);

bsp_err_t bsp_uwb_write_40bit(uint8_t reg, int32_t sub, uint64_t *value);

bsp_err_t bsp_uwb_read_40bit(uint8_t reg, int32_t sub, uint64_t *value);

#endif /* __BSP_UWB_H */

/* End of file -------------------------------------------------------- */
