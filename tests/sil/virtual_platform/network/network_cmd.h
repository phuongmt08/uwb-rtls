#ifndef NETWORK_CMD_H_
#define NETWORK_CMD_H_

#include "network/network_core.h"
#include "protos/protocol.pb.h"

#ifdef __cplusplus
extern "C" {
#endif

static inline bool network_send_calib_data(network_core_t *net, ...)
{
    (void)net;
    return true;
}

static inline bool network_send_sensor_fusion_result(network_core_t *net, ...)
{
    (void)net;
    return true;
}

#ifdef __cplusplus
}
#endif

#endif /* NETWORK_CMD_H_ */
