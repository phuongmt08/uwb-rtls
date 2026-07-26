/**
 * @file    zlib_decoder.h
 * @brief   One-shot raw DEFLATE decoder for independent FOTA blocks.
 *
 * The host limits the DEFLATE window to 4 KB (wbits=-12), so the decoder
 * never needs the 32 KB RFC maximum window.
 */

#ifndef ZLIB_DECODER_H
#define ZLIB_DECODER_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define TINF_OK          0
#define TINF_DATA_ERROR -1

#define TINF_WINDOW_BITS 12U
#define TINF_WINDOW_SIZE (1U << TINF_WINDOW_BITS)

typedef bool (*zlib_output_cb_t)(uint8_t byte,
                                 uint32_t output_index,
                                 void *user_data);

int zlib_decompress_raw(const uint8_t *src,
                        uint32_t src_len,
                        uint32_t expected_output_len,
                        zlib_output_cb_t out_cb,
                        void *user_data);

#endif /* ZLIB_DECODER_H */
