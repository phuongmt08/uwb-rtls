/**
 * @file       app_tag.c
 * @copyright
 * @license
 * @version    3.4.0 (TDMA Full)
 * @date       2026-01-31
 * @author     Phuong Mai
 * @brief      Non-blocking Tag with TDMA, Trilateration and Adaptive Kalman Filter
 */
#include "app_tag.h"
#include "bsp_io.h"
#include "bsp_util.h"
#include "mw_filter.h"
#include "mw_trilateration.h"
#include "positioning_config.h"
#include "sys_config.h"
#include "sys_logger.h"
#include "sys_ranging.h"

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

/* Anchor positions with Z from config */
static const vec3d_t ANCHOR_POSITIONS[NUM_ANCHORS] = {
    {.x = ANCHOR_1_X, .y = ANCHOR_1_Y, .z = ANCHOR_1_Z},
    {.x = ANCHOR_2_X, .y = ANCHOR_2_Y, .z = ANCHOR_2_Z},
    {.x = ANCHOR_3_X, .y = ANCHOR_3_Y, .z = ANCHOR_3_Z}
#if NUM_ANCHORS > 3
    ,{.x = ANCHOR_4_X, .y = ANCHOR_4_Y, .z = ANCHOR_4_Z}
#endif
};

/* Private types */
typedef struct {
#if (MW_FILTER_ENABLE_DES || MW_FILTER_ENABLE_AKF)
    mw_filter_cxt_t filter;
#endif
} filter_state_t;

/* Private variables */
static uint32_t s_error_count = 0;
static uint32_t s_success_count = 0;
static uint32_t s_last_ranging_tick = 0;
static uint8_t s_sequence_num = 0;
static filter_state_t s_filters;
static bool s_is_ranging_active = false;

/* Private prototypes */
static void init_filters(void);
static void process_ranging_results(sys_ranging_result_t *results, int num_success);
static bool convert_3d_to_2d_distance(double r3d, double dz, double *r2d_out);
static void get_tdma_config(uint8_t *num_anchors, uint8_t *anchor_ids);

/* Implementation */

static void init_filters(void)
{
    memset(&s_filters, 0, sizeof(s_filters));

#if (MW_FILTER_ENABLE_DES || MW_FILTER_ENABLE_AKF)
    /* Initialize Adaptive Kalman Filter at center of anchor layout */
#if NUM_ANCHORS < 4
    float init_x = (ANCHOR_1_X + ANCHOR_2_X + ANCHOR_3_X) / 3.0f;
    float init_y = (ANCHOR_1_Y + ANCHOR_2_Y + ANCHOR_3_Y) / 3.0f;
#else
    float init_x = (ANCHOR_1_X + ANCHOR_2_X + ANCHOR_3_X + ANCHOR_4_X) / 4.0f;
    float init_y = (ANCHOR_1_Y + ANCHOR_2_Y + ANCHOR_3_Y + ANCHOR_4_Y) / 4.0f;
#endif

    sys_config_t *cfg = sys_config_get();
    float dt = cfg->ranging_period_ms / 1000.0f;

    mw_filter_init(&s_filters.filter, init_x, init_y, dt,
                   DES_ALPHA_BASE, DES_BETA,
                   AKF_PROCESS_NOISE, AKF_R_BASE,
                   AKF_INNOVATION_ALPHA, AKF_R_SCALE_MIN, AKF_R_SCALE_MAX);
#endif
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
    uint8_t total = (NUM_ANCHORS > MAX_ANCHORS) ? MAX_ANCHORS : NUM_ANCHORS;
    *num_anchors = total;

    for (uint8_t i = 0; i < total; i++) {
        anchor_ids[i] = i + 1;
    }
}

static void process_ranging_results(sys_ranging_result_t *results, int num_success)
{
    // RLOG_I(LOG_OBJECT_CODE_TAG, "========== RANGING #%lu ==========", s_success_count + 1);  // DISABLED - causes 20ms delay

    mw_tril_anchor_t anchors_by_id[NUM_ANCHORS + 1];
    uint8_t valid_count = 0;
    
    for (uint8_t i = 0; i <= NUM_ANCHORS; i++) anchors_by_id[i].valid = false;

    /* Iterate through the array of results returned by TDMA */
    /* Note: 'num_success' is the number of valid items in the 'results' array */
    for (int i = 0; i < num_success; i++) {
        sys_ranging_result_t *r = &results[i];
        if (!r->valid) continue;

        uint8_t aid = r->anchor_id;
        /* Sanity check anchor ID */
        if (aid < 1 || aid > NUM_ANCHORS) continue;

         double r3d = (double)r->distance_m;
         
         /* Calculate vertical offset based on specific anchor height */
         double dz = ANCHOR_POSITIONS[aid - 1].z - (double)TAG_HEIGHT_M;
         double r2d = 0.0;
         
         if (!convert_3d_to_2d_distance(r3d, dz, &r2d)) {
             RLOG_W(LOG_OBJECT_CODE_TAG, 
                 "Anchor #%u: Cannot project to 2D (r3d=%.3fm dz=%.3fm)",
                 aid, (float)r3d, (float)dz);
             continue;
         }
         
         /* Use raw 2D distance - AKF will handle filtering */
         float distance_2d = (float)r2d;
         int8_t rssi = (int8_t)r->rssi;
         
         /* Fill anchor data at correct position (indexed by anchor_id) */
         anchors_by_id[aid].position = ANCHOR_POSITIONS[aid - 1];
         anchors_by_id[aid].distance = distance_2d;
         anchors_by_id[aid].rssi = rssi;
         anchors_by_id[aid].id = aid;
         anchors_by_id[aid].valid = true;
         valid_count++;
         
         RLOG_D(LOG_OBJECT_CODE_TAG,
             "Anchor #%u: r3d=%.3fm -> r2d=%.3fm (dz=%.2fm)",
             aid, (float)r3d, (float)r2d, (float)dz);
    }

    for (uint8_t id = 1; id <= NUM_ANCHORS; id++) {
        if (anchors_by_id[id].valid) {
            RLOG_I(LOG_OBJECT_CODE_TAG, "  Anchor #%u: dist=%.3fm RSSI=%ddBm",
                   id, anchors_by_id[id].distance, anchors_by_id[id].rssi);
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

    /* ==== STEP 2: Prepare compact array for trilateration ==== */
    mw_tril_anchor_t anchors_compact[NUM_ANCHORS];
    uint8_t compact_idx = 0;
    
    for (uint8_t id = 1; id <= NUM_ANCHORS && compact_idx < NUM_ANCHORS; id++) {
        if (anchors_by_id[id].valid) {
            anchors_compact[compact_idx++] = anchors_by_id[id];
        }
    }

    /* ==== STEP 3: Trilateration (auto-select best 3) ==== */
    vec2d_t tril_position;
    mw_tril_result_t tril_result;

    mw_tril_err_t err = mw_trilateration_2d(anchors_compact, valid_count,
                                            &tril_position, &tril_result);

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

    /* ==== STEP 5: Apply Filter ==== */
#if (MW_FILTER_ENABLE_DES || MW_FILTER_ENABLE_AKF)
    pos_vel_2d_t final_position;

    float R_scale = mw_filter_update(&s_filters.filter,
                                     tril_position.x, tril_position.y,
                                     &final_position);

    s_success_count++;
    s_error_count = 0;

    float velocity = sqrtf(final_position.vx * final_position.vx +
                          final_position.vy * final_position.vy);
    
    RLOG_I(LOG_OBJECT_CODE_TAG, "Raw:      X=%.3fm Y=%.3fm Z=%.2fm", (float)tril_position.x, (float)tril_position.y, TAG_HEIGHT_M);
    RLOG_I(LOG_OBJECT_CODE_TAG, "Filtered: X=%.3fm Y=%.3fm (V=%.3fm/s)",
           final_position.x, final_position.y, velocity);
    RLOG_I(LOG_OBJECT_CODE_TAG, "Quality:  Error=%.3fm R_adapt=%.2f",
           (float)tril_result.error_estimate, R_scale);

    if (bsp_io_uart_send_position(final_position.x, final_position.y,
                                  TAG_HEIGHT_M,
                                  (float)tril_result.error_estimate) != BSP_OK) {
        RLOG_W(LOG_OBJECT_CODE_TAG, "[UART] Failed to send position");
    }

#else
    /* No Filter - use raw trilateration */
    s_success_count++;
    s_error_count = 0;

    // RLOG_I(LOG_OBJECT_CODE_TAG, "Position: X=%.3fm Y=%.3fm Z=%.2fm", (float)tril_position.x, (float)tril_position.y, TAG_HEIGHT_M);
    // RLOG_I(LOG_OBJECT_CODE_TAG, "Error:    %.3fm", (float)tril_result.error_estimate);

    /* Send position via UART */
    if (bsp_io_uart_send_position((float)tril_position.x, (float)tril_position.y,
                                  TAG_HEIGHT_M,
                                  (float)tril_result.error_estimate) != BSP_OK) {
        RLOG_W(LOG_OBJECT_CODE_TAG, "[UART] Failed to send position");
    }
#endif
}

app_err_t app_tag_init(void)
{
    sys_config_t *cfg = sys_config_get();
    RLOG_I(LOG_OBJECT_CODE_TAG, "========== TAG INIT (TDMA) ==========");
    RLOG_I(LOG_OBJECT_CODE_TAG, "ID: %d | Interval: %dms", cfg->device_id, cfg->ranging_period_ms);
    
    /* Log ranging period from config */
    uint32_t update_hz = (cfg->ranging_period_ms > 0) ? (1000 / cfg->ranging_period_ms) : 0;
    RLOG_I(LOG_OBJECT_CODE_TAG, "Update rate: %dms (%luHz)",
           cfg->ranging_period_ms, update_hz);

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

#if (MW_FILTER_ENABLE_DES || MW_FILTER_ENABLE_AKF)
    RLOG_I(LOG_OBJECT_CODE_TAG, "Filter: %s%s%s",
           MW_FILTER_ENABLE_DES ? "DES" : "",
           (MW_FILTER_ENABLE_DES && MW_FILTER_ENABLE_AKF) ? " + " : "",
           MW_FILTER_ENABLE_AKF ? "AKF" : "");
#else
    RLOG_I(LOG_OBJECT_CODE_TAG, "Filter: DISABLED (Raw trilateration)");
#endif

    RLOG_I(LOG_OBJECT_CODE_TAG, "Anchor positions:");
    for (uint8_t i = 0; i < NUM_ANCHORS; i++) {
        RLOG_I(LOG_OBJECT_CODE_TAG, "  #%d: X=%.2fm Y=%.2fm Z=%.2fm",
               i + 1, 
               (float)ANCHOR_POSITIONS[i].x,
               (float)ANCHOR_POSITIONS[i].y,
               (float)ANCHOR_POSITIONS[i].z);
    }

    RLOG_I(LOG_OBJECT_CODE_TAG, "==============================");

    init_filters();
    return APP_OK;
}

void app_tag_process(void)
{
    static uint32_t last_log = 0;
    sys_config_t *cfg = sys_config_get();
    uint32_t now = HAL_GetTick();

    /* Debug every 1s */
    if ((now - last_log) >= 1000) {
        uint32_t delta = now - s_last_ranging_tick;
        RLOG_I(LOG_OBJECT_CODE_TAG, "[D] act=%d last=%lu now=%lu delta=%lu period=%lu OK=%d",
               s_is_ranging_active, s_last_ranging_tick, now, delta, 
               cfg->ranging_period_ms, (delta >= cfg->ranging_period_ms));
        last_log = now;
    }

    /* --- STEP 1: TRIGGER RANGING --- */
    if (!s_is_ranging_active) {
        /* Check update rate period */
        if ((now - s_last_ranging_tick) >= cfg->ranging_period_ms) {
            uint8_t num_anchors = 1;
            uint8_t anchor_ids[MAX_ANCHORS] = {0};
            get_tdma_config(&num_anchors, anchor_ids);

            s_is_ranging_active = true;
            bsp_io_led_on();

            sys_ranging_err_t err = sys_ranging_tag_run_tdma_blocking(num_anchors,
                                                                       anchor_ids,
                                                                       s_sequence_num++,
                                                                       cfg->rx_timeout_ms);

            bsp_io_led_off();
            s_last_ranging_tick = HAL_GetTick();

            if (err == SYS_RANGING_OK) {
                sys_ranging_multi_result_t multi_results;
                if (sys_ranging_tag_get_results_tdma(&multi_results) == SYS_RANGING_OK) {
                    process_ranging_results(multi_results.results, multi_results.count);
                } else {
                    s_error_count++;
                    RLOG_W(LOG_OBJECT_CODE_TAG, "[TAG] No TDMA results available");
                }
            } else {
                s_error_count++;
                if (s_error_count % 10 == 0)
                    RLOG_W(LOG_OBJECT_CODE_TAG, "[TAG] Ranging failed");
            }

            s_is_ranging_active = false;
        }
    }
}
