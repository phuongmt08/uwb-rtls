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
# Detect bằng protobuf probe (gửi device_information_get, chờ ACK)
# Không dùng VID/PID — hoàn toàn dựa trên firmware response
DONGLE_DETECT_TIMEOUT_S = 30    # Timeout cho toàn bộ quá trình detect
PROBE_READ_TIMEOUT_S = 0.05     # Serial read timeout cho mỗi lần đọc
PROBE_RESPONSE_TIMEOUT_S = 0.5  # Thời gian chờ response mỗi lần probe
MAX_PROBE_RETRIES = 3           # Số lần retry probe mỗi port

# ── Serial Communication ──────────────────────────────────────────
SERIAL_BAUD_RATE = 115200
SERIAL_READ_TIMEOUT_S = 0.1     # Non-blocking read timeout
SERIAL_WRITE_TIMEOUT_S = 1.0

# ── Protocol Addresses ────────────────────────────────────────────
# Sync voi protocol.proto device_addr_t
ADDR_NONE        = 0x0                 # PACKET_ADDR_UNSPECIFIED
ADDR_MCU         = 0x1                 # PACKET_ADDR_MCU
ADDR_CENTRAL     = 0x3                 # PACKET_ADDR_CENTRAL (dongle)
ADDR_PERIPHERAL  = 0x4                 # PACKET_ADDR_PERIPHERAL (tag/anchor)
ADDR_HOST        = 0x5                 # PACKET_ADDR_HOST
ADDR_DEBUG       = 0x7                 # PACKET_ADDR_DEBUG
ADDR_BCAST       = 0xF                 # PACKET_ADDR_BCAST
ADDR_VEHICLE     = 0x10                # PACKET_ADDR_VEHICLE
ADDR_BCAS        = ADDR_BCAST          # Backward-compatible alias for old typo

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

# ── Device & Ranging Configs ──────────────────────────────────────
DEVICE_TIMEOUT_S = 5.0              # Timeout for stale advertising devices (seconds)
STOP_TO_CONNECT_DELAY_MS = 400      # Delay after ble_scan_stop before sending ble_connect (ms)
TIME_SYNC_THRESHOLD_MS = 5000       # Time sync verification tolerance (ms)

# Device Role/Type Labels
DEVICE_TYPE_LABELS = {
    0: "UNSPECIFIED",
    1: "TAG",
    2: "ANCHOR",
    3: "GATEWAY",
    4: "DEBUG_TOOL",
}

DEVICE_TYPE_LABELS_SHORT = {
    0: "-",
    1: "TAG",
    2: "ANCHOR",
    3: "GATEWAY",
    4: "DEBUG",
}
