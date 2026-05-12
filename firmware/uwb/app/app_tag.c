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
#include "bsp_io.h"
#include "bsp_util.h"
#include "bsp_uwb.h"
#include "mw_calibration.h"
#include "mw_filter.h"
#include "mw_tdma_scheduler.h"
#include "mw_trilateration.h"
#include "positioning_config.h"
#include "sys_config.h"
#include "sys_logger.h"
#include "sys_ranging.h"

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

/* Private types ------------------------------------------------------ */
#if ENABLE_TAG_AUTO_CALIB
typedef enum {
    TAG_STATE_IDLE = 0,
    TAG_STATE_CALIB_COLLECTING,
    TAG_STATE_CALIB_CALCULATE,
    TAG_STATE_CALIB_PENDING_ACCEPT,
    TAG_STATE_CALIB_DONE
} tag_app_state_t;
#endif
typedef struct {
    mahalanobis_prefilter_t prefilter;
    distance_smoother_t smoother;

} filter_state_t;

/* Private variables --------------------------------------------------- */

/* Private variables -------------------------------------------------- */
static uint32_t s_error_count = 0;
static uint32_t s_success_count = 0;
static uint32_t s_last_ranging_tick = 0;
static uint8_t s_sequence_num = 0;
static filter_state_t s_filters;
static vec2d_t s_last_position = {.x = 0.0f, .y = 0.0f};
static bool s_is_ranging_active = false;
static uint8_t s_pending_num_anchors = 0;
static uint8_t s_pending_anchor_ids[NUM_ANCHORS] = {0};
static bool s_pending_calib_mode = false;
static uint32_t s_next_due_tick = 0;
static uint32_t s_cycle_start_tick = 0;
static uint32_t s_period_miss_count = 0;
static uint32_t s_period_overrun_count = 0;

#if ENABLE_TAG_AUTO_CALIB
static mw_calib_ctx_t s_tag_calib = {0};
static tag_app_state_t s_tag_app_state = TAG_STATE_IDLE;
#endif

/* Private prototypes --------------------------------------------------- */
static void init_filters(void);
static void process_ranging_results(sys_ranging_result_t *results, int num_success);
static bool convert_3d_to_2d_distance(double r3d, double dz, double *r2d_out);
static bool get_anchor_position(uint8_t aid, vec3d_t *pos_out);
static void get_tdma_config(uint8_t *num_anchors, uint8_t *anchor_ids);
static uint32_t estimate_tdma_cycle_ms(uint8_t num_anchors);
static void update_period_schedule(uint32_t now_tick, uint32_t period_ms);
#if ENABLE_TAG_AUTO_CALIB
static void tag_calib_reset(void);
static bool tag_calib_add_sample(float distance);
static void tag_calib_calculate_and_adjust(void);
static void tag_calib_apply_and_save(void);
static float tag_calib_get_ref_distance_3d(void);
#endif

/* Private functions --------------------------------------------------- */
static void init_filters(void)
{
    memset(&s_filters, 0, sizeof(s_filters));

    sys_config_t *cfg = sys_config_get();
    float init_x = 0.0f;
    float init_y = 0.0f;
    
    if (cfg->anchor_count > 0) {
        for (uint32_t i = 0; i < cfg->anchor_count; i++) {
            init_x += cfg->anchor_layout[i].x_m;
            init_y += cfg->anchor_layout[i].y_m;
        }
        init_x /= cfg->anchor_count;
        init_y /= cfg->anchor_count;
    }
                   
    s_last_position.x = init_x;
    s_last_position.y = init_y;

    /* Initialize Mahalanobis Prefilter (T1=6.0, T2=16.0, BaseR=0.1) */
    mw_filter_mahalanobis_init(&s_filters.prefilter, 6.0f, 16.0f, 0.1f);

    /* Smoothing is enabled by default only when Mahalanobis pre-filter is disabled. */
    mw_filter_distance_smoother_init(&s_filters.smoother,
                                     (ENABLE_MAHALANOBIS_PREFILTER == 0),
                                     SMOOTHER_ALPHA,
                                     SMOOTHER_JUMP_LIMIT_M);
}

static bool convert_3d_to_2d_distance(double r3d, double dz, double *r2d_out)
{
    if (r3d < MIN_VALID_DISTANCE_M || r3d > MAX_VALID_DISTANCE_M) {
        return false;
    }
    
    /* Check if 3D distance is physically possible given height difference
     * If r3d <= |dz|, the measurement is invalid (tag can't be that close 
     * while maintaining the height difference)
     */
    double dz_abs = fabs(dz);
    if (r3d <= dz_abs + 1e-6) {
        return false;
    }
    
    /* Calculate 2D distance using Pythagorean theorem:
     * r_3d² = r_2d² + dz²
     * r_2d = sqrt(r_3d² - dz²)
     */
    double r2d_sq = r3d * r3d - dz * dz;
    if (r2d_sq < 0.0) {
        return false;
    }
    
    *r2d_out = sqrt(r2d_sq);
    return true;
}

static void get_tdma_config(uint8_t *num_anchors, uint8_t *anchor_ids)
{
    uint8_t total = (NUM_ANCHORS > 8) ? 8 : NUM_ANCHORS;
    *num_anchors = total;

    for (uint8_t i = 0; i < total; i++) {
        anchor_ids[i] = i + 1;
    }
}

static bool get_anchor_position(uint8_t aid, vec3d_t *pos_out)
{
    sys_config_t *cfg = sys_config_get();
    for (uint32_t i = 0; i < cfg->anchor_count; i++) {
        if (cfg->anchor_layout[i].anchor_id == aid) {
            pos_out->x = (double)cfg->anchor_layout[i].x_m;
            pos_out->y = (double)cfg->anchor_layout[i].y_m;
            pos_out->z = (double)cfg->anchor_layout[i].z_m;
            return true;
        }
    }
    return false;
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

    s_next_due_tick += period_ms;

    while ((int32_t)(now_tick - s_next_due_tick) >= 0) {
        s_next_due_tick += period_ms;
        s_period_miss_count++;
    }
}

#if ENABLE_TAG_AUTO_CALIB
static float tag_calib_get_ref_distance_3d(void)
{
    float dz = (float)(CALIB_ANCHOR_HEIGHT_M - CALIB_TAG_HEIGHT_M);
    return sqrtf(CALIB_REF_DISTANCE_XY_M * CALIB_REF_DISTANCE_XY_M + dz * dz);
}

static const mw_calib_config_t s_tag_calib_cfg = {
    .samples_per_round = CALIB_SAMPLES,
    .min_valid_distance_m = 0.1f,
    .max_valid_distance_m = 50.0f,
    .max_std_m = CALIB_MAX_STD_M,
    .m_to_dw_units = CALIB_A2A_M_TO_DW_UNITS,
    .damping = CALIB_A2A_DAMPING,
    .max_iterations = CALIB_MAX_ROUNDS
};

static void tag_calib_reset(void)
{
    sys_config_t *cfg = sys_config_get();
    mw_calib_reset(&s_tag_calib, &s_tag_calib_cfg, cfg->uwb.tx_antenna_delay);

    s_tag_app_state = TAG_STATE_CALIB_COLLECTING;
    sys_ranging_set_calib_status(SYS_CALIB_STATUS_COLLECTING);
    RLOG_I(LOG_OBJECT_CODE_TAG, "[CALIB] Start: delay=%u target=%.3fm",
           s_tag_calib.current_delay, tag_calib_get_ref_distance_3d());
}

static bool tag_calib_add_sample(float distance)
{
    uint16_t prev_count = s_tag_calib.count;
    bool full = mw_calib_add_sample(&s_tag_calib, distance);

    if (s_tag_calib.count != prev_count && (s_tag_calib.count % 5 == 0)) {
        bsp_io_led_toggle();
    }

    return full;
}

static void tag_calib_calculate_and_adjust(void)
{
    mw_calib_step_result_t step = mw_calib_calculate_and_adjust(&s_tag_calib,
                                                                 tag_calib_get_ref_distance_3d());

    if (step == MW_CALIB_STEP_NOT_READY) {
        return;
    }

    if (step == MW_CALIB_STEP_REJECTED_STD) {
        RLOG_W(LOG_OBJECT_CODE_TAG,
               "[R%u] REJECTED std=%.3fm > %.3fm",
               s_tag_calib.round + 1, s_tag_calib.std_dev, CALIB_MAX_STD_M);
        return;
    }

    RLOG_I(LOG_OBJECT_CODE_TAG, "[R%u] mean=%.3fm std=%.3fm err=%+.3fm delay=%u step=%u",
           s_tag_calib.round, s_tag_calib.mean, s_tag_calib.std_dev, s_tag_calib.error,
           s_tag_calib.current_delay, s_tag_calib.delta_step);

    if (step == MW_CALIB_STEP_DONE) {
        if (s_tag_calib.done_by_threshold) {
            RLOG_I(LOG_OBJECT_CODE_TAG, "[CALIB] DONE! delay=%u err=%.3fm",
                   s_tag_calib.current_delay, s_tag_calib.error);
        } else {
            RLOG_W(LOG_OBJECT_CODE_TAG, "[CALIB] STOP! delay=%u err=%.3fm",
                   s_tag_calib.current_delay, s_tag_calib.error);
        }
        RLOG_I(LOG_OBJECT_CODE_TAG, "HOLD=accept CLICK=retry");
        s_tag_app_state = TAG_STATE_CALIB_PENDING_ACCEPT;
        sys_ranging_set_calib_status(SYS_CALIB_STATUS_PENDING_ACCEPT);
        bsp_io_led_on();
        return;
    }

    if (step == MW_CALIB_STEP_ADJUSTED) {
        sys_config_t *cfg = sys_config_get();
        protobuf_uwb_cfg_t tmp = cfg->uwb;
        tmp.tx_antenna_delay = s_tag_calib.current_delay;
        tmp.rx_antenna_delay = CALIB_FIXED_RX_ANT_DLY;
        bsp_uwb_configure(&tmp);
        s_tag_app_state = TAG_STATE_CALIB_COLLECTING;
        sys_ranging_set_calib_status(SYS_CALIB_STATUS_COLLECTING);
    }
}

static void tag_calib_apply_and_save(void)
{
    if (!s_tag_calib.converged) return;

        RLOG_I(LOG_OBJECT_CODE_TAG, "[CALIB] Saving TX delay=%u (RX fixed=%u)...",
            s_tag_calib.current_delay,
            (unsigned)CALIB_FIXED_RX_ANT_DLY);

    sys_ranging_set_calib_status(SYS_CALIB_STATUS_DONE);

    sys_config_t *cfg = sys_config_get();
    cfg->uwb.tx_antenna_delay = s_tag_calib.current_delay;
        cfg->uwb.rx_antenna_delay = CALIB_FIXED_RX_ANT_DLY;
    cfg->calib.last_avg_error_m = s_tag_calib.error;
    cfg->calib.iterations_taken = s_tag_calib.round;

    if (sys_config_save() == 0) {
        RLOG_I(LOG_OBJECT_CODE_TAG, "[CALIB] Saved! Restarting...");
        bsp_delay_ms(1000);
        HAL_NVIC_SystemReset();
    } else {
        RLOG_E(LOG_OBJECT_CODE_TAG, ERR_HAL, "[CALIB] Save failed!");
    }
}

void app_tag_on_button(bsp_io_button_event_t event)
{
    if (s_tag_app_state != TAG_STATE_CALIB_PENDING_ACCEPT) return;

    if (event == BSP_IO_EVENT_HOLD) {
        tag_calib_apply_and_save();
        s_tag_app_state = TAG_STATE_CALIB_DONE;
    } else if (event == BSP_IO_EVENT_CLICK) {
        RLOG_I(LOG_OBJECT_CODE_TAG, "[CALIB] Retry...");
        tag_calib_reset();
    } else if (event == BSP_IO_EVENT_DOUBLE_CLICK) {
        RLOG_I(LOG_OBJECT_CODE_TAG, "[CALIB] Reset to factory...");
        sys_config_t *cfg = sys_config_get();
        cfg->uwb.tx_antenna_delay = TAG_FACTORY_TX_ANT_DLY;
        cfg->uwb.rx_antenna_delay = TAG_FACTORY_RX_ANT_DLY;
        sys_config_save();
        s_tag_app_state = TAG_STATE_IDLE;
        sys_ranging_set_calib_status(SYS_CALIB_STATUS_NORMAL);
    }
}
#endif
static void process_ranging_results(sys_ranging_result_t *results, int num_success)
{
    // RLOG_I(LOG_OBJECT_CODE_TAG, "========== RANGING #%lu ==========", s_success_count + 1);  // DISABLED - causes 20ms delay

    mw_tril_anchor_t anchors_by_id[NUM_ANCHORS + 1];
    uint8_t valid_count = 0;
    
    for (uint8_t i = 0; i <= NUM_ANCHORS; i++) anchors_by_id[i].valid = false;

    sys_config_t *cfg = sys_config_get();
    
    /* 1. Extract, Filter and Project Ranging Results */
    for (int i = 0; i < num_success; i++) {
        sys_ranging_result_t *r = &results[i];
        if (!r->valid) continue;

        uint8_t aid = r->anchor_id;
        if (aid < 1 || aid > NUM_ANCHORS) continue;

        vec3d_t anchor_pos;
        if (!get_anchor_position(aid, &anchor_pos)) {
            RLOG_W(LOG_OBJECT_CODE_TAG, "Anchor #%u position not found in flash", aid);
            continue;
        }

        float d_used = r->distance_m;
        float d2_score = 0.0f;
        float r_adapt = 0.0f;

#if ENABLE_MAHALANOBIS_PREFILTER
        bool is_accepted = mw_filter_mahalanobis_update(&s_filters.prefilter, aid - 1, d_used,
                                                        (float)s_last_position.x, (float)s_last_position.y, (float)TAG_HEIGHT_M,
                                                        0.0f, 0.0f, 0.0f, (float)anchor_pos.x, (float)anchor_pos.y, (float)anchor_pos.z,
                                                        &d_used, &d2_score, &r_adapt);
        if (!is_accepted) {
            RLOG_W(LOG_OBJECT_CODE_TAG, "Anchor #%u rejected by Mahalanobis (d2=%.2f)", aid, d2_score);
            continue;
        }
#else
        d_used = mw_filter_distance_smoother_apply(&s_filters.smoother, aid - 1, d_used);
#endif

        double r2d = 0.0;
        double dz = anchor_pos.z - (double)TAG_HEIGHT_M;
        if (!convert_3d_to_2d_distance((double)d_used, dz, &r2d)) {
            RLOG_W(LOG_OBJECT_CODE_TAG, "Anchor #%u: Cannot project to 2D (r3d=%.3fm dz=%.3fm)", aid, d_used, (float)dz);
            continue;
        }

        /* Store valid anchor data */
        anchors_by_id[aid].position = anchor_pos;
        anchors_by_id[aid].distance = (double)r2d;
        anchors_by_id[aid].rssi = (int8_t)r->rssi;
        anchors_by_id[aid].id = aid;
        anchors_by_id[aid].valid = true;
        anchors_by_id[aid].d2_score = (double)d2_score;
        anchors_by_id[aid].r_adaptive = (double)r_adapt;
        valid_count++;

        RLOG_D(LOG_OBJECT_CODE_TAG, "Anchor #%u: r3d=%.3fm -> r2d=%.3fm (dz=%.2fm)", aid, d_used, (float)r2d, (float)dz);
    }

    for (uint8_t id = 1; id <= NUM_ANCHORS; id++) {
        if (anchors_by_id[id].valid) {
            RLOG_I(LOG_OBJECT_CODE_TAG, "  Anchor #%u: dist=%.3fm RSSI=%ddBm",
                   id, anchors_by_id[id].distance, anchors_by_id[id].rssi);
        }
    }

    float uart_distances[NUM_ANCHORS] = {0.0f};
    for (uint8_t id = 1; id <= NUM_ANCHORS; id++) {
        if (anchors_by_id[id].valid) {
            uart_distances[id - 1] = anchors_by_id[id].distance;
        }
    }

    /* Need at least 3 anchors for trilateration */
    if (valid_count < 3) {
        RLOG_W(LOG_OBJECT_CODE_TAG, 
               "Not enough valid anchors: %u/3 minimum", valid_count);
        RLOG_I(LOG_OBJECT_CODE_TAG, "====================================");
        s_error_count++;
        return;
    }

    /* ==== STEP 2.A: Compact Array ==== */
    mw_tril_anchor_t anchors_compact[NUM_ANCHORS];
    uint8_t compact_idx = 0;
    
    for (uint8_t id = 1; id <= NUM_ANCHORS && compact_idx < NUM_ANCHORS; id++) {
        if (anchors_by_id[id].valid) {
            anchors_compact[compact_idx++] = anchors_by_id[id];
        }
    }

    if (compact_idx < 3) {
        RLOG_W(LOG_OBJECT_CODE_TAG, "Not enough anchors passed filter (%u/3)", compact_idx);
        s_error_count++;
        return;
    }

    /* ==== STEP 2.B: Sort & Extract Best Exact 3 ==== */
    mw_tril_anchor_t best_3_anchors[3];
    uint8_t best_count = mw_trilateration_select_best(anchors_compact, compact_idx, best_3_anchors, 3);
    
    if (best_count < 3) {
        s_error_count++;
        return;
    }

    /* ==== STEP 3: Trilateration ==== */
    vec2d_t tril_position;
    mw_tril_result_t tril_result;

    mw_tril_err_t err = mw_trilateration_2d(best_3_anchors, &tril_position, &tril_result);

    if (err != MW_TRIL_OK) {
        RLOG_W(LOG_OBJECT_CODE_TAG, "[TRIL] Failed: %d", err);
        RLOG_I(LOG_OBJECT_CODE_TAG, "====================================");
        s_error_count++;
        return;
    }
    
        /* ==== STEP 4: Quality gating ==== */
#if ENABLE_QUALITY_GATING
    if (tril_result.error_estimate > MAX_ACCEPTABLE_ERROR_M) {
        RLOG_W(LOG_OBJECT_CODE_TAG,
               "[TRIL] Error %.3fm > %.3fm - REJECTED",
               (float)tril_result.error_estimate, MAX_ACCEPTABLE_ERROR_M);
        RLOG_I(LOG_OBJECT_CODE_TAG, "====================================");
        s_error_count++;
        return;
    }
#endif

    /* ==== STEP 5: Final Handling (No Ext Filters) ==== */
    s_last_position.x = tril_position.x;
    s_last_position.y = tril_position.y;
    
    s_success_count++;
    s_error_count = 0;

    RLOG_I(LOG_OBJECT_CODE_TAG, "Tril Px=%.3fm Py=%.3fm Z=%.2fm | Error: \261%.3fm", 
           (float)tril_position.x, (float)tril_position.y, TAG_HEIGHT_M, (float)tril_result.error_estimate);
    RLOG_I(LOG_OBJECT_CODE_TAG, "D2 Scores: #%u(%.1f) #%u(%.1f) #%u(%.1f)",
           best_3_anchors[0].id, best_3_anchors[0].d2_score,
           best_3_anchors[1].id, best_3_anchors[1].d2_score,
           best_3_anchors[2].id, best_3_anchors[2].d2_score);

    if (bsp_io_uart_send_position(tril_position.x, tril_position.y,
                                  TAG_HEIGHT_M,
                                  uart_distances,
                                  (float)tril_result.error_estimate) != BSP_OK) {
        RLOG_W(LOG_OBJECT_CODE_TAG, "[UART] Failed to send position");
    }
}
/* Public functions --------------------------------------------------- */
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

#ifdef PRESET_WORST_CASE
    RLOG_I(LOG_OBJECT_CODE_TAG, "Preset: WORST_CASE");
#elif defined(PRESET_BEST_CASE)
    RLOG_I(LOG_OBJECT_CODE_TAG, "Preset: BEST_CASE");
#else
    RLOG_I(LOG_OBJECT_CODE_TAG, "Preset: MANUAL");
#endif

#if ENABLE_MAHALANOBIS_PREFILTER
    RLOG_I(LOG_OBJECT_CODE_TAG, "Pre-Filter: Mahalanobis ON, smoothing OFF");
#else
    RLOG_I(LOG_OBJECT_CODE_TAG, "Pre-Filter: Mahalanobis OFF, smoothing ON");
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
    s_period_miss_count = 0;
    s_period_overrun_count = 0;

#if ENABLE_TAG_AUTO_CALIB
    RLOG_I(LOG_OBJECT_CODE_TAG, "Calib Mode: Target=%.3fm", tag_calib_get_ref_distance_3d());
    tag_calib_reset();
#else
    sys_ranging_set_calib_status(SYS_CALIB_STATUS_NORMAL);
#endif

    init_filters();
    return APP_OK;
}

void app_tag_process(void)
{
    static uint32_t last_log = 0;
    static uint32_t last_warn_log = 0;
    sys_config_t *cfg = sys_config_get();
    uint32_t now = HAL_GetTick();

#if ENABLE_TAG_AUTO_CALIB
    if (s_tag_app_state == TAG_STATE_CALIB_PENDING_ACCEPT ||
        s_tag_app_state == TAG_STATE_CALIB_DONE) {
        return;
    }
#endif

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
        bool calib_mode = false;

        get_tdma_config(&num_anchors, anchor_ids);

#if ENABLE_TAG_AUTO_CALIB
        calib_mode = (!s_tag_calib.converged &&
                      s_tag_app_state == TAG_STATE_CALIB_COLLECTING);
        if (calib_mode) {
            num_anchors = 1;
            anchor_ids[0] = CALIB_ANCHOR_ID;
        }
#endif

        sys_ranging_err_t start_err = sys_ranging_tag_start_tdma(num_anchors,
                                                                  anchor_ids,
                                                                  s_sequence_num,
                                                                  cfg->uwb.rx_timeout_ms);
        if (start_err == SYS_RANGING_OK) {
            s_sequence_num++;
            s_pending_num_anchors = num_anchors;
            memcpy(s_pending_anchor_ids, anchor_ids, sizeof(s_pending_anchor_ids));
            s_pending_calib_mode = calib_mode;
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
            s_error_count++;
        }
        return;
    }

    /* --- STEP 2: PROCESS RANGING --- */
    if (!s_is_ranging_active) {
        return;
    }

    sys_ranging_err_t err = sys_ranging_tag_process_tdma(s_pending_num_anchors,
                                                          s_pending_anchor_ids,
                                                          cfg->uwb.rx_timeout_ms);

    if (err == SYS_RANGING_OK) {
        sys_ranging_multi_result_t multi_results;
        bool cycle_success = false;
        if (sys_ranging_tag_get_results_tdma(&multi_results) == SYS_RANGING_OK) {
            cycle_success = true;
#if ENABLE_TAG_AUTO_CALIB
            if (s_pending_calib_mode) {
                bool found = false;
                for (uint8_t i = 0; i < multi_results.count; i++) {
                    sys_ranging_result_t *res = &multi_results.results[i];
                    if (res->valid && res->anchor_id == CALIB_ANCHOR_ID) {
                        found = true;
                        if (tag_calib_add_sample(res->distance_m)) {
                            s_tag_app_state = TAG_STATE_CALIB_CALCULATE;
                            tag_calib_calculate_and_adjust();
                        }
                        break;
                    }
                }
                if (!found) {
                    RLOG_W(LOG_OBJECT_CODE_TAG,
                           "[CALIB] No valid result from anchor %u",
                           CALIB_ANCHOR_ID);
                }
            } else {
                process_ranging_results(multi_results.results, multi_results.count);
            }
#else
            process_ranging_results(multi_results.results, multi_results.count);
#endif
        } else {
            s_error_count++;
            RLOG_W(LOG_OBJECT_CODE_TAG, "[TAG] No TDMA results available");
        }

        if (cycle_success) {
            bsp_io_led_blink(5);
        }

        s_last_ranging_tick = HAL_GetTick();
        if (s_cycle_start_tick != 0U) {
            uint32_t cycle_ms = s_last_ranging_tick - s_cycle_start_tick;
            RLOG_I(LOG_OBJECT_CODE_TAG,
                   "[TAG] Cycle complete: duration=%lums target_period=%ums anchors=%u valid=%u",
                   (unsigned long)cycle_ms,
                     (unsigned)cfg->uwb.ranging_period_ms,
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
        update_period_schedule(s_last_ranging_tick, cfg->uwb.ranging_period_ms);
        if ((s_period_miss_count % 10U) == 1U && s_period_miss_count > 0U) {
            RLOG_W(LOG_OBJECT_CODE_TAG,
                   "Period miss accumulated: %lu",
                   (unsigned long)s_period_miss_count);
        }
        s_is_ranging_active = false;
        return;
    }

    if (err == SYS_RANGING_ERR_TIMEOUT ||
        err == SYS_RANGING_ERR ||
        err == SYS_RANGING_ERR_NOT_STARTED) {
        s_error_count++;
        s_last_ranging_tick = HAL_GetTick();
        uint32_t cycle_ms = s_last_ranging_tick - s_cycle_start_tick;
        if (s_error_count % 10 == 0) {
            RLOG_W(LOG_OBJECT_CODE_TAG, "[TAG] Ranging failed: err=%d duration=%lums", err, (unsigned long)cycle_ms);
        }
        
        update_period_schedule(s_last_ranging_tick, cfg->uwb.ranging_period_ms);
        s_is_ranging_active = false;
    }
    
}
/* End of file -------------------------------------------------------- */
