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

/* Private function prototypes ---------------------------------------- */
static inline vec3d_t vec_diff(vec3d_t v1, vec3d_t v2);
static inline vec3d_t vec_sum(vec3d_t v1, vec3d_t v2);
static inline vec3d_t vec_mul(vec3d_t v, double s);
static inline vec3d_t vec_div(vec3d_t v, double s);
static inline double vec_norm(vec3d_t v);
static inline vec3d_t vec_cross(vec3d_t v1, vec3d_t v2);
static inline double vec_dot(vec3d_t v1, vec3d_t v2);

static int trilaterate_3sphere(vec3d_t *sol1, vec3d_t *sol2,
                               vec3d_t p1, double r1,
                               vec3d_t p2, double r2,
                               vec3d_t p3, double r3);

static inline vec3d_t vec_diff(vec3d_t v1, vec3d_t v2)
{
    return (vec3d_t){v1.x - v2.x, v1.y - v2.y, v1.z - v2.z};
}

static inline vec3d_t vec_sum(vec3d_t v1, vec3d_t v2)
{
    return (vec3d_t){v1.x + v2.x, v1.y + v2.y, v1.z + v2.z};
}

static inline vec3d_t vec_mul(vec3d_t v, double s)
{
    return (vec3d_t){v.x * s, v.y * s, v.z * s};
}

static inline vec3d_t vec_div(vec3d_t v, double s)
{
    return (vec3d_t){v.x / s, v.y / s, v.z / s};
}

static inline double vec_norm(vec3d_t v)
{
    return sqrt((v.x * v.x) + (v.y * v.y) + (v.z * v.z));
}

static inline vec3d_t vec_cross(vec3d_t v1, vec3d_t v2)
{
    return (vec3d_t){
        (v1.y * v2.z) - (v1.z * v2.y),
        (v1.z * v2.x) - (v1.x * v2.z),
        (v1.x * v2.y) - (v1.y * v2.x)
    };
}

static inline double vec_dot(vec3d_t v1, vec3d_t v2)
{
    return (v1.x * v2.x) + (v1.y * v2.y) + (v1.z * v2.z);
}

static double clamp01(double value)
{
    if (value < 0.0) return 0.0;
    if (value > 1.0) return 1.0;
    return value;
}

static double anchor_d2_penalty(double d2_score)
{
    double reject = MW_TRIL_D2_REJECT;
    if (reject <= 0.001) reject = 1.0;
    return clamp01(d2_score / reject);
}

static double fp_quality_penalty(double value, double good_value)
{
    if (value <= 0.0) return 1.0;
    return clamp01(1.0 - (value / good_value));
}

static double triplet_gdop(const mw_tril_anchor_t *a,
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
        hxx += hx * hx;
        hxy += hx * hy;
        hyy += hy * hy;
    }

    double det = (hxx * hyy) - (hxy * hxy);
    if (det <= 1.0e-6) {
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

    *residual_rms = sqrt((e1 * e1 + e2 * e2 + e3 * e3) / 3.0);
    return true;
}

/* Measurement weight -------------------------------------------------- */

double mw_huber_weight(double u, double c)
{
    double au = fabs(u);
    if (au <= c) return 1.0;
    return c / (au + MW_WEIGHT_EPS);
}

static double clamp_range(double v, double lo, double hi)
{
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

/* Phase-1 first-path weight: normalized amplitude against the LOS "good"
 * threshold. Missing diagnostics degrade softly instead of hard-rejecting. */
static double compute_fp_weight(double fp_amp_norm, bool quality_valid)
{
    if (!quality_valid) return MW_FP_UNKNOWN_WEIGHT;
    if (fp_amp_norm <= 0.0) return MW_FP_MIN_WEIGHT;
    return clamp_range(fp_amp_norm / MW_TRIL_FP_AMP_GOOD, MW_FP_MIN_WEIGHT, 1.0);
}

/* Distance-dependent range variance. Prefer r_adaptive from the Mahalanobis
 * prefilter; fall back to the simple quadratic model otherwise. */
static double compute_range_variance(double r_adaptive, double distance)
{
    if (r_adaptive > 0.0 && isfinite(r_adaptive)) {
        return r_adaptive;
    }
    return (double)MW_SIGMA_R2_BASE * (1.0 + (MW_SIGMA_R2_K_DIST * distance * distance));
}

static double median_of(double *values, uint8_t n)
{
    for (uint8_t i = 1; i < n; i++) {
        double key = values[i];
        int j = (int)i - 1;
        while (j >= 0 && values[j] > key) {
            values[j + 1] = values[j];
            j--;
        }
        values[j + 1] = key;
    }
    if (n == 0U) return 0.0;
    if ((n % 2U) == 1U) return values[n / 2U];
    return 0.5 * (values[(n / 2U) - 1U] + values[n / 2U]);
}

void mw_anchor_compute_weights(mw_tril_anchor_t *anchors,
                               uint8_t count,
                               bool p_ref_valid,
                               vec2d_t p_ref)
{
    if (!anchors) return;

    /* Frame residual weights need redundancy: with only 3 anchors a 2D fit
     * can absorb one bad range, so q_residual stays 1. */
    double residuals[8];
    uint8_t residual_idx[8];
    uint8_t residual_count = 0U;

    if (p_ref_valid) {
        for (uint8_t i = 0; i < count && residual_count < 8U; i++) {
            if (!anchors[i].valid) continue;
            double dx = p_ref.x - anchors[i].position.x;
            double dy = p_ref.y - anchors[i].position.y;
            double pred = sqrt((dx * dx) + (dy * dy));
            residuals[residual_count] = anchors[i].distance - pred;
            residual_idx[residual_count] = i;
            residual_count++;
        }
    }

    double res_median = 0.0;
    double res_scale = 0.0;
    bool use_residual = (residual_count >= 4U);
    if (use_residual) {
        double tmp[8];
        for (uint8_t i = 0; i < residual_count; i++) tmp[i] = residuals[i];
        res_median = median_of(tmp, residual_count);

        double dev[8];
        for (uint8_t i = 0; i < residual_count; i++) {
            dev[i] = fabs(residuals[i] - res_median);
        }
        res_scale = (1.4826 * median_of(dev, residual_count)) + MW_WEIGHT_EPS;
    }

    for (uint8_t i = 0; i < count; i++) {
        if (!anchors[i].valid) continue;

        double uM = sqrt((anchors[i].d2_score > 0.0) ? anchors[i].d2_score : 0.0);
        double qM = mw_huber_weight(uM, MW_HUBER_C_MAHALANOBIS);

        double qFP = compute_fp_weight(anchors[i].fp_amp_norm, anchors[i].quality_valid);

        double qR = 1.0;
        if (use_residual) {
            for (uint8_t k = 0; k < residual_count; k++) {
                if (residual_idx[k] == i) {
                    double uR = fabs(residuals[k] - res_median) / res_scale;
                    qR = mw_huber_weight(uR, MW_HUBER_C_RESIDUAL);
                    anchors[i].debug_residual = residuals[k];
                    break;
                }
            }
        }

        double sigma_r2 = compute_range_variance(anchors[i].r_adaptive, anchors[i].distance);
        /* Rescued anchors never earned an accepted r_adaptive; inflate their
         * variance so a rescue can keep the frame alive without being trusted. */
        if (anchors[i].rescued) {
            sigma_r2 *= (double)MAHALANOBIS_PREFILTER_RESCUE_NOISE_SCALE_MIN;
        }

        double w = (qM * qFP * qR) / (sigma_r2 + MW_WEIGHT_EPS);
        w = clamp_range(w, MW_WEIGHT_MIN, MW_WEIGHT_MAX);

        anchors[i].q_mahalanobis = qM;
        anchors[i].q_fp = qFP;
        anchors[i].q_residual = qR;
        anchors[i].sigma_r2 = sigma_r2;
        anchors[i].measurement_weight = w;
    }
}

/* Weighted layout selection ------------------------------------------- */

/* WGDOP = sqrt(trace(inv(H^T W H))) of a triplet at p_ref. Returns a huge
 * score for degenerate geometry so callers can rank without special cases. */
static double triplet_wgdop(const mw_tril_anchor_t *a,
                            const mw_tril_anchor_t *b,
                            const mw_tril_anchor_t *c,
                            const vec2d_t *p_ref)
{
    const mw_tril_anchor_t *triplet[3] = {a, b, c};
    double ixx = 0.0;
    double ixy = 0.0;
    double iyy = 0.0;

    for (uint8_t i = 0; i < 3U; i++) {
        double dx = p_ref->x - triplet[i]->position.x;
        double dy = p_ref->y - triplet[i]->position.y;
        double range = sqrt((dx * dx) + (dy * dy));
        if (range < MAXZERO) {
            return 1.0e9;
        }

        double w = triplet[i]->measurement_weight;
        if (!(w > 0.0) || !isfinite(w)) w = MW_WEIGHT_MIN;

        double hx = dx / range;
        double hy = dy / range;
        ixx += w * hx * hx;
        ixy += w * hx * hy;
        iyy += w * hy * hy;
    }

    double det = (ixx * iyy) - (ixy * ixy);
    if (det <= 1.0e-9) {
        return 1.0e9;
    }

    /* trace(inv(I)) = (ixx + iyy) / det for a 2x2 information matrix */
    return sqrt((ixx + iyy) / det);
}

uint8_t mw_select_ukf_layout_3(const mw_tril_anchor_t *anchors,
                               uint8_t count,
                               bool p_ref_valid,
                               vec2d_t p_ref,
                               mw_tril_anchor_t *best_out,
                               uint8_t prev_mask)
{
    if (!anchors || !best_out) return 0;

    mw_tril_anchor_t valid_anchors[8];
    uint8_t valid_count = 0;
    for (uint8_t i = 0; i < count; i++) {
        if (anchors[i].valid && valid_count < 8U) {
            valid_anchors[valid_count++] = anchors[i];
        }
    }
    if (valid_count < 3U) return 0;

    /* Reference position priority: caller (UKF predicted / last state),
     * then per-candidate debug trilateration, then anchor centroid. */
    vec2d_t centroid = {0.0, 0.0};
    for (uint8_t i = 0; i < valid_count; i++) {
        centroid.x += valid_anchors[i].position.x;
        centroid.y += valid_anchors[i].position.y;
    }
    centroid.x /= (double)valid_count;
    centroid.y /= (double)valid_count;

    uint8_t best_i = 0, best_j = 1, best_k = 2;
    double  best_score = 1.0e9;
    double  best_rms = 0.0;
    uint8_t best_found_mask = 0;

    bool    prev_found = false;
    uint8_t prev_i = 0, prev_j = 1, prev_k = 2;
    double  prev_score = 1.0e9;
    double  prev_rms = 0.0;

    for (uint8_t i = 0; i < valid_count - 2U; i++) {
        for (uint8_t j = i + 1U; j < valid_count - 1U; j++) {
            for (uint8_t k = j + 1U; k < valid_count; k++) {
                vec2d_t ref = centroid;
                vec2d_t probe_pos;
                double probe_rms = 0.0;
                bool probe_ok = trilaterate_2d_probe(&valid_anchors[i],
                                                     &valid_anchors[j],
                                                     &valid_anchors[k],
                                                     &probe_pos, &probe_rms);
                if (p_ref_valid) {
                    ref = p_ref;
                } else if (probe_ok) {
                    ref = probe_pos;
                }

                double score = triplet_wgdop(&valid_anchors[i],
                                             &valid_anchors[j],
                                             &valid_anchors[k],
                                             &ref);
                if (score >= 1.0e8) {
                    continue;
                }

                uint8_t mask = (uint8_t)((1U << (valid_anchors[i].id - 1U))
                                       | (1U << (valid_anchors[j].id - 1U))
                                       | (1U << (valid_anchors[k].id - 1U)));

                if (score < best_score) {
                    best_score = score;
                    best_i = i;
                    best_j = j;
                    best_k = k;
                    best_rms = probe_ok ? probe_rms : 0.0;
                    best_found_mask = mask;
                }

                if (prev_mask != 0U && mask == prev_mask) {
                    prev_found = true;
                    prev_i = i;
                    prev_j = j;
                    prev_k = k;
                    prev_score = score;
                    prev_rms = probe_ok ? probe_rms : 0.0;
                }
            }
        }
    }

    if (best_score >= 1.0e8) {
        return 0;
    }

    uint8_t sel_i = best_i, sel_j = best_j, sel_k = best_k;
    double  sel_score = best_score;
    double  sel_rms = best_rms;

    /* Hysteresis: keep the previous layout unless the challenger clearly wins */
    if (prev_found && best_found_mask != prev_mask) {
        bool keep_previous = prev_score <= (best_score * (1.0 + MW_LAYOUT_SWITCH_MARGIN))
                                           + MW_LAYOUT_SWITCH_EPS;
        if (keep_previous) {
            sel_i = prev_i;
            sel_j = prev_j;
            sel_k = prev_k;
            sel_score = prev_score;
            sel_rms = prev_rms;
        }
    }

    best_out[0] = valid_anchors[sel_i];
    best_out[1] = valid_anchors[sel_j];
    best_out[2] = valid_anchors[sel_k];

    for (uint8_t i = 0; i < 3U; i++) {
        best_out[i].layout_score = sel_score;
        best_out[i].debug_tril_rms = sel_rms;
        /* Legacy logging fields mirror the new score */
        best_out[i].selection_score = sel_score;
        best_out[i].residual_rms = sel_rms;
    }

#ifdef ENABLE_DEBUG_LOGGING
    RLOG_D(LOG_OBJECT_CODE_TAG,
           "UKF layout: #%u #%u #%u (wgdop=%.3f w=%.3f/%.3f/%.3f)",
           best_out[0].id, best_out[1].id, best_out[2].id, sel_score,
           best_out[0].measurement_weight,
           best_out[1].measurement_weight,
           best_out[2].measurement_weight);
#endif

    return 3;
}

/* Anchor selection --------------------------------------------------- */

uint8_t mw_trilateration_select_best(const mw_tril_anchor_t *anchors,
                                     uint8_t total_anchors,
                                     mw_tril_anchor_t *best_out,
                                     uint8_t max_out,
                                     uint8_t prev_mask)
{
    if (!anchors || !best_out || max_out == 0) return 0;

    /* Collect valid anchors */
    mw_tril_anchor_t valid_anchors[8];
    uint8_t valid_count = 0;

    for (uint8_t i = 0; i < total_anchors; i++) {
        if (anchors[i].valid) {
            if (valid_count < 8) {
                valid_anchors[valid_count++] = anchors[i];
            }
        }
    }

    if (valid_count == 0) return 0;

    /* If we have exactly max_out or fewer, return all of them directly */
    if (valid_count <= max_out) {
        for (uint8_t i = 0; i < valid_count; i++) {
            best_out[i] = valid_anchors[i];
        }
        return valid_count;
    }

    if (max_out != 3U) {
        for (uint8_t i = 0; i < valid_count - 1; i++) {
            for (uint8_t j = i + 1; j < valid_count; j++) {
                if (valid_anchors[i].d2_score > valid_anchors[j].d2_score) {
                    mw_tril_anchor_t temp = valid_anchors[i];
                    valid_anchors[i] = valid_anchors[j];
                    valid_anchors[j] = temp;
                }
            }
        }

        uint8_t out_count = (valid_count < max_out) ? valid_count : max_out;
        for (uint8_t i = 0; i < out_count; i++) {
            best_out[i] = valid_anchors[i];
        }
        return out_count;
    }

    double min_gdop = 1.0e9;
    double max_gdop = 0.0;
    for (uint8_t i = 0; i < valid_count - 2; i++) {
        for (uint8_t j = i + 1; j < valid_count - 1; j++) {
            for (uint8_t k = j + 1; k < valid_count; k++) {
                vec2d_t probe_pos;
                double residual = 0.0;
                if (!trilaterate_2d_probe(&valid_anchors[i], &valid_anchors[j], &valid_anchors[k],
                                          &probe_pos, &residual)) {
                    continue;
                }

                double gdop = triplet_gdop(&valid_anchors[i], &valid_anchors[j], &valid_anchors[k],
                                           &probe_pos);
                if (gdop < 1.0e8) {
                    if (gdop < min_gdop) min_gdop = gdop;
                    if (gdop > max_gdop) max_gdop = gdop;
                }
            }
        }
    }
    if (min_gdop >= 1.0e8) {
        return 0;
    }

    /* Dynamically calculate and normalize weights (excluding health) */
    double w_d2 = MW_TRIL_WEIGHT_D2;
    double w_fp = MW_TRIL_WEIGHT_FP_AMP;
    double w_residual = MW_TRIL_WEIGHT_RESIDUAL;
    double w_dist = MW_TRIL_WEIGHT_DIST;
    double weight_sum = w_d2 + w_fp + w_residual + w_dist;
    if (weight_sum > 0.0) {
        w_d2 /= weight_sum;
        w_fp /= weight_sum;
        w_residual /= weight_sum;
        w_dist /= weight_sum;
    } else {
        w_d2 = 0.35 / 1.05;
        w_fp = 0.15 / 1.05;
        w_residual = 0.30 / 1.05;
        w_dist = 0.25 / 1.05;
    }

    uint8_t best_i = 0, best_j = 1, best_k = 2;
    double  best_score = 1.0e9;
    double  best_residual = 0.0;
    double  best_gdop_penalty = 0.0;
    double  best_fp_penalty = 0.0;
    uint8_t best_mask = 0;

    bool    prev_found = false;
    uint8_t prev_i = 0, prev_j = 1, prev_k = 2;
    double  prev_score = 1.0e9;
    double  prev_residual = 0.0;
    double  prev_gdop_penalty = 0.0;
    double  prev_fp_penalty = 0.0;

    for (uint8_t i = 0; i < valid_count - 2; i++) {
        for (uint8_t j = i + 1; j < valid_count - 1; j++) {
            for (uint8_t k = j + 1; k < valid_count; k++) {
                vec2d_t probe_pos;
                double residual = 0.0;
                if (!trilaterate_2d_probe(&valid_anchors[i], &valid_anchors[j], &valid_anchors[k],
                                          &probe_pos, &residual)) {
                    continue;
                }

                double gdop = triplet_gdop(&valid_anchors[i], &valid_anchors[j], &valid_anchors[k],
                                           &probe_pos);
                if (gdop >= 1.0e8) {
                    continue;
                }
                double gdop_span = max_gdop - min_gdop;
                if (gdop_span <= 0.001) {
                    gdop_span = 1.0;
                }
                double gdop_penalty = clamp01((gdop - min_gdop) / gdop_span);
                double residual_penalty = clamp01(residual / MW_TRIL_RESIDUAL_SCALE_M);
                double avg_d2_penalty = (anchor_d2_penalty(valid_anchors[i].d2_score)
                                       + anchor_d2_penalty(valid_anchors[j].d2_score)
                                       + anchor_d2_penalty(valid_anchors[k].d2_score)) / 3.0;
                double avg_fp_penalty =
                    ((valid_anchors[i].quality_valid ? fp_quality_penalty(valid_anchors[i].fp_amp_norm, MW_TRIL_FP_AMP_GOOD) : 1.0)
                   + (valid_anchors[j].quality_valid ? fp_quality_penalty(valid_anchors[j].fp_amp_norm, MW_TRIL_FP_AMP_GOOD) : 1.0)
                   + (valid_anchors[k].quality_valid ? fp_quality_penalty(valid_anchors[k].fp_amp_norm, MW_TRIL_FP_AMP_GOOD) : 1.0)) / 3.0;

                double avg_range = (valid_anchors[i].distance + valid_anchors[j].distance + valid_anchors[k].distance) / 3.0;
                double range_penalty = clamp01(avg_range / 15.0);

                double score = (w_d2 * avg_d2_penalty)
                             + (w_fp * avg_fp_penalty)
                             + (w_residual * residual_penalty)
                             + (w_dist * range_penalty);

                uint8_t mask = (1 << (valid_anchors[i].id - 1))
                             | (1 << (valid_anchors[j].id - 1))
                             | (1 << (valid_anchors[k].id - 1));

                if (score < best_score) {
                    best_score = score;
                    best_i = i;
                    best_j = j;
                    best_k = k;
                    best_residual = residual;
                    best_gdop_penalty = gdop_penalty;
                    best_fp_penalty = avg_fp_penalty;
                    best_mask = mask;
                }

                if (prev_mask != 0 && mask == prev_mask) {
                    prev_found = true;
                    prev_i = i;
                    prev_j = j;
                    prev_k = k;
                    prev_score = score;
                    prev_residual = residual;
                    prev_gdop_penalty = gdop_penalty;
                    prev_fp_penalty = avg_fp_penalty;
                }
            }
        }
    }

    if (best_score >= 1.0e9) {
        return 0;
    }

    uint8_t selected_i = best_i;
    uint8_t selected_j = best_j;
    uint8_t selected_k = best_k;
    double  selected_score = best_score;
    double  selected_residual = best_residual;
    double  selected_gdop_penalty = best_gdop_penalty;
    double  selected_fp_penalty = best_fp_penalty;

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
            selected_gdop_penalty = prev_gdop_penalty;
            selected_fp_penalty = prev_fp_penalty;
        }
    }

    best_out[0] = valid_anchors[selected_i];
    best_out[1] = valid_anchors[selected_j];
    best_out[2] = valid_anchors[selected_k];

    for (uint8_t i = 0; i < 3U; i++) {
        best_out[i].selection_score = selected_score;
        best_out[i].residual_rms = selected_residual;
        best_out[i].gdop_penalty = selected_gdop_penalty;
        best_out[i].fp_penalty = selected_fp_penalty;
    }

#ifdef ENABLE_DEBUG_LOGGING
    RLOG_D(LOG_OBJECT_CODE_TAG,
            "Best composite anchors: #%u #%u #%u (score=%.3f residual=%.3f gdop=%.3f)",
            best_out[0].id, best_out[1].id, best_out[2].id,
            selected_score, selected_residual, selected_gdop_penalty);
#endif

    return 3;
}

/* Core 3-sphere trilateration ---------------------------------------- */

static int trilaterate_3sphere(vec3d_t *sol1, vec3d_t *sol2,
                               vec3d_t p1, double r1,
                               vec3d_t p2, double r2,
                               vec3d_t p3, double r3)
{
    vec3d_t ex, ey, ez, t1, t2;
    double h, i, j, x, y, z;

    /* Unit vector ex from p1 to p2 */
    ex = vec_diff(p2, p1);
    h = vec_norm(ex);
    if (h <= MAXZERO) return -1;  /* Concentric */
    ex = vec_div(ex, h);

    /* Project p3 onto ex axis */
    t1 = vec_diff(p3, p1);
    i = vec_dot(ex, t1);

    /* Unit vector ey perpendicular to ex */
    t2 = vec_mul(ex, i);
    ey = vec_diff(t1, t2);
    j = vec_norm(ey);
    if (j <= MAXZERO) return -2;  /* Collinear */
    ey = vec_div(ey, j);

    /* Calculate position in new coordinate system */
    h = vec_norm(vec_diff(p2, p1));
    x = (r1*r1 - r2*r2) / (2*h) + h / 2;
    y = (r1*r1 - r3*r3 + i*i) / (2*j) + j / 2 - x * i / j;
    z = r1*r1 - x*x - y*y;

    if (z < -MAXZERO) return -3;  /* No intersection */
    z = (z > 0.0) ? sqrt(z) : 0.0;

    /* Unit vector ez perpendicular to ex and ey */
    ez = vec_cross(ex, ey);

    /* Calculate both solutions */
    t2 = vec_sum(p1, vec_mul(ex, x));
    t2 = vec_sum(t2, vec_mul(ey, y));

    if (sol1) *sol1 = vec_sum(t2, vec_mul(ez, z));
    if (sol2) *sol2 = vec_sum(t2, vec_mul(ez, -z));

    return 0;
}

/* Public API --------------------------------------------------------- */

mw_tril_err_t mw_trilateration_3d(const mw_tril_anchor_t *anchors_exact_3,
                                  vec3d_t *position,
                                  mw_tril_result_t *result)
{
    if (!anchors_exact_3 || !position) {
        return MW_TRIL_ERR_PARAM;
    }

    /* Extract selected anchors */
    vec3d_t p1 = anchors_exact_3[0].position;
    vec3d_t p2 = anchors_exact_3[1].position;
    vec3d_t p3 = anchors_exact_3[2].position;
    double r1 = anchors_exact_3[0].distance;
    double r2 = anchors_exact_3[1].distance;
    double r3 = anchors_exact_3[2].distance;

    /* Calculate position */
    vec3d_t sol1, sol2;
    int ret = trilaterate_3sphere(&sol1, &sol2, p1, r1, p2, r2, p3, r3);

    if (ret != 0) {
        return MW_TRIL_ERR_NO_SOLUTION;
    }

    /* Select solution below anchors (typical setup) */
    *position = (sol1.z < p1.z) ? sol1 : sol2;

    /* Fill result if requested */
    if (result) {
        result->position = *position;
        result->num_anchors = 3;
        result->valid = true;

        /* 1. Calculate Residual RMS (Measurement inconsistency) */
        double d1 = vec_norm(vec_diff(*position, p1));
        double d2 = vec_norm(vec_diff(*position, p2));
        double d3 = vec_norm(vec_diff(*position, p3));
        double e1 = fabs(d1 - r1);
        double e2 = fabs(d2 - r2);
        double e3 = fabs(d3 - r3);
        double rms = sqrt((e1*e1 + e2*e2 + e3*e3) / 3.0);

        /* 2. Calculate GDOP (Geometric factor) 
         * For 3D, we approximate based on the volume of the tetrahedron 
         * or simplified matrix approach. For simplicity and robustness 
         * in this 3-anchor setup, we use the area of the base triangle. */
        double area2 = (p2.x - p1.x) * (p3.y - p1.y) - (p3.x - p1.x) * (p2.y - p1.y);
        if (area2 < 0) area2 = -area2;
        
        /* Characteristic length (avg distance between anchors) */
        double s12 = vec_norm(vec_diff(p1, p2));
        double s13 = vec_norm(vec_diff(p1, p3));
        double s23 = vec_norm(vec_diff(p2, p3));
        double s_avg = (s12 + s13 + s23) / 3.0;
        
        /* GDOP is inversely proportional to the area. 
         * Minimum GDOP for equilateral triangle is ~1.2. */
        double gdop = (s_avg * s_avg) / (area2 + 0.001); 
        if (gdop < 1.2) gdop = 1.2;
        if (gdop > 20.0) gdop = 20.0;

        /* 3. Final Estimated Position Error (EPE) */
        result->error_estimate = rms * gdop;
    }

    return MW_TRIL_OK;
}

mw_tril_err_t mw_trilateration_2d(const mw_tril_anchor_t *anchors_exact_3,
                                  vec2d_t *position,
                                  mw_tril_result_t *result)
{
    if (!anchors_exact_3 || !position) {
        return MW_TRIL_ERR_PARAM;
    }

    /* Extract 2D coordinates */
    double x1 = anchors_exact_3[0].position.x;
    double y1 = anchors_exact_3[0].position.y;
    double r1 = anchors_exact_3[0].distance;
    double x2 = anchors_exact_3[1].position.x;
    double y2 = anchors_exact_3[1].position.y;
    double r2 = anchors_exact_3[1].distance;
    double x3 = anchors_exact_3[2].position.x;
    double y3 = anchors_exact_3[2].position.y;
    double r3 = anchors_exact_3[2].distance;

    /* 2D trilateration formula */
    double delta = 4.0 * ((x1-x2)*(y1-y3) - (x1-x3)*(y1-y2));
    if (fabs(delta) < MAXZERO) {
        return MW_TRIL_ERR_NO_SOLUTION;
    }

    double A = r2*r2 - r1*r1 - x2*x2 + x1*x1 - y2*y2 + y1*y1;
    double B = r3*r3 - r1*r1 - x3*x3 + x1*x1 - y3*y3 + y1*y1;

    position->x = (1.0/delta) * (2.0*A*(y1-y3) - 2.0*B*(y1-y2));
    position->y = (1.0/delta) * (2.0*B*(x1-x2) - 2.0*A*(x1-x3));

    /* Fill result if requested */
    if (result) {
        result->position.x = position->x;
        result->position.y = position->y;
        result->position.z = 0.0;
        result->num_anchors = 3;
        result->valid = true;

        /* 1. Calculate Residual RMS (Measurement inconsistency) */
        double d1 = sqrt((position->x-x1)*(position->x-x1) + (position->y-y1)*(position->y-y1));
        double d2 = sqrt((position->x-x2)*(position->x-x2) + (position->y-y2)*(position->y-y2));
        double d3 = sqrt((position->x-x3)*(position->x-x3) + (position->y-y3)*(position->y-y3));
        double e1 = fabs(d1 - r1);
        double e2 = fabs(d2 - r2);
        double e3 = fabs(d3 - r3);
        double rms = sqrt((e1*e1 + e2*e2 + e3*e3) / 3.0);

        /* 2. Calculate GDOP (Geometric factor)
         * GDOP in 2D is inversely proportional to triangle area. 
         * delta calculated above is 8 * Area. */
        double area = fabs(delta) / 8.0;
        
        /* Characteristic length (avg distance between anchors) */
        double s12 = sqrt((x1-x2)*(x1-x2) + (y1-y2)*(y1-y2));
        double s13 = sqrt((x1-x3)*(x1-x3) + (y1-y3)*(y1-y3));
        double s23 = sqrt((x2-x3)*(x2-x3) + (y2-y3)*(y2-y3));
        double s_avg = (s12 + s13 + s23) / 3.0;

        /* Geometric factor: Ratio of perimeter-square to area.
         * Minimum for equilateral triangle is ~1.2. */
        double gdop = (s_avg * s_avg) / (area + 0.001);
        if (gdop < 1.2) gdop = 1.2;
        if (gdop > 20.0) gdop = 20.0;

        /* 3. Final Estimated Position Error (EPE) in meters */
        result->error_estimate = rms * gdop;
    }

    return MW_TRIL_OK;
}

/* End of file -------------------------------------------------------- */
