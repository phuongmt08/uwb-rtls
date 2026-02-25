/**
 * @file       network_cmd.c
 * @brief 

/* Includes ----------------------------------------------------------------- */
#include "network_cmd.h"
#include "sys_stream.h"
#include "sys_logger.h"
#include "stm32f4xx_hal.h"
#include <string.h>

/* Private defines ---------------------------------------------------------- */
#define OBJECT_CODE LOG_OBJECT_CODE_NETWORK
#define RESP_RETRY_MAX 2
#define RESP_RETRY_DELAY_MS 200

/* Private types ------------------------------------------------------------ */
typedef void (*cmd_handler_t)(network_cmd_t *cmd, const protocol_packet_t *pkt);

typedef struct {
    uint32_t payload_tag;
    cmd_handler_t handler;
    const char *name;
} network_cmd_entry_t;

/* Private variables -------------------------------------------------------- */
static network_cmd_t *g_network_cmd_instance = NULL;

/* Private function prototypes ---------------------------------------------- */
static bool _network_cmd_packet_handler(const protocol_packet_t *pkt);
static void _network_cmd_retry_pending(network_cmd_t *cmd);
static void _queue_resp_retry(network_cmd_t *cmd, const protocol_packet_t *pkt);
static void _send_packet(network_cmd_t *cmd, protocol_packet_t *pkt);
static void _handle_unimplemented(network_cmd_t *cmd, const protocol_packet_t *pkt);

/* Command table placeholder ------------------------------------------------ */
static const network_cmd_entry_t network_cmd_table[] = {
    /* Keep empty intentionally - user will rebuild commands */
};

#define NETWORK_CMD_TABLE_SIZE (sizeof(network_cmd_table) / sizeof(network_cmd_entry_t))

/* Private implementations -------------------------------------------------- */

static void _queue_resp_retry(network_cmd_t *cmd, const protocol_packet_t *pkt)
{
    if (!cmd || !pkt) {
        return;
    }

    cmd->last_resp = *pkt;
    cmd->resp_pending = true;
    cmd->resp_retry_left = RESP_RETRY_MAX;
    cmd->resp_deadline_ms = HAL_GetTick() + RESP_RETRY_DELAY_MS;
}

static void _network_cmd_retry_pending(network_cmd_t *cmd)
{
    if (!cmd || !cmd->resp_pending) {
        return;
    }

    uint32_t now = HAL_GetTick();
    if ((int32_t)(now - cmd->resp_deadline_ms) < 0) {
        return;
    }

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
    if (!cmd || !cmd->stream || !pkt) {
        return;
    }

    sys_stream_send_packet(cmd->stream, pkt->hdr.addr.dst, pkt);
}

static void _handle_unimplemented(network_cmd_t *cmd, const protocol_packet_t *pkt)
{
    (void)cmd;
    if (!pkt) {
        return;
    }

    RLOG_W(OBJECT_CODE, "No command handler for payload tag=%u", (unsigned)pkt->which_payload);
}

/* Public implementations --------------------------------------------------- */

bool network_cmd_init(network_cmd_t *cmd, sys_stream_t *stream)
{
    if (!cmd || !stream) {
        return false;
    }

    memset(cmd, 0, sizeof(network_cmd_t));
    cmd->stream = stream;
    cmd->enabled = true;

    g_network_cmd_instance = cmd;
    return sys_stream_register_packet_handler(stream, _network_cmd_packet_handler);
}

void network_cmd_process(network_cmd_t *cmd)
{
    if (!cmd || !cmd->enabled) {
        return;
    }

    _network_cmd_retry_pending(cmd);
}

bool network_cmd_process_packet(network_cmd_t *cmd, const protocol_packet_t *pkt)
{
    if (!cmd || !pkt || !cmd->enabled) {
        return false;
    }

    if (pkt->which_payload == protocol_packet_t_ack_tag) {
        return true;
    }

    _handle_unimplemented(cmd, pkt);
    return false;
}

void network_cmd_dispatch(network_cmd_t *cmd, const protocol_packet_t *pkt)
{
    if (!cmd || !pkt || !cmd->enabled) {
        return;
    }

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
    if (!g_network_cmd_instance || !pkt) {
        return false;
    }

    network_cmd_dispatch(g_network_cmd_instance, pkt);
    return true;
}

/* End of file -------------------------------------------------------------- */
