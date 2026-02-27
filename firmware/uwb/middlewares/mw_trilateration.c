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

static void select_best_3_anchors(const mw_tril_anchor_t *anchors,
                                  uint8_t num_anchors,
                                  uint8_t selected[3]);
static double triangle_area_2d(vec3d_t a, vec3d_t b, vec3d_t c);
static double rssi_quality(int8_t rssi);

/* Vector operations -------------------------------------------------- */

static inline vec3d_t vec_diff(vec3d_t v1, vec3d_t v2) {
    vec3d_t r = {v1.x - v2.x, v1.y - v2.y, v1.z - v2.z};
    return r;
}

static inline vec3d_t vec_sum(vec3d_t v1, vec3d_t v2) {
    vec3d_t r = {v1.x + v2.x, v1.y + v2.y, v1.z + v2.z};
    return r;
}

static inline vec3d_t vec_mul(vec3d_t v, double s) {
    vec3d_t r = {v.x * s, v.y * s, v.z * s};
    return r;
}

static inline vec3d_t vec_div(vec3d_t v, double s) {
    vec3d_t r = {v.x / s, v.y / s, v.z / s};
    return r;
}

static inline double vec_norm(vec3d_t v) {
    return sqrt(v.x * v.x + v.y * v.y + v.z * v.z);
}

static inline vec3d_t vec_cross(vec3d_t v1, vec3d_t v2) {
    vec3d_t r = {
        v1.y * v2.z - v1.z * v2.y,
        v1.z * v2.x - v1.x * v2.z,
        v1.x * v2.y - v1.y * v2.x
    };
    return r;
}

static inline double vec_dot(vec3d_t v1, vec3d_t v2) {
    return v1.x * v2.x + v1.y * v2.y + v1.z * v2.z;
}

static double triangle_area_2d(vec3d_t a, vec3d_t b, vec3d_t c)
{
    double area2 = a.x * (b.y - c.y) + b.x * (c.y - a.y) + c.x * (a.y - b.y);
    return fabs(area2) * 0.5;
}

static double rssi_quality(int8_t rssi)
{
    /* Normalize RSSI into [0..1] with a conservative range of [-100..-40] dBm */
    double q = ((double)rssi + 100.0) / 60.0;
    if (q < 0.0) return 0.0;
    if (q > 1.0) return 1.0;
    return q;
}

/* Anchor selection --------------------------------------------------- */

static void select_best_3_anchors(const mw_tril_anchor_t *anchors,
                                  uint8_t num_anchors,
                                  uint8_t selected[3])
{
    uint8_t idx[NUM_ANCHORS];
    uint8_t valid_count = 0;

    for (uint8_t i = 0; i < num_anchors && i < NUM_ANCHORS; i++) {
        if (!anchors[i].valid) continue;
        idx[valid_count++] = i;
    }

    if (valid_count < 3) return;

    double best_score = -1.0;
    uint8_t best_i = idx[0], best_j = idx[1], best_k = idx[2];

    for (uint8_t a = 0; a < valid_count - 2; a++) {
        for (uint8_t b = a + 1; b < valid_count - 1; b++) {
            for (uint8_t c = b + 1; c < valid_count; c++) {
                uint8_t i = idx[a];
                uint8_t j = idx[b];
                uint8_t k = idx[c];

                double area = triangle_area_2d(anchors[i].position,
                                               anchors[j].position,
                                               anchors[k].position);
                double mean_dist = (anchors[i].distance + anchors[j].distance + anchors[k].distance) / 3.0;
                double rssi_q = (rssi_quality(anchors[i].rssi) +
                                 rssi_quality(anchors[j].rssi) +
                                 rssi_quality(anchors[k].rssi)) / 3.0;

                double score = area * (1.0 + rssi_q) / (1.0 + mean_dist);

                if (score > best_score) {
                    best_score = score;
                    best_i = i;
                    best_j = j;
                    best_k = k;
                }
            }
        }
    }

    selected[0] = best_i;
    selected[1] = best_j;
    selected[2] = best_k;
#ifdef ENABLE_DEBUG_LOGGING
    RLOG_D(LOG_OBJECT_CODE_POSITIONING,
           "Selected anchors: #%u (%.3fm), #%u (%.3fm), #%u (%.3fm)",
           selected[0], anchors[selected[0]].distance,
           selected[1], anchors[selected[1]].distance,
           selected[2], anchors[selected[2]].distance);
#endif
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

mw_tril_err_t mw_trilateration_3d(const mw_tril_anchor_t *anchors,
                                  uint8_t num_anchors,
                                  vec3d_t *position,
                                  mw_tril_result_t *result)
{
    if (!anchors || !position || num_anchors < 3) {
        return MW_TRIL_ERR_PARAM;
    }

    /* Select best 3 anchors */
    uint8_t selected[3] = {0, 1, 2};
    if (num_anchors > 3) {
        select_best_3_anchors(anchors, num_anchors, selected);
    } else {
        /* Use first 3 valid anchors */
        uint8_t count = 0;
        for (uint8_t i = 0; i < num_anchors && count < 3; i++) {
            if (anchors[i].valid) {
                selected[count++] = i;
            }
        }
        if (count < 3) return MW_TRIL_ERR_PARAM;
    }

    /* Extract selected anchors */
    vec3d_t p1 = anchors[selected[0]].position;
    vec3d_t p2 = anchors[selected[1]].position;
    vec3d_t p3 = anchors[selected[2]].position;
    double r1 = anchors[selected[0]].distance;
    double r2 = anchors[selected[1]].distance;
    double r3 = anchors[selected[2]].distance;

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

        /* Calculate RMS error */
        double e1 = fabs(vec_norm(vec_diff(*position, p1)) - r1);
        double e2 = fabs(vec_norm(vec_diff(*position, p2)) - r2);
        double e3 = fabs(vec_norm(vec_diff(*position, p3)) - r3);
        result->error_estimate = sqrt((e1*e1 + e2*e2 + e3*e3) / 3.0);
    }

    return MW_TRIL_OK;
}

mw_tril_err_t mw_trilateration_2d(const mw_tril_anchor_t *anchors,
                                  uint8_t num_anchors,
                                  vec2d_t *position,
                                  mw_tril_result_t *result)
{
    if (!anchors || !position || num_anchors < 3) {
        return MW_TRIL_ERR_PARAM;
    }

    uint8_t selected[3] = {0, 1, 2};
    if (num_anchors > 3) {
        select_best_3_anchors(anchors, num_anchors, selected);
    } else {
        uint8_t count = 0;
        for (uint8_t i = 0; i < num_anchors && count < 3; i++) {
            if (anchors[i].valid) {
                selected[count++] = i;
            }
        }
        if (count < 3) return MW_TRIL_ERR_PARAM;
    }

    /* Extract 2D coordinates */
    double x1 = anchors[selected[0]].position.x;
    double y1 = anchors[selected[0]].position.y;
    double r1 = anchors[selected[0]].distance;
    double x2 = anchors[selected[1]].position.x;
    double y2 = anchors[selected[1]].position.y;
    double r2 = anchors[selected[1]].distance;
    double x3 = anchors[selected[2]].position.x;
    double y3 = anchors[selected[2]].position.y;
    double r3 = anchors[selected[2]].distance;

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

        /* Calculate RMS error */
        double d1 = sqrt((position->x-x1)*(position->x-x1) + 
                        (position->y-y1)*(position->y-y1));
        double d2 = sqrt((position->x-x2)*(position->x-x2) + 
                        (position->y-y2)*(position->y-y2));
        double d3 = sqrt((position->x-x3)*(position->x-x3) + 
                        (position->y-y3)*(position->y-y3));
        double e1 = fabs(d1 - r1);
        double e2 = fabs(d2 - r2);
        double e3 = fabs(d3 - r3);
        result->error_estimate = sqrt((e1*e1 + e2*e2 + e3*e3) / 3.0);
    }

    return MW_TRIL_OK;
}

/* End of file -------------------------------------------------------- */