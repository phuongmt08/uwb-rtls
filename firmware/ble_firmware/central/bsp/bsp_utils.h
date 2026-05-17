/**
 * @file    bsp_utils.h
 * @brief   Miscellaneous BSP utilities.
 *
 * Shared helper types, macros, and utility functions used across the
 * BSP and application layers.
 */

#ifndef BSP_UTILS_H
#define BSP_UTILS_H

#include <stdint.h>
#include <stdbool.h>
#include <string.h>

#ifdef __cplusplus
extern "C" {
#endif

/* -------------------------------------------------------------------------
 * Utility macros
 * ---------------------------------------------------------------------- */

/** Safe minimum — evaluates each argument exactly once. */
#define BSP_MIN(a, b)  (((a) < (b)) ? (a) : (b))

/** Safe maximum — evaluates each argument exactly once. */
#define BSP_MAX(a, b)  (((a) > (b)) ? (a) : (b))

/** Compute number of elements in a statically-declared array. */
#define BSP_ARRAY_SIZE(arr)  (sizeof(arr) / sizeof((arr)[0]))

#ifdef __cplusplus
}
#endif

#endif /* BSP_UTILS_H */
