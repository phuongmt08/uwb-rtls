/**
 * @file       dwm1000.h
 * @copyright
 * @license
 * @version    0.1.0
 * @date       2025
 * @author
 * @brief      Minimal DW1000/DWM1000 driver for PoC ranging
 * @note       SPI header supports 1/2/3-octet formats
 * @example    None
 */

/* Define to prevent recursive inclusion ------------------------------------ */
#ifndef __DWM1000_H
#define __DWM1000_H

/* Public includes ---------------------------------------------------------- */
#include <stdbool.h>
#include <stdint.h>

/* Public enumerate/structure ----------------------------------------------- */
typedef enum
{
  DWM_OK = 0,
  DWM_ERR,
  DWM_ERR_PARAM,
} dwm_err_t;

/**
 * @brief  SPI/GPIO binding
 */
typedef struct
{
  /* CS active-low: set_chip_select(true) asserts CS */
  void (*set_cs)(bool select);
  /* Full-duplex SPI transfer; tx/rx can be NULL if not used */
  bool (*spi_transfer)(const uint8_t *tx, uint8_t *rx, uint16_t length);
  /* Hardware reset: set_reset(true) drives RSTn low; set_reset(false) releases it */
  void (*set_reset)(bool active_low);
  /* Busy-wait delay in microseconds */
  void (*delay_us)(uint32_t us);
  void (*set_spi_low_speed)(void);   /* ~3 MHz (PSC = 32) */
  void (*set_spi_high_speed)(void);  /* ~6 MHz (PSC = 16) */
} dwm_bus_if_t;

/**
 * @brief  Driver instance
 */
typedef struct
{
  dwm_bus_if_t bus;
  /* cached configuration (optional) */
  uint8_t channel;
  uint8_t prf;
  uint8_t data_rate;
} dwm1000_t;
typedef enum
{
  DWM_SFD_STANDARD_IEEE,
  DWM_SFD_NON_STANDARD
} dwm_sfd_mode_t;
typedef enum
{
  DWM_PHYSIC_STANDARD_MODE,
  DWM_PHYSIC_EXTETENED_MODE
} dwm_phr_mode_t;

/* Inline public functions  ----------------------------------------------------------- */
/* Device Time Unit (DTU) conversion (~63.8976 DTU per microsecond) */
static inline uint64_t dwm_us_to_dtu(float us)
{
  return (uint64_t) (us * 63.8976f + 0.5f);
}
static inline float dwm_dtu_to_us(uint64_t dtu)
{
  return (float) dtu / 63.8976f;
}
/* Public function prototypes ----------------------------------------------- */
dwm_err_t dwm_read_register(dwm1000_t *dev,
                            uint8_t    register_id,
                            int32_t    subaddress,
                            void      *buffer,
                            uint16_t   length_bytes);
dwm_err_t dwm_write_register(dwm1000_t  *dev,
                             uint8_t     register_id,
                             int32_t     subaddress,
                             const void *buffer,
                             uint16_t    length_bytes);
dwm_err_t dwm_init(dwm1000_t *dev);
dwm_err_t dwm_read_device_id(dwm1000_t *dev, uint32_t *device_id);

dwm_err_t dwm_read_40bit(dwm1000_t *dev, uint8_t register_id, int32_t subaddress, uint64_t *value);

dwm_err_t dwm_write_40bit(dwm1000_t *dev, uint8_t register_id, int32_t subaddress, uint64_t value);

dwm_err_t dwm_write_system_control(dwm1000_t *dev, uint32_t value); /* TXSTRT/RXENAB/... */
dwm_err_t dwm_read_system_status(dwm1000_t *dev, uint32_t *status_le);
dwm_err_t dwm_clear_system_status(dwm1000_t *dev, uint32_t mask_le);

dwm_err_t dwm_write_tx_buffer(dwm1000_t *dev, const void *psdu, uint16_t length_bytes);
dwm_err_t dwm_read_rx_buffer(dwm1000_t *dev, void *buffer, uint16_t length_bytes);
dwm_err_t dwm_wakeup(dwm1000_t *dev);
dwm_err_t dwm_enter_sleep(dwm1000_t *dev);

/* -------------------------------------------------------------------------- */

#endif /* __DWM1000_H */

/* End of file -------------------------------------------------------------- */
