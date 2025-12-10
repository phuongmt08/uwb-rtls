/*
 * @file       mw_time_utils.h
 * @brief      Common helpers for UWB ranging (DS-TWR, TDoA)
 * @version    1.0.0
 * @date       2025-09-28
 */

#include "mw_time_utils.h"

double mw_ticks40_to_s(uint64_t t40)
{
  return (double)(t40 & 0x000000FFFFFFFFULL) * MW_DWT_TIME_UNITS;
}

float mw_tof_to_m(double tof_s)
{
  return (float)(tof_s * MW_SPEED_OF_LIGHT);
}

uint64_t mw_get_u64_from_40(const uint8_t b[5])
{
  return (uint64_t)b[0]
       | ((uint64_t)b[1] << 8)
       | ((uint64_t)b[2] << 16)
       | ((uint64_t)b[3] << 24)
       | ((uint64_t)b[4] << 32);
}

float mw_ds_twr_calc(uint64_t t1, uint64_t t2, uint64_t t3,
                     uint64_t t4, uint64_t t5, uint64_t t6)
{
  uint64_t Ra = (t4 - t1) & 0x000000FFFFFFFFULL;
  uint64_t Rb = (t6 - t3) & 0x000000FFFFFFFFULL;
  uint64_t Da = (t3 - t2) & 0x000000FFFFFFFFULL;
  uint64_t Db = (t5 - t4) & 0x000000FFFFFFFFULL;

  double Ra_s = mw_ticks40_to_s(Ra);
  double Rb_s = mw_ticks40_to_s(Rb);
  double Da_s = mw_ticks40_to_s(Da);
  double Db_s = mw_ticks40_to_s(Db);

  double num = (Ra_s * Rb_s) - (Da_s * Db_s);
  double den = (Ra_s + Rb_s + Da_s + Db_s);
  if (den <= 0.0) return -1.0f;

  double tof_s = num / den;
  return mw_tof_to_m(tof_s);
}

/* End of file -------------------------------------------------------- */
