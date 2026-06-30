/**
 * @file       hdlc.c
 * @copyright
 * @license
 * @version    1.0.1
 * @date       2025-12-15
 * @author     Phuong Mai
 * @brief      HDLC framing and parsing for serial communication.
 * @note       This implementation is designed for embedded systems and follows the C17 standard.
 */
#include "hdlc.h"

#include <string.h>

#define CHECK(_cond, _ret) do { if (!(_cond)) return (_ret); } while (0)

void hdlc_parser_init(hdlc_parser_t *parser)
{
    if (!parser) {
        return;
    }

    memset(parser, 0, sizeof(*parser));
    parser->state = HDLC_PARSER_STATE_IDLE;
}

void hdlc_parser_reset(hdlc_parser_t *parser)
{
    if (!parser) {
        return;
    }

    parser->state = HDLC_PARSER_STATE_IDLE;
    parser->data_counter = 0;
    parser->frame.sof = 0;
    parser->frame.type = 0;
    parser->frame.len = 0;
    parser->frame.checksum = 0;
}

uint8_t hdlc_checksum(const uint8_t *buf, uint16_t len)
{
    uint32_t sum = 0;

    if (!buf) {
        return 0;
    }

    while (len--) {
        sum += *buf++;
    }

    return (uint8_t)sum;
}

int hdlc_build(uint8_t *buf, uint16_t buf_size, uint8_t type, const uint8_t *data, uint16_t len)
{
    CHECK(buf && data, -1);
    CHECK(len <= HDLC_MAX_DATA_LEN, -1);

    uint16_t total = (uint16_t)(HDLC_HEADER_LEN + len + 1U);
    CHECK(buf_size >= total, -1);

    buf[0] = HDLC_SOF;
    buf[1] = type;
    buf[2] = (uint8_t)(len & 0xFFU);
    buf[3] = (uint8_t)((len >> 8U) & 0xFFU);

    if (len > 0U) {
        memcpy(&buf[HDLC_HEADER_LEN], data, len);
    }

    buf[HDLC_HEADER_LEN + len] = hdlc_checksum(buf, (uint16_t)(HDLC_HEADER_LEN + len));
    return (int)total;
}

bool hdlc_parse_byte(hdlc_parser_t *parser, uint8_t byte, hdlc_data_chunk_t *out_chunk)
{
    CHECK(parser && out_chunk, false);

    switch (parser->state)
    {
    case HDLC_PARSER_STATE_IDLE:
        if (byte == HDLC_SOF) {
            parser->frame.sof = byte;
            parser->state = HDLC_PARSER_STATE_TYPE;
        }
        break;

    case HDLC_PARSER_STATE_TYPE:
        parser->frame.type = byte;
        parser->state = HDLC_PARSER_STATE_LEN_LOW;
        break;

    case HDLC_PARSER_STATE_LEN_LOW:
        parser->frame.len = (uint16_t)byte;
        parser->state = HDLC_PARSER_STATE_LEN_HIGH;
        break;

    case HDLC_PARSER_STATE_LEN_HIGH:
        parser->frame.len |= (uint16_t)((uint16_t)byte << 8U);
        if (parser->frame.len > HDLC_MAX_DATA_LEN) {
            hdlc_parser_reset(parser);
            break;
        }
        parser->data_counter = 0;
        parser->state = (parser->frame.len == 0U) ? HDLC_PARSER_STATE_CHECKSUM : HDLC_PARSER_STATE_DATA;
        break;

    case HDLC_PARSER_STATE_DATA:
        parser->frame.data[parser->data_counter++] = byte;
        if (parser->data_counter >= parser->frame.len) {
            parser->state = HDLC_PARSER_STATE_CHECKSUM;
        }
        break;

    case HDLC_PARSER_STATE_CHECKSUM:
    {
        uint8_t frame_buf[HDLC_HEADER_LEN + HDLC_MAX_DATA_LEN];
        parser->frame.checksum = byte;

        frame_buf[0] = parser->frame.sof;
        frame_buf[1] = parser->frame.type;
        frame_buf[2] = (uint8_t)(parser->frame.len & 0xFFU);
        frame_buf[3] = (uint8_t)((parser->frame.len >> 8U) & 0xFFU);
        if (parser->frame.len > 0U) {
            memcpy(&frame_buf[HDLC_HEADER_LEN], parser->frame.data, parser->frame.len);
        }

        uint8_t calc = hdlc_checksum(frame_buf, (uint16_t)(HDLC_HEADER_LEN + parser->frame.len));
        if (calc == parser->frame.checksum) {
            out_chunk->type = parser->frame.type;
            out_chunk->len = parser->frame.len;
            if (out_chunk->len > 0U) {
                memcpy(out_chunk->data, parser->frame.data, out_chunk->len);
            }
            hdlc_parser_reset(parser);
            return true;
        }

        hdlc_parser_reset(parser);
        break;
    }

    default:
        hdlc_parser_reset(parser);
        break;
    }

    return false;
}
