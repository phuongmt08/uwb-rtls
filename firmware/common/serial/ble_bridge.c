#include "ble_bridge.h"
#include "hdlc.h"
#include "usart.h"
#include "stm32f4xx_hal.h"

#include <string.h>

#define CHECK(_cond, _ret) do { if (!(_cond)) return (_ret); } while (0)

#define BLE_RX_BUF_SIZE  512u
#define BLE_RX_BUF_MASK  (BLE_RX_BUF_SIZE - 1u)
#define BLE_DMA_BUF_SIZE 256u

static uint8_t  s_rx_buf[BLE_RX_BUF_SIZE];
static volatile uint32_t s_rx_head = 0u;
static volatile uint32_t s_rx_tail = 0u;

static uint8_t s_dma_rx_buf[BLE_DMA_BUF_SIZE];
static uint32_t s_last_dma_ptr = 0;

static serial_func_t s_tx_handler = 0;
static hdlc_parser_t s_parser;

extern DMA_HandleTypeDef hdma_usart2_rx;

static inline bool ble_rx_pop(uint8_t *out)
{
    if (s_rx_head == s_rx_tail) {
        return false;
    }

    *out = s_rx_buf[s_rx_tail & BLE_RX_BUF_MASK];
    s_rx_tail++;
    return true;
}

void ble_bridge_init(void)
{
    hdlc_parser_init(&s_parser);
    s_rx_head = 0u;
    s_rx_tail = 0u;
    s_last_dma_ptr = 0u;

    /* Start DMA circular receive */
    __HAL_UART_ENABLE_IT(&huart2, UART_IT_IDLE);
    HAL_UART_Receive_DMA(&huart2, s_dma_rx_buf, BLE_DMA_BUF_SIZE);
}

void ble_bridge_set_tx_handler(serial_func_t handler)
{
    s_tx_handler = handler;
}

/**
 * Push data into the ring buffer for HDLC processing.
 */
void ble_bridge_rx_push(const uint8_t *data, uint32_t len)
{
    for (uint32_t i = 0; i < len; i++) {
        if ((s_rx_head - s_rx_tail) >= BLE_RX_BUF_SIZE) break; /* buffer full — drop */
        s_rx_buf[s_rx_head & BLE_RX_BUF_MASK] = data[i];
        s_rx_head++;
    }
}

/**
 * Called from ISR (IDLE or RxCplt) to process new DMA data.
 */
void ble_bridge_uart_rx_check(void)
{
    uint32_t curr_dma_ptr = BLE_DMA_BUF_SIZE - __HAL_DMA_GET_COUNTER(&hdma_usart2_rx);

    if (curr_dma_ptr != s_last_dma_ptr) {
        if (curr_dma_ptr > s_last_dma_ptr) {
            /* Linear case */
            ble_bridge_rx_push(&s_dma_rx_buf[s_last_dma_ptr], curr_dma_ptr - s_last_dma_ptr);
        } else {
            /* Wrap-around case */
            ble_bridge_rx_push(&s_dma_rx_buf[s_last_dma_ptr], BLE_DMA_BUF_SIZE - s_last_dma_ptr);
            if (curr_dma_ptr > 0) {
                ble_bridge_rx_push(&s_dma_rx_buf[0], curr_dma_ptr);
            }
        }
        s_last_dma_ptr = curr_dma_ptr;
    }
}

/**
 * Compatibility wrapper for HAL callback.
 */
void ble_bridge_uart_rx_cplt(void)
{
    ble_bridge_uart_rx_check();
}

/**
 * Non-blocking read: drains bytes from the ring buffer through the HDLC
 * parser. Returns the decoded frame length, or -1 if no complete frame yet.
 */
int ble_bridge_read(int file, char *ptr, int len, uint8_t type)
{
    (void)file;
    (void)type;

    CHECK(ptr && len > 0, -1);

    hdlc_data_chunk_t chunk;
    uint8_t byte;

    while (ble_rx_pop(&byte)) {
        if (hdlc_parse_byte(&s_parser, byte, &chunk)) {
            CHECK(chunk.len <= (uint16_t)len, -1);
            if (chunk.len > 0U) {
                memcpy(ptr, chunk.data, chunk.len);
            }
            return (int)chunk.len;
        }
    }

    return -1; /* no complete frame yet */
}

int ble_bridge_write(int file, char *ptr, int len, uint8_t type)
{
    (void)file;

    CHECK(ptr && len > 0, -1);

    uint8_t frame[HDLC_FRAME_MAX_LEN];
    int frame_len = hdlc_build(frame, sizeof(frame), type, (const uint8_t *)ptr, (uint16_t)len);
    CHECK(frame_len > 0, -1);

    if (s_tx_handler) {
        return s_tx_handler(file, (char *)frame, frame_len, type);
    }

    return (HAL_UART_Transmit(&huart2, frame, (uint16_t)frame_len, 100) == HAL_OK) ? len : -1;
}
