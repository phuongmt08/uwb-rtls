/* ============================== mw_tdma_scheduler.c ==============================
 * @file       mw_tdma_scheduler.c
 * @brief      TDMA Time Slot Scheduler
 * @version    2.0.0
 * @date       2026-02-01
 * 
 */

#include "mw_tdma_scheduler.h"
#include "sys_logger.h"
#include <string.h>

/* Private functions -------------------------------------------------- */

/**
 * @brief Build slot allocation table
 */
static void build_slot_table(tdma_scheduler_t *tdma)
{
    tdma_schedule_t *sched = &tdma->schedule;
    
    /* Clear all slots */
    memset(sched->slots, 0, sizeof(sched->slots));
    
    /* Slot 0: TAG POLL (broadcast) */
    sched->slots[0].slot_id = 0;
    sched->slots[0].anchor_id = 0xFF;  /* Broadcast */
    sched->slots[0].slot_start_us = 0;
    sched->slots[0].slot_start_dw = 0;
    sched->slots[0].occupied = true;
    
    /* Slots 1-N: Anchor responses */
    for (uint8_t i = 0; i < sched->num_anchors && i < TDMA_MAX_ANCHORS; i++) {
        uint8_t slot_idx = i + 1;  /* Slot 1, 2, 3... */
        
        uint32_t effective_slot_us = sched->slot_duration_us + sched->guard_time_us;
        
        sched->slots[slot_idx].slot_id = slot_idx;
        sched->slots[slot_idx].anchor_id = sched->anchor_ids[i];
        sched->slots[slot_idx].slot_start_us = slot_idx * effective_slot_us;
        sched->slots[slot_idx].slot_start_dw = tdma_us_to_dw(sched->slots[slot_idx].slot_start_us);
        sched->slots[slot_idx].occupied = true;
    }
}

/* Public API --------------------------------------------------------- */

tdma_err_t tdma_init(tdma_scheduler_t *tdma,
                     tdma_role_t role,
                     uint8_t device_id,
                     uint8_t num_anchors,
                     const uint8_t *anchor_ids)
{
    if (!tdma) return TDMA_ERR_PARAM;
    if (num_anchors == 0 || num_anchors > TDMA_MAX_ANCHORS) return TDMA_ERR_PARAM;
    if (!anchor_ids) return TDMA_ERR_PARAM;
    
    /* Clear context */
    memset(tdma, 0, sizeof(tdma_scheduler_t));
    
    /* Set role and ID */
    tdma->role = role;
    tdma->device_id = device_id;
    
    /* Initialize schedule with defaults */
    tdma_schedule_t *sched = &tdma->schedule;
    sched->num_anchors = num_anchors;
    memcpy(sched->anchor_ids, anchor_ids, num_anchors);
    
    sched->slot_duration_us = TDMA_DEFAULT_SLOT_DURATION_US;
    
    sched->guard_time_us = TDMA_DEFAULT_GUARD_TIME_US;
    
    sched->processing_margin_us = TDMA_PROCESSING_MARGIN_US; 
    
    sched->poll_to_resp_delay_us = TDMA_DEFAULT_POLL_TO_RESP_DELAY_US;
    sched->resp_to_final_delay_us = TDMA_DEFAULT_RESP_TO_FINAL_DELAY_US;
    sched->final_to_result_delay_us = TDMA_DEFAULT_FINAL_TO_RESULT_DELAY_US;
    
    uint32_t effective_slot_us = sched->slot_duration_us + sched->guard_time_us;
    sched->superframe_duration_us = effective_slot_us * (num_anchors + 1);
    sched->superframe_duration_dw = tdma_us_to_dw(sched->superframe_duration_us);
    
    build_slot_table(tdma);
    
    tdma->synchronized = false;
    tdma->sync_timeout_ms = 1000;
    
    tdma->initialized = true;
    
    RLOG_I(LOG_OBJECT_CODE_RANGING, "[TDMA] Initialized: %u anchors, guard=%luµs (NOT %lums!), superframe=%luµs",
           num_anchors, (unsigned long)sched->guard_time_us, 
           (unsigned long)(sched->guard_time_us/1000), 
           (unsigned long)sched->superframe_duration_us);
    
    return TDMA_OK;
}

tdma_err_t tdma_set_timing(tdma_scheduler_t *tdma,
                           uint32_t slot_duration_us,
                           uint32_t guard_time_us,
                           uint32_t poll_to_resp_delay_us,
                           uint32_t resp_to_final_delay_us)
{
    if (!tdma || !tdma->initialized) return TDMA_ERR_NOT_INITIALIZED;
    
    if (guard_time_us < TDMA_MIN_GUARD_TIME_US) {
        RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING,
               "[TDMA] Guard time too small (%luµs < %luµs) - AUTO-CORRECTING",
               (unsigned long)guard_time_us, (unsigned long)TDMA_MIN_GUARD_TIME_US);
        guard_time_us = TDMA_MIN_GUARD_TIME_US;
    }
    
    if (guard_time_us > TDMA_WARN_GUARD_TIME_US) {
        RLOG_W(LOG_OBJECT_CODE_RANGING,
               "[TDMA] WARNING: Guard time %luµs > %luµs! This suggests system is NOT true TDMA.",
               (unsigned long)guard_time_us, (unsigned long)TDMA_WARN_GUARD_TIME_US);
        RLOG_W(LOG_OBJECT_CODE_RANGING,
               "[TDMA] Guard should be 200-500µs. If you need more, increase slot_duration instead.");
    }
    
    tdma_schedule_t *sched = &tdma->schedule;
    
    sched->slot_duration_us = slot_duration_us;
    sched->guard_time_us = guard_time_us;
    sched->poll_to_resp_delay_us = poll_to_resp_delay_us;
    sched->resp_to_final_delay_us = resp_to_final_delay_us;
    
    uint32_t effective_slot_us = slot_duration_us + guard_time_us;
    sched->superframe_duration_us = effective_slot_us * (sched->num_anchors + 1);
    sched->superframe_duration_dw = tdma_us_to_dw(sched->superframe_duration_us);
    
    /* Rebuild slot table */
    build_slot_table(tdma);
    
    RLOG_I(LOG_OBJECT_CODE_RANGING, "[TDMA] Timing updated: slot=%luµs, guard=%luµs",
           (unsigned long)slot_duration_us, (unsigned long)guard_time_us);
    
    return TDMA_OK;
}

tdma_err_t tdma_start_superframe(tdma_scheduler_t *tdma, uint64_t current_time_dw)
{
    if (!tdma || !tdma->initialized) return TDMA_ERR_NOT_INITIALIZED;
    if (tdma->role != TDMA_ROLE_TAG) return TDMA_ERR_PARAM;
    
    /* Set superframe start time (DW time) */
    tdma->superframe_start_dw = tdma_mask_40bit(current_time_dw);
    tdma->current_slot = 0;
    tdma->superframe_counter++;
    
    /* Mark as synchronized */
    tdma->synchronized = true;
    
    return TDMA_OK;
}

tdma_err_t tdma_sync_to_poll(tdma_scheduler_t *tdma, uint64_t poll_rx_timestamp)
{
    if (!tdma || !tdma->initialized) return TDMA_ERR_NOT_INITIALIZED;
    if (tdma->role != TDMA_ROLE_ANCHOR) return TDMA_ERR_PARAM;
    
    /* POLL RX timestamp becomes the start of the superframe */
    tdma->superframe_start_dw = tdma_mask_40bit(poll_rx_timestamp);
    tdma->current_slot = 0;
    tdma->synchronized = true;
    tdma->superframe_counter++;
    
    /* Update sync tracking */
    tdma->schedule.last_sync_timestamp_dw = tdma->superframe_start_dw;
    
    return TDMA_OK;
}

tdma_err_t tdma_get_slot_for_anchor(const tdma_scheduler_t *tdma,
                                    uint8_t anchor_id,
                                    tdma_slot_t *slot_info)
{
    if (!tdma || !tdma->initialized) return TDMA_ERR_NOT_INITIALIZED;
    if (!slot_info) return TDMA_ERR_PARAM;
    
    /* Search for anchor in slot table */
    for (uint8_t i = 1; i <= tdma->schedule.num_anchors; i++) {
        if (tdma->schedule.slots[i].anchor_id == anchor_id) {
            *slot_info = tdma->schedule.slots[i];
            return TDMA_OK;
        }
    }
    
    return TDMA_ERR_INVALID_SLOT;
}

tdma_err_t tdma_calculate_response_time(const tdma_scheduler_t *tdma,
                                        uint8_t anchor_id,
                                        uint64_t *tx_timestamp_dw)
{
    if (!tdma || !tdma->initialized) return TDMA_ERR_NOT_INITIALIZED;
    if (!tdma->synchronized) return TDMA_ERR_SYNC_LOST;
    if (!tx_timestamp_dw) return TDMA_ERR_PARAM;
    
    /* Get slot for this anchor */
    tdma_slot_t slot;
    tdma_err_t err = tdma_get_slot_for_anchor(tdma, anchor_id, &slot);
    if (err != TDMA_OK) return err;
    
    /* Calculate TX time: superframe_start + slot_start + poll_to_resp_delay */
    *tx_timestamp_dw = tdma->superframe_start_dw + 
                       slot.slot_start_dw + 
                       tdma_us_to_dw(tdma->schedule.poll_to_resp_delay_us);
    
    /* Mask to 40 bits */
    *tx_timestamp_dw = tdma_mask_40bit(*tx_timestamp_dw);
    
    return TDMA_OK;
}

tdma_err_t tdma_calculate_expected_response_time(const tdma_scheduler_t *tdma,
                                                 uint8_t anchor_id,
                                                 uint64_t *rx_timestamp_dw)
{
    if (!tdma || !tdma->initialized) return TDMA_ERR_NOT_INITIALIZED;
    if (!tdma->synchronized) return TDMA_ERR_SYNC_LOST;
    if (!rx_timestamp_dw) return TDMA_ERR_PARAM;
    
    /* Get slot for this anchor */
    tdma_slot_t slot;
    tdma_err_t err = tdma_get_slot_for_anchor(tdma, anchor_id, &slot);
    if (err != TDMA_OK) return err;
    
    /* Expected RX time = superframe_start + slot_start + poll_to_resp_delay */
    *rx_timestamp_dw = tdma->superframe_start_dw + 
                       slot.slot_start_dw + 
                       tdma_us_to_dw(tdma->schedule.poll_to_resp_delay_us);
    
    /* Mask to 40 bits */
    *rx_timestamp_dw = tdma_mask_40bit(*rx_timestamp_dw);
    
    return TDMA_OK;
}

tdma_err_t tdma_calculate_final_time(const tdma_scheduler_t *tdma,
                                     uint8_t num_responses,
                                     uint64_t *tx_timestamp_dw)
{
    if (!tdma || !tdma->initialized) return TDMA_ERR_NOT_INITIALIZED;
    if (!tdma->synchronized) return TDMA_ERR_SYNC_LOST;
    if (!tx_timestamp_dw) return TDMA_ERR_PARAM;
    
    uint32_t effective_slot_us = tdma->schedule.slot_duration_us + tdma->schedule.guard_time_us;

    /* FIX: poll_to_resp_delay_us was missing from the formula.
     * RESP slots are offset from superframe_start by poll_to_resp_delay, so
     * slot-N ends at: superframe_start + poll_to_resp_delay + N*eff
     * Without this term, FINAL was scheduled 1500us too early, coinciding
     * with the slot-N RESP TX — causing the RESP window cap to close before
     * slot-N RESP could arrive at TAG. */
    uint64_t last_anchor_slot_end_dw = tdma->superframe_start_dw +
                                        tdma_us_to_dw(tdma->schedule.poll_to_resp_delay_us) +
                                        tdma_us_to_dw(tdma->schedule.num_anchors * effective_slot_us);

    /* final_tx = last_anchor_slot_end + resp_to_final_delay */
    *tx_timestamp_dw = last_anchor_slot_end_dw +
                       tdma_us_to_dw(tdma->schedule.resp_to_final_delay_us);
    
    /* Mask to 40 bits */
    *tx_timestamp_dw = tdma_mask_40bit(*tx_timestamp_dw);
    
    return TDMA_OK;
}

bool tdma_is_synchronized(const tdma_scheduler_t *tdma)
{
    if (!tdma || !tdma->initialized) return false;
    return tdma->synchronized;
}

tdma_err_t tdma_update_clock_drift(tdma_scheduler_t *tdma,
                                   uint64_t measured_interval_dw,
                                   uint64_t expected_interval_dw)
{
    if (!tdma || !tdma->initialized) return TDMA_ERR_NOT_INITIALIZED;
    
    /* Calculate drift in ppm */
    int64_t diff = (int64_t)measured_interval_dw - (int64_t)expected_interval_dw;
    int32_t drift_ppm = (int32_t)((diff * 1000000LL) / (int64_t)expected_interval_dw);
    
    /* Simple exponential averaging */
    if (tdma->schedule.clock_drift_ppm == 0) {
        tdma->schedule.clock_drift_ppm = drift_ppm;
    } else {
        tdma->schedule.clock_drift_ppm = (tdma->schedule.clock_drift_ppm * 3 + drift_ppm) / 4;
    }
    
    return TDMA_OK;
}

int tdma_get_current_slot(const tdma_scheduler_t *tdma)
{
    if (!tdma || !tdma->initialized || !tdma->synchronized) return -1;
    return (int)tdma->current_slot;
}

void tdma_reset(tdma_scheduler_t *tdma)
{
    if (!tdma) return;
    
    tdma->superframe_start_dw = 0;
    tdma->current_slot = 0;
    tdma->superframe_counter = 0;
    tdma->synchronized = false;
}

/* ====================================================================
 * SLOT BOUNDARY FUNCTIONS
 * ==================================================================== */

uint32_t tdma_calculate_slot_rx_timeout(const tdma_scheduler_t *tdma, uint8_t slot_id)
{
    if (!tdma || !tdma->initialized) return 0;
    if (slot_id > tdma->schedule.num_anchors) return 0;
    
    uint32_t slot_duration_us = tdma->schedule.slot_duration_us;
    uint32_t guard_us = tdma->schedule.guard_time_us;
    uint32_t processing_us = tdma->schedule.processing_margin_us;
    uint32_t tx_turnaround_us = 200;  /* DW1000 TX turnaround time */
    
    uint32_t total_overhead_us = guard_us + processing_us + tx_turnaround_us;
    
    if (slot_duration_us <= total_overhead_us) {
        /* Slot too short, use minimum */
        return 1000;  /* 1ms minimum */
    }
    
    /* RX window is what's left after all overhead */
    uint32_t rx_window_us = slot_duration_us - total_overhead_us;
    
    /* Sanity check */
    if (rx_window_us < 1000) {
        rx_window_us = 1000;  /* Minimum 1ms */
    }
    
    return rx_window_us;
}

tdma_err_t tdma_calculate_slot_end_time(const tdma_scheduler_t *tdma,
                                        uint8_t slot_id,
                                        uint64_t *slot_end_dw)
{
    if (!tdma || !tdma->initialized) return TDMA_ERR_NOT_INITIALIZED;
    if (!tdma->synchronized) return TDMA_ERR_SYNC_LOST;
    if (!slot_end_dw) return TDMA_ERR_PARAM;
    if (slot_id > tdma->schedule.num_anchors) return TDMA_ERR_INVALID_SLOT;
    
    /* Slot end = superframe_start + (slot_id + 1) * (slot_duration + guard) */
    uint32_t effective_slot_us = tdma->schedule.slot_duration_us + tdma->schedule.guard_time_us;
    *slot_end_dw = tdma->superframe_start_dw + 
                   tdma_us_to_dw((slot_id + 1) * effective_slot_us);
    
    /* Mask to 40 bits */
    *slot_end_dw = tdma_mask_40bit(*slot_end_dw);
    
    return TDMA_OK;
}

tdma_err_t tdma_calculate_slot_start_time(const tdma_scheduler_t *tdma,
                                          uint8_t slot_id,
                                          uint64_t *slot_start_dw)
{
    if (!tdma || !tdma->initialized) return TDMA_ERR_NOT_INITIALIZED;
    if (!tdma->synchronized) return TDMA_ERR_SYNC_LOST;
    if (!slot_start_dw) return TDMA_ERR_PARAM;
    if (slot_id > tdma->schedule.num_anchors) return TDMA_ERR_INVALID_SLOT;
    
    /* Calculate slot start time */
    uint32_t effective_slot_us = tdma->schedule.slot_duration_us + tdma->schedule.guard_time_us;
    *slot_start_dw = tdma->superframe_start_dw + 
                     tdma_us_to_dw(slot_id * effective_slot_us);
    
    /* Mask to 40 bits */
    *slot_start_dw = tdma_mask_40bit(*slot_start_dw);
    
    return TDMA_OK;
}

bool tdma_is_in_slot(const tdma_scheduler_t *tdma,
                     uint8_t slot_id,
                     uint64_t current_time_dw)
{
    if (!tdma || !tdma->initialized || !tdma->synchronized) return false;
    if (slot_id > tdma->schedule.num_anchors) return false;
    
    /* Calculate slot boundaries with guard time */
    uint32_t effective_slot_us = tdma->schedule.slot_duration_us + tdma->schedule.guard_time_us;
    uint64_t slot_start_dw = tdma->superframe_start_dw + 
                             tdma_us_to_dw(slot_id * effective_slot_us);
    uint64_t slot_end_dw = tdma->superframe_start_dw + 
                           tdma_us_to_dw((slot_id + 1) * effective_slot_us);
    
    /* Mask to 40 bits */
    slot_start_dw = tdma_mask_40bit(slot_start_dw);
    slot_end_dw = tdma_mask_40bit(slot_end_dw);
    current_time_dw = tdma_mask_40bit(current_time_dw);
    
    /* Handle 40-bit wraparound */
    int64_t diff_start = (int64_t)((current_time_dw - slot_start_dw) & 0x000000FFFFFFFFFFULL);
    int64_t diff_end = (int64_t)((slot_end_dw - current_time_dw) & 0x000000FFFFFFFFFFULL);
    
    /* In slot if: current >= start AND current < end */
    return (diff_start >= 0) && (diff_end > 0);
}
/**
 * @brief Get RX window for TAG to listen for RESP from specific anchor
 * 
 * RX window = slot_start to (slot_end - guard - processing - tx_turnaround)
 * This ensures TAG listens at the right time without overlapping next slot
 */
tdma_err_t tdma_get_slot_rx_window(const tdma_scheduler_t *tdma,
                                   uint8_t anchor_id,
                                   uint64_t *rx_start_dw,
                                   uint64_t *rx_end_dw)
{
    if (!tdma || !rx_start_dw || !rx_end_dw) return TDMA_ERR_INVALID_PARAM;
    if (!tdma->initialized || !tdma->synchronized) return TDMA_ERR_NOT_SYNCHRONIZED;
    
    int slot_id = -1;
    for (uint8_t i = 1; i <= tdma->schedule.num_anchors; i++) {
        if (tdma->schedule.slots[i].anchor_id == anchor_id) {
            slot_id = i;  /* Anchor in slot 1-N */
            break;
        }
    }
    
    if (slot_id < 0) return TDMA_ERR_INVALID_PARAM;

    /* rx_late_margin: how long after expected_resp we still accept a frame.
     * guard_time_us already covers clock drift + PHY jitter (it is sized for that).
     * processing_margin_us covers SPI/HAL read latency after RXFCG fires.
     * TDMA_CLOCK_GUARD_US and the old hardcoded +2500us were double-counting
     * what guard_time already provides — removed. */
    uint32_t rx_early_margin_us = tdma->schedule.guard_time_us;
    uint32_t rx_late_margin_us  = tdma->schedule.guard_time_us +
                                  tdma->schedule.processing_margin_us;
    
    uint32_t effective_slot_us = tdma->schedule.slot_duration_us + tdma->schedule.guard_time_us;
    
    uint64_t expected_resp_dw = tdma->superframe_start_dw + 
                                tdma_us_to_dw(slot_id * effective_slot_us) +
                                tdma_us_to_dw(tdma->schedule.poll_to_resp_delay_us);
    
    /* RX window: [expected - early, expected + late] */
    *rx_start_dw = expected_resp_dw - tdma_us_to_dw(rx_early_margin_us);
    *rx_end_dw = expected_resp_dw + tdma_us_to_dw(rx_late_margin_us);
    
    *rx_start_dw = tdma_mask_40bit(*rx_start_dw);
    *rx_end_dw = tdma_mask_40bit(*rx_end_dw);
    
    return TDMA_OK;
}