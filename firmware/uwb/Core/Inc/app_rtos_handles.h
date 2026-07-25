/**
 * @file    app_rtos_handles.h
 * @brief   RTOS handle extern declarations shared across all modules.
 *          Definitions live in freertos.c (USER CODE Variables section).
 */
#ifndef APP_RTOS_HANDLES_H
#define APP_RTOS_HANDLES_H

#include "cmsis_os.h"
#include "bsp_imu.h"
#include "protos/protocol.pb.h"
#include <stdbool.h>
#include <stdint.h>

/* ── Semaphores ───────────────────────────────────────────────────────────── */

/** Released by DW1000 ISR (uwb_tx_cb / uwb_rx_cb) → wakes UwbRanging task */
extern osSemaphoreId_t g_uwb_isr_semHandle;

/** Released by sys_logger_write_record() → wakes Logger task */
extern osSemaphoreId_t g_logger_semHandle;

/** Released by button EXTI ISR → wakes IO task */
extern osSemaphoreId_t g_io_btn_semHandle;

/* ── Mutexes ──────────────────────────────────────────────────────────────── */

/** Protects SPI1 bus shared by DW1000 and ICM42688 IMU */
extern osMutexId_t g_spi1_mutexHandle;

/** Protects sys_logger_write_record() — vsnprintf + circular buffer */
extern osMutexId_t g_logger_mutexHandle;

/* ── Message Queue ────────────────────────────────────────────────────────── */

/**
 * @brief Payload sent from UwbRanging → SensorFusion after each ranging cycle.
 */
typedef struct uwb_distance_msg {
    float   distances[8];     /**< Raw 3D distances [m] to anchors */
    uint8_t anchor_ids[8];    /**< Anchor IDs corresponding to each distance */
    float   fp_amp_norm[8];   /**< First path amplitude normalized */
    float   fp_snr[8];        /**< First path SNR */
    float   fp_confidence[8]; /**< Register-based DW1000 link confidence [0, 1] */
    uint8_t quality_valid[8]; /**< Non-zero when FP quality metrics are valid */
    uint8_t count;            /**< Number of valid distance entries */
    uint8_t mask;             /**< Active anchor mask (valid ranging) */
    uint32_t timestamp_ms;    /**< HAL_GetTick() timestamp at enqueue time */
    uint32_t ranging_error_count; /**< Cumulative ranging errors at enqueue time */
} uwb_distance_msg_t;

/** UwbRanging → SensorFusion queue, item size follows uwb_distance_msg_t. */
extern osMessageQueueId_t g_uwb_distance_queue;
extern osMessageQueueId_t g_imu_data_queue;

/* ── Shared state ───────────────────────────────────────────────────────── */

void app_rtos_set_ranging_enabled(bool enabled);
bool app_rtos_is_ranging_enabled(void);
bool app_rtos_apply_ranging_enabled(bool enabled);
void app_rtos_request_sensor_fusion_reset(void);
bool app_rtos_request_zone_switch(uint32_t zone_id);
bool app_rtos_request_active_zone_profile_update(const protobuf_zone_profile_t *profile);

#endif /* APP_RTOS_HANDLES_H */
