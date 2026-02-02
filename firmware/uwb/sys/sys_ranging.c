    /* ============================== sys_ranging.c ==============================
    * @file       sys_ranging.c
    * @brief      DS-TWR + TDMA Multi-Anchor Ranging - BUGS FIXED
    * @version    6.1.0
    * @date       2026-02-01
    * 
    * CRITICAL FIXES APPLIED:
    * - Fix #1: Removed duplicate T1 read
    * - Fix #2: Enable RX immediately after POLL TX (before logging!)
    * - Fix #3: ANCHOR uses proper TDMA slot timing
    * - Fix #4: ANCHOR syncs to POLL RX timestamp
    * - Fix #5: Improved message validation
    */

    /* Includes ----------------------------------------------------------- */
    #include "sys_ranging.h"
    #include "sys_config.h"
    #include "sys_logger.h"
    #include "bsp_uwb.h"
    #include "bsp_util.h"
    #include "mw_tdma_scheduler.h"
    #include <stdint.h>
    #include <string.h>
    #include <stdio.h>
    #include <math.h>

    /* Constants ---------------------------------------------------------- */
    #define DWT_TIME_UNITS          (1.0/499.2e6/128.0)
    #define SPEED_OF_LIGHT          299702547.0
    #define POLL_TX_OFFSET_US       5000
    #define FINAL_RX_MARGIN_US      15000
    #define SLOT_RX_OFFSET_US       50
    #define TDMA_POLL_TO_RESP_US    2000
    #define TDMA_RESP_TO_FINAL_US   12000

    #define UWB_RX_BUFFER_SIZE      256

    /* Message type constants */
    #define MW_DSTWR_MSG_TYPE_POLL   0xE1
    #define MW_DSTWR_MSG_TYPE_RESP   0xE2
    #define MW_DSTWR_MSG_TYPE_FINAL  0xE3
#define MW_DSTWR_MSG_TYPE_RESULT 0xE4  /* Anchor sends distance to TAG */
    /* Private types ------------------------------------------------------ */
    typedef enum {
    STATE_IDLE = 0,
    STATE_TAG_RANGING_TDMA,
    STATE_TAG_COMPLETE,
    STATE_ANCHOR_RANGING_TDMA,
    STATE_ANCHOR_COMPLETE,
    STATE_ERROR
    } ranging_state_t;

    typedef struct __attribute__((packed)) {
    uint8_t msg_type;
    uint8_t sequence_num;
    uint8_t tag_id;
    uint8_t num_anchors;
    uint8_t anchor_mask;
    uint8_t rssi_last;
    uint8_t padding[7];
    } poll_msg_t;

    typedef struct __attribute__((packed)) {
    uint8_t msg_type;
    uint8_t sequence_num;
    uint8_t anchor_id;
    uint8_t slot_id;
    uint64_t poll_rx_ts;
    uint64_t resp_tx_ts;
    uint8_t rssi_poll;
    uint8_t padding[3];
    } resp_msg_t;

    typedef struct __attribute__((packed)) {
    uint8_t msg_type;
    uint8_t sequence_num;
    uint8_t tag_id;
    uint8_t num_responses;
    uint64_t poll_tx_ts;
    uint8_t anchor_resp_mask;
    uint8_t padding[3];
    } final_msg_t;

    typedef struct __attribute__((packed)) {
    uint8_t anchor_id;
    uint64_t resp_rx_ts;
    uint64_t final_tx_ts;
    } final_anchor_data_t;

/* RESULT message: Anchor sends calculated distance to TAG */
typedef struct __attribute__((packed)) {
  uint8_t msg_type;
  uint8_t sequence_num;
  uint8_t anchor_id;
  uint8_t valid;          /* 1 = valid distance, 0 = error */
  float distance_m;       /* Calculated distance */
  int8_t rssi;
  uint8_t padding[2];
} result_msg_t;
    typedef struct {
    ranging_state_t state;
    uint32_t state_entry_tick;
    uint8_t sequence_num;
    
    /* Results */
    sys_ranging_multi_result_t result_multi;
    sys_ranging_result_t result_single;
    bool has_result;
    
    /* State for anchor */
    uint8_t anchor_id;
    
    } ranging_ctx_t;

    /* Private variables -------------------------------------------------- */
    static ranging_ctx_t s_ctx = {0};
    static tdma_scheduler_t s_tdma_tag = {0};
    // static tdma_scheduler_t s_tdma_anchor = {0};
    static struct {
    uint32_t total_count;
    uint32_t success_count;
    uint32_t error_count;
    } s_stats = {0};

    /* Static guard */
    static bool s_ranging_busy = false;

    /* Helper functions --------------------------------------------------- */

    /* DS-TWR timestamp structure */
    typedef struct {
        uint64_t t1, t2, t3, t4, t5, t6;
    } dstwr_timestamps_t;

    static float calculate_distance(const dstwr_timestamps_t *ts)
    {
        uint64_t t1 = ts->t1 & 0x000000FFFFFFFFFFULL;
        uint64_t t2 = ts->t2 & 0x000000FFFFFFFFFFULL;
        uint64_t t3 = ts->t3 & 0x000000FFFFFFFFFFULL;
        uint64_t t4 = ts->t4 & 0x000000FFFFFFFFFFULL;
        uint64_t t5 = ts->t5 & 0x000000FFFFFFFFFFULL;
        uint64_t t6 = ts->t6 & 0x000000FFFFFFFFFFULL;
        
        int64_t Ra = (int64_t)t4 - (int64_t)t1;
        int64_t Rb = (int64_t)t6 - (int64_t)t3;
        int64_t Da = (int64_t)t5 - (int64_t)t2;
        int64_t Db = (int64_t)t3 - (int64_t)t2;
        
        double tof_dw = (double)(Ra * Rb - Da * Db) / (double)(Ra + Rb + Da + Db);
        return (float)(tof_dw * DWT_TIME_UNITS * SPEED_OF_LIGHT);
    }

    static void format_distance_m(char *buf, size_t len, float distance_m)
    {
        int32_t mm = (int32_t)(distance_m * 1000.0f + (distance_m >= 0.0f ? 0.5f : -0.5f));
        int32_t abs_mm = (mm >= 0) ? mm : -mm;
        uint32_t m_part = (uint32_t)(abs_mm / 1000);
        uint32_t frac_part = (uint32_t)(abs_mm % 1000);

        if (mm < 0) {
            snprintf(buf, len, "-%lu.%03lu", (unsigned long)m_part, (unsigned long)frac_part);
        } else {
            snprintf(buf, len, "%lu.%03lu", (unsigned long)m_part, (unsigned long)frac_part);
        }
    }

    /* FIX #5: Improved message validation */
    static bool validate_msg_type(const uint8_t *data, uint16_t len, uint8_t expected_type)
    {
        if (!data) return false;
        
        /* Check minimum length based on message type */
        uint16_t min_len = 0;
        switch (expected_type) {
            case MW_DSTWR_MSG_TYPE_POLL:
                min_len = sizeof(poll_msg_t);
                break;
            case MW_DSTWR_MSG_TYPE_RESP:
                min_len = sizeof(resp_msg_t);
                break;
            case MW_DSTWR_MSG_TYPE_FINAL:
                min_len = sizeof(final_msg_t);
                break;
            case MW_DSTWR_MSG_TYPE_RESULT:
                min_len = sizeof(result_msg_t);
                break;
            default:
                return false;
        }
        
        /* CRITICAL: validate ONLY - NO side effects! */
        if (len < min_len) return false;
        if (data[0] != expected_type) return false;
        
        return true;
    }

    static void state_machine_reset(void)
    {
        s_ctx.has_result = false;
        memset(&s_ctx.result_multi, 0, sizeof(s_ctx.result_multi));
        memset(&s_ctx.result_single, 0, sizeof(s_ctx.result_single));
        bsp_uwb_idle();
    }

    static void log_ranging_result(const sys_ranging_result_t *result, const char *role)
    {
        if (!result || !result->valid) return;
        if (result->distance_m > 100.0f || result->distance_m < 0.0f) {
            RLOG_W(LOG_OBJECT_CODE_RANGING, "[%s] Invalid distance: %.3f m - REJECTED", 
                role, result->distance_m);
            return;
        }
        
        s_stats.success_count++;
        char dist_str[16];
        format_distance_m(dist_str, sizeof(dist_str), result->distance_m);
        RLOG_I(LOG_OBJECT_CODE_RANGING, "[%s] Distance: %s m [A:%u RSSI:%ddBm]", 
            role, dist_str, result->anchor_id, result->rssi);
    }

    static void spin_wait_us(uint32_t us)
    {
        uint64_t target_dw = bsp_uwb_get_current_time_dw() + tdma_us_to_dw(us);
        while (bsp_uwb_get_current_time_dw() < target_dw) {
            __NOP();
        }
    }

    static uint64_t ensure_future_tx(uint64_t tx_time_dw, uint32_t guard_us)
    {
        uint64_t now = bsp_uwb_get_current_time_dw();
        uint64_t guard_dw = tdma_us_to_dw(guard_us);
        if ((int64_t)(tx_time_dw - now) <= (int64_t)guard_dw) {
            tx_time_dw = (now + guard_dw * 2ULL) & 0x000000FFFFFFFFFFULL;
        }
        return tx_time_dw;
    }

    static int ds_twr_anchor_tdma(uint8_t anchor_id, uint8_t num_anchors,
                                const uint8_t *anchor_ids, uint32_t rx_timeout_us)
    {
        if (s_ranging_busy) return -1;
        s_ranging_busy = true;

        /* SIMPLIFIED: Anchor doesn't need full TDMA scheduler
        * Only needs: slot_id, slot_duration, base_offset
        */
        
        /* Find my slot_id */
        uint8_t my_slot_id = 0;
        bool found = false;
        for (uint8_t i = 0; i < num_anchors; i++) {
            if (anchor_ids[i] == anchor_id) {
                my_slot_id = i;
                found = true;
                break;
            }
        }
        
        if (!found) {
            RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[ANCHOR] anchor_id %u not in anchor_ids list", anchor_id);
            s_ranging_busy = false;
            return -1;
        }
        
        /* TDMA timing constants */
        const uint32_t SLOT_DURATION_US = 4000;      /* 4ms per slot */
        const uint32_t POLL_TO_RESP_BASE_US = 5000;  /* 5ms base offset */

        /* 1. Receive POLL */
        uint8_t poll_buf[128];
        uint16_t poll_len = 0;

        RLOG_I(LOG_OBJECT_CODE_RANGING, "[ANCHOR] Listening for POLL (timeout=%luus)...", (unsigned long)rx_timeout_us);

        if (bsp_uwb_enable_rx(0) != BSP_OK) {
            RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[ANCHOR] Failed to enable RX for POLL");
            s_ranging_busy = false;
            return -1;
        }

        uint64_t start_dw = bsp_uwb_get_current_time_dw();
        uint64_t poll_timeout_dw = tdma_us_to_dw(rx_timeout_us);

        while (bsp_uwb_get_current_time_dw() - start_dw < poll_timeout_dw) {
            bsp_err_t err = bsp_uwb_rx(poll_buf, sizeof(poll_buf), &poll_len);
            if (err == BSP_OK && poll_len > 0 && validate_msg_type(poll_buf, poll_len, MW_DSTWR_MSG_TYPE_POLL)) {
                break;
            }
            __NOP();
        }

        if (poll_len == 0) {
            RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[ANCHOR] No POLL received (timeout=%luus)", (unsigned long)rx_timeout_us);
            s_ranging_busy = false;
            return -1;
        }

        poll_msg_t *poll = (poll_msg_t *)poll_buf;

        /* Read T2 (POLL RX timestamp on anchor) */
        uint64_t poll_rx_ts = 0;
        if (bsp_uwb_read_40bit(0x15, 0x00, &poll_rx_ts) != BSP_OK) {
            RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[ANCHOR] Failed to read T2 timestamp");
            s_ranging_busy = false;
            return -1;
        }

        /* SIMPLIFIED: Calculate RESP TX time directly
        * resp_tx = poll_rx + base_offset + slot_id * slot_duration
        * No need for full TDMA sync/scheduler
        */
        uint32_t resp_offset_us = POLL_TO_RESP_BASE_US + (my_slot_id * SLOT_DURATION_US);
        uint64_t resp_tx_time_dw = poll_rx_ts + tdma_us_to_dw(resp_offset_us);
        resp_tx_time_dw &= 0x000000FFFFFFFFFFULL;
        
        RLOG_I(LOG_OBJECT_CODE_RANGING, "[ANCHOR] T2=%llu, RESP_TX=%llu (slot=%u, offset=%luus)", 
            (unsigned long long)poll_rx_ts, (unsigned long long)resp_tx_time_dw, 
            my_slot_id, (unsigned long)resp_offset_us);

        /* Ensure future TX */
        resp_tx_time_dw = ensure_future_tx(resp_tx_time_dw, TDMA_DEFAULT_GUARD_TIME_US);

        /* Add antenna delay */
        uint16_t tx_ant_dly = bsp_uwb_get_tx_antenna_delay();
        uint64_t t3_timestamp = (resp_tx_time_dw + tx_ant_dly) & 0x000000FFFFFFFFFFULL;

        /* Build and transmit response */
        resp_msg_t resp_msg = {0};
        resp_msg.msg_type = MW_DSTWR_MSG_TYPE_RESP;
        resp_msg.sequence_num = poll->sequence_num;
        resp_msg.anchor_id = anchor_id;
        resp_msg.slot_id = my_slot_id;
        resp_msg.poll_rx_ts = poll_rx_ts;
        resp_msg.resp_tx_ts = t3_timestamp;
        resp_msg.rssi_poll = (uint8_t)bsp_uwb_get_rssi();

        if (bsp_uwb_tx_delayed(&resp_msg, sizeof(resp_msg), resp_tx_time_dw) != BSP_OK) {
            RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[ANCHOR] TX_DELAYED failed for RESP (time=%llu)", (unsigned long long)resp_tx_time_dw);
            s_ranging_busy = false;
            return -1;
        }

        /* 3. Wait for FINAL */
        uint8_t final_buf[256];
        uint16_t final_len = 0;

        /* SIMPLIFIED: Don't calculate exact FINAL time
        * Just use generous timeout: RESP_BASE + num_anchors * slot + margin
        */
        uint32_t final_timeout_us = POLL_TO_RESP_BASE_US + (num_anchors * SLOT_DURATION_US) + 
                                    TDMA_RESP_TO_FINAL_US + 5000; /* 5ms margin (was 20ms) */
        if (final_timeout_us > 100000) final_timeout_us = 100000; /* Max 100ms */

        if (bsp_uwb_enable_rx(0) != BSP_OK) {
            RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[ANCHOR] Failed to enable RX for FINAL");
            s_ranging_busy = false;
            return -1;
        }

        uint64_t final_rx_start_dw = bsp_uwb_get_current_time_dw();
        uint64_t final_timeout_dw = tdma_us_to_dw(final_timeout_us);

        while (bsp_uwb_get_current_time_dw() - final_rx_start_dw < final_timeout_dw) {
            bsp_err_t err = bsp_uwb_rx(final_buf, sizeof(final_buf), &final_len);
            if (err == BSP_OK && final_len > 0 && validate_msg_type(final_buf, final_len, MW_DSTWR_MSG_TYPE_FINAL)) {
                break;
            }
            __NOP();
        }

        if (final_len == 0) {
            RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[ANCHOR] No FINAL received (timeout=%luus)", (unsigned long)final_timeout_us);
            s_ranging_busy = false;
            return -1;
        }

        final_msg_t *final_msg = (final_msg_t *)final_buf;

        /* CRITICAL: Validate FINAL sequence_num matches POLL */
        if (final_msg->sequence_num != poll->sequence_num) {
            RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, 
                   "[ANCHOR] FINAL seq=%u mismatch POLL seq=%u", 
                   final_msg->sequence_num, poll->sequence_num);
            s_ranging_busy = false;
            return -1;
        }

        /* Read T6 */
        uint64_t final_rx_ts = 0;
        if (bsp_uwb_read_40bit(0x15, 0x00, &final_rx_ts) != BSP_OK) {
            RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[ANCHOR] Failed to read T6 timestamp");
            s_ranging_busy = false;
            return -1;
        }

        /* Extract our data */
        final_anchor_data_t *anchor_data = (final_anchor_data_t *)(final_buf + sizeof(final_msg_t));
        bool anchor_found = false;
        uint64_t resp_rx_ts_tag = 0;
        uint64_t final_tx_ts_tag = 0;

        for (uint8_t i = 0; i < final_msg->num_responses; i++) {
            if (anchor_data[i].anchor_id == anchor_id) {
                resp_rx_ts_tag = anchor_data[i].resp_rx_ts;
                final_tx_ts_tag = anchor_data[i].final_tx_ts;
                anchor_found = true;
                break;
            }
        }

        if (!anchor_found) {
            RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[ANCHOR] Anchor ID %u not found in FINAL (num_responses=%u)", anchor_id, final_msg->num_responses);
            s_ranging_busy = false;
            return -1;
        }

        /* Calculate distance */
        dstwr_timestamps_t ts = {
            .t1 = final_msg->poll_tx_ts,
            .t2 = poll_rx_ts,
            .t3 = t3_timestamp,
            .t4 = resp_rx_ts_tag,
            .t5 = final_tx_ts_tag,
            .t6 = final_rx_ts
        };

        s_ctx.result_single.distance_m = calculate_distance(&ts);
        s_ctx.result_single.anchor_id = anchor_id;
        s_ctx.result_single.rssi = (int8_t)resp_msg.rssi_poll;
        s_ctx.result_single.valid = (s_ctx.result_single.distance_m > 0.0f && 
                                    s_ctx.result_single.distance_m < 100.0f);
        s_ctx.result_single.t1 = ts.t1;
        s_ctx.result_single.t2 = ts.t2;
        s_ctx.result_single.t3 = ts.t3;
        s_ctx.result_single.t4 = ts.t4;
        s_ctx.result_single.t5 = ts.t5;
        s_ctx.result_single.t6 = ts.t6;

    /* 4. Send RESULT message to TAG */
    result_msg_t result_msg = {0};
    result_msg.msg_type = MW_DSTWR_MSG_TYPE_RESULT;
    result_msg.sequence_num = final_msg->sequence_num;
    result_msg.anchor_id = anchor_id;
    result_msg.valid = s_ctx.result_single.valid ? 1 : 0;
    result_msg.distance_m = s_ctx.result_single.distance_m;
    result_msg.rssi = s_ctx.result_single.rssi;

    /* Calculate RESULT TX time: FINAL_RX + small delay */
    uint32_t result_offset_us = 2000 + (my_slot_id * 1000); /* 2ms base + 1ms per slot */
    uint64_t result_tx_time_dw = final_rx_ts + tdma_us_to_dw(result_offset_us);
    result_tx_time_dw = ensure_future_tx(result_tx_time_dw, TDMA_DEFAULT_GUARD_TIME_US);

    if (bsp_uwb_tx_delayed(&result_msg, sizeof(result_msg), result_tx_time_dw) != BSP_OK) {
        RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[ANCHOR] Failed to TX RESULT");
        /* Don't fail - distance already calculated */
    } else {
        RLOG_I(LOG_OBJECT_CODE_RANGING, "[ANCHOR] RESULT sent: %.3fm", result_msg.distance_m);
    }

    log_ranging_result(&s_ctx.result_single, "ANCHOR");
    s_ranging_busy = false;
    return 0;
}

/* ====================================================================
 * DS-TWR TAG IMPLEMENTATION - FIXED FOR TDMA
 * ==================================================================== */

    static int ds_twr_tag_tdma(uint8_t num_anchors, const uint8_t *anchor_ids,
                            uint8_t sequence_num, uint32_t rx_timeout_us)
    {
        if (s_ranging_busy) return -1;
        s_ranging_busy = true;

        /* Init scheduler only once - reuse for all ranging cycles */
        if (!s_tdma_tag.initialized) {
            if (tdma_init(&s_tdma_tag, TDMA_ROLE_TAG, 0, num_anchors, anchor_ids) != TDMA_OK) {
                s_ranging_busy = false;
                return -1;
            }
            uint32_t slot_duration = 4000;
            if (tdma_set_timing(&s_tdma_tag, slot_duration, TDMA_DEFAULT_GUARD_TIME_US,
                                TDMA_POLL_TO_RESP_US,
                                TDMA_RESP_TO_FINAL_US) != TDMA_OK) {
                s_ranging_busy = false;
                return -1;
            }
        }
        
        tdma_scheduler_t *tdma = &s_tdma_tag;

        /* 1. Send POLL - TX ONLY ONCE */
        poll_msg_t poll_msg = {0};
        poll_msg.msg_type = MW_DSTWR_MSG_TYPE_POLL;
        poll_msg.sequence_num = sequence_num;
        poll_msg.tag_id = 0;
        poll_msg.num_anchors = num_anchors;
        /* poll_tx_ts NOT in payload - anchor doesn't need it */

        for (uint8_t i = 0; i < num_anchors; i++) {
            if (anchor_ids[i] > 0 && anchor_ids[i] <= 8) {
                poll_msg.anchor_mask |= (1 << (anchor_ids[i] - 1));
            }
        }

        /* TX POLL (broadcast - immediate TX) */
        if (bsp_uwb_tx(&poll_msg, sizeof(poll_msg)) != BSP_OK) {
            RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[TAG] Failed to TX POLL");
            s_ranging_busy = false;
            return -1;
        }

        /* Read T1 for internal use (TDMA sync + FINAL) */
        uint64_t poll_tx_ts = 0;
        if (bsp_uwb_read_40bit(0x17, 0x00, &poll_tx_ts) != BSP_OK) {
            RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[TAG] Failed to read T1 timestamp");
            s_ranging_busy = false;
            return -1;
        }

        if (bsp_uwb_enable_rx(0) != BSP_OK) {
            RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[TAG] Failed to enable RX");
            s_ranging_busy = false;
            return -1;
        }

        /* NOW sync superframe (RX is already on) */
        tdma_start_superframe(tdma, poll_tx_ts);

        /* Log AFTER RX is enabled */
        RLOG_I(LOG_OBJECT_CODE_RANGING, "[TAG] POLL sent (seq=%u, num_anchors=%u, mask=0x%02X)", 
            sequence_num, num_anchors, poll_msg.anchor_mask);

        /* 2. Receive responses from anchors */
        uint8_t response_buf[128];
        uint16_t response_len;
        uint8_t num_responses = 0;

        struct {
            uint8_t anchor_id;
            uint64_t resp_rx_ts;
            uint64_t poll_rx_ts;
            uint64_t resp_tx_ts;
            int8_t rssi;
            bool valid;
        } anchor_resp[8];
        memset(anchor_resp, 0, sizeof(anchor_resp));

        RLOG_I(LOG_OBJECT_CODE_RANGING, "[TAG] Waiting for RESP (timeout=%lums)...", rx_timeout_us/1000);

        /* SIMPLIFIED: RX continuously from POLL to FINAL
        * DW1000 RX power << debug time
        * Use slot_id in RESP payload to classify anchors
        */
        uint32_t rx_duration_us = 5000 + (num_anchors * 4000) + 2000; /* base + slots + margin */
        uint64_t rx_start_dw = bsp_uwb_get_current_time_dw();
        uint64_t rx_end_dw = rx_start_dw + tdma_us_to_dw(rx_duration_us);

        for (uint8_t i = 0; i < num_anchors; i++) {
            uint8_t anchor_id = anchor_ids[i];
            
            /* Poll for RESP continuously until end of RX window */
            while (bsp_uwb_get_current_time_dw() < rx_end_dw) {
                bsp_err_t err = bsp_uwb_rx(response_buf, sizeof(response_buf), &response_len);

                if (err == BSP_OK && response_len > 0 && validate_msg_type(response_buf, response_len, MW_DSTWR_MSG_TYPE_RESP)) {
                    resp_msg_t *resp = (resp_msg_t *)response_buf;

                    if (resp->sequence_num != sequence_num || resp->anchor_id != anchor_id) continue;

                    /* Read T4 */
                    uint64_t resp_rx_ts = 0;
                    if (bsp_uwb_read_40bit(0x15, 0x00, &resp_rx_ts) != BSP_OK) continue;

                    anchor_resp[i].anchor_id = anchor_id;
                    anchor_resp[i].resp_rx_ts = resp_rx_ts;
                    anchor_resp[i].poll_rx_ts = resp->poll_rx_ts;
                    anchor_resp[i].resp_tx_ts = resp->resp_tx_ts;
                    anchor_resp[i].rssi = (int8_t)resp->rssi_poll;
                    anchor_resp[i].valid = true;

                    num_responses++;
                    RLOG_I(LOG_OBJECT_CODE_RANGING, "[TAG] Got RESP from anchor %u", anchor_id);
                    break;
                }
                __NOP();
            }
        }

        if (num_responses == 0) {
            RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[TAG] No RESP received from %u anchors", num_anchors);
            s_ranging_busy = false;
            return -1;
        }
        
        RLOG_I(LOG_OBJECT_CODE_RANGING, "[TAG] Received %u RESP messages", num_responses);

        /* 3. Send FINAL (FIXED: use TDMA scheduler to maintain sync) */
        uint64_t final_tx_time_dw;
        if (tdma_calculate_final_time(tdma, num_responses, &final_tx_time_dw) != TDMA_OK) {
            RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[TAG] Failed to calculate FINAL time");
            s_ranging_busy = false;
            return -1;
        }
        final_tx_time_dw = ensure_future_tx(final_tx_time_dw, TDMA_DEFAULT_GUARD_TIME_US);

        uint16_t tx_ant_dly = bsp_uwb_get_tx_antenna_delay();
        uint64_t t5_payload = (final_tx_time_dw + tx_ant_dly) & 0x000000FFFFFFFFFFULL;

        uint8_t final_buf[256];
        final_msg_t *final_msg = (final_msg_t *)final_buf;
        memset(final_msg, 0, sizeof(final_msg_t));

        final_msg->msg_type = MW_DSTWR_MSG_TYPE_FINAL;
        final_msg->sequence_num = sequence_num;
        final_msg->tag_id = 0;
        final_msg->num_responses = num_responses;
        final_msg->poll_tx_ts = poll_tx_ts;

        final_anchor_data_t *anchor_final = (final_anchor_data_t *)(final_buf + sizeof(final_msg_t));

        uint8_t final_idx = 0;
        for (uint8_t i = 0; i < 8; i++) {
            if (anchor_resp[i].valid) {
                anchor_final[final_idx].anchor_id = anchor_resp[i].anchor_id;
                anchor_final[final_idx].resp_rx_ts = anchor_resp[i].resp_rx_ts;
                anchor_final[final_idx].final_tx_ts = t5_payload;
                final_idx++;
            }
        }

        uint16_t final_len = sizeof(final_msg_t) + (num_responses * sizeof(final_anchor_data_t));

        if (bsp_uwb_tx_delayed(final_buf, final_len, final_tx_time_dw) != BSP_OK) {
            s_ranging_busy = false;
            return -1;
        }

        /* FIXED: Read T6 (actual FINAL TX timestamp) */
        uint64_t final_tx_ts = 0;
        if (bsp_uwb_read_40bit(0x17, 0x00, &final_tx_ts) != BSP_OK) {
            RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[TAG] Failed to read T6 timestamp");
            s_ranging_busy = false;
            return -1;
        }
        uint64_t t6_actual = (final_tx_ts + tx_ant_dly) & 0x000000FFFFFFFFFFULL;

    /* 4. Receive RESULT messages from anchors */
    /* TAG does NOT calculate distance - wait for RESULT from each anchor */
    if (bsp_uwb_enable_rx(0) != BSP_OK) {
        RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[TAG] Failed to enable RX for RESULT");
        s_ranging_busy = false;
        return -1;
    }

    uint8_t result_buf[128];
    uint16_t result_len;
    uint32_t result_timeout_us = 10000 + (num_anchors * 2000); /* 10ms base + 2ms per anchor */
    uint64_t result_start_dw = bsp_uwb_get_current_time_dw();
    uint64_t result_timeout_dw = tdma_us_to_dw(result_timeout_us);

    s_ctx.result_multi.count = 0;
    s_ctx.result_multi.sequence_num = sequence_num;

    /* Wait for RESULT from each anchor */
    uint8_t results_received = 0;
    while (results_received < num_responses && 
           (bsp_uwb_get_current_time_dw() - result_start_dw < result_timeout_dw)) {
        
        bsp_err_t err = bsp_uwb_rx(result_buf, sizeof(result_buf), &result_len);
        if (err == BSP_OK && result_len > 0 && 
            validate_msg_type(result_buf, result_len, MW_DSTWR_MSG_TYPE_RESULT)) {
            
            result_msg_t *result = (result_msg_t *)result_buf;
            
            if (result->sequence_num != sequence_num) continue;
            
            /* Find matching anchor in our response list */
            for (uint8_t i = 0; i < 8; i++) {
                if (anchor_resp[i].valid && anchor_resp[i].anchor_id == result->anchor_id) {
                    /* Store result from anchor */
                    sys_ranging_result_t *tag_result = &s_ctx.result_multi.results[s_ctx.result_multi.count];
                    tag_result->anchor_id = result->anchor_id;
                    tag_result->distance_m = result->distance_m;
                    tag_result->rssi = result->rssi;
                    tag_result->valid = (result->valid == 1);
                    
                    /* Store timestamps for reference */
                    tag_result->t1 = poll_tx_ts;
                    tag_result->t2 = anchor_resp[i].poll_rx_ts;
                    tag_result->t3 = anchor_resp[i].resp_tx_ts;
                    tag_result->t4 = anchor_resp[i].resp_rx_ts;
                    tag_result->t5 = t5_payload;
                    tag_result->t6 = t6_actual; /* TAG's FINAL TX, not anchor's RX */
                    
                    s_ctx.result_multi.count++;
                    results_received++;
                    
                    RLOG_I(LOG_OBJECT_CODE_RANGING, "[TAG] Got RESULT from anchor %u: %.3fm", 
                           result->anchor_id, result->distance_m);
                    break;
                }
            }
        }
        __NOP();
    }

    if (s_ctx.result_multi.count == 0) {
        RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, 
               "[TAG] No RESULT received from %u anchors", num_anchors);
        s_ranging_busy = false;
        return -1;
    }

    RLOG_I(LOG_OBJECT_CODE_RANGING, "[TAG] Received %u RESULT messages", s_ctx.result_multi.count);
        s_ctx.sequence_num = sequence_num;
        s_ctx.state = STATE_TAG_RANGING_TDMA;
        s_ctx.state_entry_tick = HAL_GetTick();
        s_stats.total_count++;

        return SYS_RANGING_OK;
    }

    sys_ranging_err_t sys_ranging_tag_process_tdma(uint8_t num_anchors,
                                                const uint8_t *anchor_ids,
                                                uint32_t rx_timeout_ms)
    {
        if (s_ctx.state == STATE_IDLE) return SYS_RANGING_ERR_NOT_STARTED;
        if (s_ctx.state != STATE_TAG_RANGING_TDMA) return SYS_RANGING_ERR;

        uint32_t timeout_ms = (rx_timeout_ms == 0) ? 100 : rx_timeout_ms;
        if (HAL_GetTick() - s_ctx.state_entry_tick > timeout_ms) {
            state_machine_reset();
            return SYS_RANGING_ERR_TIMEOUT;
        }

        int ret = ds_twr_tag_tdma(num_anchors, anchor_ids, s_ctx.sequence_num, rx_timeout_ms * 1000);

        if (ret == 0) {
            s_ctx.has_result = true;
            s_ctx.state = STATE_TAG_COMPLETE;
            for (uint8_t i = 0; i < s_ctx.result_multi.count; i++) {
                log_ranging_result(&s_ctx.result_multi.results[i], "TAG");
            }
            return SYS_RANGING_OK;
        } else {
            RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[TAG] TDMA Error");
            s_stats.error_count++;
            state_machine_reset();
            return SYS_RANGING_ERR;
        }
    }

    sys_ranging_err_t sys_ranging_tag_get_results_tdma(sys_ranging_multi_result_t *results)
    {
        if (!results) return SYS_RANGING_ERR_PARAM;
        if (s_ctx.state != STATE_TAG_COMPLETE || !s_ctx.has_result) return SYS_RANGING_ERR_NO_RESULT;

        memcpy(results, &s_ctx.result_multi, sizeof(sys_ranging_multi_result_t));
        state_machine_reset();
        return SYS_RANGING_OK;
    }

    sys_ranging_err_t sys_ranging_tag_run_tdma_blocking(uint8_t num_anchors,
                                                        const uint8_t *anchor_ids,
                                                        uint8_t sequence_num,
                                                        uint32_t rx_timeout_ms)
    {
        if (num_anchors == 0 || num_anchors > 8 || !anchor_ids) return SYS_RANGING_ERR_PARAM;
        
        int ret = ds_twr_tag_tdma(num_anchors, anchor_ids, sequence_num, rx_timeout_ms * 1000);
        
        return (ret == 0) ? SYS_RANGING_OK : SYS_RANGING_ERR;
    }

    sys_ranging_err_t sys_ranging_anchor_run_tdma_blocking(uint8_t anchor_id,
                                                        uint8_t num_anchors,
                                                        const uint8_t *anchor_ids,
                                                        uint32_t rx_timeout_ms)
    {
        if (anchor_id == 0 || anchor_id > 8) return SYS_RANGING_ERR_PARAM;
        if (num_anchors == 0 || num_anchors > 8 || !anchor_ids) return SYS_RANGING_ERR_PARAM;
        
        int ret = ds_twr_anchor_tdma(anchor_id, num_anchors, anchor_ids, rx_timeout_ms * 1000);
        
        if (ret == 0) {
            /* Success - result is in s_ctx.result_single */
            return SYS_RANGING_OK;
        } else {
            return SYS_RANGING_ERR;
        }
    }

    sys_ranging_err_t sys_ranging_anchor_get_last_result(sys_ranging_result_t *result)
    {
        if (!result) return SYS_RANGING_ERR_PARAM;
        if (!s_ctx.result_single.valid) return SYS_RANGING_ERR_NO_RESULT;
        
        memcpy(result, &s_ctx.result_single, sizeof(sys_ranging_result_t));
        return SYS_RANGING_OK;
    }

    sys_ranging_err_t sys_ranging_anchor_start_tdma(uint8_t anchor_id,
                                                    uint8_t num_anchors,
                                                    const uint8_t *anchor_ids,
                                                    uint32_t rx_timeout_ms)
    {
        if (s_ctx.state != STATE_IDLE) return SYS_RANGING_ERR_BUSY;
        if (anchor_id == 0 || anchor_id > 8) return SYS_RANGING_ERR_PARAM;
        if (num_anchors == 0 || num_anchors > 8 || !anchor_ids) return SYS_RANGING_ERR_PARAM;

        state_machine_reset();
        s_ctx.anchor_id = anchor_id;
        s_ctx.state = STATE_ANCHOR_RANGING_TDMA;
        s_ctx.state_entry_tick = HAL_GetTick();
        s_stats.total_count++;

        return SYS_RANGING_OK;
    }

    sys_ranging_err_t sys_ranging_anchor_process_tdma(uint8_t num_anchors,
                                                    const uint8_t *anchor_ids,
                                                    uint32_t rx_timeout_ms)
    {
        if (s_ctx.state == STATE_IDLE) return SYS_RANGING_ERR_NOT_STARTED;
        if (s_ctx.state != STATE_ANCHOR_RANGING_TDMA) return SYS_RANGING_ERR;

        uint32_t timeout_ms = (rx_timeout_ms == 0) ? 100 : rx_timeout_ms;
        if (HAL_GetTick() - s_ctx.state_entry_tick > timeout_ms) {
            state_machine_reset();
            return SYS_RANGING_ERR_TIMEOUT;
        }

        int ret = ds_twr_anchor_tdma(s_ctx.anchor_id, num_anchors, anchor_ids, rx_timeout_ms * 1000);

        if (ret == 0) {
            s_ctx.has_result = true;
            s_ctx.state = STATE_ANCHOR_COMPLETE;
            log_ranging_result(&s_ctx.result_single, "ANCHOR");
            return SYS_RANGING_OK;
        } else {
            RLOG_E(LOG_OBJECT_CODE_RANGING, ERR_UWB_RANGING, "[ANCHOR] TDMA Error");
            s_stats.error_count++;
            state_machine_reset();
            return SYS_RANGING_ERR;
        }
    }

    sys_ranging_err_t sys_ranging_anchor_get_result_tdma(sys_ranging_result_t *result)
    {
        if (!result) return SYS_RANGING_ERR_PARAM;
        if (s_ctx.state != STATE_ANCHOR_COMPLETE || !s_ctx.has_result) return SYS_RANGING_ERR_NO_RESULT;

        memcpy(result, &s_ctx.result_single, sizeof(sys_ranging_result_t));
        state_machine_reset();
        return SYS_RANGING_OK;
    }


    void sys_ranging_reset_stats(void)
    {
        s_stats.total_count = 0;
        s_stats.success_count = 0;
        s_stats.error_count = 0;
    }

