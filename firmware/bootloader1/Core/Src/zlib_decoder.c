/**
 * @file    zlib_decoder.c
 * @brief   Small raw-DEFLATE decoder for independent FOTA blocks.
 * @license zlib License
 */

#include "zlib_decoder.h"

#include <string.h>

#define TINF_MAX_BITS          15U
#define TINF_MAX_LIT_SYMBOLS  288U
#define TINF_MAX_DIST_SYMBOLS  32U
#define TINF_MAX_CODE_LENGTHS (TINF_MAX_LIT_SYMBOLS + TINF_MAX_DIST_SYMBOLS)

typedef struct {
    uint16_t count[TINF_MAX_BITS + 1U];
    uint16_t symbol[TINF_MAX_LIT_SYMBOLS];
    uint16_t symbol_count;
} tinf_tree_t;

typedef struct {
    const uint8_t *source;
    uint32_t src_len;
    uint32_t src_pos;

    uint32_t bit_buf;
    uint8_t bit_count;

    zlib_output_cb_t out_cb;
    void *user_data;
    uint32_t out_index;
    uint32_t expected_output_len;

    uint8_t window[TINF_WINDOW_SIZE];
    uint32_t win_pos;

    tinf_tree_t lit_tree;
    tinf_tree_t dist_tree;
    tinf_tree_t code_tree;
    uint8_t dynamic_lengths[TINF_MAX_CODE_LENGTHS];
    uint8_t code_lengths[19];
} tinf_data_t;

static const uint8_t s_length_bits[29] = {
    0, 0, 0, 0, 0, 0, 0, 0,
    1, 1, 1, 1,
    2, 2, 2, 2,
    3, 3, 3, 3,
    4, 4, 4, 4,
    5, 5, 5, 5,
    0
};

static const uint16_t s_length_base[29] = {
    3, 4, 5, 6, 7, 8, 9, 10,
    11, 13, 15, 17,
    19, 23, 27, 31,
    35, 43, 51, 59,
    67, 83, 99, 115,
    131, 163, 195, 227, 258
};

static const uint8_t s_dist_bits[30] = {
    0, 0, 0, 0,
    1, 1,
    2, 2,
    3, 3,
    4, 4,
    5, 5,
    6, 6,
    7, 7,
    8, 8,
    9, 9,
    10, 10,
    11, 11,
    12, 12,
    13, 13
};

static const uint16_t s_dist_base[30] = {
    1, 2, 3, 4,
    5, 7,
    9, 13,
    17, 25,
    33, 49,
    65, 97,
    129, 193,
    257, 385,
    513, 769,
    1025, 1537,
    2049, 3073,
    4097, 6145,
    8193, 12289,
    16385, 24577
};

static const uint8_t s_code_length_order[19] = {
    16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15
};

static bool tinf_read_bits(tinf_data_t *data, uint8_t count, uint32_t *value)
{
    if (count == 0U) {
        *value = 0U;
        return true;
    }

    while (data->bit_count < count) {
        if (data->src_pos >= data->src_len) {
            return false;
        }
        data->bit_buf |= ((uint32_t)data->source[data->src_pos++])
                         << data->bit_count;
        data->bit_count = (uint8_t)(data->bit_count + 8U);
    }

    *value = data->bit_buf & ((1UL << count) - 1UL);
    data->bit_buf >>= count;
    data->bit_count = (uint8_t)(data->bit_count - count);
    return true;
}

static bool tinf_build_tree(tinf_tree_t *tree,
                            const uint8_t *lengths,
                            uint16_t symbol_count)
{
    uint16_t offsets[TINF_MAX_BITS + 1U] = {0};
    int32_t codes_left = 1;

    memset(tree, 0, sizeof(*tree));

    for (uint16_t symbol = 0U; symbol < symbol_count; symbol++) {
        uint8_t length = lengths[symbol];
        if (length > TINF_MAX_BITS) {
            return false;
        }
        tree->count[length]++;
    }
    tree->count[0] = 0U;

    for (uint8_t length = 1U; length <= TINF_MAX_BITS; length++) {
        codes_left = (codes_left << 1) - tree->count[length];
        if (codes_left < 0) {
            return false;
        }
    }

    offsets[1] = 0U;
    for (uint8_t length = 1U; length < TINF_MAX_BITS; length++) {
        offsets[length + 1U] =
            (uint16_t)(offsets[length] + tree->count[length]);
    }

    for (uint16_t symbol = 0U; symbol < symbol_count; symbol++) {
        uint8_t length = lengths[symbol];
        if (length != 0U) {
            uint16_t index = offsets[length]++;
            if (index >= TINF_MAX_LIT_SYMBOLS) {
                return false;
            }
            tree->symbol[index] = symbol;
            tree->symbol_count++;
        }
    }

    return true;
}

static bool tinf_decode_symbol(tinf_data_t *data,
                               const tinf_tree_t *tree,
                               uint16_t *symbol)
{
    int32_t sum = 0;
    int32_t current = 0;

    for (uint8_t length = 1U; length <= TINF_MAX_BITS; length++) {
        uint32_t bit;
        if (!tinf_read_bits(data, 1U, &bit)) {
            return false;
        }

        current = (current << 1) | (int32_t)bit;
        sum += tree->count[length];
        current -= tree->count[length];

        if (current < 0) {
            int32_t index = sum + current;
            if (index < 0 || index >= tree->symbol_count) {
                return false;
            }
            *symbol = tree->symbol[index];
            return true;
        }
    }

    return false;
}

static bool tinf_build_fixed_trees(tinf_data_t *data)
{
    uint8_t lit_lengths[TINF_MAX_LIT_SYMBOLS];
    uint8_t dist_lengths[TINF_MAX_DIST_SYMBOLS];

    for (uint16_t i = 0U; i < 144U; i++) {
        lit_lengths[i] = 8U;
    }
    for (uint16_t i = 144U; i < 256U; i++) {
        lit_lengths[i] = 9U;
    }
    for (uint16_t i = 256U; i < 280U; i++) {
        lit_lengths[i] = 7U;
    }
    for (uint16_t i = 280U; i < TINF_MAX_LIT_SYMBOLS; i++) {
        lit_lengths[i] = 8U;
    }
    memset(dist_lengths, 5, sizeof(dist_lengths));

    return tinf_build_tree(&data->lit_tree,
                           lit_lengths,
                           TINF_MAX_LIT_SYMBOLS) &&
           tinf_build_tree(&data->dist_tree,
                           dist_lengths,
                           TINF_MAX_DIST_SYMBOLS);
}

static bool tinf_build_dynamic_trees(tinf_data_t *data)
{
    uint32_t bits;
    uint16_t hlit;
    uint16_t hdist;
    uint16_t hclen;
    uint8_t *lengths = data->dynamic_lengths;
    uint8_t *code_lengths = data->code_lengths;

    memset(lengths, 0, TINF_MAX_CODE_LENGTHS);
    memset(code_lengths, 0, 19U);

    if (!tinf_read_bits(data, 5U, &bits)) {
        return false;
    }
    hlit = (uint16_t)(bits + 257U);
    if (!tinf_read_bits(data, 5U, &bits)) {
        return false;
    }
    hdist = (uint16_t)(bits + 1U);
    if (!tinf_read_bits(data, 4U, &bits)) {
        return false;
    }
    hclen = (uint16_t)(bits + 4U);

    if (hlit > 286U || hdist > TINF_MAX_DIST_SYMBOLS || hclen > 19U) {
        return false;
    }

    for (uint16_t i = 0U; i < hclen; i++) {
        if (!tinf_read_bits(data, 3U, &bits)) {
            return false;
        }
        code_lengths[s_code_length_order[i]] = (uint8_t)bits;
    }
    if (!tinf_build_tree(&data->code_tree, code_lengths, 19U)) {
        return false;
    }

    uint16_t total = (uint16_t)(hlit + hdist);
    uint16_t index = 0U;
    while (index < total) {
        uint16_t symbol;
        uint16_t repeat;
        uint8_t value;

        if (!tinf_decode_symbol(data, &data->code_tree, &symbol)) {
            return false;
        }

        if (symbol < 16U) {
            lengths[index++] = (uint8_t)symbol;
            continue;
        }

        if (symbol == 16U) {
            if (index == 0U || !tinf_read_bits(data, 2U, &bits)) {
                return false;
            }
            repeat = (uint16_t)(bits + 3U);
            value = lengths[index - 1U];
        } else if (symbol == 17U) {
            if (!tinf_read_bits(data, 3U, &bits)) {
                return false;
            }
            repeat = (uint16_t)(bits + 3U);
            value = 0U;
        } else if (symbol == 18U) {
            if (!tinf_read_bits(data, 7U, &bits)) {
                return false;
            }
            repeat = (uint16_t)(bits + 11U);
            value = 0U;
        } else {
            return false;
        }

        if ((uint32_t)index + repeat > total) {
            return false;
        }
        while (repeat-- > 0U) {
            lengths[index++] = value;
        }
    }

    return tinf_build_tree(&data->lit_tree, lengths, hlit) &&
           tinf_build_tree(&data->dist_tree, &lengths[hlit], hdist);
}

static bool tinf_emit(tinf_data_t *data, uint8_t byte)
{
    if (data->out_index >= data->expected_output_len) {
        return false;
    }

    data->window[data->win_pos & (TINF_WINDOW_SIZE - 1U)] = byte;
    data->win_pos++;

    if (!data->out_cb(byte, data->out_index, data->user_data)) {
        return false;
    }
    data->out_index++;
    return true;
}

static bool tinf_inflate_stored(tinf_data_t *data)
{
    uint32_t value;
    uint16_t length;
    uint16_t inverse_length;

    uint8_t discard = (uint8_t)(data->bit_count & 7U);
    if (discard != 0U && !tinf_read_bits(data, discard, &value)) {
        return false;
    }
    if (!tinf_read_bits(data, 16U, &value)) {
        return false;
    }
    length = (uint16_t)value;
    if (!tinf_read_bits(data, 16U, &value)) {
        return false;
    }
    inverse_length = (uint16_t)value;
    if ((uint16_t)(length ^ 0xFFFFU) != inverse_length) {
        return false;
    }

    for (uint16_t i = 0U; i < length; i++) {
        if (!tinf_read_bits(data, 8U, &value) ||
            !tinf_emit(data, (uint8_t)value)) {
            return false;
        }
    }
    return true;
}

static bool tinf_inflate_huffman(tinf_data_t *data)
{
    for (;;) {
        uint16_t symbol;
        if (!tinf_decode_symbol(data, &data->lit_tree, &symbol)) {
            return false;
        }

        if (symbol < 256U) {
            if (!tinf_emit(data, (uint8_t)symbol)) {
                return false;
            }
            continue;
        }
        if (symbol == 256U) {
            return true;
        }
        if (symbol < 257U || symbol > 285U) {
            return false;
        }

        uint16_t length_index = (uint16_t)(symbol - 257U);
        uint32_t extra;
        if (!tinf_read_bits(data, s_length_bits[length_index], &extra)) {
            return false;
        }
        uint32_t match_length = s_length_base[length_index] + extra;

        uint16_t distance_symbol;
        if (!tinf_decode_symbol(data,
                                &data->dist_tree,
                                &distance_symbol) ||
            distance_symbol >= 30U ||
            !tinf_read_bits(data,
                            s_dist_bits[distance_symbol],
                            &extra)) {
            return false;
        }

        uint32_t distance = s_dist_base[distance_symbol] + extra;
        if (distance == 0U ||
            distance > data->out_index ||
            distance > TINF_WINDOW_SIZE) {
            return false;
        }

        for (uint32_t i = 0U; i < match_length; i++) {
            uint8_t byte =
                data->window[(data->win_pos - distance) &
                             (TINF_WINDOW_SIZE - 1U)];
            if (!tinf_emit(data, byte)) {
                return false;
            }
        }
    }
}

int zlib_decompress_raw(const uint8_t *src,
                        uint32_t src_len,
                        uint32_t expected_output_len,
                        zlib_output_cb_t out_cb,
                        void *user_data)
{
    static tinf_data_t data;
    uint32_t value;
    bool is_final;

    if (src == NULL ||
        src_len == 0U ||
        expected_output_len == 0U ||
        expected_output_len > TINF_WINDOW_SIZE ||
        out_cb == NULL) {
        return TINF_DATA_ERROR;
    }

    memset(&data, 0, sizeof(data));
    data.source = src;
    data.src_len = src_len;
    data.out_cb = out_cb;
    data.user_data = user_data;
    data.expected_output_len = expected_output_len;

    do {
        if (!tinf_read_bits(&data, 1U, &value)) {
            return TINF_DATA_ERROR;
        }
        is_final = value != 0U;

        if (!tinf_read_bits(&data, 2U, &value)) {
            return TINF_DATA_ERROR;
        }

        if (value == 0U) {
            if (!tinf_inflate_stored(&data)) {
                return TINF_DATA_ERROR;
            }
        } else {
            if (value == 1U) {
                if (!tinf_build_fixed_trees(&data)) {
                    return TINF_DATA_ERROR;
                }
            } else if (value == 2U) {
                if (!tinf_build_dynamic_trees(&data)) {
                    return TINF_DATA_ERROR;
                }
            } else {
                return TINF_DATA_ERROR;
            }

            if (!tinf_inflate_huffman(&data)) {
                return TINF_DATA_ERROR;
            }
        }
    } while (!is_final);

    if (data.out_index != expected_output_len ||
        data.src_pos != data.src_len) {
        return TINF_DATA_ERROR;
    }

    return TINF_OK;
}
