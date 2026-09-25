#ifndef BSP_IO_H_
#define BSP_IO_H_

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

static inline bool bsp_io_uart_send_fusion_data(uint8_t mask, ...)
{
    (void)mask;
    return true;
}

static inline bool bsp_io_uart_send_fusion_log_data(uint8_t mask, ...)
{
    (void)mask;
    return true;
}

#ifdef __cplusplus
}
#endif

#endif /* BSP_IO_H_ */
