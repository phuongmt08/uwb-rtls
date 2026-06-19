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
#include "app_rtos_handles.h"
#include "bsp_io.h"
#include "bsp_util.h"
#include "bsp_uwb.h"
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
#if ENABLE_SYS_FUSION
#include "sys_sensor_fusion.h"
#endif

#if !ENABLE_SYS_FUSION
#include "sys_sensor_fusion.h"
#include "bsp_imu.h"
#endif

#if ENABLE_SYS_FUSION
#ifdef SYS_FUSION_PREFILTER_ENABLED
#undef SYS_FUSION_PREFILTER_ENABLED
#endif
#define SYS_FUSION_PREFILTER_ENABLED 0
#endif

/* Private types ------------------------------------------------------ */
typedef struct {
#if !ENABLE_SYS_FUSION
    mahalanobis_prefilter_t prefilter;
#else
    char dummy;
#endif
} filter_state_t;

/* Private variables -------------------------------------------------- */
static uint32_t s_error_count = 0;
static uint32_t s_success_count = 0;
static uint32_t s_last_ranging_tick = 0;
static uint8_t s_sequence_num = 0;
#if !ENABLE_SYS_FUSION
static filter_state_t s_filters;
#endif
static bool s_is_ranging_active = false;
static uint8_t s_pending_num_anchors = 0;
static uint8_t s_pending_anchor_ids[NUM_ANCHORS] = {0};
static uint32_t s_next_due_tick = 0;
static uint32_t s_cycle_start_tick = 0;
static uint32_t s_last_cycle_done_tick = 0;
static uint32_t s_period_miss_count = 0;
static uint32_t s_period_overrun_count = 0;
static bool s_position_valid = false;
static uint8_t s_last_selected_anchors_mask = 0;

#if !ENABLE_SYS_FUSION
static vec2d_t s_latest_fusion_position = {.x = 0.0f, .y = 0.0f};
static bool s_latest_fusion_position_valid = false;
static uint8_t s_last_selected_anchors_mask = 0;
static float s_latest_distances[NUM_ANCHORS] = {0};
static double s_latest_fp_amp_norm[NUM_ANCHORS] = {0};
static double s_latest_fp_snr[NUM_ANCHORS] = {0};
static float s_latest_ranging_dt = 0.0f;
static uint32_t s_fusion_log_seq = 0U;
static bool s_ukf_initialized = false;
static ukf_init_filter_t s_ukf_init_filter;
static ukf_init_distance_filter_t s_ukf_init_dist_filter;
static uint32_t s_last_fusion_log_tick = 0U;
#endif

/* Private prototypes --------------------------------------------------- */
#if !ENABLE_SYS_FUSION
static void init_filters(void);
static bool convert_3d_to_2d_distance(double r3d, double dz, double *r2d_out);
static bool get_anchor_position(uint8_t aid, vec3d_t *pos_out);
static bool ensure_minimum_ranging_anchors(uint8_t count, const char *context);
#endif
static void process_ranging_results(sys_ranging_result_t *results, int num_success);
static void get_tdma_config(uint8_t *num_anchors, uint8_t *anchor_ids);
static uint32_t estimate_tdma_cycle_ms(uint8_t num_anchors);
static void update_period_schedule(uint32_t now_tick, uint32_t period_ms);
static void record_ranging_error(void);
static void finish_failed_ranging_cycle(sys_ranging_err_t err,
                                        uint32_t now_tick,
                                        uint32_t period_ms,
                                        bool abort_ranging,
                                        const char *reason);
#if !ENABLE_SYS_FUSION
static void record_fusion_log_update_timing(void);
static void send_fusion_log_snapshot(void);
#endif

/* Private functions --------------------------------------------------- */
#if !ENABLE_SYS_FUSION
static void init_filters(void)
{
    memset(&s_filters, 0, sizeof(s_filters));

#if SYS_FUSION_PREFILTER_ENABLED
    /* Fusion-predicted Mahalanobis prefilter state:
     * T1 = recover threshold, T2 = reject threshold, R = adaptive output base. */
    mw_filter_mahalanobis_init(&s_filters.prefilter,
                               MAHALANOBIS_PREFILTER_D2_RECOVER,
                               MAHALANOBIS_PREFILTER_D2_REJECT,
                               MAHALANOBIS_PREFILTER_R_BASE);
#endif

    mw_filter_ukf_init_reset(&s_ukf_init_filter);
    mw_filter_ukf_init_distance_reset(&s_ukf_init_dist_filter);
    for (uint8_t i = 0; i < NUM_ANCHORS; i++) {
        s_latest_distances[i] = 0.0f;
        s_latest_fp_amp_norm[i] = 0.0;
        s_latest_fp_snr[i] = 0.0;
    }
    s_latest_ranging_dt = 0.0f;
    s_fusion_log_seq = 0U;
    s_last_fusion_log_tick = 0U;
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
#endif

static void get_tdma_config(uint8_t *num_anchors, uint8_t *anchor_ids)
{
    uint8_t total = (NUM_ANCHORS > 8) ? 8 : NUM_ANCHORS;
    *num_anchors = total;

    for (uint8_t i = 0; i < total; i++) {
        anchor_ids[i] = i + 1;
    }
}

#if !ENABLE_SYS_FUSION
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
#endif

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

#if !ENABLE_SYS_FUSION
static bool ensure_minimum_ranging_anchors(uint8_t count, const char *context)
{
    if (count >= 3U) {
        return true;
    }

    RLOG_W(LOG_OBJECT_CODE_TAG, "%s: %u/3 anchors", context, count);
    record_ranging_error();
    return false;
}
#endif

#if !ENABLE_SYS_FUSION
static void record_fusion_log_update_timing(void)
{
    uint32_t now = HAL_GetTick();

    if (s_last_fusion_log_tick == 0U) {
        s_latest_ranging_dt = 0.0f;
    } else {
        uint32_t dt_ms = now - s_last_fusion_log_tick;
        if (dt_ms > 5000U) dt_ms = 5000U;
        if (dt_ms < 1U) dt_ms = 1U;
        s_latest_ranging_dt = (float)dt_ms / 1000.0f;
    }

    s_last_fusion_log_tick = now;
    s_fusion_log_seq++;
}

static void send_fusion_log_snapshot(void)
{
    if (!s_latest_fusion_position_valid) {
        return;
    }

    bsp_imu_data_t imu_data = {0};
    (void)bsp_imu_get_raw_data(&imu_data);
    bsp_io_uart_send_fusion_log_data(s_last_selected_anchors_mask,
                                     s_error_count,
                                     imu_data.ax,
                                     imu_data.ay,
                                     imu_data.gz,
                                     (float)s_latest_fusion_position.x,
                                     (float)s_latest_fusion_position.y,
                                     s_latest_distances,
                                     s_latest_fp_amp_norm,
                                     s_latest_fp_snr,
                                     s_latest_ranging_dt);
}
#endif

static void process_ranging_results(sys_ranging_result_t *results, int num_success)
{
    /* Active SensorFusion owns projection/trilateration/UKF. Tag task only feeds raw ranges. */
#if ENABLE_SYS_FUSION
    uwb_distance_msg_t msg = {0};
    uint8_t valid_count = 0;
    msg.count = 0;
    msg.mask  = 0;
    for (int i = 0; i < num_success; i++) {
        sys_ranging_result_t *r = &results[i];
        uint8_t aid = r->anchor_id;
        if (aid < 1 || aid > NUM_ANCHORS) continue;

        msg.distances[aid - 1] = r->distance_m;
        msg.anchor_ids[aid - 1] = aid;
        msg.fp_amp_norm[aid - 1] = (float)r->fp_amp_norm_q8 / 256.0f;
        msg.fp_snr[aid - 1] = (float)r->fp_snr_q8 / 256.0f;

        if (r->valid) {
            msg.mask |= (1 << (aid - 1));
            valid_count++;
        }
        msg.count++;
    }

    if (valid_count < 3U) {
        record_ranging_error();
        return;
    }

    osMessageQueuePut(g_uwb_distance_queue, &msg, 0, 0);
    s_success_count++;
    s_error_count = 0;
#else
    mw_tril_anchor_t anchors_by_id[NUM_ANCHORS + 1];
    uint8_t valid_count = 0;
    
    for (uint8_t i = 0; i <= NUM_ANCHORS; i++) anchors_by_id[i].valid = false;

    float anchor_distances[NUM_ANCHORS] = {0.0f};
#if (SYS_FUSION_PREFILTER_ENABLED && (MAHALANOBIS_PREFILTER_RESCUE_MIN_ANCHORS > 0U))
    mw_tril_anchor_t prefilter_rejects[NUM_ANCHORS];
    uint8_t prefilter_reject_count = 0U;
#endif
    
    /* 1. Extract, Filter and Project Ranging Results */
    for (int i = 0; i < num_success; i++) {
        sys_ranging_result_t *r = &results[i];
        uint8_t aid = r->anchor_id;
        if (aid < 1 || aid > NUM_ANCHORS) continue;
        anchor_distances[aid - 1] = r->distance_m;

        if (!r->valid) {
            RLOG_W(LOG_OBJECT_CODE_TAG,
                   "Anchor #%u invalid distance marker %.3fm - skipped for position",
                   aid, r->distance_m);
            continue;
        }

        vec3d_t anchor_pos;
        if (!get_anchor_position(aid, &anchor_pos)) {
            RLOG_W(LOG_OBJECT_CODE_TAG, "Anchor #%u position not found in flash", aid);
            continue;
        }

        float d_used = r->distance_m;
        float d2_score = 0.0f;
        float r_adapt = MAHALANOBIS_PREFILTER_R_BASE;

        double r2d = 0.0;
        double dz = anchor_pos.z - (double)TAG_HEIGHT_M;
        if (!convert_3d_to_2d_distance((double)d_used, dz, &r2d)) {
            RLOG_W(LOG_OBJECT_CODE_TAG, "Anchor #%u: Cannot project to 2D (r3d=%.3fm dz=%.3fm)", aid, d_used, (float)dz);
            continue;
        }
        anchor_distances[aid - 1] = (float)r2d;
        d_used = (float)r2d;

        mw_tril_anchor_t anchor_entry = {0};
        anchor_entry.position = anchor_pos;
        anchor_entry.distance = (double)r2d;
        anchor_entry.id = aid;
        anchor_entry.valid = true;
        anchor_entry.r_adaptive = (double)r_adapt;
        anchor_entry.fp_amp_norm = (double)r->fp_amp_norm_q8 / 256.0;
        anchor_entry.fp_snr = (double)r->fp_snr_q8 / 256.0;
        RLOG_I(LOG_OBJECT_CODE_TAG,
               "[FP] Anchor #%u amp_norm=%.3f snr=%.3f raw_amp_q8=%u raw_snr_q8=%u",
               aid,
               anchor_entry.fp_amp_norm,
               anchor_entry.fp_snr,
               (unsigned)r->fp_amp_norm_q8,
               (unsigned)r->fp_snr_q8);
        anchor_entry.quality_valid = (r->quality != 0U);
        anchor_entry.selection_score = 0.0;
        anchor_entry.residual_rms = 0.0;
        anchor_entry.gdop_penalty = 0.0;
        anchor_entry.fp_penalty = 0.0;

#if SYS_FUSION_PREFILTER_ENABLED
        if (s_ukf_initialized) {
            bool pass = mw_filter_mahalanobis_update(&s_filters.prefilter,
                                                     aid - 1U,
                                                     d_used,
                                                     ukf_data.px,
                                                     ukf_data.py,
                                                     TAG_HEIGHT_M,
                                                     ukf_data.vx,
                                                     ukf_data.vy,
                                                     0.0f,
                                                     (float)anchor_pos.x,
                                                     (float)anchor_pos.y,
                                                     (float)anchor_pos.z,
                                                     &d_used,
                                                     &d2_score,
                                                     &r_adapt);
            anchor_entry.d2_score = (double)d2_score;
            anchor_entry.r_adaptive = (double)r_adapt;

            if (!pass) {
#if (MAHALANOBIS_PREFILTER_RESCUE_MIN_ANCHORS > 0U)
                if (prefilter_reject_count < NUM_ANCHORS) {
                    prefilter_rejects[prefilter_reject_count++] = anchor_entry;
                }
#endif
                RLOG_W(LOG_OBJECT_CODE_TAG,
                       "Anchor #%u rejected by fusion mw_filter Mahalanobis (d2=%.2f r2d=%.3fm)",
                       aid, d2_score, d_used);
                continue;
            }
        } else {
            anchor_entry.d2_score = (double)d2_score;
        }
#else
        anchor_entry.d2_score = (double)d2_score;
#endif

        anchors_by_id[aid] = anchor_entry;
        valid_count++;

    }

#if (SYS_FUSION_PREFILTER_ENABLED && (MAHALANOBIS_PREFILTER_RESCUE_MIN_ANCHORS > 0U))
    if (valid_count < MAHALANOBIS_PREFILTER_RESCUE_MIN_ANCHORS &&
        prefilter_reject_count > 0U) {
        for (uint8_t i = 1U; i < prefilter_reject_count; i++) {
            mw_tril_anchor_t key = prefilter_rejects[i];
            int j = (int)i - 1;
            while (j >= 0 && prefilter_rejects[j].d2_score > key.d2_score) {
                prefilter_rejects[j + 1] = prefilter_rejects[j];
                j--;
            }
            prefilter_rejects[j + 1] = key;
        }

        uint8_t rescue_target = MAHALANOBIS_PREFILTER_RESCUE_MIN_ANCHORS;
        if (rescue_target > NUM_ANCHORS) rescue_target = NUM_ANCHORS;
        for (uint8_t i = 0U; i < prefilter_reject_count && valid_count < rescue_target; i++) {
            uint8_t aid = prefilter_rejects[i].id;
            if (aid == 0U || aid > NUM_ANCHORS || anchors_by_id[aid].valid) {
                continue;
            }
            anchors_by_id[aid] = prefilter_rejects[i];
            valid_count++;
            RLOG_W(LOG_OBJECT_CODE_TAG,
                   "Anchor #%u rescued by fusion mw_filter Mahalanobis (d2=%.2f, valid=%u/%u)",
                   aid,
                   prefilter_rejects[i].d2_score,
                   valid_count,
                   rescue_target);
        }
    }
#endif

    for (uint8_t id = 1; id <= NUM_ANCHORS; id++) {
        if (anchors_by_id[id].valid) {
            anchor_distances[id - 1] = anchors_by_id[id].distance;
        }
    }

    /* Need at least 3 anchors for trilateration */
    if (valid_count < 3) {
        RLOG_I(LOG_OBJECT_CODE_TAG,
               "Dist A1=%.3fm A2=%.3fm A3=%.3fm A4=%.3fm",
               anchor_distances[0], anchor_distances[1], anchor_distances[2], anchor_distances[3]);
        RLOG_I(LOG_OBJECT_CODE_TAG, "====================================");
        (void)ensure_minimum_ranging_anchors(valid_count, "Not enough valid anchors");
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

    if (!ensure_minimum_ranging_anchors(compact_idx, "Not enough anchors passed filter")) {
        return;
    }

    /* ==== STEP 2.B: Sort & Extract Best Exact 3 ==== */
    mw_tril_anchor_t best_3_anchors[3];
    uint8_t best_count = mw_trilateration_select_best(anchors_compact, compact_idx, best_3_anchors, 3, s_last_selected_anchors_mask);
    
    if (!ensure_minimum_ranging_anchors(best_count, "Not enough anchors selected")) {
        return;
    }

    /* Build anchor selection mask */
    s_last_selected_anchors_mask = 0;
    for (uint8_t i = 0; i < 3; i++) {
        s_last_selected_anchors_mask |= (1 << (best_3_anchors[i].id - 1));
    }

#if !ENABLE_SYS_FUSION
    /* ==== STEP 3-ALT: UKF Initialization or Update (LOG mode) ==== */
    if (!s_ukf_initialized)
    {
        vec2d_t tril_position;
        mw_tril_result_t tril_result;

        mw_tril_err_t err = mw_trilateration_2d(best_3_anchors, &tril_position, &tril_result);

        if (err != MW_TRIL_OK) {
            RLOG_W(LOG_OBJECT_CODE_TAG, "[TRIL] Failed: %d", err);
            RLOG_I(LOG_OBJECT_CODE_TAG, "====================================");
            return;
        }

        float init_x, init_y;
        float init_d0, init_d1, init_d2;
        bool pos_done = mw_filter_ukf_init_add(&s_ukf_init_filter, (float)tril_position.x, (float)tril_position.y, &init_x, &init_y);
        bool dist_done = mw_filter_ukf_init_distance_add(&s_ukf_init_dist_filter, (float)best_3_anchors[0].distance, (float)best_3_anchors[1].distance, (float)best_3_anchors[2].distance, &init_d0, &init_d1, &init_d2);

        if (pos_done && dist_done)
        {
            /* Set initial position for UKF */
            s_ukf_initialized = true;

            RLOG_I(LOG_OBJECT_CODE_TAG, "[UKF Init] Tril Px=%.3fm Py=%.3fm Z=%.2fm", init_x, init_y, TAG_HEIGHT_M);

            for(int i=0; i<NUM_ANCHORS; i++) s_latest_distances[i] = 0.0f;
            s_latest_distances[best_3_anchors[0].id - 1] = init_d0;
            s_latest_distances[best_3_anchors[1].id - 1] = init_d1;
            s_latest_distances[best_3_anchors[2].id - 1] = init_d2;

            for (uint8_t i = 0; i < NUM_ANCHORS; i++) {
                s_latest_fp_amp_norm[i] = anchors_by_id[i + 1].fp_amp_norm;
                s_latest_fp_snr[i] = anchors_by_id[i + 1].fp_snr;
            }

            sys_sensor_fusion_set_initial_position(&ukf_data, init_x, init_y);
            sys_sensor_fusion_set_predict_flag();
            s_latest_fusion_position.x = init_x;
            s_latest_fusion_position.y = init_y;
            s_latest_fusion_position_valid = true;
            record_fusion_log_update_timing();
            send_fusion_log_snapshot();

        }
        else
        {
            /* Still collecting data to initialize UKF */
            int collected = s_ukf_init_filter.count >= UKF_INIT_DISCARD_SAMPLES ? s_ukf_init_filter.count - UKF_INIT_DISCARD_SAMPLES : 0;
            RLOG_I(LOG_OBJECT_CODE_TAG, "[UKF Init] Collecting %d/%d (discarded %d/%d)",
                   collected, UKF_INIT_SAMPLES,
                   s_ukf_init_filter.count < UKF_INIT_DISCARD_SAMPLES ? s_ukf_init_filter.count : UKF_INIT_DISCARD_SAMPLES,
                   UKF_INIT_DISCARD_SAMPLES);
        }
    }
    else
    {

        for(int i=0; i<NUM_ANCHORS; i++) s_latest_distances[i] = 0.0f;
        for(int i=0; i<compact_idx; i++)
        {
            s_latest_distances[anchors_compact[i].id - 1] = (float)anchors_compact[i].distance;
        }

    	/* ==== STEP 3: Trilateration ==== */
		vec2d_t tril_position;
		mw_tril_result_t tril_result;

		mw_tril_err_t err = mw_trilateration_2d(best_3_anchors, &tril_position, &tril_result);

		if (err != MW_TRIL_OK) {
			RLOG_W(LOG_OBJECT_CODE_TAG, "[TRIL] Failed: %d", err);
			RLOG_I(LOG_OBJECT_CODE_TAG, "====================================");
            return;
		}

		s_last_selected_anchors_mask = 0;
		for (uint8_t i = 0; i < 3; i++) {
			s_last_selected_anchors_mask |= (1 << (best_3_anchors[i].id - 1));
		}
        uint8_t selected_anchor_mask = s_last_selected_anchors_mask;

        for (uint8_t i = 0; i < NUM_ANCHORS; i++) {
            s_latest_fp_amp_norm[i] = anchors_by_id[i + 1].fp_amp_norm;
            s_latest_fp_snr[i] = anchors_by_id[i + 1].fp_snr;
        }

        sys_sensor_fusion_update(&ukf_data,
                                 best_3_anchors[0].distance,
                                 best_3_anchors[1].distance,
                                 best_3_anchors[2].distance,
                                 selected_anchor_mask);
        s_latest_fusion_position = tril_position;
        s_latest_fusion_position_valid = true;
        record_fusion_log_update_timing();
        send_fusion_log_snapshot();
    }

    s_success_count++;
#else
    /* ==== STEP 3: Trilateration (Default/Calibration mode) ==== */
    vec2d_t tril_position;
    mw_tril_result_t tril_result;
    mw_tril_err_t err = mw_trilateration_2d(best_3_anchors, &tril_position, &tril_result);

    if (err != MW_TRIL_OK) {
        RLOG_W(LOG_OBJECT_CODE_TAG, "[TRIL] Failed: %d", err);
        RLOG_I(LOG_OBJECT_CODE_TAG, "====================================");
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

    /* ==== STEP 5: Final Handling ==== */
    vec2d_t final_position = tril_position;

    s_last_position.x = final_position.x;
    s_last_position.y = final_position.y;
    s_position_valid = true;
    
    s_success_count++;
    s_error_count = 0;

    RLOG_I(LOG_OBJECT_CODE_TAG,
           "Dist A1=%.3fm A2=%.3fm A3=%.3fm A4=%.3fm",
           anchor_distances[0], anchor_distances[1], anchor_distances[2], anchor_distances[3]);
    RLOG_I(LOG_OBJECT_CODE_TAG,
           "Pos x=%.3fm y=%.3fm z=%.2fm err=%.3fm",
           (float)final_position.x, (float)final_position.y,
           TAG_HEIGHT_M, (float)tril_result.error_estimate);

    if (bsp_io_uart_send_position(final_position.x, final_position.y,
                                  TAG_HEIGHT_M,
                                  anchor_distances,
                                  (float)tril_result.error_estimate) != BSP_OK) {
        RLOG_W(LOG_OBJECT_CODE_TAG, "[UART] Failed to send position");
    }
#endif
#endif
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
           "Pre-Filter: fusion mw_filter Mahalanobis ON (rescue_min=%u)",
           (unsigned)MAHALANOBIS_PREFILTER_RESCUE_MIN_ANCHORS);
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

#if !ENABLE_SYS_FUSION
    if (sys_sensor_fusion_init(&ukf_data) != SYS_SENSOR_FUSION_OK) {
        RLOG_E(LOG_OBJECT_CODE_TAG, ERR_SYSTEM, "Sensor fusion initialization failed");
    } else {
        RLOG_I(LOG_OBJECT_CODE_TAG, "Sensor fusion initialized successfully");
    }
#endif

#if !ENABLE_SYS_FUSION
    init_filters();
#endif
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
            cycle_success = true;
            process_ranging_results(multi_results.results, multi_results.count);
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
    sys_sensor_fusion_clear_predict_flag();
    sys_sensor_fusion_clear_update_flag();
#if !ENABLE_SYS_FUSION
    init_filters();
#endif
#if !ENABLE_SYS_FUSION
    s_latest_fusion_position_valid = false;
    s_latest_fusion_position.x = 0.0f;
    s_latest_fusion_position.y = 0.0f;
#endif
    s_is_ranging_active = false;
    s_error_count = 0;
    
#if !ENABLE_SYS_FUSION
    s_ukf_initialized = false;
    if (sys_sensor_fusion_init(&ukf_data) != SYS_SENSOR_FUSION_OK) {
        RLOG_W(LOG_OBJECT_CODE_TAG, "[FUSION] UKF re-initialization failed");
    } else {
        RLOG_I(LOG_OBJECT_CODE_TAG, "[FUSION] UKF re-initialized successfully");
    }
#else
    /* For active Sensor Fusion, delegate the reset to sys_sensor_fusion. */
    sys_sensor_fusion_reset();
#endif
}

/* End of file -------------------------------------------------------- */
