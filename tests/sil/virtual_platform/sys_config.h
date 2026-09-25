#ifndef SYS_CONFIG_H_
#define SYS_CONFIG_H_

#include <stdint.h>
#include <stdbool.h>
#include "positioning_config.h"

#define SYS_CONFIG_MAX_ANCHORS MAX_ANCHORS_SUPPORTED

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint32_t anchor_id;
    float x_m;
    float y_m;
    float z_m;
} sys_anchor_layout_t;

typedef struct {
    bool enable;
    float recover_d2;
    float reject_d2;
    float r_base;
    float r_gate;
    float velocity_weight;
    float min_covariance;
} sys_prefilter_cfg_t;

typedef struct {
    uint32_t anchor_count;
    sys_anchor_layout_t anchor_layout[MAX_ANCHORS_SUPPORTED];
    sys_prefilter_cfg_t prefilter;
} sys_config_t;

sys_config_t *sys_config_get(void);
const sys_prefilter_cfg_t *sys_config_get_prefilter(void);
int sys_config_set_prefilter(const sys_prefilter_cfg_t *prefilter);
void sys_config_get_anchor_layout(sys_anchor_layout_t *anchors, uint32_t *count);
int sys_config_set_anchor_layout(const sys_anchor_layout_t *anchors, uint32_t count);

/* Helper for SIL test configuration */
void v_sys_config_init_default(void);

#ifdef __cplusplus
}
#endif

#endif /* SYS_CONFIG_H_ */
