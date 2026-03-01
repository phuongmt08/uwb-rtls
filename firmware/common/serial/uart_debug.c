#include "uart_debug.h"
#include "hdlc.h"
#include "usart.h"
#include "stm32f4xx_hal.h"

#include <string.h>

#define CHECK(_cond, _ret) do { if (!(_cond)) return (_ret); } while (0)

static serial_func_t s_uart_tx_handler = 0;
static hdlc_parser_t s_uart_parser;

void uart_debug_init(void)
{
    hdlc_parser_init(&s_uart_parser);
}

void uart_debug_set_tx_handler(serial_func_t handler)
{
    s_uart_tx_handler = handler;
}

void uart_debug_rx_push(const uint8_t *data, uint32_t len)
{
    (void)data;
    (void)len;
}

int uart_debug_read(int file, char *ptr, int len, uint8_t type)
{
    (void)file;
    (void)type;

    CHECK(ptr && len > 0, -1);

    hdlc_data_chunk_t chunk;
    uint8_t byte;

    while (1) {
        if (HAL_UART_Receive(&huart1, &byte, 1, HAL_MAX_DELAY) != HAL_OK) {
            return -1;
        }

        if (hdlc_parse_byte(&s_uart_parser, byte, &chunk)) {
            CHECK(chunk.len <= (uint16_t)len, -1);
            if (chunk.len > 0U) {
                memcpy(ptr, chunk.data, chunk.len);
            }
            return (int)chunk.len;
        }
    }
}

int uart_debug_write(int file, char *ptr, int len, uint8_t type)
{
    CHECK(ptr && len > 0, -1);

    uint8_t frame[HDLC_FRAME_MAX_LEN];
    int frame_len = hdlc_build(frame, sizeof(frame), type, (const uint8_t*)ptr, (uint16_t)len);
    CHECK(frame_len > 0, -1);

    if (s_uart_tx_handler) {
        return s_uart_tx_handler(file, (char*)frame, frame_len, type);
    }

    return (HAL_UART_Transmit(&huart1, frame, (uint16_t)frame_len, HAL_MAX_DELAY) == HAL_OK) ? len : -1;
}
