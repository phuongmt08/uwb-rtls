#include "v_platform.h"
#include "cmsis_os2.h"
#include <string.h>
#include <math.h>

osMessageQueueId_t g_imu_data_queue = NULL;
osMessageQueueId_t g_uwb_distance_queue = NULL;
bool g_sil_verbose_logging = false;

#ifdef _WIN32
void __chkstk(void) {}
#endif

float fmodf(float x, float y)
{
    return (float)fmod((double)x, (double)y);
}

int __fpclassifyf(float x)
{
    uint32_t u = 0;
    memcpy(&u, &x, sizeof(u));
    uint32_t exp = (u >> 23) & 0xFFU;
    uint32_t mant = u & 0x7FFFFFU;
    if (exp == 0U) {
        return (mant == 0U) ? 2 /* FP_ZERO */ : 3 /* FP_SUBNORMAL */;
    }
    if (exp == 0xFFU) {
        return (mant == 0U) ? 1 /* FP_INFINITE */ : 0 /* FP_NAN */;
    }
    return 4 /* FP_NORMAL */;
}

int __fpclassifyd(double x)
{
    uint64_t u = 0;
    memcpy(&u, &x, sizeof(u));
    uint64_t exp = (u >> 52) & 0x7FFULL;
    uint64_t mant = u & 0xFFFFFFFFFFFFFULL;
    if (exp == 0ULL) {
        return (mant == 0ULL) ? 2 /* FP_ZERO */ : 3 /* FP_SUBNORMAL */;
    }
    if (exp == 0x7FFULL) {
        return (mant == 0ULL) ? 1 /* FP_INFINITE */ : 0 /* FP_NAN */;
    }
    return 4 /* FP_NORMAL */;
}

bool sys_logger_write_record(const void *rec)
{
    (void)rec;
    return true;
}

#include <sys/reent.h>
static char s_reent_stub[1024] = {0};
struct _reent *_impure_ptr = (struct _reent *)s_reent_stub;

void v_platform_init(void)
{
    v_rtos_init();
    v_hal_init();
    v_imu_init();
    v_sys_config_init_default();

    if (g_imu_data_queue == NULL)
    {
        g_imu_data_queue = osMessageQueueNew(16U, sizeof(bsp_imu_data_t), NULL);
    }
    else
    {
        (void)osMessageQueueReset(g_imu_data_queue);
    }

    if (g_uwb_distance_queue == NULL)
    {
        g_uwb_distance_queue = osMessageQueueNew(8U, sizeof(uwb_distance_msg_t), NULL);
    }
    else
    {
        (void)osMessageQueueReset(g_uwb_distance_queue);
    }
}
