"""
===============================================================================
  UWB RTLS Studio — Log Text Widget Component
===============================================================================
  File        : views/components/log_text_widget.py
  Description : Custom QTextEdit cho hiển thị log entries.
                Color-coded theo log level, auto-scroll, monospace font.

  MVVM Role   : VIEW COMPONENT — reusable widget.

  Features:
    - Color-coded log levels:
        ERROR  → 🔴 Red
        WARN   → 🟡 Yellow/Orange
        INFO   → 🟢 Green
        DEBUG  → ⚪ Gray
    - Monospace font (Consolas / Courier New)
    - Auto-scroll to bottom (toggleable)
    - Max line limit (prevent memory bloat)
    - Copy selected text
    - Right-click context menu

  Public API:
    - append_log(timestamp, level, source, message)
    - clear()
    - set_auto_scroll(enabled: bool)
    - set_max_lines(n: int)
===============================================================================
"""
pass
