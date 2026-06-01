"""
UWB RTLS Studio — Calibration Tab (Frontend Only)
Tab 4: Antenna delay tuning + Calibration status (Developer only).
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QGridLayout, QPushButton, QSpinBox, QDoubleSpinBox,
    QProgressBar, QFrame, QCheckBox, QScrollArea
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont


class CalibrationTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        main = QHBoxLayout(content)
        main.setSpacing(16)
        main.setContentsMargins(16, 16, 16, 16)

        # ═══ LEFT: Calibration Setup ═══
        left = QVBoxLayout()
        left.setSpacing(14)

        # Antenna Delay Calibration
        delay_group = QGroupBox("🔧 Antenna Delay Calibration")
        delay_grid = QGridLayout(delay_group)
        delay_grid.setSpacing(10)

        delay_grid.addWidget(QLabel("Reference Distance (m):"), 0, 0)
        ref_dist = QDoubleSpinBox()
        ref_dist.setRange(0.1, 100.0)
        ref_dist.setValue(1.0)
        ref_dist.setDecimals(3)
        delay_grid.addWidget(ref_dist, 0, 1)

        delay_grid.addWidget(QLabel("Num Samples:"), 1, 0)
        samples = QSpinBox()
        samples.setRange(10, 10000)
        samples.setValue(1000)
        delay_grid.addWidget(samples, 1, 1)

        delay_grid.addWidget(QLabel("Current TX Delay:"), 2, 0)
        tx_delay = QSpinBox()
        tx_delay.setRange(0, 65535)
        tx_delay.setValue(16436)
        delay_grid.addWidget(tx_delay, 2, 1)

        delay_grid.addWidget(QLabel("Current RX Delay:"), 3, 0)
        rx_delay = QSpinBox()
        rx_delay.setRange(0, 65535)
        rx_delay.setValue(16436)
        delay_grid.addWidget(rx_delay, 3, 1)

        delay_grid.addWidget(QLabel("Damping Factor:"), 4, 0)
        damping = QDoubleSpinBox()
        damping.setRange(0.0, 1.0)
        damping.setValue(0.5)
        damping.setDecimals(3)
        delay_grid.addWidget(damping, 4, 1)

        delay_grid.addWidget(QLabel("Max Iterations:"), 5, 0)
        iterations = QSpinBox()
        iterations.setRange(1, 100)
        iterations.setValue(20)
        delay_grid.addWidget(iterations, 5, 1)

        calib_btns = QHBoxLayout()
        btn_start_calib = QPushButton("▶ Start Calibration")
        btn_start_calib.setFixedHeight(38)
        btn_start_calib.setStyleSheet("""
            QPushButton { background: #059669; color: #F8FAFC; border: 1px solid #10B981;
                border-radius: 8px; font-weight: bold; }
            QPushButton:hover { background: #10B981; }
        """)
        btn_stop_calib = QPushButton("■ Stop")
        btn_stop_calib.setFixedHeight(38)
        btn_stop_calib.setStyleSheet("""
            QPushButton { background: rgba(239,68,68,0.15); color: #EF4444;
                border: 1px solid #EF4444; border-radius: 8px; font-weight: bold; }
            QPushButton:hover { background: #EF4444; color: #F8FAFC; }
        """)
        calib_btns.addWidget(btn_start_calib)
        calib_btns.addWidget(btn_stop_calib)
        delay_grid.addLayout(calib_btns, 6, 0, 1, 2)
        left.addWidget(delay_group)

        # Calibration Options
        opts_group = QGroupBox("⚙ Calibration Options")
        opts_layout = QVBoxLayout(opts_group)
        opts_layout.addWidget(QCheckBox("Enable Height Calibration"))
        opts_layout.addWidget(QCheckBox("Auto-apply delay after calibration"))
        opts_layout.addWidget(QCheckBox("Save calibration to flash"))
        left.addWidget(opts_group)

        left.addStretch()
        main.addLayout(left, 1)

        # ═══ RIGHT: Calibration Results ═══
        right = QVBoxLayout()
        right.setSpacing(14)

        # Progress
        prog_group = QGroupBox("📊 Calibration Progress")
        prog_layout = QVBoxLayout(prog_group)

        self._calib_progress = QProgressBar()
        self._calib_progress.setRange(0, 100)
        self._calib_progress.setValue(0)
        self._calib_progress.setFixedHeight(20)
        prog_layout.addWidget(self._calib_progress)

        self._calib_status = QLabel("Status: Idle")
        self._calib_status.setStyleSheet("color: #94A3B8; font-size: 13px;")
        prog_layout.addWidget(self._calib_status)

        self._calib_iter = QLabel("Iteration: 0 / 20")
        self._calib_iter.setStyleSheet("color: #94A3B8;")
        prog_layout.addWidget(self._calib_iter)
        right.addWidget(prog_group)

        # Diagnostics Results
        diag_group = QGroupBox("📈 Diagnostics")
        diag_grid = QGridLayout(diag_group)
        diag_grid.setSpacing(10)

        diag_data = [
            ("Error Mean:", "0.000 m"),
            ("Error Std:", "0.000 m"),
            ("Error RMS:", "0.000 m"),
            ("Error Min:", "0.000 m"),
            ("Error Max:", "0.000 m"),
            ("Optimized TX Delay:", "—"),
            ("Optimized RX Delay:", "—"),
        ]
        for i, (label, value) in enumerate(diag_data):
            lbl = QLabel(label)
            lbl.setStyleSheet("color: #94A3B8; font-weight: bold;")
            val = QLabel(value)
            val.setStyleSheet("color: #F8FAFC; font-size: 14px;")
            diag_grid.addWidget(lbl, i, 0)
            diag_grid.addWidget(val, i, 1)
        right.addWidget(diag_group)

        # Apply button
        btn_apply = QPushButton("✅ Apply Calibration Results")
        btn_apply.setFixedHeight(42)
        btn_apply.setStyleSheet("""
            QPushButton { background: #0E7490; color: #F8FAFC; border: 1px solid #22D3EE;
                border-radius: 8px; font-weight: bold; font-size: 14px; }
            QPushButton:hover { background: #22D3EE; color: #0F172A; }
        """)
        right.addWidget(btn_apply)

        right.addStretch()
        main.addLayout(right, 1)

        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
