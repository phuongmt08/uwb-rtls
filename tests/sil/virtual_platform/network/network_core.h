#ifndef NETWORK_CORE_H_
#define NETWORK_CORE_H_

#include <stdint.h>
#include <stdbool.h>
#include "positioning_config.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct uwb_distance_msg {
    uint8_t count;
    uint8_t mask;
    uint8_t anchor_ids[MAX_ANCHORS_SUPPORTED];
    float distances[MAX_ANCHORS_SUPPORTED];
    float fp_amp_norm[MAX_ANCHORS_SUPPORTED];
    float fp_snr[MAX_ANCHORS_SUPPORTED];
    float fp_confidence[MAX_ANCHORS_SUPPORTED];
    uint8_t quality_valid[MAX_ANCHORS_SUPPORTED];
    uint32_t ranging_error_count;
} uwb_distance_msg_t;

typedef struct {
    bool enabled;
} network_core_t;

extern network_core_t g_network_core;

#ifdef __cplusplus
}
#endif

#endif /* NETWORK_CORE_H_ */
