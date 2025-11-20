/*
 * @file       mw_time_utils.h
 * @brief      Common helpers for UWB ranging (DS-TWR, TDoA)
 * @version    1.0.0
 * @date       2025-09-28
 */


#ifndef __MW_RANGE_MATH_H
#define __MW_RANGE_MATH_H

/* Includes ----------------------------------------------------------- */
#include <stdint.h>

/* Public defines ----------------------------------------------------- */
#define MW_DWT_TIME_UNITS   (1.0 / (499200000.0 * 128.0)) /*!< ~15.65 ps per tick */
#define MW_SPEED_OF_LIGHT   (299792458.0)                 /*!< m/s */

/* Public function prototypes ---------------------------------------- */
/**
 * @brief Convert 40-bit DW1000 time to seconds
 */
double mw_ticks40_to_s(uint64_t t40);

/**
 * @brief Convert time-of-flight (seconds) to meters
 */
float mw_tof_to_m(double tof_s);

/**
 * @brief Reconstruct 40-bit little-endian value from byte buffer
 */
uint64_t mw_get_u64_from_40(const uint8_t b[5]);

/**
 * @brief Compute DS-TWR distance from 6 timestamps
 */
float mw_ds_twr_calc(uint64_t t1, uint64_t t2, uint64_t t3,
                     uint64_t t4, uint64_t t5, uint64_t t6);

#endif /* __MW_RANGE_MATH_H */

/* End of file -------------------------------------------------------- */
