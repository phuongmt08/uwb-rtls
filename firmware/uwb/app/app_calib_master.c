/**
 * @file       app_calib_master.c
 * @brief      Center-tag calibration master application.
 */
#include "app_calib_master.h"

#include "bsp_io.h"
#include "bsp_util.h"
#include "positioning_config.h"
#include "sys_config.h"
#include "sys_logger.h"
#include "sys_ranging.h"

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define CALIB_MASTER_ANCHOR_COUNT        MAX_ZONE_ANCHORS
#define CALIB_MASTER_PROGRESS_DONE       100U
#define CALIB_MASTER_LOG_EVERY_SAMPLE    0U

typedef struct {
    uint8_t anchor_id;
    float x_m;
    float y_m;
    float z_m;
    float known_m;
    float samples[SYS_CONFIG_CALIB_MAX_SAMPLES];
    uint16_t sample_count;
    uint16_t timeout_count;
    float mean_m;
    float std_m;
    float timeout_rate;
    float error_m;
    int32_t delta_dw;
    uint16_t suggested_combined_delay;
    uint16_t suggested_tx_delay;
    uint16_t suggested_rx_delay;
    bool ready;
    bool candidate_valid;
} calib_master_anchor_t;

static protobuf_calib_state_t s_state = protobuf_calib_state_t_CALIB_STATE_IDLE;
static calib_master_anchor_t s_anchors[CALIB_MASTER_ANCHOR_COUNT];
static uint8_t s_anchor_ids[CALIB_MASTER_ANCHOR_COUNT] = {1U, 2U, 3U, 4U, 5U, 6U};
static uint8_t s_anchor_count = CALIB_MASTER_ANCHOR_COUNT;
static uint16_t s_sample_target = CALIB_ANCHOR_SAMPLES;
static float s_max_std_m = CALIB_ANCHOR_MAX_STD_M;
static float s_max_timeout_rate = CALIB_A2A_MAX_TIMEOUT_RATE;
static uint8_t s_ready_mask = 0U;
static uint8_t s_candidate_mask = 0U;
static uint32_t s_rejected_batch_count = 0U;
static uint32_t s_error_count = 0U;
static uint8_t s_sequence_num = 0U;
static bool s_is_ranging_active = false;
static uint8_t s_pending_num_anchors = 0U;
static uint8_t s_pending_anchor_ids[CALIB_MASTER_ANCHOR_COUNT] = {0U};
static uint32_t s_next_due_tick = 0U;
static uint32_t s_cycle_start_tick = 0U;
static uint32_t s_last_cycle_done_tick = 0U;
static uint32_t s_last_ranging_tick = 0U;
static uint32_t s_cycle_count = 0U;
static bool s_decision_logged = false;
static float s_last_error_mean_m = 0.0f;
static float s_last_error_rms_m = 0.0f;
static float s_last_error_max_abs_m = 0.0f;
static float s_last_error_mean_abs_m = 0.0f;
static float s_last_error_spread_m = 0.0f;
static bool s_reference_position_valid = false;
static float s_reference_x_m = 0.0f;
static float s_reference_y_m = 0.0f;
static float s_reference_z_m = 0.0f;
static bool s_active = false;

static uint16_t clamp_u16_u32(uint32_t value, uint16_t min_value, uint16_t max_value);

static uint16_t get_current_combined_delay(void)
{
    const sys_config_t *cfg = sys_config_get();
    uint32_t combined = cfg
                        ? cfg->uwb.tx_antenna_delay + cfg->uwb.rx_antenna_delay
                        : ANCHOR_DEFAULT_TX_ANT_DLY + ANCHOR_DEFAULT_RX_ANT_DLY;
    return clamp_u16_u32(combined, CALIB_A2A_ANT_MIN, CALIB_A2A_ANT_MAX);
}

static uint16_t clamp_u16_u32(uint32_t value, uint16_t min_value, uint16_t max_value)
{
    if (value < min_value) {
        return min_value;
    }
    if (value > max_value) {
        return max_value;
    }
    return (uint16_t)value;
}

static uint16_t clamp_i32_to_u16(int32_t value, uint16_t min_value, uint16_t max_value)
{
    if (value < (int32_t)min_value) {
        return min_value;
    }
    if (value > (int32_t)max_value) {
        return max_value;
    }
    return (uint16_t)value;
}

static uint8_t anchor_bit(uint8_t anchor_id)
{
    if (anchor_id == 0U || anchor_id > 8U) {
        return 0U;
    }
    return (uint8_t)(1U << (anchor_id - 1U));
}

static calib_master_anchor_t *find_anchor(uint8_t anchor_id)
{
    for (uint8_t i = 0U; i < s_anchor_count; i++) {
        if (s_anchors[i].anchor_id == anchor_id) {
            return &s_anchors[i];
        }
    }
    return NULL;
}

static uint32_t get_period_ms(const sys_config_t *cfg)
{
    if (!cfg || cfg->uwb.ranging_period_ms == 0U) {
        return 1U;
    }
    return cfg->uwb.ranging_period_ms;
}

static uint16_t get_sample_target(const sys_calib_cfg_t *calib)
{
    uint32_t samples = (calib && calib->samples > 0U)
                       ? calib->samples
                       : CALIB_ANCHOR_SAMPLES;
    return clamp_u16_u32(samples, 1U, SYS_CONFIG_CALIB_MAX_SAMPLES);
}

static float get_max_std_m(const sys_calib_cfg_t *calib)
{
    if (calib && calib->max_std_m > 0.0f) {
        return calib->max_std_m;
    }
    return CALIB_ANCHOR_MAX_STD_M;
}

static void reset_anchor_batch(calib_master_anchor_t *anchor)
{
    if (!anchor) {
        return;
    }
    anchor->sample_count = 0U;
    anchor->timeout_count = 0U;
    anchor->mean_m = 0.0f;
    anchor->std_m = 0.0f;
    anchor->timeout_rate = 0.0f;
    anchor->error_m = 0.0f;
}

static bool compute_anchor_stats(calib_master_anchor_t *anchor)
{
    if (!anchor || anchor->sample_count < s_sample_target) {
        return false;
    }

    float sum = 0.0f;
    for (uint16_t i = 0U; i < anchor->sample_count; i++) {
        sum += anchor->samples[i];
    }
    anchor->mean_m = sum / (float)anchor->sample_count;

    float variance = 0.0f;
    for (uint16_t i = 0U; i < anchor->sample_count; i++) {
        float diff = anchor->samples[i] - anchor->mean_m;
        variance += diff * diff;
    }
    anchor->std_m = sqrtf(variance / (float)anchor->sample_count);

    uint32_t attempts = (uint32_t)anchor->sample_count + (uint32_t)anchor->timeout_count;
    anchor->timeout_rate = (attempts > 0U)
                           ? ((float)anchor->timeout_count / (float)attempts)
                           : 0.0f;
    anchor->error_m = anchor->mean_m - anchor->known_m;

    if (anchor->std_m > s_max_std_m || anchor->timeout_rate > s_max_timeout_rate) {
        s_rejected_batch_count++;
        RLOG_W(LOG_OBJECT_CODE_TAG,
               "[CALIB][MASTER] reject A%u samples=%u std=%.3fm max=%.3fm timeout=%.2f max=%.2f",
               anchor->anchor_id,
               (unsigned)anchor->sample_count,
               anchor->std_m,
               s_max_std_m,
               anchor->timeout_rate,
               s_max_timeout_rate);
        reset_anchor_batch(anchor);
        return false;
    }

    anchor->ready = true;
    s_ready_mask |= anchor_bit(anchor->anchor_id);
    RLOG_I(LOG_OBJECT_CODE_TAG,
           "[CALIB][MASTER] ready A%u mean=%.3fm std=%.3fm known=%.3fm err=%.3fm timeout=%.2f",
           anchor->anchor_id,
           anchor->mean_m,
           anchor->std_m,
           anchor->known_m,
           anchor->error_m,
           anchor->timeout_rate);
    return true;
}

static void note_timeout_for_missing(uint8_t seen_mask)
{
    for (uint8_t i = 0U; i < s_anchor_count; i++) {
        calib_master_anchor_t *anchor = &s_anchors[i];
        if (anchor->ready) {
            continue;
        }
        if ((seen_mask & anchor_bit(anchor->anchor_id)) == 0U) {
            anchor->timeout_count++;
        }
    }
}

static void collect_result(const sys_ranging_result_t *result)
{
    if (!result || !result->valid) {
        return;
    }

    calib_master_anchor_t *anchor = find_anchor(result->anchor_id);
    if (!anchor || anchor->ready) {
        return;
    }

    if (result->distance_m < MIN_VALID_DISTANCE_M ||
        result->distance_m > MAX_VALID_DISTANCE_M ||
        anchor->sample_count >= s_sample_target) {
        return;
    }

    anchor->samples[anchor->sample_count++] = result->distance_m;

#if CALIB_MASTER_LOG_EVERY_SAMPLE
    RLOG_I(LOG_OBJECT_CODE_TAG,
           "[CALIB][MASTER] sample A%u %u/%u d=%.3fm known=%.3fm err=%.3fm",
           anchor->anchor_id,
           (unsigned)anchor->sample_count,
           (unsigned)s_sample_target,
           result->distance_m,
           anchor->known_m,
           result->distance_m - anchor->known_m);
#endif

    if (anchor->sample_count >= s_sample_target) {
        (void)compute_anchor_stats(anchor);
    }
}

static bool all_anchors_ready(void)
{
    uint8_t target_mask = 0U;
    for (uint8_t i = 0U; i < s_anchor_count; i++) {
        target_mask |= anchor_bit(s_anchors[i].anchor_id);
    }
    return (target_mask != 0U) && ((s_ready_mask & target_mask) == target_mask);
}

static void update_config_diagnostics(uint32_t usable_count)
{
    sys_config_t *cfg = sys_config_get();
    if (!cfg) {
        return;
    }

    cfg->calib.last_pair_error_mean_m = s_last_error_mean_m;
    cfg->calib.last_pair_error_spread_m = s_last_error_spread_m;
    cfg->calib.last_pair_std_mean_m = 0.0f;
    cfg->calib.last_usable_pair_count = usable_count;
    cfg->calib.last_rejected_pair_count = s_anchor_count - usable_count;
    cfg->calib.rejected_batch_count = s_rejected_batch_count;
    cfg->calib.last_pair_error_rms_m = s_last_error_rms_m;
    cfg->calib.last_pair_error_max_abs_m = s_last_error_max_abs_m;
    cfg->calib.last_pair_error_mean_abs_m = s_last_error_mean_abs_m;
    cfg->calib.iterations_taken = 1U;
}

static void calculate_candidates(void)
{
    if (s_state == protobuf_calib_state_t_CALIB_STATE_DONE ||
        s_state == protobuf_calib_state_t_CALIB_STATE_ERROR) {
        return;
    }

    s_state = protobuf_calib_state_t_CALIB_STATE_CALCULATING;
    s_candidate_mask = 0U;

    float error_sum = 0.0f;
    float error_abs_sum = 0.0f;
    float error_sq_sum = 0.0f;
    float max_abs = 0.0f;
    float min_error = 0.0f;
    float max_error = 0.0f;
    uint32_t usable_count = 0U;
    bool pass = true;
    uint16_t base_combined = get_current_combined_delay();

    for (uint8_t i = 0U; i < s_anchor_count; i++) {
        calib_master_anchor_t *anchor = &s_anchors[i];
        if (!anchor->ready) {
            pass = false;
            continue;
        }

        anchor->delta_dw = (int32_t)(anchor->error_m * CALIB_A2A_M_TO_DW_UNITS);
        int32_t suggested_combined = (int32_t)base_combined + anchor->delta_dw;
        anchor->suggested_combined_delay =
            clamp_i32_to_u16(suggested_combined, CALIB_A2A_ANT_MIN, CALIB_A2A_ANT_MAX);
        anchor->suggested_tx_delay = (uint16_t)(anchor->suggested_combined_delay / 2U);
        anchor->suggested_rx_delay =
            (uint16_t)(anchor->suggested_combined_delay - anchor->suggested_tx_delay);
        anchor->candidate_valid = true;
        s_candidate_mask |= anchor_bit(anchor->anchor_id);

        if (anchor->suggested_combined_delay == CALIB_A2A_ANT_MIN ||
            anchor->suggested_combined_delay == CALIB_A2A_ANT_MAX) {
            pass = false;
        }

        float abs_error = fabsf(anchor->error_m);
        error_sum += anchor->error_m;
        error_abs_sum += abs_error;
        error_sq_sum += anchor->error_m * anchor->error_m;
        if (usable_count == 0U) {
            min_error = anchor->error_m;
            max_error = anchor->error_m;
        } else {
            if (anchor->error_m < min_error) {
                min_error = anchor->error_m;
            }
            if (anchor->error_m > max_error) {
                max_error = anchor->error_m;
            }
        }
        if (abs_error > max_abs) {
            max_abs = abs_error;
        }
        usable_count++;

        RLOG_I(LOG_OBJECT_CODE_TAG,
               "[CALIB][MASTER] candidate_A%u known=%.3fm mean=%.3fm err=%.3fm std=%.3fm delta_dw=%ld base_combined=%u suggested_combined=%u suggested_tx=%u suggested_rx=%u",
               anchor->anchor_id,
               anchor->known_m,
               anchor->mean_m,
               anchor->error_m,
               anchor->std_m,
               (long)anchor->delta_dw,
               (unsigned)base_combined,
               (unsigned)anchor->suggested_combined_delay,
               (unsigned)anchor->suggested_tx_delay,
               (unsigned)anchor->suggested_rx_delay);
    }

    if (usable_count > 0U) {
        s_last_error_mean_m = error_sum / (float)usable_count;
        s_last_error_rms_m = sqrtf(error_sq_sum / (float)usable_count);
        s_last_error_max_abs_m = max_abs;
        s_last_error_mean_abs_m = error_abs_sum / (float)usable_count;
        s_last_error_spread_m = max_error - min_error;
    }

    update_config_diagnostics(usable_count);

    if (usable_count != s_anchor_count) {
        pass = false;
    }

    s_state = pass ? protobuf_calib_state_t_CALIB_STATE_DONE : protobuf_calib_state_t_CALIB_STATE_ERROR;
    s_decision_logged = true;
    s_active = false;

    RLOG_I(LOG_OBJECT_CODE_TAG,
           "[CALIB][MASTER] decision %s ready_mask=0x%02X candidate_mask=0x%02X rms=%.3fm max_abs=%.3fm mean_abs=%.3fm rejected=%lu",
           pass ? "PASS" : "FAIL",
           (unsigned)s_ready_mask,
           (unsigned)s_candidate_mask,
           s_last_error_rms_m,
           s_last_error_max_abs_m,
           s_last_error_mean_abs_m,
           (unsigned long)s_rejected_batch_count);
}

static void load_anchor_layout(void)
{
    sys_config_t *cfg = sys_config_get();

    memset(s_anchors, 0, sizeof(s_anchors));

    if (cfg && cfg->anchor_count > 0) {
        s_anchor_count = cfg->anchor_count;
        if (s_anchor_count > CALIB_MASTER_ANCHOR_COUNT) {
            s_anchor_count = CALIB_MASTER_ANCHOR_COUNT;
        }
        for (uint8_t i = 0U; i < s_anchor_count; i++) {
            calib_master_anchor_t *anchor = &s_anchors[i];
            anchor->anchor_id = (uint8_t)cfg->anchor_layout[i].anchor_id;
            anchor->x_m = cfg->anchor_layout[i].x_m;
            anchor->y_m = cfg->anchor_layout[i].y_m;
            anchor->z_m = cfg->anchor_layout[i].z_m;
            s_anchor_ids[i] = anchor->anchor_id;
        }
    } else {
        s_anchor_count = 4;
        for (uint8_t i = 0U; i < s_anchor_count; i++) {
            calib_master_anchor_t *anchor = &s_anchors[i];
#if DEFAULT_ZONE_ID == 2
            anchor->anchor_id = i + 5;
            anchor->z_m = ANCHOR_HEIGHT_M;
            switch (anchor->anchor_id) {
                case 5U:
                    anchor->x_m = ZONE_2_ANCHOR_1_X;
                    anchor->y_m = ZONE_2_ANCHOR_1_Y;
                    anchor->z_m = ZONE_2_ANCHOR_1_Z;
                    break;
                case 6U:
                    anchor->x_m = ZONE_2_ANCHOR_2_X;
                    anchor->y_m = ZONE_2_ANCHOR_2_Y;
                    anchor->z_m = ZONE_2_ANCHOR_2_Z;
                    break;
                case 7U:
                    anchor->x_m = ZONE_2_ANCHOR_3_X;
                    anchor->y_m = ZONE_2_ANCHOR_3_Y;
                    anchor->z_m = ZONE_2_ANCHOR_3_Z;
                    break;
                default:
                    anchor->x_m = ZONE_2_ANCHOR_4_X;
                    anchor->y_m = ZONE_2_ANCHOR_4_Y;
                    anchor->z_m = ZONE_2_ANCHOR_4_Z;
                    break;
            }
#else
            anchor->anchor_id = i + 1;
            anchor->z_m = ANCHOR_HEIGHT_M;
            switch (anchor->anchor_id) {
                case 1U:
                    anchor->x_m = ZONE_1_ANCHOR_1_X;
                    anchor->y_m = ZONE_1_ANCHOR_1_Y;
                    anchor->z_m = ZONE_1_ANCHOR_1_Z;
                    break;
                case 2U:
                    anchor->x_m = ZONE_1_ANCHOR_2_X;
                    anchor->y_m = ZONE_1_ANCHOR_2_Y;
                    anchor->z_m = ZONE_1_ANCHOR_2_Z;
                    break;
                case 3U:
                    anchor->x_m = ZONE_1_ANCHOR_3_X;
                    anchor->y_m = ZONE_1_ANCHOR_3_Y;
                    anchor->z_m = ZONE_1_ANCHOR_3_Z;
                    break;
                default:
                    anchor->x_m = ZONE_1_ANCHOR_4_X;
                    anchor->y_m = ZONE_1_ANCHOR_4_Y;
                    anchor->z_m = ZONE_1_ANCHOR_4_Z;
                    break;
            }
#endif
            s_anchor_ids[i] = anchor->anchor_id;
        }
    }

    for (uint8_t i = 0U; i < s_anchor_count; i++) {
        calib_master_anchor_t *anchor = &s_anchors[i];
        float dx = anchor->x_m - s_reference_x_m;
        float dy = anchor->y_m - s_reference_y_m;
        float dz = anchor->z_m - s_reference_z_m;
        anchor->known_m = sqrtf((dx * dx) + (dy * dy) + (dz * dz));
    }
}

static uint32_t progress_percent(void)
{
    if (s_anchor_count == 0U || s_sample_target == 0U) {
        return 0U;
    }

    uint32_t collected = 0U;
    uint32_t target = (uint32_t)s_anchor_count * (uint32_t)s_sample_target;
    for (uint8_t i = 0U; i < s_anchor_count; i++) {
        uint16_t samples = s_anchors[i].sample_count;
        collected += (samples > s_sample_target) ? s_sample_target : samples;
    }

    uint32_t percent = (collected * CALIB_MASTER_PROGRESS_DONE) / target;
    return (percent > CALIB_MASTER_PROGRESS_DONE) ? CALIB_MASTER_PROGRESS_DONE : percent;
}

static void finish_failed_cycle(sys_ranging_err_t err,
                                uint32_t now_tick,
                                uint32_t period_ms,
                                bool abort_ranging,
                                const char *reason)
{
    if (abort_ranging) {
        sys_ranging_abort();
    }

    s_error_count++;
    s_last_ranging_tick = now_tick;
    s_next_due_tick = now_tick + period_ms;
    s_is_ranging_active = false;

    if ((s_error_count % 10U) == 1U) {
        RLOG_W(LOG_OBJECT_CODE_TAG,
               "[CALIB][MASTER] %s err=%d errors=%lu",
               reason,
               err,
               (unsigned long)s_error_count);
    }
}

bool app_calib_master_should_run(void)
{
    const sys_config_t *cfg = sys_config_get();
    return s_active &&
           cfg &&
           cfg->uwb.role == DEVICE_ROLE_TAG;
}

void app_calib_master_set_active(bool active)
{
    s_active = active;
}

bool app_calib_master_is_active(void)
{
    return s_active;
}

bool app_calib_master_set_reference_position(float x_m, float y_m, float z_m)
{
    if (!isfinite(x_m) || !isfinite(y_m) || !isfinite(z_m)) {
        return false;
    }
    s_reference_x_m = x_m;
    s_reference_y_m = y_m;
    s_reference_z_m = z_m;
    s_reference_position_valid = true;
    return true;
}

app_err_t app_calib_master_init(void)
{
    sys_config_t *cfg = sys_config_get();
    if (!s_reference_position_valid) {
        RLOG_E(LOG_OBJECT_CODE_TAG, ERR_INVALID_PARAM,
               "[CALIB][MASTER] explicit reference tag position is required");
        return APP_ERR;
    }

    s_state = protobuf_calib_state_t_CALIB_STATE_IDLE;
    s_ready_mask = 0U;
    s_candidate_mask = 0U;
    s_rejected_batch_count = 0U;
    s_error_count = 0U;
    s_sequence_num = 0U;
    s_is_ranging_active = false;
    s_decision_logged = false;
    s_cycle_count = 0U;
    s_last_error_mean_m = 0.0f;
    s_last_error_rms_m = 0.0f;
    s_last_error_max_abs_m = 0.0f;
    s_last_error_mean_abs_m = 0.0f;
    s_last_error_spread_m = 0.0f;

    s_sample_target = get_sample_target(cfg ? &cfg->calib : NULL);
    s_max_std_m = get_max_std_m(cfg ? &cfg->calib : NULL);
    s_max_timeout_rate = CALIB_A2A_MAX_TIMEOUT_RATE;
    load_anchor_layout();

    uint32_t now = bsp_util_get_ticks();
    uint32_t period_ms = get_period_ms(cfg);
    s_last_ranging_tick = now;
    s_next_due_tick = now + period_ms;
    s_cycle_start_tick = 0U;
    s_last_cycle_done_tick = 0U;
    sys_ranging_set_calib_status(SYS_CALIB_STATUS_NORMAL);

    RLOG_I(LOG_OBJECT_CODE_TAG,
           "[CALIB][MASTER] init reference=(%.3f,%.3f,%.3f) samples=%u max_std=%.3fm timeout_max=%.2f period=%lums",
           s_reference_x_m,
           s_reference_y_m,
           s_reference_z_m,
           (unsigned)s_sample_target,
           s_max_std_m,
           s_max_timeout_rate,
           (unsigned long)period_ms);
    return APP_OK;
}

void app_calib_master_process(void)
{
    if (!app_calib_master_should_run()) {
        return;
    }

    if (s_state == protobuf_calib_state_t_CALIB_STATE_DONE ||
        s_state == protobuf_calib_state_t_CALIB_STATE_ERROR) {
        if (!s_decision_logged) {
            calculate_candidates();
        }
        return;
    }

    sys_config_t *cfg = sys_config_get();
    uint32_t now = bsp_util_get_ticks();
    uint32_t period_ms = get_period_ms(cfg);

    if (!s_is_ranging_active && (int32_t)(now - s_next_due_tick) >= 0) {
        sys_ranging_err_t start_err = sys_ranging_tag_start_tdma(s_anchor_count,
                                                                  s_anchor_ids,
                                                                  s_sequence_num,
                                                                  cfg ? cfg->uwb.rx_timeout_ms : DEFAULT_RX_TIMEOUT_MS);
        if (start_err == SYS_RANGING_OK) {
            s_sequence_num++;
            s_pending_num_anchors = s_anchor_count;
            memcpy(s_pending_anchor_ids, s_anchor_ids, sizeof(s_pending_anchor_ids));
            s_is_ranging_active = true;
            s_cycle_start_tick = now;
            s_state = protobuf_calib_state_t_CALIB_STATE_COLLECTING;
            return;
        }

        if (start_err != SYS_RANGING_ERR_BUSY) {
            finish_failed_cycle(start_err, now, period_ms, false, "start failed");
        }
        return;
    }

    if (!s_is_ranging_active) {
        return;
    }

    if (s_cycle_start_tick != 0U &&
        (int32_t)(now - s_cycle_start_tick) >= (int32_t)period_ms) {
        finish_failed_cycle(SYS_RANGING_ERR_TIMEOUT,
                            now,
                            period_ms,
                            true,
                            "period watchdog abort");
        return;
    }

    sys_ranging_err_t err = sys_ranging_tag_process_tdma(s_pending_num_anchors,
                                                          s_pending_anchor_ids,
                                                          cfg ? cfg->uwb.rx_timeout_ms : DEFAULT_RX_TIMEOUT_MS);
    if (err == SYS_RANGING_OK || err == SYS_RANGING_ERR_PARTIAL) {
        sys_ranging_multi_result_t multi_results;
        memset(&multi_results, 0, sizeof(multi_results));

        if (sys_ranging_tag_get_results_tdma(&multi_results) == SYS_RANGING_OK) {
            uint8_t seen_mask = 0U;
            for (uint8_t i = 0U; i < multi_results.count; i++) {
                const sys_ranging_result_t *result = &multi_results.results[i];
                if (result->anchor_id > 0U && result->anchor_id <= 8U) {
                    seen_mask |= anchor_bit(result->anchor_id);
                }
                collect_result(result);
            }
            note_timeout_for_missing(seen_mask);
        } else {
            note_timeout_for_missing(0U);
            s_error_count++;
        }

        uint32_t done_tick = bsp_util_get_ticks();
        uint32_t interval_ms = (s_last_cycle_done_tick == 0U)
                               ? period_ms
                               : done_tick - s_last_cycle_done_tick;
        s_last_cycle_done_tick = done_tick;
        s_last_ranging_tick = done_tick;
        s_next_due_tick = done_tick + period_ms;
        s_is_ranging_active = false;
        s_cycle_count++;

        if ((s_cycle_count % 10U) == 1U) {
            char samples_buf[128] = {0};
            int offset = 0;
            for (uint8_t i = 0; i < s_anchor_count; i++) {
                offset += snprintf(samples_buf + offset, sizeof(samples_buf) - offset, " A%u=%u", s_anchors[i].anchor_id, s_anchors[i].sample_count);
            }
            RLOG_I(LOG_OBJECT_CODE_TAG,
                   "[CALIB][MASTER] progress=%lu%% ready=0x%02X samples%s interval=%lums",
                   (unsigned long)progress_percent(),
                   (unsigned)s_ready_mask,
                   samples_buf,
                   (unsigned long)interval_ms);
        }

        if (all_anchors_ready()) {
            bsp_io_led_blink(5);
            calculate_candidates();
        }
        return;
    }

    if (err != SYS_RANGING_ERR_BUSY) {
        note_timeout_for_missing(0U);
        finish_failed_cycle(err,
                            bsp_util_get_ticks(),
                            period_ms,
                            false,
                            "ranging failed");
    }
}

void app_calib_master_on_ranging_stopped(void)
{
    if (s_is_ranging_active) {
        sys_ranging_abort();
    }
    s_is_ranging_active = false;
    if (s_state == protobuf_calib_state_t_CALIB_STATE_COLLECTING ||
        s_state == protobuf_calib_state_t_CALIB_STATE_CALCULATING) {
        s_state = protobuf_calib_state_t_CALIB_STATE_IDLE;
    }
    RLOG_I(LOG_OBJECT_CODE_TAG, "[CALIB][MASTER] stopped");
}

void app_calib_master_fill_status(protobuf_calib_status_resp_t *resp)
{
    if (!resp) {
        return;
    }

    memset(resp, 0, sizeof(*resp));
    resp->state = s_state;
    resp->progress_percent = progress_percent();
    resp->current_iteration = 1U;
    resp->total_iterations = 1U;
    resp->last_pair_error_mean_m = s_last_error_mean_m;
    resp->current_antenna_delay = get_current_combined_delay();
    resp->peer_ready_mask = s_ready_mask;
    resp->last_pair_error_spread_m = s_last_error_spread_m;
    resp->rejected_batch_count = s_rejected_batch_count;
    resp->last_pair_error_rms_m = s_last_error_rms_m;
    resp->last_pair_error_max_abs_m = s_last_error_max_abs_m;
    resp->last_pair_error_mean_abs_m = s_last_error_mean_abs_m;
    resp->sample_count = 0U;
    resp->sample_target = s_sample_target;
    resp->candidate_mask = s_candidate_mask;

    for (uint8_t i = 0U; i < s_anchor_count; i++) {
        const calib_master_anchor_t *anchor = &s_anchors[i];
        resp->sample_count += anchor->sample_count;

        if (!anchor->candidate_valid ||
            resp->candidates_count >= (sizeof(resp->candidates) / sizeof(resp->candidates[0]))) {
            continue;
        }

        protobuf_calib_anchor_candidate_t *candidate =
            &resp->candidates[resp->candidates_count++];
        candidate->anchor_id = anchor->anchor_id;
        candidate->known_m = anchor->known_m;
        candidate->mean_m = anchor->mean_m;
        candidate->error_m = anchor->error_m;
        candidate->std_m = anchor->std_m;
        candidate->timeout_rate = anchor->timeout_rate;
        candidate->valid_count = anchor->sample_count;
        candidate->delta_dw = anchor->delta_dw;
        candidate->suggested_combined_delay = anchor->suggested_combined_delay;
        candidate->suggested_tx_delay = anchor->suggested_tx_delay;
        candidate->suggested_rx_delay = anchor->suggested_rx_delay;
    }
}

bool app_calib_master_get_average_candidate(uint32_t anchor_mask,
                                            uint16_t *tx_delay,
                                            uint16_t *rx_delay)
{
    if (!tx_delay || !rx_delay || s_state != protobuf_calib_state_t_CALIB_STATE_DONE) {
        return false;
    }

    uint32_t tx_total = 0U;
    uint32_t rx_total = 0U;
    uint32_t count = 0U;
    for (uint8_t i = 0U; i < s_anchor_count; i++) {
        const calib_master_anchor_t *anchor = &s_anchors[i];
        if (anchor->candidate_valid &&
            (anchor_mask & anchor_bit(anchor->anchor_id)) != 0U) {
            tx_total += anchor->suggested_tx_delay;
            rx_total += anchor->suggested_rx_delay;
            count++;
        }
    }

    if (count == 0U) {
        return false;
    }
    *tx_delay = (uint16_t)(tx_total / count);
    *rx_delay = (uint16_t)(rx_total / count);
    return true;
}
