/**
 * @file    app_rtos_handles.h
 * @brief   RTOS handle extern declarations shared across all modules.
 *          Definitions live in freertos.c (USER CODE Variables section).
 */
#ifndef APP_RTOS_HANDLES_H
#define APP_RTOS_HANDLES_H

#include "cmsis_os.h"
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
typedef struct {
    float   distances[3];  /**< Filtered 2D distances [m] to anchors 0-2      */
    uint8_t anchor_ids[3]; /**< Anchor IDs corresponding to each distance       */
    uint8_t count;         /**< Number of valid distance entries (usually 3)    */
} uwb_distance_msg_t;      /* sizeof = 16 bytes; queue item size = 16 */

/** UwbRanging → SensorFusion: 4 items × 16 bytes = 64 bytes RAM */
extern osMessageQueueId_t g_uwb_distance_queue;

/* ── Shared state ───────────────────────────────────────────────────────── */

/** Set false via IO task DOUBLE_CLICK; set true via IO task CLICK */
extern bool g_ranging_enabled;

#include "network/network_core.h"
#include "network/network_cmd.h"
extern network_core_t g_network_core;
extern network_cmd_t  g_network_cmd;
extern uint8_t        g_network_rx_buf[512];

#endif /* APP_RTOS_HANDLES_H */
