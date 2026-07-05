/**
 * @file       hdlc.h
 * @copyright
 * @license
 * @version    1.0.1
 * @date       2025-12-15
 * @author     Phuong Mai
 * @brief      HDLC framing and parsing for serial communication.
 * @note       This implementation is designed for embedded systems and follows the C17 standard.
 */
#ifndef __HDLC_H
#define __HDLC_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define HDLC_SOF              (0x55U)
#define HDLC_HEADER_LEN       (4U)
#define HDLC_MAX_DATA_LEN     (256U)
#define HDLC_FRAME_MAX_LEN    (HDLC_HEADER_LEN + HDLC_MAX_DATA_LEN + 1U)

typedef enum {
    HDLC_PARSER_STATE_IDLE = 0,
    HDLC_PARSER_STATE_TYPE,
    HDLC_PARSER_STATE_LEN_LOW,
    HDLC_PARSER_STATE_LEN_HIGH,
    HDLC_PARSER_STATE_DATA,
    HDLC_PARSER_STATE_CHECKSUM
} hdlc_parser_state_t;

typedef struct {
    uint8_t sof;
    uint8_t type;
    uint16_t len;
    uint8_t data[HDLC_MAX_DATA_LEN];
    uint8_t checksum;
} hdlc_frame_t;

typedef struct {
    uint8_t type;
    uint8_t data[HDLC_MAX_DATA_LEN];
    uint16_t len;
} hdlc_data_chunk_t;

typedef struct {
    hdlc_parser_state_t state;
    uint16_t data_counter;
    hdlc_frame_t frame;
} hdlc_parser_t;

void hdlc_parser_init(hdlc_parser_t *parser);
void hdlc_parser_reset(hdlc_parser_t *parser);
uint8_t hdlc_checksum(const uint8_t *buf, uint16_t len);
int hdlc_build(uint8_t *buf, uint16_t buf_size, uint8_t type, const uint8_t *data, uint16_t len);
bool hdlc_parse_byte(hdlc_parser_t *parser, uint8_t byte, hdlc_data_chunk_t *out_chunk);

#endif /* __HDLC_H */
