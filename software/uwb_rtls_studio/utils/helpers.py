"""
===============================================================================
  UWB RTLS Studio — Helpers
===============================================================================
  File        : utils/helpers.py
  Description : Utility functions dùng chung trong app.

  Functions:
    - format_coord(val) → format float to clean string
===============================================================================
"""

def format_coord(val) -> str:
    """Format coordinate to display as integer if it's a whole number, else decimal."""
    try:
        f_val = float(val)
        if f_val == int(f_val):
            return str(int(f_val))
        return f"{f_val:.2f}".rstrip('0').rstrip('.')
    except (ValueError, TypeError):
        return str(val)
