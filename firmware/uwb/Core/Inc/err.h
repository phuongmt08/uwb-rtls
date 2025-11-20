/**
 * @file       err.h
 * @brief      Error checking macros for embedded drivers
 * @version    1.0.0
 * @date       2025
 */

#ifndef __ERR_H
#define __ERR_H

#include <assert.h> /* For debug-time checks */

/**
 * @brief Check expression, return error code if false
 * @param expr      Condition to check
 * @param err_code  Error code to return if check fails
 */
#define CHECK_ERR(expr, err_code) \
  do                              \
  {                               \
    if (!(expr))                  \
      return (err_code);          \
  } while (0)

/**
 * @brief Check parameter validity: assert in debug, return error if invalid
 * @param expr      Condition to check
 * @param err_code  Error code to return if check fails
 */
#define CHECK_PARAM(expr, err_code) \
  do                                \
  {                                 \
    if (!(expr))                    \
    {                               \
      assert(expr);                 \
      return (err_code);            \
    }                               \
  } while (0)
  
#define CHECK_VOID(condition) \
	do { \
		if (!(condition)) return; \
	} while(0)
#endif /* __ERR_H */
