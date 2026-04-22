/**
 * @file       app_tag.c
 * @copyright
 * @license
 * @version    3.3.0
 * @date       2026-01-10
 * @author     Phuong Mai
 * @brief      Non-blocking Tag with AKF (Adaptive Kalman Filter)
 * @note       
 * Pipeline:
 *   1. Raw 3D distance → Convert to 2D planar distance (height compensation)
 *   2. 2D planar distance → Trilateration (auto-select best 3)
 *   3. Trilateration position → Adaptive Kalman Filter (AKF)
 *   4. AKF: Innovation-based automatic R adaptation (no pre-filtering needed!)
 * @example    None
 */
#include "app_tag.h"

#include "bsp_io.h"
#include "bsp_util.h"
#include "bsp_uwb.h"
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
#if NUM_ANCHORS > 3
    ,{.x = ANCHOR_4_X, .y = ANCHOR_4_Y, .z = ANCHOR_4_Z}
#endif
};

/* Private types ------------------------------------------------------ */
#if ENABLE_TAG_AUTO_CALIB
typedef enum {
    TAG_STATE_IDLE = 0,
    TAG_STATE_CALIB_COLLECTING,
    TAG_STATE_CALIB_CALCULATE,
    TAG_STATE_CALIB_PENDING_ACCEPT,
    TAG_STATE_CALIB_DONE
} tag_app_state_t;

typedef struct {
    float distances[CALIB_SAMPLES];
    uint16_t count;
    float mean;
    float error;
    float last_error;
    uint16_t current_delay;
    uint16_t delta_step;
    uint16_t round;
    bool converged;
} tag_calib_state_t;
#endif
typedef struct {
#if (MW_FILTER_ENABLE_DES || MW_FILTER_ENABLE_AKF)
    mw_filter_cxt_t filter;
#endif
} filter_state_t;

/* Private variables -------------------------------------------------- */
static uint32_t s_error_count = 0;
static uint32_t s_success_count = 0;
static uint32_t s_last_ranging_tick = 0;
static uint8_t s_sequence_num = 0;
static filter_state_t s_filters;
#if ENABLE_TAG_AUTO_CALIB
static tag_calib_state_t s_tag_calib = {0};
static tag_app_state_t s_tag_app_state = TAG_STATE_IDLE;
#endif

/* Private function prototypes ---------------------------------------- */
static void init_filters(void);
static void process_ranging_results(sys_ranging_result_t *results, int num_success);
static bool convert_3d_to_2d_distance(double r3d, double dz, double *r2d_out);
#if ENABLE_TAG_AUTO_CALIB
static void tag_calib_reset(void);
static bool tag_calib_add_sample(float distance);
static void tag_calib_calculate_and_adjust(void);
static void tag_calib_apply_and_save(void);
static float tag_calib_get_ref_distance_3d(void);
#endif

/* Private function implementations ----------------------------------- */

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
    float dt = cfg->uwb.ranging_period_ms / 1000.0f;

    mw_filter_init(&s_filters.filter, init_x, init_y, dt,
                   DES_ALPHA_BASE, DES_BETA,
                   AKF_PROCESS_NOISE, AKF_R_BASE,
                   AKF_INNOVATION_ALPHA, AKF_R_SCALE_MIN, AKF_R_SCALE_MAX);
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

#if ENABLE_TAG_AUTO_CALIB
static float tag_calib_get_ref_distance_3d(void)
{
    float dz = (float)(CALIB_ANCHOR_HEIGHT_M - CALIB_TAG_HEIGHT_M);
    return sqrtf(CALIB_REF_DISTANCE_XY_M * CALIB_REF_DISTANCE_XY_M + dz * dz);
}

static void tag_calib_reset(void)
{
    memset(&s_tag_calib, 0, sizeof(s_tag_calib));

    sys_config_t *cfg = sys_config_get();
    s_tag_calib.current_delay = cfg->uwb.tx_antenna_delay;
    s_tag_calib.delta_step = 100;
    s_tag_calib.last_error = 999.0f;
    s_tag_calib.converged = false;

    s_tag_app_state = TAG_STATE_CALIB_COLLECTING;
    RLOG_I(LOG_OBJECT_CODE_TAG, "[CALIB] Start: delay=%u target=%.3fm",
           s_tag_calib.current_delay, tag_calib_get_ref_distance_3d());
}

static bool tag_calib_add_sample(float distance)
{
    if (s_tag_calib.count >= CALIB_SAMPLES) {
        return true;
    }

    if (distance < 0.1f || distance > 50.0f) {
        return false;
    }

    s_tag_calib.distances[s_tag_calib.count++] = distance;

    if (s_tag_calib.count % 5 == 0) {
        bsp_io_led_toggle();
    }

    return (s_tag_calib.count >= CALIB_SAMPLES);
}

static void tag_calib_calculate_and_adjust(void)
{
    if (s_tag_calib.count < CALIB_SAMPLES) {
        return;
    }

    float sum = 0.0f;
    for (uint16_t i = 0; i < s_tag_calib.count; i++) {
        sum += s_tag_calib.distances[i];
    }
    s_tag_calib.mean = sum / s_tag_calib.count;

    float variance = 0.0f;
    for (uint16_t i = 0; i < s_tag_calib.count; i++) {
        float diff = s_tag_calib.distances[i] - s_tag_calib.mean;
        variance += diff * diff;
    }
    float std_dev = sqrtf(variance / s_tag_calib.count);

    if (std_dev > CALIB_MAX_STD_M) {
        RLOG_W(LOG_OBJECT_CODE_TAG,
               "[R%u] REJECTED std=%.3fm > %.3fm",
               s_tag_calib.round + 1, std_dev, CALIB_MAX_STD_M);
        s_tag_calib.count = 0;
        return;
    }

    s_tag_calib.error = s_tag_calib.mean - tag_calib_get_ref_distance_3d();
    s_tag_calib.round++;

    RLOG_I(LOG_OBJECT_CODE_TAG, "[R%u] mean=%.3fm std=%.3fm err=%+.3fm delay=%u step=%u",
           s_tag_calib.round, s_tag_calib.mean, std_dev, s_tag_calib.error,
           s_tag_calib.current_delay, s_tag_calib.delta_step);

    if (fabsf(s_tag_calib.error) < CALIB_ERROR_THRESHOLD_M) {
        RLOG_I(LOG_OBJECT_CODE_TAG, "[CALIB] DONE! delay=%u err=%.3fm",
               s_tag_calib.current_delay, s_tag_calib.error);
        RLOG_I(LOG_OBJECT_CODE_TAG, "HOLD=accept CLICK=retry");
        s_tag_calib.converged = true;
        s_tag_app_state = TAG_STATE_CALIB_PENDING_ACCEPT;
        bsp_io_led_on();
        return;
    }

    if (s_tag_calib.round >= CALIB_MAX_ROUNDS || s_tag_calib.delta_step < CALIB_MIN_DELTA_STEP) {
        RLOG_W(LOG_OBJECT_CODE_TAG, "[CALIB] STOP! delay=%u err=%.3fm",
               s_tag_calib.current_delay, s_tag_calib.error);
        RLOG_I(LOG_OBJECT_CODE_TAG, "HOLD=accept CLICK=retry");
        s_tag_calib.converged = true;
        s_tag_app_state = TAG_STATE_CALIB_PENDING_ACCEPT;
        bsp_io_led_on();
        return;
    }

    if (s_tag_calib.error * s_tag_calib.last_error < 0.0f) {
        s_tag_calib.delta_step = s_tag_calib.delta_step / 2;
    }

    int32_t new_delay;
    if (s_tag_calib.error > 0.0f) {
        new_delay = (int32_t)s_tag_calib.current_delay + s_tag_calib.delta_step;
    } else {
        new_delay = (int32_t)s_tag_calib.current_delay - s_tag_calib.delta_step;
    }

    if (new_delay < 0) new_delay = 0;
    if (new_delay > 65535) new_delay = 65535;

    s_tag_calib.last_error = s_tag_calib.error;
    s_tag_calib.current_delay = (uint16_t)new_delay;

    sys_config_t *cfg = sys_config_get();
    protobuf_uwb_cfg_t tmp = cfg->uwb;
    tmp.tx_antenna_delay = s_tag_calib.current_delay;
    tmp.rx_antenna_delay = s_tag_calib.current_delay;
    bsp_uwb_configure(&tmp);
    s_tag_calib.count = 0;
    s_tag_app_state = TAG_STATE_CALIB_COLLECTING;
}

static void tag_calib_apply_and_save(void)
{
    if (!s_tag_calib.converged) return;

    RLOG_I(LOG_OBJECT_CODE_TAG, "[CALIB] Saving TX/RX delay=%u...", s_tag_calib.current_delay);

    sys_config_t *cfg = sys_config_get();
    cfg->uwb.tx_antenna_delay = s_tag_calib.current_delay;
    cfg->uwb.rx_antenna_delay = s_tag_calib.current_delay;

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
    }
}
#endif

static void process_ranging_results(sys_ranging_result_t *results, int num_success)
{

    RLOG_I(LOG_OBJECT_CODE_TAG, "========== RANGING #%lu ==========", 
           s_success_count + 1);
    for (uint8_t i = 0; i < NUM_ANCHORS; i++) {
        RLOG_I(LOG_OBJECT_CODE_TAG, 
               "  [%u] ID=%u Valid=%d Dist=%.3fm RSSI=%ddBm",
               i,
               results[i].anchor_id,
               results[i].valid,
               results[i].distance_m,
               results[i].rssi);
    }
    
    /* ==== STEP 1: Convert 3D to 2D planar distance ==== */
    
    /* Use array indexed by anchor_id for proper mapping */
    mw_tril_anchor_t anchors_by_id[NUM_ANCHORS + 1]; /* +1 for 1-based indexing */
    uint8_t valid_count = 0;
    
    /* Initialize all as invalid */
    for (uint8_t i = 0; i <= NUM_ANCHORS; i++) {
        anchors_by_id[i].valid = false;
    }

    /* Process each ranging result */
    for (uint8_t i = 0; i < NUM_ANCHORS; i++) {
        uint8_t anchor_id = results[i].anchor_id;
        
        /* Validate anchor_id */
        if (anchor_id < 1 || anchor_id > NUM_ANCHORS || !results[i].valid) {
            RLOG_W(LOG_OBJECT_CODE_TAG, 
                   "Anchor #%u: REJECTED (id_range:%d valid:%d)",
                   anchor_id,
                   (anchor_id >= 1 && anchor_id <= NUM_ANCHORS),
                   results[i].valid);
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
        
        /* Use raw 2D distance - AKF will handle filtering */
        float distance_2d = (float)r2d;
        int8_t rssi = (int8_t)results[i].rssi;
        
        /* Fill anchor data at correct position (indexed by anchor_id) */
        anchors_by_id[anchor_id].position = ANCHOR_POSITIONS[anchor_idx];
        anchors_by_id[anchor_id].distance = distance_2d;
        anchors_by_id[anchor_id].rssi = rssi;
        anchors_by_id[anchor_id].id = anchor_id;
        anchors_by_id[anchor_id].valid = true;
        valid_count++;
        
        RLOG_D(LOG_OBJECT_CODE_TAG,
               "Anchor #%u: r3d=%.3fm -> r2d=%.3fm (dz=%.2fm)",
               anchor_id, (float)r3d, (float)r2d, (float)dz);
    }

    for (uint8_t id = 1; id <= NUM_ANCHORS; id++) {
        if (anchors_by_id[id].valid) {
            RLOG_I(LOG_OBJECT_CODE_TAG, "  Anchor #%u: dist=%.3fm RSSI=%ddBm",
                   id, anchors_by_id[id].distance, anchors_by_id[id].rssi);
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

    float anchor_distance[NUM_ANCHORS] = {0};
    for (uint8_t id = 1; id <= NUM_ANCHORS; id++) {
        if (anchors_by_id[id].valid) {
            anchor_distance[id - 1] = anchors_by_id[id].distance;
        }
    }

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
    
    RLOG_I(LOG_OBJECT_CODE_TAG, "Raw:      X=%.3fm Y=%.3fm Z=%.2fm",
           (float)tril_position.x, (float)tril_position.y, TAG_HEIGHT_M);
    RLOG_I(LOG_OBJECT_CODE_TAG, "Filtered: X=%.3fm Y=%.3fm (V=%.3fm/s)",
           final_position.x, final_position.y, velocity);
    RLOG_I(LOG_OBJECT_CODE_TAG, "Quality:  Error=%.3fm R_adapt=%.2f",
           (float)tril_result.error_estimate, R_scale);

    if (bsp_io_uart_send_position(final_position.x, final_position.y,
                                  TAG_HEIGHT_M,
								  anchor_distance,
                                  (float)tril_result.error_estimate) != BSP_OK) {
        RLOG_W(LOG_OBJECT_CODE_TAG, "[UART] Failed to send position");
    }

#else
    /* No Filter - use raw trilateration */
    s_success_count++;
    s_error_count = 0;

    RLOG_I(LOG_OBJECT_CODE_TAG, "Position: X=%.3fm Y=%.3fm Z=%.2fm",
           (float)tril_position.x, (float)tril_position.y, TAG_HEIGHT_M);
    RLOG_I(LOG_OBJECT_CODE_TAG, "Error:    %.3fm", (float)tril_result.error_estimate);

    /* Send position via UART */
    if (bsp_io_uart_send_position((float)tril_position.x, (float)tril_position.y,
                                  TAG_HEIGHT_M,
                                  anchor_distance,
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
    RLOG_I(LOG_OBJECT_CODE_TAG, "Tag ID: 0x%02X", cfg->uwb.device_id);
    
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

#if (MW_FILTER_ENABLE_DES || MW_FILTER_ENABLE_AKF)
    RLOG_I(LOG_OBJECT_CODE_TAG, "Filter: %s%s%s",
           MW_FILTER_ENABLE_DES ? "DES" : "",
           (MW_FILTER_ENABLE_DES && MW_FILTER_ENABLE_AKF) ? " + " : "",
           MW_FILTER_ENABLE_AKF ? "AKF" : "");
#if MW_FILTER_ENABLE_DES
    RLOG_I(LOG_OBJECT_CODE_TAG, "  DES: alpha=%.2f beta=%.2f", 
           DES_ALPHA_BASE, DES_BETA);
#endif
#if MW_FILTER_ENABLE_AKF
    RLOG_I(LOG_OBJECT_CODE_TAG, "  AKF: Q=%.3f R=%.2f", 
           AKF_PROCESS_NOISE, AKF_R_BASE);
#endif
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

#if ENABLE_TAG_AUTO_CALIB
    RLOG_I(LOG_OBJECT_CODE_TAG, "Calib Mode: Target=%.3fm", tag_calib_get_ref_distance_3d());
    tag_calib_reset();
#endif

    init_filters();

    return APP_OK;
}

void app_tag_process(void)
{
    sys_config_t *cfg = sys_config_get();
    uint32_t current_tick = HAL_GetTick();

#if ENABLE_TAG_AUTO_CALIB
    if (s_tag_app_state == TAG_STATE_CALIB_PENDING_ACCEPT ||
        s_tag_app_state == TAG_STATE_CALIB_DONE) {
        return;
    }
#endif

    if ((current_tick - s_last_ranging_tick) < cfg->uwb.ranging_period_ms) {
        return;
    }

    /* Record ranging start time BEFORE ranging starts */
    s_last_ranging_tick = current_tick;

    /* LED on during ranging */
    bsp_io_led_on();

    uint8_t anchor_ids[NUM_ANCHORS] = {0};
    uint8_t anchor_count = NUM_ANCHORS;

#if ENABLE_TAG_AUTO_CALIB
    if (!s_tag_calib.converged && s_tag_app_state == TAG_STATE_CALIB_COLLECTING) {
        anchor_ids[0] = CALIB_ANCHOR_ID;
        anchor_count = 1;
    } else {
#endif
#if NUM_ANCHORS < 4
        anchor_ids[0] = 1;
        anchor_ids[1] = 2;
        anchor_ids[2] = 3;
#else
        anchor_ids[0] = 1;
        anchor_ids[1] = 2;
        anchor_ids[2] = 3;
        anchor_ids[3] = 4;
#endif
#if ENABLE_TAG_AUTO_CALIB
    }
#endif

    sys_ranging_result_t results[NUM_ANCHORS] = {0};

    int num_success = sys_ranging_tag_multi_anchor(anchor_ids, anchor_count,
                                                   results, s_sequence_num++,
                                                   cfg->uwb.rx_timeout_ms);

    bsp_io_led_off();

    if (num_success > 0) {
#if ENABLE_TAG_AUTO_CALIB
        if (!s_tag_calib.converged && s_tag_app_state == TAG_STATE_CALIB_COLLECTING) {
            if (results[0].valid && results[0].anchor_id == CALIB_ANCHOR_ID) {
                if (tag_calib_add_sample(results[0].distance_m)) {
                    s_tag_app_state = TAG_STATE_CALIB_CALCULATE;
                    tag_calib_calculate_and_adjust();
                }
            } else {
                RLOG_W(LOG_OBJECT_CODE_TAG, "[CALIB] No valid result from anchor %u", CALIB_ANCHOR_ID);
            }
        } else {
            process_ranging_results(results, num_success);
        }
#else
        process_ranging_results(results, num_success);
#endif
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
