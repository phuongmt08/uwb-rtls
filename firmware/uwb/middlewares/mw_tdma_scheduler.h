/* ============================== mw_tdma_scheduler.h ==============================
 * @file       mw_tdma_scheduler.h
 * @brief      TDMA Time Slot Scheduler
 * @author     Phuong Mai
 * @version    2.0.0
 * @date       2026-02-01
 */

#ifndef MW_TDMA_SCHEDULER_H
#define MW_TDMA_SCHEDULER_H

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif


#define TDMA_PROCESSING_MARGIN_US    400      /* SPI + HAL overhead (300-500µs) */
#define TDMA_CLOCK_GUARD_US          300      /* Clock drift + PHY jitter (200-500µs) */

#define TDMA_DEFAULT_GUARD_TIME_US   2000
/* ====================================================================
 * TIMING CONSTANTS
 * ==================================================================== */
#define TDMA_MAX_ANCHORS             8

#define TDMA_DEFAULT_SLOT_DURATION_US      3000   /* 3.0ms payload window per slot */
#define TDMA_DEFAULT_POLL_TO_RESP_DELAY_US 3000   /* 3.0ms delay */
#define TDMA_DEFAULT_RESP_TO_FINAL_DELAY_US 7000  /* 5ms — to give TAG enough headroom to build FINAL message
                                                   * after RESP loop without hitting
                                                   * ensure_future_tx guard (1500µs).
                                                   * With 4 anchors, loop ends at ~+22000µs,
                                                   * build+TX takes ~300µs, FINAL planned at
                                                   * +25000µs → ahead=2700µs > 1500µs guard. */
#define TDMA_DEFAULT_FINAL_TO_RESULT_DELAY_US 9000 /* NOTE: 8.0ms — Anchor slot 1 offset = 6500+3500 = 10000µs.
                                                    * Processing from FINAL RX to ensure_future_tx
                                                    * takes ~7400µs (bsp_uwb_rx SPI/LDE/RSSI ~1500µs
                                                    * + data extract + calculate_distance ~5900µs).
                                                    * Budget = 10000 - 1500 guard >= 8500µs >> 7400µs. */

/* DW1000 time unit conversions */
#define DW_TIME_UNIT_NS              15.65f   /* ~15.65 ps per tick */
#define DW_TICKS_PER_US              63898ULL /* Approx ticks per microsecond */
/*
 * NOTE Superframe layout:
 * +------+ +-------------------+ +-------------------+ +-------+ +-------------------+ +---------------------+
 * | TAG  | | poll_to_resp_delay| | N ANCHOR RESP     | | TAG   | | final_to_result_d | | N ANCHOR RESULT     |
 * +------+ +-------------------+ +-------------------+ +-------+ +-------------------+ +---------------------+
 * | POLL | | (4.0ms default)   | | slots 1→N         | | FINAL | | (8.0ms default)   | | slots 1→N           |
 * | tx   | |                   | | N * 3.0ms         | | tx    | |                   | | N * 3.0ms           |
 * +------+ +-------------------+ +-------------------+ +-------+ +-------------------+ +---------------------+
 *                                ^ each slot = slot_duration (2.0ms) + guard_time (1.0ms) = 3.0ms
 *
 * Full timing formula (based on current configuration):
 *   T_superframe(N) = poll_to_resp_delay
 *                   + N * (slot_duration + guard_time)
 *                   + (slot_duration + resp_to_final_delay)
 *                   + final_to_result_delay
 *                   + N * (slot_duration + guard_time)
 *                   + slot_duration (last result airtime)
 *
 *   => T_superframe(N) = 4000 + N*3000 + (2000 + 5000) + 8000 + N*3000 + 2000 (us)
 *                      = 22500 + 6000*N (us)
 *
 * Case N = 8 anchors (Maximum):
 *   T_active = 22500 + 6000*8 = 70500 us = 70.5 ms
 *
 * Case N = 6 anchors:
 *   T_active = 22500 + 6000*6 = 58500 us = 58.5 ms
 *
 * Case N = 4 anchors (Default):
 *   T_active = 22500 + 6000*4 = 46500 us = 46.5 ms
 *
 * Guard time notes:
 * - guard_time_us (1000us) is a safety gap inside each slot to absorb clock drift and processing jitter.
 * - This large guard ensures stability across all 8 possible anchor slots even with software overhead.
 */


/* ====================================================================
 * TYPES
 * ==================================================================== */

typedef enum {
    TDMA_OK = 0,
    TDMA_ERR = -1,
    TDMA_ERR_PARAM = -2,
    TDMA_ERR_NOT_INITIALIZED = -3,
    TDMA_ERR_SYNC_LOST = -4,
    TDMA_ERR_INVALID_SLOT = -5,
    TDMA_ERR_INVALID_PARAM = -6,
    TDMA_ERR_NOT_SYNCHRONIZED = -7
} tdma_err_t;

typedef enum {
    TDMA_ROLE_TAG = 0,
    TDMA_ROLE_ANCHOR = 1
} tdma_role_t;

typedef struct {
    uint8_t slot_id;
    uint8_t anchor_id;
    uint32_t slot_start_us;
    uint64_t slot_start_dw;
    bool occupied;
} tdma_slot_t;

typedef struct {
    uint8_t num_anchors;
    uint8_t anchor_ids[TDMA_MAX_ANCHORS];
    
    /* Timing parameters */
    uint32_t slot_duration_us;
    uint32_t guard_time_us;              /* TRUE guard (clock drift only) */
    uint32_t processing_margin_us;        /* NEW: Software processing time */
    uint32_t poll_to_resp_delay_us;
    uint32_t resp_to_final_delay_us;
    uint32_t final_to_result_delay_us;
    
    /* Calculated values */
    uint32_t superframe_duration_us;
    uint64_t superframe_duration_dw;
    
    /* Slot table */
    tdma_slot_t slots[TDMA_MAX_ANCHORS + 1];
    
    /* Clock drift tracking */
    int32_t clock_drift_ppm;
    uint64_t last_sync_timestamp_dw;
} tdma_schedule_t;

typedef struct {
    /* Role and ID */
    tdma_role_t role;
    uint8_t device_id;
    
    /* Schedule */
    tdma_schedule_t schedule;
    
    /* Synchronization */
    bool synchronized;
    uint64_t superframe_start_dw;
    uint8_t current_slot;
    uint32_t superframe_counter;
    uint32_t sync_timeout_ms;
    
    /* State */
    bool initialized;
} tdma_scheduler_t;

/* ====================================================================
 * UTILITY MACROS
 * ==================================================================== */

/* Convert microseconds to DW1000 time units */
static inline uint64_t tdma_us_to_dw(uint32_t us) {
    return (uint64_t)us * DW_TICKS_PER_US;
}

/* Convert DW1000 time units to microseconds */
static inline uint32_t tdma_dw_to_us(uint64_t dw) {
    return (uint32_t)(dw / DW_TICKS_PER_US);
}

/* Mask timestamp to 40 bits */
static inline uint64_t tdma_mask_40bit(uint64_t timestamp) {
    return timestamp & 0x000000FFFFFFFFFFULL;
}

/* ====================================================================
 * PUBLIC API
 * ==================================================================== */

/**
 * @brief Initialize TDMA scheduler
 */
tdma_err_t tdma_init(tdma_scheduler_t *tdma,
                     tdma_role_t role,
                     uint8_t device_id,
                     uint8_t num_anchors,
                     const uint8_t *anchor_ids);

/**
 * @brief Set custom timing parameters
 */
tdma_err_t tdma_set_timing(tdma_scheduler_t *tdma,
                           uint32_t slot_duration_us,
                           uint32_t guard_time_us,
                           uint32_t poll_to_resp_delay_us,
                           uint32_t resp_to_final_delay_us);

/**
 * @brief Start new superframe (TAG only)
 */
tdma_err_t tdma_start_superframe(tdma_scheduler_t *tdma, uint64_t current_time_dw);

/**
 * @brief Synchronize to POLL reception (ANCHOR only)
 */
tdma_err_t tdma_sync_to_poll(tdma_scheduler_t *tdma, uint64_t poll_rx_timestamp);

/**
 * @brief Get slot information for specific anchor
 */
tdma_err_t tdma_get_slot_for_anchor(const tdma_scheduler_t *tdma,
                                    uint8_t anchor_id,
                                    tdma_slot_t *slot_info);

/**
 * @brief Calculate when anchor should transmit response
 */
tdma_err_t tdma_calculate_response_time(const tdma_scheduler_t *tdma,
                                        uint8_t anchor_id,
                                        uint64_t *tx_timestamp_dw);

/**
 * @brief Calculate when TAG expects to receive response
 */
tdma_err_t tdma_calculate_expected_response_time(const tdma_scheduler_t *tdma,
                                                 uint8_t anchor_id,
                                                 uint64_t *rx_timestamp_dw);

/**
 * @brief Calculate when TAG should transmit FINAL
 */
tdma_err_t tdma_calculate_final_time(const tdma_scheduler_t *tdma,
                                     uint8_t num_responses,
                                     uint64_t *tx_timestamp_dw);

/**
 * @brief Check if synchronized
 */
bool tdma_is_synchronized(const tdma_scheduler_t *tdma);

/**
 * @brief Update clock drift estimate
 */
tdma_err_t tdma_update_clock_drift(tdma_scheduler_t *tdma,
                                   uint64_t measured_interval_dw,
                                   uint64_t expected_interval_dw);

/**
 * @brief Get current slot number
 */
int tdma_get_current_slot(const tdma_scheduler_t *tdma);

/**
 * @brief Reset scheduler state
 */
void tdma_reset(tdma_scheduler_t *tdma);

/**
 * @brief Calculate RX timeout for slot (accounting for processing margin)
 * NEW: Now properly accounts for processing margin separately
 */
uint32_t tdma_calculate_slot_rx_timeout(const tdma_scheduler_t *tdma, uint8_t slot_id);

/**
 * @brief Calculate slot end time
 */
tdma_err_t tdma_calculate_slot_end_time(const tdma_scheduler_t *tdma,
                                        uint8_t slot_id,
                                        uint64_t *slot_end_dw);

/**
 * @brief Check if current time is within slot boundaries
 */
bool tdma_is_in_slot(const tdma_scheduler_t *tdma,
                     uint8_t slot_id,
                     uint64_t current_time_dw);

/**
 * @brief NEW: Calculate slot start time for RX synchronization
 * Used for FIX #3 - slot-synchronized RX windows
 */
tdma_err_t tdma_calculate_slot_start_time(const tdma_scheduler_t *tdma,
                                          uint8_t slot_id,
                                          uint64_t *slot_start_dw);

/**
 * @brief NEW: Get RX window (start, end) for anchor slot
 * Used by TAG to listen for RESP in TDMA slot window
 */
tdma_err_t tdma_get_slot_rx_window(const tdma_scheduler_t *tdma,
                                   uint8_t anchor_id,
                                   uint64_t *rx_start_dw,
                                   uint64_t *rx_end_dw);

#ifdef __cplusplus
}
#endif

#endif /* MW_TDMA_SCHEDULER_H */
