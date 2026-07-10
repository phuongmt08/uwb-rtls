"""
===============================================================================
  UWB RTLS Studio — Dongle Popup
===============================================================================
  File        : views/popups/dongle_popup.py
  Description : Modal popup hiển thị khi app khởi động.
                Detect dongle NRF52840 Central và connect.

  MVVM Role   : VIEW — pure UI.

  Popup Layout:
    ┌─────────────────────────────────────────────┐
    │         🔌 Detecting Dongle...              │
    │                                              │
    │    ┌──────────────────────────────────────┐  │
    │    │  [Spinner Animation]                 │  │
    │    │                                      │  │
    │    │  Scanning USB ports...               │  │
    │    └──────────────────────────────────────┘  │
    │                                              │
    │    Status: Looking for NRF52840 Central...   │
    │                                              │
    │    ─── Log Area ───────────────────────────  │
    │    [12:30:01] Scanning COM ports...          │
    │    [12:30:02] Found device on COM3           │
    │    [12:30:02] VID:0x1915 PID:0x520F          │
    │    [12:30:02] ✅ Detected Dongle Central     │
    │    [12:30:03] Connecting...                   │
    │    [12:30:03] ✅ Connected! FW: v1.2.1       │
    │    ────────────────────────────────────────── │
    │                                              │
    │          [Retry]        [Cancel]             │
    └─────────────────────────────────────────────┘

  State transitions:
    DETECTING → DETECTED → CONNECTING → CONNECTED → (auto-close, open ScanPopup)
    DETECTING → ERROR → (show error, enable Retry)

  Bindings:
    - DongleViewModel.dongle_detected   → update status text
    - DongleViewModel.dongle_connected  → auto close, emit proceed
    - DongleViewModel.dongle_error      → show error
    - Retry button → DongleViewModel.on_retry_detection()
    - Cancel button → close app
===============================================================================
"""
pass
