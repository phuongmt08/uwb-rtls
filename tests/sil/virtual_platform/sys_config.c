#include "sys_config.h"
#include <string.h>

static sys_config_t s_sys_config;

void v_sys_config_init_default(void)
{
    memset(&s_sys_config, 0, sizeof(s_sys_config));

    s_sys_config.anchor_count = 4U;
    /* Default 4 anchors in standard rectangle: (0,0), (5,0), (5,5), (0,5) at z=2.495m */
    s_sys_config.anchor_layout[0].anchor_id = 1U;
    s_sys_config.anchor_layout[0].x_m = 0.0f;
    s_sys_config.anchor_layout[0].y_m = 0.0f;
    s_sys_config.anchor_layout[0].z_m = ANCHOR_HEIGHT_M;

    s_sys_config.anchor_layout[1].anchor_id = 2U;
    s_sys_config.anchor_layout[1].x_m = 5.0f;
    s_sys_config.anchor_layout[1].y_m = 0.0f;
    s_sys_config.anchor_layout[1].z_m = ANCHOR_HEIGHT_M;

    s_sys_config.anchor_layout[2].anchor_id = 3U;
    s_sys_config.anchor_layout[2].x_m = 5.0f;
    s_sys_config.anchor_layout[2].y_m = 5.0f;
    s_sys_config.anchor_layout[2].z_m = ANCHOR_HEIGHT_M;

    s_sys_config.anchor_layout[3].anchor_id = 4U;
    s_sys_config.anchor_layout[3].x_m = 0.0f;
    s_sys_config.anchor_layout[3].y_m = 5.0f;
    s_sys_config.anchor_layout[3].z_m = ANCHOR_HEIGHT_M;

    /* Prefilter default config */
    s_sys_config.prefilter.enable = true;
    s_sys_config.prefilter.recover_d2 = MAHALANOBIS_PREFILTER_D2_RECOVER;
    s_sys_config.prefilter.reject_d2 = MAHALANOBIS_PREFILTER_D2_REJECT;
    s_sys_config.prefilter.r_base = MAHALANOBIS_PREFILTER_R_BASE;
    s_sys_config.prefilter.r_gate = MAHALANOBIS_PREFILTER_R_GATE;
    s_sys_config.prefilter.velocity_weight = MAHALANOBIS_PREFILTER_VELOCITY_WEIGHT;
    s_sys_config.prefilter.min_covariance = MAHALANOBIS_PREFILTER_MIN_COVARIANCE;
}

sys_config_t *sys_config_get(void)
{
    return &s_sys_config;
}

const sys_prefilter_cfg_t *sys_config_get_prefilter(void)
{
    return &s_sys_config.prefilter;
}

int sys_config_set_prefilter(const sys_prefilter_cfg_t *prefilter)
{
    if (prefilter == NULL) return -1;
    s_sys_config.prefilter = *prefilter;
    return 0;
}

void sys_config_get_anchor_layout(sys_anchor_layout_t *anchors, uint32_t *count)
{
    if (anchors == NULL || count == NULL) return;
    *count = s_sys_config.anchor_count;
    memcpy(anchors, s_sys_config.anchor_layout, sizeof(sys_anchor_layout_t) * s_sys_config.anchor_count);
}

int sys_config_set_anchor_layout(const sys_anchor_layout_t *anchors, uint32_t count)
{
    if (anchors == NULL || count > SYS_CONFIG_MAX_ANCHORS) return -1;
    s_sys_config.anchor_count = count;
    memcpy(s_sys_config.anchor_layout, anchors, sizeof(sys_anchor_layout_t) * count);
    return 0;
}
