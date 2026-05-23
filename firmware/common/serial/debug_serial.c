#include "debug_serial.h"
#include "hdlc.h"
#include "usart.h"
#include "stm32f4xx_hal.h"
#include "config.h"

#ifndef BOOTLOADER
#include "usbd_cdc_if.h"
#include "sys_config.h"
#endif

#include <string.h>

#define CHECK(_cond, _ret) do { if (!(_cond)) return (_ret); } while (0)
#define DEBUG_RX_BUF_SIZE 1024u
#define DEBUG_RX_BUF_MASK (DEBUG_RX_BUF_SIZE - 1u)
#define DEBUG_DMA_BUF_SIZE 256u

static serial_func_t s_tx_handler = 0;
static hdlc_parser_t s_parser_host; /* Unified parser for USB and UART DMA */

static uint8_t s_rx_buf[DEBUG_RX_BUF_SIZE];
static volatile uint32_t s_rx_head = 0u;
static volatile uint32_t s_rx_tail = 0u;

static uint8_t s_dma_rx_buf[DEBUG_DMA_BUF_SIZE];
static uint32_t s_last_dma_ptr = 0;

extern DMA_HandleTypeDef hdma_usart1_rx;

static inline bool debug_rx_pop(uint8_t *out)
{
    if (s_rx_head == s_rx_tail) {
        return false;
    }

    *out = s_rx_buf[s_rx_tail & DEBUG_RX_BUF_MASK];
    s_rx_tail++;
    return true;
}

/**
 * Called from ISR (IDLE or RxCplt) to process new DMA data from UART1.
 */
void debug_serial_uart_rx_check(void)
{
    uint32_t curr_dma_ptr = DEBUG_DMA_BUF_SIZE - __HAL_DMA_GET_COUNTER(&hdma_usart1_rx);

    if (curr_dma_ptr != s_last_dma_ptr) {
        if (curr_dma_ptr > s_last_dma_ptr) {
            debug_serial_rx_push(&s_dma_rx_buf[s_last_dma_ptr], curr_dma_ptr - s_last_dma_ptr);
        } else {
            debug_serial_rx_push(&s_dma_rx_buf[s_last_dma_ptr], DEBUG_DMA_BUF_SIZE - s_last_dma_ptr);
            if (curr_dma_ptr > 0) {
                debug_serial_rx_push(&s_dma_rx_buf[0], curr_dma_ptr);
            }
        }
        s_last_dma_ptr = curr_dma_ptr;
    }
}

void debug_serial_init(void)
{
    hdlc_parser_init(&s_parser_host);
    s_rx_head = 0u;
    s_rx_tail = 0u;
    s_last_dma_ptr = 0u;

    /* Start DMA circular receive for Console/Network UART */
    __HAL_UART_ENABLE_IT(&huart1, UART_IT_IDLE);
    HAL_UART_Receive_DMA(&huart1, s_dma_rx_buf, DEBUG_DMA_BUF_SIZE);
}

void debug_serial_set_tx_handler(serial_func_t handler)
{
    s_tx_handler = handler;
}

void debug_serial_rx_push(const uint8_t *data, uint32_t len)
{
    for (uint32_t i = 0u; i < len; i++) {
        if ((s_rx_head - s_rx_tail) >= DEBUG_RX_BUF_SIZE) {
            break; /* buffer full - drop newest bytes */
        }

        s_rx_buf[s_rx_head & DEBUG_RX_BUF_MASK] = data[i];
        s_rx_head++;
    }
}

int debug_serial_read(int file, char *ptr, int len, uint8_t type)
{
    (void)file;
    (void)type;

    CHECK(ptr && len > 0, -1);

    hdlc_data_chunk_t chunk;
    uint8_t byte;

    /* Both USB (via CDC Callback) and UART (via DMA) push into s_rx_buf */
    while (debug_rx_pop(&byte)) {
        if (hdlc_parse_byte(&s_parser_host, byte, &chunk)) {
            CHECK(chunk.len <= (uint16_t)len, -1);
            if (chunk.len > 0U) {
                memcpy(ptr, chunk.data, chunk.len);
            }
            return (int)chunk.len;
        }
    }

    return -1;
}

int debug_serial_write(int file, char *ptr, int len, uint8_t type)
{
    (void)file;

    CHECK(ptr && len > 0, -1);

    uint8_t frame[HDLC_FRAME_MAX_LEN];
    int frame_len = hdlc_build(frame, sizeof(frame), type, (const uint8_t *)ptr, (uint16_t)len);
    CHECK(frame_len > 0, -1);

    if (s_tx_handler) {
        return s_tx_handler(file, (char *)frame, frame_len, type);
    }

#ifndef BOOTLOADER
    host_transport_t host_transport = sys_config_get_host_transport();
    if (host_transport == HOST_TRANSPORT_USB) {
        uint32_t start_ms = HAL_GetTick();
        uint8_t res;
        do {
            res = CDC_Transmit_FS(frame, (uint16_t)frame_len);
            if (res != USBD_BUSY) {
                break;
            }
        } while ((HAL_GetTick() - start_ms) <= 10u);
        return (res == USBD_OK) ? len : -1;
    }

    if (host_transport == HOST_TRANSPORT_UART) {
        return (HAL_UART_Transmit(&huart1, frame, (uint16_t)frame_len, 100) == HAL_OK) ? len : -1;
    }
#endif

    return (HAL_UART_Transmit(&huart1, frame, (uint16_t)frame_len, 100) == HAL_OK) ? len : -1;
}
