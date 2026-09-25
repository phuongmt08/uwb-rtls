#ifndef OTP_H_
#define OTP_H_

#include <stdint.h>
#include <stdbool.h>

typedef enum {
    OTP_OK = 0,
    OTP_ERR_FULL = -1,
    OTP_ERR_NOT_FOUND = -2,
    OTP_ERR_MAP_VERSION = -3,
    OTP_ERR_FLASH_ACCESS = -4,
    OTP_ERR_INVALID_ARG = -5,
} otp_err_t;

#endif /* OTP_H_ */
