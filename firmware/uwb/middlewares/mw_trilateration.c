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

#ifndef MW_TRIL_D2_RECOVER
#define MW_TRIL_D2_RECOVER 4.0
#endif

#ifndef MW_TRIL_D2_REJECT
#define MW_TRIL_D2_REJECT 9.0
#endif

#ifndef MW_TRIL_RESIDUAL_SCALE_M
#define MW_TRIL_RESIDUAL_SCALE_M 0.30
#endif

#ifndef MW_TRIL_FP_NORM_GOOD
#define MW_TRIL_FP_NORM_GOOD 8.0
#endif

#ifndef MW_TRIL_FP_SNR_GOOD
#define MW_TRIL_FP_SNR_GOOD 12.0
#endif

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

/* Anchor selection --------------------------------------------------- */

uint8_t mw_trilateration_select_best(const mw_tril_anchor_t *anchors,
                                     uint8_t total_anchors,
                                     mw_tril_anchor_t *best_out,
                                     uint8_t max_out)
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

    uint8_t best_i = 0, best_j = 1, best_k = 2;
    double  best_score = 1.0e9;
    double  best_residual = 0.0;
    double  best_gdop_penalty = 0.0;
    double  best_fp_penalty = 0.0;
    double  best_fp_snr_penalty = 0.0;

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
                    ((valid_anchors[i].quality_valid ? fp_quality_penalty(valid_anchors[i].fp_amp_norm, MW_TRIL_FP_NORM_GOOD) : 1.0)
                   + (valid_anchors[j].quality_valid ? fp_quality_penalty(valid_anchors[j].fp_amp_norm, MW_TRIL_FP_NORM_GOOD) : 1.0)
                   + (valid_anchors[k].quality_valid ? fp_quality_penalty(valid_anchors[k].fp_amp_norm, MW_TRIL_FP_NORM_GOOD) : 1.0)) / 3.0;
                double avg_fp_snr_penalty =
                    ((valid_anchors[i].quality_valid ? fp_quality_penalty(valid_anchors[i].fp_snr, MW_TRIL_FP_SNR_GOOD) : 1.0)
                   + (valid_anchors[j].quality_valid ? fp_quality_penalty(valid_anchors[j].fp_snr, MW_TRIL_FP_SNR_GOOD) : 1.0)
                   + (valid_anchors[k].quality_valid ? fp_quality_penalty(valid_anchors[k].fp_snr, MW_TRIL_FP_SNR_GOOD) : 1.0)) / 3.0;

                double score = (0.35 * avg_d2_penalty) + (0.15 * avg_fp_penalty)
                             + (0.10 * avg_fp_snr_penalty) + (0.20 * gdop_penalty)
                             + (0.20 * residual_penalty);

                if (score < best_score) {
                    best_score = score;
                    best_i = i;
                    best_j = j;
                    best_k = k;
                    best_residual = residual;
                    best_gdop_penalty = gdop_penalty;
                    best_fp_penalty = avg_fp_penalty;
                    best_fp_snr_penalty = avg_fp_snr_penalty;
                }
            }
        }
    }

    if (best_score >= 1.0e9) {
        return 0;
    }

    best_out[0] = valid_anchors[best_i];
    best_out[1] = valid_anchors[best_j];
    best_out[2] = valid_anchors[best_k];

    for (uint8_t i = 0; i < 3U; i++) {
        best_out[i].selection_score = best_score;
        best_out[i].residual_rms = best_residual;
        best_out[i].gdop_penalty = best_gdop_penalty;
        best_out[i].fp_penalty = best_fp_penalty;
        best_out[i].fp_snr_penalty = best_fp_snr_penalty;
    }

#ifdef ENABLE_DEBUG_LOGGING
    RLOG_D(LOG_OBJECT_CODE_TAG,
            "Best composite anchors: #%u #%u #%u (score=%.3f residual=%.3f gdop=%.3f)",
            best_out[0].id, best_out[1].id, best_out[2].id,
            best_score, best_residual, best_gdop_penalty);
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
