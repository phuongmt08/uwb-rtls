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


def set_widget_placeholder(widget):
    """Set the widget to a placeholder '-' state indicating no data has been received yet."""
    from PyQt6.QtWidgets import QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox
    from PyQt6.QtCore import Qt

    # Save and block signals to avoid triggering valueChanged or textChanged slots
    was_blocked = widget.signalsBlocked()
    widget.blockSignals(True)
    try:
        if isinstance(widget, QLineEdit):
            widget.setText("-")
            widget.setEnabled(False)
        elif isinstance(widget, QComboBox):
            # Check if "-" is in the list
            if widget.findText("-") == -1:
                widget.insertItem(0, "-")
            widget.setCurrentIndex(widget.findText("-"))
            widget.setEnabled(False)
        elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            if not hasattr(widget, "_orig_min"):
                widget._orig_min = widget.minimum()
                widget._orig_max = widget.maximum()
            widget.setSpecialValueText("-")
            # Set minimum to min - 1 to allow the special value text to trigger
            widget.setMinimum(widget._orig_min - 1)
            widget.setValue(widget._orig_min - 1)
            widget.setEnabled(False)
        elif isinstance(widget, QCheckBox):
            widget.setTristate(True)
            widget.setCheckState(Qt.CheckState.PartiallyChecked)
            widget.setEnabled(False)
    finally:
        widget.blockSignals(was_blocked)


def set_widget_value(widget, value):
    """Restore the widget's normal state and set its value from the received data."""
    from PyQt6.QtWidgets import QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox

    if value is None or value in ("-", "--"):
        set_widget_placeholder(widget)
        return

    was_blocked = widget.signalsBlocked()
    widget.blockSignals(True)
    try:
        widget.setEnabled(True)
        if isinstance(widget, QLineEdit):
            widget.setText(str(value))
        elif isinstance(widget, QComboBox):
            # If "-" is in the list, remove it
            dash_idx = widget.findText("-")
            idx = widget.findText(str(value))
            if idx != -1:
                widget.setCurrentIndex(idx)
            else:
                widget.setCurrentText(str(value))
            if dash_idx != -1 and widget.currentIndex() != dash_idx:
                widget.removeItem(dash_idx)
        elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            if hasattr(widget, "_orig_min"):
                widget.setMinimum(widget._orig_min)
            # Try to convert value to type of spinbox
            try:
                if isinstance(widget, QSpinBox):
                    widget.setValue(int(float(value)))
                else:
                    widget.setValue(float(value))
            except (ValueError, TypeError):
                pass
        elif isinstance(widget, QCheckBox):
            widget.setTristate(False)
            widget.setChecked(bool(value))
    finally:
        widget.blockSignals(was_blocked)

