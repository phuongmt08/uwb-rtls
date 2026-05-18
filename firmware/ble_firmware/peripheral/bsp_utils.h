/**
 * @file       bsp_utils.h
 * @copyright  [Your Copyright]
 * @license    [Your License]
 * @version    1.0.0
 * @date       2026-04-08
 * @author     Dong Son
 *
 * @brief      
 */
/* Define to prevent recursive inclusion ------------------------------ */
#ifndef BSP_UTILS_H
#define BSP_UTILS_H

/* Includes ----------------------------------------------------------- */
#include <stdint.h>
#include <stdbool.h>

/* Public defines ----------------------------------------------------- */

/* Public enumerate/structure ----------------------------------------- */
/* Public macros ------------------------------------------------------ */
/* Public variables --------------------------------------------------- */
/* Public function prototypes ----------------------------------------- */
/**@brief Initialize timer module.
 */
void bsp_utils_init(void);

/**@brief Start LED blink timer (250ms toggle interval = 500ms full cycle).
 *
 * @return 0 on success, error code on failure
 */
void bsp_utils_led_blink_start(void);

/**@brief Stop LED blink timer.
 *
 * @return 0 on success, error code on failure
 */
void bsp_utils_led_blink_stop(void);

/**@brief Turn on LED.
 */
void bsp_utils_led_on(void);

/**@brief Turn off LED.
 */
void bsp_utils_led_off(void);

/**@brief Toggle LED.
 */
void bsp_utils_led_toggle(void);

/**@brief Pulse the status LED once, then restore the previous blink state.
 */
void bsp_utils_led_activity_pulse(void);

#endif // BSP_UTILS_H
