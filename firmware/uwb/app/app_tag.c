/**
 * @file       app_tag.c
 * @copyright
 * @license
 * @version    3.4.0
 * @date       2026-01-31
 * @author     Phuong Mai
 * @brief      Non-blocking Tag with TDMA, Trilateration and Adaptive Kalman Filter
 */
/* Includes ----------------------------------------------------------- */
#include "app_tag.h"
#include "app_anchor.h"
#include "app_calib_master.h"
#include "app_rtos_handles.h"
#include "bsp_io.h"
#include "bsp_util.h"
#include "bsp_uwb.h"
#include "mw_tdma_scheduler.h"
#include "positioning_config.h"
#include "sys_config.h"
#include "sys_logger.h"
#include "sys_ranging.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>
#if ENABLE_SYS_FUSION
#ifdef SYS_FUSION_PREFILTER_ENABLED
#undef SYS_FUSION_PREFILTER_ENABLED
#endif
#define SYS_FUSION_PREFILTER_ENABLED 0
#endif

/* Private types ------------------------------------------------------ */
typedef enum {
    APP_TAG_UWB_CONTROL_NONE = 0,
    APP_TAG_UWB_CONTROL_SWITCH_ZONE,
    APP_TAG_UWB_CONTROL_UPDATE_ACTIVE_ZONE_PROFILE,
    APP_TAG_UWB_CONTROL_START_TAG_CALIBRATION,
    APP_TAG_UWB_CONTROL_STOP_TAG_CALIBRATION,
    APP_TAG_UWB_CONTROL_APPLY_TAG_CALIBRATION,
} app_tag_uwb_control_op_t;

/* Private variables -------------------------------------------------- */
static uint32_t s_error_count = 0;
static uint32_t s_success_count = 0;
static uint32_t s_last_ranging_tick = 0;
static uint8_t s_sequence_num = 0;
static bool s_is_ranging_active = false;
static uint8_t s_pending_num_anchors = 0;
static uint8_t s_pending_anchor_ids[NUM_ANCHORS] = {0};
static uint32_t s_next_due_tick = 0;
static uint32_t s_cycle_start_tick = 0;
static uint32_t s_last_cycle_done_tick = 0;
static uint32_t s_period_miss_count = 0;
static uint32_t s_period_overrun_count = 0;
static volatile app_tag_uwb_control_op_t s_uwb_control_op = APP_TAG_UWB_CONTROL_NONE;
static uint32_t s_control_zone_id = 0U;
static protobuf_zone_profile_t s_control_zone_profile = {0};
static uint32_t s_control_sample_target = 0U;
static uint32_t s_control_anchor_mask = 0U;
static float s_control_tag_x_m = 0.0f;
static float s_control_tag_y_m = 0.0f;
static float s_control_tag_z_m = 0.0f;
static uint32_t s_zone_switch_tick = 0U;
static bool s_zone_switch_pending_save = false;
#if SYS_ZONE_SWITCH_STRESS_TEST_ENABLE
static uint32_t s_zone_switch_stress_last_request_tick = 0U;
static uint32_t s_zone_switch_stress_last_complete_tick = 0U;
static uint32_t s_zone_switch_stress_count = 0U;
static uint32_t s_zone_switch_stress_fail_count = 0U;
static uint32_t s_zone_switch_stress_last_warn_tick = 0U;
#endif

/* Private prototypes --------------------------------------------------- */
static bool process_ranging_results(sys_ranging_result_t *results, int num_success);
static void get_tdma_config(uint8_t *num_anchors, uint8_t *anchor_ids);
static uint32_t estimate_tdma_cycle_ms(uint8_t num_anchors);
static void update_period_schedule(uint32_t now_tick, uint32_t period_ms);
static void record_ranging_error(void);
static void finish_failed_ranging_cycle(sys_ranging_err_t err,
                                        uint32_t now_tick,
                                        uint32_t period_ms,
                                        bool abort_ranging,
                                        const char *reason);
static bool queue_uwb_control_request(app_tag_uwb_control_op_t op);
static void reset_app_after_radio_reconfigure(sys_config_t *cfg);
static void persist_stable_zone_switch(sys_config_t *cfg);
#if SYS_ZONE_SWITCH_STRESS_TEST_ENABLE
static bool find_next_stress_zone(const sys_config_t *cfg, uint32_t current_zone, uint32_t *next_zone);
static void schedule_zone_switch_stress_test(sys_config_t *cfg);
#endif

/* Private functions --------------------------------------------------- */
static bool queue_uwb_control_request(app_tag_uwb_control_op_t op)
{
    if (s_uwb_control_op != APP_TAG_UWB_CONTROL_NONE) {
        return false;
    }

    s_uwb_control_op = op;
    if (g_uwb_isr_semHandle != NULL) {
        (void)osSemaphoreRelease(g_uwb_isr_semHandle);
    }
    return true;
}

bool app_rtos_request_zone_switch(uint32_t zone_id)
{
    if (zone_id < 1U || zone_id > 4U ||
        s_uwb_control_op != APP_TAG_UWB_CONTROL_NONE) {
        return false;
    }

    s_control_zone_id = zone_id;
    return queue_uwb_control_request(APP_TAG_UWB_CONTROL_SWITCH_ZONE);
}

bool app_rtos_request_active_zone_profile_update(const protobuf_zone_profile_t *profile)
{
    if (!profile ||
        profile->zone_id != sys_config_get_active_zone_id() ||
        !sys_config_zone_profile_valid(profile) ||
        s_uwb_control_op != APP_TAG_UWB_CONTROL_NONE) {
        return false;
    }

    s_control_zone_profile = *profile;
    return queue_uwb_control_request(APP_TAG_UWB_CONTROL_UPDATE_ACTIVE_ZONE_PROFILE);
}

bool app_rtos_request_tag_calibration_start(uint32_t sample_target,
                                            float tag_x_m,
                                            float tag_y_m,
                                            float tag_z_m)
{
    if (s_uwb_control_op != APP_TAG_UWB_CONTROL_NONE) {
        return false;
    }

    s_control_sample_target = sample_target;
    s_control_tag_x_m = tag_x_m;
    s_control_tag_y_m = tag_y_m;
    s_control_tag_z_m = tag_z_m;
    return queue_uwb_control_request(APP_TAG_UWB_CONTROL_START_TAG_CALIBRATION);
}

bool app_rtos_request_tag_calibration_stop(void)
{
    app_calib_master_set_active(false);
    return queue_uwb_control_request(APP_TAG_UWB_CONTROL_STOP_TAG_CALIBRATION);
}

bool app_rtos_request_tag_calibration_apply(uint32_t anchor_mask)
{
    if (anchor_mask == 0U || s_uwb_control_op != APP_TAG_UWB_CONTROL_NONE) {
        return false;
    }

    s_control_anchor_mask = anchor_mask;
    return queue_uwb_control_request(APP_TAG_UWB_CONTROL_APPLY_TAG_CALIBRATION);
}

static void reset_app_after_radio_reconfigure(sys_config_t *cfg)
{
    if (!cfg) {
        return;
    }

    if (cfg->uwb.role == DEVICE_ROLE_TAG) {
        app_tag_reset_fusion();
        (void)app_tag_init();
    } else {
        (void)app_anchor_init();
    }
}

static void persist_stable_zone_switch(sys_config_t *cfg)
{
    if (!cfg ||
        !s_zone_switch_pending_save ||
        (HAL_GetTick() - s_zone_switch_tick) <= 10000U) {
        return;
    }

    s_zone_switch_pending_save = false;
    uint32_t active_zone = sys_config_get_active_zone_id();
    cfg->default_zone_id = active_zone;

    RLOG_I(LOG_OBJECT_CODE_SYS_CFG,
           "[UWB] Zone switch stable for 10s. Persisting default_zone_id=%lu to Flash...",
           (unsigned long)active_zone);

    if (sys_config_save() == 0) {
        RLOG_I(LOG_OBJECT_CODE_SYS_CFG,
               "[UWB] Successfully persisted default_zone_id=%lu to Flash",
               (unsigned long)active_zone);
    } else {
        RLOG_E(LOG_OBJECT_CODE_SYS_CFG, ERR_HAL,
               "[UWB] Failed to persist default_zone_id to Flash");
    }
}

#if SYS_ZONE_SWITCH_STRESS_TEST_ENABLE
static bool find_next_stress_zone(const sys_config_t *cfg, uint32_t current_zone, uint32_t *next_zone)
{
    if (!cfg || !next_zone) {
        return false;
    }

    for (uint32_t step = 1U; step <= 4U; step++) {
        uint32_t candidate = ((current_zone + step - 1U) % 4U) + 1U;
        if (candidate != current_zone &&
            sys_config_zone_profile_valid(&cfg->zone_profiles[candidate - 1U])) {
            *next_zone = candidate;
            return true;
        }
    }

    return false;
}

static void schedule_zone_switch_stress_test(sys_config_t *cfg)
{
    if (!cfg ||
        cfg->calib.enable_tag_auto_calib ||
        cfg->calib.enable_anchor_auto_calib ||
        s_uwb_control_op != APP_TAG_UWB_CONTROL_NONE) {
        return;
    }

    uint32_t now = HAL_GetTick();
    if (s_zone_switch_stress_last_request_tick != 0U &&
        (now - s_zone_switch_stress_last_request_tick) < SYS_ZONE_SWITCH_STRESS_INTERVAL_MS) {
        return;
    }

    uint32_t current_zone = sys_config_get_active_zone_id();
    uint32_t next_zone = 0U;
    if (!find_next_stress_zone(cfg, current_zone, &next_zone)) {
        if ((now - s_zone_switch_stress_last_warn_tick) >= 2000U) {
            RLOG_W(LOG_OBJECT_CODE_UWB_DRIVER,
                   "[UWB][ZONE_STRESS] No alternate valid zone profile from active Zone %lu",
                   (unsigned long)current_zone);
            s_zone_switch_stress_last_warn_tick = now;
        }
        return;
    }

    s_control_zone_id = next_zone;
    s_zone_switch_stress_last_request_tick = now;
    if (queue_uwb_control_request(APP_TAG_UWB_CONTROL_SWITCH_ZONE)) {
        RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER,
               "[UWB][ZONE_STRESS] request #%lu Zone %lu -> %lu",
               (unsigned long)(s_zone_switch_stress_count + 1U),
               (unsigned long)current_zone,
               (unsigned long)next_zone);
    }
}
#endif

void app_tag_process_uwb_control(sys_config_t *cfg)
{
    persist_stable_zone_switch(cfg);
#if SYS_ZONE_SWITCH_STRESS_TEST_ENABLE
    schedule_zone_switch_stress_test(cfg);
#endif

    app_tag_uwb_control_op_t op = s_uwb_control_op;
    if (op == APP_TAG_UWB_CONTROL_NONE || !cfg) {
        return;
    }

    uint32_t zone_id = s_control_zone_id;
    protobuf_zone_profile_t zone_profile = s_control_zone_profile;
    uint32_t sample_target = s_control_sample_target;
    uint32_t anchor_mask = s_control_anchor_mask;
    float tag_x_m = s_control_tag_x_m;
    float tag_y_m = s_control_tag_y_m;
    float tag_z_m = s_control_tag_z_m;
    s_uwb_control_op = APP_TAG_UWB_CONTROL_NONE;

    (void)osMutexAcquire(g_spi1_mutexHandle, osWaitForever);

    if ((op == APP_TAG_UWB_CONTROL_START_TAG_CALIBRATION ||
         op == APP_TAG_UWB_CONTROL_STOP_TAG_CALIBRATION ||
         op == APP_TAG_UWB_CONTROL_APPLY_TAG_CALIBRATION) &&
        cfg->uwb.role != DEVICE_ROLE_TAG) {
        RLOG_W(LOG_OBJECT_CODE_APPLICATION,
               "[UWB] Ignoring tag calibration control request on non-tag role");
        (void)osMutexRelease(g_spi1_mutexHandle);
        return;
    }

    if (op == APP_TAG_UWB_CONTROL_SWITCH_ZONE) {
        uint32_t old_zone_id = sys_config_get_active_zone_id();
        uint32_t switch_start_tick = HAL_GetTick();
        sys_ranging_abort();
        bsp_uwb_idle();

        if (sys_config_apply_zone_profile(zone_id) &&
            bsp_uwb_configure(&cfg->uwb) == BSP_OK) {
            sys_config_set_active_zone_id(zone_id);
            reset_app_after_radio_reconfigure(cfg);
            app_rtos_request_sensor_fusion_reset();
            s_zone_switch_tick = HAL_GetTick();
            s_zone_switch_pending_save = true;
            RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER,
                   "[UWB] Zone switch to %lu complete: preamble=%lu anchors=%lu latency=%lums",
                   (unsigned long)zone_id,
                   (unsigned long)cfg->uwb.uwb_preamble_code,
                   (unsigned long)cfg->anchor_count,
                   (unsigned long)(s_zone_switch_tick - switch_start_tick));
#if SYS_ZONE_SWITCH_STRESS_TEST_ENABLE
            s_zone_switch_stress_count++;
            uint32_t complete_gap_ms = (s_zone_switch_stress_last_complete_tick == 0U)
                                       ? 0U
                                       : (s_zone_switch_tick - s_zone_switch_stress_last_complete_tick);
            s_zone_switch_stress_last_complete_tick = s_zone_switch_tick;
            RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER,
                   "[UWB][ZONE_STRESS] done #%lu %lu->%lu latency=%lums complete_gap=%lums fail=%lu",
                   (unsigned long)s_zone_switch_stress_count,
                   (unsigned long)old_zone_id,
                   (unsigned long)zone_id,
                   (unsigned long)(s_zone_switch_tick - switch_start_tick),
                   (unsigned long)complete_gap_ms,
                   (unsigned long)s_zone_switch_stress_fail_count);
#endif
        } else {
            (void)sys_config_apply_zone_profile(old_zone_id);
            (void)bsp_uwb_configure(&cfg->uwb);
            reset_app_after_radio_reconfigure(cfg);
#if SYS_ZONE_SWITCH_STRESS_TEST_ENABLE
            s_zone_switch_stress_fail_count++;
#endif
            RLOG_E(LOG_OBJECT_CODE_UWB_DRIVER, ERR_HAL,
                   "[UWB] Zone switch to %lu failed; restored Zone %lu",
                   (unsigned long)zone_id,
                   (unsigned long)old_zone_id);
        }
    } else if (op == APP_TAG_UWB_CONTROL_UPDATE_ACTIVE_ZONE_PROFILE) {
        uint32_t active_zone_id = sys_config_get_active_zone_id();
        protobuf_zone_profile_t previous = cfg->zone_profiles[active_zone_id - 1U];
        sys_ranging_abort();
        bsp_uwb_idle();

        bool updated = zone_profile.zone_id == active_zone_id &&
                       sys_config_set_zone_profile(&zone_profile) == 0 &&
                       sys_config_apply_zone_profile(active_zone_id) &&
                       bsp_uwb_configure(&cfg->uwb) == BSP_OK &&
                       sys_config_save() == 0;
        if (updated) {
            reset_app_after_radio_reconfigure(cfg);
            app_rtos_request_sensor_fusion_reset();
            RLOG_I(LOG_OBJECT_CODE_UWB_DRIVER,
                   "[UWB] Active Zone %lu profile updated and persisted",
                   (unsigned long)active_zone_id);
        } else {
            cfg->zone_profiles[active_zone_id - 1U] = previous;
            (void)sys_config_apply_zone_profile(active_zone_id);
            (void)bsp_uwb_configure(&cfg->uwb);
            reset_app_after_radio_reconfigure(cfg);
            RLOG_E(LOG_OBJECT_CODE_UWB_DRIVER, ERR_HAL,
                   "[UWB] Active Zone %lu profile update failed; restored previous profile",
                   (unsigned long)active_zone_id);
        }
    } else if (op == APP_TAG_UWB_CONTROL_START_TAG_CALIBRATION) {
        bool was_ranging_enabled = app_rtos_is_ranging_enabled();
        sys_ranging_abort();
        bsp_uwb_idle();
        app_tag_reset_fusion();
        cfg->calib.samples = sample_target;
        app_calib_master_set_active(true);
        if (app_calib_master_set_reference_position(tag_x_m, tag_y_m, tag_z_m) &&
            app_calib_master_init() == APP_OK) {
            app_rtos_set_ranging_enabled(true);
            RLOG_I(LOG_OBJECT_CODE_TAG, "[CALIB][MASTER] start request accepted");
        } else {
            app_calib_master_set_active(false);
            app_rtos_set_ranging_enabled(was_ranging_enabled);
            RLOG_E(LOG_OBJECT_CODE_TAG, ERR_INVALID_PARAM,
                   "[CALIB][MASTER] start request rejected");
        }
    } else if (op == APP_TAG_UWB_CONTROL_STOP_TAG_CALIBRATION) {
        sys_ranging_abort();
        bsp_uwb_idle();
        app_calib_master_on_ranging_stopped();
        app_calib_master_set_active(false);
        app_rtos_set_ranging_enabled(true);
        app_tag_reset_fusion();
        (void)sys_config_save();
    } else if (op == APP_TAG_UWB_CONTROL_APPLY_TAG_CALIBRATION) {
        uint16_t tx_delay = 0U;
        uint16_t rx_delay = 0U;
        if (app_calib_master_get_average_candidate(anchor_mask, &tx_delay, &rx_delay)) {
            uint32_t old_tx_delay = cfg->uwb.tx_antenna_delay;
            uint32_t old_rx_delay = cfg->uwb.rx_antenna_delay;
            bool old_calib_enabled = app_calib_master_is_active();
            bool old_ranging_enabled = app_rtos_is_ranging_enabled();
            sys_ranging_abort();
            bsp_uwb_idle();
            cfg->uwb.tx_antenna_delay = tx_delay;
            cfg->uwb.rx_antenna_delay = rx_delay;
            app_calib_master_set_active(false);
            app_rtos_set_ranging_enabled(false);
            if (bsp_uwb_configure(&cfg->uwb) == BSP_OK && sys_config_save() == 0) {
                app_calib_master_on_ranging_stopped();
                app_tag_reset_fusion();
                app_rtos_set_ranging_enabled(true);
                RLOG_I(LOG_OBJECT_CODE_TAG,
                       "[CALIB][MASTER] applied tag delay TX=%u RX=%u",
                       tx_delay,
                       rx_delay);
            } else {
                cfg->uwb.tx_antenna_delay = old_tx_delay;
                cfg->uwb.rx_antenna_delay = old_rx_delay;
                app_calib_master_set_active(old_calib_enabled);
                app_rtos_set_ranging_enabled(old_ranging_enabled);
                (void)bsp_uwb_configure(&cfg->uwb);
                RLOG_E(LOG_OBJECT_CODE_TAG, ERR_HAL,
                       "[CALIB][MASTER] apply failed; restored previous delays");
            }
        } else {
            RLOG_W(LOG_OBJECT_CODE_TAG,
                   "[CALIB][MASTER] apply rejected: no completed candidates for mask=0x%02lX",
                   (unsigned long)anchor_mask);
        }
    }

    (void)osMutexRelease(g_spi1_mutexHandle);
}

/*
     * r_3d² = r_2d² + dz²
     * r_2d = sqrt(r_3d² - dz²)
*/
static void get_tdma_config(uint8_t *num_anchors, uint8_t *anchor_ids)
{
    const sys_config_t *cfg = sys_config_get();
    uint8_t total = (cfg->anchor_count > NUM_ANCHORS)
                    ? NUM_ANCHORS
                    : (uint8_t)cfg->anchor_count;
    *num_anchors = total;

    for (uint8_t i = 0; i < total; i++) {
        anchor_ids[i] = (uint8_t)cfg->anchor_layout[i].anchor_id;
    }
}

static uint32_t estimate_tdma_cycle_ms(uint8_t num_anchors)
{
    uint32_t n = (num_anchors == 0) ? 1U : (uint32_t)num_anchors;
    uint32_t effective_slot_us = TDMA_DEFAULT_SLOT_DURATION_US + TDMA_DEFAULT_GUARD_TIME_US;

    /* Keep this estimate tied to central TDMA defaults in mw_tdma_scheduler. */
    uint32_t resp_phase_us = TDMA_DEFAULT_POLL_TO_RESP_DELAY_US +
                             (n * effective_slot_us);
    uint32_t final_phase_us = TDMA_DEFAULT_RESP_TO_FINAL_DELAY_US;
    uint32_t result_phase_us = TDMA_DEFAULT_FINAL_TO_RESULT_DELAY_US +
                               (n * effective_slot_us);
    uint32_t processing_margin_us = TDMA_PROCESSING_MARGIN_US + TDMA_CLOCK_GUARD_US;

    uint32_t total_us = resp_phase_us + final_phase_us + result_phase_us + processing_margin_us;

    return (total_us + 999U) / 1000U;
}

static void update_period_schedule(uint32_t now_tick, uint32_t period_ms)
{
    if (period_ms == 0U) {
        period_ms = 1U;
    }

    if (s_next_due_tick == 0U) {
        s_next_due_tick = now_tick + period_ms;
        return;
    }

    uint32_t next_due_tick = s_next_due_tick + period_ms;

    if ((int32_t)(now_tick - next_due_tick) >= 0) {
        s_next_due_tick = now_tick;
        s_period_miss_count++;
        return;
    }

    s_next_due_tick = next_due_tick;
}

static void record_ranging_error(void)
{
    s_error_count++;
}

static void finish_failed_ranging_cycle(sys_ranging_err_t err,
                                        uint32_t now_tick,
                                        uint32_t period_ms,
                                        bool abort_ranging,
                                        const char *reason)
{
    if (abort_ranging) {
        sys_ranging_abort();
    }

    record_ranging_error();
    s_last_ranging_tick = now_tick;
    uint32_t cycle_ms = (s_cycle_start_tick != 0U)
                        ? (now_tick - s_cycle_start_tick)
                        : 0U;

    if ((s_error_count % 10U) == 0U) {
        RLOG_W(LOG_OBJECT_CODE_TAG,
               "[TAG] %s: err=%d duration=%lums period=%ums",
               reason,
               err,
               (unsigned long)cycle_ms,
               (unsigned)period_ms);
    }

    update_period_schedule(s_last_ranging_tick, period_ms);
    s_is_ranging_active = false;
}

static bool process_ranging_results(sys_ranging_result_t *results, int num_success)
{
    uwb_distance_msg_t msg = {0};
    uint8_t valid_count = 0;
    msg.count = 0;
    msg.mask  = 0;
    for (int i = 0; i < num_success; i++) {
        sys_ranging_result_t *r = &results[i];
        uint8_t aid = r->anchor_id;
        if (aid < 1 || aid > MAX_ANCHORS_SUPPORTED || msg.count >= MAX_ANCHORS_SUPPORTED) {
            continue;
        }

        uint8_t entry = msg.count;
        msg.distances[entry] = r->distance_m;
        msg.anchor_ids[entry] = aid;
        msg.fp_amp_norm[entry] = (float)r->fp_amp_norm_q8 / 256.0f;
        msg.fp_snr[entry] = (float)r->fp_snr_q8 / 256.0f;
        msg.rx_fp_delta_db[entry] = (float)r->rx_fp_delta_db_q8 / 256.0f;
        msg.quality_valid[entry] = (r->quality != 0U) ? 1U : 0U;

        if (r->valid) {
            msg.mask |= (1 << (aid - 1));
            valid_count++;
        }
        msg.count++;
    }

    if (valid_count < 3U) {
        record_ranging_error();
        if (osMessageQueuePut(g_uwb_distance_queue, &msg, 0U, 0U) != osOK) {
            RLOG_W(LOG_OBJECT_CODE_TAG, "[FUSION] Distance queue full, dropping failed ranging cycle");
        }
        return false;
    }

    if (osMessageQueuePut(g_uwb_distance_queue, &msg, 0U, 0U) != osOK) {
        record_ranging_error();
        RLOG_W(LOG_OBJECT_CODE_TAG, "[FUSION] Distance queue full, dropping ranging cycle");
        return false;
    }
    s_success_count++;
    return true;
}
/* Public functions --------------------------------------------------- */

uint32_t app_tag_get_ranging_error_count(void)
{
    return s_error_count;
}

app_err_t app_tag_init(void)
{
    sys_config_t *cfg = sys_config_get();
    uint8_t cfg_num_anchors = 1;
    uint8_t cfg_anchor_ids[NUM_ANCHORS] = {0};
    RLOG_I(LOG_OBJECT_CODE_TAG, "========== TAG INIT ==========");
    RLOG_I(LOG_OBJECT_CODE_TAG, "ID: %d | Interval: %dms", cfg->uwb.device_id, cfg->uwb.ranging_period_ms);
    
    /* Log ranging period from config */
    uint32_t update_hz = (cfg->uwb.ranging_period_ms > 0) ? (1000 / cfg->uwb.ranging_period_ms) : 0;
    RLOG_I(LOG_OBJECT_CODE_TAG, "Update rate: %dms (%luHz)",
           cfg->uwb.ranging_period_ms, update_hz);

    /* Log height configuration */
    RLOG_I(LOG_OBJECT_CODE_TAG, "Height: Tag=%.2fm Anchor=%.2fm dZ=%.2fm",
           TAG_HEIGHT_M, ANCHOR_HEIGHT_M, HEIGHT_OFFSET_M);

#if SYS_FUSION_PREFILTER_ENABLED
    RLOG_I(LOG_OBJECT_CODE_TAG,
           "Pre-Filter: fusion mw_filter Mahalanobis %s (recover=%.2f reject=%.2f)",
           cfg->prefilter.enable ? "ON" : "OFF",
           cfg->prefilter.recover_d2,
           cfg->prefilter.reject_d2);
#else
    RLOG_I(LOG_OBJECT_CODE_TAG, "Pre-Filter: Mahalanobis OFF");
#endif

    RLOG_I(LOG_OBJECT_CODE_TAG, "Anchor positions:");
    for (uint32_t i = 0; i < cfg->anchor_count; i++) {
        RLOG_I(LOG_OBJECT_CODE_TAG, "  #%lu: X=%.2fm Y=%.2fm Z=%.2fm",
               (unsigned long)cfg->anchor_layout[i].anchor_id, 
               (float)cfg->anchor_layout[i].x_m,
               (float)cfg->anchor_layout[i].y_m,
               (float)cfg->anchor_layout[i].z_m);
    }

    RLOG_I(LOG_OBJECT_CODE_TAG, "==============================");

    get_tdma_config(&cfg_num_anchors, cfg_anchor_ids);
    {
        uint32_t est_4_ms = estimate_tdma_cycle_ms(4);
        uint32_t est_6_ms = estimate_tdma_cycle_ms(6);
        uint32_t est_cfg_ms = estimate_tdma_cycle_ms(cfg_num_anchors);

        RLOG_I(LOG_OBJECT_CODE_TAG,
               "TDMA cycle estimate: 4 anchors ~%lums, 6 anchors ~%lums",
               (unsigned long)est_4_ms,
               (unsigned long)est_6_ms);

        if (cfg->uwb.ranging_period_ms < est_cfg_ms) {
            RLOG_W(LOG_OBJECT_CODE_TAG,
                   "Configured period %ums < estimated stable %lums for %u anchors",
                   cfg->uwb.ranging_period_ms,
                   (unsigned long)est_cfg_ms,
                   cfg_num_anchors);
        }
    }

    s_last_ranging_tick = HAL_GetTick();
    s_next_due_tick = s_last_ranging_tick + cfg->uwb.ranging_period_ms;
    s_cycle_start_tick = 0;
    s_last_cycle_done_tick = 0;
    s_period_miss_count = 0;
    s_period_overrun_count = 0;

    sys_ranging_set_calib_status(SYS_CALIB_STATUS_NORMAL);

    return APP_OK;
}

void app_tag_process(void)
{
    static uint32_t last_warn_log = 0;
    static uint32_t s_reported_period_miss_count = 0;
    sys_config_t *cfg = sys_config_get();
    uint32_t now = HAL_GetTick();
    uint32_t period_ms = (cfg->uwb.ranging_period_ms == 0U)
                         ? 1U
                         : cfg->uwb.ranging_period_ms;

    if (!s_is_ranging_active && s_next_due_tick != 0U && (int32_t)(now - s_next_due_tick) > 0) {
        uint32_t lateness_ms = now - s_next_due_tick;
        if ((now - last_warn_log) >= 2000U) {
            RLOG_W(LOG_OBJECT_CODE_TAG,
                   "Period slip before start: late=%lums target=%ums",
                   (unsigned long)lateness_ms,
                   (unsigned)cfg->uwb.ranging_period_ms);
            last_warn_log = now;
        }
    }

    /* --- STEP 1: TRIGGER RANGING --- */
    if (!s_is_ranging_active && ((int32_t)(now - s_next_due_tick) >= 0)) {
        uint8_t num_anchors = 1;
        uint8_t anchor_ids[NUM_ANCHORS] = {0};

        get_tdma_config(&num_anchors, anchor_ids);

        sys_ranging_err_t start_err = sys_ranging_tag_start_tdma(num_anchors,
                                                                  anchor_ids,
                                                                  s_sequence_num,
                                                                  cfg->uwb.rx_timeout_ms);
        if (start_err == SYS_RANGING_OK) {
            s_sequence_num++;
            s_pending_num_anchors = num_anchors;
            memcpy(s_pending_anchor_ids, anchor_ids, sizeof(s_pending_anchor_ids));
            s_is_ranging_active = true;
            s_cycle_start_tick = now;
            return;
        }

        if (start_err == SYS_RANGING_ERR_BUSY) {
            if ((now - last_warn_log) >= 2000U) {
                RLOG_W(LOG_OBJECT_CODE_TAG,
                       "Start TDMA busy: now=%lu next_due=%lu last=%lu",
                       (unsigned long)now,
                       (unsigned long)s_next_due_tick,
                       (unsigned long)s_last_ranging_tick);
                last_warn_log = now;
            }
        } else {
            record_ranging_error();
            update_period_schedule(now, period_ms);
        }
        return;
    }

    /* --- STEP 2: PROCESS RANGING --- */
    if (!s_is_ranging_active) {
        return;
    }

    if (s_cycle_start_tick != 0U &&
        (int32_t)(now - s_cycle_start_tick) >= (int32_t)period_ms) {
        finish_failed_ranging_cycle(SYS_RANGING_ERR_TIMEOUT,
                                    now,
                                    period_ms,
                                    true,
                                    "Period watchdog abort");
        return;
    }

    sys_ranging_err_t err = sys_ranging_tag_process_tdma(s_pending_num_anchors,
                                                          s_pending_anchor_ids,
                                                          cfg->uwb.rx_timeout_ms);

    if (err == SYS_RANGING_OK) {
        sys_ranging_multi_result_t multi_results = {0};
        bool cycle_success = false;
        if (sys_ranging_tag_get_results_tdma(&multi_results) == SYS_RANGING_OK) {
            cycle_success = process_ranging_results(multi_results.results, multi_results.count);
        } else {
            record_ranging_error();
            RLOG_W(LOG_OBJECT_CODE_TAG, "[TAG] No TDMA results available");
        }

        if (cycle_success) {
            bsp_io_led_blink(5);
        }

        uint32_t cycle_done_tick = HAL_GetTick();
        if (s_cycle_start_tick != 0U) {
            uint32_t cycle_ms = cycle_done_tick - s_cycle_start_tick;
            uint32_t interval_ms = (s_last_cycle_done_tick == 0U)
                                   ? cfg->uwb.ranging_period_ms
                                   : (cycle_done_tick - s_last_cycle_done_tick);
            float current_hz = (interval_ms > 0U) ? (1000.0f / (float)interval_ms) : 0.0f;

            RLOG_I(LOG_OBJECT_CODE_TAG,
                   "Cycle duration=%lums period=%ums success rate=%.2fHz anchors=%u valid=%u",
                   (unsigned long)cycle_ms,
                   (unsigned)cfg->uwb.ranging_period_ms,
                   current_hz,
                   (unsigned)s_pending_num_anchors,
                   (unsigned)multi_results.count);

            if (cycle_ms > cfg->uwb.ranging_period_ms) {
                s_period_overrun_count++;
                if ((s_period_overrun_count % 5U) == 1U) {
                    RLOG_W(LOG_OBJECT_CODE_TAG,
                           "Cycle overrun: %lums > period %ums (count=%lu)",
                           (unsigned long)cycle_ms,
                           (unsigned)cfg->uwb.ranging_period_ms,
                           (unsigned long)s_period_overrun_count);
                }
            }
        }
        s_last_cycle_done_tick = cycle_done_tick;
        s_last_ranging_tick = cycle_done_tick;
        update_period_schedule(s_last_ranging_tick, period_ms);
        if (s_period_miss_count != s_reported_period_miss_count
            && (s_period_miss_count % 10U) == 1U
            && s_period_miss_count > 0U) {
            RLOG_W(LOG_OBJECT_CODE_TAG,
                   "Period miss accumulated: %lu",
                   (unsigned long)s_period_miss_count);
            s_reported_period_miss_count = s_period_miss_count;
        }
        s_is_ranging_active = false;
        return;
    }

    if (err != SYS_RANGING_ERR_BUSY) {
        finish_failed_ranging_cycle(err,
                                    HAL_GetTick(),
                                    period_ms,
                                    false,
                                    "Ranging failed");
    }
    
}

void app_tag_reset_fusion(void)
{
    RLOG_I(LOG_OBJECT_CODE_TAG, "[FUSION] Resetting sensor fusion filters and state...");
    s_is_ranging_active = false;
    s_error_count = 0;
    s_last_ranging_tick = HAL_GetTick();
    s_next_due_tick = s_last_ranging_tick + sys_config_get()->uwb.ranging_period_ms;
    s_cycle_start_tick = 0U;
    s_last_cycle_done_tick = 0U;

#if ENABLE_SYS_FUSION
    app_rtos_request_sensor_fusion_reset();
#endif
}

/* End of file -------------------------------------------------------- */
