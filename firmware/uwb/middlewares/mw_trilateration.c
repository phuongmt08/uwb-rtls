/* ============================== mw_trilateration.c =========================
 * @file       mw_trilateration.c
 * @brief      Middleware - Simple trilateration implementation
 * @version    3.0.0
 * @date       2025-12-20
 */

/* Includes ----------------------------------------------------------- */
#include "mw_trilateration.h"
#include "positioning_config.h"
#include <math.h>
#include <string.h>
#ifdef ENABLE_DEBUG_LOGGING
#include "sys_logger.h"
#endif
/* Private defines ---------------------------------------------------- */
#define MAXZERO  (0.001)

static double huber_weight(double value, double delta)
{
    double magnitude = fabs(value);
    if (!isfinite(magnitude)) return MW_TRIL_HUBER_WEIGHT_FLOOR;
    if (delta <= MAXZERO || magnitude <= delta) return 1.0;

    double weight = delta / magnitude;
    return (weight < MW_TRIL_HUBER_WEIGHT_FLOOR) ? MW_TRIL_HUBER_WEIGHT_FLOOR : weight;
}

static double range_variance(const mw_tril_anchor_t *anchor)
{
    double distance = fabs(anchor->distance);
    double sigma_base = MW_TRIL_RANGE_SIGMA_BASE_M;
    double sigma_dist = MW_TRIL_RANGE_SIGMA_SLOPE * distance;
    double sigma = sqrt((sigma_base * sigma_base) + (sigma_dist * sigma_dist));

    if (sigma > MW_TRIL_RANGE_SIGMA_MAX_M) sigma = MW_TRIL_RANGE_SIGMA_MAX_M;
    /* sigma >= sigma_base by construction (sqrt of sum-of-squares), so
     * no lower-bound guard is needed here. */
    double variance = sigma * sigma;
    if (anchor->rescued) {
        variance *= (double)MAHALANOBIS_PREFILTER_RESCUE_NOISE_SCALE_MIN;
    }
    return variance;
}

static double d2_huber_weight(const mw_tril_anchor_t *anchor)
{
    if (anchor->d2_score <= 0.0) return 1.0;
    /* Apply Huber on sqrt(d2) = Mahalanobis L2 distance, not on d2 directly.
     * This keeps the downweighting in a more linear domain and avoids
     * over-penalising moderately elevated d2 scores. */
    return huber_weight(sqrt(anchor->d2_score), sqrt(MW_TRIL_D2_RECOVER));
}

static double fp_huber_weight(const mw_tril_anchor_t *anchor)
{
    if (!anchor->quality_valid) {
        /* FP confidence is unknown: confidence_valid was absent on at least
         * one ranging leg. Apply a conservative downweight (deficit = 0.5
         * exceeds the Huber delta = 0.35) rather than returning 1.0 (full
         * trust). This prevents NLOS anchors whose DW1000 could not compute
         * first-path index from contaminating the WGDOP score. */
        return huber_weight(0.5, MW_TRIL_HUBER_FP_DEFICIT_DELTA);
    }
    if (!isfinite(anchor->fp_confidence) || anchor->fp_confidence < 0.0) {
        return MW_TRIL_HUBER_WEIGHT_FLOOR;
    }

    double confidence = anchor->fp_confidence;
    if (confidence > 1.0) confidence = 1.0;
    double deficit = 1.0 - confidence;
    if (deficit <= 0.0) return 1.0;
    return huber_weight(deficit, MW_TRIL_HUBER_FP_DEFICIT_DELTA);
}

static double median_in_place(double *values, uint8_t count)
{
    if (!values || count == 0U) return 0.0;

    for (uint8_t i = 1U; i < count; i++) {
        double key = values[i];
        int j = (int)i - 1;
        while (j >= 0 && values[j] > key) {
            values[j + 1] = values[j];
            j--;
        }
        values[j + 1] = key;
    }

    if ((count & 1U) != 0U) return values[count / 2U];
    return 0.5 * (values[(count / 2U) - 1U] + values[count / 2U]);
}

void mw_trilateration_compute_weights(mw_tril_anchor_t *candidates,
                                      uint8_t candidate_count,
                                      bool reference_valid,
                                      vec2d_t reference_position)
{
    if (!candidates || candidate_count == 0U ||
        candidate_count > MAX_ANCHORS_SUPPORTED) {
        return;
    }

    double residuals[MAX_ANCHORS_SUPPORTED] = {0.0};
    double sorted[MAX_ANCHORS_SUPPORTED] = {0.0};
    double deviations[MAX_ANCHORS_SUPPORTED] = {0.0};
    bool use_residual = reference_valid && candidate_count >= 4U;
    double residual_median = 0.0;
    double residual_mad_scale = 0.0;

    if (use_residual) {
        for (uint8_t i = 0U; i < candidate_count; i++) {
            double dx = reference_position.x - candidates[i].position.x;
            double dy = reference_position.y - candidates[i].position.y;
            double predicted = sqrt((dx * dx) + (dy * dy));
            residuals[i] = candidates[i].distance - predicted;
            sorted[i] = residuals[i];
        }
        residual_median = median_in_place(sorted, candidate_count);
        for (uint8_t i = 0U; i < candidate_count; i++) {
            deviations[i] = fabs(residuals[i] - residual_median);
        }
        residual_mad_scale = 1.4826 * median_in_place(deviations, candidate_count);
    }

    for (uint8_t i = 0U; i < candidate_count; i++) {
        double variance = range_variance(&candidates[i]);
        double residual_weight = 1.0;

        if (use_residual) {
            /* MAD can collapse on a very clean frame. The expected range
             * sigma is the statistical floor for residual normalization. */
            double scale = residual_mad_scale;
            double sigma = sqrt(variance);
            if (scale < sigma) scale = sigma;
            double normalized = fabs(residuals[i] - residual_median) / scale;
            residual_weight = huber_weight(normalized,
                                           MW_TRIL_HUBER_RESIDUAL_DELTA);
        }

        candidates[i].measurement_weight =
            (d2_huber_weight(&candidates[i])
             * fp_huber_weight(&candidates[i])
             * residual_weight) / variance;
    }
}

static double triplet_wgdop(const mw_tril_anchor_t *a,
                            const mw_tril_anchor_t *b,
                            const mw_tril_anchor_t *c,
                            const vec2d_t *position)
{
    const mw_tril_anchor_t *triplet[3] = {a, b, c};
    double hxx = 0.0;
    double hxy = 0.0;
    double hyy = 0.0;

    for (uint8_t i = 0; i < 3U; i++) {
        double dx = position->x - triplet[i]->position.x;
        double dy = position->y - triplet[i]->position.y;
        double range = sqrt((dx * dx) + (dy * dy));
        if (range < MAXZERO) {
            return 1.0e9;
        }

        double hx = dx / range;
        double hy = dy / range;
        double weight = triplet[i]->measurement_weight;
        if (!(weight > 0.0) || !isfinite(weight)) {
            weight = MW_TRIL_HUBER_WEIGHT_FLOOR / range_variance(triplet[i]);
        }
        hxx += weight * hx * hx;
        hxy += weight * hx * hy;
        hyy += weight * hy * hy;
    }

    double det = (hxx * hyy) - (hxy * hxy);
    if (!isfinite(det) || det <= MW_TRIL_WGDOP_DET_MIN) {
        return 1.0e9;
    }

    return sqrt((hxx + hyy) / det);
}

static bool trilaterate_2d_probe(const mw_tril_anchor_t *a,
                                 const mw_tril_anchor_t *b,
                                 const mw_tril_anchor_t *c,
                                 vec2d_t *position,
                                 double *residual_rms)
{
    double x1 = a->position.x, y1 = a->position.y, r1 = a->distance;
    double x2 = b->position.x, y2 = b->position.y, r2 = b->distance;
    double x3 = c->position.x, y3 = c->position.y, r3 = c->distance;

    double delta = 4.0 * ((x1 - x2) * (y1 - y3) - (x1 - x3) * (y1 - y2));
    if (fabs(delta) < MAXZERO) {
        return false;
    }

    double A = r2 * r2 - r1 * r1 - x2 * x2 + x1 * x1 - y2 * y2 + y1 * y1;
    double B = r3 * r3 - r1 * r1 - x3 * x3 + x1 * x1 - y3 * y3 + y1 * y1;

    position->x = (1.0 / delta) * (2.0 * A * (y1 - y3) - 2.0 * B * (y1 - y2));
    position->y = (1.0 / delta) * (2.0 * B * (x1 - x2) - 2.0 * A * (x1 - x3));

    double d1 = sqrt((position->x - x1) * (position->x - x1) + (position->y - y1) * (position->y - y1));
    double d2 = sqrt((position->x - x2) * (position->x - x2) + (position->y - y2) * (position->y - y2));
    double d3 = sqrt((position->x - x3) * (position->x - x3) + (position->y - y3) * (position->y - y3));
    double e1 = d1 - r1;
    double e2 = d2 - r2;
    double e3 = d3 - r3;

    if (residual_rms != NULL) {
        *residual_rms = sqrt((e1 * e1 + e2 * e2 + e3 * e3) / 3.0);
    }
    return true;
}

static double frame_residual_rms(const mw_tril_anchor_t *anchors,
                                 uint8_t count,
                                 const vec2d_t *position)
{
    /* Diagnostic only: production residual weights are computed once for the
     * whole frame at a trusted common reference. */
    double sum_sq = 0.0;
    for (uint8_t i = 0U; i < count; i++) {
        double dx = position->x - anchors[i].position.x;
        double dy = position->y - anchors[i].position.y;
        double predicted = sqrt((dx * dx) + (dy * dy));
        double residual = predicted - anchors[i].distance;
        sum_sq += residual * residual;
    }
    return (count > 0U) ? sqrt(sum_sq / (double)count) : 0.0;
}

static bool snapshot_selected_triplet(mw_tril_anchor_t selected[3],
                                      bool reference_valid,
                                      vec2d_t reference_position)
{
    vec2d_t probe;
    if (!trilaterate_2d_probe(&selected[0], &selected[1], &selected[2], &probe, NULL)) {
        return false;
    }

    const vec2d_t *score_position = reference_valid ? &reference_position : &probe;
    double score = triplet_wgdop(&selected[0], &selected[1], &selected[2], score_position);
    double residual = frame_residual_rms(selected, 3U, &probe);
    double fp_weight = (fp_huber_weight(&selected[0])
                      + fp_huber_weight(&selected[1])
                      + fp_huber_weight(&selected[2])) / 3.0;
    for (uint8_t i = 0U; i < 3U; i++) {
        selected[i].wgdop = score;
        selected[i].residual_rms = residual;
        selected[i].triplet_fp_weight = fp_weight;
    }

    return true;
}

/* Anchor selection --------------------------------------------------- */

uint8_t mw_trilateration_select_best_3(const mw_tril_anchor_t *candidates,
                                       uint8_t candidate_count,
                                       mw_tril_anchor_t selected_out[3],
                                       uint8_t prev_mask,
                                       bool reference_valid,
                                       vec2d_t reference_position)
{
    if (!candidates || !selected_out ||
        candidate_count < 3U || candidate_count > MAX_ANCHORS_SUPPORTED) {
        return 0U;
    }

    /* sensor_fusion_entry has already filtered and compacted this array. */
    for (uint8_t i = 0U; i < candidate_count; i++) {
        if (!candidates[i].valid) {
            return 0U;
        }
    }

    if (candidate_count == 3U) {
        memcpy(selected_out, candidates, 3U * sizeof(selected_out[0]));
        return snapshot_selected_triplet(selected_out,
                                         reference_valid,
                                         reference_position) ? 3U : 0U;
    }

    uint8_t best_i = 0U, best_j = 1U, best_k = 2U;
    double  best_score = 1.0e9;
    double  best_residual = 0.0;
    double  best_fp_weight = 0.0;
    uint8_t best_mask = 0U;

    bool    prev_found = false;
    uint8_t prev_i = 0U, prev_j = 1U, prev_k = 2U;
    double  prev_score = 1.0e9;
    double  prev_residual = 0.0;
    double  prev_fp_weight = 0.0;

    for (uint8_t i = 0U; i < candidate_count - 2U; i++) {
        for (uint8_t j = i + 1U; j < candidate_count - 1U; j++) {
            for (uint8_t k = j + 1U; k < candidate_count; k++) {
                vec2d_t probe_pos;
                /* Candidate trilateration remains a fallback reference while
                 * the UKF is uncertain, and a diagnostic otherwise. */
                if (!trilaterate_2d_probe(&candidates[i], &candidates[j], &candidates[k],
                                          &probe_pos, NULL)) {
                    continue;
                }

                double residual = frame_residual_rms(candidates, candidate_count, &probe_pos);

                const vec2d_t *score_position = reference_valid
                                              ? &reference_position
                                              : &probe_pos;
                double wgdop = triplet_wgdop(&candidates[i],
                                             &candidates[j],
                                             &candidates[k],
                                             score_position);
                if (wgdop >= 1.0e8) {
                    continue;
                }
                double avg_fp_weight = (fp_huber_weight(&candidates[i])
                                      + fp_huber_weight(&candidates[j])
                                      + fp_huber_weight(&candidates[k])) / 3.0;
                double score = wgdop;

                uint8_t mask = (1U << (candidates[i].id - 1U))
                             | (1U << (candidates[j].id - 1U))
                             | (1U << (candidates[k].id - 1U));

                if (score < best_score) {
                    best_score = score;
                    best_i = i;
                    best_j = j;
                    best_k = k;
                    best_residual = residual;
                    best_fp_weight = avg_fp_weight;
                    best_mask = mask;
                }

                if (prev_mask != 0U && mask == prev_mask) {
                    prev_found = true;
                    prev_i = i;
                    prev_j = j;
                    prev_k = k;
                    prev_score = score;
                    prev_residual = residual;
                    prev_fp_weight = avg_fp_weight;
                }
            }
        }
    }

    if (best_score >= 1.0e9) {
        return 0U;
    }

    uint8_t selected_i = best_i;
    uint8_t selected_j = best_j;
    uint8_t selected_k = best_k;
    double  selected_score = best_score;
    double  selected_residual = best_residual;
    double  selected_fp_weight = best_fp_weight;

    if (prev_found && best_mask != prev_mask) {
        double switch_margin = MW_TRIL_SWITCH_MARGIN;
        double switch_score_eps = MW_TRIL_SWITCH_SCORE_EPS;
        bool keep_previous = prev_score <= (best_score * (1.0 + switch_margin)) + switch_score_eps;
        if (keep_previous) {
            selected_i = prev_i;
            selected_j = prev_j;
            selected_k = prev_k;
            selected_score = prev_score;
            selected_residual = prev_residual;
            selected_fp_weight = prev_fp_weight;
        }
    }

    selected_out[0] = candidates[selected_i];
    selected_out[1] = candidates[selected_j];
    selected_out[2] = candidates[selected_k];

    for (uint8_t i = 0; i < 3U; i++) {
        selected_out[i].wgdop = selected_score;
        selected_out[i].residual_rms = selected_residual;
        selected_out[i].triplet_fp_weight = selected_fp_weight;
    }
#ifdef ENABLE_DEBUG_LOGGING
    RLOG_D(LOG_OBJECT_CODE_TAG,
            "Best WGDOP anchors: #%u #%u #%u (wgdop=%.3fm residual=%.3fm fp_weight=%.3f)",
            selected_out[0].id, selected_out[1].id, selected_out[2].id,
            selected_score, selected_residual, selected_fp_weight);
#endif

    return 3U;
}

/* Public API --------------------------------------------------------- */

mw_tril_err_t mw_trilateration_2d(const mw_tril_anchor_t *anchors_exact_3,
                                  vec2d_t *position,
                                  mw_tril_result_t *result)
{
    if (!anchors_exact_3 || !position) {
        return MW_TRIL_ERR_PARAM;
    }

    double residual_rms = 0.0;
    if (!trilaterate_2d_probe(&anchors_exact_3[0],
                              &anchors_exact_3[1],
                              &anchors_exact_3[2],
                              position,
                              &residual_rms)) {
        return MW_TRIL_ERR_NO_SOLUTION;
    }

    if (result) {
        result->position.x = position->x;
        result->position.y = position->y;
        result->position.z = 0.0;
        result->num_anchors = 3U;
        result->valid = true;
        /* Geometry quality already lives in the selection output (wgdop);
         * residual RMS is the remaining quality figure of this debug fix. */
        result->error_estimate = residual_rms;
    }

    return MW_TRIL_OK;
}

/* End of file -------------------------------------------------------- */
