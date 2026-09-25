#ifndef V_HAL_H_
#define V_HAL_H_

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Virtual STM32 HAL tick */
uint32_t HAL_GetTick(void);

void v_hal_init(void);
void v_hal_advance_ms(uint32_t ms);
void v_hal_set_ms(uint32_t ms);

#ifdef __cplusplus
}
#endif

#endif /* V_HAL_H_ */
