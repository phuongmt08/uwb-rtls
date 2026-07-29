#include "ble_bridge.h"
#include "hdlc.h"
#include "usart.h"
#include "stm32f4xx_hal.h"

#include <string.h>

#define CHECK(_cond, _ret) do { if (!(_cond)) return (_ret); } while (0)

#define BLE_RX_BUF_SIZE  2048u
#define BLE_RX_BUF_MASK  (BLE_RX_BUF_SIZE - 1u)
#define BLE_DMA_BUF_SIZE 256u

static uint8_t  s_rx_buf[BLE_RX_BUF_SIZE];
static volatile uint32_t s_rx_head = 0u;
static volatile uint32_t s_rx_tail = 0u;

static uint8_t s_dma_rx_buf[BLE_DMA_BUF_SIZE];
static volatile uint32_t s_last_dma_ptr = 0;

static serial_func_t s_tx_handler = 0;
static hdlc_parser_t s_parser;

volatile ble_bridge_diag_t g_ble_bridge_diag;

extern DMA_HandleTypeDef hdma_usart2_rx;

static void ble_bridge_reset_rx_state(void)
{
    s_rx_head = 0u;
    s_rx_tail = 0u;
    s_last_dma_ptr = 0u;
    hdlc_parser_reset(&s_parser);
}

static void ble_bridge_start_dma_rx(void)
{
    __HAL_UART_CLEAR_OREFLAG(&huart2);
    __HAL_UART_CLEAR_FEFLAG(&huart2);
    __HAL_UART_CLEAR_NEFLAG(&huart2);
    __HAL_UART_CLEAR_IDLEFLAG(&huart2);
    __HAL_UART_ENABLE_IT(&huart2, UART_IT_IDLE);
    if (HAL_UART_Receive_DMA(&huart2,
                             s_dma_rx_buf,
                             BLE_DMA_BUF_SIZE) != HAL_OK) {
        g_ble_bridge_diag.dma_start_failed++;
    }
}

static bool ble_bridge_dma_rx_running(void)
{
    return (huart2.RxState == HAL_UART_STATE_BUSY_RX) &&
           HAL_IS_BIT_SET(huart2.Instance->CR3, USART_CR3_DMAR) &&
           (hdma_usart2_rx.State != HAL_DMA_STATE_READY);
}

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
    memset((void *)&g_ble_bridge_diag, 0, sizeof(g_ble_bridge_diag));
    hdlc_parser_init(&s_parser);
    ble_bridge_reset_rx_state();
    ble_bridge_start_dma_rx();
}

void ble_bridge_set_tx_handler(serial_func_t handler)
{
    s_tx_handler = handler;
}

void ble_bridge_rx_push(const uint8_t *data, uint32_t len)
{
    for (uint32_t i = 0; i < len; i++) {
        if ((s_rx_head - s_rx_tail) >= BLE_RX_BUF_SIZE) {
            g_ble_bridge_diag.ring_dropped_bytes += (len - i);
            break;
        }
        s_rx_buf[s_rx_head & BLE_RX_BUF_MASK] = data[i];
        s_rx_head++;
        g_ble_bridge_diag.rx_bytes++;
    }
}

void ble_bridge_uart_rx_check(void)
{
    g_ble_bridge_diag.dma_rx_checks++;

    if (!ble_bridge_dma_rx_running()) {
        g_ble_bridge_diag.dma_not_running++;
        ble_bridge_uart_rx_recover();
        return;
    }

    uint32_t curr_dma_ptr =
        BLE_DMA_BUF_SIZE - __HAL_DMA_GET_COUNTER(&hdma_usart2_rx);

    if (curr_dma_ptr != s_last_dma_ptr) {
        if (curr_dma_ptr > s_last_dma_ptr) {
            ble_bridge_rx_push(&s_dma_rx_buf[s_last_dma_ptr],
                               curr_dma_ptr - s_last_dma_ptr);
        } else {
            ble_bridge_rx_push(&s_dma_rx_buf[s_last_dma_ptr],
                               BLE_DMA_BUF_SIZE - s_last_dma_ptr);
            if (curr_dma_ptr > 0) {
                ble_bridge_rx_push(&s_dma_rx_buf[0], curr_dma_ptr);
            }
        }
        s_last_dma_ptr = curr_dma_ptr;
    }
}

void ble_bridge_uart_rx_cplt(void)
{
    ble_bridge_uart_rx_check();
}

void ble_bridge_uart_rx_error(uint32_t error_code)
{
    g_ble_bridge_diag.uart_error_count++;
    g_ble_bridge_diag.uart_last_error = error_code;

    if ((error_code & HAL_UART_ERROR_PE) != 0U) {
        g_ble_bridge_diag.uart_parity_errors++;
    }
    if ((error_code & HAL_UART_ERROR_NE) != 0U) {
        g_ble_bridge_diag.uart_noise_errors++;
    }
    if ((error_code & HAL_UART_ERROR_FE) != 0U) {
        g_ble_bridge_diag.uart_framing_errors++;
    }
    if ((error_code & HAL_UART_ERROR_ORE) != 0U) {
        g_ble_bridge_diag.uart_overrun_errors++;
    }
    if ((error_code & HAL_UART_ERROR_DMA) != 0U) {
        g_ble_bridge_diag.uart_dma_errors++;
    }

    ble_bridge_uart_rx_recover();
}

void ble_bridge_uart_rx_recover(void)
{
    g_ble_bridge_diag.dma_recoveries++;
    (void)HAL_UART_DMAStop(&huart2);
    ble_bridge_reset_rx_state();
    ble_bridge_start_dma_rx();
}

int ble_bridge_read(int file, char *ptr, int len, uint8_t type)
{
    (void)file;
    (void)type;

    CHECK(ptr && len > 0, -1);

    hdlc_data_chunk_t chunk;
    uint8_t byte;

    while (ble_rx_pop(&byte)) {
        hdlc_parser_state_t const state_before = s_parser.state;

        if (hdlc_parse_byte(&s_parser, byte, &chunk)) {
            g_ble_bridge_diag.frames_ok++;
            CHECK(chunk.len <= (uint16_t)len, -1);
            if (chunk.len > 0U) {
                memcpy(ptr, chunk.data, chunk.len);
            }
            return (int)chunk.len;
        }

        if (state_before == HDLC_PARSER_STATE_CHECKSUM) {
            g_ble_bridge_diag.frame_bad_checksum++;
        } else if (state_before == HDLC_PARSER_STATE_LEN_HIGH &&
                   s_parser.state == HDLC_PARSER_STATE_IDLE) {
            g_ble_bridge_diag.frame_bad_length++;
        }

        /*
         * Count only a real parser restart while reading the HDLC header.
         * 0x55 is legal inside protobuf/compressed payload data and must not
         * be reported as a resynchronization event.
         */
        if (byte == HDLC_SOF &&
            s_parser.state == HDLC_PARSER_STATE_TYPE &&
            (state_before == HDLC_PARSER_STATE_TYPE ||
             state_before == HDLC_PARSER_STATE_LEN_LOW ||
             state_before == HDLC_PARSER_STATE_LEN_HIGH ||
             state_before == HDLC_PARSER_STATE_CHECKSUM)) {
            g_ble_bridge_diag.frame_resync++;
        }
    }

    return -1;
}

int ble_bridge_write(int file, char *ptr, int len, uint8_t type)
{
    (void)file;

    CHECK(ptr && len > 0, -1);

    uint8_t frame[HDLC_FRAME_MAX_LEN];
    int frame_len = hdlc_build(frame, sizeof(frame), type,
                               (const uint8_t *)ptr, (uint16_t)len);
    CHECK(frame_len > 0, -1);

    if (s_tx_handler) {
        int result = s_tx_handler(file, (char *)frame, frame_len, type);
        if (result >= 0) {
            g_ble_bridge_diag.tx_ok++;
        } else {
            g_ble_bridge_diag.tx_failed++;
        }
        return result;
    }

    if (HAL_UART_Transmit(&huart2, frame, (uint16_t)frame_len, 100) == HAL_OK) {
        g_ble_bridge_diag.tx_ok++;
        return len;
    }

    g_ble_bridge_diag.tx_failed++;
    return -1;
}
