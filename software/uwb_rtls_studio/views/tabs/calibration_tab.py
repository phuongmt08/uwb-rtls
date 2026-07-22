"""
UWB RTLS Studio - Calibration Tab (UI loaded from .ui file)
Tab 4: Position auto-calibration config + IMU calibration (Developer only).

Antenna-delay calibration itself has moved to the host-driven
AntennaDelayCalibTab (views/tabs/antenna_delay_calib_tab.py) — firmware no
longer runs any calibration state machine, so this tab keeps only the
pos_calib_cfg (anchor/tag auto-calib survey config) and IMU controls, which
are unrelated features that happen to share this screen.

FE: Loaded from views/ui/calibration_tab.ui (editable in Qt Designer)
BE: pos_calib_cfg + IMU ViewModel bindings (this file)
"""

import os
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QMessageBox
)
from PyQt6.QtCore import QTimer, QPropertyAnimation, QEasingCurve
from PyQt6.QtWidgets import QGraphicsDropShadowEffect
from PyQt6.QtGui import QColor
from PyQt6 import uic

# Path to .ui file
UI_FILE = os.path.join(os.path.dirname(__file__), '..', 'ui', 'calibration_tab.ui')


def _flash_button(btn, color_hex: str = "#22D3EE", duration_ms: int = 450):
    """Hiệu ứng glow phát sáng tắt dần khi nhấn nút.

    Sử dụng QGraphicsDropShadowEffect + QPropertyAnimation để tạo
    hiệu ứng viền sáng lan rộng rồi mờ dần sau khi nhấn. Không
    ảnh hưởng đến layout vì dùng shadow effect.
    """
    effect = QGraphicsDropShadowEffect(btn)
    effect.setBlurRadius(32)
    effect.setColor(QColor(color_hex))
    effect.setOffset(0, 0)
    btn.setGraphicsEffect(effect)

    anim = QPropertyAnimation(effect, b"blurRadius", btn)
    anim.setDuration(duration_ms)
    anim.setStartValue(32)
    anim.setEndValue(0)
    anim.setEasingCurve(QEasingCurve.Type.OutQuad)
    # Giữ tham chiếu Python trên widget để tránh GC hủy animation sớm
    btn._flash_anim = anim
    anim.finished.connect(lambda: _cleanup_flash(btn))
    anim.start()


def _cleanup_flash(btn):
    """Xóa graphics effect và tham chiếu animation sau khi glow kết thúc."""
    btn.setGraphicsEffect(None)
    btn._flash_anim = None


class CalibrationTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._vm = None

        # Load UI from .ui file
        uic.loadUi(UI_FILE, self)

        self._setup_imu_controls()
        self.btn_start_pos_calib.clicked.connect(self._start_position_calibration)
        if hasattr(self, "chk_save_flash"):
            self.chk_save_flash.setVisible(False)
        self.btn_start_pos_calib.setText("Save Position Config")
        self.btn_stop_pos_calib.setVisible(False)
        self.btn_stop_pos_calib.setEnabled(False)

        # Antenna-delay calibration now lives in AntennaDelayCalibTab — hide
        # this screen's now-dead controls rather than editing the .ui file.
        for widget_name in ("delay_group", "prog_group", "diag_group", "opts_group", "btn_apply_calib"):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.setVisible(False)

        self._reset_display_fields()

    def _reset_display_fields(self):
        # Reset all config input widgets in CalibrationTab
        from utils.helpers import set_widget_placeholder
        for attr in (
            "chk_enable_anchor_calib", "chk_enable_tag_calib",
            "pos_ref_dist_spin", "pos_tag_height_spin", "pos_anchor_height_spin",
            "pos_calib_anchor_spin", "pos_samples_spin", "pos_err_thresh_spin",
            "pos_min_delta_spin", "pos_max_rounds_spin", "pos_max_std_spin",
            "pos_damping_spin", "pos_iterations_spin",
        ):
            widget = getattr(self, attr, None)
            if widget is not None:
                set_widget_placeholder(widget)

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

        # Bind shared app state for position calibration changes
        from utils.app_state import shared_app_state
        shared_app_state.pos_calib_cfg_changed.connect(self._on_pos_calib_cfg_loaded)

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
        self.btn_start_pos_calib.setEnabled(connected)
        self.btn_imu_reset.setEnabled(False)
        self.btn_imu_calibrate.setEnabled(False)

    def _start_position_calibration(self):
        if not self._vm or not self._require_connected_device("saving position calibration config"):
            return

        _flash_button(self.btn_start_pos_calib, "#22D3EE")
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
        self._vm.save_position_calibration_config(config)

    def _reset_imu(self):
        if self._vm and self._require_connected_device("resetting IMU"):
            _flash_button(self.btn_imu_reset, "#EF4444")
            self._vm.reset_imu()

    def _calibrate_imu(self):
        if self._vm and self._require_connected_device("calibrating IMU"):
            _flash_button(self.btn_imu_calibrate, "#22D3EE")
            self._vm.calibrate_imu()

    def _on_connection_state_changed(self, info: dict):
        if info.get("status") in ("Disconnected", "Connecting", "Connected"):
            self._reset_display_fields()
        self._update_action_state()

    def _on_pos_calib_cfg_loaded(self, cfg: dict):
        """Populate the Position Calibration Options group when pos_calib_cfg_t loads.

        Source message: pos_calib_cfg_t (pos_calib_cfg_get/set/resp)
        """
        from utils.helpers import set_widget_placeholder, set_widget_value
        if not cfg:
            for widget_name in (
                "chk_enable_anchor_calib", "chk_enable_tag_calib",
                "pos_ref_dist_spin", "pos_tag_height_spin", "pos_anchor_height_spin",
                "pos_calib_anchor_spin", "pos_samples_spin", "pos_err_thresh_spin",
                "pos_min_delta_spin", "pos_max_rounds_spin", "pos_max_std_spin",
                "pos_damping_spin", "pos_iterations_spin",
            ):
                if hasattr(self, widget_name):
                    set_widget_placeholder(getattr(self, widget_name))
            return

        if hasattr(self, "chk_enable_anchor_calib"):
            set_widget_value(self.chk_enable_anchor_calib, cfg.get("enable_anchor_auto_calib", True))
        if hasattr(self, "chk_enable_tag_calib"):
            set_widget_value(self.chk_enable_tag_calib, cfg.get("enable_tag_auto_calib", True))
        if hasattr(self, "pos_ref_dist_spin"):
            set_widget_value(self.pos_ref_dist_spin, cfg.get("ref_distance_xy_m", 2.0))
        if hasattr(self, "pos_tag_height_spin"):
            set_widget_value(self.pos_tag_height_spin, cfg.get("tag_height_m", 1.0))
        if hasattr(self, "pos_anchor_height_spin"):
            set_widget_value(self.pos_anchor_height_spin, cfg.get("anchor_height_m", 2.5))
        if hasattr(self, "pos_calib_anchor_spin"):
            set_widget_value(self.pos_calib_anchor_spin, cfg.get("calib_anchor_id", 1))
        if hasattr(self, "pos_samples_spin"):
            set_widget_value(self.pos_samples_spin, cfg.get("samples", 10))
        if hasattr(self, "pos_err_thresh_spin"):
            set_widget_value(self.pos_err_thresh_spin, cfg.get("error_threshold_m", 0.3))
        if hasattr(self, "pos_min_delta_spin"):
            set_widget_value(self.pos_min_delta_spin, cfg.get("min_delta_step", 1))
        if hasattr(self, "pos_max_rounds_spin"):
            set_widget_value(self.pos_max_rounds_spin, cfg.get("max_rounds", 10))
        if hasattr(self, "pos_max_std_spin"):
            set_widget_value(self.pos_max_std_spin, cfg.get("max_std_m", 0.2))
        if hasattr(self, "pos_damping_spin"):
            set_widget_value(self.pos_damping_spin, cfg.get("damping", 0.1))
        if hasattr(self, "pos_iterations_spin"):
            set_widget_value(self.pos_iterations_spin, cfg.get("iterations", 100))
