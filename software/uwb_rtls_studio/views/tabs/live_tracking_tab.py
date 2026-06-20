"""
==============================================================================
  UWB RTLS Studio - Live Tracking Tab View
==============================================================================
"""
import os
import time
import uuid
import math
import json
import logging

log = logging.getLogger(__name__)

from PyQt6 import uic
from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QRect, QTimer, Qt, QPointF, QPoint
from PyQt6.QtWidgets import (
    QLabel,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLineEdit,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QFormLayout,
    QGridLayout,
    QMessageBox,
    QFileDialog,
    QFrame,
    QCheckBox,
    QToolButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QColorDialog,
    QStackedWidget,
)
from PyQt6.QtGui import QColor, QPolygonF, QShortcut, QKeySequence, QVector3D

from views.components.position_canvas import PositionCanvas
from views.components.geofence_3d_widget import Geofence3DWidget, OPENGL_AVAILABLE
from models.geofence_model import GeofenceZone
from views.components.geofence_editor import GeofenceEditorWidget
from utils.config_dim import GRID_SPACING_M


UI_FILE = os.path.join(os.path.dirname(__file__), "..", "ui", "live_tracking_tab.ui")


class LiveTrackingTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._vm = None
        self._frame_count = 0
        self._start_time = time.time()
        self._is_ranging = False
        self._last_z = 0.0
        self._last_rms = 0.0
        self._last_stats = {}
        self.sidebar_expanded = True
        self._is_developer_mode = False
        self._anchor_layout_commit_pending = False
        self._draft_anchor_layout = []
        self._geofence_anchor_baseline = []
        self._pending_layout_read_for_editor = False
        self._undo_stack = []
        self._redo_stack = []
        self._restoring_geofence_state = False
        self._map_properties_syncing = False

        uic.loadUi(UI_FILE, self)
        if hasattr(self, "canvas_header"):
            self.canvas_header.hide()
        self._setup_dynamic_metrics()

        self._canvas = self.position_canvas
        self._canvas.parent_tab = self

        # Replace position_canvas in layout with QStackedWidget
        self._canvas_stack = QStackedWidget(self)
        self._canvas_stack.setMinimumSize(400, 300)
        self._canvas_stack.setStyleSheet("background: #1E293B; border: none;")
        self.main_layout.removeWidget(self.position_canvas)
        self.main_layout.addWidget(self._canvas_stack, 0, 0, 2, 2)
        self._canvas_stack.addWidget(self.position_canvas)

        # Initialize real 3D widget
        self.gl_widget = None
        if OPENGL_AVAILABLE:
            try:
                self.gl_widget = Geofence3DWidget(self._vm, self)
                self.gl_widget.setStyleSheet("border: none;")
                self._canvas_stack.addWidget(self.gl_widget)
            except Exception as e:
                log.error(f"Error initializing Geofence3DWidget: {e}")
                self.gl_widget = None

        if self.gl_widget is None:
            self.warning_container = QLabel(self)
            self.warning_container.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.warning_container.setWordWrap(True)
            self.warning_container.setStyleSheet("background-color: #1E293B; border: 1px solid #EF4444; padding: 20px; color: #F87171; font-size: 14px;")
            self.warning_container.setText(
                "⚠️ Không thể mở chế độ 3D!\n\n"
                "Thư viện PyOpenGL chưa được cài đặt trên máy tính của bạn.\n"
                "Để kích hoạt tính năng hiển thị 3D cho mô hình 2.5D, vui lòng cài đặt bằng lệnh:\n"
                "pip install PyOpenGL PyOpenGL_accelerate"
            )
            self._canvas_stack.addWidget(self.warning_container)

        # Intercept calls to update 3D widget in sync with 2D canvas
        orig_set_geofences = self._canvas.set_geofences
        orig_set_anchors = self._canvas.set_anchors

        def intercepted_set_geofences(zones):
            orig_set_geofences(zones)
            if self.gl_widget is not None:
                self.gl_widget.set_geofences(zones)

        def intercepted_set_anchors(anchors):
            orig_set_anchors(anchors)
            if self.gl_widget is not None:
                self.gl_widget.set_anchors(anchors)

        self._canvas.set_geofences = intercepted_set_geofences
        self._canvas.set_anchors = intercepted_set_anchors

        self._preview_overlay_btn = QToolButton(self)
        self._canvas_tool_label = QLabel(self)
        self._canvas_tool_bar = QFrame(self)

        self._setup_geofencing_ui()

        self.warning_label.setVisible(False)
        self.btn_toggle_sidebar.clicked.connect(self.toggle_sidebar)
        self.btn_start.clicked.connect(self._start_ranging)
        self.btn_stop.clicked.connect(self._stop_ranging)
        self.btn_clear.clicked.connect(self._canvas.clear_trail)

        self.header_widget.raise_()
        self.right_widget.raise_()
        self.btn_toggle_sidebar.raise_()

        self._stats_timer = QTimer(self)
        self._stats_timer.timeout.connect(self._update_stats)
        self._stats_timer.start(1000)
        self._setup_canvas_preview_button()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_sidebar_geometry()
        self._position_canvas_preview_button()

    def update_sidebar_geometry(self):
        panel_width = 380
        panel_height = self.height() - 20
        target_x = self.width() - panel_width - 10 if self.sidebar_expanded else self.width()

        self.right_widget.setGeometry(target_x, 10, panel_width, panel_height)

        button_width = self.btn_toggle_sidebar.width()
        button_height = self.btn_toggle_sidebar.height()
        self.btn_toggle_sidebar.setGeometry(
            target_x - button_width,
            (self.height() - button_height) // 2,
            button_width,
            button_height,
        )

        header_width = max(
            self.width() - 20 - (panel_width + 10 if self.sidebar_expanded else 0),
            100,
        )
        self.header_widget.setGeometry(10, 10, header_width, 40)
        self._position_canvas_preview_button()

    def _setup_canvas_preview_button(self):
        btn = self._preview_overlay_btn
        btn.setText("Top")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        btn.setAutoRaise(True)
        btn.setCheckable(True)
        btn.setChecked(False)
        btn.setFixedSize(58, 34)
        btn.setStyleSheet(
            "QToolButton { background: rgba(17, 24, 39, 230); color: #F8FAFC; border: 1px solid #FACC15; "
            "border-radius: 8px; font-weight: bold; padding: 5px 10px; }"
            "QToolButton:hover { background: rgba(30, 41, 59, 245); border-color: #FDE047; }"
            "QToolButton:checked { background: rgba(37, 99, 235, 235); border-color: #60A5FA; }"
            "QToolButton:pressed { background: rgba(15, 23, 42, 245); }"
        )
        btn.clicked.connect(self._toggle_canvas_view_mode)
        btn.raise_()
        self._setup_canvas_tool_bar()
        self._position_canvas_preview_button()

    def _toggle_canvas_view_mode(self, checked: bool):
        if checked:
            # Switching from 2D to 3D: sync 3D camera from 2D canvas
            if self.gl_widget is not None:
                cx = self._canvas._view_cx
                cy = self._canvas._view_cy
                vrange = self._canvas._view_range
                self.gl_widget.setCameraPosition(pos=QVector3D(cx, cy, 0.0), distance=vrange * 1.70)

            self._canvas_stack.setCurrentIndex(1)
            self._preview_overlay_btn.setText("3D")
        else:
            # Switching from 3D to 2D: sync 2D canvas from 3D camera
            if self.gl_widget is not None:
                center = self.gl_widget.opts['center']
                distance = self.gl_widget.opts['distance']
                
                # Update 2D canvas center and range safely
                cx = center.x() if hasattr(center, 'x') else center[0]
                cy = center.y() if hasattr(center, 'y') else center[1]
                self._canvas._view_cx = cx
                self._canvas._view_cy = cy
                self._canvas._view_range = max(1.0, distance / 1.70)
                self._canvas.update()

            self._canvas_stack.setCurrentIndex(0)
            self._preview_overlay_btn.setText("Top")

    def _setup_canvas_tool_bar(self):
        bar = self._canvas_tool_bar
        bar.setObjectName("canvas_tool_bar")
        bar.setStyleSheet(
            "QFrame#canvas_tool_bar { background: rgba(15, 23, 42, 226); border: 1px solid rgba(148, 163, 184, 120); border-radius: 8px; }"
            "QToolButton { background: transparent; color: #CBD5E1; border: none; padding: 6px 9px; font-weight: bold; }"
            "QToolButton:hover { color: #FFFFFF; background: rgba(51, 65, 85, 180); }"
            "QToolButton:checked { color: #22D3EE; background: rgba(8, 47, 73, 210); }"
        )
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(4, 3, 4, 3)
        layout.setSpacing(2)
        tools = [
            ("1", "Room", "Room Zone (1)", lambda: self._set_editor_tool("room", "draw")),
            ("2", "Wall", "Wall (2)", lambda: self._set_editor_tool("wall", "draw")),
            ("3", "Zone", "Rule Zone (3)", lambda: self._set_editor_tool("zone", "draw")),
            ("4", "Object", "Object (4)", lambda: self._set_editor_tool("object", "draw")),
            ("5", "Edit", "Edit Mode (5)", lambda: self._set_editor_mode("edit_vertices")),
            ("6", "View", "View Only (6)", lambda: self._set_view_only_mode()),
            ("load", "Load", "Load map JSON", lambda: self._load_map()),
            ("save", "Save", "Save map JSON", lambda: self._save_map()),
            ("clear", "Clear", "Clear canvas", lambda: self._clear_map()),
        ]
        self._canvas_tool_buttons = {}
        for key, display_name, tip, handler in tools:
            tool = QToolButton(bar)
            tool.setText(display_name)
            tool.setToolTip(tip)
            tool.setCheckable(key in {"1", "2", "3", "4", "5"})
            tool.clicked.connect(handler)
            layout.addWidget(tool)
            self._canvas_tool_buttons[key] = tool
        bar.adjustSize()
        bar.setVisible(False)
        bar.raise_()

    def _position_canvas_preview_button(self):
        if not hasattr(self, "_preview_overlay_btn"):
            return
        canvas = getattr(self, "_canvas_stack", self._canvas)
        canvas_origin = canvas.mapTo(self, QPoint(0, 0))
        sidebar_w = self.right_widget.width() if self.sidebar_expanded else 0
        x = max(canvas_origin.x() + canvas.width() - sidebar_w - self._preview_overlay_btn.width() - 28, canvas_origin.x() + 12)
        y = canvas_origin.y() + 10
        self._preview_overlay_btn.move(x, y)
        self._preview_overlay_btn.raise_()
        if hasattr(self, "_canvas_tool_bar"):
            self._canvas_tool_bar.adjustSize()
            self._canvas_tool_bar.move(canvas_origin.x() + 14, y)
            self._canvas_tool_bar.raise_()

    def _set_canvas_tool_status(self, text: str):
        self._position_canvas_preview_button()

    def _make_metric_label(self, text: str, color: str = "#94A3B8", bold: bool = False) -> QLabel:
        label = QLabel(text, self)
        weight = "bold" if bold else "normal"
        label.setStyleSheet(
            f"font-family: 'Consolas'; font-size: 14px; font-weight: {weight}; "
            f"color: {color}; background-color: transparent;"
        )
        return label

    def _add_metric_row(self, grid, row: int, title: str, value_label: QLabel):
        title_label = QLabel(title, self)
        title_label.setStyleSheet("font-size: 13px; color: #94A3B8; background-color: transparent;")
        grid.addWidget(title_label, row, 0)
        grid.addWidget(value_label, row, 1)

    def _set_metric_value(self, label_widget, value, format_str="{:.3f}"):
        if label_widget:
            unit = getattr(label_widget, "unit", "")
            unit_space = " " if unit else ""
            if value is None or value == "--":
                label_widget.setText("--")
            elif isinstance(value, str):
                label_widget.setText(f"{value}{unit_space}{unit}")
            else:
                label_widget.setText(f"{format_str.format(value)}{unit_space}{unit}")

    def _clear_live_metrics(self):
        widgets = [
            "sof_label", "length_label", "anchor_mask_label", "fusion_ts_label",
            "tx_frame_cnt_label", "error_frame_cnt_label",
            "ukf_x_label", "ukf_y_label", "ukf_yaw_label",
            "tril_x_label", "tril_y_label", "yaw_label",
            "vx_label", "vy_label",
            "d1_label", "d2_label", "d3_label", "d4_label",
            "z_label", "error_label"
        ]
        for w in widgets:
            label_widget = getattr(self, w, None)
            if label_widget:
                label_widget.setText("--")
        self._last_anchor_mask = 0

    def _setup_dynamic_metrics(self):
        while self.pos_grid.count():
            item = self.pos_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.pos_grid.setSpacing(10)

        groups = [
            ("FRAME", [
                ("SOF:", "sof_label", "#60A5FA", ""),
                ("Length:", "length_label", "#60A5FA", "bytes"),
                ("Anchor Mask:", "anchor_mask_label", "#60A5FA", ""),
                ("Timestamp:", "fusion_ts_label", "#CBD5E1", "ms")
            ]),
            ("COUNTERS", [
                ("Tx Frames:", "tx_frame_cnt_label", "#2DD4BF", ""),
                ("Err Frames:", "error_frame_cnt_label", "#F87171", "")
            ]),
            ("UKF", [
                ("X:", "ukf_x_label", "#60A5FA", "m"),
                ("Y:", "ukf_y_label", "#60A5FA", "m"),
                ("Yaw:", "ukf_yaw_label", "#F472B6", "deg")
            ]),
            ("TRILATERATION", [
                ("X:", "tril_x_label", "#FB923C", "m"),
                ("Y:", "tril_y_label", "#FB923C", "m"),
                ("Yaw:", "yaw_label", "#F472B6", "deg")
            ]),
            ("MOTION", [
                ("VX:", "vx_label", "#2DD4BF", "m/s"),
                ("VY:", "vy_label", "#2DD4BF", "m/s")
            ]),
            ("RANGING", [
                ("D1:", "d1_label", "#A78BFA", "m"),
                ("D2:", "d2_label", "#A78BFA", "m"),
                ("D3:", "d3_label", "#A78BFA", "m"),
                ("D4:", "d4_label", "#A78BFA", "m")
            ]),
            ("QUALITY", [
                ("Z Height:", "z_label", "#60A5FA", "m"),
                ("Error:", "error_label", "#F59E0B", "m")
            ])
        ]

        current_row = 0
        for group_name, items in groups:
            group_label = QLabel(group_name, self)
            group_label.setStyleSheet(
                "font-size: 11px; font-weight: bold; color: #64748B; "
                "margin-top: 5px; background-color: transparent;"
            )
            self.pos_grid.addWidget(group_label, current_row, 0, 1, 2)
            current_row += 1
            
            for text, attr, color, unit in items:
                lbl = QLabel(text, self)
                lbl.setStyleSheet("font-size: 13px; color: #94A3B8; background-color: transparent;")
                self.pos_grid.addWidget(lbl, current_row, 0)
                
                value_label = self._make_metric_label("--", color, True)
                value_label.unit = unit
                self.pos_grid.addWidget(value_label, current_row, 1)
                setattr(self, attr, value_label)
                current_row += 1

        self.x_label = self.ukf_x_label
        self.y_label = self.ukf_y_label
        self.err_cnt_label = self.error_frame_cnt_label
        self.tril_xy_label = self.tril_x_label
        self.raw_yaw_label = self.yaw_label

        self.success_label = self._make_metric_label("--", "#10B981", True)
        self.failed_label = self._make_metric_label("--", "#F87171", True)
        self.timeout_label = self._make_metric_label("--", "#F59E0B", True)
        self.period_label = self._make_metric_label("--", "#60A5FA", True)
        self.success_rate_label = self._make_metric_label("--", "#10B981", True)
        self.avg_rssi_label = self._make_metric_label("--", "#A78BFA", True)
        self.last_range_time_label = self._make_metric_label("--", "#CBD5E1", True)

        self._add_metric_row(self.stats_grid, 3, "Success:", self.success_label)
        self._add_metric_row(self.stats_grid, 4, "Failed:", self.failed_label)
        self._add_metric_row(self.stats_grid, 5, "Timeout:", self.timeout_label)
        self._add_metric_row(self.stats_grid, 6, "Period:", self.period_label)
        self._add_metric_row(self.stats_grid, 7, "Success Rate:", self.success_rate_label)
        self._add_metric_row(self.stats_grid, 8, "Avg RSSI:", self.avg_rssi_label)
        self._add_metric_row(self.stats_grid, 9, "Last Range:", self.last_range_time_label)

    def toggle_sidebar(self):
        self.sidebar_expanded = not self.sidebar_expanded
        self.btn_toggle_sidebar.setText(">" if self.sidebar_expanded else "<")

        panel_width = 380
        panel_height = self.height() - 20
        end_x = self.width() - panel_width - 10 if self.sidebar_expanded else self.width()

        button_width = self.btn_toggle_sidebar.width()
        button_height = self.btn_toggle_sidebar.height()

        self.anim = QPropertyAnimation(self.right_widget, b"geometry")
        self.anim.setDuration(250)
        self.anim.setStartValue(self.right_widget.geometry())
        self.anim.setEndValue(QRect(end_x, 10, panel_width, panel_height))
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.btn_anim = QPropertyAnimation(self.btn_toggle_sidebar, b"geometry")
        self.btn_anim.setDuration(250)
        self.btn_anim.setStartValue(self.btn_toggle_sidebar.geometry())
        self.btn_anim.setEndValue(
            QRect(
                end_x - button_width,
                (self.height() - button_height) // 2,
                button_width,
                button_height,
            )
        )
        self.btn_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        header_width = max(
            self.width() - 20 - (panel_width + 10 if self.sidebar_expanded else 0),
            100,
        )
        self.header_anim = QPropertyAnimation(self.header_widget, b"geometry")
        self.header_anim.setDuration(250)
        self.header_anim.setStartValue(self.header_widget.geometry())
        self.header_anim.setEndValue(QRect(10, 10, header_width, 40))
        self.header_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.anim.start()
        self.btn_anim.start()
        self.header_anim.start()
        self._canvas.auto_fit()
        self._position_canvas_preview_button()

    def set_viewmodel(self, vm):
        self._vm = vm
        if hasattr(self, "gl_widget") and self.gl_widget is not None:
            self.gl_widget.set_viewmodel(vm)
        self._vm.ranging_started.connect(self._on_ranging_started)
        self._vm.ranging_stopped.connect(self._on_ranging_stopped)
        self._vm.position_updated.connect(self._on_position_updated)
        self._vm.sensor_fusion_updated.connect(self._on_sensor_fusion_updated)
        self._vm.anchor_distances_updated.connect(self._on_anchor_distances)
        self._vm.anchor_layout_updated.connect(self._on_anchor_layout_updated)
        self._vm.stats_updated.connect(self._on_stats_updated)
        
        self._vm.geofence_status_updated.connect(self._on_geofence_status_updated)
        self._vm.geofence_layout_updated.connect(self._canvas.set_geofences)
        
        if hasattr(self._vm, "scan_devices_updated"):
            self._vm.scan_devices_updated.connect(self._on_scan_devices_updated)
            self._on_scan_devices_updated(self._vm.get_scan_devices())
        
        self._vm.load_geofences()
        self._canvas.set_geofences(self._vm.get_geofence_zones())
        
        current_layout = getattr(self._vm, "current_anchor_layout", [])
        if current_layout:
            self._on_anchor_layout_updated(current_layout)

    def _on_anchor_layout_updated(self, anchors_list):
        if getattr(self._canvas, "dim_tracking_view", False):
            normalized = [self._normalize_anchor_record(anchor, idx) for idx, anchor in enumerate(anchors_list or [])]
            self._draft_anchor_layout = normalized
            if self._pending_layout_read_for_editor:
                self._canvas.set_anchors(self._format_anchors_for_canvas(normalized))
                if self._vm:
                    self._vm.geofence_repo.set_anchors(normalized)
                self._anchor_layout_commit_pending = True
                self._pending_layout_read_for_editor = False
                self._refresh_anchor_status_label()
            return
        self.set_anchors(self._format_anchors_for_canvas(anchors_list or []))

    def _start_ranging(self):
        if self._vm:
            self._vm.start_ranging()

    def _stop_ranging(self):
        if self._vm:
            self._vm.stop_ranging()

    def _on_ranging_started(self):
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self._is_ranging = True
        self._frame_count = 0
        self._start_time = time.time()
        self._canvas.clear_trail()
        self._last_stats = {}
        self._clear_live_metrics()
        self._render_stats()

    def _on_ranging_stopped(self):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self._is_ranging = False
        self._clear_live_metrics()

    def _on_position_updated(self, x, y, z, rms):
        self._frame_count += 1
        self._last_z = z
        self._last_rms = rms
        self._canvas.update_position(
            {
                "x": x,
                "y": y,
                "z": z,
                "error": rms,
                "yaw": 0.0,
                "source": "ranging",
            }
        )

        seq = 0
        anchor_mask = 0
        timestamp_ms = 0
        if self._vm and self._vm.model._position_history:
            last_sample = self._vm.model._position_history[-1]
            seq = last_sample.get("seq", 0)
            anchor_mask = last_sample.get("anchor_mask", 0)
            timestamp_ms = last_sample.get("timestamp_ms", 0)
            self._last_anchor_mask = anchor_mask

        self._set_metric_value(self.sof_label, "0xAA")
        self._set_metric_value(self.length_label, 33, "{:d}")
        if anchor_mask:
            self._set_metric_value(self.anchor_mask_label, f"0x{anchor_mask:02X} ({anchor_mask:08b})")
        else:
            self._set_metric_value(self.anchor_mask_label, "--")
        
        self._set_metric_value(self.fusion_ts_label, timestamp_ms, "{:d}")
        self._set_metric_value(self.tx_frame_cnt_label, seq, "{:d}")

        is_fusion_stale = (time.time() - getattr(self, "_last_fusion_time", 0.0) > 2.0)
        
        if is_fusion_stale:
            self._set_metric_value(self.ukf_x_label, x)
            self._set_metric_value(self.ukf_y_label, y)
            self._set_metric_value(self.ukf_yaw_label, 0.0, "{:.1f}")
            self._set_metric_value(self.tril_x_label, x)
            self._set_metric_value(self.tril_y_label, y)
            self._set_metric_value(self.yaw_label, 0.0, "{:.1f}")
            self._set_metric_value(self.vx_label, 0.0)
            self._set_metric_value(self.vy_label, 0.0)

        self._set_metric_value(self.z_label, z)
        self._set_metric_value(self.error_label, rms)
        
        err_cnt = self._last_stats.get("failed_count", 0) + self._last_stats.get("timeout_count", 0)
        self._set_metric_value(self.error_frame_cnt_label, err_cnt, "{:d}")

        if self._canvas.anchors:
            anchors = self._canvas.anchors
            min_x = min(anchor["x"] for anchor in anchors)
            max_x = max(anchor["x"] for anchor in anchors)
            min_y = min(anchor["y"] for anchor in anchors)
            max_y = max(anchor["y"] for anchor in anchors)
            self.warning_label.setVisible(not (min_x <= x <= max_x and min_y <= y <= max_y))
        else:
            self.warning_label.setVisible(False)

    def _on_sensor_fusion_updated(self, data: dict):
        self._last_fusion_time = time.time()
        x = float(data.get("ukf_x_m", 0.0))
        y = float(data.get("ukf_y_m", 0.0))
        yaw = float(data.get("ukf_yaw_deg", 0.0))
        vx = float(data.get("vx_mps", 0.0))
        vy = float(data.get("vy_mps", 0.0))
        tril_x = float(data.get("tril_x_m", 0.0))
        tril_y = float(data.get("tril_y_m", 0.0))
        raw_yaw = float(data.get("yaw_deg", 0.0))
        timestamp_ms = int(data.get("timestamp_ms", 0))
        err_count = int(data.get("ranging_error_count", 0))
        seq = int(data.get("seq", 0))

        self._canvas.update_position(
            {
                "x": x,
                "y": y,
                "z": self._last_z,
                "error": self._last_rms,
                "yaw": yaw,
                "source": "sensor_fusion",
            }
        )

        self._set_metric_value(self.sof_label, "0xAA")
        self._set_metric_value(self.length_label, 33, "{:d}")
        
        anchor_mask = getattr(self, "_last_anchor_mask", 0)
        if anchor_mask:
            self._set_metric_value(self.anchor_mask_label, f"0x{anchor_mask:02X} ({anchor_mask:08b})")
        else:
            self._set_metric_value(self.anchor_mask_label, "--")

        self._set_metric_value(self.fusion_ts_label, timestamp_ms, "{:d}")
        self._set_metric_value(self.tx_frame_cnt_label, seq, "{:d}")
        self._set_metric_value(self.error_frame_cnt_label, err_count, "{:d}")

        self._set_metric_value(self.ukf_x_label, x)
        self._set_metric_value(self.ukf_y_label, y)
        self._set_metric_value(self.ukf_yaw_label, yaw, "{:.1f}")

        self._set_metric_value(self.tril_x_label, tril_x)
        self._set_metric_value(self.tril_y_label, tril_y)
        self._set_metric_value(self.yaw_label, raw_yaw, "{:.1f}")

        self._set_metric_value(self.vx_label, vx)
        self._set_metric_value(self.vy_label, vy)

        self._set_metric_value(self.z_label, self._last_z)
        self._set_metric_value(self.error_label, self._last_rms)

    def _on_anchor_distances(self, anchors):
        for anchor in anchors:
            anchor_id = anchor.get("id", "")
            idx = anchor_id.replace("A", "")
            label_widget = getattr(self, f"d{idx}_label", None)
            if label_widget:
                distance_cm = anchor.get("distance_cm")
                self._set_metric_value(
                    label_widget,
                    None if distance_cm is None else float(distance_cm) / 100.0,
                    "{:.3f}"
                )

    def _update_stats(self):
        if not self._is_ranging:
            return

        uptime = int(time.time() - self._start_time)
        self.uptime_label.setText(f"{uptime}s")
        self._render_stats()

    def _on_stats_updated(self, stats: dict):
        self._last_stats = stats.copy()
        self._render_stats()

    def _render_stats(self):
        stats = self._last_stats
        if not stats and self._frame_count == 0:
            self.frames_label.setText("--")
            self.fps_label.setText("--")
            self.success_label.setText("--")
            self.failed_label.setText("--")
            self.timeout_label.setText("--")
            self.period_label.setText("--")
            self.success_rate_label.setText("--")
            self.avg_rssi_label.setText("--")
            self.last_range_time_label.setText("--")
            return
        total = int(stats.get("total_count", stats.get("ranging_total_count", self._frame_count)))
        success = int(stats.get("success_count", stats.get("ranging_success_count", 0)))
        failed = int(stats.get("failed_count", stats.get("ranging_failed_count", 0)))
        timeout = int(stats.get("timeout_count", stats.get("ranging_timeout_count", 0)))
        rate = float(stats.get("update_rate_hz", 0.0))
        if rate <= 0:
            uptime = max(int(time.time() - self._start_time), 1)
            rate = self._frame_count / uptime

        self.frames_label.setText(str(total))
        self.fps_label.setText(f"{rate:.1f}")
        self.success_label.setText(str(success))
        self.failed_label.setText(str(failed))
        self.timeout_label.setText(str(timeout))
        self.period_label.setText(
            f"{int(stats['ranging_period_ms'])} ms" if "ranging_period_ms" in stats else "--"
        )
        self.success_rate_label.setText(
            f"{float(stats['success_rate_percent']):.1f}%" if "success_rate_percent" in stats else "--"
        )
        self.avg_rssi_label.setText(
            f"{int(stats['last_avg_rssi_dbm'])} dBm" if "last_avg_rssi_dbm" in stats else "--"
        )
        self.last_range_time_label.setText(
            f"{int(stats['last_ranging_time_ms'])} ms" if "last_ranging_time_ms" in stats else "--"
        )

    def set_anchors(self, anchors):
        self._canvas.set_anchors(anchors)

    def _coerce_int_id(self, value, default: int = 0) -> int:
        if value is None or value == "":
            return default
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        text = str(value).strip()
        try:
            if text.lower().startswith("0x"):
                return int(text, 16)
            if text.lower().startswith("a") and text[1:].isdigit():
                return int(text[1:])
            return int(text)
        except (TypeError, ValueError):
            return default

    def _normalize_anchor_record(self, anchor: dict, idx: int = 0) -> dict:
        anchor_id = self._coerce_int_id(anchor.get("anchor_id", anchor.get("id", idx)), idx)
        return {
            "anchor_id": anchor_id,
            "label": anchor.get("label", f"A{anchor_id}"),
            "role": anchor.get("role", "anchor"),
            "device_type": anchor.get("device_type", "uwb_anchor"),
            "device_id": self._coerce_int_id(anchor.get("device_id", anchor_id), anchor_id),
            "mac": anchor.get("mac", ""),
            "zone_id": anchor.get("zone_id", ""),
            "zone_name": anchor.get("zone_name", ""),
            "zone_ids": list(anchor.get("zone_ids", [])),
            "zone_names": list(anchor.get("zone_names", [])),
            "room_id": anchor.get("room_id", anchor.get("zone_id", "")),
            "local_x_m": float(anchor.get("local_x_m", anchor.get("x_m", anchor.get("x", 0.0)))),
            "local_y_m": float(anchor.get("local_y_m", anchor.get("y_m", anchor.get("y", 0.0)))),
            "x_m": float(anchor.get("x_m", anchor.get("x", 0.0))),
            "y_m": float(anchor.get("y_m", anchor.get("y", 0.0))),
            "z_m": float(anchor.get("z_m", anchor.get("z", 0.0))),
            "placed": bool(anchor.get("placed", True)),
            "is_scanned": bool(anchor.get("is_scanned", anchor.get("scan_seen", False))),
            "sync_state": anchor.get("sync_state", "draft"),
        }

    def _find_room(self, room_id):
        if not self._vm or not room_id:
            return None
        return next(
            (
                zone for zone in self._vm.get_geofence_zones()
                if getattr(zone, "object_type", "zone") == "room" and zone.id == room_id
            ),
            None,
        )

    @staticmethod
    def _room_origin(room):
        points = list(getattr(room, "points", []) or [])
        if not points:
            return 0.0, 0.0
        idx = getattr(room, "origin_vertex_idx", None)
        if idx is None or not 0 <= int(idx) < len(points):
            idx = 0
        return float(points[int(idx)][0]), float(points[int(idx)][1])

    @staticmethod
    def _scene_to_room_local(scene_x, scene_y, room):
        ox, oy = LiveTrackingTab._room_origin(room)
        theta = math.radians(float(getattr(room, "local_frame_yaw_deg", 0.0)))
        dx, dy = float(scene_x) - ox, float(scene_y) - oy
        c, sin_t = math.cos(theta), math.sin(theta)
        return c * dx + sin_t * dy, -sin_t * dx + c * dy

    @staticmethod
    def _room_local_to_scene(local_x, local_y, room):
        ox, oy = LiveTrackingTab._room_origin(room)
        theta = math.radians(float(getattr(room, "local_frame_yaw_deg", 0.0)))
        c, sin_t = math.cos(theta), math.sin(theta)
        return ox + c * float(local_x) - sin_t * float(local_y), oy + sin_t * float(local_x) + c * float(local_y)

    def _selected_room(self):
        room = self._find_room(self._canvas.selected_zone_id)
        if room is not None:
            return room
        anchor_idx = getattr(self._canvas, "selected_anchor_idx", None)
        if anchor_idx is not None and 0 <= anchor_idx < len(self._canvas.anchors):
            return self._find_room(self._canvas.anchors[anchor_idx].get("room_id", ""))
        return None

    def _format_anchors_for_canvas(self, anchors):
        formatted = []
        for idx, anchor in enumerate(anchors or []):
            item = self._normalize_anchor_record(anchor, idx)
            room = self._find_room(item.get("room_id"))
            if room is not None:
                scene_x, scene_y = self._room_local_to_scene(
                    item["local_x_m"], item["local_y_m"], room
                )
            else:
                scene_x, scene_y = item["x_m"], item["y_m"]
            formatted.append(
                {
                    "anchor_id": item["anchor_id"],
                    "x": scene_x,
                    "y": scene_y,
                    "z": item["z_m"],
                    "label": item["label"],
                    "role": item["role"],
                    "device_type": item["device_type"],
                    "device_id": item["device_id"],
                    "mac": item["mac"],
                    "zone_id": item["zone_id"],
                    "zone_name": item["zone_name"],
                    "zone_ids": list(item["zone_ids"]),
                    "zone_names": list(item["zone_names"]),
                    "room_id": item.get("room_id", ""),
                    "local_x_m": item.get("local_x_m", item["x_m"]),
                    "local_y_m": item.get("local_y_m", item["y_m"]),
                    "placed": item["placed"],
                    "is_scanned": item["is_scanned"],
                    "sync_state": item["sync_state"],
                }
            )
        return formatted

    def _same_anchor_layout(self, left, right) -> bool:
        def key(items):
            normalized = [self._normalize_anchor_record(anchor, idx) for idx, anchor in enumerate(items or [])]
            return [
                (
                    item["anchor_id"],
                    round(item["x_m"], 4),
                    round(item["y_m"], 4),
                    round(item["z_m"], 4),
                    item["label"],
                    item.get("room_id", item.get("zone_id", "")),
                )
                for item in sorted(normalized, key=lambda a: a["anchor_id"])
            ]
        return key(left) == key(right)

    def _point_in_points(self, points, x: float, y: float) -> bool:
        if len(points or []) < 3:
            return False
        poly = QPolygonF()
        for px, py in points:
            poly.append(QPointF(float(px), float(py)))
        return poly.containsPoint(QPointF(float(x), float(y)), Qt.FillRule.OddEvenFill)

    def _rooms_for_anchor(self, anchor: dict) -> list:
        if not self._vm:
            return []
        x = float(anchor.get("x_m", anchor.get("x", 0.0)))
        y = float(anchor.get("y_m", anchor.get("y", 0.0)))
        rooms = []
        for zone in self._vm.get_geofence_zones():
            if getattr(zone, "object_type", "zone") != "room":
                continue
            if self._point_in_points(getattr(zone, "points", []), x, y):
                rooms.append(zone)
        return rooms

    def _annotate_anchor_membership(self, anchors) -> list[dict]:
        """Convert canvas scene poses to per-room local anchor poses.

        `room_id` is explicit. Legacy unassigned anchors are associated with the
        first containing room once, then stay with that room even if rooms overlap.

        When loading from file, x_m/y_m may have been overwritten with room-local
        values by a previous annotation pass. In that case we reconstruct the true
        scene position using room_id + local_x_m/y_m before re-annotating.
        """
        annotated = []
        for idx, anchor in enumerate(anchors or []):
            item = self._normalize_anchor_record(anchor, idx)

            # If this anchor has an explicit room_id and local coords stored
            # (i.e. it came from a save-file), reconstruct scene coords from them.
            # Otherwise fall back to whatever x/y the canvas provided.
            existing_room_id = anchor.get("room_id", anchor.get("zone_id", ""))
            has_local = "local_x_m" in anchor and "local_y_m" in anchor
            existing_room = self._find_room(existing_room_id) if existing_room_id else None

            if existing_room is not None and has_local:
                # Use room-local → scene transform to get true scene position
                scene_x, scene_y = self._room_local_to_scene(
                    float(anchor["local_x_m"]),
                    float(anchor["local_y_m"]),
                    existing_room
                )
            else:
                # Canvas provides "x"/"y" as scene coords (set_anchors canvas format)
                # Fallback to x_m/y_m if no canvas coords available
                scene_x = float(anchor.get("x", anchor.get("x_m", 0.0)))
                scene_y = float(anchor.get("y", anchor.get("y_m", 0.0)))

            room = self._find_room(item.get("room_id"))
            if room is None:
                rooms = self._rooms_for_anchor({"x_m": scene_x, "y_m": scene_y})
                room = rooms[0] if rooms else None
            if room is not None:
                local_x, local_y = self._scene_to_room_local(scene_x, scene_y, room)
                item["room_id"] = room.id
                item["zone_id"] = room.id
                item["zone_name"] = room.name
                item["zone_ids"] = [room.id]
                item["zone_names"] = [room.name]
                item["local_x_m"] = local_x
                item["local_y_m"] = local_y
                # Device payload remains backward compatible: x_m/y_m become room-local.
                item["x_m"] = local_x
                item["y_m"] = local_y
            else:
                item["room_id"] = ""
                item["zone_id"] = ""
                item["zone_name"] = ""
                item["zone_ids"] = []
                item["zone_names"] = []
                item["local_x_m"] = scene_x
                item["local_y_m"] = scene_y
                item["x_m"] = scene_x
                item["y_m"] = scene_y
            annotated.append(item)
        return annotated

    def _refresh_anchor_membership_from_canvas(self):
        if not self._vm:
            return
        annotated = self._annotate_anchor_membership(self._canvas.anchor_layout_for_device())
        formatted = self._format_anchors_for_canvas(annotated)
        for idx, anchor in enumerate(formatted):
            if idx < len(self._canvas.anchors):
                self._canvas.anchors[idx].update(
                    {
                        "zone_id": anchor.get("zone_id", ""),
                        "zone_name": anchor.get("zone_name", ""),
                        "zone_ids": list(anchor.get("zone_ids", [])),
                        "zone_names": list(anchor.get("zone_names", [])),
                    }
                )
        if getattr(self._canvas, "dim_tracking_view", False):
            self._draft_anchor_layout = annotated
            self._anchor_layout_commit_pending = not self._same_anchor_layout(
                self._draft_anchor_layout,
                self._geofence_anchor_baseline,
            )
            self._vm.geofence_repo.set_anchors(annotated)
        else:
            self._vm.update_anchor_layout_from_map(annotated)
        self._refresh_anchor_status_label()
        self._canvas.update()

    def _validate_anchor_layout(self, anchors, *, require_four=False) -> tuple[list[str], list[str]]:
        errors = []
        warnings = []
        normalized = [self._normalize_anchor_record(anchor, idx) for idx, anchor in enumerate(anchors or [])]
        ids = [item["anchor_id"] for item in normalized]
        duplicates = sorted({anchor_id for anchor_id in ids if ids.count(anchor_id) > 1})
        if duplicates:
            errors.append("Duplicate anchor ID: " + ", ".join(f"A{x}" for x in duplicates))
        if require_four and len(normalized) < 4:
            errors.append("Anchor layout needs at least 4 anchors for this 1 tag + 4 anchor setup.")
        elif len(normalized) < 4:
            warnings.append("Less than 4 anchors are placed.")
        for item in normalized:
            if not item["placed"]:
                warnings.append(f"{item['label']} is not placed.")
        return errors, warnings

    def _validate_geofence_map(self) -> tuple[list[str], list[str]]:
        errors = []
        warnings = []
        zones = self._vm.get_geofence_zones() if self._vm else []
        rooms = [zone for zone in zones if getattr(zone, "object_type", "zone") == "room"]
        walls = [zone for zone in zones if getattr(zone, "object_type", "zone") == "wall"]
        objects = [zone for zone in zones if getattr(zone, "object_type", "zone") == "object"]
        rule_zones = [zone for zone in zones if getattr(zone, "object_type", "zone") == "zone"]
        if len(rooms) > 4:
            warnings.append("More than 4 Room Zones are allowed in the editor temporarily; verify firmware/runtime support before deployment.")
        active_room_ids = self._vm.get_active_room_ids() if self._vm and hasattr(self._vm, "get_active_room_ids") else []
        if len(active_room_ids) > 4:
            errors.append("Only up to 4 active Room Zones are allowed.")
        if active_room_ids:
            for active_room_id in active_room_ids:
                active_room = next((room for room in rooms if room.id == active_room_id), None)
                if active_room is None:
                    errors.append("Active Room Zone no longer exists.")
                elif not self._room_has_anchor_layout(active_room_id):
                    errors.append(f"Active Room Zone '{active_room.name}' needs at least 3 placed anchors.")
        elif rooms:
            warnings.append("No Active Room Zone is selected.")
        for zone in zones:
            object_type = getattr(zone, "object_type", "zone")
            point_count = len(getattr(zone, "points", []))
            if object_type == "wall" and point_count < 2:
                errors.append(f"{zone.name} wall needs at least 2 points.")
            elif object_type != "wall" and point_count < 3:
                errors.append(f"{zone.name} has fewer than 3 points.")
            if object_type in {"wall", "object"} and zone.max_z <= zone.min_z:
                warnings.append(f"{zone.name} height is not set.")
            if object_type == "object" and getattr(zone, "shape_kind", "polygon") == "circle" and float(getattr(zone, "radius_m", 0.0)) <= 0.0:
                warnings.append(f"{zone.name} circle radius is not set.")
        for wall in walls:
            if getattr(wall, "wall_mode", "free_standing") == "boundary_outside":
                host_room = next((room for room in rooms if room.id == getattr(wall, "host_room_id", None)), None)
                if host_room is None:
                    errors.append(f"{wall.name} is a boundary wall but has no valid host room.")
        for rule_zone in rule_zones:
            if rooms and any(
                not any(self._point_in_points(room.points, pt[0], pt[1]) for room in rooms)
                for pt in getattr(rule_zone, "points", [])
            ):
                warnings.append(f"{rule_zone.name} has points outside defined rooms.")

        annotated_anchors = self._annotate_anchor_membership(self._canvas.anchor_layout_for_device())
        anchor_errors, anchor_warnings = self._validate_anchor_layout(annotated_anchors)
        errors.extend(anchor_errors)
        warnings.extend(anchor_warnings)
        if rooms:
            outside = [a["label"] for a in annotated_anchors if not a.get("zone_id")]
            if outside:
                warnings.append("Anchors outside rooms: " + ", ".join(outside))
        return errors, warnings

    def _refresh_anchor_status_label(self):
        if not hasattr(self.geofence_editor_widget, "lbl_anchor_status"):
            return
        self._refresh_scanned_anchor_status()
        self._refresh_room_anchor_table()
        anchors = self._canvas.anchor_layout_for_device()
        errors, warnings = self._validate_anchor_layout(anchors)
        state = "Draft" if self._anchor_layout_commit_pending else "Synced"
        if errors:
            self.geofence_editor_widget.lbl_anchor_status.setText(f"{state} / Invalid: " + "; ".join(errors))
            self.geofence_editor_widget.lbl_anchor_status.setStyleSheet("color: #FCA5A5; font-weight: bold;")
        elif warnings:
            self.geofence_editor_widget.lbl_anchor_status.setText(f"{state} / Warning: " + "; ".join(warnings))
            self.geofence_editor_widget.lbl_anchor_status.setStyleSheet("color: #FBBF24; font-weight: bold;")
        else:
            self.geofence_editor_widget.lbl_anchor_status.setText(f"{state} / Ready: {len(anchors)} anchors placed")
            self.geofence_editor_widget.lbl_anchor_status.setStyleSheet("color: #34D399; font-weight: bold;")
        self._refresh_active_room_combo()

    def _refresh_room_anchor_table(self):
        table = getattr(self.geofence_editor_widget, "tbl_room_anchors", None)
        if table is None:
            return
        room = self._selected_room()
        table.blockSignals(True)
        try:
            table.setHorizontalHeaderLabels(["Label", "X local (m)", "Y local (m)", "Z (m)"])
            rows = []
            if room is not None:
                for canvas_idx, anchor in enumerate(self._canvas.anchors):
                    assigned_room_id = anchor.get("room_id", anchor.get("zone_id", ""))
                    if assigned_room_id == room.id:
                        local_x, local_y = self._scene_to_room_local(
                            anchor.get("x", 0.0), anchor.get("y", 0.0), room
                        )
                        anchor["local_x_m"] = local_x
                        anchor["local_y_m"] = local_y
                        rows.append((canvas_idx, anchor))
            table.setRowCount(len(rows))
            table._room_anchor_rows = [canvas_idx for canvas_idx, _ in rows]
            for row, (_, anchor) in enumerate(rows):
                values = [
                    str(anchor.get("label", f"A{self._coerce_int_id(anchor.get('anchor_id'), row)}")),
                    f"{float(anchor.get('local_x_m', 0.0)):.3f}",
                    f"{float(anchor.get('local_y_m', 0.0)):.3f}",
                    f"{float(anchor.get('z', anchor.get('z_m', 0.0))):.3f}",
                ]
                for col, text in enumerate(values):
                    item = QTableWidgetItem(text)
                    if col in (1, 2, 3):
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                    table.setItem(row, col, item)
        finally:
            table.blockSignals(False)


    def _refresh_origin_anchor_combobox(self):
        combo = getattr(self.geofence_editor_widget, "cmb_origin_anchor", None)
        if combo is None:
            return
        combo.blockSignals(True)
        current_text = combo.currentText()
        combo.clear()
        combo.addItem("None")
        
        selected_room = None
        if self._vm:
            selected_id = self._canvas.selected_zone_id
            if selected_id:
                zones = self._vm.get_geofence_zones()
                selected_room = next((z for z in zones if z.id == selected_id and getattr(z, "object_type", "zone") == "room"), None)
        
        if selected_room:
            combo.addItem("Room Bottom-Left")
            
        for anchor in self._canvas.anchors:
            label = anchor.get("label", f"A{anchor.get('anchor_id')}")
            combo.addItem(label)
            
        idx = combo.findText(current_text)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else:
            combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def _set_coordinate_origin(self, origin_label):
        """Compatibility entry-point: origin changes are room-local only."""
        room = self._selected_room()
        if room is None:
            return
        if origin_label == "Room Bottom-Left":
            origin_idx = min(range(len(room.points)), key=lambda i: (room.points[i][0], room.points[i][1]))
            room.origin_vertex_idx = origin_idx
            self._canvas.set_room_origin(room.id, room.points[origin_idx])
            self._vm.geofence_layout_updated.emit(self._vm.get_geofence_zones())
            self._refresh_anchor_membership_from_canvas()


    def _on_anchor_table_cell_clicked(self, row, col):
        table = self.geofence_editor_widget.tbl_room_anchors
        rows = getattr(table, "_room_anchor_rows", [])
        if 0 <= row < len(rows):
            canvas_idx = rows[row]
            self._canvas.set_selected_anchor(canvas_idx)
            self._on_canvas_anchor_selected(canvas_idx)
            self._canvas.update()

    def _on_anchor_table_cell_changed(self, row, col):
        if not self._vm:
            return
        table = self.geofence_editor_widget.tbl_room_anchors
        rows = getattr(table, "_room_anchor_rows", [])
        room = self._selected_room()
        if room is None or not (0 <= row < len(rows)):
            return
        table.blockSignals(True)
        try:
            canvas_idx = rows[row]
            anchor = self._canvas.anchors[canvas_idx]
            item = table.item(row, col)
            if item is not None:
                text = item.text().strip()
                if col == 0:
                    anchor["label"] = text or anchor.get("label", f"A{canvas_idx}")
                elif col in (1, 2, 3):
                    try:
                        value = float(text)
                    except ValueError:
                        value = None
                    if value is not None:
                        if col in (1, 2):
                            local_x = value if col == 1 else float(anchor.get("local_x_m", 0.0))
                            local_y = value if col == 2 else float(anchor.get("local_y_m", 0.0))
                            scene_x, scene_y = self._room_local_to_scene(local_x, local_y, room)
                            anchor["local_x_m"] = local_x
                            anchor["local_y_m"] = local_y
                            anchor["x"] = scene_x
                            anchor["y"] = scene_y
                        else:
                            anchor["z"] = value
                            anchor["z_m"] = value
            self._anchor_layout_commit_pending = True
            annotated = self._annotate_anchor_membership(self._canvas.anchor_layout_for_device())
            self._canvas.set_anchors(self._format_anchors_for_canvas(annotated))
            self._vm.geofence_repo.set_anchors(annotated)
            self._refresh_anchor_status_label()
            self._canvas.update()
        finally:
            table.blockSignals(False)

    def _refresh_scanned_anchor_status(self):
        combo = getattr(self.geofence_editor_widget, "cmb_scanned_anchors", None)
        if combo is None:
            return
        placed_ids = {self._coerce_int_id(anchor.get("anchor_id"), -1) for anchor in self._canvas.anchors}
        combo.blockSignals(True)
        try:
            for idx in range(combo.count()):
                data = combo.itemData(idx)
                if not isinstance(data, dict):
                    continue
                anchor_id = self._coerce_int_id(data.get("anchor_id"), idx)
                label = data.get("label", f"A{anchor_id}")
                scanned = "scan" if data.get("is_scanned") else "manual"
                state = "placed" if anchor_id in placed_ids else "unplaced"
                combo.setItemText(idx, f"{label} / 0x{anchor_id:04x} / {scanned} / {state}")
        finally:
            combo.blockSignals(False)

    def _setup_user_map_ui(self):
        self.user_map_groupbox = QGroupBox("Active Geofence Map", self)
        self.user_map_groupbox.setStyleSheet("""
            QGroupBox {
                background-color: rgba(15, 23, 42, 0.90);
                color: #38BDF8;
                font-weight: bold;
                font-family: 'Segoe UI';
                font-size: 13px;
                border: 1px solid rgba(56, 189, 248, 0.35);
                border-radius: 8px;
                padding-top: 15px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                background-color: transparent;
            }
            QComboBox {
                background-color: #1E293B;
                border: 1px solid #475569;
                border-radius: 6px;
                color: #F8FAFC;
                padding: 5px;
                font-size: 12px;
            }
            QComboBox:hover {
                border-color: #38BDF8;
            }
            QComboBox QAbstractItemView {
                background-color: #0F172A;
                color: #F8FAFC;
                selection-background-color: #2563EB;
                border: 1px solid #334155;
            }
            QCheckBox {
                color: #CBD5E1;
                font-weight: bold;
                font-size: 12px;
            }
        """)

        layout = QVBoxLayout(self.user_map_groupbox)
        layout.setContentsMargins(10, 15, 10, 10)
        layout.setSpacing(8)

        self.cmb_user_map = QComboBox(self)
        self.chk_enable_geofence = QCheckBox("Geofence map disabled", self)

        layout.addWidget(self.cmb_user_map)
        layout.addWidget(self.chk_enable_geofence)

        self.chk_enable_geofence.toggled.connect(self._on_enable_geofence_toggled)
        self.chk_enable_geofence.setChecked(True)
        self.cmb_user_map.currentIndexChanged.connect(self._on_user_map_changed)

        self._refresh_map_list()

        self.user_map_groupbox.setParent(self)
        self.user_map_groupbox.setVisible(True)
        self.update_sidebar_geometry()

    def _refresh_map_list(self):
        self.cmb_user_map.clear()

        maps_dir = self._maps_dir()
        if not os.path.exists(maps_dir):
            os.makedirs(maps_dir, exist_ok=True)

        files = [f for f in os.listdir(maps_dir) if f.endswith(".json")]

        default_file = "geofence_map.json"
        if default_file not in files:
            files.insert(0, default_file)

        for f in files:
            full_path = os.path.join(maps_dir, f)
            label = f[:-5] if f.endswith(".json") else f
            self.cmb_user_map.addItem(label, full_path)

    def _app_data_dir(self) -> str:
        app_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(app_dir, "data")

    def _maps_dir(self) -> str:
        return os.path.join(self._app_data_dir(), "maps")

    def _config_dir(self) -> str:
        return os.path.join(self._app_data_dir(), "config")

    def _on_user_map_changed(self, index):
        if index < 0:
            return
        if self.chk_enable_geofence.isChecked():
            file_path = self.cmb_user_map.itemData(index)
            if self._vm and file_path and os.path.exists(file_path):
                if self._vm.load_geofences(file_path):
                    self._canvas.set_geofences(self._vm.get_geofence_zones())
                    self._refresh_active_room_combo()

    def _setup_geofencing_ui(self):
        self.geofence_editor_widget = GeofenceEditorWidget(self)
        self.geofence_page_layout.addWidget(self.geofence_editor_widget)
        self._setup_user_map_ui()

        self.sidebar_stack.setCurrentIndex(0)
        self.user_map_groupbox.setVisible(False)
        self._canvas._show_scale_bar = False
        self._canvas._show_mouse_coords = False
        self._canvas.is_developer_mode = False

        editor = self.geofence_editor_widget
        for name in ("editor_header", "tool_palette", "help_frame", "io_frame"):
            widget = getattr(editor, name, None)
            if widget is not None:
                widget.setVisible(False)
        if hasattr(editor, "status_strip"):
            editor.status_strip.setVisible(True)
        if hasattr(editor, "lbl_section_title"):
            editor.lbl_section_title.setText("PROPERTY")
        self._setup_anchor_authoring_controls(editor)
        self._install_property_extensions(editor)
        self._wire_structure_autosave(editor)
        editor.editor_tabs.currentChanged.connect(self._on_editor_tab_changed)
        editor.btn_mode_room.clicked.connect(lambda: self._set_editor_tool("room", "draw"))
        editor.btn_mode_wall.clicked.connect(lambda: self._set_editor_tool("wall", "draw"))
        if hasattr(editor, "btn_mode_object"):
            editor.btn_mode_object.clicked.connect(lambda: self._set_editor_tool("object", "draw"))
        editor.btn_mode_edit_map.clicked.connect(lambda: self._set_editor_mode("edit_vertices"))
        editor.btn_mode_draw.clicked.connect(lambda: self._set_editor_tool("zone", "draw"))
        editor.btn_mode_edit.clicked.connect(lambda: self._set_editor_mode("edit_vertices"))
        self._setup_geofence_shortcuts()
        editor.sb_grid_spacing.valueChanged.connect(self._update_grid_settings)
        editor.sb_grid_subdivisions.valueChanged.connect(self._update_grid_settings)
        # Apply the destructive geometry snap only after the user commits a grid value.
        # This avoids repeatedly rounding walls while a spin box is being adjusted.
        editor.sb_grid_spacing.editingFinished.connect(self._snap_existing_walls_to_grid)
        editor.sb_grid_subdivisions.editingFinished.connect(self._snap_existing_walls_to_grid)
        editor.cmb_map_type.currentIndexChanged.connect(self._sync_map_height_visibility)
        if hasattr(editor, "cmb_object_shape"):
            editor.cmb_object_shape.currentIndexChanged.connect(self._sync_map_height_visibility)
        if hasattr(editor, "cmb_object_subtype"):
            editor.cmb_object_subtype.currentIndexChanged.connect(self._sync_map_height_visibility)
        if hasattr(editor, "cmb_object_direction"):
            editor.cmb_object_direction.currentIndexChanged.connect(self._sync_map_height_visibility)
        editor.cmb_zone_type.currentIndexChanged.connect(self._sync_rule_speed_visibility)
        editor.btn_apply_properties.clicked.connect(self._apply_zone_properties)
        editor.btn_save_map.clicked.connect(self._save_map)
        editor.btn_clear_map.clicked.connect(self._clear_map)
        editor.btn_exit_editor.clicked.connect(self._exit_geofence_editor)

        self._canvas.polygon_completed.connect(self._on_canvas_polygon_completed)
        self._canvas.zone_selected.connect(self._on_canvas_zone_selected)
        self._canvas.zone_modified.connect(self._on_canvas_zone_modified)
        self._canvas.zone_properties_updated.connect(self._on_canvas_zone_properties_updated)
        self._canvas.anchor_selected.connect(self._on_canvas_anchor_selected)
        self._canvas.anchor_layout_edited.connect(self._on_canvas_anchor_layout_edited)
        self._canvas.room_origin_vertex_picked.connect(self._on_canvas_room_origin_vertex_picked)
        self._canvas.edit_operation_started.connect(lambda: self._push_undo_state("canvas edit"))

        self._apply_geofence_config(self._load_geofence_config().get("editor_settings", {}))
        self._update_grid_settings()
        self._sync_map_height_visibility()
        self._sync_rule_speed_visibility()
        self._set_context_help("room")
        self._set_editor_tool("room", "draw")

    def _install_property_extensions(self, editor):
        """Add inspector controls without relying on hard-coded QFormLayout rows."""
        def install_color_picker(target, form_layout, hidden_field_name, button_name, parent, default_color):
            button = getattr(editor, button_name, None)
            hidden_field = getattr(editor, hidden_field_name, None)
            if button is None:
                button = QPushButton("Choose color…", parent)
                setattr(editor, button_name, button)
                if hidden_field is not None:
                    row, _role = form_layout.getWidgetPosition(hidden_field)
                    if row >= 0:
                        form_layout.setWidget(row, QFormLayout.ItemRole.FieldRole, button)
                    else:
                        form_layout.addRow("Color", button)
                else:
                    form_layout.addRow("Color", button)
            if hidden_field is not None:
                hidden_field.hide()
            button.setMinimumHeight(25)
            if not getattr(button, "_color_picker_connected", False):
                button.clicked.connect(lambda _checked=False, kind=target: self._choose_editor_color(kind))
                button._color_picker_connected = True
            if not hasattr(editor, f"_{target}_color"):
                setattr(editor, f"_{target}_color", default_color)

        install_color_picker("map", editor.map_properties_form_layout, "txt_map_color", "btn_map_color_picker", editor.gb_map_properties, "#F8FAFC")
        install_color_picker("zone", editor.properties_form_layout, "txt_zone_color", "btn_zone_color_picker", editor.gb_properties, "#22C55E")
        if editor.cmb_map_type.findText("Object") < 0:
            editor.cmb_map_type.addItem("Object")
        if not hasattr(editor, "cmb_wall_mode"):
            editor.cmb_wall_mode = QComboBox(editor.gb_map_properties)
            editor.cmb_wall_mode.addItem("Boundary outside room", "boundary_outside")
            editor.cmb_wall_mode.addItem("Internal partition", "internal_partition")
            editor.cmb_wall_mode.addItem("Free-standing", "free_standing")
            editor.cmb_wall_host_room = QComboBox(editor.gb_map_properties)
            editor.map_properties_form_layout.addRow("Wall behavior", editor.cmb_wall_mode)
            editor.map_properties_form_layout.addRow("Host room", editor.cmb_wall_host_room)
        if not hasattr(editor, "cmb_object_shape"):
            editor.cmb_object_shape = QComboBox(editor.gb_map_properties)
            editor.cmb_object_shape.addItem("Polygon", "polygon")
            editor.cmb_object_shape.addItem("Circle", "circle")
            editor.lbl_object_shape = QLabel("Shape", editor.gb_map_properties)
            editor.lbl_object_radius = QLabel("Radius", editor.gb_map_properties)
            editor.sb_object_radius = QDoubleSpinBox(editor.gb_map_properties)
            editor.sb_object_radius.setRange(0.01, 100.0)
            editor.sb_object_radius.setSingleStep(0.05)
            editor.sb_object_radius.setSuffix(" m")
            editor.map_properties_form_layout.addRow(editor.lbl_object_shape, editor.cmb_object_shape)
            editor.map_properties_form_layout.addRow(editor.lbl_object_radius, editor.sb_object_radius)
        if not hasattr(editor, "cmb_object_subtype"):
            editor.cmb_object_subtype = QComboBox(editor.gb_map_properties)
            editor.cmb_object_subtype.addItem("Generic", "generic")
            editor.cmb_object_subtype.addItem("Stairs", "stairs")
            editor.lbl_object_subtype = QLabel("Object kind", editor.gb_map_properties)
            editor.map_properties_form_layout.addRow(editor.lbl_object_subtype, editor.cmb_object_subtype)
        if not hasattr(editor, "cmb_object_direction"):
            editor.cmb_object_direction = QComboBox(editor.gb_map_properties)
            editor.cmb_object_direction.addItem("Up", "up")
            editor.cmb_object_direction.addItem("Down", "down")
            editor.lbl_object_direction = QLabel("Direction", editor.gb_map_properties)
            editor.map_properties_form_layout.addRow(editor.lbl_object_direction, editor.cmb_object_direction)
        if not hasattr(editor, "btn_insert_vertex"):
            editor.btn_insert_vertex = QPushButton("Add Point on Edge", editor.gb_map_properties)
            editor.btn_insert_vertex.setToolTip("Select a polygon, then click one of its edges to insert a new vertex.")
            editor.map_properties_form_layout.addRow("Geometry", editor.btn_insert_vertex)
            editor.btn_insert_vertex.clicked.connect(self._begin_insert_vertex)
        if not hasattr(editor, "lbl_map_geometry"):
            editor.lbl_map_geometry = QLabel(editor.tab_map_layout)
            editor.lbl_map_geometry.setWordWrap(True)
            editor.lbl_map_geometry.setStyleSheet("color: #93C5FD; font-family: Consolas; padding: 5px;")
            editor.map_tab_layout.insertWidget(1, editor.lbl_map_geometry)
        if not hasattr(editor, "lbl_zone_geometry"):
            editor.lbl_zone_geometry = QLabel(editor.tab_rule_zones)
            editor.lbl_zone_geometry.setWordWrap(True)
            editor.lbl_zone_geometry.setStyleSheet("color: #93C5FD; font-family: Consolas; padding: 5px;")
            editor.rule_tab_layout.insertWidget(1, editor.lbl_zone_geometry)
        self._set_editor_color("map", getattr(editor, "_map_color", "#F8FAFC"))
        self._set_editor_color("zone", getattr(editor, "_zone_color", "#22C55E"))

    def _wire_structure_autosave(self, editor):
        if getattr(editor, "_structure_autosave_wired", False):
            return
        editor._structure_autosave_wired = True

        def commit_structure(_value=None):
            self._auto_apply_map_properties()

        if hasattr(editor, "txt_map_name"):
            editor.txt_map_name.editingFinished.connect(commit_structure)
        for widget_name in ("cmb_map_type", "cmb_wall_mode", "cmb_wall_host_room", "cmb_object_shape", "cmb_object_subtype", "cmb_object_direction"):
            widget = getattr(editor, widget_name, None)
            if widget is not None:
                widget.currentIndexChanged.connect(commit_structure)
        for widget_name in ("sb_map_height", "sb_wall_thickness", "sb_object_radius"):
            widget = getattr(editor, widget_name, None)
            if widget is not None:
                widget.valueChanged.connect(commit_structure)

    def _set_editor_color(self, target, color, *, auto_apply=True):
        color = self._valid_hex_color_or(color, "#F8FAFC" if target == "map" else "#22C55E")
        editor = self.geofence_editor_widget
        setattr(editor, f"_{target}_color", color)
        button = getattr(editor, f"btn_{target}_color_picker", None)
        if button is not None:
            button.setStyleSheet(
                f"QPushButton {{ background: {color}; color: {'#0F172A' if QColor(color).lightness() > 150 else '#F8FAFC'}; "
                "border: 1px solid #64748B; border-radius: 5px; font-weight: bold; }}"
            )
            button.setText("Choose color…")
        if auto_apply and target == "map":
            self._auto_apply_map_properties()

    def _capture_geofence_config(self) -> dict:
        editor = self.geofence_editor_widget
        settings = {
            "grid_spacing_m": float(editor.sb_grid_spacing.value()),
            "grid_subdivisions": int(editor.sb_grid_subdivisions.value()),
            "map_color": self._valid_hex_color_or(getattr(editor, "_map_color", "#F8FAFC"), "#F8FAFC"),
            "zone_color": self._valid_hex_color_or(getattr(editor, "_zone_color", "#22C55E"), "#22C55E"),
            "map_name": editor.txt_map_name.text().strip(),
            "map_type": editor.cmb_map_type.currentText().strip(),
            "map_height_m": float(editor.sb_map_height.value()),
            "rule_name": editor.txt_zone_name.text().strip(),
            "rule_type_index": int(editor.cmb_zone_type.currentIndex()),
            "rule_speed_mps": float(editor.sb_speed.value()),
        }
        optional_spin_boxes = {
            "wall_thickness_m": "sb_wall_thickness",
            "object_radius_m": "sb_object_radius",
        }
        for key, widget_name in optional_spin_boxes.items():
            widget = getattr(editor, widget_name, None)
            if widget is not None:
                settings[key] = float(widget.value())
        optional_combos = {
            "wall_mode": "cmb_wall_mode",
            "wall_host_room_id": "cmb_wall_host_room",
            "object_shape": "cmb_object_shape",
            "object_subtype": "cmb_object_subtype",
            "object_direction": "cmb_object_direction",
        }
        for key, widget_name in optional_combos.items():
            widget = getattr(editor, widget_name, None)
            if widget is not None:
                settings[key] = widget.currentData() or ""
        return settings

    def _geofence_config_path(self) -> str:
        return os.path.join(self._config_dir(), "geofence_config.json")

    def _load_geofence_config(self) -> dict:
        path = self._geofence_config_path()
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not load geofence config from %s: %s", path, exc)
            return {}

    def _save_geofence_config(self) -> None:
        path = self._geofence_config_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {"editor_settings": self._capture_geofence_config()}
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except OSError as exc:
            log.warning("Could not save geofence config to %s: %s", path, exc)

    def _set_combo_data_or_text(self, combo, value) -> None:
        if combo is None or value is None:
            return
        idx = combo.findData(value)
        if idx < 0:
            idx = combo.findText(str(value), Qt.MatchFlag.MatchFixedString)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _apply_geofence_config(self, settings: dict) -> None:
        if not settings:
            return
        editor = self.geofence_editor_widget
        widgets_to_block = [
            editor.sb_grid_spacing,
            editor.sb_grid_subdivisions,
            editor.txt_map_name,
            editor.cmb_map_type,
            editor.sb_map_height,
            editor.txt_zone_name,
            editor.cmb_zone_type,
            editor.sb_speed,
        ]
        for widget_name in ("sb_wall_thickness", "cmb_wall_mode", "cmb_wall_host_room", "cmb_object_shape", "cmb_object_subtype", "cmb_object_direction", "sb_object_radius"):
            widget = getattr(editor, widget_name, None)
            if widget is not None:
                widgets_to_block.append(widget)

        for widget in widgets_to_block:
            widget.blockSignals(True)
        try:
            if "grid_spacing_m" in settings:
                editor.sb_grid_spacing.setValue(float(settings["grid_spacing_m"]))
            if "grid_subdivisions" in settings:
                editor.sb_grid_subdivisions.setValue(int(settings["grid_subdivisions"]))
            if "map_name" in settings:
                editor.txt_map_name.setText(str(settings["map_name"]))
            self._set_combo_data_or_text(editor.cmb_map_type, settings.get("map_type"))
            if "map_height_m" in settings:
                editor.sb_map_height.setValue(float(settings["map_height_m"]))
            if "rule_name" in settings:
                editor.txt_zone_name.setText(str(settings["rule_name"]))
            if "rule_type_index" in settings:
                editor.cmb_zone_type.setCurrentIndex(max(0, min(int(settings["rule_type_index"]), editor.cmb_zone_type.count() - 1)))
            if "rule_speed_mps" in settings:
                editor.sb_speed.setValue(float(settings["rule_speed_mps"]))
            if hasattr(editor, "sb_wall_thickness") and "wall_thickness_m" in settings:
                editor.sb_wall_thickness.setValue(float(settings["wall_thickness_m"]))
            self._set_combo_data_or_text(getattr(editor, "cmb_wall_mode", None), settings.get("wall_mode"))
            self._refresh_wall_host_rooms(settings.get("wall_host_room_id"))
            self._set_combo_data_or_text(getattr(editor, "cmb_wall_host_room", None), settings.get("wall_host_room_id"))
            self._set_combo_data_or_text(getattr(editor, "cmb_object_shape", None), settings.get("object_shape"))
            self._set_combo_data_or_text(getattr(editor, "cmb_object_subtype", None), settings.get("object_subtype"))
            self._set_combo_data_or_text(getattr(editor, "cmb_object_direction", None), settings.get("object_direction"))
            if hasattr(editor, "sb_object_radius") and "object_radius_m" in settings:
                editor.sb_object_radius.setValue(float(settings["object_radius_m"]))
        finally:
            for widget in widgets_to_block:
                widget.blockSignals(False)

        self._set_editor_color("map", settings.get("map_color", getattr(editor, "_map_color", "#F8FAFC")), auto_apply=False)
        self._set_editor_color("zone", settings.get("zone_color", getattr(editor, "_zone_color", "#22C55E")), auto_apply=False)

    def _choose_editor_color(self, target):
        current = getattr(self.geofence_editor_widget, f"_{target}_color", "#F8FAFC")
        color = QColorDialog.getColor(QColor(current), self, "Select display color")
        if color.isValid():
            self._set_editor_color(target, color.name().upper())
            self._save_geofence_config()
            if target == "map":
                self._auto_apply_map_properties()

    @staticmethod
    def _polygon_metrics(points, closed=True):
        pts = list(points or [])
        if len(pts) < 2:
            return 0.0, 0.0, []
        edge_count = len(pts) if closed and len(pts) >= 3 else len(pts) - 1
        edges = []
        perimeter = 0.0
        for idx in range(edge_count):
            x1, y1 = pts[idx]
            x2, y2 = pts[(idx + 1) % len(pts)]
            length = math.hypot(x2 - x1, y2 - y1)
            angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
            edges.append((length, angle))
            perimeter += length
        area = 0.0
        if closed and len(pts) >= 3:
            area = abs(sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts))) * 0.5)
        return area, perimeter, edges

    def _update_geometry_inspector(self, zone=None):
        editor = self.geofence_editor_widget
        if zone is None and self._vm and self._canvas.selected_zone_id:
            zone = next((z for z in self._vm.get_geofence_zones() if z.id == self._canvas.selected_zone_id), None)
        if zone is None:
            if hasattr(editor, "lbl_map_geometry"):
                editor.lbl_map_geometry.setText("Select an object to inspect dimensions.")
            if hasattr(editor, "lbl_zone_geometry"):
                editor.lbl_zone_geometry.setText("Select a rule zone to inspect dimensions.")
            return

        object_type = getattr(zone, "object_type", "zone")
        area, perimeter, edges = self._polygon_metrics(zone.points, closed=object_type != "wall")
        visible_edges = edges[:6]
        edge_parts = [f"S{i + 1} {length:.2f}m @ {angle:.0f}deg" for i, (length, angle) in enumerate(visible_edges)]
        if len(edges) > len(visible_edges):
            edge_parts.append(f"+{len(edges) - len(visible_edges)} more")
        edge_lines = "Edges: " + " | ".join(edge_parts) if edge_parts else ""

        if object_type == "wall":
            text = (
                f"Length {perimeter:.2f} m | Height {max(0.0, zone.max_z - zone.min_z):.2f} m | "
                f"Thickness {float(getattr(zone, 'thickness_m', 0.1)):.2f} m"
            )
            if edge_lines:
                text += f"\n{edge_lines}"
            editor.lbl_map_geometry.setText(text)
        elif object_type == "object":
            shape_kind = getattr(zone, "shape_kind", "polygon")
            object_subtype = getattr(zone, "object_subtype", "generic")
            radius = float(getattr(zone, "radius_m", 0.0))
            text = (
                f"{object_subtype.title()} | {shape_kind.title()} | H {max(0.0, zone.max_z - zone.min_z):.2f} m | R {radius:.2f} m\n"
                f"Area {area:.2f} m2 | Perimeter {perimeter:.2f} m"
            )
            if edge_lines:
                text += f"\n{edge_lines}"
            editor.lbl_map_geometry.setText(text)
        else:
            text = f"Area {area:.2f} m2 | Perimeter {perimeter:.2f} m"
            if edge_lines:
                text += f"\n{edge_lines}"
            target = editor.lbl_zone_geometry if object_type == "zone" else editor.lbl_map_geometry
            target.setText(text)

    def _refresh_wall_host_rooms(self, selected_host_id=None):
        editor = self.geofence_editor_widget
        combo = getattr(editor, "cmb_wall_host_room", None)
        if combo is None:
            return
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem("None", "")
            if self._vm:
                for room in self._vm.get_geofence_zones():
                    if getattr(room, "object_type", "zone") == "room":
                        combo.addItem(room.name, room.id)
            idx = combo.findData(selected_host_id or "")
            combo.setCurrentIndex(max(idx, 0))
        finally:
            combo.blockSignals(False)

    def _room_has_anchor_layout(self, room_id: str, min_anchors: int = 3) -> bool:
        if not room_id:
            return False
        anchors = self._annotate_anchor_membership(self._canvas.anchor_layout_for_device()) if self._vm else []
        count = 0
        for anchor in anchors:
            if not anchor.get("placed", True):
                continue
            zone_ids = set(anchor.get("zone_ids", []))
            if anchor.get("room_id") == room_id or anchor.get("zone_id") == room_id or room_id in zone_ids:
                count += 1
        return count >= min_anchors

    def _refresh_active_room_combo(self):
        editor = getattr(self, "geofence_editor_widget", None)
        checks_layout = getattr(editor, "active_room_checks_layout", None)
        if checks_layout is None:
            return
        active_ids = self._vm.get_active_room_ids() if self._vm and hasattr(self._vm, "get_active_room_ids") else []
        if hasattr(self._canvas, "set_active_room_ids"):
            self._canvas.set_active_room_ids(active_ids)
        while checks_layout.count():
            item = checks_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not self._vm:
            return
        for room in self._room_zones():
            has_layout = self._room_has_anchor_layout(room.id)
            check = QCheckBox(room.name + ("" if has_layout else " (needs anchors)"), editor.gb_anchor_layout)
            check.setProperty("room_id", room.id)
            check.setChecked(room.id in active_ids)
            check.setEnabled(has_layout or room.id in active_ids)
            check.toggled.connect(self._on_active_room_toggled)
            checks_layout.addWidget(check)

    def _on_active_room_toggled(self, checked):
        if not self._vm:
            return
        check = self.sender()
        room_id = check.property("room_id") if check is not None else ""
        room_name = check.text().replace(" (needs anchors)", "") if check is not None else "Room Zone"
        current_ids = self._vm.get_active_room_ids() if hasattr(self._vm, "get_active_room_ids") else []
        if checked:
            if room_id in current_ids:
                return
            if len(current_ids) >= 4:
                QMessageBox.warning(self, "Active Room Limit", "Only up to 4 Room Zones can be active.")
                check.blockSignals(True)
                check.setChecked(False)
                check.blockSignals(False)
                return
            if not self._room_has_anchor_layout(room_id):
                QMessageBox.warning(
                    self,
                    "Active Room Requires Anchors",
                    f"Add at least 3 placed anchors to '{room_name}' before making it active.",
                )
                check.blockSignals(True)
                check.setChecked(False)
                check.blockSignals(False)
                return
            current_ids.append(room_id)
        else:
            current_ids = [rid for rid in current_ids if rid != room_id]
        self._vm.set_active_room_ids(current_ids)
        if hasattr(self._canvas, "set_active_room_ids"):
            self._canvas.set_active_room_ids(current_ids)

    def _setup_geofence_shortcuts(self):
        shortcuts = [
            ("1", lambda: self._set_editor_tool("room", "draw")),
            ("R", lambda: self._set_editor_tool("room", "draw")),
            ("2", lambda: self._set_editor_tool("wall", "draw")),
            ("W", lambda: self._set_editor_tool("wall", "draw")),
            ("3", lambda: self._set_editor_tool("zone", "draw")),
            ("Z", lambda: self._set_editor_tool("zone", "draw")),
            ("4", lambda: self._set_editor_tool("object", "draw")),
            ("O", lambda: self._set_editor_tool("object", "draw")),
            ("5", lambda: self._set_editor_mode("edit_vertices")),
            ("6", lambda: self._set_view_only_mode()),
            ("V", lambda: self._preview_overlay_btn.click()),
            ("Delete", lambda: self._delete_selected_zone()),
            ("Ctrl+Z", lambda: self._undo_geofence()),
            ("Ctrl+Y", lambda: self._redo_geofence()),
            ("Ctrl+C", lambda: self._copy_selected_zone()),
            ("Ctrl+X", lambda: self._cut_selected_zone()),
            ("Ctrl+V", lambda: self._paste_zone()),
        ]
        self._geofence_shortcuts = []
        for key, handler in shortcuts:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(handler)
            self._geofence_shortcuts.append(shortcut)

    def _capture_geofence_state(self):
        zones = self._vm.get_geofence_zones() if self._vm else []
        return {
            "zones": [zone.to_dict() for zone in zones],
            "anchors": [dict(anchor) for anchor in self._canvas.anchor_layout_for_device()],
            "selected_zone_id": self._canvas.selected_zone_id,
            "selected_anchor_idx": self._canvas.selected_anchor_idx,
        }

    def _state_signature(self, state):
        return repr(state.get("zones", [])) + repr(state.get("anchors", []))

    def _push_undo_state(self, _reason="edit"):
        if self._restoring_geofence_state or not self._vm:
            return
        state = self._capture_geofence_state()
        if self._undo_stack and self._state_signature(self._undo_stack[-1]) == self._state_signature(state):
            return
        self._undo_stack.append(state)
        if len(self._undo_stack) > 80:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def _restore_geofence_state(self, state):
        if not self._vm:
            return
        self._restoring_geofence_state = True
        try:
            zones = [GeofenceZone.from_dict(item) for item in state.get("zones", [])]
            self._vm.geofence_repo.clear()
            for zone in zones:
                self._vm.geofence_repo.add_zone(zone)
            self._canvas.set_geofences(zones)
            anchors = [dict(anchor) for anchor in state.get("anchors", [])]
            self._vm.geofence_repo.set_anchors(anchors)
            self._canvas.set_anchors(self._format_anchors_for_canvas(anchors))
            self._draft_anchor_layout = self._annotate_anchor_membership(anchors)
            self._canvas.set_selected_zone(state.get("selected_zone_id"))
            selected_anchor_idx = state.get("selected_anchor_idx")
            if selected_anchor_idx is not None and 0 <= selected_anchor_idx < len(self._canvas.anchors):
                self._canvas.set_selected_anchor(selected_anchor_idx)
            self._vm.geofence_layout_updated.emit(self._vm.get_geofence_zones())
            self._refresh_anchor_status_label()
            self._refresh_room_anchor_table()
        finally:
            self._restoring_geofence_state = False

    def _undo_geofence(self):
        if not self._undo_stack or not self._vm:
            return
        self._redo_stack.append(self._capture_geofence_state())
        self._restore_geofence_state(self._undo_stack.pop())

    def _redo_geofence(self):
        if not self._redo_stack or not self._vm:
            return
        self._undo_stack.append(self._capture_geofence_state())
        self._restore_geofence_state(self._redo_stack.pop())

    def _setup_anchor_authoring_controls(self, editor):
        # --- Anchor Layout GroupBox (only created once) ---
        if not hasattr(editor, "gb_anchor_layout"):
            editor.gb_anchor_layout = QGroupBox("Room Anchor Layout", editor)
            editor.anchor_layout_outer = QVBoxLayout(editor.gb_anchor_layout)
            editor.anchor_layout_outer.setContentsMargins(8, 10, 8, 8)
            editor.anchor_layout_outer.setSpacing(6)
            editor.map_tab_layout.addWidget(editor.gb_anchor_layout)

        if not hasattr(editor, "active_room_checks_layout"):
            editor.lbl_active_room_zone = QLabel("Active rooms (max 4)", editor.gb_anchor_layout)
            editor.lbl_active_room_zone.setToolTip("Tick up to 4 Room Zones for runtime use. Each active room needs at least 3 placed anchors.")
            editor.anchor_layout_outer.addWidget(editor.lbl_active_room_zone)
            editor.active_room_checks_layout = QVBoxLayout()
            editor.active_room_checks_layout.setSpacing(2)
            editor.anchor_layout_outer.addLayout(editor.active_room_checks_layout)

        # --- Anchor action buttons row: Add | Remove | Set Corner ---
        if not hasattr(editor, "btn_add_anchor"):
            btn_row = QHBoxLayout()
            btn_row.setSpacing(4)
            editor.btn_add_anchor = QPushButton("Add Anchor", editor.gb_anchor_layout)
            editor.btn_remove_anchor = QPushButton("Remove", editor.gb_anchor_layout)
            editor.btn_set_corner = QPushButton("Set Local Origin", editor.gb_anchor_layout)
            for btn in (editor.btn_add_anchor, editor.btn_remove_anchor, editor.btn_set_corner):
                btn.setMinimumHeight(28)
            editor.btn_set_corner.setToolTip("Chọn vertex làm gốc local (0,0); không dịch geometry canvas")
            btn_row.addWidget(editor.btn_add_anchor)
            btn_row.addWidget(editor.btn_remove_anchor)
            btn_row.addWidget(editor.btn_set_corner)
            editor.anchor_layout_outer.addLayout(btn_row)
            editor.btn_add_anchor.clicked.connect(self._add_anchor)
            editor.btn_remove_anchor.clicked.connect(self._remove_selected_anchor)
            editor.btn_set_corner.clicked.connect(self._on_btn_set_corner_clicked)

        # --- Anchor table ---
        if not hasattr(editor, "tbl_room_anchors"):
            editor.tbl_room_anchors = QTableWidget(editor.gb_anchor_layout)
            editor.tbl_room_anchors.setColumnCount(4)
            editor.tbl_room_anchors.setHorizontalHeaderLabels(["Label", "X local (m)", "Y local (m)", "Z (m)"])
            editor.tbl_room_anchors.verticalHeader().setVisible(False)
            editor.tbl_room_anchors.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            editor.tbl_room_anchors.setEditTriggers(
                QAbstractItemView.EditTrigger.DoubleClicked |
                QAbstractItemView.EditTrigger.EditKeyPressed |
                QAbstractItemView.EditTrigger.AnyKeyPressed
            )
            editor.tbl_room_anchors.setAlternatingRowColors(True)
            editor.tbl_room_anchors.setMinimumHeight(160)
            editor.tbl_room_anchors.setStyleSheet(
                "QTableWidget { background: #090F1D; color: #E5E7EB; border: 1px solid #334155; "
                "border-radius: 6px; gridline-color: #1F2937; selection-background-color: #1D4ED8; }"
                "QHeaderView::section { background: #111827; color: #93C5FD; border: none; "
                "border-right: 1px solid #334155; padding: 4px; font-weight: bold; }"
            )
            hdr = editor.tbl_room_anchors.horizontalHeader()
            hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            for col in (1, 2, 3):
                hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
            editor.anchor_layout_outer.addWidget(editor.tbl_room_anchors)
            editor.tbl_room_anchors.cellChanged.connect(self._on_anchor_table_cell_changed)
            editor.tbl_room_anchors.cellClicked.connect(self._on_anchor_table_cell_clicked)

        # --- Anchor status label ---
        if not hasattr(editor, "lbl_anchor_status"):
            editor.lbl_anchor_status = QLabel("No anchors placed", editor.gb_anchor_layout)
            editor.lbl_anchor_status.setWordWrap(True)
            editor.lbl_anchor_status.setStyleSheet("color: #94A3B8; font-weight: bold;")
            editor.anchor_layout_outer.addWidget(editor.lbl_anchor_status)

        # --- Keep legacy hidden widgets for code compatibility ---
        if not hasattr(editor, "cmb_scanned_anchors"):
            editor.cmb_scanned_anchors = QComboBox(editor.gb_anchor_layout)
            editor.cmb_scanned_anchors.addItem("Manual (No Link)", None)
            editor.cmb_scanned_anchors.setVisible(False)
            self._on_scan_devices_updated([])
        if not hasattr(editor, "sb_anchor_x"):
            editor.sb_anchor_x = QDoubleSpinBox(editor.gb_anchor_layout)
            editor.sb_anchor_y = QDoubleSpinBox(editor.gb_anchor_layout)
            editor.sb_anchor_z = QDoubleSpinBox(editor.gb_anchor_layout)
            for spin in (editor.sb_anchor_x, editor.sb_anchor_y, editor.sb_anchor_z):
                spin.setRange(-1000.0, 1000.0); spin.setDecimals(3); spin.setSuffix(" m")
                spin.setVisible(False)
        if not hasattr(editor, "btn_create_default_anchors"):
            editor.btn_create_default_anchors = QPushButton(editor.gb_anchor_layout)
            editor.btn_create_default_anchors.setVisible(False)
        if not hasattr(editor, "btn_assign_anchor"):
            editor.btn_assign_anchor = QPushButton(editor.gb_anchor_layout)
            editor.btn_assign_anchor.setVisible(False)

        # --- Device sync and Load/Save buttons ---
        if not hasattr(editor, "btn_read_layout_dev"):
            sync_parent_layout = getattr(editor, "io_layout", None) or getattr(editor, "editor_content_layout", editor.main_layout)
            if sync_parent_layout is not None:
                sync_gb = QGroupBox("Device Layout Sync", editor)
                editor.gb_device_sync = sync_gb
                sync_layout = QVBoxLayout(sync_gb)
                sync_layout.setContentsMargins(6, 10, 6, 6)
                sync_layout.setSpacing(8)
                editor.cmb_device_target = QComboBox(sync_gb)
                editor.cmb_device_target.addItem("Tag / MCU (0x0001)", {"dst_addr": 1, "role": "tag"})
                editor.btn_read_layout_dev = QPushButton("Read from Tag", sync_gb)
                editor.btn_read_layout_dev.setStyleSheet("background: #0284C7; color: white; border: 1px solid #0369A1; font-weight: bold; padding: 6px;")
                editor.btn_write_layout_dev = QPushButton("Write to Tag", sync_gb)
                editor.btn_write_layout_dev.setStyleSheet("background: #0D9488; color: white; border: 1px solid #0F766E; font-weight: bold; padding: 6px;")
                sync_buttons = QHBoxLayout()
                sync_buttons.addWidget(editor.btn_read_layout_dev)
                sync_buttons.addWidget(editor.btn_write_layout_dev)
                sync_layout.addWidget(editor.cmb_device_target)
                sync_layout.addLayout(sync_buttons)
                idx = sync_parent_layout.indexOf(editor.btn_save_map)
                if idx >= 0:
                    sync_parent_layout.insertWidget(idx, sync_gb)
                else:
                    sync_parent_layout.addWidget(sync_gb)
                editor.btn_read_layout_dev.clicked.connect(self._read_layout_from_device)
                editor.btn_write_layout_dev.clicked.connect(self._write_layout_to_device)
                sync_gb.setVisible(False)

        if not hasattr(editor, "btn_load_map"):
            editor.btn_load_map = QPushButton("Load Map JSON", editor)
            editor.btn_load_map.setStyleSheet(
                "background: #334155; color: white; border: 1px solid #475569; "
                "font-weight: bold; padding: 6px;"
            )
            parent_layout = editor.btn_save_map.parentWidget().layout() if editor.btn_save_map.parentWidget() else None
            if parent_layout:
                save_idx = parent_layout.indexOf(editor.btn_save_map)
                parent_layout.insertWidget(max(save_idx, 0), editor.btn_load_map)
            else:
                editor.map_tab_layout.addWidget(editor.btn_load_map)
            editor.btn_load_map.clicked.connect(self._load_map)

        self._sync_map_height_visibility()
        self._refresh_anchor_status_label()
        self._refresh_room_anchor_table()

    def _apply_anchor_template_from_combo(self):
        if not hasattr(self.geofence_editor_widget, "cmb_scanned_anchors"):
            return
        data = self.geofence_editor_widget.cmb_scanned_anchors.currentData()
        template = dict(data) if isinstance(data, dict) else {}
        room = self._selected_room()
        if room is not None:
            template["room_id"] = room.id
            template["zone_id"] = room.id
            template["zone_name"] = room.name
        self._canvas.set_anchor_template(template)

    def _selected_device_target(self):
        if hasattr(self.geofence_editor_widget, "cmb_device_target"):
            data = self.geofence_editor_widget.cmb_device_target.currentData()
            if isinstance(data, dict):
                return data
        return {"dst_addr": 1, "role": "tag"}

    def _selected_room_zone(self):
        if not self._vm or not self._canvas.selected_zone_id:
            return None
        return next(
            (
                zone
                for zone in self._vm.get_geofence_zones()
                if zone.id == self._canvas.selected_zone_id and getattr(zone, "object_type", "zone") == "room"
            ),
            None,
        )

    def _room_center(self, room):
        points = list(getattr(room, "points", []) or [])
        if not points:
            return getattr(self._canvas, "_view_cx", 0.0), getattr(self._canvas, "_view_cy", 0.0)
        return (
            sum(pt[0] for pt in points) / len(points),
            sum(pt[1] for pt in points) / len(points),
        )

    def _require_selected_room_for_anchor(self):
        room = self._selected_room()
        if room is None:
            QMessageBox.warning(
                self,
                "Select Room Zone",
                "Select a Room Zone first. Anchor layout is configured from the Room Zone property panel.",
            )
        return room

    def _valid_hex_color_or(self, text: str, fallback: str) -> str:
        color = (text or "").strip()
        if color and QColor.isValidColor(color):
            return color
        return fallback

    def _create_default_anchors(self):
        if not self._vm:
            return
        selected_room = self._require_selected_room_for_anchor()
        if selected_room is None:
            return
        local_points = [
            self._scene_to_room_local(x, y, selected_room)
            for x, y in getattr(selected_room, "points", [])
        ]
        if local_points:
            min_x = min(p[0] for p in local_points)
            max_x = max(p[0] for p in local_points)
            min_y = min(p[1] for p in local_points)
            max_y = max(p[1] for p in local_points)
        else:
            min_x, min_y, max_x, max_y = 0.0, 0.0, 9.8, 9.8
        anchors = [
            {"anchor_id": 0, "label": "A0", "room_id": selected_room.id, "local_x_m": min_x, "local_y_m": min_y, "x_m": min_x, "y_m": min_y, "z_m": 0.0},
            {"anchor_id": 1, "label": "A1", "room_id": selected_room.id, "local_x_m": max_x, "local_y_m": min_y, "x_m": max_x, "y_m": min_y, "z_m": 0.0},
            {"anchor_id": 2, "label": "A2", "room_id": selected_room.id, "local_x_m": max_x, "local_y_m": max_y, "x_m": max_x, "y_m": max_y, "z_m": 0.0},
            {"anchor_id": 3, "label": "A3", "room_id": selected_room.id, "local_x_m": min_x, "local_y_m": max_y, "x_m": min_x, "y_m": max_y, "z_m": 0.0},
        ]
        self._push_undo_state("fit anchors")
        self._draft_anchor_layout = anchors
        self._canvas.set_anchors(self._format_anchors_for_canvas(anchors))
        self._anchor_layout_commit_pending = True
        self._vm.geofence_repo.set_anchors(anchors)
        self._refresh_anchor_status_label()
        self._set_context_help("anchor")
        self._set_canvas_tool_status(f"Anchor Layout / {selected_room.name}")

    def _add_anchor(self):
        if not self._vm:
            return
        room = self._require_selected_room_for_anchor()
        if room is None:
            return
        used_ids = {self._coerce_int_id(anchor.get("anchor_id"), idx) for idx, anchor in enumerate(self._canvas.anchors)}
        anchor_id = 0
        while anchor_id in used_ids:
            anchor_id += 1
        world_x, world_y = self._room_center(room)
        local_x, local_y = self._scene_to_room_local(world_x, world_y, room)
        self._canvas.set_anchor_template(
            {
                "anchor_id": anchor_id,
                "label": f"A{anchor_id}",
                "role": "anchor",
                "device_type": "uwb_anchor",
                "device_id": anchor_id,
                "is_scanned": False,
                "room_id": room.id,
                "zone_id": room.id,
                "zone_name": room.name,
                "local_x_m": local_x,
                "local_y_m": local_y,
            }
        )
        self._canvas.selected_anchor_idx = None
        self._push_undo_state("add anchor")
        self._canvas.add_or_move_anchor_at(world_x, world_y)
        self._canvas.set_edit_mode("edit_vertices")
        self._anchor_layout_commit_pending = True
        self._vm.geofence_repo.set_anchors(self._annotate_anchor_membership(self._canvas.anchor_layout_for_device()))
        self._refresh_anchor_status_label()
        self._set_context_help("anchor")
        self._set_canvas_tool_status(f"Anchor Layout / A{anchor_id}")

    def _assign_or_focus_selected_anchor(self):
        room = self._require_selected_room_for_anchor()
        if room is None:
            return
        self._apply_anchor_template_from_combo()
        data = self.geofence_editor_widget.cmb_scanned_anchors.currentData() if hasattr(self.geofence_editor_widget, "cmb_scanned_anchors") else None
        if not isinstance(data, dict):
            self._set_context_help("anchor")
            self._set_canvas_tool_status("Anchor Layout / Select scanned anchor")
            return
        anchor_id = self._coerce_int_id(data.get("anchor_id"), 0)
        for idx, anchor in enumerate(self._canvas.anchors):
            if self._coerce_int_id(anchor.get("anchor_id"), -1) == anchor_id:
                self._canvas.set_selected_anchor(idx)
                self._canvas.set_edit_mode("edit_vertices")
                self._set_context_help("anchor")
                self._set_canvas_tool_status(f"Anchor Layout / Focus A{anchor_id}")
                return
        world_x, world_y = self._room_center(room)
        self._push_undo_state("assign anchor")
        self._canvas.add_or_move_anchor_at(world_x, world_y)
        self._canvas.set_edit_mode("edit_vertices")
        self._anchor_layout_commit_pending = True
        self._vm.geofence_repo.set_anchors(self._annotate_anchor_membership(self._canvas.anchor_layout_for_device()))
        self._refresh_anchor_status_label()
        self._set_context_help("anchor")
        self._set_canvas_tool_status(f"Anchor Layout / Place A{anchor_id}")

    def _remove_selected_anchor(self):
        self._push_undo_state("remove anchor")
        if self._canvas.delete_selected_anchor():
            self._anchor_layout_commit_pending = True
            if self._vm:
                self._vm.geofence_repo.set_anchors(self._annotate_anchor_membership(self._canvas.anchor_layout_for_device()))
            self.geofence_editor_widget.txt_map_name.clear()
            self._refresh_anchor_status_label()

    def _read_layout_from_device(self):
        if not self._vm:
            QMessageBox.warning(self, "No Connection", "ViewModel not initialized.")
            return
        target = self._selected_device_target()
        self._pending_layout_read_for_editor = bool(getattr(self._canvas, "dim_tracking_view", False))
        self._vm._send_command("anchor_layout_get", dst_addr=self._coerce_int_id(target.get("dst_addr"), 1))
        QMessageBox.information(self, "Read Layout", "Sent layout query to Tag MCU.")

    def _write_layout_to_device(self):
        if not self._vm:
            QMessageBox.warning(self, "No Connection", "ViewModel not initialized.")
            return
        layout = self._annotate_anchor_membership(self._canvas.anchor_layout_for_device())
        if not layout:
            QMessageBox.warning(self, "No Anchors", "No anchors found on the map to save.")
            return
        errors, warnings = self._validate_anchor_layout(layout, require_four=True)
        if errors:
            QMessageBox.warning(self, "Invalid Anchor Layout", "\n".join(errors))
            return
        
        anchors_payload = []
        for a in layout:
            anchors_payload.append({
                "anchor_id": self._coerce_int_id(a["anchor_id"], 0),
                "x_m": float(a["x_m"]),
                "y_m": float(a["y_m"]),
                "z_m": float(a["z_m"])
            })
        
        target = self._selected_device_target()
        self._vm._send_command("anchor_layout_set", dst_addr=self._coerce_int_id(target.get("dst_addr"), 1), anchors=anchors_payload)
        warning_text = ("\n" + "\n".join(warnings)) if warnings else ""
        QMessageBox.information(self, "Write Layout", f"Sent layout with {len(layout)} anchors to Tag MCU.{warning_text}")

    def _on_scan_devices_updated(self, devices):
        if not hasattr(self.geofence_editor_widget, "cmb_scanned_anchors"):
            return
        combo = self.geofence_editor_widget.cmb_scanned_anchors
        target_combo = getattr(self.geofence_editor_widget, "cmb_device_target", None)
        
        selected_data = combo.currentData()
        selected_id = selected_data.get("anchor_id") if isinstance(selected_data, dict) else None
        selected_target = target_combo.currentData() if target_combo else None
        
        combo.blockSignals(True)
        if target_combo:
            target_combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem("Manual (No Link)", None)
            if target_combo:
                target_combo.clear()
                target_combo.addItem("Tag / MCU (0x0001)", {"dst_addr": 1, "role": "tag"})
            
            added_ids = set()
            
            for dev in (devices or []):
                raw_type = str(dev.get("device_type", dev.get("type", ""))).lower()
                raw_role = str(dev.get("role", "")).lower()
                dev_id = self._coerce_int_id(
                    dev.get("device_id") or dev.get("anchor_id") or dev.get("serial_number"),
                    0,
                )
                mac = dev.get("mac", dev.get("mac_address", ""))
                display_id = dev_id if dev_id > 0 else len(added_ids)
                is_anchor = raw_type in {"2", "anchor", "uwb_anchor"} or raw_role == "anchor"
                is_tag = raw_type in {"1", "tag", "uwb_tag"} or raw_role == "tag"
                if is_anchor and display_id not in added_ids:
                    item = {
                        "anchor_id": display_id,
                        "label": f"A{display_id}",
                        "role": "anchor",
                        "device_type": "uwb_anchor",
                        "device_id": display_id,
                        "mac": mac,
                        "is_scanned": True,
                    }
                    mac_suffix = f" - {mac}" if mac else ""
                    combo.addItem(f"A{display_id} / Anchor 0x{display_id:04x}{mac_suffix}", item)
                    added_ids.add(display_id)
                if target_combo and (is_tag or dev_id > 0):
                    label_role = "Tag" if is_tag else ("Anchor" if is_anchor else "Device")
                    target_combo.addItem(
                        f"{label_role} 0x{display_id:04x}",
                        {"dst_addr": display_id, "role": raw_role or label_role.lower(), "device_type": raw_type},
                    )
            
            for i in range(0, 4):
                if i not in added_ids:
                    combo.addItem(
                        f"A{i} / Anchor 0x{i:04x}",
                        {
                            "anchor_id": i,
                            "label": f"A{i}",
                            "role": "anchor",
                            "device_type": "uwb_anchor",
                            "device_id": i,
                            "mac": "",
                            "is_scanned": False,
                        },
                    )
            
            if selected_id is not None:
                restored_idx = 0
                for idx in range(combo.count()):
                    data = combo.itemData(idx)
                    if isinstance(data, dict) and self._coerce_int_id(data.get("anchor_id"), -1) == self._coerce_int_id(selected_id, -2):
                        restored_idx = idx
                        break
                combo.setCurrentIndex(restored_idx)
            else:
                combo.setCurrentIndex(0)
            if target_combo and isinstance(selected_target, dict):
                target_addr = selected_target.get("dst_addr")
                for idx in range(target_combo.count()):
                    data = target_combo.itemData(idx)
                    if isinstance(data, dict) and data.get("dst_addr") == target_addr:
                        target_combo.setCurrentIndex(idx)
                        break
        finally:
            combo.blockSignals(False)
            if target_combo:
                target_combo.blockSignals(False)
        self._apply_anchor_template_from_combo()

    def _enter_geofence_editor(self):
        self._canvas.dim_tracking_view = True
        self._canvas_tool_label.setVisible(False)
        self._canvas_tool_bar.setVisible(True)
        self._anchor_layout_commit_pending = False
        self._pending_layout_read_for_editor = False
        self.user_map_groupbox.setVisible(False)
        self.sidebar_stack.setCurrentIndex(1)
        self.canvas_header.setText("Geofencing Map Setup")

        if self._canvas.edit_mode == "navigate":
            self._set_editor_tool("room", "draw")
        if self._vm:
            self._canvas.set_geofences(self._vm.get_geofence_zones())
            map_anchors = [self._normalize_anchor_record(anchor, idx) for idx, anchor in enumerate(self._vm.get_map_anchors())]
            self._geofence_anchor_baseline = [dict(anchor) for anchor in map_anchors]
            self._draft_anchor_layout = [dict(anchor) for anchor in map_anchors]
            self._canvas.set_anchors(self._format_anchors_for_canvas(map_anchors))
            self._refresh_anchor_status_label()

    def _exit_geofence_editor(self):
        if self._is_developer_mode:
            self._enter_geofence_editor()
            return

        if self._vm:
            draft_layout = self._annotate_anchor_membership(self._canvas.anchor_layout_for_device())
            should_commit = self._anchor_layout_commit_pending or not self._same_anchor_layout(
                draft_layout,
                self._geofence_anchor_baseline,
            )
            if should_commit:
                self._vm.update_anchor_layout_from_map(draft_layout)
            self._canvas.set_anchors(self._vm.current_anchor_layout)
            self._anchor_layout_commit_pending = False
            self._pending_layout_read_for_editor = False

        self._canvas.dim_tracking_view = False
        self._canvas_tool_label.setVisible(False)
        self._canvas_tool_bar.setVisible(False)
        self._canvas.set_edit_mode("navigate")
        self._preview_overlay_btn.setChecked(False)
        self._toggle_canvas_view_mode(False)
        self.sidebar_stack.setCurrentIndex(0)
        self.canvas_header.setText("Real-time Position Tracking")
        self.user_map_groupbox.setVisible(True)
        if self._vm:
            self._canvas.set_anchors(self._vm.current_anchor_layout)

    def _update_grid_settings(self, *_args):
        major_m = self.geofence_editor_widget.sb_grid_spacing.value()
        subdivisions = self.geofence_editor_widget.sb_grid_subdivisions.value()
        minor_m = major_m / max(subdivisions, 1)
        if minor_m < 1.0:
            label = f"{minor_m:.2f} m ({minor_m * 100:.0f} cm)"
        else:
            label = f"{minor_m:.2f} m"
        self.geofence_editor_widget.lbl_minor_resolution.setText(label)
        self._canvas.set_grid_settings(major_m, subdivisions)
        # NOTE: Thickness is fully independent — do NOT touch sb_wall_thickness here.
        self._save_geofence_config()

    def _snap_existing_walls_to_grid(self):
        """Snap wall centerline vertices to the final snap step without changing thickness."""
        if not self._vm:
            return

        snap_step = self._canvas._snap_step()
        if snap_step <= 0.0:
            return

        changed = False
        for zone in self._vm.get_geofence_zones():
            if getattr(zone, "object_type", "zone") != "wall" or len(zone.points) < 2:
                continue

            snapped_points = [
                self._canvas._snap_world_point(x, y)
                for x, y in zone.points
            ]

            # Do not corrupt a wall when a coarse grid would collapse one of its segments.
            if any(
                snapped_points[idx] == snapped_points[idx + 1]
                for idx in range(len(snapped_points) - 1)
            ):
                log.warning(
                    "Skipped snapping wall %s: %.6f m step would collapse a segment.",
                    zone.id,
                    snap_step,
                )
                continue

            if snapped_points != zone.points:
                zone.points = snapped_points
                changed = True

        if not changed:
            return

        self._vm.geofence_layout_updated.emit(self._vm.get_geofence_zones())

        # Refresh displayed dimensions when the selected wall moved to the new grid.
        selected_id = self._canvas.selected_zone_id
        selected_zone = next(
            (zone for zone in self._vm.get_geofence_zones() if zone.id == selected_id),
            None,
        )
        if selected_zone and getattr(selected_zone, "object_type", "zone") == "wall":
            self._load_zone_properties_to_ui(selected_zone)

        self._canvas.update()

    def _set_context_help(self, context: str):
        title = getattr(self.geofence_editor_widget, "lbl_context_title", None)
        body = getattr(self.geofence_editor_widget, "lbl_context_body", None)
        if title is None or body is None:
            return

        help_text = {
            "room": (
                "Room Zone",
                "Room Zone is the usable tracked area. The editor temporarily allows more than 4 rooms for layout work.",
            ),
            "wall": (
                "Wall Shell",
                "Walls represent physical boundaries and should wrap around Room Zones. Wall thickness is rendered in true meters, so zooming will not inflate it.",
            ),
            "object": (
                "Object Layer",
                "Objects are generic 3D map assets such as circles or polygons. Use them for furniture, obstacles, or custom geometry inside the room.",
            ),
            "zone": (
                "Rule Zone",
                "Rule Zones are optional sub-areas inside a Room Zone. Use No-Go for forbidden areas or Speed Limited for slow movement regions.",
            ),
            "edit": (
                "Edit Geometry",
                "Select an object, drag vertices or edges, and edit exact dimensions from the inspector. Properties stay in the sidebar.",
            ),
            "anchor": (
                "Room Anchor Layout",
                "Anchors are configured from the selected Room Zone property table. They are not a free canvas drawing tool.",
            ),
        }
        help_title, help_body = help_text.get(context, help_text["room"])
        title.setText(help_title)
        body.setText(help_body)

    def _on_editor_tab_changed(self, index):
        if index == 0:
            self._set_editor_tool("room", "draw")
        else:
            self._set_editor_tool("zone", "draw")

    def _set_editor_tool(self, object_type: str, mode: str):
        if object_type == "anchor":
            self._canvas.set_edit_mode("edit_vertices")
            self._set_context_help("anchor")
            self._set_canvas_tool_status("Anchor Layout / Room property")
            return
        self._canvas.set_draw_object_type(object_type)
        if object_type == "object":
            self._canvas.set_draw_object_shape(getattr(self.geofence_editor_widget, "cmb_object_shape", None).currentData() if hasattr(self.geofence_editor_widget, "cmb_object_shape") else "polygon")
        target_tab = 1 if object_type == "zone" else 0
        if self.geofence_editor_widget.editor_tabs.currentIndex() != target_tab:
            self.geofence_editor_widget.editor_tabs.blockSignals(True)
            self.geofence_editor_widget.editor_tabs.setCurrentIndex(target_tab)
            self.geofence_editor_widget.editor_tabs.blockSignals(False)
        if object_type in {"room", "wall", "object", "anchor"}:
            lookup_text = "Room" if object_type == "room" else object_type.title()
            idx = self.geofence_editor_widget.cmb_map_type.findText(lookup_text, Qt.MatchFlag.MatchStartsWith)
            if idx >= 0 and self.geofence_editor_widget.cmb_map_type.currentIndex() != idx:
                self.geofence_editor_widget.cmb_map_type.blockSignals(True)
                self.geofence_editor_widget.cmb_map_type.setCurrentIndex(idx)
                self.geofence_editor_widget.cmb_map_type.blockSignals(False)
            self._sync_map_height_visibility()
        if object_type == "anchor":
            self._apply_anchor_template_from_combo()
        self._set_context_help(object_type if object_type in {"room", "wall", "zone", "object"} else "room")
        self._set_editor_mode(mode)

    def _set_editor_mode(self, mode):
        self._canvas.set_edit_mode(mode)
        draw_type = self._canvas.draw_object_type
        is_draw = mode == "draw"
        is_edit = mode == "edit_vertices"
        is_map_tab = self.geofence_editor_widget.editor_tabs.currentIndex() == 0
        self.geofence_editor_widget.btn_mode_room.setChecked(is_draw and draw_type == "room")
        self.geofence_editor_widget.btn_mode_wall.setChecked(is_draw and draw_type == "wall")
        self.geofence_editor_widget.btn_mode_draw.setChecked(is_draw and draw_type == "zone")
        if hasattr(self.geofence_editor_widget, "btn_mode_object"):
            self.geofence_editor_widget.btn_mode_object.setChecked(is_draw and draw_type == "object")
        self.geofence_editor_widget.btn_mode_edit_map.setChecked(is_edit and is_map_tab)
        self.geofence_editor_widget.btn_mode_edit.setChecked(is_edit and not is_map_tab)
        if hasattr(self, "_canvas_tool_buttons"):
            btns = self._canvas_tool_buttons
            if btns.get("1"):
                btns["1"].setChecked(is_draw and draw_type == "room")
            if btns.get("2"):
                btns["2"].setChecked(is_draw and draw_type == "wall")
            if btns.get("3"):
                btns["3"].setChecked(is_draw and draw_type == "zone")
            if btns.get("4"):
                btns["4"].setChecked(is_draw and draw_type == "object")
            if btns.get("5"):
                btns["5"].setChecked(is_edit)
            if btns.get("6"):
                btns["6"].setChecked(False)  # view mode is separate
        if is_edit:
            self._set_context_help("edit")
            self._set_canvas_tool_status("Edit Geometry")
        elif is_draw:
            labels = {"room": "Room", "wall": "Wall", "zone": "Rule", "object": "Object"}
            self._set_canvas_tool_status(labels.get(draw_type, "Canvas / Draw"))

    def _sync_map_height_visibility(self, *_args):
        object_type = self.geofence_editor_widget.cmb_map_type.currentText().strip().lower()
        is_wall = object_type == "wall"
        is_room = object_type.startswith("room")
        is_object = object_type == "object"
        self.geofence_editor_widget.lbl_map_height.setText("Height:")
        self.geofence_editor_widget.sb_map_height.setMinimum(0.1)
        self.geofence_editor_widget.lbl_map_height.setVisible(is_wall or is_object)
        self.geofence_editor_widget.sb_map_height.setVisible(is_wall or is_object)
        if hasattr(self.geofence_editor_widget, "lbl_wall_thickness"):
            self.geofence_editor_widget.lbl_wall_thickness.setVisible(is_wall)
            self.geofence_editor_widget.sb_wall_thickness.setVisible(is_wall)
        if hasattr(self.geofence_editor_widget, "cmb_wall_mode"):
            self.geofence_editor_widget.cmb_wall_mode.setVisible(is_wall)
            self.geofence_editor_widget.cmb_wall_host_room.setVisible(is_wall)
        if hasattr(self.geofence_editor_widget, "cmb_object_shape"):
            self.geofence_editor_widget.lbl_object_shape.setVisible(is_object)
            self.geofence_editor_widget.cmb_object_shape.setVisible(is_object)
            object_subtype = self.geofence_editor_widget.cmb_object_subtype.currentData() if hasattr(self.geofence_editor_widget, "cmb_object_subtype") else "generic"
            is_special_object = object_subtype == "stairs"
            is_circle = self.geofence_editor_widget.cmb_object_shape.currentData() == "circle" and not is_special_object
            self.geofence_editor_widget.lbl_object_radius.setVisible(is_object and is_circle)
            self.geofence_editor_widget.sb_object_radius.setVisible(is_object and is_circle)
            if is_object and getattr(self._canvas, "draw_object_type", "") == "object":
                shape_kind = "polygon" if is_special_object else (self.geofence_editor_widget.cmb_object_shape.currentData() or "polygon")
                self._canvas.set_draw_object_shape(shape_kind)
        if hasattr(self.geofence_editor_widget, "cmb_object_subtype"):
            self.geofence_editor_widget.cmb_object_subtype.setVisible(is_object)
            if hasattr(self.geofence_editor_widget, "lbl_object_subtype"):
                self.geofence_editor_widget.lbl_object_subtype.setVisible(is_object)
        if hasattr(self.geofence_editor_widget, "cmb_object_direction"):
            object_subtype = self.geofence_editor_widget.cmb_object_subtype.currentData() if hasattr(self.geofence_editor_widget, "cmb_object_subtype") else "generic"
            is_stairs = is_object and object_subtype == "stairs"
            self.geofence_editor_widget.cmb_object_direction.setVisible(is_stairs)
            if hasattr(self.geofence_editor_widget, "lbl_object_direction"):
                self.geofence_editor_widget.lbl_object_direction.setVisible(is_stairs)
        if hasattr(self.geofence_editor_widget, "btn_insert_vertex"):
            self.geofence_editor_widget.btn_insert_vertex.setVisible(is_room or is_wall or is_object)
        if hasattr(self.geofence_editor_widget, "gb_anchor_layout"):
            self.geofence_editor_widget.gb_anchor_layout.setVisible(is_room)
        # Hide Rules tab (index 1) when viewing a Room or Wall in Structure tab
        try:
            tabs = self.geofence_editor_widget.editor_tabs
            if is_room or is_wall or is_object:
                tabs.setTabVisible(1, False)
            else:
                tabs.setTabVisible(1, True)
        except Exception:
            pass
        self._set_context_help("wall" if is_wall else "object" if is_object else "room")

    def _sync_rule_speed_visibility(self, *_args):
        is_speed_limited = self.geofence_editor_widget.cmb_zone_type.currentIndex() == 0
        self.geofence_editor_widget.lbl_prop_speed.setVisible(is_speed_limited)
        self.geofence_editor_widget.sb_speed.setVisible(is_speed_limited)

    def _begin_insert_vertex(self):
        if not self._canvas.selected_zone_id:
            QMessageBox.information(self, "Add Point", "Select a room, wall, or object first.")
            return
        self._push_undo_state("insert point")
        if self._canvas.begin_insert_vertex():
            self._set_canvas_tool_status("Add Point / Click an edge")
        else:
            QMessageBox.information(self, "Add Point", "Selected object has no edge to edit.")

    def _room_zones(self):
        if not self._vm:
            return []
        return [
            zone
            for zone in self._vm.get_geofence_zones()
            if getattr(zone, "object_type", "zone") == "room"
        ]

    @staticmethod
    def _segment_lies_on_room_edge(p1, p2, edge_start, edge_end, tolerance):
        """True when the entire wall segment is collinear with one room boundary edge."""
        ex = edge_end[0] - edge_start[0]
        ey = edge_end[1] - edge_start[1]
        edge_length = math.hypot(ex, ey)
        if edge_length <= 1e-9:
            return False
        def point_distance_to_line(point):
            return abs((point[0] - edge_start[0]) * ey - (point[1] - edge_start[1]) * ex) / edge_length
        if point_distance_to_line(p1) > tolerance or point_distance_to_line(p2) > tolerance:
            return False
        proj_1 = ((p1[0] - edge_start[0]) * ex + (p1[1] - edge_start[1]) * ey) / edge_length
        proj_2 = ((p2[0] - edge_start[0]) * ex + (p2[1] - edge_start[1]) * ey) / edge_length
        return min(proj_1, proj_2) >= -tolerance and max(proj_1, proj_2) <= edge_length + tolerance

    def _detect_boundary_host_room(self, wall_points):
        """Find a room whose boundary fully contains every new wall segment."""
        points = list(wall_points or [])
        if len(points) < 2:
            return None
        snap_step = self._canvas._snap_step() if hasattr(self._canvas, "_snap_step") else 0.1
        tolerance = max(0.01, min(0.05, float(snap_step) * 0.30))
        for room in self._room_zones():
            room_points = list(getattr(room, "points", []) or [])
            if len(room_points) < 3:
                continue
            edges = [(room_points[idx], room_points[(idx + 1) % len(room_points)]) for idx in range(len(room_points))]
            if all(
                any(self._segment_lies_on_room_edge(p1, p2, edge_start, edge_end, tolerance)
                    for edge_start, edge_end in edges)
                for p1, p2 in zip(points, points[1:])
            ):
                return room
        return None

    def _wall_points_inside_rooms(self, points):
        conflicts = []
        for room in self._room_zones():
            samples = list(points)
            for idx in range(max(0, len(points) - 1)):
                p1 = points[idx]
                p2 = points[idx + 1]
                steps = max(2, int(math.hypot(p2[0] - p1[0], p2[1] - p1[1]) / 0.1))
                for step in range(1, steps):
                    t = step / steps
                    samples.append((p1[0] + (p2[0] - p1[0]) * t, p1[1] + (p2[1] - p1[1]) * t))
            for pt in samples:
                if self._point_in_points(room.points, pt[0], pt[1]):
                    conflicts.append(room.name)
                    break
        return sorted(set(conflicts))

    def _on_canvas_polygon_completed(self, points):
        if not self._vm:
            return

        object_type = self._canvas.draw_object_type
        objects = self._vm.get_geofence_zones()
        zone_id = str(uuid.uuid4())[:8]

        if object_type == "zone":
            number = sum(1 for obj in objects if getattr(obj, "object_type", "zone") == "zone") + 1
            zone_type = "allowed" if self.geofence_editor_widget.cmb_zone_type.currentIndex() == 0 else "forbidden"
            new_zone = GeofenceZone(
                id=zone_id,
                name=f"Rule Zone {number}",
                zone_type=zone_type,
                points=points,
                min_z=0.0,
                max_z=0.0,
                speed_limit=self.geofence_editor_widget.sb_speed.value(),
                color="#22C55E" if zone_type == "allowed" else "#EF4444",
                object_type="zone",
            )
        elif object_type == "object":
            number = sum(1 for obj in objects if getattr(obj, "object_type", "zone") == "object") + 1
            object_shape = getattr(self.geofence_editor_widget, "cmb_object_shape", None).currentData() if hasattr(self.geofence_editor_widget, "cmb_object_shape") else "polygon"
            object_subtype = getattr(self.geofence_editor_widget, "cmb_object_subtype", None).currentData() if hasattr(self.geofence_editor_widget, "cmb_object_subtype") else "generic"
            object_direction = getattr(self.geofence_editor_widget, "cmb_object_direction", None).currentData() if hasattr(self.geofence_editor_widget, "cmb_object_direction") else "up"
            if object_subtype == "stairs":
                object_shape = "polygon"
            height = self.geofence_editor_widget.sb_map_height.value()
            radius = 0.0
            if object_shape == "circle" and points:
                center_x = sum(pt[0] for pt in points) / len(points)
                center_y = sum(pt[1] for pt in points) / len(points)
                radius = sum(math.hypot(pt[0] - center_x, pt[1] - center_y) for pt in points) / len(points)
            new_zone = GeofenceZone(
                id=zone_id,
                name=f"Stairs {number}" if object_subtype == "stairs" else f"Object {number}",
                zone_type="allowed",
                points=points,
                min_z=0.0,
                max_z=height,
                speed_limit=0.0,
                color="#F59E0B",
                object_type="object",
                shape_kind=object_shape,
                object_subtype=object_subtype,
                object_direction=object_direction,
                radius_m=radius,
                thickness_m=0.0,
            )
        else:
            if object_type == "wall":
                # Wall thickness is an explicit physical dimension in meters.
                # It must not inherit the display grid major-cell size.
                thickness = max(0.01, float(self.geofence_editor_widget.sb_wall_thickness.value()))
            else:
                thickness = 0.0
            number = sum(1 for obj in objects if getattr(obj, "object_type", "zone") == object_type) + 1
            default_name = f"Wall Segment {number}" if object_type == "wall" else f"{object_type.title()} {number}"
            height = self.geofence_editor_widget.sb_map_height.value() if object_type == "wall" else 0.0
            new_zone = GeofenceZone(
                id=zone_id,
                name=default_name,
                zone_type=object_type,
                points=points,
                min_z=0.0,
                max_z=height,
                speed_limit=0.0,
                color="#F8FAFC" if object_type == "room" else "#0F172A",
                object_type=object_type,
                thickness_m=thickness,
                wall_mode="free_standing",
                host_room_id=None,
            )
            if object_type == "wall":
                host_room = self._detect_boundary_host_room(points)
                if host_room is not None:
                    new_zone.wall_mode = "boundary_outside"
                    new_zone.host_room_id = host_room.id

        self._push_undo_state("add object")
        self._vm.add_geofence_zone(new_zone)
        self._canvas.set_geofences(self._vm.get_geofence_zones())
        self._canvas.set_selected_zone(zone_id)
        self._load_zone_properties_to_ui(new_zone)
        if object_type == "room":
            self._refresh_anchor_membership_from_canvas()
            self._refresh_active_room_combo()

    def _load_zone_properties_to_ui(self, zone):
        object_type = getattr(zone, "object_type", "zone")
        self._map_properties_syncing = True
        try:
            if object_type == "zone":
                try:
                    self.geofence_editor_widget.editor_tabs.setTabVisible(0, True)
                    self.geofence_editor_widget.editor_tabs.setTabVisible(1, True)
                except Exception:
                    pass
                self.geofence_editor_widget.editor_tabs.setCurrentIndex(1)
                self.geofence_editor_widget.txt_zone_name.setText(zone.name)
                self.geofence_editor_widget.cmb_zone_type.setCurrentIndex(0 if zone.zone_type == "allowed" else 1)
                self.geofence_editor_widget.sb_speed.setValue(zone.speed_limit)
                self._set_editor_color("zone", zone.color)
                self._sync_rule_speed_visibility()
                self._canvas.set_draw_object_type("zone")
            else:
                self.geofence_editor_widget.editor_tabs.setCurrentIndex(0)
                self.geofence_editor_widget.txt_map_name.setText(zone.name)
                if object_type == "room":
                    map_idx = self.geofence_editor_widget.cmb_map_type.findText("Room", Qt.MatchFlag.MatchStartsWith)
                elif object_type == "wall":
                    map_idx = self.geofence_editor_widget.cmb_map_type.findText("Wall", Qt.MatchFlag.MatchStartsWith)
                else:
                    map_idx = self.geofence_editor_widget.cmb_map_type.findText("Object", Qt.MatchFlag.MatchStartsWith)
                self.geofence_editor_widget.cmb_map_type.setCurrentIndex(max(map_idx, 0))
                self._set_editor_color("map", zone.color)
                if object_type == "wall":
                    self.geofence_editor_widget.sb_map_height.setValue(max(0.1, zone.max_z - zone.min_z))
                    if hasattr(self.geofence_editor_widget, "sb_wall_thickness"):
                        thickness_m = max(0.01, float(getattr(zone, "thickness_m", 0.1)))
                        self.geofence_editor_widget.sb_wall_thickness.setValue(thickness_m)
                    self._refresh_wall_host_rooms(getattr(zone, "host_room_id", None))
                    mode_combo = getattr(self.geofence_editor_widget, "cmb_wall_mode", None)
                    if mode_combo is not None:
                        mode_idx = mode_combo.findData(getattr(zone, "wall_mode", "free_standing"))
                        mode_combo.setCurrentIndex(max(mode_idx, 0))
                elif object_type == "object":
                    self.geofence_editor_widget.sb_map_height.setValue(max(0.1, zone.max_z - zone.min_z))
                    if hasattr(self.geofence_editor_widget, "cmb_object_subtype"):
                        subtype_idx = self.geofence_editor_widget.cmb_object_subtype.findData(getattr(zone, "object_subtype", "generic"))
                        self.geofence_editor_widget.cmb_object_subtype.setCurrentIndex(max(subtype_idx, 0))
                    if hasattr(self.geofence_editor_widget, "cmb_object_direction"):
                        direction_idx = self.geofence_editor_widget.cmb_object_direction.findData(getattr(zone, "object_direction", "up"))
                        self.geofence_editor_widget.cmb_object_direction.setCurrentIndex(max(direction_idx, 0))
                    if hasattr(self.geofence_editor_widget, "cmb_object_shape"):
                        shape_idx = self.geofence_editor_widget.cmb_object_shape.findData(getattr(zone, "shape_kind", "polygon"))
                        self.geofence_editor_widget.cmb_object_shape.setCurrentIndex(max(shape_idx, 0))
                        if hasattr(self.geofence_editor_widget, "sb_object_radius"):
                            self.geofence_editor_widget.sb_object_radius.setValue(max(0.01, float(getattr(zone, "radius_m", 0.0)) or 0.5))
                self._canvas.set_draw_object_type(object_type)
                if object_type == "object":
                    self._canvas.set_draw_object_shape(getattr(zone, "shape_kind", "polygon"))
                self._sync_map_height_visibility()
        finally:
            self._map_properties_syncing = False
        self._update_geometry_inspector(zone)
        self._set_editor_mode("edit_vertices")

    def _auto_apply_map_properties(self, *_args):
        self._apply_map_properties(silent=True)

    def _apply_map_properties(self, silent=False):
        if self._map_properties_syncing:
            return
        if self._canvas.selected_anchor_idx is not None:
            name = self.geofence_editor_widget.txt_map_name.text().strip()
            anchor_id = None
            
            cmb_val = self.geofence_editor_widget.cmb_scanned_anchors.currentData()
            if isinstance(cmb_val, dict):
                anchor_id = self._coerce_int_id(cmb_val.get("anchor_id"), 0)
                if not name:
                    name = cmb_val.get("label", f"A{anchor_id}")
            else:
                if name.lower().startswith("a") and name[1:].isdigit():
                    anchor_id = self._coerce_int_id(name, 0)
                elif name.isdigit():
                    anchor_id = self._coerce_int_id(name, 0)
            if anchor_id is not None:
                for idx, anchor in enumerate(self._canvas.anchors):
                    if idx != self._canvas.selected_anchor_idx and self._coerce_int_id(anchor.get("anchor_id"), -1) == anchor_id:
                        if not silent:
                            QMessageBox.warning(self, "Duplicate Anchor", f"A{anchor_id} is already placed on the map.")
                        return

            self._push_undo_state("anchor properties")
            self._canvas.update_selected_anchor(
                anchor_id=anchor_id,
                label=name or None,
                x=self.geofence_editor_widget.sb_anchor_x.value() if hasattr(self.geofence_editor_widget, "sb_anchor_x") else None,
                y=self.geofence_editor_widget.sb_anchor_y.value() if hasattr(self.geofence_editor_widget, "sb_anchor_y") else None,
                z=self.geofence_editor_widget.sb_anchor_z.value() if hasattr(self.geofence_editor_widget, "sb_anchor_z") else 0.0,
                role="anchor",
                device_type="uwb_anchor",
            )
            self._anchor_layout_commit_pending = True
            self._refresh_anchor_status_label()
            self._save_geofence_config()
            return

        selected_ids = list(self._canvas.selected_zone_ids)
        if not selected_ids or not self._vm:
            if not silent:
                QMessageBox.warning(self, "No Selection", "Select a room, wall, or object on the map first.")
            return

        objects = self._vm.get_geofence_zones()
        target_zones = [z for z in objects if z.id in selected_ids]
        if not target_zones or all(getattr(z, "object_type", "zone") == "zone" for z in target_zones):
            if not silent:
                QMessageBox.warning(self, "Wrong Object Type", "The selected objects are rule zones, not map objects.")
            return

        selected_type = self.geofence_editor_widget.cmb_map_type.currentText().strip().lower()
        if selected_type == "anchor":
            if not silent:
                QMessageBox.warning(self, "Wrong Object Type", "Select an anchor on the map first.")
            return
        if selected_type == "wall":
            object_type = "wall"
        elif selected_type == "object":
            object_type = "object"
        else:
            object_type = "room"

        self._push_undo_state("map properties")

        name_text = self.geofence_editor_widget.txt_map_name.text().strip()
        max_z_val = self.geofence_editor_widget.sb_map_height.value() if object_type in {"wall", "object"} else 0.0
        thickness_val = max(0.01, float(self.geofence_editor_widget.sb_wall_thickness.value())) if hasattr(self.geofence_editor_widget, "sb_wall_thickness") else 0.1
        wall_mode_val = self.geofence_editor_widget.cmb_wall_mode.currentData() or "free_standing" if hasattr(self.geofence_editor_widget, "cmb_wall_mode") else "free_standing"
        host_room_val = self.geofence_editor_widget.cmb_wall_host_room.currentData() or None if hasattr(self.geofence_editor_widget, "cmb_wall_host_room") else None
        shape_kind_val = self.geofence_editor_widget.cmb_object_shape.currentData() if hasattr(self.geofence_editor_widget, "cmb_object_shape") else "polygon"
        object_subtype_val = self.geofence_editor_widget.cmb_object_subtype.currentData() if hasattr(self.geofence_editor_widget, "cmb_object_subtype") else "generic"
        object_direction_val = self.geofence_editor_widget.cmb_object_direction.currentData() if hasattr(self.geofence_editor_widget, "cmb_object_direction") else "up"
        if object_subtype_val == "stairs":
            shape_kind_val = "polygon"
        radius_val = float(self.geofence_editor_widget.sb_object_radius.value()) if shape_kind_val == "circle" and hasattr(self.geofence_editor_widget, "sb_object_radius") else 0.0

        for zone in target_zones:
            if getattr(zone, "object_type", "zone") == "zone":
                continue
            zone.object_type = object_type
            zone.zone_type = object_type
            zone.name = name_text or object_type.title()
            zone.min_z = 0.0
            zone.max_z = max_z_val
            if object_type == "wall":
                zone.thickness_m = thickness_val
                zone.wall_mode = wall_mode_val
                zone.host_room_id = host_room_val
                zone.shape_kind = "polygon"
                zone.radius_m = 0.0
            elif object_type == "object":
                zone.thickness_m = 0.0
                zone.wall_mode = "free_standing"
                zone.host_room_id = None
                zone.shape_kind = shape_kind_val
                zone.object_subtype = object_subtype_val
                zone.object_direction = object_direction_val
                zone.radius_m = radius_val
            else:
                zone.thickness_m = 0.0
                zone.shape_kind = "polygon"
                zone.object_subtype = "generic"
                zone.object_direction = "up"
                zone.radius_m = 0.0
            zone.speed_limit = 0.0
            default_color = "#F8FAFC" if object_type == "room" else "#F59E0B" if object_type == "object" else "#0F172A"
            zone.color = self._valid_hex_color_or(
                getattr(self.geofence_editor_widget, "_map_color", ""),
                getattr(zone, "color", default_color) or default_color,
            )

        self._vm.geofence_layout_updated.emit(self._vm.get_geofence_zones())
        self._update_geometry_inspector(target_zones[0])
        self._canvas.update()
        if object_type == "room":
            self._refresh_anchor_membership_from_canvas()
            self._refresh_active_room_combo()
        self._save_geofence_config()

    def _apply_zone_properties(self):
        selected_ids = list(self._canvas.selected_zone_ids)
        if not selected_ids or not self._vm:
            QMessageBox.warning(self, "No Selection", "Select a rule zone on the map first.")
            return

        objects = self._vm.get_geofence_zones()
        target_zones = [z for z in objects if z.id in selected_ids]
        if not target_zones or all(getattr(z, "object_type", "zone") != "zone" for z in target_zones):
            QMessageBox.warning(self, "Wrong Object Type", "The selected objects are rooms or walls, not rule zones.")
            return

        self._push_undo_state("rule properties")
        name_text = self.geofence_editor_widget.txt_zone_name.text().strip() or "Rule Zone"
        zone_type_val = "allowed" if self.geofence_editor_widget.cmb_zone_type.currentIndex() == 0 else "forbidden"
        speed_limit_val = self.geofence_editor_widget.sb_speed.value()

        for zone in target_zones:
            if getattr(zone, "object_type", "zone") != "zone":
                continue
            zone.name = name_text
            zone.zone_type = zone_type_val
            zone.min_z = 0.0
            zone.max_z = 0.0
            zone.speed_limit = speed_limit_val
            default_color = "#22C55E" if zone.zone_type == "allowed" else "#EF4444"
            zone.color = self._valid_hex_color_or(
                getattr(self.geofence_editor_widget, "_zone_color", ""),
                getattr(zone, "color", default_color) or default_color,
            )
            zone.object_type = "zone"

        self._vm.geofence_layout_updated.emit(self._vm.get_geofence_zones())
        self._canvas.update()
        self._update_geometry_inspector(target_zones[0])
        self._save_geofence_config()

    # -----------------------------------------------------------------------
    # View-only mode
    # -----------------------------------------------------------------------
    def _set_view_only_mode(self):
        self._canvas.set_edit_mode("navigate")
        if hasattr(self, "_canvas_tool_buttons"):
            for key, btn in self._canvas_tool_buttons.items():
                btn.setChecked(key == "6")
        self._set_canvas_tool_status("View Only")

    # -----------------------------------------------------------------------
    # Set corner origin for room
    # -----------------------------------------------------------------------
    def _on_btn_set_corner_clicked(self):
        """Let the user click a visible room vertex instead of choosing a menu item."""
        button = self.geofence_editor_widget.btn_set_corner
        if getattr(self._canvas, "_origin_pick_room_id", None):
            self._canvas.cancel_room_origin_pick()
            button.setText("Set Local Origin")
            return
        room = self._selected_room()
        if room is None or len(room.points) < 3:
            QMessageBox.information(self, "Set Local Origin", "Select a Room Zone on the canvas first.")
            return
        self._set_editor_mode("edit_vertices")
        if self._canvas.begin_room_origin_pick(room.id):
            button.setText("Click a corner…")
            button.setToolTip("Hover a room vertex to preview it; click to set local (0,0). Right-click or press this button again to cancel.")

    def _on_canvas_room_origin_vertex_picked(self, room_id: str, vertex_idx: int):
        room = self._find_room(room_id)
        if room is None or not (0 <= vertex_idx < len(room.points)):
            return
        self._push_undo_state("room origin")
        room.origin_vertex_idx = vertex_idx
        self._canvas.set_room_origin(room.id, room.points[vertex_idx])
        self.geofence_editor_widget.btn_set_corner.setText("Set Local Origin")
        self.geofence_editor_widget.btn_set_corner.setToolTip("Choose a visible room vertex as local (0,0); canvas geometry does not move.")
        self._vm.geofence_layout_updated.emit(self._vm.get_geofence_zones())
        self._refresh_anchor_membership_from_canvas()
        self._refresh_room_anchor_table()
        self._update_geometry_inspector(room)


    # -----------------------------------------------------------------------
    # Copy / Cut / Paste
    # -----------------------------------------------------------------------
    def _copy_selected_zone(self):
        import copy
        selected_id = self._canvas.selected_zone_id
        if not selected_id or not self._vm:
            return
        zones = self._vm.get_geofence_zones()
        zone = next((z for z in zones if z.id == selected_id), None)
        if zone:
            self._clipboard_zone = copy.deepcopy(zone)

    def _cut_selected_zone(self):
        self._copy_selected_zone()
        self._delete_selected_zone()

    def _paste_zone(self):
        import copy
        if not hasattr(self, "_clipboard_zone") or self._clipboard_zone is None or not self._vm:
            return
        self._push_undo_state("paste")
        new_zone = copy.deepcopy(self._clipboard_zone)
        new_zone.id = str(uuid.uuid4())
        new_zone.name = new_zone.name + " (copy)"
        # Offset by one snap step so it's not perfectly overlapping
        snap = self.geofence_editor_widget.sb_grid_spacing.value() / max(
            self.geofence_editor_widget.sb_grid_subdivisions.value(), 1
        )
        new_zone.points = [(p[0] + snap, p[1] + snap) for p in new_zone.points]
        self._vm.add_geofence_zone(new_zone)
        self._canvas.set_geofences(self._vm.get_geofence_zones())
        self._canvas.set_selected_zone(new_zone.id)
        if getattr(new_zone, "object_type", "zone") == "room":
            self._refresh_active_room_combo()

    def _delete_selected_zone(self):
        if self._canvas.selected_anchor_idx is not None:
            self._push_undo_state("delete anchor")
        if self._canvas.delete_selected_anchor():
            self.geofence_editor_widget.txt_map_name.clear()
            return

        selected_ids = list(self._canvas.selected_zone_ids)
        if not selected_ids or not self._vm:
            QMessageBox.warning(self, "No Selection", "Select an object on the map first.")
            return

        self._push_undo_state("delete")
        any_room_deleted = False
        for selected_id in selected_ids:
            zones = self._vm.get_geofence_zones()
            deleted_zone = next((z for z in zones if z.id == selected_id), None)
            if deleted_zone and getattr(deleted_zone, "object_type", "zone") == "room":
                if hasattr(self._vm, "get_active_room_ids"):
                    active_ids = [room_id for room_id in self._vm.get_active_room_ids() if room_id != deleted_zone.id]
                    self._vm.set_active_room_ids(active_ids)
            self._vm.remove_geofence_zone(selected_id)
            if deleted_zone and getattr(deleted_zone, "object_type", "zone") == "room":
                any_room_deleted = True

        self._canvas.set_geofences(self._vm.get_geofence_zones())
        self._canvas.set_selected_zone(None)
        self.geofence_editor_widget.txt_zone_name.clear()
        self.geofence_editor_widget.txt_map_name.clear()
        if any_room_deleted:
            self._refresh_anchor_membership_from_canvas()
            self._refresh_active_room_combo()

    def _on_canvas_zone_selected(self, zone_id):
        if not zone_id:
            self.geofence_editor_widget.txt_zone_name.clear()
            self.geofence_editor_widget.txt_map_name.clear()
            return
        if not self._vm:
            return
        zones = self._vm.get_geofence_zones()
        zone = next((z for z in zones if z.id == zone_id), None)
        if zone:
            self._load_zone_properties_to_ui(zone)
            self._refresh_room_anchor_table()

    def _on_canvas_anchor_selected(self, anchor_idx):
        if anchor_idx is None or anchor_idx < 0 or anchor_idx >= len(self._canvas.anchors):
            return
        anchor = self._canvas.anchors[anchor_idx]
        self.geofence_editor_widget.editor_tabs.setCurrentIndex(0)
        self.geofence_editor_widget.txt_map_name.setText(anchor.get("label", f"A{anchor.get('anchor_id', anchor_idx)}"))
        room_type_idx = self.geofence_editor_widget.cmb_map_type.findText("Room", Qt.MatchFlag.MatchStartsWith)
        if room_type_idx >= 0:
            self.geofence_editor_widget.cmb_map_type.setCurrentIndex(room_type_idx)
        if hasattr(self.geofence_editor_widget, "sb_anchor_x"):
            self.geofence_editor_widget.sb_anchor_x.setValue(float(anchor.get("x", 0.0)))
            self.geofence_editor_widget.sb_anchor_y.setValue(float(anchor.get("y", 0.0)))
            self.geofence_editor_widget.sb_anchor_z.setValue(float(anchor.get("z", 0.0)))
        
        aid = anchor.get("anchor_id")
        if aid is not None:
            selected_idx = 0
            for idx in range(self.geofence_editor_widget.cmb_scanned_anchors.count()):
                data = self.geofence_editor_widget.cmb_scanned_anchors.itemData(idx)
                if isinstance(data, dict) and self._coerce_int_id(data.get("anchor_id"), -1) == self._coerce_int_id(aid, -2):
                    selected_idx = idx
                    break
            self.geofence_editor_widget.cmb_scanned_anchors.setCurrentIndex(selected_idx)
        else:
            self.geofence_editor_widget.cmb_scanned_anchors.setCurrentIndex(0)

        self._sync_map_height_visibility()
        self._refresh_anchor_status_label()
        self._set_context_help("anchor")
        self._set_canvas_tool_status(f"Anchor Layout / {anchor.get('label', f'A{anchor_idx}')}")

    def _on_canvas_anchor_layout_edited(self, anchors):
        if not self._vm:
            return
        if getattr(self._canvas, "dim_tracking_view", False):
            self._draft_anchor_layout = self._annotate_anchor_membership(anchors)
            self._anchor_layout_commit_pending = not self._same_anchor_layout(
                self._draft_anchor_layout,
                self._geofence_anchor_baseline,
            )
            self._vm.geofence_repo.set_anchors(self._draft_anchor_layout)
            self._refresh_anchor_status_label()
            return
        self._vm.update_anchor_layout_from_map(self._annotate_anchor_membership(anchors))

    def _on_canvas_zone_modified(self, zone_id, points):
        if not self._vm:
            return
        zones = self._vm.get_geofence_zones()
        zone = next((z for z in zones if z.id == zone_id), None)
        if zone:
            zone.points = points
            self._vm.geofence_layout_updated.emit(self._vm.get_geofence_zones())
            if getattr(zone, "object_type", "zone") == "room":
                self._refresh_anchor_membership_from_canvas()
            self._update_geometry_inspector(zone)

    def _on_canvas_zone_properties_updated(self, zone_id, props):
        if not self._vm:
            return
        zones = self._vm.get_geofence_zones()
        zone = next((z for z in zones if z.id == zone_id), None)
        if zone:
            for key, val in props.items():
                if key == "name":
                    zone.name = val
                elif key == "color":
                    zone.color = val
                elif key == "height":
                    zone.min_z = 0.0
                    zone.max_z = float(val)
                elif key == "thickness_m":
                    zone.thickness_m = float(val)
                elif key == "speed_limit":
                    zone.speed_limit = float(val)
            
            # Update sidebar fields if it's currently selected zone
            if self._canvas.selected_zone_id == zone_id:
                self.geofence_editor_widget.blockSignals(True)
                self._load_zone_properties_to_ui(zone)
                self.geofence_editor_widget.blockSignals(False)
                
            self._vm.geofence_layout_updated.emit(self._vm.get_geofence_zones())

    def _load_map(self):
        if not self._vm:
            return

        default_dir = self._maps_dir()
        os.makedirs(default_dir, exist_ok=True)

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Geofencing Map",
            default_dir,
            "Geofencing Map JSON (*.json)"
        )
        if not file_path:
            return

        undo_len = len(self._undo_stack)
        self._push_undo_state("load map")
        if not self._vm.load_geofences(file_path):
            if len(self._undo_stack) > undo_len:
                self._undo_stack.pop()
            QMessageBox.warning(self, "Load Failed", "Could not load the selected geofencing map.")
            return

        zones = self._vm.get_geofence_zones()
        map_anchors = self._annotate_anchor_membership(self._vm.get_map_anchors())
        self._vm.geofence_repo.set_anchors(map_anchors)
        self._canvas.set_geofences(zones)
        self._canvas.set_anchors(self._format_anchors_for_canvas(map_anchors))
        self._geofence_anchor_baseline = [dict(anchor) for anchor in map_anchors]
        self._draft_anchor_layout = [dict(anchor) for anchor in map_anchors]
        self._anchor_layout_commit_pending = False
        self._refresh_map_list()
        self._refresh_anchor_status_label()
        QMessageBox.information(self, "Map Loaded", f"Loaded geofencing map:\n{os.path.basename(file_path)}")

    def _save_map(self):
        if not self._vm:
            return
        self._vm.geofence_repo.set_anchors(self._annotate_anchor_membership(self._canvas.anchor_layout_for_device()))
        self._save_geofence_config()
        errors, warnings = self._validate_geofence_map()
        if errors:
            QMessageBox.warning(self, "Invalid Map", "\n".join(errors))
            return

        default_dir = self._maps_dir()
        os.makedirs(default_dir, exist_ok=True)

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Geofencing Map",
            os.path.join(default_dir, "geofence_map.json"),
            "Geofencing Map JSON (*.json)"
        )

        if file_path:
            if self._vm.save_geofences(file_path):
                warning_text = ("\n\nWarnings:\n" + "\n".join(warnings)) if warnings else ""
                QMessageBox.information(self, "Map Saved", f"Saved geofencing map:\n{os.path.basename(file_path)}{warning_text}")
                self._refresh_map_list()
            else:
                QMessageBox.warning(self, "Save Failed", "Could not save the geofencing map.")

    def _clear_map(self):
        if not self._vm:
            return
        reply = QMessageBox.question(
            self,
            "Clear Map",
            "Delete all rooms, walls, objects, and rule zones?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._push_undo_state("clear")
            self._vm.clear_geofence_zones()
            self._canvas.set_geofences([])
            self._canvas.set_anchors([])
            if getattr(self._canvas, "dim_tracking_view", False):
                self._vm.geofence_repo.set_anchors([])
                self._draft_anchor_layout = []
                self._anchor_layout_commit_pending = True
            self._canvas.set_selected_zone(None)
            self.geofence_editor_widget.txt_zone_name.clear()
            self.geofence_editor_widget.txt_map_name.clear()

    def set_developer_mode(self, is_developer: bool):
        self._is_developer_mode = is_developer
        self._canvas.is_developer_mode = is_developer
        self._canvas._show_scale_bar = is_developer
        self._canvas._show_mouse_coords = is_developer
        if is_developer:
            self.user_map_groupbox.setVisible(False)
            self.geofence_editor_widget.btn_exit_editor.setVisible(False)
            self._enter_geofence_editor()
        else:
            self.geofence_editor_widget.btn_exit_editor.setVisible(True)
            self.user_map_groupbox.setVisible(True)
            if self._vm:
                self._canvas.set_anchors(self._vm.current_anchor_layout)
            if self.sidebar_stack.currentIndex() == 1:
                self._exit_geofence_editor()

    def _on_enable_geofence_toggled(self, checked):
        self._preview_overlay_btn.setChecked(False)
        self._toggle_canvas_view_mode(False)
        if checked:
            self.chk_enable_geofence.setText("Geofence map enabled")
            if self._vm:
                file_path = self.cmb_user_map.currentData()
                if file_path and os.path.exists(file_path):
                    self._vm.load_geofences(file_path)
                else:
                    self._vm.load_geofences()
                self._canvas.set_geofences(self._vm.get_geofence_zones())
        else:
            self.chk_enable_geofence.setText("Geofence map disabled")
            self._canvas.set_geofences([])

    def _on_geofence_status_updated(self, status: str, zone_name: str, speed_limit: float):
        if not self.chk_enable_geofence.isChecked():
            self.warning_label.setVisible(False)
            return

        if status == "forbidden":
            self.warning_label.setText(f"FORBIDDEN ZONE: {zone_name}")
            self.warning_label.setStyleSheet(
                "color: white; font-size: 14px; font-weight: bold; background-color: #EF4444; padding: 2px 10px; border-radius: 4px;"
            )
            self.warning_label.setVisible(True)
        elif status == "overspeed":
            self.warning_label.setText(f"⚠️ OVERSPEED IN {zone_name.upper()}! (Limit: {speed_limit:.1f} m/s)")
            self.warning_label.setStyleSheet(
                "color: white; font-size: 14px; font-weight: bold; background-color: #F59E0B; padding: 2px 10px; border-radius: 4px;"
            )
            self.warning_label.setVisible(True)
        elif status == "allowed" and zone_name != "Default Space":
            self.warning_label.setText(f"Allowed zone: {zone_name} (Max speed: {speed_limit:.1f} m/s)")
            self.warning_label.setStyleSheet(
                "color: white; font-size: 14px; font-weight: bold; background-color: #10B981; padding: 2px 10px; border-radius: 4px;"
            )
            self.warning_label.setVisible(True)
        else:
            self.warning_label.setVisible(False)

