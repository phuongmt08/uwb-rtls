/**
 * @file       app_tag.c
 * @copyright
 * @license
 * @version    3.2.0
 * @date       2025-12-24
 * @author     Phuong Mai
 * @brief      Non-blocking Tag with filtering and trilateration
 * @note       
 * Pipeline:
 *   1. Raw 3D distance → Convert to 2D planar distance (height compensation)
 *   2. 2D distance → EMA filter (optional)
 *   3. Raw RSSI → EMA filter (optional)
 *   4. Filtered 2D distance + RSSI → Trilateration (auto-select best 3)
 *   5. Trilateration position → Kalman 2D
 *   6. Kalman R: Fixed tuning OR adaptive from RSSI
 * @example    None
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

/* Anchor positions with Z from config -------------------------------- */
static const vec3d_t ANCHOR_POSITIONS[NUM_ANCHORS] = {
    {.x = ANCHOR_1_X, .y = ANCHOR_1_Y, .z = ANCHOR_1_Z},
    {.x = ANCHOR_2_X, .y = ANCHOR_2_Y, .z = ANCHOR_2_Z},
    {.x = ANCHOR_3_X, .y = ANCHOR_3_Y, .z = ANCHOR_3_Z}
};

/* Private types ------------------------------------------------------ */
typedef struct {
#if ENABLE_DISTANCE_FILTER
    ema_filter_t dist_filter[NUM_ANCHORS];
#endif
#if ENABLE_RSSI_FILTER
    ema_filter_t rssi_filter[NUM_ANCHORS];
#endif
#if MW_FILTER_ENABLE_KALMAN_2D
    kalman_2d_t kalman;
#endif
} filter_state_t;

/* Private variables -------------------------------------------------- */
static uint32_t s_error_count = 0;
static uint32_t s_success_count = 0;
static uint32_t s_last_ranging_tick = 0;
static uint8_t s_sequence_num = 0;
static filter_state_t s_filters;

/* Private function prototypes ---------------------------------------- */
static void init_filters(void);
static void process_ranging_results(sys_ranging_result_t *results, int num_success);
static float rssi_to_r_scale(float avg_rssi);
static bool convert_3d_to_2d_distance(double r3d, double dz, double *r2d_out);

/* Private function implementations ----------------------------------- */

static void init_filters(void)
{
    memset(&s_filters, 0, sizeof(s_filters));

    for (uint8_t i = 0; i < NUM_ANCHORS; i++) {
#if ENABLE_DISTANCE_FILTER
        mw_filter_ema_init(&s_filters.dist_filter[i], DISTANCE_EMA_ALPHA);
#endif
#if ENABLE_RSSI_FILTER
        mw_filter_ema_init(&s_filters.rssi_filter[i], RSSI_EMA_ALPHA);
#endif
    }

#if MW_FILTER_ENABLE_KALMAN_2D
    /* Initialize at center of anchor layout */
    float init_x = (ANCHOR_1_X + ANCHOR_2_X + ANCHOR_3_X) / 4.0f;
    float init_y = (ANCHOR_1_Y + ANCHOR_2_Y + ANCHOR_3_Y) / 4.0f;
    
    /* Get dt from config ranging_period_ms */
    sys_config_t *cfg = sys_config_get();
    float dt = cfg->ranging_period_ms / 1000.0f;

    mw_filter_kalman2d_init(&s_filters.kalman, init_x, init_y, dt,
                           KALMAN_PROCESS_NOISE, KALMAN_MEASURE_NOISE);
#endif
}

/**
 * @brief Convert 3D slant distance to 2D planar distance with height compensation
 * 
 * Formula:
 *   dz = z_anchor - z_tag
 *   r_xy = sqrt(r_meas² - dz²)
 * 
 * @param r3d Measured 3D distance (slant range from UWB)
 * @param dz Vertical offset (anchor_z - tag_z)
 * @param r2d_out Output: 2D planar distance (horizontal projection)
 * @return true if conversion successful, false if invalid
 */
static bool convert_3d_to_2d_distance(double r3d, double dz, double *r2d_out)
{
    /* Validate input range */
    if (r3d < MIN_VALID_DISTANCE_M || r3d > MAX_VALID_DISTANCE_M) {
        return false;
    }
    
    /* Check if 3D distance is physically possible given height difference
     * If r3d <= |dz|, the measurement is invalid (tag can't be that close 
     * while maintaining the height difference)
     */
    double dz_abs = fabs(dz);
    if (r3d <= dz_abs + 1e-6) {  /* Add small epsilon for floating point */
        return false;
    }
    
    /* Calculate 2D distance using Pythagorean theorem:
     * r_3d² = r_2d² + dz²
     * r_2d = sqrt(r_3d² - dz²)
     */
    double r2d_sq = r3d * r3d - dz * dz;
    if (r2d_sq < 0.0) {
        return false;  /* Should not happen after above check, but safety */
    }
    
    *r2d_out = sqrt(r2d_sq);
    return true;
}


static float rssi_to_r_scale(float avg_rssi)
{

    if (avg_rssi > RSSI_THRESHOLD_EXCELLENT) {
        return 1.0f;
    } else if (avg_rssi > RSSI_THRESHOLD_GOOD) {
        float range = RSSI_THRESHOLD_EXCELLENT - RSSI_THRESHOLD_GOOD;
        float scale = 1.0f + ((RSSI_THRESHOLD_EXCELLENT - avg_rssi) / range) * 1.0f;
        return scale;
    } else if (avg_rssi > RSSI_THRESHOLD_MODERATE) {
        float range = RSSI_THRESHOLD_GOOD - RSSI_THRESHOLD_MODERATE;
        float scale = 2.0f + ((RSSI_THRESHOLD_GOOD - avg_rssi) / range) * 2.0f;
        return scale;
    } else if (avg_rssi > RSSI_THRESHOLD_POOR) {
        float range = RSSI_THRESHOLD_MODERATE - RSSI_THRESHOLD_POOR;
        float scale = 4.0f + ((RSSI_THRESHOLD_MODERATE - avg_rssi) / range) * 4.0f;
        return scale;
    } else {
        return 8.0f;
    }
}

static void process_ranging_results(sys_ranging_result_t *results, int num_success)
{
    /* ==== STEP 1: Convert 3D to 2D + Apply EMA filters ==== */
    
    /* Use array indexed by anchor_id for proper mapping */
    mw_tril_anchor_t anchors_by_id[NUM_ANCHORS + 1]; /* +1 for 1-based indexing */
    float filtered_rssi_sum = 0.0f;
    uint8_t valid_count = 0;
    
    /* Initialize all as invalid */
    for (uint8_t i = 0; i <= NUM_ANCHORS; i++) {
        anchors_by_id[i].valid = false;
    }

    /* Process each ranging result */
    for (uint8_t i = 0; i < num_success && i < NUM_ANCHORS; i++) {
        uint8_t anchor_id = results[i].anchor_id;
        
        /* Validate anchor_id */
        if (anchor_id < 1 || anchor_id > NUM_ANCHORS || !results[i].valid) {
            if (results[i].anchor_id != 0) {  /* Don't log if completely invalid */
                RLOG_W(LOG_OBJECT_CODE_TAG, "Anchor #%u: Invalid ID or result", anchor_id);
            }
            continue;
        }
        
        /* Get anchor array index (1-based ID to 0-based index) */
        uint8_t anchor_idx = anchor_id - 1;
        
        /* Get 3D distance from ranging */
        double r3d = (double)results[i].distance_m;
        
        /* Calculate vertical offset for this specific anchor */
        double dz = ANCHOR_POSITIONS[anchor_idx].z - (double)TAG_HEIGHT_M;
        
        /* Convert 3D slant distance to 2D planar distance */
        double r2d = 0.0;
        if (!convert_3d_to_2d_distance(r3d, dz, &r2d)) {
            RLOG_W(LOG_OBJECT_CODE_TAG, 
                   "Anchor #%u: Cannot project to 2D (r3d=%.3fm dz=%.3fm)",
                   anchor_id, (float)r3d, (float)dz);
            continue;
        }
        
        /* Apply distance EMA filter (on 2D distance) */
        float filtered_dist = (float)r2d;
#if ENABLE_DISTANCE_FILTER
        filtered_dist = mw_filter_ema_update(&s_filters.dist_filter[anchor_idx], 
                                            (float)r2d);
#endif

        /* Apply RSSI EMA filter */
        float filtered_rssi = (float)results[i].rssi;
#if ENABLE_RSSI_FILTER
        filtered_rssi = mw_filter_ema_update(&s_filters.rssi_filter[anchor_idx],
                                            (float)results[i].rssi);
#endif

        filtered_rssi_sum += filtered_rssi;
        
        /* Fill anchor data at correct position (indexed by anchor_id) */
        anchors_by_id[anchor_id].position = ANCHOR_POSITIONS[anchor_idx];
        anchors_by_id[anchor_id].distance = filtered_dist;  /* 2D planar distance! */
        anchors_by_id[anchor_id].rssi = (int8_t)filtered_rssi;
        anchors_by_id[anchor_id].id = anchor_id;
        anchors_by_id[anchor_id].valid = true;
        valid_count++;
        
        /* Debug: show conversion */
        RLOG_D(LOG_OBJECT_CODE_TAG,
               "Anchor #%u: r3d=%.3fm -> r2d=%.3fm (dz=%.2fm, filt=%.3fm)",
               anchor_id, (float)r3d, (float)r2d, (float)dz, filtered_dist);
    }

    /* ALWAYS log individual anchor distances (even if <3 anchors) */
    RLOG_I(LOG_OBJECT_CODE_TAG, "========== RANGING #%lu ==========", 
           s_success_count + 1);
    for (uint8_t id = 1; id <= NUM_ANCHORS; id++) {
        if (anchors_by_id[id].valid) {
            RLOG_I(LOG_OBJECT_CODE_TAG, "Anchor #%u: dist=%.3fm RSSI=%ddBm",
                   id, (float)anchors_by_id[id].distance, anchors_by_id[id].rssi);
        }
    }

    /* Need at least 3 anchors for trilateration - use valid_count not num_success */
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

    /* ==== STEP 5: Calculate R_scale from filtered RSSI ==== */
    float R_scale = 1.0f;

#if ENABLE_RSSI_ADAPTIVE
    if (valid_count > 0) {
        float avg_rssi = filtered_rssi_sum / valid_count;
        R_scale = rssi_to_r_scale(avg_rssi);
    }
#endif

    /* ==== STEP 6: Apply Kalman filter ==== */
#if MW_FILTER_ENABLE_KALMAN_2D
    pos_vel_2d_t final_position;

    bool ok = mw_filter_kalman2d_update(&s_filters.kalman,
                                       tril_position.x, tril_position.y,
                                       R_scale, &final_position);

    if (!ok) {
        RLOG_W(LOG_OBJECT_CODE_TAG, "[KALMAN] Update failed");
        RLOG_I(LOG_OBJECT_CODE_TAG, "====================================");
        s_error_count++;
        return;
    }

    /* Success! */
    s_success_count++;
    s_error_count = 0;

    /* Log results */
    float velocity = sqrtf(final_position.vx * final_position.vx +
                          final_position.vy * final_position.vy);
    
    RLOG_I(LOG_OBJECT_CODE_TAG, "Raw:      X=%.3fm Y=%.3fm Z=%.2fm",
           (float)tril_position.x, (float)tril_position.y, TAG_HEIGHT_M);
    RLOG_I(LOG_OBJECT_CODE_TAG, "Filtered: X=%.3fm Y=%.3fm (V=%.3fm/s)",
           final_position.x, final_position.y, velocity);
    RLOG_I(LOG_OBJECT_CODE_TAG, "Quality:  Error=%.3fm R_scale=%.2f",
           (float)tril_result.error_estimate, R_scale);

    /* Send position via UART (X, Y, Z, ERROR) */
    if (bsp_io_uart_send_position(final_position.x, final_position.y,
                                  TAG_HEIGHT_M,
                                  (float)tril_result.error_estimate) != BSP_OK) {
        RLOG_W(LOG_OBJECT_CODE_TAG, "[UART] Failed to send position");
    }

#else
    /* No Kalman - use raw trilateration */
    s_success_count++;
    s_error_count = 0;

    RLOG_I(LOG_OBJECT_CODE_TAG, "Position: X=%.3fm Y=%.3fm Z=%.2fm",
           (float)tril_position.x, (float)tril_position.y, TAG_HEIGHT_M);
    RLOG_I(LOG_OBJECT_CODE_TAG, "Error:    %.3fm", (float)tril_result.error_estimate);

    /* Send position via UART */
    if (bsp_io_uart_send_position((float)tril_position.x, (float)tril_position.y,
                                  TAG_HEIGHT_M,
                                  (float)tril_result.error_estimate) != BSP_OK) {
        RLOG_W(LOG_OBJECT_CODE_TAG, "[UART] Failed to send position");
    }
#endif

    RLOG_I(LOG_OBJECT_CODE_TAG, "====================================");
}

/* Public function definitions ---------------------------------------- */

app_err_t app_tag_init(void)
{
    sys_config_t *cfg = sys_config_get();

    RLOG_I(LOG_OBJECT_CODE_TAG, "========== TAG INIT ==========");
    RLOG_I(LOG_OBJECT_CODE_TAG, "Tag ID: 0x%02X", cfg->device_id);
    
    /* Log ranging period from config */
    uint32_t update_hz = (cfg->ranging_period_ms > 0) ? (1000 / cfg->ranging_period_ms) : 0;
    RLOG_I(LOG_OBJECT_CODE_TAG, "Update rate: %dms (%luHz)",
           cfg->ranging_period_ms, update_hz);

    /* Log height configuration */
    RLOG_I(LOG_OBJECT_CODE_TAG, "Height: Tag=%.2fm Anchor=%.2fm dZ=%.2fm",
           TAG_HEIGHT_M, ANCHOR_HEIGHT_M, HEIGHT_OFFSET_M);

    /* Log active preset */
#ifdef PRESET_TEST_WORST_CASE
    RLOG_I(LOG_OBJECT_CODE_TAG, "Preset: TEST_WORST_CASE");
#elif defined(PRESET_HIGH_SPEED_VEHICLE)
    RLOG_I(LOG_OBJECT_CODE_TAG, "Preset: HIGH_SPEED_VEHICLE");
#else
    RLOG_I(LOG_OBJECT_CODE_TAG, "Preset: MANUAL");
#endif

    /* Log anchor positions */
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
    sys_config_t *cfg = sys_config_get();
    uint32_t current_tick = HAL_GetTick();

    if ((current_tick - s_last_ranging_tick) < cfg->ranging_period_ms) {
        return;
    }

    /* Record ranging start time BEFORE ranging starts */
    s_last_ranging_tick = current_tick;

    /* LED on during ranging */
    bsp_io_led_on();

    /* Use anchor_ids array matching NUM_ANCHORS from config */
    const uint8_t anchor_ids[NUM_ANCHORS] = {1, 2, 3};
    sys_ranging_result_t results[NUM_ANCHORS];

    int num_success = sys_ranging_tag_multi_anchor(anchor_ids, NUM_ANCHORS,
                                                   results, s_sequence_num++,
                                                   cfg->rx_timeout_ms);

    bsp_io_led_off();

    /* Process results */
    if (num_success > 0) {
        process_ranging_results(results, num_success);
    } else {
        RLOG_W(LOG_OBJECT_CODE_TAG, "[TAG] No anchors responded");
        s_error_count++;

        if (s_error_count >= MAX_CONSECUTIVE_ERR) {
            char err_str[16];
            snprintf(err_str, sizeof(err_str), "%lu", s_error_count);
            RLOG_E(LOG_OBJECT_CODE_TAG, ERR_TIMEOUT,
                   "Too many errors (%s), check anchors!", err_str);
            s_error_count = 0;
        }
    }
}

/* End of file -------------------------------------------------------- */