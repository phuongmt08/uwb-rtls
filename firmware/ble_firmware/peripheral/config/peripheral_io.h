/**
 * Custom board definition for FSC-BT630 (nRF52832)
 * Based on schematic: BLE module with UART interface + status LED
 */

#ifndef FSC_BT630_H
#define FSC_BT630_H

#ifdef __cplusplus
extern "C" {
#endif

#include "nrf_gpio.h"

// ─── LEDs ────────────────────────────────────────────────────────────────────
// D8 GREEN LED via R19 (2.2k) connected to P0.03/AIN1 (module pin 12)
#define LEDS_NUMBER     1

#define LED1_G          NRF_GPIO_PIN_MAP(0, 3)   // P0.03 - Green LED D8

#define LED_1           LED1_G

#define LEDS_ACTIVE_STATE 1                       // Active HIGH (common anode to VCC)

#define LEDS_LIST       { LED_1 }
#define LEDS_INV_MASK   LEDS_MASK

#define BSP_LED_0       LED_1

// ─── Buttons ─────────────────────────────────────────────────────────────────
// No user button in schematic
#define BUTTONS_NUMBER  0
#define BUTTONS_LIST    { }

// ─── UART (connect to gateway nRF52840 via BLE_TX/BLE_RX) ───────────────────────
// FSC-BT630 default UART pins (nRF52832 internal mapping)
// Bỏ comment dòng dưới đây nếu nạp cho board lỗi (câu dây chân P0.05 vào BLE_RX)
// #define BLE_RX_BYPASS_P005

#ifdef BLE_RX_BYPASS_P005
#define RX_PIN_NUMBER   NRF_GPIO_PIN_MAP(0, 5)   // Map P0.05 → UART_RX (BLE_RX) do hỏng chân P0.08
#define RTS_PIN_NUMBER  0xFFFFFFFF               // Ngắt cấu hình RTS để tránh xung đột trên chân P0.05
#else
#define RX_PIN_NUMBER   NRF_GPIO_PIN_MAP(0, 8)   // P0.08 → UART_RX (BLE_RX from gateway)
#define RTS_PIN_NUMBER  NRF_GPIO_PIN_MAP(0, 5)   // P0.05 → RTS
#endif
#define TX_PIN_NUMBER   NRF_GPIO_PIN_MAP(0, 6)   // P0.06 → UART_TX (BLE_TX to gateway)
#define CTS_PIN_NUMBER  NRF_GPIO_PIN_MAP(0, 7)   // P0.07 → CTS
#define HWFC            true

// ─── SWD (debug interface) ────────────────────────────────────────────────────
// BLE_SWDIO → pin 8 (SWDIO), BLE_SWDCLK → pin 7 (SWDCLK)
// BLE_RST   → pin 6 (RESET)

// ─── GPIO not available on module ────────────────────────────────────────────────
// Pin 11: P0.02/AIN0  → free
// Pin 12: P0.03/AIN1  → LED D8
// Pin 13: P0.04/AIN2  → free
// Pin 14: P0.05/AIN3  → RTS
// Pin 15: P0.06       → UART_TX
// Pin 16: P0.07       → CTS
// Pin 17: P0.08       → UART_RX
// Pin 18: P0.09/NFC1  → free (avoid if using NFC)
// Pin 19: P0.10/NFC2  → free (avoid if using NFC)
// Pin 20: P0.11       → free

#ifdef __cplusplus
}
#endif

#endif // FSC_BT630_H