/* ============================== app_tag.c ==================================
 * @file       app_tag.c
 * @brief      Tag application with 2D positioning (3 anchors)
 * @version    2.0.0
 * @date       2025-12-20
 */

/* Includes ----------------------------------------------------------- */
#include "app_tag.h"
#include "sys_ranging.h"
#include "sys_config.h"
#include "sys_logger.h"
#include "bsp_io.h"
#include "bsp_util.h"
#include "mw_trilateration.h"
#include <stdint.h>
#include <stdio.h>

/* Configuration ------------------------------------------------------ */
#define RANGING_INTERVAL_MS     (100)   /* Range every 100ms */
#define MAX_CONSECUTIVE_ERR     (5)
#define NUM_ANCHORS             (3)     /* Fixed: 3 anchors */

/* Anchor positions (MUST BE MEASURED!)
 * Example: Triangle setup on ceiling/wall
 * TODO: Measure your actual anchor positions and update here!
 */
static const vec3d_t ANCHOR_POSITIONS[NUM_ANCHORS] = {
    {.x = 0.0, .y = 0.0, .z = 2.5},    // Anchor #1
    {.x = 5.0, .y = 0.0, .z = 2.5},    // Anchor #2
    {.x = 2.5, .y = 5.0, .z = 2.5}     // Anchor #3
};

/* Private variables -------------------------------------------------- */
static uint32_t s_error_count = 0;
static uint32_t s_success_count = 0;
static uint32_t s_last_ranging_tick = 0;
static uint8_t s_sequence_num = 0;

/* Private function prototypes ---------------------------------------- */
static void calculate_and_log_position(sys_ranging_result_t *ranging_results);

/* Private function implementations ----------------------------------- */

static void calculate_and_log_position(sys_ranging_result_t *ranging_results)
{
    /* Prepare trilateration input */
    mw_tril_anchor_t anchors[NUM_ANCHORS];
    
    for (uint8_t i = 0; i < NUM_ANCHORS; i++) {
        uint8_t anchor_id = ranging_results[i].anchor_id;
        
        /* Map anchor_id (1,2,3) to array index (0,1,2) */
        if (anchor_id < 1 || anchor_id > NUM_ANCHORS) {
            RLOG_W(LOG_OBJECT_CODE_TAG, "[POS] Invalid anchor ID: %u", anchor_id);
            return;
        }
        
        anchors[i].position = ANCHOR_POSITIONS[anchor_id - 1];  // ID 1-3 → index 0-2
        anchors[i].distance = ranging_results[i].distance_m;
        anchors[i].rssi = ranging_results[i].rssi;
        anchors[i].id = anchor_id;
        anchors[i].valid = ranging_results[i].valid;
    }
    
    /* Calculate 2D position */
    vec2d_t position;
    mw_tril_result_t result;
    
    mw_tril_err_t err = mw_trilateration_2d(anchors, NUM_ANCHORS, &position, &result);
    
    if (err == MW_TRIL_OK) {
        /* Success! */
        s_success_count++;
        
        RLOG_I(LOG_OBJECT_CODE_TAG, 
               "========== POSITION #%lu ==========", 
               s_success_count);
        RLOG_I(LOG_OBJECT_CODE_TAG, 
               "X: %.2f m, Y: %.2f m", 
               position.x, position.y);
        RLOG_I(LOG_OBJECT_CODE_TAG, 
               "Error: %.2f m", 
               result.error_estimate);
        RLOG_I(LOG_OBJECT_CODE_TAG, 
               "====================================");
        
        /* TODO: Send position via UART/BLE/Display */
        
    } else {
        RLOG_W(LOG_OBJECT_CODE_TAG, "[POS] Calculation failed: %d", err);
        
        /* Debug: show which anchor caused the problem */
        for (uint8_t i = 0; i < NUM_ANCHORS; i++) {
            if (!anchors[i].valid) {
                RLOG_W(LOG_OBJECT_CODE_TAG, "  Anchor #%u: INVALID", anchors[i].id);
            } else {
                RLOG_D(LOG_OBJECT_CODE_TAG, 
                       "  Anchor #%u: %.2fm, RSSI=%ddBm", 
                       anchors[i].id, anchors[i].distance, anchors[i].rssi);
            }
        }
    }
}

/* Public function definitions ---------------------------------------- */

app_err_t app_tag_init(void)
{
    sys_config_t *cfg = sys_config_get();
    
    RLOG_I(LOG_OBJECT_CODE_TAG, "========== TAG INIT ==========");
    RLOG_I(LOG_OBJECT_CODE_TAG, "Tag ID: 0x%02X", cfg->device_id);
    RLOG_I(LOG_OBJECT_CODE_TAG, "Mode: 2D Positioning");
    RLOG_I(LOG_OBJECT_CODE_TAG, "Anchors: %d", NUM_ANCHORS);
    RLOG_I(LOG_OBJECT_CODE_TAG, "==============================");
    
    /* Log anchor positions for verification */
    RLOG_I(LOG_OBJECT_CODE_TAG, "Anchor layout:");
    for (uint8_t i = 0; i < NUM_ANCHORS; i++) {
        RLOG_I(LOG_OBJECT_CODE_TAG, 
               "  Anchor #%d: X=%.2f Y=%.2f Z=%.2f", 
               i+1, 
               ANCHOR_POSITIONS[i].x, 
               ANCHOR_POSITIONS[i].y, 
               ANCHOR_POSITIONS[i].z);
    }

    return APP_OK;
}

void app_tag_process(void)
{
    sys_config_t *cfg = sys_config_get();
    uint32_t current_tick = HAL_GetTick();
    
    /* Rate limiting */
    if ((current_tick - s_last_ranging_tick) < RANGING_INTERVAL_MS) {
        return;
    }
    s_last_ranging_tick = current_tick;
    
    /* LED on during ranging */
    bsp_io_led_on();
    
#ifdef MULTIPLE_ANCHOR
    /* Ranging with 3 anchors */
    const uint8_t anchor_ids[NUM_ANCHORS] = {1, 2, 3};
    sys_ranging_result_t ranging_results[NUM_ANCHORS];
    
    int num_success = sys_ranging_tag_multi_anchor(
        anchor_ids, 
        NUM_ANCHORS, 
        ranging_results, 
        s_sequence_num++, 
        cfg->rx_timeout_ms
    );
    
    /* LED off */
    bsp_io_led_off();
    
    if (num_success == NUM_ANCHORS) {
        /* All 3 anchors successful - calculate position */
        s_error_count = 0;
        calculate_and_log_position(ranging_results);
        
    } else if (num_success >= 2) {
        /* Got 2 anchors, not enough for trilateration */
        RLOG_W(LOG_OBJECT_CODE_TAG, 
               "[TAG] Only %d/%d anchors responded", 
               num_success, NUM_ANCHORS);
        s_error_count++;
        
    } else {
        /* 0-1 anchor, total failure */
        RLOG_E(LOG_OBJECT_CODE_TAG, ERR_UWB_RANGING, 
               "[TAG] Ranging failed (%d/%d)", num_success, NUM_ANCHORS);
        s_error_count++;
        
        if (s_error_count >= MAX_CONSECUTIVE_ERR) {
            RLOG_E(LOG_OBJECT_CODE_TAG, ERR_TIMEOUT,
                   "Too many errors (%lu), check anchors!", s_error_count);
            s_error_count = 0;
        }
    }
    
#else
    /* MULTIPLE_ANCHOR not defined - cannot do positioning */
    #error "MULTIPLE_ANCHOR must be defined for positioning!"
#endif
}

/* End of file -------------------------------------------------------- */