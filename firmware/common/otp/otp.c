/**
 * @file       otp.c
 * @brief      One-Time Programmable (OTP) Memory Driver with Flash Mocking
 * @details    Provides a TLV-based append-only key-value storage over STM32F4
 *             OTP memory (or simulated Flash memory block).
 */

#include "otp/otp.h"
#include "stm32f4xx_hal.h"
#include <string.h>

#ifdef BOOTLOADER
    #include "sys_logger_bl.h"
#else
    #include "sys_logger.h"
#endif

#define OBJECT_CODE             LOG_OBJECT_CODE_SYS_CFG

/* Keep track of the next available empty cell index for writing (descending from 63 down to 0) */
static int s_next_write_idx = -1;
static bool s_otp_initialized = false;

static bool otp_cell_is_blank(uint32_t cell_addr)
{
    for (uint32_t i = 0; i < OTP_CELL_SIZE; i++)
    {
        if (*(volatile uint8_t *)(cell_addr + i) != 0xFFu)
        {
            return false;
        }
    }
    return true;
}

static void otp_ensure_init(void)
{
    if (!s_otp_initialized)
    {
        otp_init();
    }
}

void otp_init(void)
{
    s_next_write_idx = -1;

    /* A partially written record is not valid until version is programmed, but
     * it still consumes a physical cell because other bytes may no longer be 0xFF. */
    int first_used_idx = (int)OTP_CELLS;
    for (int i = 0; i < (int)OTP_CELLS; i++)
    {
        uint32_t cell_addr = OTP_BASE_ADDR + i * OTP_CELL_SIZE;
        if (!otp_cell_is_blank(cell_addr))
        {
            first_used_idx = i;
            break;
        }
    }

    /* Next write index is the cell immediately below the first used cell */
    s_next_write_idx = first_used_idx - 1;
    s_otp_initialized = true;
}

otp_err_t otp_get(uint8_t type, void *value, uint8_t value_capacity, uint8_t *length)
{
    if (!length || !value || value_capacity == 0u)
    {
        return OTP_ERR_INVALID_ARG;
    }

    otp_ensure_init();

    /* If no cells are used, we won't find anything */
    int start_idx = s_next_write_idx + 1;
    if (start_idx < 0)
    {
        start_idx = 0;
    }
    if (start_idx >= (int)OTP_CELLS)
    {
        return OTP_ERR_NOT_FOUND;
    }

    /* Scan ascending from the first used cell upwards to OTP_CELLS - 1.
     * Scanning upwards ensures that we find the cell at the lowest index first,
     * which corresponds to the most recently written/appended value. */
    for (int i = start_idx; i < (int)OTP_CELLS; i++)
    {
        uint32_t cell_addr = OTP_BASE_ADDR + i * OTP_CELL_SIZE;
        uint8_t version   = *(volatile uint8_t *)(cell_addr + OTP_IDX_VERSION);
        uint8_t cell_type = *(volatile uint8_t *)(cell_addr + OTP_IDX_TYPE);
        uint8_t cell_len  = *(volatile uint8_t *)(cell_addr + OTP_IDX_LEN);

        if (version == OTP_MAP_VERSION && cell_type == type)
        {
            if (cell_len > (OTP_CELL_SIZE - OTP_HEADER_LEN))
            {
                return OTP_ERR_MAP_VERSION; /* Cell header/length is corrupted */
            }
            if (cell_len > value_capacity)
            {
                *length = cell_len;
                return OTP_ERR_INVALID_ARG;
            }

            *length = cell_len;
            /* Copy data from flash byte-by-byte */
            uint8_t *dst = (uint8_t *)value;
            for (uint8_t d = 0; d < cell_len; d++)
            {
                dst[d] = *(volatile uint8_t *)(cell_addr + OTP_HEADER_LEN + d);
            }
            return OTP_OK;
        }
    }

    return OTP_ERR_NOT_FOUND;
}

otp_err_t otp_set(uint8_t type, uint8_t length, const void *value)
{
    if (!value)
    {
        return OTP_ERR_INVALID_ARG;
    }

    if (type == 0u || length == 0u || length > (OTP_CELL_SIZE - OTP_HEADER_LEN))
    {
        return OTP_ERR_INVALID_ARG;
    }
    otp_ensure_init();

    /* Check if storage is full */
    if (s_next_write_idx < 0)
    {
        RLOG_E(OBJECT_CODE, ERR_FLASH_PROGRAM, "OTP: Storage is full, cannot write type 0x%02X", type);
        return OTP_ERR_FULL;
    }

    /* Construct the 8-byte cell payload. Version stays erased until commit. */
    uint8_t cell_data[OTP_CELL_SIZE];
    memset(cell_data, 0xFF, sizeof(cell_data));
    cell_data[OTP_IDX_VERSION] = OTP_MAP_VERSION;
    cell_data[OTP_IDX_TYPE]    = type;
    cell_data[OTP_IDX_LEN]     = length;
    memcpy(&cell_data[OTP_HEADER_LEN], value, length);

    uint32_t cell_addr = OTP_BASE_ADDR + s_next_write_idx * OTP_CELL_SIZE;

    RLOG_W(OBJECT_CODE, "OTP: One-time write type=0x%02X len=%u cell=%d addr=0x%08X",
           type, length, s_next_write_idx, (unsigned int)cell_addr);

    /* Write cell byte-by-byte using HAL FLASH driver */
    HAL_StatusTypeDef status = HAL_OK;
    
    HAL_FLASH_Unlock();

    /* Clear standard pending flash status flags to avoid writing issues */
    __HAL_FLASH_CLEAR_FLAG(FLASH_FLAG_EOP | FLASH_FLAG_OPERR | FLASH_FLAG_WRPERR |
                           FLASH_FLAG_PGAERR | FLASH_FLAG_PGPERR | FLASH_FLAG_PGSERR);

    uint32_t programmed_len = OTP_HEADER_LEN + (uint32_t)length;
    for (uint32_t i = OTP_IDX_TYPE; i < programmed_len; i++)
    {
        status = HAL_FLASH_Program(FLASH_TYPEPROGRAM_BYTE, cell_addr + i, cell_data[i]);
        if (status != HAL_OK)
        {
            break;
        }
    }
    if (status == HAL_OK)
    {
        status = HAL_FLASH_Program(FLASH_TYPEPROGRAM_BYTE, cell_addr + OTP_IDX_VERSION, cell_data[OTP_IDX_VERSION]);
    }

    HAL_FLASH_Lock();

    if (status != HAL_OK)
    {
        RLOG_E(OBJECT_CODE, ERR_FLASH_PROGRAM, "OTP: Flash program failed at addr 0x%08X", (unsigned int)cell_addr);
        if (!otp_cell_is_blank(cell_addr))
        {
            s_next_write_idx--;
        }
        return OTP_ERR_FLASH_ACCESS;
    }

    /* Read back and verify the entire cell */
    for (uint32_t i = 0; i < OTP_CELL_SIZE; i++)
    {
        if (*(volatile uint8_t *)(cell_addr + i) != cell_data[i])
        {
            RLOG_E(OBJECT_CODE, ERR_FLASH_VERIFY, "OTP: Verify failed at addr 0x%08X", (unsigned int)(cell_addr + i));
            if (!otp_cell_is_blank(cell_addr))
            {
                s_next_write_idx--;
            }
            return OTP_ERR_FLASH_ACCESS;
        }
    }

    /* Successfully written, decrement next index */
    s_next_write_idx--;

    return OTP_OK;
}

otp_err_t otp_debug_reset_mock(void)
{
#if MOCK_OTP_IN_FLASH
    /* Erasing Sector 5 during application execution is dangerous since code is active there.
     * Re-flashing via programmer natively resets this sector. We warn and report skip. */
    RLOG_W(OBJECT_CODE, "OTP: Sector 5 reset skipped in runtime to avoid application execution stall");
    return OTP_OK;
#else
    RLOG_E(OBJECT_CODE, ERR_FLASH_ERASE, "OTP: Real physical OTP cannot be erased/reset");
    return OTP_ERR_FLASH_ACCESS;
#endif
}

void otp_test_run(void)
{
#if MOCK_OTP_IN_FLASH && OTP_ENABLE_FLASH_SELF_TEST
    RLOG_I(OBJECT_CODE, "=== Starting OTP Automatic Self-Test ===");

    otp_init();
    RLOG_I(OBJECT_CODE, "OTP: Initialized. Next write index = %d", s_next_write_idx);

    uint8_t payload[5] = {1u, 2u, 3u, 4u, 5u};
    uint8_t readback[5] = {0};
    uint8_t len = 0u;
    const uint8_t test_type = 0x7Eu;

    otp_err_t err = otp_get(test_type, readback, sizeof(readback), &len);
    if (err == OTP_OK && len == sizeof(payload) && memcmp(readback, payload, sizeof(payload)) == 0) {
        RLOG_I(OBJECT_CODE, "OTP Test: existing raw TLV readback PASSED; skipping program");
        RLOG_I(OBJECT_CODE, "=== OTP Automatic Self-Test Completed ===");
        return;
    }

    err = otp_set(test_type, sizeof(payload), payload);
    if (err != OTP_OK) {
        RLOG_E(OBJECT_CODE, ERR_FLASH_PROGRAM, "OTP Test: raw write failed (%d)", err);
        return;
    }

    err = otp_get(test_type, readback, sizeof(readback), &len);
    if (err != OTP_OK || len != sizeof(payload) || memcmp(readback, payload, sizeof(payload)) != 0) {
        RLOG_E(OBJECT_CODE, ERR_FLASH_VERIFY, "OTP Test: raw readback failed (%d)", err);
        return;
    }

    RLOG_I(OBJECT_CODE, "OTP Test: raw TLV write/read PASSED");

    RLOG_I(OBJECT_CODE, "=== OTP Automatic Self-Test Completed ===");
#else
    otp_init();
    RLOG_I(OBJECT_CODE, "OTP self-test disabled (MOCK_OTP_IN_FLASH=%d, OTP_ENABLE_FLASH_SELF_TEST=%d)",
           MOCK_OTP_IN_FLASH, OTP_ENABLE_FLASH_SELF_TEST);
#endif
}
