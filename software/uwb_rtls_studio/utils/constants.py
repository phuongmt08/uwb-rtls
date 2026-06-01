"""
===============================================================================
  UWB RTLS Studio — Constants
===============================================================================
  File        : utils/constants.py
  Description : App-wide constants. Tập trung tất cả magic numbers.

  Categories:
    - USB: VID/PID cho dongle detect
    - Serial: Baud rate, timeouts
    - Protocol: Addresses, frame types
    - UI: Window sizes, refresh rates
    - Polling: Intervals cho periodic polls
===============================================================================
"""

# ── USB Dongle Detection ───────────────────────────────────────────
# Nordic Semiconductor NRF52840 Dongle
DONGLE_VID = 0x1915             # Nordic Semiconductor ASA
DONGLE_PID = 0x520F             # NRF52840 USB CDC ACM (cần verify PID thực tế)
DONGLE_DETECT_TIMEOUT_S = 10    # Timeout cho auto-detect
DONGLE_DETECT_POLL_MS = 500     # Polling interval khi scan COM ports

# ── Serial Communication ──────────────────────────────────────────
SERIAL_BAUD_RATE = 115200
SERIAL_READ_TIMEOUT_S = 0.1     # Non-blocking read timeout
SERIAL_WRITE_TIMEOUT_S = 1.0

# ── Protocol Addresses ────────────────────────────────────────────
# Sync với protocol.proto device_addr_t
ADDR_HOST = 0x5                 # PACKET_ADDR_HOST
ADDR_CENTRAL = 0x3              # PACKET_ADDR_CENTRAL (dongle)
ADDR_PERIPHERAL = 0x4           # PACKET_ADDR_PERIPHERAL (tag/anchor)

# ── UI Settings ───────────────────────────────────────────────────
MAIN_WINDOW_MIN_WIDTH = 1200
MAIN_WINDOW_MIN_HEIGHT = 800
POPUP_WIDTH = 600
POPUP_HEIGHT = 500
CANVAS_FPS = 30                 # Position canvas refresh rate

# ── Polling Intervals (ms) ────────────────────────────────────────
POLL_BATTERY_MS = 30000         # 30s
POLL_BLE_STATUS_MS = 10000      # 10s
POLL_RANGING_STATUS_MS = 5000   # 5s (khi ranging active)
POLL_CALIB_STATUS_MS = 2000     # 2s (khi calibrating)

# ── Data Limits ───────────────────────────────────────────────────
MAX_POSITION_HISTORY = 100000     # Max trajectory samples
MAX_LOG_ENTRIES = 50000          # Max log entries in memory
MAX_SCAN_DEVICES = 10           # Max BLE devices in scan list
