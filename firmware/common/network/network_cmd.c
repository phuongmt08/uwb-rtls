#include "network_cmd.h"
#include "sys_logger.h"
#include "stm32f4xx_hal.h"

#include <string.h>

#define OBJECT_CODE LOG_OBJECT_CODE_NETWORK
#define RESP_RETRY_MAX 2
#define RESP_RETRY_DELAY_MS 200

#define CHECK(_cond, _ret) do { if (!(_cond)) return (_ret); } while (0)
#define CHECK_VOID(_cond) do { if (!(_cond)) return; } while (0)

typedef void (*cmd_handler_t)(network_cmd_t *cmd, const protocol_packet_t *pkt);

typedef struct {
    uint32_t payload_tag;
    cmd_handler_t handler;
    const char *name;
} network_cmd_entry_t;

static network_cmd_t *g_network_cmd_instance = NULL;

static bool _network_cmd_packet_handler(const protocol_packet_t *pkt);
static void _network_cmd_retry_pending(network_cmd_t *cmd);
static void _send_packet(network_cmd_t *cmd, protocol_packet_t *pkt);
static void _handle_unimplemented(network_cmd_t *cmd, const protocol_packet_t *pkt);

static const network_cmd_entry_t network_cmd_table[] = {
};

#define NETWORK_CMD_TABLE_SIZE (sizeof(network_cmd_table) / sizeof(network_cmd_entry_t))

static void _network_cmd_retry_pending(network_cmd_t *cmd)
{
    CHECK_VOID(cmd && cmd->resp_pending);

    uint32_t now = HAL_GetTick();
    CHECK_VOID((int32_t)(now - cmd->resp_deadline_ms) >= 0);

    if (cmd->resp_retry_left == 0) {
        cmd->resp_pending = false;
        return;
    }

    protocol_packet_t pkt = cmd->last_resp;
    _send_packet(cmd, &pkt);

    cmd->resp_retry_left--;
    cmd->resp_deadline_ms = now + RESP_RETRY_DELAY_MS;
}

static void _send_packet(network_cmd_t *cmd, protocol_packet_t *pkt)
{
    CHECK_VOID(cmd && cmd->stream && pkt);
    (void)network_core_send_packet(cmd->stream, pkt->hdr.addr.dst, pkt);
}

static void _handle_unimplemented(network_cmd_t *cmd, const protocol_packet_t *pkt)
{
    (void)cmd;
    CHECK_VOID(pkt);
    RLOG_W(OBJECT_CODE, "No command handler for payload tag=%u", (unsigned)pkt->which_payload);
}

bool network_cmd_init(network_cmd_t *cmd, network_core_t *stream)
{
    CHECK(cmd && stream, false);

    memset(cmd, 0, sizeof(network_cmd_t));
    cmd->stream = stream;
    cmd->enabled = true;

    g_network_cmd_instance = cmd;
    return network_core_register_packet_handler(stream, _network_cmd_packet_handler);
}

void network_cmd_process(network_cmd_t *cmd)
{
    CHECK_VOID(cmd && cmd->enabled);
    _network_cmd_retry_pending(cmd);
}

bool network_cmd_process_packet(network_cmd_t *cmd, const protocol_packet_t *pkt)
{
    CHECK(cmd && pkt && cmd->enabled, false);

    if (pkt->which_payload == protocol_packet_t_ack_tag) {
        return true;
    }

    _handle_unimplemented(cmd, pkt);
    return false;
}

void network_cmd_dispatch(network_cmd_t *cmd, const protocol_packet_t *pkt)
{
    CHECK_VOID(cmd && pkt && cmd->enabled);

    if (pkt->which_payload == protocol_packet_t_ack_tag) {
        return;
    }

    for (size_t i = 0; i < NETWORK_CMD_TABLE_SIZE; i++) {
        if (network_cmd_table[i].payload_tag == pkt->which_payload) {
            network_cmd_table[i].handler(cmd, pkt);
            return;
        }
    }

    _handle_unimplemented(cmd, pkt);
}

bool network_cmd_is_distance_streaming(const network_cmd_t *cmd)
{
    (void)cmd;
    return false;
}

bool network_cmd_is_log_streaming(const network_cmd_t *cmd)
{
    (void)cmd;
    return false;
}

static bool _network_cmd_packet_handler(const protocol_packet_t *pkt)
{
    CHECK(g_network_cmd_instance && pkt, false);
    network_cmd_dispatch(g_network_cmd_instance, pkt);
    return true;
}
