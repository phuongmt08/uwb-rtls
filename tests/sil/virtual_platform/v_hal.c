#include "v_hal.h"
#include "v_rtos.h"

static uint32_t s_hal_tick_ms = 0U;

void v_hal_init(void)
{
    s_hal_tick_ms = 0U;
}

void v_hal_advance_ms(uint32_t ms)
{
    s_hal_tick_ms += ms;
}

void v_hal_set_ms(uint32_t ms)
{
    s_hal_tick_ms = ms;
}

uint32_t HAL_GetTick(void)
{
    /* HAL tick syncs with virtual RTOS tick if RTOS is advanced */
    uint32_t rtos_tick = osKernelGetTickCount();
    return (rtos_tick > s_hal_tick_ms) ? rtos_tick : s_hal_tick_ms;
}
