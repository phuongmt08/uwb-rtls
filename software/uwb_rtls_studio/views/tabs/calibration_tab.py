"""
UWB RTLS Studio — Calibration Tab (UI loaded from .ui file)
Tab 4: Antenna delay tuning + Calibration status (Developer only).

FE: Loaded from views/ui/calibration_tab.ui (editable in Qt Designer)
BE: Calibration logic + ViewModel bindings (this file)
"""
import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QGridLayout, QPushButton, QSpinBox, QDoubleSpinBox,
    QProgressBar, QFrame, QCheckBox, QScrollArea
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6 import uic

# Path to .ui file
UI_FILE = os.path.join(os.path.dirname(__file__), '..', 'ui', 'calibration_tab.ui')


class CalibrationTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        # ── Load UI from .ui file ──
        uic.loadUi(UI_FILE, self)

        # Widgets are now accessible via objectNames:
        # self.calib_progress  → QProgressBar
        # self.calib_status    → QLabel "Status: Idle"
        # self.calib_iter      → QLabel "Iteration: 0 / 20"
        # self.btn_start_calib → QPushButton
        # self.btn_stop_calib  → QPushButton
        # self.btn_apply_calib → QPushButton
        # self.ref_dist_spin, self.samples_spin, etc.
