"""
==============================================================================
  UWB RTLS Studio - Live Tracking Tab View
==============================================================================
"""
import os
import time
import uuid

from PyQt6 import uic
from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QRect, QTimer, Qt, QPointF
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
    QMessageBox,
    QFileDialog,
    QFrame,
    QCheckBox,
    QDialog,
    QToolButton,
)
from PyQt6.QtGui import QPainter, QPen, QColor, QBrush, QFont, QPolygonF

from views.components.position_canvas import PositionCanvas
from models.geofence_model import GeofenceZone
from views.components.geofence_editor import GeofenceEditorWidget


class _PreviewPane(QWidget):
    def __init__(self, source, mode="top", parent=None):
        super().__init__(parent)
        self._source = source
        self._mode = mode
        self.setMinimumSize(760, 420)
        self.setStyleSheet("background: #0F172A; border: 1px solid #334155; border-radius: 8px;")

    def set_mode(self, mode: str):
        self._mode = "angled" if mode == "angled" else "top"
        self.update()

    def _zones(self):
        if hasattr(self._source, "get_geofence_zones"):
            return self._source.get_geofence_zones()
        return getattr(self._source, "geofence_zones", [])

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(15, 23, 42))

        zones = self._zones()

        if self._mode == "top":
            self._draw_top_view(painter, zones)
        else:
            self._draw_angled_view(painter, zones)

    def _zone_height(self, zone):
        if getattr(zone, "object_type", "zone") not in {"room", "wall"}:
            return 0.0
        return max(0.1, float(zone.max_z - zone.min_z))

    def _style_for_zone(self, zone, alpha=60):
        object_type = getattr(zone, "object_type", "zone")
        if object_type == "room":
            return QColor(248, 250, 252, alpha), QColor(226, 232, 240)
        if object_type == "wall":
            return QColor(100, 116, 139, max(alpha + 35, 100)), QColor(51, 65, 85)
        if zone.zone_type == "forbidden":
            return QColor(239, 68, 68, alpha), QColor(239, 68, 68)
        return QColor(34, 197, 94, alpha), QColor(34, 197, 94)

    def _bounds(self, zones):
        pts = []
        for zone in zones:
            if getattr(zone, "object_type", "zone") in {"room", "wall", "zone"}:
                pts.extend(zone.points)
        if not pts:
            return 0, 0, 10, 10
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        pad = 0.5
        return min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad

    def _map_point(self, x, y, bounds, rect):
        min_x, min_y, max_x, max_y = bounds
        w = max(max_x - min_x, 0.1)
        h = max(max_y - min_y, 0.1)
        sx = rect.left() + 18 + (x - min_x) / w * (rect.width() - 36)
        sy = rect.bottom() - 18 - (y - min_y) / h * (rect.height() - 36)
        return QPointF(sx, sy)

    def _draw_top_view(self, painter, zones):
        rect = self.rect().adjusted(12, 12, -12, -12)
        painter.setPen(QPen(QColor(34, 211, 238), 1))
        painter.setBrush(QColor(15, 23, 42))
        painter.drawRoundedRect(rect, 8, 8)

        bounds = self._bounds(zones)
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        painter.setPen(QColor(226, 232, 240))
        painter.drawText(16, 22, "Top View")

        for zone in zones:
            if len(zone.points) < 3:
                continue
            poly = QPolygonF()
            for x, y in zone.points:
                poly.append(self._map_point(x, y, bounds, rect))
            fill, border = self._style_for_zone(zone, 50)
            painter.setBrush(QBrush(fill))
            width = 3 if getattr(zone, "object_type", "zone") == "wall" else 2
            painter.setPen(QPen(border, width))
            painter.drawPolygon(poly)

    def _angle_raw_point(self, x, y, z, bounds):
        min_x, min_y, max_x, max_y = bounds
        cx = (min_x + max_x) / 2.0
        h = max(max_y - min_y, 0.1)
        dx = x - cx
        depth = (y - min_y) / h
        front_depth = depth - 0.5
        perspective = 1.0 - depth * 0.14
        return dx * perspective, -front_depth * 2.5 - z * 1.75

    def _angle_bounds(self, zones, bounds):
        raw = []
        corners = [
            (bounds[0], bounds[1]),
            (bounds[2], bounds[1]),
            (bounds[2], bounds[3]),
            (bounds[0], bounds[3]),
        ]
        for x, y in corners:
            raw.append(self._angle_raw_point(x, y, 0.0, bounds))
        for zone in zones:
            height = self._zone_height(zone)
            for x, y in zone.points:
                raw.append(self._angle_raw_point(x, y, 0.0, bounds))
                if height > 0:
                    raw.append(self._angle_raw_point(x, y, height, bounds))
        xs = [p[0] for p in raw]
        ys = [p[1] for p in raw]
        return min(xs), min(ys), max(xs), max(ys)

    def _angle_point(self, x, y, z, bounds, angle_bounds, rect):
        raw_x, raw_y = self._angle_raw_point(x, y, z, bounds)
        min_x, min_y, max_x, max_y = angle_bounds
        w = max(max_x - min_x, 0.1)
        h = max(max_y - min_y, 0.1)
        scale = min((rect.width() - 72) / w, (rect.height() - 76) / h)
        cx = rect.center().x()
        cy = rect.center().y() + 12
        return QPointF(
            cx + (raw_x - (min_x + max_x) / 2.0) * scale,
            cy + (raw_y - (min_y + max_y) / 2.0) * scale,
        )

    def _angle_poly(self, zone, z, bounds, angle_bounds, rect):
        poly = QPolygonF()
        for x, y in zone.points:
            poly.append(self._angle_point(x, y, z, bounds, angle_bounds, rect))
        return poly

    def _draw_angled_view(self, painter, zones):
        rect = self.rect().adjusted(12, 12, -12, -12)
        painter.setPen(QPen(QColor(245, 158, 11), 1))
        painter.setBrush(QColor(15, 23, 42))
        painter.drawRoundedRect(rect, 8, 8)
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        painter.setPen(QColor(226, 232, 240))
        painter.drawText(16, 22, "Angled View")

        bounds = self._bounds(zones)
        angle_bounds = self._angle_bounds(zones, bounds)
        floor = QPolygonF()
        for x, y in [(bounds[0], bounds[1]), (bounds[2], bounds[1]), (bounds[2], bounds[3]), (bounds[0], bounds[3])]:
            floor.append(self._angle_point(x, y, 0.0, bounds, angle_bounds, rect))

        painter.setBrush(QColor(30, 41, 59, 210))
        painter.setPen(QPen(QColor(71, 85, 105), 2))
        painter.drawPolygon(floor)

        sorted_zones = sorted(
            zones,
            key=lambda z: sum(p[1] for p in z.points) / max(len(z.points), 1),
        )
        for zone in sorted_zones:
            if len(zone.points) < 3:
                continue

            height = self._zone_height(zone)
            base_poly = self._angle_poly(zone, 0.0, bounds, angle_bounds, rect)
            fill, border = self._style_for_zone(zone, 58)
            if height <= 0:
                painter.setBrush(QBrush(fill))
                painter.setPen(QPen(border, 2, Qt.PenStyle.DashLine))
                painter.drawPolygon(base_poly)
                continue

            top_poly = self._angle_poly(zone, height, bounds, angle_bounds, rect)
            side_color = QColor(border)
            side_color.setAlpha(95 if getattr(zone, "object_type", "zone") == "room" else 145)
            painter.setBrush(QBrush(side_color))
            painter.setPen(QPen(QColor(51, 65, 85), 1))
            for idx in range(base_poly.count()):
                nxt = (idx + 1) % base_poly.count()
                face = QPolygonF([base_poly[idx], base_poly[nxt], top_poly[nxt], top_poly[idx]])
                painter.drawPolygon(face)

            top_fill = QColor(fill)
            top_fill.setAlpha(82 if getattr(zone, "object_type", "zone") == "room" else 165)
            painter.setBrush(QBrush(top_fill))
            painter.setPen(QPen(border, 2))
            painter.drawPolygon(top_poly)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(226, 232, 240, 150), 1))
            for idx in range(base_poly.count()):
                painter.drawLine(base_poly[idx], top_poly[idx])


class GeofencePreviewDialog(QDialog):
    def __init__(self, source, parent=None):
        super().__init__(parent)
        self._source = source
        self.setWindowTitle("Geofence Preview")
        self.resize(920, 620)
        self.setStyleSheet("background: #0F172A; color: #F8FAFC;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("Geofence Preview")
        title.setStyleSheet("color: #22D3EE; font-size: 15px; font-weight: bold;")
        header.addWidget(title)
        header.addStretch()

        self.btn_top_view = QPushButton("Top")
        self.btn_angle_view = QPushButton("Angle")
        for btn in (self.btn_top_view, self.btn_angle_view):
            btn.setCheckable(True)
            btn.setMinimumSize(76, 30)
            btn.setStyleSheet(
                "QPushButton { background: #1E293B; color: #CBD5E1; border: 1px solid #334155; "
                "border-radius: 6px; font-weight: bold; }"
                "QPushButton:checked { background: #2563EB; color: white; border-color: #38BDF8; }"
            )
            header.addWidget(btn)
        layout.addLayout(header)

        self.preview_pane = _PreviewPane(source, "top", self)
        layout.addWidget(self.preview_pane)

        self.btn_top_view.clicked.connect(lambda: self._set_view_mode("top"))
        self.btn_angle_view.clicked.connect(lambda: self._set_view_mode("angled"))
        self._set_view_mode("top")

    def _set_view_mode(self, mode: str):
        is_angle = mode == "angled"
        self.btn_top_view.setChecked(not is_angle)
        self.btn_angle_view.setChecked(is_angle)
        self.preview_pane.set_mode("angled" if is_angle else "top")


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

        uic.loadUi(UI_FILE, self)
        self._setup_dynamic_metrics()

        self._canvas = self.position_canvas
        self._canvas.parent_tab = self
        self._preview_dialog = None
        self._preview_overlay_btn = QToolButton(self._canvas)

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
        btn.setText("2.5D Preview")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        btn.setAutoRaise(True)
        btn.setFixedSize(136, 36)
        btn.setStyleSheet(
            "QToolButton { background: rgba(17, 24, 39, 230); color: #F8FAFC; border: 1px solid #FACC15; "
            "border-radius: 8px; font-weight: bold; padding: 5px 10px; }"
            "QToolButton:hover { background: rgba(30, 41, 59, 245); border-color: #FDE047; }"
            "QToolButton:pressed { background: rgba(15, 23, 42, 245); }"
        )
        btn.clicked.connect(self._open_preview_dialog)
        btn.raise_()
        self._position_canvas_preview_button()

    def _position_canvas_preview_button(self):
        if not hasattr(self, "_preview_overlay_btn"):
            return
        canvas = self._canvas
        sidebar_w = self.right_widget.width() if self.sidebar_expanded else 0
        x = max(canvas.width() - sidebar_w - self._preview_overlay_btn.width() - 28, 12)
        y = 10
        self._preview_overlay_btn.move(x, y)
        self._preview_overlay_btn.raise_()

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
        # Clear existing layout children just in case
        while self.pos_grid.count():
            item = self.pos_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.pos_grid.setSpacing(10)

        # 7 groups of metrics based on the RTOS dashboard design combined with studio specific telemetry
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

        # Backward compatibility aliases
        self.x_label = self.ukf_x_label
        self.y_label = self.ukf_y_label
        self.err_cnt_label = self.error_frame_cnt_label
        # Alias for temporary compatibilities
        self.tril_xy_label = self.tril_x_label
        self.raw_yaw_label = self.yaw_label

        # Setup stats grid
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

    def set_viewmodel(self, vm):
        self._vm = vm
        self._vm.ranging_started.connect(self._on_ranging_started)
        self._vm.ranging_stopped.connect(self._on_ranging_stopped)
        self._vm.position_updated.connect(self._on_position_updated)
        self._vm.sensor_fusion_updated.connect(self._on_sensor_fusion_updated)
        self._vm.anchor_distances_updated.connect(self._on_anchor_distances)
        self._vm.anchor_layout_updated.connect(self._on_anchor_layout_updated)
        self._vm.stats_updated.connect(self._on_stats_updated)
        
        # Connect Geofencing signals
        self._vm.geofence_status_updated.connect(self._on_geofence_status_updated)
        self._vm.geofence_layout_updated.connect(self._canvas.set_geofences)
        
        # Load any existing geofence maps on startup
        self._vm.load_geofences()
        self._canvas.set_geofences(self._vm.get_geofence_zones())
        
        current_layout = getattr(self._vm, "current_anchor_layout", [])
        if current_layout:
            self._on_anchor_layout_updated(current_layout)

    def _on_anchor_layout_updated(self, anchors_list):
        formatted = []
        for anchor in anchors_list:
            if anchor.get("x_m") is None or anchor.get("y_m") is None:
                continue
            formatted.append(
                {
                    "x": anchor["x_m"],
                    "y": anchor["y_m"],
                    "label": f"A{anchor['anchor_id']}",
                }
            )
        self.set_anchors(formatted)

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

        # Update Frame and Counters
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
        self._set_metric_value(self.length_label, 33, "{:d}") # ranging_result size mock
        if anchor_mask:
            self._set_metric_value(self.anchor_mask_label, f"0x{anchor_mask:02X} ({anchor_mask:08b})")
        else:
            self._set_metric_value(self.anchor_mask_label, "--")
        
        self._set_metric_value(self.fusion_ts_label, timestamp_ms, "{:d}")
        self._set_metric_value(self.tx_frame_cnt_label, seq, "{:d}")

        # Update Trilateration and UKF (if fusion isn't running)
        is_fusion_stale = (time.time() - getattr(self, "_last_fusion_time", 0.0) > 2.0)
        
        if is_fusion_stale:
            # Under raw ranging mode, show trilateration coordinates as UKF too to provide visualization
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

        # Update Frame and Counters
        self._set_metric_value(self.sof_label, "0xAA")
        self._set_metric_value(self.length_label, 33, "{:d}") # payload_size
        
        anchor_mask = getattr(self, "_last_anchor_mask", 0)
        if anchor_mask:
            self._set_metric_value(self.anchor_mask_label, f"0x{anchor_mask:02X} ({anchor_mask:08b})")
        else:
            self._set_metric_value(self.anchor_mask_label, "--")

        self._set_metric_value(self.fusion_ts_label, timestamp_ms, "{:d}")
        self._set_metric_value(self.tx_frame_cnt_label, seq, "{:d}")
        self._set_metric_value(self.error_frame_cnt_label, err_count, "{:d}")

        # Update UKF
        self._set_metric_value(self.ukf_x_label, x)
        self._set_metric_value(self.ukf_y_label, y)
        self._set_metric_value(self.ukf_yaw_label, yaw, "{:.1f}")

        # Update TRILATERATION
        self._set_metric_value(self.tril_x_label, tril_x)
        self._set_metric_value(self.tril_y_label, tril_y)
        self._set_metric_value(self.yaw_label, raw_yaw, "{:.1f}")

        # Update MOTION
        self._set_metric_value(self.vx_label, vx)
        self._set_metric_value(self.vy_label, vy)

        # Update QUALITY (Z Height and Error)
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

    # --- 2.5D GEOFENCING IMPLEMENTATION ---

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

        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        maps_dir = os.path.join(base_dir, "data", "runtime")

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

    def _on_user_map_changed(self, index):
        if index < 0:
            return
        if self.chk_enable_geofence.isChecked():
            file_path = self.cmb_user_map.itemData(index)
            if self._vm and file_path and os.path.exists(file_path):
                self._vm.load_geofences(file_path)
                self._canvas.set_geofences(self._vm.get_geofence_zones())

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
        editor.editor_tabs.currentChanged.connect(self._on_editor_tab_changed)
        editor.btn_mode_room.clicked.connect(lambda: self._set_editor_tool("room", "draw"))
        editor.btn_mode_wall.clicked.connect(lambda: self._set_editor_tool("wall", "draw"))
        editor.btn_mode_edit_map.clicked.connect(lambda: self._set_editor_mode("edit_vertices"))
        editor.btn_mode_draw.clicked.connect(lambda: self._set_editor_tool("zone", "draw"))
        editor.btn_mode_edit.clicked.connect(lambda: self._set_editor_mode("edit_vertices"))
        editor.sb_grid_spacing.valueChanged.connect(self._update_grid_settings)
        editor.sb_grid_subdivisions.valueChanged.connect(self._update_grid_settings)
        editor.btn_apply_map_properties.clicked.connect(self._apply_map_properties)
        editor.btn_apply_properties.clicked.connect(self._apply_zone_properties)
        editor.btn_delete_zone.clicked.connect(self._delete_selected_zone)
        editor.btn_save_map.clicked.connect(self._save_map)
        editor.btn_clear_map.clicked.connect(self._clear_map)
        editor.btn_exit_editor.clicked.connect(self._exit_geofence_editor)

        self._canvas.polygon_completed.connect(self._on_canvas_polygon_completed)
        self._canvas.zone_selected.connect(self._on_canvas_zone_selected)
        self._canvas.zone_modified.connect(self._on_canvas_zone_modified)

        self._update_grid_settings()
        self._set_editor_tool("room", "draw")

    def _enter_geofence_editor(self):
        self._canvas.dim_tracking_view = True
        self.user_map_groupbox.setVisible(False)
        self.sidebar_stack.setCurrentIndex(1)
        self.canvas_header.setText("Geofencing Map Setup")

        if self._canvas.edit_mode == "navigate":
            self._set_editor_tool("room", "draw")
        if self._vm:
            self._canvas.set_geofences(self._vm.get_geofence_zones())

    def _exit_geofence_editor(self):
        if self._is_developer_mode:
            self._enter_geofence_editor()
            return

        self._canvas.dim_tracking_view = False
        self._canvas.set_edit_mode("navigate")
        self.sidebar_stack.setCurrentIndex(0)
        self.canvas_header.setText("Real-time Position Tracking")
        self.user_map_groupbox.setVisible(True)

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

    def _on_editor_tab_changed(self, index):
        if index == 0:
            self._set_editor_tool("room", "draw")
        else:
            self._set_editor_tool("zone", "draw")

    def _set_editor_tool(self, object_type: str, mode: str):
        self._canvas.set_draw_object_type(object_type)
        target_tab = 1 if object_type == "zone" else 0
        if self.geofence_editor_widget.editor_tabs.currentIndex() != target_tab:
            self.geofence_editor_widget.editor_tabs.blockSignals(True)
            self.geofence_editor_widget.editor_tabs.setCurrentIndex(target_tab)
            self.geofence_editor_widget.editor_tabs.blockSignals(False)
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
        self.geofence_editor_widget.btn_mode_edit_map.setChecked(is_edit and is_map_tab)
        self.geofence_editor_widget.btn_mode_edit.setChecked(is_edit and not is_map_tab)

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
        else:
            number = sum(1 for obj in objects if getattr(obj, "object_type", "zone") == object_type) + 1
            new_zone = GeofenceZone(
                id=zone_id,
                name=f"{object_type.title()} {number}",
                zone_type=object_type,
                points=points,
                min_z=0.0,
                max_z=self.geofence_editor_widget.sb_map_height.value(),
                speed_limit=0.0,
                color="#F8FAFC" if object_type == "room" else "#94A3B8",
                object_type=object_type,
            )

        self._vm.add_geofence_zone(new_zone)
        self._canvas.set_geofences(self._vm.get_geofence_zones())
        self._canvas.set_selected_zone(zone_id)
        self._load_zone_properties_to_ui(new_zone)

    def _load_zone_properties_to_ui(self, zone):
        object_type = getattr(zone, "object_type", "zone")
        if object_type == "zone":
            self.geofence_editor_widget.editor_tabs.setCurrentIndex(1)
            self.geofence_editor_widget.txt_zone_name.setText(zone.name)
            self.geofence_editor_widget.cmb_zone_type.setCurrentIndex(0 if zone.zone_type == "allowed" else 1)
            self.geofence_editor_widget.sb_speed.setValue(zone.speed_limit)
            self._canvas.set_draw_object_type("zone")
        else:
            self.geofence_editor_widget.editor_tabs.setCurrentIndex(0)
            self.geofence_editor_widget.txt_map_name.setText(zone.name)
            self.geofence_editor_widget.cmb_map_type.setCurrentIndex(0 if object_type == "room" else 1)
            self.geofence_editor_widget.sb_map_height.setValue(max(0.1, zone.max_z - zone.min_z))
            self._canvas.set_draw_object_type(object_type)
        self._set_editor_mode("edit_vertices")

    def _apply_map_properties(self):
        selected_id = self._canvas.selected_zone_id
        if not selected_id or not self._vm:
            QMessageBox.warning(self, "No Selection", "Select a room or wall on the map first.")
            return

        objects = self._vm.get_geofence_zones()
        zone = next((z for z in objects if z.id == selected_id), None)
        if not zone or getattr(zone, "object_type", "zone") == "zone":
            QMessageBox.warning(self, "Wrong Object Type", "The selected object is a rule zone, not a map object.")
            return

        object_type = "room" if self.geofence_editor_widget.cmb_map_type.currentIndex() == 0 else "wall"
        zone.object_type = object_type
        zone.zone_type = object_type
        zone.name = self.geofence_editor_widget.txt_map_name.text().strip() or object_type.title()
        zone.min_z = 0.0
        zone.max_z = self.geofence_editor_widget.sb_map_height.value()
        zone.speed_limit = 0.0
        zone.color = "#F8FAFC" if object_type == "room" else "#94A3B8"
        self._vm.geofence_layout_updated.emit(self._vm.get_geofence_zones())
        self._canvas.update()

    def _apply_zone_properties(self):
        selected_id = self._canvas.selected_zone_id
        if not selected_id or not self._vm:
            QMessageBox.warning(self, "No Selection", "Select a rule zone on the map first.")
            return

        objects = self._vm.get_geofence_zones()
        zone = next((z for z in objects if z.id == selected_id), None)
        if not zone or getattr(zone, "object_type", "zone") != "zone":
            QMessageBox.warning(self, "Wrong Object Type", "The selected object is a room or wall, not a rule zone.")
            return

        zone.name = self.geofence_editor_widget.txt_zone_name.text().strip() or "Rule Zone"
        zone.zone_type = "allowed" if self.geofence_editor_widget.cmb_zone_type.currentIndex() == 0 else "forbidden"
        zone.min_z = 0.0
        zone.max_z = 0.0
        zone.speed_limit = self.geofence_editor_widget.sb_speed.value()
        zone.color = "#22C55E" if zone.zone_type == "allowed" else "#EF4444"
        zone.object_type = "zone"
        self._vm.geofence_layout_updated.emit(self._vm.get_geofence_zones())
        self._canvas.update()

    def _delete_selected_zone(self):
        selected_id = self._canvas.selected_zone_id
        if not selected_id or not self._vm:
            QMessageBox.warning(self, "No Selection", "Select an object on the map first.")
            return
        self._vm.remove_geofence_zone(selected_id)
        self._canvas.set_geofences(self._vm.get_geofence_zones())
        self._canvas.set_selected_zone(None)
        self.geofence_editor_widget.txt_zone_name.clear()
        self.geofence_editor_widget.txt_map_name.clear()

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

    def _on_canvas_zone_modified(self, zone_id, points):
        if not self._vm:
            return
        zones = self._vm.get_geofence_zones()
        zone = next((z for z in zones if z.id == zone_id), None)
        if zone:
            zone.points = points
            self._vm.geofence_layout_updated.emit(self._vm.get_geofence_zones())

    def _save_map(self):
        if not self._vm:
            return

        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        default_dir = os.path.join(base_dir, "data", "runtime")
        os.makedirs(default_dir, exist_ok=True)

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Geofencing Map",
            os.path.join(default_dir, "geofence_map.json"),
            "Geofencing Map JSON (*.json)"
        )

        if file_path:
            if self._vm.save_geofences(file_path):
                QMessageBox.information(self, "Map Saved", f"Saved geofencing map:\n{os.path.basename(file_path)}")
                self._refresh_map_list()
            else:
                QMessageBox.warning(self, "Save Failed", "Could not save the geofencing map.")

    def _clear_map(self):
        if not self._vm:
            return
        reply = QMessageBox.question(
            self,
            "Clear Map",
            "Delete all rooms, walls, and rule zones?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._vm.clear_geofence_zones()
            self._canvas.set_geofences([])
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
            self._canvas.set_25d_preview(self.chk_enable_geofence.isChecked())
            if self.sidebar_stack.currentIndex() == 1:
                self._exit_geofence_editor()

    def _on_enable_geofence_toggled(self, checked):
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
        elif status == "allowed" and zone_name != "Default Space":
            self.warning_label.setText(f"Allowed zone: {zone_name} (Max speed: {speed_limit:.1f} m/s)")
            self.warning_label.setStyleSheet(
                "color: white; font-size: 14px; font-weight: bold; background-color: #10B981; padding: 2px 10px; border-radius: 4px;"
            )
            self.warning_label.setVisible(True)
        else:
            self.warning_label.setVisible(False)

    def _open_preview_dialog(self):
        source = self._vm if self._vm else self._canvas
        if self._preview_dialog is None:
            self._preview_dialog = GeofencePreviewDialog(source, parent=self)
        self._preview_dialog.preview_pane.update()
        self._preview_dialog.show()
        self._preview_dialog.raise_()
        self._preview_dialog.activateWindow()
