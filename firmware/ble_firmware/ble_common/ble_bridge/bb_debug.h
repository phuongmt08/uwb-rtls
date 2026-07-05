#ifndef BB_DEBUG_H
#define BB_DEBUG_H

/*
 * Set DEBUG_STREAM_MCU_PERI to 1 to suppress extra logs while debugging the
 * MCU -> peripheral stream. Default keeps the existing logs enabled.
 */
#ifndef DEBUG_STREAM_MCU_PERI
#define DEBUG_STREAM_MCU_PERI 1
#endif

/* Peripheral MCU RX statistics print interval. Set to 0 to disable. */
#ifndef DEBUG_STREAM_MCU_PERI_STATS_INTERVAL_MS
#define DEBUG_STREAM_MCU_PERI_STATS_INTERVAL_MS 5000
#endif

/* Enable detailed transport routing logs (BLE/UART RX/TX, cmd_id, src, dst) */
#ifndef BB_DEBUG_TRANSPORT_LOG_ENABLED
#define BB_DEBUG_TRANSPORT_LOG_ENABLED 0
#endif

#if defined(BLE_PERIPHERAL) && DEBUG_STREAM_MCU_PERI
#define BB_DEBUG_STREAM_MCU_PERI_ENABLED 1
#else
#define BB_DEBUG_STREAM_MCU_PERI_ENABLED 0
#endif

#if BB_DEBUG_STREAM_MCU_PERI_ENABLED
#define BB_DEBUG_LOG_INFO(...) do {} while (0)
#else
#define BB_DEBUG_LOG_INFO(...) NRF_LOG_INFO(__VA_ARGS__)
#endif

#endif /* BB_DEBUG_H */
