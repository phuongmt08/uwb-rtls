"""
==============================================================================
  UWB RTLS Studio - Live Tracking Tab View
==============================================================================
"""
import os
import time
import uuid
import math
import logging

log = logging.getLogger(__name__)

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
    QGridLayout,
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
from utils.config_dim import GRID_SPACING_M


class _PreviewPane(QWidget):
    def __init__(self, source, mode="top", parent=None):
        super().__init__(parent)
        self._source = source
        self._mode = mode
        self.setMinimumSize(760, 420)
        self.setStyleSheet("background: #DDE3EA; border: 1px solid #CBD5E1; border-radius: 8px;")

    def set_mode(self, mode: str):
        self._mode = "angled" if mode == "angled" else "top"
        self.update()

    def _zones(self):
        if hasattr(self._source, "get_geofence_zones"):
            base_zones = list(self._source.get_geofence_zones())
        else:
            base_zones = list(getattr(self._source, "geofence_zones", []))

        # Check if the canvas has an in-progress drawing.
        dialog = self.parent()
        tab = dialog.parent() if dialog is not None else None
        if hasattr(tab, "_canvas"):
            canvas = tab._canvas
            if canvas.edit_mode == "draw" and len(canvas.current_draw_points) > 0:
                object_type = canvas.draw_object_type
                height = tab.geofence_editor_widget.sb_map_height.value() if object_type == "wall" else 0.0
                color = "#F8FAFC" if object_type == "room" else ("#0F172A" if object_type == "wall" else "#22C55E")
                
                # Combine current points with current mouse position to show real-time line
                pts = list(canvas.current_draw_points)
                if getattr(canvas, "mouse_world_pos", None):
                    pts.append(canvas.mouse_world_pos)
                
                temp_zone = GeofenceZone(
                    id="temp_drawing",
                    name="Drawing...",
                    zone_type=object_type,
                    points=pts,
                    min_z=0.0,
                    max_z=height,
                    color=color,
                    object_type=object_type
                )
                base_zones.append(temp_zone)
        return base_zones

    def _anchors(self):
        dialog = self.parent()
        tab = dialog.parent() if dialog is not None else None
        if hasattr(tab, "_canvas"):
            return list(getattr(tab._canvas, "anchors", []))
        if hasattr(self._source, "get_map_anchors"):
            return list(self._source.get_map_anchors())
        return []

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(221, 227, 234))

        zones = self._zones()

        if self._mode == "top":
            self._draw_top_view(painter, zones)
        else:
            self._draw_angled_view(painter, zones)

    def _zone_height(self, zone):
        if getattr(zone, "object_type", "zone") not in {"wall"}:
            return 0.0
        return max(0.1, float(zone.max_z - zone.min_z))

    def _style_for_zone(self, zone, alpha=60):
        object_type = getattr(zone, "object_type", "zone")
        if object_type == "room":
            base_color = zone.color.replace("_semi", "")
            is_semi = zone.color.endswith("_semi")
            fill_color = QColor(base_color if base_color.startswith("#") else "#F8FAFC")
            fill_color.setAlpha(48 if is_semi else max(alpha, 120))
            border_color = QColor(148, 163, 184)
            return fill_color, border_color
        elif object_type == "wall":
            fill_color = QColor(zone.color)
            fill_color.setAlpha(max(alpha + 55, 150))
            border_color = QColor(zone.color)
            return fill_color, border_color
        elif zone.zone_type == "forbidden":
            fill_color = QColor(zone.color)
            fill_color.setAlpha(alpha)
            border_color = QColor(zone.color)
            return fill_color, border_color
        else:
            fill_color = QColor(zone.color)
            fill_color.setAlpha(alpha)
            border_color = QColor(zone.color)
            return fill_color, border_color

    def _bounds(self, zones):
        pts = []
        for zone in zones:
            if getattr(zone, "object_type", "zone") in {"room", "wall", "zone"}:
                pts.extend(zone.points)
        for anchor in self._anchors():
            pts.append((anchor.get("x", anchor.get("x_m", 0.0)), anchor.get("y", anchor.get("y_m", 0.0))))
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
        painter.setPen(QPen(QColor(14, 116, 144), 1))
        painter.setBrush(QColor(255, 255, 255))
        painter.drawRoundedRect(rect, 8, 8)

        bounds = self._bounds(zones)
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        painter.setPen(QColor(30, 41, 59))
        painter.drawText(16, 22, "Top View")

        # Draw floor grid lines
        step = GRID_SPACING_M
        min_x, min_y, max_x, max_y = bounds
        grid_x_start = math.ceil(min_x / step) * step
        grid_y_start = math.ceil(min_y / step) * step

        painter.save()
        grid_pen = QPen(QColor(15, 23, 42, 30), 1, Qt.PenStyle.DotLine)
        painter.setPen(grid_pen)
        
        # Vertical grid lines (constant x)
        x = grid_x_start
        grid_guard = 0
        while x <= max_x and grid_guard < 1000:
            p1 = self._map_point(x, min_y, bounds, rect)
            p2 = self._map_point(x, max_y, bounds, rect)
            painter.drawLine(p1, p2)
            x += step
            grid_guard += 1

        # Horizontal grid lines (constant y)
        y = grid_y_start
        grid_guard = 0
        while y <= max_y and grid_guard < 1000:
            p1 = self._map_point(min_x, y, bounds, rect)
            p2 = self._map_point(max_x, y, bounds, rect)
            painter.drawLine(p1, p2)
            y += step
            grid_guard += 1

        painter.restore()

        # Sort zones: Rooms first, then Rule Zones, Walls on top
        def get_priority(z):
            obj_type = getattr(z, "object_type", "zone")
            if obj_type == "room":
                return 0
            elif obj_type == "zone":
                return 1
            else: # wall
                return 2
        for zone in sorted(zones, key=get_priority):
            if len(zone.points) < 3:
                if zone.id == "temp_drawing" and len(zone.points) >= 1:
                    painter.save()
                    fill, border = self._style_for_zone(zone, 80)
                    painter.setPen(QPen(border, 2, Qt.PenStyle.DashLine))
                    p_prev = None
                    for x, y in zone.points:
                        p_curr = self._map_point(x, y, bounds, rect)
                        if p_prev is not None:
                            painter.drawLine(p_prev, p_curr)
                        p_prev = p_curr
                    painter.restore()
                continue
            poly = QPolygonF()
            for x, y in zone.points:
                poly.append(self._map_point(x, y, bounds, rect))
            fill, border = self._style_for_zone(zone, 50)
            painter.setBrush(QBrush(fill))
            width = 3 if getattr(zone, "object_type", "zone") == "wall" else 2
            painter.setPen(QPen(border, width))
            painter.drawPolygon(poly)

        self._draw_preview_anchors(painter, bounds, rect, angled=False)

    def _angle_raw_point(self, x, y, z, bounds):
        min_x, min_y, max_x, max_y = bounds
        w = max(max_x - min_x, 0.1)
        h = max(max_y - min_y, 0.1)
        nx = (x - min_x) / w
        ny = (y - min_y) / h
        # Keep coordinates proportional to the real map and only add a mild oblique lift for depth.
        return nx * 2.0 + ny * 0.12, -(ny * 1.15) - z * 0.18

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
        painter.setPen(QPen(QColor(194, 120, 3), 1))
        painter.setBrush(QColor(221, 227, 234))
        painter.drawRoundedRect(rect, 8, 8)
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        painter.setPen(QColor(30, 41, 59))
        painter.drawText(16, 22, "Angled View")

        bounds = self._bounds(zones)
        angle_bounds = self._angle_bounds(zones, bounds)
        floor = QPolygonF()
        for x, y in [(bounds[0], bounds[1]), (bounds[2], bounds[1]), (bounds[2], bounds[3]), (bounds[0], bounds[3])]:
            floor.append(self._angle_point(x, y, 0.0, bounds, angle_bounds, rect))

        painter.setBrush(QColor(241, 245, 249))
        painter.setPen(QPen(QColor(203, 213, 225), 2))
        painter.drawPolygon(floor)

        # Draw floor grid lines (angled projection)
        step = GRID_SPACING_M
        min_x, min_y, max_x, max_y = bounds
        grid_x_start = math.ceil(min_x / step) * step
        grid_y_start = math.ceil(min_y / step) * step

        painter.save()
        grid_pen = QPen(QColor(15, 23, 42, 30), 1, Qt.PenStyle.DotLine)
        painter.setPen(grid_pen)
        
        # Vertical grid lines (constant x)
        x = grid_x_start
        grid_guard = 0
        while x <= max_x and grid_guard < 1000:
            p1 = self._angle_point(x, min_y, 0.0, bounds, angle_bounds, rect)
            p2 = self._angle_point(x, max_y, 0.0, bounds, angle_bounds, rect)
            painter.drawLine(p1, p2)
            x += step
            grid_guard += 1

        # Horizontal grid lines (constant y)
        y = grid_y_start
        grid_guard = 0
        while y <= max_y and grid_guard < 1000:
            p1 = self._angle_point(min_x, y, 0.0, bounds, angle_bounds, rect)
            p2 = self._angle_point(max_x, y, 0.0, bounds, angle_bounds, rect)
            painter.drawLine(p1, p2)
            y += step
            grid_guard += 1

        painter.restore()

        sorted_zones = sorted(
            zones,
            key=lambda z: sum(p[1] for p in z.points) / max(len(z.points), 1),
        )
        for zone in sorted_zones:
            if len(zone.points) < 3:
                if zone.id == "temp_drawing" and len(zone.points) >= 1:
                    painter.save()
                    fill, border = self._style_for_zone(zone, 80)
                    painter.setPen(QPen(border, 2, Qt.PenStyle.DashLine))
                    p_prev = None
                    for x, y in zone.points:
                        p_curr = self._angle_point(x, y, 0.0, bounds, angle_bounds, rect)
                        if p_prev is not None:
                            painter.drawLine(p_prev, p_curr)
                        p_prev = p_curr
                    painter.restore()
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
            painter.setPen(QPen(border, 1))
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
            vertical_pen_color = QColor(border)
            vertical_pen_color.setAlpha(150)
            painter.setPen(QPen(vertical_pen_color, 1))
            for idx in range(base_poly.count()):
                painter.drawLine(base_poly[idx], top_poly[idx])

        self._draw_preview_anchors(painter, bounds, rect, angled=True, angle_bounds=angle_bounds)

    def _draw_preview_anchors(self, painter, bounds, rect, angled=False, angle_bounds=None):
        painter.save()
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        for idx, anchor in enumerate(self._anchors()):
            x = float(anchor.get("x", anchor.get("x_m", 0.0)))
            y = float(anchor.get("y", anchor.get("y_m", 0.0)))
            z = float(anchor.get("z", anchor.get("z_m", 0.0)))
            if angled and angle_bounds is not None:
                p = self._angle_point(x, y, z, bounds, angle_bounds, rect)
            else:
                p = self._map_point(x, y, bounds, rect)
            dialog = self.parent()
            tab = dialog.parent() if dialog is not None else None
            selected = getattr(tab._canvas, "selected_anchor_idx", None) == idx if hasattr(tab, "_canvas") else False
            ring = QColor(250, 204, 21) if selected else QColor(14, 165, 233)
            painter.setPen(QPen(ring, 2))
            painter.setBrush(QColor(15, 23, 42))
            painter.drawEllipse(int(p.x() - 6), int(p.y() - 6), 12, 12)
            painter.setPen(QColor(15, 23, 42))
            painter.drawText(int(p.x() + 8), int(p.y() - 7), anchor.get("label", f"A{anchor.get('anchor_id', idx)}"))
        painter.restore()


class GeofencePreviewDialog(QDialog):
    def __init__(self, source, parent=None):
        super().__init__(parent)
        self._source = source
        self.setWindowTitle("Geofence Preview")
        self.resize(920, 620)
        self.setStyleSheet("background: #F8FAFC; color: #0F172A;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("Geofence Preview")
        title.setStyleSheet("color: #0E7490; font-size: 15px; font-weight: bold;")
        header.addWidget(title)
        header.addStretch()

        self.btn_top_view = QPushButton("Top")
        self.btn_angle_view = QPushButton("Angle")
        for btn in (self.btn_top_view, self.btn_angle_view):
            btn.setCheckable(True)
            btn.setMinimumSize(76, 30)
            btn.setStyleSheet(
                "QPushButton { background: #E2E8F0; color: #1E293B; border: 1px solid #CBD5E1; "
                "border-radius: 6px; font-weight: bold; }"
                "QPushButton:checked { background: #2563EB; color: white; border-color: #2563EB; }"
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
        self._anchor_layout_commit_pending = False
        self._draft_anchor_layout = []
        self._geofence_anchor_baseline = []
        self._pending_layout_read_for_editor = False

        uic.loadUi(UI_FILE, self)
        self._setup_dynamic_metrics()

        self._canvas = self.position_canvas
        self._canvas.parent_tab = self
        self._preview_dialog = None
        self._preview_overlay_btn = QToolButton(self)

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
        self._position_canvas_preview_button()

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
        
        if hasattr(self._vm, "scan_devices_updated"):
            self._vm.scan_devices_updated.connect(self._on_scan_devices_updated)
            self._on_scan_devices_updated(self._vm.get_scan_devices())
        
        # Load any existing geofence maps on startup
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
            "x_m": float(anchor.get("x_m", anchor.get("x", 0.0))),
            "y_m": float(anchor.get("y_m", anchor.get("y", 0.0))),
            "z_m": float(anchor.get("z_m", anchor.get("z", 0.0))),
            "placed": bool(anchor.get("placed", True)),
            "is_scanned": bool(anchor.get("is_scanned", anchor.get("scan_seen", False))),
            "sync_state": anchor.get("sync_state", "draft"),
        }

    def _format_anchors_for_canvas(self, anchors):
        formatted = []
        for idx, anchor in enumerate(anchors or []):
            item = self._normalize_anchor_record(anchor, idx)
            formatted.append(
                {
                    "anchor_id": item["anchor_id"],
                    "x": item["x_m"],
                    "y": item["y_m"],
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
                    item.get("zone_id", ""),
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
        annotated = []
        for idx, anchor in enumerate(anchors or []):
            item = self._normalize_anchor_record(anchor, idx)
            rooms = self._rooms_for_anchor(item)
            zone_ids = [room.id for room in rooms]
            zone_names = [room.name for room in rooms]
            item["zone_ids"] = zone_ids
            item["zone_names"] = zone_names
            item["zone_id"] = zone_ids[0] if zone_ids else ""
            item["zone_name"] = zone_names[0] if zone_names else ""
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
        rule_zones = [zone for zone in zones if getattr(zone, "object_type", "zone") == "zone"]
        for zone in zones:
            object_type = getattr(zone, "object_type", "zone")
            if len(getattr(zone, "points", [])) < 3:
                errors.append(f"{zone.name} has fewer than 3 points.")
            if object_type == "wall" and zone.max_z <= zone.min_z:
                warnings.append(f"{zone.name} wall height is not set.")
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
        self._setup_anchor_authoring_controls(editor)
        editor.editor_tabs.currentChanged.connect(self._on_editor_tab_changed)
        editor.btn_mode_room.clicked.connect(lambda: self._set_editor_tool("room", "draw"))
        editor.btn_mode_wall.clicked.connect(lambda: self._set_editor_tool("wall", "draw"))
        editor.btn_mode_anchor.clicked.connect(lambda: self._set_editor_tool("anchor", "draw"))
        editor.btn_mode_edit_map.clicked.connect(lambda: self._set_editor_mode("edit_vertices"))
        editor.btn_mode_draw.clicked.connect(lambda: self._set_editor_tool("zone", "draw"))
        editor.btn_mode_edit.clicked.connect(lambda: self._set_editor_mode("edit_vertices"))
        editor.sb_grid_spacing.valueChanged.connect(self._update_grid_settings)
        editor.sb_grid_subdivisions.valueChanged.connect(self._update_grid_settings)
        editor.cmb_map_type.currentIndexChanged.connect(self._sync_map_height_visibility)
        editor.btn_apply_map_properties.clicked.connect(self._apply_map_properties)
        editor.btn_apply_properties.clicked.connect(self._apply_zone_properties)
        editor.btn_delete_zone.clicked.connect(self._delete_selected_zone)
        editor.btn_save_map.clicked.connect(self._save_map)
        editor.btn_clear_map.clicked.connect(self._clear_map)
        editor.btn_exit_editor.clicked.connect(self._exit_geofence_editor)

        self._canvas.polygon_completed.connect(self._on_canvas_polygon_completed)
        self._canvas.zone_selected.connect(self._on_canvas_zone_selected)
        self._canvas.zone_modified.connect(self._on_canvas_zone_modified)
        self._canvas.zone_properties_updated.connect(self._on_canvas_zone_properties_updated)
        self._canvas.anchor_selected.connect(self._on_canvas_anchor_selected)
        self._canvas.anchor_layout_edited.connect(self._on_canvas_anchor_layout_edited)

        self._update_grid_settings()
        self._sync_map_height_visibility()
        self._set_editor_tool("room", "draw")

    def _setup_anchor_authoring_controls(self, editor):
        if editor.cmb_map_type.findText("Anchor") < 0:
            editor.cmb_map_type.addItem("Anchor")
        if not hasattr(editor, "btn_mode_anchor"):
            editor.btn_mode_anchor = QPushButton("Anchor", editor)
            editor.btn_mode_anchor.setCheckable(True)
            editor.btn_mode_anchor.setStyleSheet(
                "QPushButton:checked { background-color: #0891B2; color: white; border-color: #22D3EE; }"
            )
            editor.map_modes_layout.insertWidget(2, editor.btn_mode_anchor)

        # Inject scanned devices picker
        if not hasattr(editor, "cmb_scanned_anchors"):
            editor.lbl_scanned_device = QLabel("Scanned Link:", editor)
            editor.cmb_scanned_anchors = QComboBox(editor)
            editor.cmb_scanned_anchors.addItem("Manual (No Link)", None)
            editor.map_properties_form_layout.insertRow(3, editor.lbl_scanned_device, editor.cmb_scanned_anchors)
            
            # Auto fill name when combobox changes
            def on_cmb_changed(index):
                data = editor.cmb_scanned_anchors.itemData(index)
                self._apply_anchor_template_from_combo()
                if isinstance(data, dict):
                    editor.txt_map_name.setText(data.get("label", f"A{data.get('anchor_id', 0)}"))
            editor.cmb_scanned_anchors.currentIndexChanged.connect(on_cmb_changed)
            self._on_scan_devices_updated([])

        if not hasattr(editor, "sb_anchor_x"):
            editor.lbl_anchor_x = QLabel("X:", editor)
            editor.sb_anchor_x = QDoubleSpinBox(editor)
            editor.lbl_anchor_y = QLabel("Y:", editor)
            editor.sb_anchor_y = QDoubleSpinBox(editor)
            editor.lbl_anchor_z = QLabel("Z:", editor)
            editor.sb_anchor_z = QDoubleSpinBox(editor)
            for spin in (editor.sb_anchor_x, editor.sb_anchor_y, editor.sb_anchor_z):
                spin.setRange(-1000.0, 1000.0)
                spin.setDecimals(3)
                spin.setSingleStep(0.1)
                spin.setSuffix(" m")
            editor.map_properties_form_layout.insertRow(4, editor.lbl_anchor_x, editor.sb_anchor_x)
            editor.map_properties_form_layout.insertRow(5, editor.lbl_anchor_y, editor.sb_anchor_y)
            editor.map_properties_form_layout.insertRow(6, editor.lbl_anchor_z, editor.sb_anchor_z)

        if not hasattr(editor, "btn_create_default_anchors"):
            editor.btn_create_default_anchors = QPushButton("Create A0..A3", editor)
            editor.btn_add_anchor = QPushButton("Add Anchor", editor)
            editor.btn_assign_anchor = QPushButton("Assign / Focus", editor)
            editor.btn_remove_anchor = QPushButton("Remove Anchor", editor)
            anchor_actions = QGridLayout()
            anchor_actions.setHorizontalSpacing(6)
            anchor_actions.setVerticalSpacing(6)
            anchor_actions.addWidget(editor.btn_create_default_anchors, 0, 0)
            anchor_actions.addWidget(editor.btn_add_anchor, 0, 1)
            anchor_actions.addWidget(editor.btn_assign_anchor, 1, 0)
            anchor_actions.addWidget(editor.btn_remove_anchor, 1, 1)
            for btn in (
                editor.btn_create_default_anchors,
                editor.btn_add_anchor,
                editor.btn_assign_anchor,
                editor.btn_remove_anchor,
            ):
                btn.setMinimumHeight(30)
            editor.map_tab_layout.insertLayout(2, anchor_actions)
            editor.btn_create_default_anchors.clicked.connect(self._create_default_anchors)
            editor.btn_add_anchor.clicked.connect(self._add_anchor)
            editor.btn_assign_anchor.clicked.connect(self._assign_or_focus_selected_anchor)
            editor.btn_remove_anchor.clicked.connect(self._remove_selected_anchor)

        if not hasattr(editor, "lbl_anchor_status"):
            editor.lbl_anchor_status = QLabel("No anchors placed", editor)
            editor.lbl_anchor_status.setWordWrap(True)
            editor.lbl_anchor_status.setStyleSheet("color: #94A3B8; font-weight: bold;")
            editor.map_tab_layout.insertWidget(3, editor.lbl_anchor_status)

        # Inject Device Layout Sync groupbox above btn_save_map
        if not hasattr(editor, "btn_read_layout_dev"):
            sync_parent_layout = getattr(editor, "editor_content_layout", editor.main_layout)
            idx = sync_parent_layout.indexOf(editor.btn_save_map)
            if idx >= 0:
                sync_gb = QGroupBox("Device Layout Sync", editor)
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
                
                sync_parent_layout.insertWidget(idx, sync_gb)
                
                editor.btn_read_layout_dev.clicked.connect(self._read_layout_from_device)
                editor.btn_write_layout_dev.clicked.connect(self._write_layout_to_device)

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

    def _apply_anchor_template_from_combo(self):
        if not hasattr(self.geofence_editor_widget, "cmb_scanned_anchors"):
            return
        data = self.geofence_editor_widget.cmb_scanned_anchors.currentData()
        template = data if isinstance(data, dict) else None
        self._canvas.set_anchor_template(template)

    def _selected_device_target(self):
        if hasattr(self.geofence_editor_widget, "cmb_device_target"):
            data = self.geofence_editor_widget.cmb_device_target.currentData()
            if isinstance(data, dict):
                return data
        return {"dst_addr": 1, "role": "tag"}

    def _create_default_anchors(self):
        if not self._vm:
            return
        zones = self._vm.get_geofence_zones()
        points = [pt for zone in zones for pt in getattr(zone, "points", [])]
        if points:
            min_x = min(p[0] for p in points)
            max_x = max(p[0] for p in points)
            min_y = min(p[1] for p in points)
            max_y = max(p[1] for p in points)
        else:
            min_x, min_y, max_x, max_y = 0.0, 0.0, 9.8, 9.8
        anchors = [
            {"anchor_id": 0, "label": "A0", "x_m": min_x, "y_m": min_y, "z_m": 0.0},
            {"anchor_id": 1, "label": "A1", "x_m": max_x, "y_m": min_y, "z_m": 0.0},
            {"anchor_id": 2, "label": "A2", "x_m": max_x, "y_m": max_y, "z_m": 0.0},
            {"anchor_id": 3, "label": "A3", "x_m": min_x, "y_m": max_y, "z_m": 0.0},
        ]
        self._canvas.set_anchors(self._format_anchors_for_canvas(anchors))
        self._draft_anchor_layout = self._annotate_anchor_membership(anchors)
        self._canvas.set_anchors(self._format_anchors_for_canvas(self._draft_anchor_layout))
        self._anchor_layout_commit_pending = True
        self._vm.geofence_repo.set_anchors(self._draft_anchor_layout)
        self._refresh_anchor_status_label()

    def _add_anchor(self):
        if not self._vm:
            return
        used_ids = {self._coerce_int_id(anchor.get("anchor_id"), idx) for idx, anchor in enumerate(self._canvas.anchors)}
        anchor_id = 0
        while anchor_id in used_ids:
            anchor_id += 1
        world_x = getattr(self._canvas, "_view_cx", 0.0)
        world_y = getattr(self._canvas, "_view_cy", 0.0)
        self._canvas.set_anchor_template(
            {
                "anchor_id": anchor_id,
                "label": f"A{anchor_id}",
                "role": "anchor",
                "device_type": "uwb_anchor",
                "device_id": anchor_id,
                "is_scanned": False,
            }
        )
        self._canvas.selected_anchor_idx = None
        self._canvas.add_or_move_anchor_at(world_x, world_y)
        self._anchor_layout_commit_pending = True
        self._vm.geofence_repo.set_anchors(self._annotate_anchor_membership(self._canvas.anchor_layout_for_device()))
        self._refresh_anchor_status_label()

    def _assign_or_focus_selected_anchor(self):
        self._apply_anchor_template_from_combo()
        data = self.geofence_editor_widget.cmb_scanned_anchors.currentData() if hasattr(self.geofence_editor_widget, "cmb_scanned_anchors") else None
        if not isinstance(data, dict):
            self._set_editor_tool("anchor", "draw")
            return
        anchor_id = self._coerce_int_id(data.get("anchor_id"), 0)
        for idx, anchor in enumerate(self._canvas.anchors):
            if self._coerce_int_id(anchor.get("anchor_id"), -1) == anchor_id:
                self._canvas.set_selected_anchor(idx)
                self._set_editor_tool("anchor", "draw")
                return
        world_x = getattr(self._canvas, "_view_cx", 0.0)
        world_y = getattr(self._canvas, "_view_cy", 0.0)
        self._canvas.add_or_move_anchor_at(world_x, world_y)
        self._set_editor_tool("anchor", "draw")

    def _remove_selected_anchor(self):
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
        
        # Keep track of currently selected anchor_id
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
            
            # 1. Add anchors from the scan list
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
            
            # 2. Add fallback/default items (1 to 8) if not present
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
            
            # Restore selection
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
        self._canvas.set_edit_mode("navigate")
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
        if object_type in {"room", "wall", "anchor"}:
            idx = self.geofence_editor_widget.cmb_map_type.findText(object_type.title())
            if idx >= 0 and self.geofence_editor_widget.cmb_map_type.currentIndex() != idx:
                self.geofence_editor_widget.cmb_map_type.blockSignals(True)
                self.geofence_editor_widget.cmb_map_type.setCurrentIndex(idx)
                self.geofence_editor_widget.cmb_map_type.blockSignals(False)
            self._sync_map_height_visibility()
        if object_type == "anchor":
            self._apply_anchor_template_from_combo()
        self._set_editor_mode(mode)

    def _set_editor_mode(self, mode):
        self._canvas.set_edit_mode(mode)
        draw_type = self._canvas.draw_object_type
        is_draw = mode == "draw"
        is_edit = mode == "edit_vertices"
        is_map_tab = self.geofence_editor_widget.editor_tabs.currentIndex() == 0
        self.geofence_editor_widget.btn_mode_room.setChecked(is_draw and draw_type == "room")
        self.geofence_editor_widget.btn_mode_wall.setChecked(is_draw and draw_type == "wall")
        self.geofence_editor_widget.btn_mode_anchor.setChecked(is_draw and draw_type == "anchor")
        self.geofence_editor_widget.btn_mode_draw.setChecked(is_draw and draw_type == "zone")
        self.geofence_editor_widget.btn_mode_edit_map.setChecked(is_edit and is_map_tab)
        self.geofence_editor_widget.btn_mode_edit.setChecked(is_edit and not is_map_tab)

    def _sync_map_height_visibility(self, *_args):
        object_type = self.geofence_editor_widget.cmb_map_type.currentText().strip().lower()
        is_wall = object_type == "wall"
        is_anchor = object_type == "anchor"
        self.geofence_editor_widget.lbl_map_height.setText("Height:")
        self.geofence_editor_widget.sb_map_height.setMinimum(0.1)
        self.geofence_editor_widget.lbl_map_height.setVisible(is_wall)
        self.geofence_editor_widget.sb_map_height.setVisible(is_wall)
        for name in (
            "lbl_scanned_device",
            "cmb_scanned_anchors",
            "lbl_anchor_x",
            "sb_anchor_x",
            "lbl_anchor_y",
            "sb_anchor_y",
            "lbl_anchor_z",
            "sb_anchor_z",
            "btn_create_default_anchors",
            "btn_add_anchor",
            "btn_assign_anchor",
            "btn_remove_anchor",
            "lbl_anchor_status",
        ):
            widget = getattr(self.geofence_editor_widget, name, None)
            if widget is not None:
                widget.setVisible(is_anchor)

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
            height = self.geofence_editor_widget.sb_map_height.value() if object_type == "wall" else 0.0
            new_zone = GeofenceZone(
                id=zone_id,
                name=f"{object_type.title()} {number}",
                zone_type=object_type,
                points=points,
                min_z=0.0,
                max_z=height,
                speed_limit=0.0,
                color="#F8FAFC" if object_type == "room" else "#0F172A",
                object_type=object_type,
            )

        self._vm.add_geofence_zone(new_zone)
        self._canvas.set_geofences(self._vm.get_geofence_zones())
        self._canvas.set_selected_zone(zone_id)
        self._load_zone_properties_to_ui(new_zone)
        if object_type == "room":
            self._refresh_anchor_membership_from_canvas()

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
            if object_type == "wall":
                self.geofence_editor_widget.sb_map_height.setValue(max(0.1, zone.max_z - zone.min_z))
            self._canvas.set_draw_object_type(object_type)
            self._sync_map_height_visibility()
        self._set_editor_mode("edit_vertices")

    def _apply_map_properties(self):
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
                        QMessageBox.warning(self, "Duplicate Anchor", f"A{anchor_id} is already placed on the map.")
                        return

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
            return

        selected_id = self._canvas.selected_zone_id
        if not selected_id or not self._vm:
            QMessageBox.warning(self, "No Selection", "Select a room or wall on the map first.")
            return

        objects = self._vm.get_geofence_zones()
        zone = next((z for z in objects if z.id == selected_id), None)
        if not zone or getattr(zone, "object_type", "zone") == "zone":
            QMessageBox.warning(self, "Wrong Object Type", "The selected object is a rule zone, not a map object.")
            return

        selected_type = self.geofence_editor_widget.cmb_map_type.currentText().strip().lower()
        if selected_type == "anchor":
            QMessageBox.warning(self, "Wrong Object Type", "Select an anchor on the map first.")
            return
        object_type = "wall" if selected_type == "wall" else "room"
        zone.object_type = object_type
        zone.zone_type = object_type
        zone.name = self.geofence_editor_widget.txt_map_name.text().strip() or object_type.title()
        zone.min_z = 0.0
        zone.max_z = self.geofence_editor_widget.sb_map_height.value() if object_type == "wall" else 0.0
        zone.speed_limit = 0.0
        zone.color = "#F8FAFC" if object_type == "room" else "#0F172A"
        self._vm.geofence_layout_updated.emit(self._vm.get_geofence_zones())
        self._canvas.update()
        if object_type == "room":
            self._refresh_anchor_membership_from_canvas()

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
        if self._canvas.delete_selected_anchor():
            self.geofence_editor_widget.txt_map_name.clear()
            return

        selected_id = self._canvas.selected_zone_id
        if not selected_id or not self._vm:
            QMessageBox.warning(self, "No Selection", "Select an object on the map first.")
            return
        zones = self._vm.get_geofence_zones()
        deleted_zone = next((z for z in zones if z.id == selected_id), None)
        self._vm.remove_geofence_zone(selected_id)
        self._canvas.set_geofences(self._vm.get_geofence_zones())
        self._canvas.set_selected_zone(None)
        self.geofence_editor_widget.txt_zone_name.clear()
        self.geofence_editor_widget.txt_map_name.clear()
        if deleted_zone and getattr(deleted_zone, "object_type", "zone") == "room":
            self._refresh_anchor_membership_from_canvas()

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

    def _on_canvas_anchor_selected(self, anchor_idx):
        if anchor_idx is None or anchor_idx < 0 or anchor_idx >= len(self._canvas.anchors):
            return
        anchor = self._canvas.anchors[anchor_idx]
        self.geofence_editor_widget.editor_tabs.setCurrentIndex(0)
        self.geofence_editor_widget.txt_map_name.setText(anchor.get("label", f"A{anchor.get('anchor_id', anchor_idx)}"))
        anchor_type_idx = self.geofence_editor_widget.cmb_map_type.findText("Anchor")
        if anchor_type_idx >= 0:
            self.geofence_editor_widget.cmb_map_type.setCurrentIndex(anchor_type_idx)
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

        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        default_dir = os.path.join(base_dir, "data", "runtime")
        os.makedirs(default_dir, exist_ok=True)

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Geofencing Map",
            default_dir,
            "Geofencing Map JSON (*.json)"
        )
        if not file_path:
            return

        if not self._vm.load_geofences(file_path):
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
        errors, warnings = self._validate_geofence_map()
        if errors:
            QMessageBox.warning(self, "Invalid Map", "\n".join(errors))
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
            "Delete all rooms, walls, and rule zones?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
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
        # Keep the editor canvas strictly 2D; 2.5D is shown only in the preview dialog.
        self._canvas.set_25d_preview(False)
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
        self._canvas.set_25d_preview(False)
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

    def _open_preview_dialog(self):
        source = self._vm if self._vm else self._canvas
        if self._preview_dialog is None:
            self._preview_dialog = GeofencePreviewDialog(source, parent=self)
        self._preview_dialog.preview_pane.update()
        self._preview_dialog.show()
        self._preview_dialog.raise_()
        self._preview_dialog.activateWindow()

    def update_preview_pane(self):
        if self._preview_dialog and self._preview_dialog.isVisible():
            self._preview_dialog.preview_pane.update()
