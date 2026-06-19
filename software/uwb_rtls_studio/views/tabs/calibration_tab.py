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
        self._vm = None

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
        self._setup_imu_controls()
        self.btn_start_calib.clicked.connect(self._start_calibration)
        self.btn_stop_calib.clicked.connect(self._stop_calibration)
        self.btn_apply_calib.clicked.connect(self._apply_calibration)
        self.btn_stop_calib.setEnabled(False)

    def _setup_imu_controls(self):
        row = QHBoxLayout()
        self.btn_imu_reset = QPushButton("Reset IMU")
        self.btn_imu_calibrate = QPushButton("Calibrate IMU")
        row.addWidget(self.btn_imu_reset)
        row.addWidget(self.btn_imu_calibrate)
        self.opts_layout.addLayout(row)
        self.btn_imu_reset.clicked.connect(self._reset_imu)
        self.btn_imu_calibrate.clicked.connect(self._calibrate_imu)

    def set_viewmodel(self, viewmodel):
        if self._vm is viewmodel:
            return
        self._vm = viewmodel
        self._vm.status_updated.connect(self._on_status_updated)
        self._vm.running_changed.connect(self._on_running_changed)
        self._vm.operation_failed.connect(self._on_operation_failed)
        QTimer.singleShot(0, self._vm.initialize)

    def _start_calibration(self):
        if not self._vm:
            return
        self._vm.start_calibration({
            "enable_anchor_auto_calib": True,
            "enable_tag_auto_calib": self.chk_height.isChecked(),
            "ref_distance_xy_m": self.ref_dist_spin.value(),
            "samples": self.samples_spin.value(),
            "damping": self.damping_spin.value(),
            "iterations": self.iterations_spin.value(),
        })

    def _stop_calibration(self):
        if self._vm:
            self._vm.stop_calibration()

    def _apply_calibration(self):
        if self._vm:
            self._vm.apply_results()

    def _reset_imu(self):
        if self._vm:
            self._vm.reset_imu()

    def _calibrate_imu(self):
        if self._vm:
            self._vm.calibrate_imu()

    def _on_running_changed(self, running: bool):
        self.btn_start_calib.setEnabled(not running)
        self.btn_stop_calib.setEnabled(running)

    def _on_status_updated(self, status: dict):
        state = int(status.get("state", 0))
        state_labels = {
            0: "Unspecified",
            1: "Idle",
            2: "Collecting",
            3: "Calculating",
            4: "Done",
            5: "Error",
        }
        progress = int(status.get("progress_percent", 0))
        current = int(status.get("current_iteration", 0))
        total = int(status.get("total_iterations", 0))
        delay = int(status.get("current_antenna_delay", 0))

        self.calib_progress.setValue(max(0, min(100, progress)))
        self.calib_status.setText(f"Status: {state_labels.get(state, state)}")
        self.calib_iter.setText(f"Iteration: {current} / {total}")
        self.val_err_mean.setText(f"{status.get('last_pair_error_mean_m', 0.0):.3f} m")
        self.val_err_std.setText(f"{status.get('last_pair_error_spread_m', 0.0):.3f} m")
        self.val_err_rms.setText(f"{status.get('last_pair_error_rms_m', 0.0):.3f} m")
        self.val_err_min.setText("--")
        self.val_err_max.setText(f"{status.get('last_pair_error_max_abs_m', 0.0):.3f} m")
        self.val_opt_tx.setText(str(delay) if delay > 0 else "--")
        self.val_opt_rx.setText(str(delay) if delay > 0 else "--")

        if state == 4 and self.chk_auto_apply.isChecked():
            self._apply_calibration()

    def _on_operation_failed(self, message: str):
        self.calib_status.setText(f"Status: Error - {message}")
