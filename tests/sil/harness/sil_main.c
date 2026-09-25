/**
 * @file sil_main.c
 * @brief Software-in-the-Loop (SIL) test harness for UWB-RTLS sensor fusion
 */

#include <stdio.h>
#include <stdlib.h>
#include <stdbool.h>
#include <string.h>
#include <math.h>

#include "v_platform.h"
#include "sys_sensor_fusion.h"
#include "mw_filter.h"
#include "mw_trilateration.h"
#include "positioning_config.h"

#ifdef _WIN32
void ExitProcess(int code);
int _fltused = 0x9875;
#endif

typedef struct {
    mw_tril_anchor_t candidate_anchors[MAX_ANCHORS_SUPPORTED];
    mw_tril_anchor_t selected_anchors[3U];
    mw_tril_anchor_t rejected_anchors[MAX_ANCHORS_SUPPORTED];
} sil_workspace_t;

static bool convert_3d_to_2d_distance(double r3d, double dz, double *r2d_out)
{
    if (r3d < MIN_VALID_DISTANCE_M || r3d > MAX_VALID_DISTANCE_M) {
        return false;
    }
    double dz_abs = fabs(dz);
    if (r3d <= dz_abs + 1e-6) {
        return false;
    }
    double r2d_sq = r3d * r3d - dz * dz;
    if (r2d_sq < 0.0) {
        return false;
    }
    *r2d_out = sqrt(r2d_sq);
    return true;
}

static bool get_anchor_pos(uint8_t aid, vec3d_t *pos_out)
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

static bool sensor_fusion_has_candidate(const mw_tril_anchor_t *candidates,
                                        uint8_t count,
                                        uint8_t aid)
{
    for (uint8_t i = 0U; i < count; i++) {
        if (candidates[i].id == aid) return true;
    }
    return false;
}

static uint8_t count_valid_ranges(uint8_t mask)
{
    uint8_t c = 0;
    for (uint8_t i = 0; i < 8; i++) {
        if (mask & (1U << i)) c++;
    }
    return c;
}

int main(int argc, char *argv[])
{
    const char *scenario_path = NULL;
    const char *output_path = NULL;
    bool verbose = false;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--scenario") == 0 && i + 1 < argc) {
            scenario_path = argv[++i];
        } else if (strcmp(argv[i], "--output") == 0 && i + 1 < argc) {
            output_path = argv[++i];
        } else if (strcmp(argv[i], "--verbose") == 0) {
            verbose = true;
        }
    }

    if (!scenario_path || !output_path) {
        printf("Usage: sil_harness --scenario <scenario_file> --output <output_csv> [--verbose]\n");
        return 1;
    }

    FILE *f_scen = fopen(scenario_path, "r");
    if (!f_scen) {
        printf("[SIL ERR] Failed to open scenario: %s\n", scenario_path);
        return 2;
    }

    FILE *f_out = fopen(output_path, "w");
    if (!f_out) {
        printf("[SIL ERR] Failed to open output CSV: %s\n", output_path);
        fclose(f_scen);
        return 3;
    }

    g_sil_verbose_logging = verbose;
    v_platform_init();

    sys_sensor_fusion_data_t ukf_data = {0};
    if (sys_sensor_fusion_init(&ukf_data) != SYS_SENSOR_FUSION_OK) {
        printf("[SIL ERR] sys_sensor_fusion_init failed\n");
        fclose(f_scen);
        fclose(f_out);
        return 4;
    }

    mahalanobis_prefilter_t s_prefilter;
    const sys_prefilter_cfg_t *prefilter_cfg = sys_config_get_prefilter();
    mw_filter_mahalanobis_init(&s_prefilter,
                                prefilter_cfg->recover_d2,
                                prefilter_cfg->reject_d2,
                                prefilter_cfg->r_gate,
                                prefilter_cfg->min_covariance);

    fprintf(f_out, "step,time_ms,dt_ms,gt_x,gt_y,ukf_px,ukf_py,ukf_vx,ukf_vy,ukf_yaw,rejected_mask,divergence_streak,update_performed\n");

    char line[512];
    uint32_t last_tick_ms = 0U;
    uint32_t s_sensor_fusion_next_tick = 0U;
    uint8_t s_last_selected_anchors_mask = 0U;
    uint8_t s_prefilter_divergence_streak = 0U;
    sil_workspace_t workspace_storage = {0};
    sil_workspace_t *const workspace = &workspace_storage;

    while (fgets(line, sizeof(line), f_scen)) {
        /* Skip comments or empty lines */
        if (line[0] == '#' || line[0] == '\r' || line[0] == '\n' || line[0] == '\0') {
            continue;
        }

        /* Check for header directives */
        if (strncmp(line, "INIT_POS", 8) == 0) {
            float x0 = 0.0f, y0 = 0.0f;
            if (sscanf(line + 8, "%f %f", &x0, &y0) == 2) {
                sys_sensor_fusion_set_initial_position(&ukf_data, x0, y0);
                sys_sensor_fusion_set_predict_flag();
            }
            continue;
        }

        if (strncmp(line, "ANCHOR", 6) == 0) {
            uint32_t aid = 0;
            float ax = 0.0f, ay = 0.0f, az = 2.495f;
            if (sscanf(line + 6, "%u %f %f %f", &aid, &ax, &ay, &az) >= 3) {
                sys_config_t *cfg = sys_config_get();
                if (aid >= 1 && aid <= MAX_ANCHORS_SUPPORTED) {
                    bool found = false;
                    for (uint32_t i = 0; i < cfg->anchor_count; i++) {
                        if (cfg->anchor_layout[i].anchor_id == aid) {
                            cfg->anchor_layout[i].x_m = ax;
                            cfg->anchor_layout[i].y_m = ay;
                            cfg->anchor_layout[i].z_m = az;
                            found = true;
                            break;
                        }
                    }
                    if (!found && cfg->anchor_count < MAX_ANCHORS_SUPPORTED) {
                        cfg->anchor_layout[cfg->anchor_count].anchor_id = aid;
                        cfg->anchor_layout[cfg->anchor_count].x_m = ax;
                        cfg->anchor_layout[cfg->anchor_count].y_m = ay;
                        cfg->anchor_layout[cfg->anchor_count].z_m = az;
                        cfg->anchor_count++;
                    }
                }
            }
            continue;
        }

        /* Step row format:
         * STEP <step_idx> <time_ms> <gt_x> <gt_y> <ax> <ay> <gz> <mask_hex> <r1> <r2> <r3> <r4> ...
         * or CSV row without "STEP" prefix:
         * <step_idx>,<time_ms>,<gt_x>,<gt_y>,<ax>,<ay>,<gz>,<mask_hex>,<r1>,<r2>,<r3>,<r4>...
         */
        const char *p = line;
        if (strncmp(p, "STEP", 4) == 0) p += 4;
        while (*p == ' ' || *p == '\t' || *p == ',') p++;

        uint32_t step_idx = 0U;
        uint32_t time_ms = 0U;
        float gt_x = 0.0f, gt_y = 0.0f;
        float imu_ax = 0.0f, imu_ay = 0.0f, imu_gz = 0.0f;
        unsigned int mask_int = 0U;
        float ranges[MAX_ANCHORS_SUPPORTED] = {0};

        int matched = sscanf(p, "%u%*[, \t]%u%*[, \t]%f%*[, \t]%f%*[, \t]%f%*[, \t]%f%*[, \t]%f%*[, \t]%x"
                                "%*[, \t]%f%*[, \t]%f%*[, \t]%f%*[, \t]%f%*[, \t]%f%*[, \t]%f%*[, \t]%f%*[, \t]%f",
                             &step_idx, &time_ms, &gt_x, &gt_y,
                             &imu_ax, &imu_ay, &imu_gz, &mask_int,
                             &ranges[0], &ranges[1], &ranges[2], &ranges[3],
                             &ranges[4], &ranges[5], &ranges[6], &ranges[7]);
        if (matched < 8) {
            continue;
        }

        uint8_t mask = (uint8_t)mask_int;

        /* Advance virtual time to simulation step */
        uint32_t current_tick_ms = osKernelGetTickCount();
        uint32_t dt_ms = (step_idx == 0U) ? 20U : (current_tick_ms - last_tick_ms);
        last_tick_ms = current_tick_ms;

        /* Inject IMU into queue */
        bsp_imu_data_t imu_sample = {
            .ax = imu_ax,
            .ay = imu_ay,
            .az = 0.0f,
            .gx = 0.0f,
            .gy = 0.0f,
            .gz = imu_gz
        };
        v_imu_set_sample(imu_sample.ax, imu_sample.ay, imu_sample.az,
                         imu_sample.gx, imu_sample.gy, imu_sample.gz);
        (void)osMessageQueuePut(g_imu_data_queue, &imu_sample, 0U, 0U);

        /* 1. Predict Step */
        bool predict_performed = false;
        if (sys_sensor_fusion_check_predict_flag() &&
            g_imu_data_queue != NULL &&
            osMessageQueueGetCount(g_imu_data_queue) > 0U)
        {
            predict_performed = (sys_sensor_fusion_predict(&ukf_data) == SYS_SENSOR_FUSION_OK);
        }

        /* 2. UWB Ranging & Prefilter Step */
        bool update_performed = false;
        uint8_t rejected_mask = 0U;

        if (mask != 0U) {
            memset(workspace, 0, sizeof(*workspace));
            uint8_t candidate_count = 0U;
            uint8_t prefilter_reject_count = 0U;

            float ukf_pxx = 0.0f, ukf_pxy = 0.0f, ukf_pyy = 0.0f;
            bool ukf_cov_valid = sys_sensor_fusion_get_position_covariance(&ukf_pxx, &ukf_pxy, &ukf_pyy);
            float ukf_reference_std = 0.0f;
            if (ukf_cov_valid) {
                float pos_var = ukf_pxx + ukf_pyy;
                if (isfinite(pos_var) && pos_var >= 0.0f) {
                    ukf_reference_std = sqrtf(pos_var);
                } else {
                    ukf_cov_valid = false;
                }
            }
            bool ukf_reference_valid = ukf_cov_valid &&
                                       isfinite(ukf_data.px) &&
                                       isfinite(ukf_data.py) &&
                                       ukf_reference_std <= MW_TRIL_REFERENCE_MAX_STD_M;
            vec2d_t ukf_reference = { .x = (double)ukf_data.px, .y = (double)ukf_data.py };

            uwb_distance_msg_t msg = {0};
            msg.mask = mask;
            msg.count = 0U;

            for (uint8_t i = 0U; i < MAX_ANCHORS_SUPPORTED; i++) {
                uint8_t aid = i + 1U;
                if (!(mask & (1U << i))) continue;

                msg.anchor_ids[msg.count] = aid;
                msg.distances[msg.count] = ranges[i];
                msg.quality_valid[msg.count] = 1U;
                msg.count++;

                vec3d_t anchor_pos;
                if (!get_anchor_pos(aid, &anchor_pos)) continue;

                double r2d = 0.0;
                double dz = anchor_pos.z - (double)TAG_HEIGHT_M;
                if (!convert_3d_to_2d_distance((double)ranges[i], dz, &r2d)) continue;

                mw_tril_anchor_t anchor_entry = {0};
                anchor_entry.position = anchor_pos;
                anchor_entry.distance = r2d;
                anchor_entry.id = aid;
                anchor_entry.valid = true;
                anchor_entry.quality_valid = true;

                bool pass = true;
                float d2_score = 0.0f;
                if (sys_sensor_fusion_is_initialized() && ukf_cov_valid && prefilter_cfg->enable) {
                    pass = mw_filter_mahalanobis_update(&s_prefilter,
                                                        aid - 1U,
                                                        (float)r2d,
                                                        ukf_data.px,
                                                        ukf_data.py,
                                                        TAG_HEIGHT_M,
                                                        ukf_pxx, ukf_pxy, ukf_pyy,
                                                        (float)anchor_pos.x,
                                                        (float)anchor_pos.y,
                                                        (float)anchor_pos.z,
                                                        &d2_score);
                }
                anchor_entry.d2_score = (double)d2_score;

                if (!pass) {
                    rejected_mask |= (1U << (aid - 1U));
                    sys_sensor_fusion_prefilter_record_reject();
                    if (prefilter_reject_count < MAX_ANCHORS_SUPPORTED) {
                        workspace->rejected_anchors[prefilter_reject_count++] = anchor_entry;
                    }
                    continue;
                }

                if (candidate_count < MAX_ANCHORS_SUPPORTED &&
                    !sensor_fusion_has_candidate(workspace->candidate_anchors, candidate_count, aid)) {
                    workspace->candidate_anchors[candidate_count++] = anchor_entry;
                }
            }

            /* Rescue mechanism */
            if (candidate_count < MAHALANOBIS_PREFILTER_RESCUE_MIN_ANCHORS && prefilter_reject_count > 0U) {
                /* Insertion sort by d2_score */
                for (uint8_t i = 1U; i < prefilter_reject_count; i++) {
                    mw_tril_anchor_t key = workspace->rejected_anchors[i];
                    int j = (int)i - 1;
                    while (j >= 0 && workspace->rejected_anchors[j].d2_score > key.d2_score) {
                        workspace->rejected_anchors[j + 1] = workspace->rejected_anchors[j];
                        j--;
                    }
                    workspace->rejected_anchors[j + 1] = key;
                }

                uint8_t rescue_target = MAHALANOBIS_PREFILTER_RESCUE_MIN_ANCHORS;
                for (uint8_t i = 0U; i < prefilter_reject_count && candidate_count < rescue_target; i++) {
                    uint8_t aid = workspace->rejected_anchors[i].id;
                    if (aid == 0U || aid > MAX_ANCHORS_SUPPORTED ||
                        !isfinite(workspace->rejected_anchors[i].d2_score) ||
                        workspace->rejected_anchors[i].d2_score > MAHALANOBIS_PREFILTER_RESCUE_D2_MAX ||
                        s_prefilter.anchors[aid - 1U].reject_streak < MAHALANOBIS_PREFILTER_RESCUE_MIN_REJECT_STREAK ||
                        sensor_fusion_has_candidate(workspace->candidate_anchors, candidate_count, aid)) {
                        continue;
                    }
                    workspace->rejected_anchors[i].rescued = true;
                    workspace->candidate_anchors[candidate_count++] = workspace->rejected_anchors[i];
                }
            }

            /* Trilateration and Update */
            if (candidate_count >= 3U) {
                s_prefilter_divergence_streak = 0U;
                mw_trilateration_compute_weights(workspace->candidate_anchors,
                                                 candidate_count,
                                                 ukf_reference_valid,
                                                 ukf_reference);
                uint8_t selected_count = mw_trilateration_select_best_3(
                                            workspace->candidate_anchors,
                                            candidate_count,
                                            workspace->selected_anchors,
                                            s_last_selected_anchors_mask,
                                            ukf_reference_valid,
                                            ukf_reference);
                if (selected_count >= 3U) {
                    s_last_selected_anchors_mask = 0U;
                    for (uint8_t i = 0U; i < 3U; i++) {
                        s_last_selected_anchors_mask |= (1U << (workspace->selected_anchors[i].id - 1U));
                    }
                    vec2d_t tril_pos = {0.0, 0.0};
                    if (mw_trilateration_2d(workspace->selected_anchors, &tril_pos, NULL) == MW_TRIL_OK) {
                        update_performed = sys_sensor_fusion_update(&ukf_data,
                                                                    &tril_pos,
                                                                    workspace->selected_anchors,
                                                                    workspace->candidate_anchors,
                                                                    candidate_count,
                                                                    s_last_selected_anchors_mask,
                                                                    &msg);
                    }
                }
            } else if (count_valid_ranges(mask) >= MAHALANOBIS_PREFILTER_RESCUE_MIN_ANCHORS) {
                /* Divergence recovery path */
                s_prefilter_divergence_streak++;
                if (s_prefilter_divergence_streak > MAHALANOBIS_PREFILTER_RESCUE_MIN_REJECT_STREAK) {
                    mw_tril_anchor_t raw_anchors[MAX_ANCHORS_SUPPORTED];
                    uint8_t raw_count = 0U;
                    for (uint8_t i = 0U; i < candidate_count && raw_count < MAX_ANCHORS_SUPPORTED; i++) {
                        raw_anchors[raw_count++] = workspace->candidate_anchors[i];
                    }
                    for (uint8_t i = 0U; i < prefilter_reject_count && raw_count < MAX_ANCHORS_SUPPORTED; i++) {
                        if (!sensor_fusion_has_candidate(raw_anchors, raw_count, workspace->rejected_anchors[i].id)) {
                            raw_anchors[raw_count++] = workspace->rejected_anchors[i];
                        }
                    }
                    if (raw_count >= 3U) {
                        mw_tril_anchor_t best_3[3];
                        vec2d_t zero_ref = {0.0, 0.0};
                        if (mw_trilateration_select_best_3(raw_anchors, raw_count, best_3, 0U, false, zero_ref) >= 3U) {
                            vec2d_t rec_tril_pos = {0.0, 0.0};
                            if (mw_trilateration_2d(best_3, &rec_tril_pos, NULL) == MW_TRIL_OK) {
                                (void)sys_sensor_fusion_realign_position(&ukf_data, (float)rec_tril_pos.x, (float)rec_tril_pos.y);
                                mw_filter_mahalanobis_reset_anchors(&s_prefilter);
                                s_prefilter_divergence_streak = 0U;
                                update_performed = true;
                            }
                        }
                    }
                }
            }
        }

        /* 3. Cadence Timing Scheduling (replicates FreeRTOS 50Hz periodic loop) */
        uint32_t now_tick = osKernelGetTickCount();
        if (s_sensor_fusion_next_tick == 0U || (int32_t)(now_tick - s_sensor_fusion_next_tick) > 0) {
            s_sensor_fusion_next_tick = now_tick + pdMS_TO_TICKS(20U);
        } else {
            s_sensor_fusion_next_tick += pdMS_TO_TICKS(20U);
        }
        (void)osDelayUntil(s_sensor_fusion_next_tick);

        /* Write output row */
        float yaw_deg = ukf_data.theta * (180.0f / 3.14159265358979323846f);
        fprintf(f_out, "%u,%u,%u,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%.2f,0x%02X,%u,%d\n",
                step_idx, current_tick_ms, dt_ms,
                gt_x, gt_y,
                ukf_data.px, ukf_data.py, ukf_data.vx, ukf_data.vy,
                yaw_deg, rejected_mask, s_prefilter_divergence_streak,
                update_performed ? 1 : 0);
    }

    fclose(f_scen);
    fclose(f_out);

    if (verbose) {
        printf("[SIL] Completed scenario successfully. Output written to %s\n", output_path);
    }
    return 0;
}

#ifdef _WIN32
void mainCRTStartup(void)
{
    /* Parse command line arguments from GetCommandLineA() if invoked via CRT startup */
    int argc = 0;
    char *argv[16];
    char cmdline_buf[512];

    extern char *GetCommandLineA(void);
    char *cmd = GetCommandLineA();
    if (!cmd) cmd = "";
    strncpy(cmdline_buf, cmd, sizeof(cmdline_buf) - 1);
    cmdline_buf[sizeof(cmdline_buf) - 1] = '\0';

    char *token = strtok(cmdline_buf, " \t\r\n");
    while (token && argc < 16) {
        argv[argc++] = token;
        token = strtok(NULL, " \t\r\n");
    }

    int ret = main(argc, argv);
    ExitProcess(ret);
}
#endif
