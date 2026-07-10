"""
UWB RTLS Studio - Calibration Tab (UI loaded from .ui file)
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
        self._auto_applied_delay = 0

        # Load UI from .ui file
        uic.loadUi(UI_FILE, self)

        # Widgets are now accessible via objectNames:
        # self.calib_progress  -> QProgressBar
        # self.calib_status    -> QLabel "Status: Idle"
        # self.calib_iter      -> QLabel "Iteration: 0 / 20"
        # self.btn_start_calib -> QPushButton
        # self.btn_stop_calib  -> QPushButton
        # self.btn_apply_calib -> QPushButton
        # self.ref_dist_spin, self.samples_spin, etc.
        self._setup_imu_controls()
        self.btn_start_calib.clicked.connect(self._start_calibration)
        self.btn_stop_calib.clicked.connect(self._stop_calibration)
        self.btn_start_pos_calib.clicked.connect(self._start_position_calibration)
        self.btn_stop_pos_calib.clicked.connect(self._stop_calibration)
        self.btn_apply_calib.clicked.connect(self._apply_calibration)
        if hasattr(self, "chk_save_flash"):
            self.chk_save_flash.setVisible(False)
        self.btn_stop_calib.setEnabled(False)
        self.btn_stop_pos_calib.setEnabled(False)
        self._reset_display_fields()

    def _reset_display_fields(self):
        self.calib_progress.setValue(0)
        self.calib_status.setText("Status: -")
        self.calib_iter.setText("Iteration: -")
        for label_name in (
            "val_err_mean",
            "val_err_std",
            "val_err_rms",
            "val_err_min",
            "val_err_max",
            "val_opt_tx",
            "val_opt_rx",
        ):
            label = getattr(self, label_name, None)
            if label is not None:
                label.setText("-")

        # Reset all config input widgets in CalibrationTab
        from utils.helpers import set_widget_placeholder
        for attr in (
            "tx_delay_spin", "rx_delay_spin",
            "chk_enable_anchor_calib", "chk_enable_tag_calib",
            "pos_ref_dist_spin", "pos_tag_height_spin", "pos_anchor_height_spin",
            "pos_calib_anchor_spin", "pos_samples_spin", "pos_err_thresh_spin",
            "pos_min_delta_spin", "pos_max_rounds_spin", "pos_max_std_spin",
            "pos_damping_spin", "pos_iterations_spin",
            "ref_dist_spin", "samples_spin", "damping_spin", "iterations_spin"
        ):
            widget = getattr(self, attr, None)
            if widget is not None:
                set_widget_placeholder(widget)

    @staticmethod
    def _format_metric(value, suffix=""):
        if value is None:
            return "-"
        try:
            return f"{float(value):.3f}{suffix}"
        except (TypeError, ValueError):
            return "-"

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

        # Bind shared app state for position calibration changes
        from utils.app_state import shared_app_state
        shared_app_state.pos_calib_cfg_changed.connect(self._on_pos_calib_cfg_loaded)
        shared_app_state.sys_config_changed.connect(self._on_sys_config_loaded)

        if hasattr(self._vm.model, "connection_state_changed"):
            self._vm.model.connection_state_changed.connect(self._on_connection_state_changed)

        QTimer.singleShot(0, self._vm.initialize)
        self._update_action_state()

    def _has_connected_device(self) -> bool:
        return bool(self._vm and self._vm.model.is_connected)

    def _require_connected_device(self, action_name: str) -> bool:
        if self._has_connected_device():
            return True
        QMessageBox.information(self, "No Connected Device", f"Connect a device before {action_name}.")
        return False

    def _update_action_state(self):
        connected = self._has_connected_device()
        running = bool(self._vm and getattr(self._vm, "_running", False))
        # self.btn_start_calib -> QPushButton
        self.btn_start_pos_calib.setEnabled(connected and not running)
        # self.btn_stop_calib  -> QPushButton
        self.btn_stop_pos_calib.setEnabled(connected and running)
        # self.btn_apply_calib -> QPushButton
        self.btn_imu_reset.setEnabled(connected)
        self.btn_imu_calibrate.setEnabled(connected)

    def _start_calibration(self):
        if not self._vm or not self._require_connected_device("starting calibration"):
            return

        # Collect values only from delay_group (Antenna Delay Calibration)
        config = {
            "enable_anchor_auto_calib": False,
            "enable_tag_auto_calib": False,
            "ref_distance_xy_m": self.ref_dist_spin.value(),
            "tag_height_m": 1.0,
            "anchor_height_m": 2.5,
            "calib_anchor_id": 1,
            "samples": self.samples_spin.value(),
            "error_threshold_m": 0.3,
            "min_delta_step": 1,
            "max_rounds": 10,
            "max_std_m": 0.2,
            "damping": self.damping_spin.value(),
            "iterations": self.iterations_spin.value(),
        }
        self._vm.start_calibration(config)

    def _start_position_calibration(self):
        if not self._vm or not self._require_connected_device("starting position calibration"):
            return

        # Collect values from pos_calib_group (Position Calibration Options)
        config = {
            "enable_anchor_auto_calib": self.chk_enable_anchor_calib.isChecked(),
            "enable_tag_auto_calib": self.chk_enable_tag_calib.isChecked(),
            "ref_distance_xy_m": self.pos_ref_dist_spin.value(),
            "tag_height_m": self.pos_tag_height_spin.value(),
            "anchor_height_m": self.pos_anchor_height_spin.value(),
            "calib_anchor_id": self.pos_calib_anchor_spin.value(),
            "samples": self.pos_samples_spin.value(),
            "error_threshold_m": self.pos_err_thresh_spin.value(),
            "min_delta_step": self.pos_min_delta_spin.value(),
            "max_rounds": self.pos_max_rounds_spin.value(),
            "max_std_m": self.pos_max_std_spin.value(),
            "damping": self.pos_damping_spin.value(),
            "iterations": self.pos_iterations_spin.value(),
        }
        self._vm.start_calibration(config)

    def _stop_calibration(self):
        if self._vm:
            self._vm.stop_calibration()

    def _apply_calibration(self):
        if not self._vm or not self._require_connected_device("applying calibration"):
            return

        if self._vm.is_applying:
            self.calib_status.setText("Status: Error - Apply already in progress")
            return

        latest_status = self._vm.latest_status
        delay = int(latest_status.get("current_antenna_delay", 0))

        # Fallback to current inputs if 0
        tx_delay = delay if delay > 0 else self.tx_delay_spin.value()
        rx_delay = delay if delay > 0 else self.rx_delay_spin.value()

        if tx_delay <= 0 or rx_delay <= 0:
            self.calib_status.setText("Status: Error - No valid antenna delay to apply")
            return

        # Collect Position Calibration Option inputs
        pos_config = {
            "enable_anchor_auto_calib": self.chk_enable_anchor_calib.isChecked(),
            "enable_tag_auto_calib": self.chk_enable_tag_calib.isChecked(),
            "ref_distance_xy_m": self.pos_ref_dist_spin.value(),
            "tag_height_m": self.pos_tag_height_spin.value(),
            "anchor_height_m": self.pos_anchor_height_spin.value(),
            "calib_anchor_id": self.pos_calib_anchor_spin.value(),
            "samples": self.pos_samples_spin.value(),
            "error_threshold_m": self.pos_err_thresh_spin.value(),
            "min_delta_step": self.pos_min_delta_spin.value(),
            "max_rounds": self.pos_max_rounds_spin.value(),
            "max_std_m": self.pos_max_std_spin.value(),
            "damping": self.pos_damping_spin.value(),
            "iterations": self.pos_iterations_spin.value(),
        }

        self._vm.apply_results_sequence(tx_delay, rx_delay, pos_config)

    def _reset_imu(self):
        if self._vm and self._require_connected_device("resetting IMU"):
            self._vm.reset_imu()

    def _calibrate_imu(self):
        if self._vm and self._require_connected_device("calibrating IMU"):
            self._vm.calibrate_imu()

    def _on_connection_state_changed(self, info: dict):
        if info.get("status") in ("Disconnected", "Connecting", "Connected"):
            self._reset_display_fields()
            self._update_action_state()

    def _on_running_changed(self, running: bool):
        _ = running
        self._update_action_state()

    def _on_sys_config_loaded(self, cfg: dict):
        from utils.helpers import set_widget_placeholder, set_widget_value
        if not cfg:
            if hasattr(self, "tx_delay_spin"):
                set_widget_placeholder(self.tx_delay_spin)
            if hasattr(self, "rx_delay_spin"):
                set_widget_placeholder(self.rx_delay_spin)
            return
        if hasattr(self, "tx_delay_spin"):
            set_widget_value(self.tx_delay_spin, cfg.get("tx_antenna_delay", self.tx_delay_spin.value()))
        if hasattr(self, "rx_delay_spin"):
            set_widget_value(self.rx_delay_spin, cfg.get("rx_antenna_delay", self.rx_delay_spin.value()))

    def _on_pos_calib_cfg_loaded(self, cfg: dict):
        """Populate ALL calibration inputs when pos_calib_cfg_t is loaded from device.

        Covers both groups:
          - Antenna Delay Calibration: ref_dist_spin, samples_spin, damping_spin, iterations_spin
          - Position Calibration Options: all pos_* widgets
        Source message: pos_calib_cfg_t  (tag 61/62/63  pos_calib_cfg_get/set/resp)
        """
        from utils.helpers import set_widget_placeholder, set_widget_value
        if not cfg:
            for widget_name in (
                # --- Antenna Delay Calibration group ---
                "ref_dist_spin", "samples_spin", "damping_spin", "iterations_spin",
                # --- Position Calibration Options group ---
                "chk_enable_anchor_calib", "chk_enable_tag_calib",
                "pos_ref_dist_spin", "pos_tag_height_spin", "pos_anchor_height_spin",
                "pos_calib_anchor_spin", "pos_samples_spin", "pos_err_thresh_spin",
                "pos_min_delta_spin", "pos_max_rounds_spin", "pos_max_std_spin",
                "pos_damping_spin", "pos_iterations_spin",
            ):
                if hasattr(self, widget_name):
                    set_widget_placeholder(getattr(self, widget_name))
            return

        # ── Antenna Delay Calibration group ─────────────────────────────────
        # pos_calib_cfg_t.ref_distance_xy_m  → ref_dist_spin
        if hasattr(self, "ref_dist_spin"):
            set_widget_value(self.ref_dist_spin, cfg.get("ref_distance_xy_m", 1.0))
        # pos_calib_cfg_t.samples  → samples_spin
        if hasattr(self, "samples_spin"):
            set_widget_value(self.samples_spin, cfg.get("samples", 1000))
        # pos_calib_cfg_t.damping  → damping_spin
        if hasattr(self, "damping_spin"):
            set_widget_value(self.damping_spin, cfg.get("damping", 0.5))
        # pos_calib_cfg_t.iterations  → iterations_spin
        if hasattr(self, "iterations_spin"):
            set_widget_value(self.iterations_spin, cfg.get("iterations", 20))

        # ── Position Calibration Options group ───────────────────────────────
        # pos_calib_cfg_t.enable_anchor_auto_calib  → chk_enable_anchor_calib
        if hasattr(self, "chk_enable_anchor_calib"):
            set_widget_value(self.chk_enable_anchor_calib, cfg.get("enable_anchor_auto_calib", True))
        # pos_calib_cfg_t.enable_tag_auto_calib  → chk_enable_tag_calib
        if hasattr(self, "chk_enable_tag_calib"):
            set_widget_value(self.chk_enable_tag_calib, cfg.get("enable_tag_auto_calib", True))
        # pos_calib_cfg_t.ref_distance_xy_m  → pos_ref_dist_spin
        if hasattr(self, "pos_ref_dist_spin"):
            set_widget_value(self.pos_ref_dist_spin, cfg.get("ref_distance_xy_m", 2.0))
        # pos_calib_cfg_t.tag_height_m  → pos_tag_height_spin
        if hasattr(self, "pos_tag_height_spin"):
            set_widget_value(self.pos_tag_height_spin, cfg.get("tag_height_m", 1.0))
        # pos_calib_cfg_t.anchor_height_m  → pos_anchor_height_spin
        if hasattr(self, "pos_anchor_height_spin"):
            set_widget_value(self.pos_anchor_height_spin, cfg.get("anchor_height_m", 2.5))
        # pos_calib_cfg_t.calib_anchor_id  → pos_calib_anchor_spin
        if hasattr(self, "pos_calib_anchor_spin"):
            set_widget_value(self.pos_calib_anchor_spin, cfg.get("calib_anchor_id", 1))
        # pos_calib_cfg_t.samples  → pos_samples_spin
        if hasattr(self, "pos_samples_spin"):
            set_widget_value(self.pos_samples_spin, cfg.get("samples", 10))
        # pos_calib_cfg_t.error_threshold_m  → pos_err_thresh_spin
        if hasattr(self, "pos_err_thresh_spin"):
            set_widget_value(self.pos_err_thresh_spin, cfg.get("error_threshold_m", 0.3))
        # pos_calib_cfg_t.min_delta_step  → pos_min_delta_spin
        if hasattr(self, "pos_min_delta_spin"):
            set_widget_value(self.pos_min_delta_spin, cfg.get("min_delta_step", 1))
        # pos_calib_cfg_t.max_rounds  → pos_max_rounds_spin
        if hasattr(self, "pos_max_rounds_spin"):
            set_widget_value(self.pos_max_rounds_spin, cfg.get("max_rounds", 10))
        # pos_calib_cfg_t.max_std_m  → pos_max_std_spin
        if hasattr(self, "pos_max_std_spin"):
            set_widget_value(self.pos_max_std_spin, cfg.get("max_std_m", 0.2))
        # pos_calib_cfg_t.damping  → pos_damping_spin
        if hasattr(self, "pos_damping_spin"):
            set_widget_value(self.pos_damping_spin, cfg.get("damping", 0.1))
        # pos_calib_cfg_t.iterations  → pos_iterations_spin
        if hasattr(self, "pos_iterations_spin"):
            set_widget_value(self.pos_iterations_spin, cfg.get("iterations", 100))

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

        self.calib_progress.setValue(progress)

        custom_text = status.get("custom_status_text")
        if custom_text:
            self.calib_status.setText(str(custom_text))
        else:
            self.calib_status.setText(f"Status: {state_labels.get(state, 'Unknown')}")

        # calib_status_resp_t.current_iteration / total_iterations
        self.calib_iter.setText(f"Iteration: {current} / {total}" if total > 0 else "Iteration: -")
        # calib_status_resp_t.last_pair_error_mean_m  → Error Mean
        self.val_err_mean.setText(self._format_metric(status.get("last_pair_error_mean_m"), " m"))
        # calib_status_resp_t.last_pair_error_spread_m  → Error Std
        self.val_err_std.setText(self._format_metric(status.get("last_pair_error_spread_m"), " m"))
        # calib_status_resp_t.last_pair_error_rms_m  → Error RMS
        self.val_err_rms.setText(self._format_metric(status.get("last_pair_error_rms_m"), " m"))
        # calib_status_resp_t.last_pair_error_mean_abs_m  → Error Min
        # (protocol không có trường error_min riêng; dùng mean_abs thay thế)
        self.val_err_min.setText(self._format_metric(status.get("last_pair_error_mean_abs_m"), " m"))
        # calib_status_resp_t.last_pair_error_max_abs_m  → Error Max
        self.val_err_max.setText(self._format_metric(status.get("last_pair_error_max_abs_m"), " m"))
        # calib_status_resp_t.current_antenna_delay  → Optimized TX / RX Delay
        self.val_opt_tx.setText(str(delay) if delay > 0 else "-")
        self.val_opt_rx.setText(str(delay) if delay > 0 else "-")

        if state != 4:
            self._auto_applied_delay = 0
        elif (
            delay > 0
            and self.chk_auto_apply.isChecked()
            and custom_text is None
            and delay != self._auto_applied_delay
            and not self._vm.is_applying
        ):
            self._auto_applied_delay = delay
            self._apply_calibration()

    def _on_operation_failed(self, message: str):
        self.calib_status.setText(f"Status: Error - {message}")
