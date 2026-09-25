#ifndef SYS_LOGGER_H_
#define SYS_LOGGER_H_

#include <stdio.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

#define LOG_OBJECT_CODE_TAG 0

extern bool g_sil_verbose_logging;

#define RLOG_I(tag, fmt, ...) do { \
    if (g_sil_verbose_logging) { \
        printf("[SIL INFO] " fmt "\n", ##__VA_ARGS__); \
    } \
} while(0)

#define RLOG_W(tag, fmt, ...) do { \
    if (g_sil_verbose_logging) { \
        printf("[SIL WARN] " fmt "\n", ##__VA_ARGS__); \
    } \
} while(0)

#define RLOG_E(tag, fmt, ...) do { \
    printf("[SIL ERR]  " fmt "\n", ##__VA_ARGS__); \
} while(0)

#define SYSVIEW_START(marker) ((void)0)
#define SYSVIEW_STOP(marker)  ((void)0)

#ifdef __cplusplus
}
#endif

#endif /* SYS_LOGGER_H_ */
