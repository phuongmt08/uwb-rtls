/**
 * @file       sys_flash_storage.c
 * @version    1.0.0
 * @date       2026-03-05
 * @author     Phuong Mai
 * @brief      Shared dual-sector flash storage singleton
 */

/* Includes ----------------------------------------------------------------- */
#include "sys_flash_storage.h"
#include "sys_logger.h"
#include "bsp_util.h"
#include <string.h>
#include "config.h"

#ifdef HAVE_FLASH_STORAGE
#include "stm32f4xx_hal.h"
#endif

/* Private defines ---------------------------------------------------------- */
#ifdef HAVE_FLASH_STORAGE
#ifdef HAVE_RTC
#  define SYS_FLASH_TIMESTAMP_FN   bsp_rtc_get_timestamp_s
#else
#  define SYS_FLASH_TIMESTAMP_FN   HAL_GetTick
#endif
#endif /* HAVE_FLASH_STORAGE */

/* Private variables -------------------------------------------------------- */
#ifdef HAVE_FLASH_STORAGE
static bsp_flash_dual_t g_flash_dual;
static uint8_t          g_initialized = 0u;
#endif

/* Private function prototypes ---------------------------------------------- */
int sys_flash_storage_init(void)
{
#ifdef HAVE_FLASH_STORAGE
    if (g_initialized)
    {
        return 0;
    }

    /* Initialise CRC / RTC hardware (safe to call multiple times) */
    if (bsp_util_init() != BSP_UTIL_OK)
    {
        RLOG_W(LOG_OBJECT_CODE_SYS_CFG, "bsp_util_init failed — CRC/RTC may be unavailable");
    }

    bsp_flash_status_t status = bsp_flash_dual_init(
        &g_flash_dual,
        SYS_FLASH_SECTOR0_BASE, SYS_FLASH_SECTOR0_SIZE,
        SYS_FLASH_SECTOR1_BASE, SYS_FLASH_SECTOR1_SIZE,
        bsp_crc32,
        SYS_FLASH_TIMESTAMP_FN
    );

    if (status != BSP_FLASH_OK)
    {
        RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_HAL,
               "sys_flash_storage_init: bsp_flash_dual_init failed (%d)", status);
        return -1;
    }

    g_initialized = 1u;
    RLOG_I(LOG_OBJECT_CODE_SYS_CFG, "Flash storage initialised (S6+S7 dual-sector)");
    return 0;
#else
    return 0;   /* No-op in RAM-only builds */
#endif
}

bsp_flash_dual_t *sys_flash_storage_get(void)
{
#ifdef HAVE_FLASH_STORAGE
    if (!g_initialized)
    {
        return NULL;
    }
    return &g_flash_dual;
#else
    return NULL;
#endif
}

/* ── Config partition helpers ─────────────────────────────────────────────── */

bsp_flash_status_t sys_flash_cfg_write(const void *data, uint32_t size)
{
#ifdef HAVE_FLASH_STORAGE
    bsp_flash_dual_t *fh = sys_flash_storage_get();
    if (!fh)
        return BSP_FLASH_ERR_NULL_PTR;
    return bsp_flash_cfg_write(fh, data, size);
#else
    (void)data; (void)size;
    return BSP_FLASH_OK;
#endif
}

uint32_t sys_flash_cfg_read(void *out, uint32_t max_size)
{
#ifdef HAVE_FLASH_STORAGE
    bsp_flash_dual_t *fh = sys_flash_storage_get();
    if (!fh)
        return 0u;
    return bsp_flash_cfg_read(fh, out, max_size);
#else
    (void)out; (void)max_size;
    return 0u;
#endif
}

/* ── Log partition helpers (write pointer owned by sys_logger) ─────────── */

bsp_flash_status_t sys_flash_log_write_at(uint32_t    log_read_pos,
                                           const void *data,
                                           uint32_t    size,
                                           uint32_t   *out_actual_pos)
{
#ifdef HAVE_FLASH_STORAGE
    bsp_flash_dual_t *fh = sys_flash_storage_get();
    if (!fh || !data || size == 0u)
        return BSP_FLASH_ERR_NULL_PTR;
    return bsp_flash_log_append(fh, log_read_pos, data, size, out_actual_pos);
#else
    (void)log_read_pos; (void)data; (void)size; (void)out_actual_pos;
    return BSP_FLASH_OK;
#endif
}

bsp_flash_status_t sys_flash_log_update_read_pos(uint32_t read_pos)
{
#ifdef HAVE_FLASH_STORAGE
    bsp_flash_dual_t *fh = sys_flash_storage_get();
    if (!fh)
        return BSP_FLASH_ERR_NULL_PTR;
    return bsp_flash_log_update_read_pos(fh, read_pos);
#else
    (void)read_pos;
    return BSP_FLASH_OK;
#endif
}

bsp_flash_status_t sys_flash_log_get_positions(uint32_t *out_write_pos, uint32_t *out_read_pos)
{
#ifdef HAVE_FLASH_STORAGE
    bsp_flash_dual_t *fh = sys_flash_storage_get();
    if (!fh)
        return BSP_FLASH_ERR_NULL_PTR;
    return bsp_flash_log_get_positions(fh, out_write_pos, out_read_pos);
#else
    if (out_write_pos) *out_write_pos = 0u;
    if (out_read_pos)  *out_read_pos  = 0u;
    return BSP_FLASH_OK;
#endif
}

uint32_t sys_flash_log_read(void *out, uint32_t offset, uint32_t length)
{
#ifdef HAVE_FLASH_STORAGE
    bsp_flash_dual_t *fh = sys_flash_storage_get();
    if (!fh || !out || length == 0u)
        return 0u;

    if (offset >= BSP_FLASH_LOG_DATA_LENGTH)
        return 0u;

    uint32_t avail     = BSP_FLASH_LOG_DATA_LENGTH - offset;
    uint32_t copy_len  = (length > avail) ? avail : length;
    uint32_t read_addr = fh->sectors[fh->active].base
                       + BSP_FLASH_METADATA_SIZE
                       + BSP_FLASH_LOG_DATA_OFFSET
                       + offset;
    memcpy(out, (const void *)read_addr, copy_len);
    return copy_len;
#else
    (void)out; (void)offset; (void)length;
    return 0u;
#endif
}

/* End of file -------------------------------------------------------------- */
