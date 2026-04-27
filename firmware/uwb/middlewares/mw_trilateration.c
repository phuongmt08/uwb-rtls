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

/* Anchor selection --------------------------------------------------- */

uint8_t mw_trilateration_select_best(const mw_tril_anchor_t *anchors, 
                                     uint8_t total_anchors, 
                                     mw_tril_anchor_t *best_out, 
                                     uint8_t max_out)
{
    if (!anchors || !best_out || max_out == 0) return 0;
    
    // Copy valid anchors to a local array for sorting
    mw_tril_anchor_t valid_anchors[8]; // Assumes max 8 anchors per slot
    uint8_t valid_count = 0;
    
    for (uint8_t i = 0; i < total_anchors; i++) {
        if (anchors[i].valid) {
            if (valid_count < 8) {
                valid_anchors[valid_count++] = anchors[i];
            }
        }
    }
    
    if (valid_count == 0) return 0;
    
    // Sort array in ascending order of d2_score using simple selection sort
    for (uint8_t i = 0; i < valid_count - 1; i++) {
        uint8_t min_idx = i;
        for (uint8_t j = i + 1; j < valid_count; j++) {
            if (valid_anchors[j].d2_score < valid_anchors[min_idx].d2_score) {
                min_idx = j;
            }
        }
        // Swap
        if (min_idx != i) {
            mw_tril_anchor_t temp = valid_anchors[i];
            valid_anchors[i] = valid_anchors[min_idx];
            valid_anchors[min_idx] = temp;
        }
    }
    
    // Copy top M to output
    uint8_t count = (valid_count < max_out) ? valid_count : max_out;
    for (uint8_t i = 0; i < count; i++) {
        best_out[i] = valid_anchors[i];
    }
    
#ifdef ENABLE_DEBUG_LOGGING
    if (count >= 3) {
        RLOG_D(LOG_OBJECT_CODE_POSITIONING,
               "Selected best anchors by d2: #%u(d2:%.2f), #%u(d2:%.2f), #%u(d2:%.2f)",
               best_out[0].id, best_out[0].d2_score,
               best_out[1].id, best_out[1].d2_score,
               best_out[2].id, best_out[2].d2_score);
    }
#endif

    return count;
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

        /* Calculate RMS error */
        double e1 = fabs(vec_norm(vec_diff(*position, p1)) - r1);
        double e2 = fabs(vec_norm(vec_diff(*position, p2)) - r2);
        double e3 = fabs(vec_norm(vec_diff(*position, p3)) - r3);
        result->error_estimate = sqrt((e1*e1 + e2*e2 + e3*e3) / 3.0);
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