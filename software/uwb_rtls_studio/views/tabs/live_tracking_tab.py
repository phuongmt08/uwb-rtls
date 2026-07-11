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
from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QRect, QTimer, Qt, QPointF, QEvent
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
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QHeaderView,
    QAbstractItemView,
    QColorDialog,
    QMenu,
    QApplication,
)
from PyQt6.QtGui import QPainter, QPen, QColor, QBrush, QFont, QPolygonF, QShortcut, QKeySequence, QAction

from common.transport import VvAddress
from views.components.position_canvas import PositionCanvas
from views.components.distance_graph import DistanceGraph
from views.components.geofence_3d_widget import Geofence3DWidget, OPENGL_AVAILABLE
from models.geofence_model import GeofenceZone
from views.components.geofence_editor import GeofenceEditorWidget
from views.components.zone_property_panel import ZonePropertyPanel
from utils.config_dim import GRID_SPACING_M
from utils.app_state import shared_app_state

DEFAULT_ANCHOR_LAYOUT = [
    {"anchor_id": 1, "x_m": 0.0, "y_m": 0.0, "z_m": 0.0, "label": "A1"},
    {"anchor_id": 2, "x_m": 10.76, "y_m": 0.0, "z_m": 0.0, "label": "A2"},
    {"anchor_id": 3, "x_m": 0.0, "y_m": 13.2, "z_m": 0.0, "label": "A3"},
    {"anchor_id": 4, "x_m": 10.76, "y_m": 13.2, "z_m": 0.0, "label": "A4"},
]


class _PreviewPane(QWidget):
    def __init__(self, source, mode="top", parent=None):
        super().__init__(parent)
        self._source = source
        self._mode = mode
        self._zoom = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._dragging = False
        self._drag_start = None
        self._drag_pan_x = 0.0
        self._drag_pan_y = 0.0
        self.setMinimumSize(760, 420)
        self.setStyleSheet("background: #DDE3EA; border: 1px solid #CBD5E1; border-radius: 8px;")
        self.setMouseTracking(True)

    def set_mode(self, mode: str):
        self._mode = "angled" if mode == "angled" else "top"
        self.update()

    def reset_view(self):
        self._zoom = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self.update()

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else (1.0 / 1.15)
        self._zoom = max(0.35, min(self._zoom * factor, 8.0))
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_start = event.position()
            self._drag_pan_x = self._pan_x
            self._drag_pan_y = self._pan_y
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and self._drag_start:
            dx = event.position().x() - self._drag_start.x()
            dy = event.position().y() - self._drag_start.y()
            self._pan_x = self._drag_pan_x + dx
            self._pan_y = self._drag_pan_y + dy
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self._drag_start = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.reset_view()
        event.accept()

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
        inset = 18
        base_w = rect.width() - 2 * inset
        base_h = rect.height() - 2 * inset
        cx = rect.center().x() + self._pan_x
        cy = rect.center().y() + self._pan_y
        sx = cx + (((x - min_x) / w) - 0.5) * base_w * self._zoom
        sy = cy - (((y - min_y) / h) - 0.5) * base_h * self._zoom
        return QPointF(sx, sy)

    def _draw_top_view(self, painter, zones):
        rect = self.rect().adjusted(12, 12, -12, -12)
        painter.setPen(QPen(QColor(14, 116, 144), 1))
        painter.setBrush(QColor(229, 231, 235))
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
        # Keep the angled view mostly front-facing: less horizontal skew, more top-down lift.
        return nx * 1.75 + ny * 0.06, -(ny * 1.35) - z * 0.24

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
        scale = min((rect.width() - 72) / w, (rect.height() - 76) / h) * self._zoom
        cx = rect.center().x() + self._pan_x
        cy = rect.center().y() + 18 + self._pan_y
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
        painter.setBrush(QColor(229, 231, 235))
        painter.drawRoundedRect(rect, 8, 8)
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        painter.setPen(QColor(30, 41, 59))
        painter.drawText(16, 22, "3D View")

        bounds = self._bounds(zones)
        angle_bounds = self._angle_bounds(zones, bounds)
        room_zones = [zone for zone in zones if getattr(zone, "object_type", "zone") == "room" and len(zone.points) >= 3]
        floor = QPolygonF()
        if room_zones:
            primary_room = max(room_zones, key=lambda zone: abs(self._polygon_area(zone.points)))
            for x, y in primary_room.points:
                floor.append(self._angle_point(x, y, 0.0, bounds, angle_bounds, rect))
        else:
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

        # XYZ axes at the world origin.
        axis_length = max(min(bounds[2] - bounds[0], bounds[3] - bounds[1]) * 0.18, 1.0)
        axis_height = max((max((self._zone_height(zone) for zone in zones), default=1.0)) * 0.7, 1.0)
        origin = self._angle_point(0.0, 0.0, 0.0, bounds, angle_bounds, rect)
        axis_points = (
            ("X", self._angle_point(axis_length, 0.0, 0.0, bounds, angle_bounds, rect), QColor("#EF4444")),
            ("Y", self._angle_point(0.0, axis_length, 0.0, bounds, angle_bounds, rect), QColor("#22C55E")),
            ("Z", self._angle_point(0.0, 0.0, axis_height, bounds, angle_bounds, rect), QColor("#3B82F6")),
        )
        painter.save()
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        for label, endpoint, color in axis_points:
            painter.setPen(QPen(color, 3))
            painter.drawLine(origin, endpoint)
            painter.setPen(color)
            painter.drawText(int(endpoint.x() + 5), int(endpoint.y() - 4), label)
        painter.setBrush(QColor("#0F172A"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(int(origin.x() - 3), int(origin.y() - 3), 6, 6)
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

    def _polygon_area(self, points):
        if len(points) < 3:
            return 0.0
        area = 0.0
        for idx, (x1, y1) in enumerate(points):
            x2, y2 = points[(idx + 1) % len(points)]
            area += (x1 * y2) - (x2 * y1)
        return area / 2.0

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
        self.setWindowTitle("Geofence 3D View")
        self.resize(920, 620)
        self.setStyleSheet("background: #F8FAFC; color: #0F172A;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("Geofence 3D View")
        title.setStyleSheet("color: #0E7490; font-size: 15px; font-weight: bold;")
        header.addWidget(title)
        header.addStretch()
        layout.addLayout(header)

        self.preview_pane = _PreviewPane(source, "angled", self)
        layout.addWidget(self.preview_pane)


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
        self._yaw_offset_deg = 0.0
        self.sidebar_expanded = True
        self._is_developer_mode = False
        self._anchor_layout_commit_pending = False
        self._draft_anchor_layout = []
        self._anchor_table_room_id = ""
        self._anchor_layout_row_map = []
        self._geofence_anchor_baseline = []
        self._pending_layout_read_for_editor = False
        self._pending_layout_read_room_id = ""
        self._clipboard = None
        self._anchor_telemetry_cache = {}

        uic.loadUi(UI_FILE, self)
        self._setup_dynamic_metrics()
        self._clear_live_metrics()
        self.uptime_label.setText("-")
        self._render_stats()

        self._canvas = self.position_canvas
        self._canvas.parent_tab = self
        if hasattr(self._canvas, "set_render_fps"):
            self._canvas.set_render_fps(60)
        self._preview_sync_dirty = False
        self._preview_sync_timer = QTimer(self)
        self._preview_sync_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._preview_sync_timer.setInterval(33)
        self._preview_sync_timer.timeout.connect(self._flush_preview_pane_update)
        self._layout_emit_dirty = False
        self._layout_emit_timer = QTimer(self)
        self._layout_emit_timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._layout_emit_timer.setInterval(50)
        self._layout_emit_timer.timeout.connect(self._flush_geofence_layout_emit)
        self._pending_probe_shortcut = False
        self._pending_probe_shortcut_timer = QTimer(self)
        self._pending_probe_shortcut_timer.setSingleShot(True)
        self._pending_probe_shortcut_timer.setInterval(900)
        self._pending_probe_shortcut_timer.timeout.connect(self._reset_probe_shortcut_state)
        QApplication.instance().installEventFilter(self)
        self._preview_overlay_btn = self.btn_preview_overlay
        self._detail_overlay_btn = QPushButton("Detail", self)
        self._helpers_overlay_btn = QPushButton("Helpers", self)
        self._setup_map_views()
        self._setup_live_subtabs()

        self._setup_geofencing_ui()

        self.header_widget.hide()
        self.warning_label.setVisible(False)
        self.btn_toggle_sidebar.clicked.connect(self.toggle_sidebar)
        self.btn_stop.hide()
        self.btn_start.clicked.connect(self._toggle_ranging)
        self.btn_clear.clicked.connect(self._clear_tracking_trails)
        self._sync_ranging_button()
        self._setup_yaw_offset_control()

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

    def _setup_yaw_offset_control(self):
        self.yaw_offset_group = QGroupBox("Yaw Offset", self.scroll_content)
        self.yaw_offset_group.setStyleSheet(
            "QGroupBox { background-color: rgba(15, 23, 42, 0.72); color: #38BDF8; "
            "font-weight: bold; font-family: 'Segoe UI'; font-size: 13px; "
            "border: 1px solid rgba(56, 189, 248, 0.35); border-radius: 8px; padding-top: 14px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; background: transparent; }"
            "QDoubleSpinBox { background: #1E293B; color: #F8FAFC; border: 1px solid #475569; "
            "border-radius: 6px; padding: 4px 6px; font-family: 'Consolas'; font-size: 13px; }"
            "QCheckBox { color: #E2E8F0; font-weight: normal; spacing: 8px; }"
            "QCheckBox::indicator { width: 15px; height: 15px; }"
        )
        layout = QFormLayout(self.yaw_offset_group)
        layout.setContentsMargins(10, 16, 10, 10)
        layout.setSpacing(8)

        self.yaw_offset_spin = QDoubleSpinBox(self.yaw_offset_group)
        self.yaw_offset_spin.setRange(-360.0, 360.0)
        self.yaw_offset_spin.setDecimals(1)
        self.yaw_offset_spin.setSingleStep(1.0)
        self.yaw_offset_spin.setSuffix(" deg")
        self.yaw_offset_spin.setToolTip("Yaw offset used locally and sent as ranging_start.yaw_deg when starting.")
        self.yaw_offset_spin.valueChanged.connect(self._on_yaw_offset_changed)
        layout.addRow("Yaw offset:", self.yaw_offset_spin)

        self.reinit_ukf_check = QCheckBox("Reinit UKF", self.yaw_offset_group)
        self.reinit_ukf_check.setToolTip("Send ranging_start.is_ukf_reinit=true when starting.")
        layout.addRow("", self.reinit_ukf_check)

        insert_at = self.right_panel.indexOf(self.separator_1) if hasattr(self, "separator_1") else -1
        if insert_at >= 0:
            self.right_panel.insertWidget(insert_at, self.yaw_offset_group)
        else:
            self.right_panel.addWidget(self.yaw_offset_group)

    def _on_yaw_offset_changed(self, value):
        self._yaw_offset_deg = float(value)
        active_position = self._canvas.fusion_position if self._canvas.fusion_position is not None else self._canvas.position
        if active_position:
            updated = active_position.copy()
            raw_yaw = float(updated.get("raw_yaw", updated.get("yaw", 0.0)))
            updated["raw_yaw"] = raw_yaw
            updated["yaw"] = self._apply_yaw_offset(raw_yaw)
            if updated.get("source") == "sensor_fusion":
                self._canvas.fusion_position = updated
            else:
                self._canvas.position = updated
            self._canvas.update()
            if self._map_view_stack.currentWidget() is self._map_3d:
                self._map_3d._tag_position = [
                    float(updated.get("x", 0.0)),
                    float(updated.get("y", 0.0)),
                    float(updated.get("z", 0.0)),
                ]
                self._map_3d._tag_yaw = float(updated.get("yaw", 0.0))
                if getattr(self._map_3d, "gl_widget", None):
                    self._map_3d._update_tag_arrow()
                    self._map_3d.gl_widget.update()

    def _apply_yaw_offset(self, yaw_deg: float) -> float:
        value = float(yaw_deg) + float(self._yaw_offset_deg)
        while value > 180.0:
            value -= 360.0
        while value <= -180.0:
            value += 360.0
        return value

    def _ensure_stream_active(self):
        if self._is_ranging:
            return
        self._is_ranging = True
        self._start_time = time.time()
        self._sync_ranging_button()

    @staticmethod
    def _format_anchor_mask(mask, valid=True):
        if not valid or mask is None or mask == "":
            return "-"
        try:
            value = int(mask)
        except (TypeError, ValueError):
            return "-"
        return f"0x{value:02X} ({value:08b})"

    def _setup_canvas_preview_button(self):
        self._preview_overlay_btn.setCheckable(True)
        self._preview_overlay_btn.setChecked(False)
        self._preview_overlay_btn.setText("2D")
        self._preview_overlay_btn.setToolTip("Switch between 2D top view and 3D view")
        self._preview_overlay_btn.toggled.connect(self._toggle_map_view)

        self._detail_overlay_btn.setCheckable(True)
        self._detail_overlay_btn.setChecked(True)
        self._detail_overlay_btn.setText("Detail")
        self._detail_overlay_btn.setToolTip("Toggle detailed labels and dimensions")
        self._detail_overlay_btn.setFixedHeight(self._preview_overlay_btn.height())
        self._detail_overlay_btn.setMinimumWidth(78)
        self._detail_overlay_btn.setStyleSheet(self._preview_overlay_btn.styleSheet())
        self._detail_overlay_btn.toggled.connect(self._toggle_overlay_detail)

        self._helpers_overlay_btn.setText("Helpers")
        self._helpers_overlay_btn.setToolTip("Show mouse helpers and keyboard shortcuts (F1)")
        self._helpers_overlay_btn.setFixedHeight(self._preview_overlay_btn.height())
        self._helpers_overlay_btn.setMinimumWidth(88)
        self._helpers_overlay_btn.setStyleSheet(self._preview_overlay_btn.styleSheet())
        self._helpers_overlay_btn.setShortcut(QKeySequence("F1"))
        self._helpers_overlay_btn.clicked.connect(self._show_canvas_helpers)
        if hasattr(self._canvas, "set_overlay_detail_mode"):
            self._canvas.set_overlay_detail_mode(True)

        self._preview_overlay_btn.raise_()
        self._detail_overlay_btn.raise_()
        self._helpers_overlay_btn.raise_()
        self._position_canvas_preview_button()

    def _toggle_overlay_detail(self, detailed):
        self._detail_overlay_btn.setText("Detail" if detailed else "Clean")
        if hasattr(self._canvas, "set_overlay_detail_mode"):
            self._canvas.set_overlay_detail_mode(detailed)

    def _reset_probe_shortcut_state(self):
        self._pending_probe_shortcut = False
        if hasattr(self, "_pending_probe_shortcut_timer"):
            self._pending_probe_shortcut_timer.stop()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            modifiers = event.modifiers()
            focus = QApplication.focusWidget()
            typing_focus = isinstance(focus, (QLineEdit, QDoubleSpinBox, QComboBox))
            if self._pending_probe_shortcut:
                if key == Qt.Key.Key_D and modifiers == Qt.KeyboardModifier.NoModifier and not typing_focus:
                    self._reset_probe_shortcut_state()
                    self._toggle_probe_dimension_mode()
                    return True
                if key not in (Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Meta):
                    self._reset_probe_shortcut_state()
            if key == Qt.Key.Key_P and modifiers == Qt.KeyboardModifier.NoModifier and not typing_focus:
                self._pending_probe_shortcut = True
                self._pending_probe_shortcut_timer.start()
                return True
        return super().eventFilter(watched, event)

    def _toggle_probe_dimension_mode(self):
        enabled = self._canvas.toggle_probe_dimension_mode()
        if enabled:
            self._set_canvas_tool_status("Dimension probe / Click 2 points")
        else:
            self._set_canvas_tool_status("Dimension probe cleared")
    def _show_canvas_helpers(self):
        helper_text = (
            "Mouse helpers\n"
            "- Left drag on empty area: box select multiple objects\n"
            "- Left drag on object body: move object\n"
            "- Left drag on object name: move label only\n"
            "- Middle drag: pan map\n"
            "- Mouse wheel: zoom in/out\n"
            "- Right drag: zoom to rectangle\n\n"
            "Keyboard shortcuts\n"
            "- Ctrl+Z: undo\n"
            "- Delete: delete selected object(s)\n"
            "- Ctrl+X: cut selected object(s)\n"
            "- Ctrl+C: copy selected object(s)\n"
            "- Ctrl+V: paste object(s)\n"
            "- Alt+A: add anchor\n"
            "- Alt+O: set local origin\n"
            "- Alt+Delete: remove anchor\n"
            "- Alt+L: open Anchor Layout menu\n"
            "- Alt+R: read anchor layout\n"
            "- Alt+W: write anchor layout\n"
            "- P, D: measure between 2 snapped points (horizontal/vertical)\n"
            "- Right click while measuring: cancel probe"
        )
        QMessageBox.information(self, "Canvas Helpers & Shortcuts", helper_text)

    def _setup_map_views(self):
        self._map_view_stack = QStackedWidget(self)
        self.main_layout.removeWidget(self._canvas)
        self._map_view_stack.addWidget(self._canvas)
        self._map_3d = Geofence3DWidget(self._map_view_stack)
        self._map_view_stack.addWidget(self._map_3d)
        self.main_layout.addWidget(self._map_view_stack, 0, 0, 2, 2)
        self._map_view_stack.setCurrentWidget(self._canvas)

    def _setup_live_subtabs(self):
        self._live_sub_tabs = QTabWidget(self)
        self._live_sub_tabs.setDocumentMode(True)
        self._live_sub_tabs.setStyleSheet(
            "QTabWidget::pane { border: 0; background: #0F172A; }"
            "QTabBar::tab { background: #1E293B; color: #94A3B8; padding: 8px 18px; "
            "border: 1px solid #334155; border-bottom: 0; }"
            "QTabBar::tab:selected { color: #22D3EE; background: #0F172A; }"
        )
        self.main_layout.removeWidget(self._map_view_stack)
        self._live_sub_tabs.addTab(self._map_view_stack, "2D Tracking")
        self._distance_graph = DistanceGraph(self._live_sub_tabs)
        self._live_sub_tabs.addTab(self._distance_graph, "Distance Log")
        self._live_sub_tabs.currentChanged.connect(self._on_live_subtab_changed)
        self.main_layout.addWidget(self._live_sub_tabs, 0, 0, 2, 2)
        self._on_live_subtab_changed(0)

    def _on_live_subtab_changed(self, index):
        map_visible = index == 0
        self._preview_overlay_btn.setVisible(map_visible)
        self._detail_overlay_btn.setVisible(map_visible)
        self._helpers_overlay_btn.setVisible(map_visible)

    def _toggle_map_view(self, show_3d):
        if show_3d and not OPENGL_AVAILABLE:
            self._preview_overlay_btn.blockSignals(True)
            self._preview_overlay_btn.setChecked(False)
            self._preview_overlay_btn.blockSignals(False)
            self._preview_overlay_btn.setText("2D")
            self._map_view_stack.setCurrentWidget(self._canvas)
            return
        if show_3d:
            self._map_3d.set_camera_from_2d(
                self._canvas._view_cx,
                self._canvas._view_cy,
                self._canvas._view_range,
            )
            self._map_3d.set_geofences(self._canvas.geofence_zones)
            self._map_3d.set_anchors(self._canvas.anchors)
            active_position = self._canvas.fusion_position if self._canvas.fusion_position is not None else self._canvas.position
            self._map_3d.update_position(active_position)
            self._map_view_stack.setCurrentWidget(self._map_3d)
            self._preview_overlay_btn.setText("3D")
        else:
            camera = self._map_3d.camera_for_2d()
            if camera:
                self._canvas._view_cx, self._canvas._view_cy, self._canvas._view_range = camera
                self._canvas.update()
            self._map_view_stack.setCurrentWidget(self._canvas)
            self._preview_overlay_btn.setText("2D")
        self.header_widget.raise_()
        self.right_widget.raise_()
        self.btn_toggle_sidebar.raise_()
        self._preview_overlay_btn.raise_()
        if hasattr(self, "_detail_overlay_btn"):
            self._detail_overlay_btn.raise_()
        if hasattr(self, "_helpers_overlay_btn"):
            self._helpers_overlay_btn.raise_()

    def _clear_tracking_trails(self):
        self._canvas.clear_trail()
        self._map_3d.clear_trail()
        self._distance_graph.clear()

    def _position_canvas_preview_button(self):
        if not hasattr(self, "_preview_overlay_btn"):
            return
        canvas = self._map_view_stack
        canvas_origin = canvas.mapTo(self, QPointF(0, 0).toPoint())
        preview_x = max(self.right_widget.x() - self._preview_overlay_btn.width() - 12, canvas_origin.x() + 12)
        y = canvas_origin.y() + 10
        self._preview_overlay_btn.move(preview_x, y)
        self._preview_overlay_btn.raise_()
        detail_x = preview_x
        if hasattr(self, "_detail_overlay_btn"):
            detail_x = max(preview_x - self._detail_overlay_btn.width() - 8, canvas_origin.x() + 12)
            self._detail_overlay_btn.move(detail_x, y)
            self._detail_overlay_btn.raise_()
        if hasattr(self, "_helpers_overlay_btn"):
            helper_x = max(detail_x - self._helpers_overlay_btn.width() - 8, canvas_origin.x() + 12)
            self._helpers_overlay_btn.move(helper_x, y)
            self._helpers_overlay_btn.raise_()

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
            if value is None or value in ("--", "-"):
                label_widget.setText("-")
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
                label_widget.setText("-")
        self._anchor_telemetry_cache.clear()
        self._last_anchor_mask = 0
        self._last_anchor_mask_valid = False
        if hasattr(self, "_canvas") and hasattr(self._canvas, "clear_anchor_telemetry"):
            self._canvas.clear_anchor_telemetry()

    @staticmethod
    def _anchor_telemetry_text(anchor):
        distance_m = None
        distance_mm = anchor.get("distance_mm")
        if distance_mm is not None:
            distance_m = float(distance_mm) / 1000.0
        elif anchor.get("distance_cm") is not None:
            distance_m = float(anchor["distance_cm"]) / 100.0

        if distance_m is None:
            return "-"

        text = f"{distance_m:.3f} m"
        if anchor.get("weight") is not None:
            text += f"  |  W: {float(anchor['weight']) / 100.0:.2f}"
        return text

    def _show_anchor_telemetry(self, anchors):
        """Render cached distance and weight values in the four live rows."""
        for anchor in anchors or []:
            anchor_id = int(anchor.get("anchor_id", 0) or 0)
            if anchor_id <= 0:
                text_id = str(anchor.get("id", "")).replace("A", "")
                anchor_id = int(text_id) if text_id.isdigit() else 0
            if anchor_id <= 0:
                continue
            cached = self._anchor_telemetry_cache.get(anchor_id, {}).copy()
            cached.update(anchor)
            cached["anchor_id"] = anchor_id
            self._anchor_telemetry_cache[anchor_id] = cached

        for anchor_idx in range(1, 5):
            label_widget = getattr(self, f"d{anchor_idx}_label", None)
            if label_widget is not None:
                label_widget.setText(self._anchor_telemetry_text(self._anchor_telemetry_cache[anchor_idx])
                                     if anchor_idx in self._anchor_telemetry_cache else "-")

    def _setup_dynamic_metrics(self):
        if hasattr(self, "lbl_fps"):
            self.lbl_fps.setText("Rate:")

        # Configure units for the statically loaded metric labels
        self.length_label.unit = "bytes"
        self.fusion_ts_label.unit = "ms"
        
        self.ukf_x_label.unit = "m"
        self.ukf_y_label.unit = "m"
        self.ukf_yaw_label.unit = "deg"
        
        self.tril_x_label.unit = "m"
        self.tril_y_label.unit = "m"
        self.yaw_label.unit = "deg"
        
        self.vx_label.unit = "m/s"
        self.vy_label.unit = "m/s"
        
        self.d1_label.unit = "m"
        self.d2_label.unit = "m"
        self.d3_label.unit = "m"
        self.d4_label.unit = "m"
        
        self.z_label.unit = "m"
        self.error_label.unit = "m"

        # Backward compatibility aliases
        self.x_label = self.ukf_x_label
        self.y_label = self.ukf_y_label
        self.err_cnt_label = self.error_frame_cnt_label
        self.tril_xy_label = self.tril_x_label
        self.raw_yaw_label = self.yaw_label

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
        self._vm.calib_data_updated.connect(self._on_calib_data_updated)
        self._vm.anchor_distances_updated.connect(self._on_anchor_distances)
        self._vm.anchor_layout_updated.connect(self._on_anchor_layout_updated)
        self._vm.stats_updated.connect(self._on_stats_updated)
        
        # Connect Geofencing signals
        self._vm.geofence_status_updated.connect(self._on_geofence_status_updated)
        self._vm.geofence_layout_updated.connect(self._canvas.set_geofences)
        self._vm.geofence_layout_updated.connect(self._map_3d.set_geofences)
        self._vm.geofence_layout_updated.connect(self._sync_room_origins)
        
        if hasattr(self._vm, "scan_devices_updated"):
            self._vm.scan_devices_updated.connect(self._update_device_targets)
            self._update_device_targets(self._vm.get_scan_devices())
        
        # Load any existing geofence maps on startup
        self._vm.load_geofences()
        self._canvas.set_geofences(self._vm.get_geofence_zones())
        self._map_3d.set_geofences(self._vm.get_geofence_zones())
        self._sync_room_origins(self._vm.get_geofence_zones())
        
        current_layout = getattr(self._vm, "current_anchor_layout", [])
        if current_layout:
            self._on_anchor_layout_updated(current_layout)

    def _on_anchor_layout_updated(self, anchors_list):
        if getattr(self._canvas, "dim_tracking_view", False):
            normalized = [self._normalize_anchor_record(anchor, idx) for idx, anchor in enumerate(anchors_list or [])]
            if self._pending_layout_read_for_editor:
                room_id = self._pending_layout_read_room_id or self._layout_scope_room_id(require=False)
                if room_id:
                    self._merge_anchor_layout_into_room(room_id, normalized)
                else:
                    self._draft_anchor_layout = normalized
                    self._canvas.set_anchors(self._format_anchors_for_canvas(normalized))
                    if self._vm:
                        self._vm.geofence_repo.set_anchors(normalized)
                    self._anchor_layout_commit_pending = True
                    self._refresh_anchor_status_label()
                self._pending_layout_read_for_editor = False
                self._pending_layout_read_room_id = ""
                return
            self._draft_anchor_layout = normalized
            return
        self.set_anchors(self._format_anchors_for_canvas(anchors_list or []))

    def _start_ranging(self):
        if self._vm:
            self._distance_graph.start_session()
            yaw_deg = int(round(float(self.yaw_offset_spin.value()))) % 360
            is_ukf_reinit = bool(self.reinit_ukf_check.isChecked())
            self._vm.start_ranging(yaw_deg=yaw_deg, is_ukf_reinit=is_ukf_reinit)

    def _stop_ranging(self):
        if self._vm:
            self._vm.stop_ranging()
            self._distance_graph.stop_session()

    def _toggle_ranging(self):
        if self._is_ranging:
            self._stop_ranging()
        else:
            self._start_ranging()

    def _sync_ranging_button(self):
        if self._is_ranging:
            self.btn_start.setText("Stop Ranging")
            self.btn_start.setStyleSheet(
                "QPushButton { background: rgba(239,68,68,0.15); color: #EF4444; "
                "border: 1px solid #EF4444; border-radius: 8px; font-weight: bold; font-size: 14px; }"
                "QPushButton:hover { background: #EF4444; color: #F8FAFC; }"
            )
        else:
            self.btn_start.setText("Start Ranging")
            self.btn_start.setStyleSheet(
                "QPushButton { background: #059669; color: #F8FAFC; border: 1px solid #10B981; "
                "border-radius: 8px; font-weight: bold; font-size: 14px; }"
                "QPushButton:hover { background: #10B981; }"
            )
        self.btn_start.setEnabled(True)

    def _on_ranging_started(self):
        self._is_ranging = True
        self._sync_ranging_button()
        self._frame_count = 0
        self._start_time = time.time()
        self._canvas.clear_trail()
        self._last_stats = {}
        self._clear_live_metrics()
        self._render_stats()

    def _on_ranging_stopped(self):
        self._is_ranging = False
        self._distance_graph.stop_session()
        self._sync_ranging_button()
        self._clear_live_metrics()

    def _on_position_updated(self, x, y, z, rms):
        self._ensure_stream_active()
        self._frame_count += 1
        self._last_z = z
        self._last_rms = rms
        position = {
            "x": x,
            "y": y,
            "z": z,
            "error": rms,
            "yaw": self._apply_yaw_offset(0.0),
            "raw_yaw": 0.0,
            "source": "ranging",
        }
        self._canvas.update_position(position)
        if self._map_view_stack.currentWidget() is self._map_3d:
            self._map_3d.update_position(position)

        seq = None
        anchor_mask = None
        anchor_mask_valid = False
        timestamp_ms = None
        payload_size = None
        if self._vm and self._vm.model._position_history:
            last_sample = self._vm.model._position_history[-1]
            seq = last_sample.get("seq")
            anchor_mask = last_sample.get("anchor_mask")
            anchor_mask_valid = bool(last_sample.get("anchor_mask_valid", bool(last_sample.get("anchors"))))
            timestamp_ms = last_sample.get("timestamp_ms")
            payload_size = last_sample.get("payload_size")
            if anchor_mask_valid:
                self._last_anchor_mask = anchor_mask
                self._last_anchor_mask_valid = True

        self._set_metric_value(self.sof_label, "0xAA")
        self._set_metric_value(self.length_label, payload_size, "{:d}")
        self._set_metric_value(self.anchor_mask_label, self._format_anchor_mask(anchor_mask, anchor_mask_valid))
        ranging_anchors = last_sample.get("anchors", []) if self._vm and self._vm.model._position_history else []
        if hasattr(self._canvas, "set_anchor_telemetry"):
            self._canvas.set_anchor_telemetry(anchor_mask, ranging_anchors, anchor_mask_valid)
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
        self._ensure_stream_active()
        self._frame_count += 1
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
        ukf_step = int(data.get("ukf_step", 0))

        position = {
            "x": x,
            "y": y,
            "z": self._last_z,
            "error": self._last_rms,
            "yaw": yaw,
            "raw_yaw": yaw,
            "ukf_step": ukf_step,
            "tril_x": tril_x,
            "tril_y": tril_y,
            "source": "sensor_fusion",
        }
        self._canvas.update_position(position)
        if self._map_view_stack.currentWidget() is self._map_3d:
            self._map_3d.update_position(position)

        self._set_metric_value(self.sof_label, "0xAA")
        self._set_metric_value(self.length_label, data.get("payload_size"), "{:d}")

        anchor_mask = data.get("anchor_mask")
        anchor_mask_valid = bool(data.get("anchor_mask_valid", anchor_mask is not None and anchor_mask != ""))
        if not anchor_mask_valid:
            anchor_mask = getattr(self, "_last_anchor_mask", None)
            anchor_mask_valid = bool(getattr(self, "_last_anchor_mask_valid", False))
        else:
            self._last_anchor_mask = anchor_mask
            self._last_anchor_mask_valid = True
        self._set_metric_value(self.anchor_mask_label, self._format_anchor_mask(anchor_mask, anchor_mask_valid))
        anchors = list(data.get("anchors", []) or [])
        self._show_anchor_telemetry(anchors)
        if hasattr(self._canvas, "set_anchor_telemetry"):
            self._canvas.set_anchor_telemetry(anchor_mask, anchors, anchor_mask_valid)

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
        self._show_anchor_telemetry(anchors)

    def _on_calib_data_updated(self, data: dict):
        self._ensure_stream_active()
        self._distance_graph.append_sample(data)

    def _update_stats(self):
        if not self._is_ranging:
            return

        if not self._last_stats and self._frame_count == 0:
            self.uptime_label.setText("-")
            self._render_stats()
            return

        uptime = int(time.time() - self._start_time)
        self.uptime_label.setText(f"{uptime}s")
        self._render_stats()

    def _on_stats_updated(self, stats: dict):
        if stats:
            self._ensure_stream_active()
        self._last_stats = stats.copy()
        self._render_stats()

    def _render_stats(self):
        stats = self._last_stats
        if not stats and self._frame_count == 0:
            self.frames_label.setText("-")
            self.fps_label.setText("-")
            self.success_label.setText("-")
            self.failed_label.setText("-")
            self.timeout_label.setText("-")
            self.period_label.setText("-")
            self.success_rate_label.setText("-")
            self.avg_rssi_label.setText("-")
            self.last_range_time_label.setText("-")
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
        self.fps_label.setText(f"{rate:.1f} Hz")
        self.success_label.setText(str(success))
        self.failed_label.setText(str(failed))
        self.timeout_label.setText(str(timeout))
        self.period_label.setText(
            f"{int(stats['ranging_period_ms'])} ms" if "ranging_period_ms" in stats else "-"
        )
        self.success_rate_label.setText(
            f"{float(stats['success_rate_percent']):.1f}%" if "success_rate_percent" in stats else "-"
        )
        self.avg_rssi_label.setText(
            f"{int(stats['last_avg_rssi_dbm'])} dBm" if "last_avg_rssi_dbm" in stats else "-"
        )
        self.last_range_time_label.setText(
            f"{int(stats['last_ranging_time_ms'])} ms" if "last_ranging_time_ms" in stats else "-"
        )

    def set_anchors(self, anchors):
        self._canvas.set_anchors(anchors)
        self._map_3d.set_anchors(anchors)
        self._refresh_anchor_layout_table()

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

    def _room_zones(self):
        if not self._vm:
            return []
        return [
            zone for zone in self._vm.get_geofence_zones()
            if getattr(zone, "object_type", "zone") == "room"
        ]

    @staticmethod
    def _segment_lies_on_room_edge(p1, p2, edge_start, edge_end, tolerance):
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

    @staticmethod
    def _project_point_to_segment(point, edge_start, edge_end):
        ex = edge_end[0] - edge_start[0]
        ey = edge_end[1] - edge_start[1]
        edge_len_sq = ex * ex + ey * ey
        if edge_len_sq <= 1e-12:
            return float(edge_start[0]), float(edge_start[1])
        t = ((point[0] - edge_start[0]) * ex + (point[1] - edge_start[1]) * ey) / edge_len_sq
        t = max(0.0, min(1.0, t))
        return round(edge_start[0] + ex * t, 6), round(edge_start[1] + ey * t, 6)

    def _detect_boundary_wall_match(self, wall_points):
        points = list(wall_points or [])
        if len(points) < 2:
            return None, points
        snap_step = self._canvas._snap_step() if hasattr(self._canvas, "_snap_step") else 0.1
        tolerance = max(0.01, min(0.05, float(snap_step) * 0.30))
        for room in self._room_zones():
            room_points = list(getattr(room, "points", []) or [])
            if len(room_points) < 3:
                continue
            edges = [(room_points[idx], room_points[(idx + 1) % len(room_points)]) for idx in range(len(room_points))]
            snapped = list(points)
            matched = True
            for idx, (p1, p2) in enumerate(zip(points, points[1:])):
                edge = next(
                    (
                        (edge_start, edge_end)
                        for edge_start, edge_end in edges
                        if self._segment_lies_on_room_edge(p1, p2, edge_start, edge_end, tolerance)
                    ),
                    None,
                )
                if edge is None:
                    matched = False
                    break
                snapped[idx] = self._project_point_to_segment(p1, edge[0], edge[1])
                snapped[idx + 1] = self._project_point_to_segment(p2, edge[0], edge[1])
            if matched:
                return room, snapped
        return None, points
    def _find_room(self, room_id):
        if not room_id or not self._vm:
            return None
        return next(
            (
                zone for zone in self._vm.get_geofence_zones()
                if zone.id == room_id and getattr(zone, "object_type", "zone") == "room"
            ),
            None,
        )

    @staticmethod
    def _room_origin(room):
        points = list(getattr(room, "points", []) or [])
        if not points:
            return 0.0, 0.0
        index = getattr(room, "origin_vertex_idx", None)
        if index is None or not 0 <= int(index) < len(points):
            index = 0
        return float(points[int(index)][0]), float(points[int(index)][1])

    @staticmethod
    def _scene_to_room_local(scene_x, scene_y, room):
        origin_x, origin_y = LiveTrackingTab._room_origin(room)
        theta = math.radians(float(getattr(room, "local_frame_yaw_deg", 0.0)))
        dx, dy = float(scene_x) - origin_x, float(scene_y) - origin_y
        cos_theta, sin_theta = math.cos(theta), math.sin(theta)
        return cos_theta * dx + sin_theta * dy, -sin_theta * dx + cos_theta * dy

    @staticmethod
    def _room_local_to_scene(local_x, local_y, room):
        origin_x, origin_y = LiveTrackingTab._room_origin(room)
        theta = math.radians(float(getattr(room, "local_frame_yaw_deg", 0.0)))
        cos_theta, sin_theta = math.cos(theta), math.sin(theta)
        return (
            origin_x + cos_theta * float(local_x) - sin_theta * float(local_y),
            origin_y + sin_theta * float(local_x) + cos_theta * float(local_y),
        )

    def _anchor_scene_xy_from_payload(self, anchor: dict, item: dict | None = None):
        """Resolve anchor payloads to scene XY while preserving local storage semantics."""
        anchor = anchor or {}
        item = item or self._normalize_anchor_record(anchor, 0)
        room_id = anchor.get("room_id", anchor.get("zone_id", item.get("room_id", item.get("zone_id", ""))))
        room = self._find_room(room_id)
        if "x" in anchor and "y" in anchor:
            return float(anchor.get("x", 0.0)), float(anchor.get("y", 0.0)), room
        if room is not None:
            local_x = anchor.get("local_x_m", item.get("local_x_m", item.get("x_m", 0.0)))
            local_y = anchor.get("local_y_m", item.get("local_y_m", item.get("y_m", 0.0)))
            scene_x, scene_y = self._room_local_to_scene(local_x, local_y, room)
            return scene_x, scene_y, room
        return float(item.get("x_m", 0.0)), float(item.get("y_m", 0.0)), room

    def _selected_room(self):
        room = self._find_room(self._canvas.selected_zone_id)
        if room is not None:
            return room
        anchor_index = self._canvas.selected_anchor_idx
        if anchor_index is not None and 0 <= anchor_index < len(self._canvas.anchors):
            anchor = self._canvas.anchors[anchor_index]
            return self._find_room(anchor.get("room_id", anchor.get("zone_id", "")))
        return None

    def _format_anchors_for_canvas(self, anchors):
        formatted = []
        for idx, anchor in enumerate(anchors or []):
            item = self._normalize_anchor_record(anchor, idx)
            scene_x, scene_y, room = self._anchor_scene_xy_from_payload(anchor, item)
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
            scene_x, scene_y, room = self._anchor_scene_xy_from_payload(anchor, item)
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
                        "room_id": anchor.get("room_id", ""),
                        "local_x_m": anchor.get("local_x_m", anchor.get("x", 0.0)),
                        "local_y_m": anchor.get("local_y_m", anchor.get("y", 0.0)),
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

    def _validate_anchor_layout(self, anchors, *, require_four=False, min_anchors=None) -> tuple[list[str], list[str]]:
        errors = []
        warnings = []
        normalized = [self._normalize_anchor_record(anchor, idx) for idx, anchor in enumerate(anchors or [])]
        ids = [item["anchor_id"] for item in normalized]
        duplicates = sorted({anchor_id for anchor_id in ids if ids.count(anchor_id) > 1})
        if duplicates:
            errors.append("Duplicate anchor ID: " + ", ".join(f"A{x}" for x in duplicates))
        required_count = int(min_anchors) if min_anchors is not None else (4 if require_four else 0)
        if required_count and len(normalized) < required_count:
            errors.append(f"Anchor layout needs at least {required_count} anchors for this room.")
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
            if object_type in {"wall", "object"} and zone.max_z <= zone.min_z:
                warnings.append(f"{zone.name} height is not set.")
            if object_type == "object" and getattr(zone, "shape_kind", "polygon") == "circle" and float(getattr(zone, "radius_m", 0.0)) <= 0.0:
                warnings.append(f"{zone.name} circle radius is not set.")
        active_room_ids = self._vm.get_active_room_ids() if self._vm else []
        for room_id in active_room_ids:
            room = next((item for item in rooms if item.id == room_id), None)
            if room is None:
                errors.append("An active room no longer exists.")
            elif not self._room_has_anchor_layout(room_id):
                errors.append(f"Active room {room.name} needs at least 3 placed anchors.")
        if rooms and not active_room_ids:
            warnings.append("No active room is selected.")
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
        self._refresh_anchor_layout_table()
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

    # --- 2.5D GEOFENCING IMPLEMENTATION ---

    def _setup_user_map_ui(self):
        self.chk_enable_geofence.toggled.connect(self._on_enable_geofence_toggled)
        self.chk_enable_geofence.setChecked(True)
        self.cmb_user_map.currentIndexChanged.connect(self._on_user_map_changed)
        self._refresh_map_list()

    def _refresh_map_list(self):
        self.cmb_user_map.clear()

        maps_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "runtime"))

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
        self._setup_user_map_ui()

        self.sidebar_stack.setCurrentIndex(0)
        self.user_map_groupbox.setVisible(False)
        self._canvas._show_scale_bar = False
        self._canvas._show_mouse_coords = False
        self._canvas._show_tracking_grid = True
        self._canvas.is_developer_mode = False

        editor = self.geofence_editor_widget
        if not hasattr(editor, "btn_mode_object"):
            editor.btn_mode_object = QPushButton("Object", editor)
            editor.btn_mode_object.setCheckable(True)
            editor.btn_mode_object.setStyleSheet("QPushButton:checked { background-color: #F59E0B; color: #111827; border-color: #FCD34D; }")
            editor.map_modes_layout.insertWidget(2, editor.btn_mode_object)
        if editor.cmb_map_type.findText("Object") < 0:
            editor.cmb_map_type.insertItem(2, "Object")
        self._setup_anchor_authoring_controls(editor)
        self._setup_properties_tab(editor)
        editor.editor_tabs.currentChanged.connect(self._on_editor_tab_changed)
        editor.btn_mode_room.clicked.connect(lambda: self._set_editor_tool("room", "draw"))
        editor.btn_mode_wall.clicked.connect(lambda: self._set_editor_tool("wall", "draw"))
        editor.btn_mode_object.clicked.connect(lambda: self._set_editor_tool("object", "draw"))
        editor.btn_mode_anchor.clicked.connect(lambda: self._set_editor_tool("anchor", "pick_zone"))
        editor.btn_mode_edit_map.clicked.connect(lambda: self._set_editor_mode("edit_vertices"))
        editor.btn_mode_draw.clicked.connect(lambda: self._set_editor_tool("zone", "draw"))
        editor.btn_mode_edit.clicked.connect(lambda: self._set_editor_mode("edit_vertices"))
        editor.sb_grid_spacing.valueChanged.connect(self._update_grid_settings)
        editor.sb_grid_subdivisions.valueChanged.connect(self._update_grid_settings)
        editor.cmb_map_type.currentIndexChanged.connect(self._sync_map_height_visibility)
        editor.btn_apply_map_properties.clicked.connect(self._apply_map_properties)
        editor.txt_map_name.returnPressed.connect(self._apply_map_properties_from_enter)
        for spinbox in (editor.sb_map_height, editor.sb_anchor_x, editor.sb_anchor_y, editor.sb_anchor_z):
            spinbox.lineEdit().returnPressed.connect(self._apply_map_properties_from_enter)
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
        self._canvas.room_origin_vertex_picked.connect(self._on_canvas_room_origin_vertex_picked)
        self._canvas.zones_undo_remove_requested.connect(self._undo_remove_zones)
        self._canvas.zones_undo_restore_requested.connect(self._undo_restore_zones)
        self._undo_shortcut = QShortcut(QKeySequence.StandardKey.Undo, self)
        self._undo_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._undo_shortcut.activated.connect(self._canvas.undo_last_action)

        # Delete shortcut (Del)
        self._delete_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Delete), self)
        self._delete_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._delete_shortcut.activated.connect(self._delete_selected_zone)

        # Cut shortcut (Ctrl+X)
        self._cut_shortcut = QShortcut(QKeySequence("Ctrl+X"), self)
        self._cut_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._cut_shortcut.activated.connect(self._cut_selected_object)

        # Copy shortcut (Ctrl+C)
        self._copy_shortcut = QShortcut(QKeySequence("Ctrl+C"), self)
        self._copy_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._copy_shortcut.activated.connect(self._copy_selected_object)

        # Paste shortcut (Ctrl+V)
        self._paste_shortcut = QShortcut(QKeySequence("Ctrl+V"), self)
        self._paste_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._paste_shortcut.activated.connect(self._paste_object)


        self._update_grid_settings()
        self._sync_map_height_visibility()
        self._set_editor_tool("room", "draw")

    def _setup_properties_tab(self, editor):
        editor.sync_gb.hide()
        editor.gb_map_properties.setTitle("Anchor Properties")
        editor.editor_content_layout.setSpacing(4)
        editor.map_tab_layout.setContentsMargins(8, 0, 8, 4)
        editor.map_tab_layout.setSpacing(0)
        editor.map_tab_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        if hasattr(editor, "map_modes_layout"):
            editor.map_modes_layout.setContentsMargins(0, 0, 0, 0)
            editor.map_modes_layout.setSpacing(6)
        self._properties_panel = ZonePropertyPanel(embedded=True)
        self._properties_panel.closed.connect(lambda: None)
        self._properties_panel.property_changed.connect(self._canvas._on_panel_property_changed)
        self._properties_panel.edge_changed.connect(self._canvas._on_panel_edge_changed)
        self._properties_scroll = self._properties_panel
        editor.map_tab_layout.insertWidget(1, self._properties_panel)
        editor.map_tab_layout.setStretch(0, 0)
        editor.map_tab_layout.setStretch(1, 0)
        self._properties_panel.load_draft("room")
        self._properties_panel.show()

        self._rule_color_label = QLabel("Theme Color:", editor)
        self._rule_color_combo = QComboBox(editor)
        editor.properties_form_layout.insertRow(3, self._rule_color_label, self._rule_color_combo)
        self._rule_color_button = QPushButton("Choose color...", editor)
        self._rule_color_button.clicked.connect(self._choose_rule_color)
        editor.properties_form_layout.insertRow(4, QLabel("Custom:", editor), self._rule_color_button)
        editor.cmb_zone_type.setItemText(0, "Allowed / Speed Limited")
        editor.cmb_zone_type.setItemText(1, "Banned (No Entry)")
        editor.cmb_zone_type.currentIndexChanged.connect(self._on_rule_zone_type_changed)
        self._install_property_extensions(editor)
        self._on_rule_zone_type_changed(editor.cmb_zone_type.currentIndex())

        self._geometry_button = QPushButton("Add Point on Edge", self._properties_panel)
        self._geometry_button.setToolTip("Select a room, wall, or object, then click an edge to insert a point.")
        self._geometry_button.clicked.connect(self._begin_insert_vertex)
        self._properties_panel.form_layout.addRow(QLabel("Geometry:", self._properties_panel), self._geometry_button)
        self._properties_panel.schedule_height_refresh()
        self._properties_panel.cmb_object_shape.currentIndexChanged.connect(self._sync_object_drawing_options)
        self._properties_panel.cmb_object_kind.currentIndexChanged.connect(self._sync_object_drawing_options)

        # Reserve the middle area for object properties and pin file actions low.
        content_layout = editor.editor_content_layout
        for index in reversed(range(content_layout.count())):
            item = content_layout.itemAt(index)
            if item.spacerItem() is not None:
                content_layout.takeAt(index)
        delete_index = content_layout.indexOf(editor.btn_delete_zone)
        if delete_index >= 0:
            content_layout.insertStretch(delete_index, 1)

    def _install_property_extensions(self, editor):
        if not hasattr(editor, "cmb_wall_mode"):
            editor.cmb_wall_mode = QComboBox(editor.gb_map_properties)
            editor.cmb_wall_mode.addItem("Boundary outside room", "boundary_outside")
            editor.cmb_wall_mode.addItem("Internal partition", "internal_partition")
            editor.cmb_wall_mode.addItem("Free-standing", "free_standing")
            editor.cmb_wall_host_room = QComboBox(editor.gb_map_properties)
            editor.map_properties_form_layout.addRow("Wall behavior:", editor.cmb_wall_mode)
            editor.map_properties_form_layout.addRow("Host room:", editor.cmb_wall_host_room)
        if not hasattr(editor, "lbl_map_geometry"):
            editor.lbl_map_geometry = QLabel(editor.tab_map_layout)
            editor.lbl_map_geometry.setWordWrap(True)
            editor.lbl_map_geometry.setStyleSheet("color: #93C5FD; font-family: Consolas; padding: 5px;")
            editor.map_tab_layout.insertWidget(1, editor.lbl_map_geometry)

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
        label = getattr(editor, "lbl_map_geometry", None)
        if label is None:
            return
        if zone is None and self._vm and self._canvas.selected_zone_id:
            zone = next((z for z in self._vm.get_geofence_zones() if z.id == self._canvas.selected_zone_id), None)
        if zone is None:
            label.setText("Select an object to inspect dimensions.")
            return
        object_type = getattr(zone, "object_type", "zone")
        area, perimeter, edges = self._polygon_metrics(zone.points, closed=object_type != "wall")
        edge_parts = [f"S{i + 1} {length:.2f}m @ {angle:.0f}deg" for i, (length, angle) in enumerate(edges[:6])]
        if len(edges) > 6:
            edge_parts.append(f"+{len(edges) - 6} more")
        edge_line = " | ".join(edge_parts)
        if object_type == "wall":
            text = f"Length {perimeter:.2f} m | Height {max(0.0, zone.max_z - zone.min_z):.2f} m | Thickness {float(getattr(zone, 'thickness', 0.2)):.2f} m"
        elif object_type == "object":
            text = f"{getattr(zone, 'object_subtype', 'generic').title()} | {getattr(zone, 'shape_kind', 'polygon').title()} | H {max(0.0, zone.max_z - zone.min_z):.2f} m | R {float(getattr(zone, 'radius_m', 0.0)):.2f} m"
        else:
            text = f"Area {area:.2f} m2 | Perimeter {perimeter:.2f} m"
        if edge_line:
            text += "\nEdges: " + edge_line
        label.setText(text)

    def _refresh_wall_host_rooms(self, selected_host_id=None):
        combo = getattr(self.geofence_editor_widget, "cmb_wall_host_room", None)
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
    def _sync_room_origins(self, zones):
        for zone in zones or []:
            if getattr(zone, "object_type", "zone") != "room":
                continue
            origin = self._room_origin(zone)
            self._canvas.set_room_origin(zone.id, origin)
    def _on_rule_zone_type_changed(self, _index):
        allowed = self.geofence_editor_widget.cmb_zone_type.currentIndex() == 0
        self.geofence_editor_widget.lbl_prop_speed.setVisible(allowed)
        self.geofence_editor_widget.sb_speed.setVisible(allowed)
        if not allowed:
            self.geofence_editor_widget.sb_speed.setValue(0.0)
        self._refresh_rule_zone_colors(selected_color="#22C55E" if allowed else "#EF4444")

    def _refresh_rule_zone_colors(self, *_args, selected_color=None):
        if not hasattr(self, "_rule_color_combo"):
            return
        allowed = self.geofence_editor_widget.cmb_zone_type.currentIndex() == 0
        colors = (
            [
                ("Speed Green", "#22C55E"),
                ("Review Purple", "#A855F7"),
                ("Caution Yellow", "#EAB308"),
                ("Info Blue", "#3B82F6"),
            ]
            if allowed
            else [
                ("No-Go Red", "#EF4444"),
                ("No-Go Black", "#0F172A"),
                ("No-Go Gray", "#475569"),
            ]
        )
        current = selected_color or self._rule_color_combo.currentData()
        self._rule_color_combo.blockSignals(True)
        self._rule_color_combo.clear()
        selected_index = 0
        matched = False
        for index, (name, color) in enumerate(colors):
            self._rule_color_combo.addItem(name, color)
            if color == current:
                selected_index = index
                matched = True
        if current and QColor(str(current)).isValid() and not matched:
            self._rule_color_combo.addItem(f"Custom {current}", current)
            selected_index = self._rule_color_combo.count() - 1
        self._rule_color_combo.setCurrentIndex(selected_index)
        self._rule_color_combo.blockSignals(False)
        if hasattr(self, "_rule_color_button"):
            color = QColor(self._rule_color_combo.currentData() or "#22C55E")
            text_color = "#0F172A" if color.lightness() > 150 else "#F8FAFC"
            self._rule_color_button.setStyleSheet(
                f"background: {color.name()}; color: {text_color}; border: 1px solid #64748B; border-radius: 5px; font-weight: bold;"
            )

    def _choose_rule_color(self):
        current = self._rule_color_combo.currentData() or "#22C55E"
        color = QColorDialog.getColor(QColor(current), self, "Select display color")
        if not color.isValid():
            return
        value = color.name().upper()
        index = self._rule_color_combo.findData(value)
        if index < 0:
            self._rule_color_combo.addItem(f"Custom {value}", value)
            index = self._rule_color_combo.count() - 1
        self._rule_color_combo.setCurrentIndex(index)
        text_color = "#0F172A" if color.lightness() > 150 else "#F8FAFC"
        self._rule_color_button.setStyleSheet(
            f"background: {value}; color: {text_color}; border: 1px solid #64748B; border-radius: 5px; font-weight: bold;"
        )

    def _sync_object_drawing_options(self, *_args):
        shape = self._properties_panel.cmb_object_shape.currentData() or "polygon"
        if self._properties_panel.cmb_object_kind.currentData() == "stairs":
            shape = "polygon"
            self._properties_panel.cmb_object_shape.blockSignals(True)
            self._properties_panel.cmb_object_shape.setCurrentIndex(0)
            self._properties_panel.cmb_object_shape.blockSignals(False)
        self._canvas.set_draw_object_shape(shape)

    def _begin_insert_vertex(self):
        if not self._canvas.selected_zone_id:
            QMessageBox.information(self, "Add Point", "Select a room, wall, or object first.")
            return
        if not self._canvas.begin_insert_vertex():
            QMessageBox.information(self, "Add Point", "The selected geometry has no editable edge.")

    def _undo_remove_zones(self, zone_ids):
        if not self._vm:
            return
        for zone_id in zone_ids:
            self._vm.geofence_repo.remove_zone(zone_id)
        self._canvas.set_geofences(self._vm.get_geofence_zones())
        self._canvas.set_selected_zone(None)
        self._vm.geofence_layout_updated.emit(self._vm.get_geofence_zones())
        self._refresh_anchor_membership_from_canvas()
        self._active_rooms_snapshot = None
        self._refresh_active_rooms()

    def _undo_restore_zones(self, zone_snapshots):
        if not self._vm:
            return
        selected_id = self._canvas.selected_zone_id
        zones = [GeofenceZone.from_dict(snapshot) for snapshot in zone_snapshots]
        if hasattr(self._vm, "set_geofence_zones"):
            self._vm.set_geofence_zones(zones)
        else:
            self._vm.geofence_repo.set_zones(zones)
            self._vm.geofence_layout_updated.emit(self._vm.get_geofence_zones())
        restored_zones = self._vm.get_geofence_zones()
        self._canvas.set_geofences(restored_zones)
        self._reload_selected_zone_properties(selected_id)
        QTimer.singleShot(0, lambda zone_id=selected_id: self._reload_selected_zone_properties(zone_id))
        self._refresh_anchor_membership_from_canvas()
        self._active_rooms_snapshot = None
        self._refresh_active_rooms()

    def _reload_selected_zone_properties(self, zone_id):
        if not self._vm:
            return
        restored_selection = next((zone for zone in self._vm.get_geofence_zones() if zone.id == zone_id), None)
        if restored_selection is None:
            self._canvas.set_selected_zone(None)
            self.geofence_editor_widget.txt_zone_name.clear()
            self.geofence_editor_widget.txt_map_name.clear()
            return
        self._canvas.set_selected_zone(restored_selection.id)
        self._load_zone_properties_to_ui(restored_selection)
        if hasattr(self, "_properties_panel"):
            self._properties_panel.load_zone(restored_selection)
            self._properties_panel.update()
        self.geofence_editor_widget.update()
        self._canvas.update()

    def _setup_anchor_authoring_controls(self, editor):
        # Configure coordinates inputs
        for spin in (editor.sb_anchor_x, editor.sb_anchor_y, editor.sb_anchor_z):
            spin.setRange(-1000.0, 1000.0)
            spin.setDecimals(3)
            spin.setSingleStep(0.1)
            spin.setSuffix(" m")


        # Wire anchor button actions
        self._setup_anchor_sync_button(editor)
        editor.btn_add_anchor.clicked.connect(self._add_anchor)
        editor.btn_remove_anchor.clicked.connect(self._remove_selected_anchor)
        editor.btn_assign_anchor.setText("Set Local Origin")
        editor.btn_assign_anchor.setToolTip("Select a room, then click one of its vertices to set local (0,0)")
        editor.btn_assign_anchor.clicked.connect(self._on_set_local_origin_clicked)

        # Device target setup
        editor.cmb_device_target.setItemData(
            0,
            {
                "proto_dst_addr": int(VvAddress.MCU),
                "device_id": 1,
                "role": "tag",
            },
        )
        editor.btn_read_layout_dev.clicked.connect(self._read_layout_from_device)
        editor.btn_write_layout_dev.clicked.connect(self._write_layout_to_device)

        # Load map setup
        editor.btn_load_map.clicked.connect(self._load_map)

        self._setup_anchor_keyboard_helpers(editor)
        self._sync_map_height_visibility()
        self._setup_anchor_layout_table(editor)
        self._refresh_anchor_status_label()
        self._refresh_active_rooms()

    def _is_anchor_tool_active(self):
        return bool(getattr(self._canvas, "draw_object_type", "") == "anchor")

    def _run_anchor_shortcut(self, callback):
        if self._is_anchor_tool_active():
            callback()

    def _show_anchor_layout_menu(self):
        if not self._is_anchor_tool_active():
            return
        button = self.geofence_editor_widget.btn_create_default_anchors
        menu = button.menu()
        if menu is not None:
            menu.popup(button.mapToGlobal(button.rect().bottomLeft()))

    def _setup_anchor_keyboard_helpers(self, editor):
        shortcuts = (
            (editor.btn_add_anchor, "Alt+A", "Add Anchor"),
            (editor.btn_assign_anchor, "Alt+O", "Set Local Origin"),
            (editor.btn_remove_anchor, "Alt+Delete", "Remove Anchor"),
        )
        for button, shortcut, label in shortcuts:
            button.setShortcut(QKeySequence(shortcut))
            button.setToolTip(f"{label} ({shortcut})")
        editor.btn_create_default_anchors.setToolTip("Anchor Layout (Alt+L). Read: Alt+R, Write: Alt+W")

        self._anchor_read_shortcut = QShortcut(QKeySequence("Alt+R"), self.geofence_editor_widget)
        self._anchor_read_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._anchor_read_shortcut.activated.connect(lambda: self._run_anchor_shortcut(self._read_layout_from_device))
        self._anchor_write_shortcut = QShortcut(QKeySequence("Alt+W"), self.geofence_editor_widget)
        self._anchor_write_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._anchor_write_shortcut.activated.connect(lambda: self._run_anchor_shortcut(self._write_layout_to_device))
        self._anchor_layout_menu_shortcut = QShortcut(QKeySequence("Alt+L"), self.geofence_editor_widget)
        self._anchor_layout_menu_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._anchor_layout_menu_shortcut.activated.connect(self._show_anchor_layout_menu)
    def _setup_anchor_sync_button(self, editor):
        button = editor.btn_create_default_anchors
        button.setText("Anchor Layout")
        button.setToolTip("Anchor Layout (Alt+L). Read: Alt+R, Write: Alt+W")
        menu = QMenu(button)
        read_action = QAction("Read", button)
        write_action = QAction("Write", button)
        read_action.triggered.connect(self._read_layout_from_device)
        write_action.triggered.connect(self._write_layout_to_device)
        menu.addAction(read_action)
        menu.addAction(write_action)
        button.setMenu(menu)

    def _apply_anchor_template_from_combo(self):
        name = self.geofence_editor_widget.txt_map_name.text().strip()
        anchor_id = None
        if name.lower().startswith("a") and name[1:].isdigit():
            anchor_id = self._coerce_int_id(name[1:], 0)
        template = {
            "anchor_id": anchor_id,
            "label": name or None,
            "role": "anchor",
            "device_type": "uwb_anchor",
        } if name else None
        self._canvas.set_anchor_template(template)

    def _set_anchor_property_enabled(self, enabled):
        for name in ("txt_map_name", "cmb_map_type", "sb_anchor_x", "sb_anchor_y", "sb_anchor_z", "btn_apply_map_properties"):
            widget = getattr(self.geofence_editor_widget, name, None)
            if widget is not None:
                widget.setEnabled(enabled)

    def _clear_anchor_property_fields(self):
        self.geofence_editor_widget.txt_map_name.clear()
        if hasattr(self.geofence_editor_widget, "sb_anchor_x"):
            self.geofence_editor_widget.lbl_anchor_x.setText("Local X:")
            self.geofence_editor_widget.lbl_anchor_y.setText("Local Y:")
            self.geofence_editor_widget.lbl_anchor_z.setText("Z:")
            self.geofence_editor_widget.sb_anchor_x.setValue(0.0)
            self.geofence_editor_widget.sb_anchor_y.setValue(0.0)
            self.geofence_editor_widget.sb_anchor_z.setValue(0.0)
            self.geofence_editor_widget.sb_anchor_x.setToolTip("Select an anchor to edit its local X coordinate.")
            self.geofence_editor_widget.sb_anchor_y.setToolTip("Select an anchor to edit its local Y coordinate.")
            self.geofence_editor_widget.sb_anchor_z.setToolTip("Select an anchor to edit its height.")
        anchor_type_idx = self.geofence_editor_widget.cmb_map_type.findText("Anchor")
        if anchor_type_idx >= 0:
            self.geofence_editor_widget.cmb_map_type.setCurrentIndex(anchor_type_idx)
        self._set_anchor_property_enabled(False)
    def _anchor_local_xyz(self, anchor):
        room = self._find_room(anchor.get("room_id", anchor.get("zone_id", "")))
        if room is not None:
            local_x, local_y = self._scene_to_room_local(
                anchor.get("x", 0.0), anchor.get("y", 0.0), room
            )
            return local_x, local_y, float(anchor.get("z", 0.0)), True
        return float(anchor.get("x", 0.0)), float(anchor.get("y", 0.0)), float(anchor.get("z", 0.0)), False

    def _setup_anchor_layout_table(self, editor):
        self._anchor_layout_group = QGroupBox("Anchor Layout", editor)
        layout = QVBoxLayout(self._anchor_layout_group)
        layout.setContentsMargins(6, 8, 6, 4)
        layout.setSpacing(3)
        self._active_rooms_label = QLabel("Active rooms (max 4)", self._anchor_layout_group)
        self._active_rooms_layout = QVBoxLayout()
        self._active_rooms_layout.setSpacing(1)
        layout.addWidget(self._active_rooms_label)
        layout.addLayout(self._active_rooms_layout)
        self._anchor_layout_table = QTableWidget(0, 4, self._anchor_layout_group)
        self._anchor_layout_table.setHorizontalHeaderLabels(["ID", "X", "Y", "Z"])
        self._anchor_layout_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._anchor_layout_table.verticalHeader().setVisible(False)
        self._anchor_layout_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._anchor_layout_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._anchor_layout_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._anchor_layout_table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        self._anchor_layout_table.cellClicked.connect(self._on_anchor_layout_row_clicked)
        self._anchor_layout_table.cellChanged.connect(self._on_anchor_layout_cell_changed)
        self._resize_anchor_layout_table(0)
        layout.addWidget(self._anchor_layout_table)
        editor.map_tab_layout.insertWidget(2, self._anchor_layout_group)
        self._anchor_layout_group.hide()

    def _room_name(self, room_id):
        room = self._find_room(room_id)
        return room.name if room is not None else "selected room"

    def _anchor_table_room_ids(self):
        if not self._vm:
            self._anchor_table_room_id = ""
            return []
        focus_id = str(getattr(self, "_anchor_table_room_id", "") or "")
        if focus_id and self._find_room(focus_id) is not None:
            return [focus_id]

        active_ids = self._vm.get_active_room_ids()
        for room_id in active_ids:
            if self._find_room(room_id) is not None:
                self._anchor_table_room_id = room_id
                return [room_id]

        selected_room = self._selected_room()
        if selected_room is not None:
            self._anchor_table_room_id = selected_room.id
            return [selected_room.id]

        self._anchor_table_room_id = ""
        return []

    def _anchor_entries_for_table(self):
        room_ids = set(self._anchor_table_room_ids())
        entries = []
        if not room_ids:
            return entries
        for index, anchor in enumerate(self._canvas.anchors):
            annotated = self._annotate_anchor_membership([anchor])[0]
            if annotated.get("room_id", annotated.get("zone_id", "")) in room_ids:
                entries.append((index, annotated, anchor))
        return entries

    def _resize_anchor_layout_table(self, row_count):
        table = getattr(self, "_anchor_layout_table", None)
        if table is None:
            return
        visible_rows = max(1, min(int(row_count or 0), 5))
        header_h = max(table.horizontalHeader().height(), 24)
        row_h = max(table.verticalHeader().defaultSectionSize(), 28)
        frame = table.frameWidth() * 2
        table.setFixedHeight(header_h + row_h * visible_rows + frame + 4)
        policy = (
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
            if row_count > visible_rows
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        table.setVerticalScrollBarPolicy(policy)

    def _refresh_anchor_layout_table(self):
        table = getattr(self, "_anchor_layout_table", None)
        if table is None:
            return
        entries = self._anchor_entries_for_table()
        self._anchor_layout_row_map = [index for index, _annotated, _anchor in entries]
        room_ids = self._anchor_table_room_ids()
        if hasattr(self, "_anchor_layout_group"):
            if room_ids:
                self._anchor_layout_group.setTitle(f"Anchor Layout - {self._room_name(room_ids[0])}")
            else:
                self._anchor_layout_group.setTitle("Anchor Layout - select/activate a room")
        table.blockSignals(True)
        table.setRowCount(len(entries))
        self._resize_anchor_layout_table(len(entries))
        for row, (_anchor_index, annotated, anchor) in enumerate(entries):
            anchor_id = self._coerce_int_id(anchor.get("anchor_id"), row)
            values = (
                f"A{anchor_id}",
                f"{float(annotated.get('local_x_m', anchor.get('x', 0.0))):.3f}",
                f"{float(annotated.get('local_y_m', anchor.get('y', 0.0))):.3f}",
                f"{float(anchor.get('z', anchor.get('z_m', 0.0))):.3f}",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                table.setItem(row, column, item)
        table.blockSignals(False)

    def _on_anchor_layout_row_clicked(self, row, _column):
        anchor_index = self._anchor_layout_row_map[row] if 0 <= row < len(self._anchor_layout_row_map) else None
        if anchor_index is not None and 0 <= anchor_index < len(self._canvas.anchors):
            self._canvas.set_selected_anchor(anchor_index)
            self._on_canvas_anchor_selected(anchor_index)

    def _on_anchor_layout_cell_changed(self, row, column):
        if not (0 <= row < len(getattr(self, "_anchor_layout_row_map", []))):
            return
        anchor_index = self._anchor_layout_row_map[row]
        if not (0 <= anchor_index < len(self._canvas.anchors)):
            return
        item = self._anchor_layout_table.item(row, column)
        if item is None:
            return
        anchor = self._canvas.anchors[anchor_index]
        room = self._find_room(anchor.get("room_id", anchor.get("zone_id", "")))
        self._anchor_layout_table.blockSignals(True)
        try:
            if column == 0:
                text = item.text().strip()
                parsed_id = self._coerce_int_id(text, self._coerce_int_id(anchor.get("anchor_id"), anchor_index))
                if any(
                    index != anchor_index and self._coerce_int_id(other.get("anchor_id"), -1) == parsed_id
                    for index, other in enumerate(self._canvas.anchors)
                ):
                    QMessageBox.warning(self, "Duplicate Anchor", f"A{parsed_id} is already used.")
                    self._refresh_anchor_layout_table()
                    return
                anchor["anchor_id"] = parsed_id
                anchor["device_id"] = parsed_id
                anchor["label"] = f"A{parsed_id}"
            else:
                try:
                    value = float(item.text().strip())
                except ValueError:
                    self._refresh_anchor_layout_table()
                    return
                if column in (1, 2) and room is not None:
                    local_x, local_y = self._scene_to_room_local(
                        anchor.get("x", 0.0), anchor.get("y", 0.0), room
                    )
                    if column == 1:
                        local_x = value
                    else:
                        local_y = value
                    scene_x, scene_y = self._room_local_to_scene(local_x, local_y, room)
                    anchor.update({"x": scene_x, "y": scene_y, "local_x_m": local_x, "local_y_m": local_y})
                elif column == 1:
                    anchor["x"] = value
                    anchor["local_x_m"] = value
                elif column == 2:
                    anchor["y"] = value
                    anchor["local_y_m"] = value
                else:
                    anchor["z"] = value
            self._canvas._emit_anchor_layout_edited()
            self._canvas.update()
        finally:
            self._anchor_layout_table.blockSignals(False)
        self._refresh_anchor_status_label()

    def _layout_scope_room_id(self, *, require=True):
        room_ids = self._anchor_table_room_ids()
        if room_ids:
            return room_ids[0]
        if require:
            QMessageBox.information(self, "Anchor Layout", "Select or activate a room before syncing its anchor layout.")
        return ""

    def _anchor_layout_for_room(self, room_id):
        if not room_id:
            return []
        anchors = self._annotate_anchor_membership(self._canvas.anchor_layout_for_device())
        return [
            anchor for anchor in anchors
            if anchor.get("placed", True) and anchor.get("room_id", anchor.get("zone_id", "")) == room_id
        ]

    def _merge_anchor_layout_into_room(self, room_id, anchors):
        if not room_id:
            return
        current = self._annotate_anchor_membership(self._canvas.anchor_layout_for_device())
        kept = [
            anchor for anchor in current
            if anchor.get("room_id", anchor.get("zone_id", "")) != room_id
        ]
        room = self._find_room(room_id)
        room_name = room.name if room is not None else ""
        incoming = []
        for idx, anchor in enumerate(anchors or []):
            item = self._normalize_anchor_record(anchor, idx)
            local_x = float(anchor.get("local_x_m", anchor.get("x_m", item["x_m"])))
            local_y = float(anchor.get("local_y_m", anchor.get("y_m", item["y_m"])))
            item.update({
                "room_id": room_id,
                "zone_id": room_id,
                "zone_name": room_name,
                "zone_ids": [room_id],
                "zone_names": [room_name] if room_name else [],
                "local_x_m": local_x,
                "local_y_m": local_y,
                "x_m": local_x,
                "y_m": local_y,
                "placed": True,
                "sync_state": "synced",
            })
            incoming.append(item)
        merged = kept + incoming
        self._draft_anchor_layout = self._annotate_anchor_membership(merged)
        self._canvas.set_anchors(self._format_anchors_for_canvas(self._draft_anchor_layout))
        self._anchor_layout_commit_pending = True
        if self._vm:
            self._vm.geofence_repo.set_anchors(self._draft_anchor_layout)
        shared_app_state.anchor_layout = [dict(anchor) for anchor in self._draft_anchor_layout]
        self._refresh_anchor_status_label()
        self._refresh_active_rooms()

    def _room_has_anchor_layout(self, room_id, min_anchors=3):
        if not room_id:
            return False
        anchors = self._annotate_anchor_membership(self._canvas.anchor_layout_for_device())
        return sum(
            1 for anchor in anchors
            if anchor.get("placed", True) and anchor.get("room_id", anchor.get("zone_id", "")) == room_id
        ) >= min_anchors

    def _refresh_active_rooms(self):
        layout = getattr(self, "_active_rooms_layout", None)
        if layout is None or not self._vm:
            return
        active_ids = self._vm.get_active_room_ids()
        self._canvas.set_active_room_ids(active_ids)
        if hasattr(self, "_map_3d") and hasattr(self._map_3d, "set_active_room_ids"):
            self._map_3d.set_active_room_ids(active_ids)
        rooms = self._room_zones()
        snapshot = tuple((room.id, room.name, self._room_has_anchor_layout(room.id), room.id in active_ids) for room in rooms)
        if snapshot == getattr(self, "_active_rooms_snapshot", None):
            return
        self._active_rooms_snapshot = snapshot
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for room in rooms:
            ready = self._room_has_anchor_layout(room.id)
            check = QCheckBox(room.name + ("" if ready else " (needs anchors)"), self._anchor_layout_group)
            check.setProperty("room_id", room.id)
            check.setChecked(room.id in active_ids)
            check.setEnabled(ready or room.id in active_ids)
            check.toggled.connect(self._on_active_room_toggled)
            layout.addWidget(check)

    def _on_active_room_toggled(self, checked):
        check = self.sender()
        room_id = str(check.property("room_id") or "")
        active_ids = self._vm.get_active_room_ids()
        if checked:
            if room_id in active_ids:
                return
            if len(active_ids) >= 4:
                QMessageBox.warning(self, "Active Room Limit", "Only up to 4 rooms can be active.")
                check.blockSignals(True)
                check.setChecked(False)
                check.blockSignals(False)
                return
            if not self._room_has_anchor_layout(room_id):
                QMessageBox.warning(self, "Room Needs Anchors", "Place at least 3 anchors in this room first.")
                check.blockSignals(True)
                check.setChecked(False)
                check.blockSignals(False)
                return
            active_ids.append(room_id)
        else:
            active_ids = [item for item in active_ids if item != room_id]
        self._vm.set_active_room_ids(active_ids)
        self._canvas.set_active_room_ids(active_ids)
        if checked:
            self._anchor_table_room_id = room_id
        elif self._anchor_table_room_id == room_id:
            self._anchor_table_room_id = active_ids[0] if active_ids else ""
        self._active_rooms_snapshot = None
        self._refresh_active_rooms()
        self._refresh_anchor_layout_table()

    def _on_set_local_origin_clicked(self):
        if self._canvas._origin_pick_room_id is not None:
            self._canvas.cancel_room_origin_pick()
            self.geofence_editor_widget.btn_assign_anchor.setText("Set Local Origin")
            return
        room = self._selected_room()
        if room is None or len(room.points) < 3:
            QMessageBox.information(self, "Set Local Origin", "Select a Room on the map first.")
            return
        self._set_editor_mode("edit_vertices")
        if self._canvas.begin_room_origin_pick(room.id):
            self.geofence_editor_widget.btn_assign_anchor.setText("Click a corner...")

    def _on_canvas_room_origin_vertex_picked(self, room_id, vertex_index):
        room = self._find_room(room_id)
        if room is None or not (0 <= vertex_index < len(room.points)):
            return
        room.origin_vertex_idx = vertex_index
        self._canvas.set_room_origin(room.id, room.points[vertex_index])
        self.geofence_editor_widget.btn_assign_anchor.setText("Set Local Origin")
        if self._vm:
            self._vm.geofence_layout_updated.emit(self._vm.get_geofence_zones())
        self._refresh_anchor_membership_from_canvas()
        self._refresh_anchor_layout_table()

    def _selected_device_target(self):
        if hasattr(self.geofence_editor_widget, "cmb_device_target"):
            data = self.geofence_editor_widget.cmb_device_target.currentData()
            if isinstance(data, dict):
                return data
        return {
            "proto_dst_addr": int(VvAddress.MCU),
            "device_id": 1,
            "role": "tag",
        }

    def _create_default_anchors(self):
        if not self._vm:
            return
        room = self._selected_room()
        if room is not None:
            local_points = [self._scene_to_room_local(x, y, room) for x, y in room.points]
            min_x = min(point[0] for point in local_points)
            max_x = max(point[0] for point in local_points)
            min_y = min(point[1] for point in local_points)
            max_y = max(point[1] for point in local_points)
            anchors = [
                {"anchor_id": 0, "label": "A0", "room_id": room.id, "local_x_m": min_x, "local_y_m": min_y, "x_m": min_x, "y_m": min_y, "z_m": 0.0},
                {"anchor_id": 1, "label": "A1", "room_id": room.id, "local_x_m": max_x, "local_y_m": min_y, "x_m": max_x, "y_m": min_y, "z_m": 0.0},
                {"anchor_id": 2, "label": "A2", "room_id": room.id, "local_x_m": max_x, "local_y_m": max_y, "x_m": max_x, "y_m": max_y, "z_m": 0.0},
                {"anchor_id": 3, "label": "A3", "room_id": room.id, "local_x_m": min_x, "local_y_m": max_y, "x_m": min_x, "y_m": max_y, "z_m": 0.0},
            ]
        else:
            zones = self._vm.get_geofence_zones()
            points = [point for zone in zones for point in getattr(zone, "points", [])]
            if points:
                min_x, max_x = min(p[0] for p in points), max(p[0] for p in points)
                min_y, max_y = min(p[1] for p in points), max(p[1] for p in points)
            else:
                min_x, min_y, max_x, max_y = 0.0, 0.0, 9.8, 9.8
            anchors = [
                {"anchor_id": 0, "label": "A0", "x_m": min_x, "y_m": min_y, "z_m": 0.0},
                {"anchor_id": 1, "label": "A1", "x_m": max_x, "y_m": min_y, "z_m": 0.0},
                {"anchor_id": 2, "label": "A2", "x_m": max_x, "y_m": max_y, "z_m": 0.0},
                {"anchor_id": 3, "label": "A3", "x_m": min_x, "y_m": max_y, "z_m": 0.0},
            ]
        self._draft_anchor_layout = self._annotate_anchor_membership(anchors)
        self._canvas.set_anchors(self._format_anchors_for_canvas(self._draft_anchor_layout))
        self._anchor_layout_commit_pending = True
        self._vm.geofence_repo.set_anchors(self._draft_anchor_layout)
        shared_app_state.anchor_layout = [dict(anchor) for anchor in self._draft_anchor_layout]
        self._refresh_anchor_status_label()

    def _add_anchor(self):
        if not self._vm:
            return
        room_id = self._layout_scope_room_id(require=False)
        room = self._find_room(room_id)
        if room is None:
            QMessageBox.information(self, "Add Anchor", "Select a room first, then press Add Anchor.")
            return

        used_ids = {self._coerce_int_id(anchor.get("anchor_id"), idx) for idx, anchor in enumerate(self._canvas.anchors)}
        anchor_id = 0
        while anchor_id in used_ids:
            anchor_id += 1

        self._anchor_table_room_id = room.id
        world_x = sum(point[0] for point in room.points) / len(room.points)
        world_y = sum(point[1] for point in room.points) / len(room.points)
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
        self._canvas.add_or_move_anchor_at(world_x, world_y)
        self._anchor_layout_commit_pending = True
        self._vm.geofence_repo.set_anchors(self._annotate_anchor_membership(self._canvas.anchor_layout_for_device()))
        self._refresh_anchor_status_label()

    def _remove_selected_anchor(self):
        if self._canvas.delete_selected_anchor():
            self._anchor_layout_commit_pending = True
            if self._vm:
                self._vm.geofence_repo.set_anchors(self._annotate_anchor_membership(self._canvas.anchor_layout_for_device()))
            self._clear_anchor_property_fields()
            self._refresh_anchor_status_label()

    def _read_layout_from_device(self):
        if not self._vm:
            QMessageBox.warning(self, "No Connection", "ViewModel not initialized.")
            return
        room_id = self._layout_scope_room_id(require=True)
        if not room_id:
            return
        target = self._selected_device_target()
        self._pending_layout_read_for_editor = bool(getattr(self._canvas, "dim_tracking_view", False))
        self._pending_layout_read_room_id = room_id
        self._vm._send_command(
            "anchor_layout_get",
            dst_addr=self._coerce_int_id(target.get("proto_dst_addr"), int(VvAddress.MCU)),
        )
        QMessageBox.information(self, "Read Layout", f"Sent layout query to MCU for {self._room_name(room_id)}.")

    def _write_layout_to_device(self):
        if not self._vm:
            QMessageBox.warning(self, "No Connection", "ViewModel not initialized.")
            return
        room_id = self._layout_scope_room_id(require=True)
        if not room_id:
            return
        layout = self._anchor_layout_for_room(room_id)
        if not layout:
            QMessageBox.warning(self, "No Anchors", f"No anchors found in {self._room_name(room_id)}.")
            return
        errors, warnings = self._validate_anchor_layout(layout, min_anchors=3)
        if errors:
            QMessageBox.warning(self, "Invalid Anchor Layout", "\n".join(errors))
            return

        anchors_payload = []
        for a in layout:
            anchors_payload.append({
                "anchor_id": self._coerce_int_id(a["anchor_id"], 0),
                "x_m": float(a.get("local_x_m", a.get("x_m", 0.0))),
                "y_m": float(a.get("local_y_m", a.get("y_m", 0.0))),
                "z_m": float(a["z_m"]),
            })

        target = self._selected_device_target()
        self._vm._send_command(
            "anchor_layout_set",
            dst_addr=self._coerce_int_id(target.get("proto_dst_addr"), int(VvAddress.MCU)),
            anchors=anchors_payload,
        )
        warning_text = ("\n" + "\n".join(warnings)) if warnings else ""
        QMessageBox.information(
            self,
            "Write Layout",
            f"Sent {self._room_name(room_id)} local layout with {len(layout)} anchors to MCU.{warning_text}",
        )

    def _update_device_targets(self, devices):
        combo = getattr(self.geofence_editor_widget, "cmb_device_target", None)
        if combo is None:
            return
        selected = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(
            "Tag 0x0001 / MCU (0x01)",
            {
                "proto_dst_addr": int(VvAddress.MCU),
                "device_id": 1,
                "role": "tag",
            },
        )
        seen = {1}
        for device in devices or []:
            role = str(device.get("role", "")).lower()
            device_type = str(device.get("device_type", device.get("type", ""))).lower()
            if role != "tag" and device_type not in {"1", "tag", "uwb_tag"}:
                continue
            device_id = self._coerce_int_id(device.get("device_id") or device.get("serial_number"), 0)
            if device_id <= 0 or device_id in seen:
                continue
            combo.addItem(
                f"Tag 0x{device_id:04x} / MCU (0x01)",
                {
                    "proto_dst_addr": int(VvAddress.MCU),
                    "device_id": device_id,
                    "role": "tag",
                    "device_type": device_type,
                },
            )
            seen.add(device_id)
        if isinstance(selected, dict):
            selected_device_id = self._coerce_int_id(selected.get("device_id"), 1)
            for index in range(combo.count()):
                data = combo.itemData(index)
                if isinstance(data, dict) and self._coerce_int_id(data.get("device_id"), 1) == selected_device_id:
                    combo.setCurrentIndex(index)
                    break
        combo.blockSignals(False)
    def _enter_geofence_editor(self):
        self._canvas.dim_tracking_view = True
        self._anchor_layout_commit_pending = False
        self._pending_layout_read_for_editor = False
        self.user_map_groupbox.setVisible(False)
        self.sidebar_stack.setCurrentIndex(1)
        self.canvas_header.setText("Geofencing Map Editor")

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
        self.user_map_groupbox.setVisible(False)
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
        self.geofence_editor_widget.editor_tabs.updateGeometry()

    def _set_anchor_authoring_visible(self, visible: bool):
        for name in (
            "btn_create_default_anchors",
            "btn_add_anchor",
            "btn_assign_anchor",
            "btn_remove_anchor",
            "lbl_anchor_status",
        ):
            widget = getattr(self.geofence_editor_widget, name, None)
            if widget is not None:
                widget.setVisible(visible)
        if hasattr(self, "_anchor_layout_group"):
            self._anchor_layout_group.setVisible(visible)

    def _set_editor_tool(self, object_type: str, mode: str):
        is_anchor = object_type == "anchor"
        self.geofence_editor_widget.gb_map_properties.setVisible(is_anchor)
        self._set_anchor_authoring_visible(is_anchor)
        if hasattr(self, "_properties_scroll"):
            if object_type in {"room", "wall", "object"}:
                if object_type == "wall":
                    self._properties_panel.set_rooms_list(self._room_zones())
                self._properties_panel.load_draft(object_type)
                self._properties_scroll.show()
            else:
                self._properties_scroll.hide()
        self._canvas.set_draw_object_type(object_type)
        target_tab = 1 if object_type == "zone" else 0
        if self.geofence_editor_widget.editor_tabs.currentIndex() != target_tab:
            self.geofence_editor_widget.editor_tabs.blockSignals(True)
            self.geofence_editor_widget.editor_tabs.setCurrentIndex(target_tab)
            self.geofence_editor_widget.editor_tabs.blockSignals(False)
            self.geofence_editor_widget.editor_tabs.updateGeometry()
        if object_type in {"room", "wall", "object", "anchor"}:
            idx = self.geofence_editor_widget.cmb_map_type.findText(object_type.title())
            if idx >= 0 and self.geofence_editor_widget.cmb_map_type.currentIndex() != idx:
                self.geofence_editor_widget.cmb_map_type.blockSignals(True)
                self.geofence_editor_widget.cmb_map_type.setCurrentIndex(idx)
                self.geofence_editor_widget.cmb_map_type.blockSignals(False)
            self._refresh_wall_host_rooms()
            self._sync_map_height_visibility()
            self._update_geometry_inspector()
        if object_type == "object":
            self._sync_object_drawing_options()
        if object_type == "anchor":
            if self._canvas.selected_anchor_idx is None:
                self._clear_anchor_property_fields()
            else:
                self._on_canvas_anchor_selected(self._canvas.selected_anchor_idx)
        if hasattr(self, "_anchor_layout_group"):
            self._anchor_layout_group.setVisible(object_type == "anchor")
            if object_type == "anchor":
                self._refresh_active_rooms()
        self._set_editor_mode(mode)

    def _set_editor_mode(self, mode):
        self._canvas.set_edit_mode(mode)
        draw_type = self._canvas.draw_object_type
        is_draw = mode == "draw"
        is_edit = mode == "edit_vertices"
        is_map_tab = self.geofence_editor_widget.editor_tabs.currentIndex() == 0
        self.geofence_editor_widget.btn_mode_room.setChecked(is_draw and draw_type == "room")
        self.geofence_editor_widget.btn_mode_wall.setChecked(is_draw and draw_type == "wall")
        self.geofence_editor_widget.btn_mode_object.setChecked(is_draw and draw_type == "object")
        self.geofence_editor_widget.btn_mode_anchor.setChecked(draw_type == "anchor" and mode in {"draw", "pick_zone"})
        self.geofence_editor_widget.btn_mode_draw.setChecked(is_draw and draw_type == "zone")
        self.geofence_editor_widget.btn_mode_edit_map.setChecked(is_edit and is_map_tab)
        self.geofence_editor_widget.btn_mode_edit.setChecked(is_edit and not is_map_tab)
        if is_edit and draw_type == "anchor":
            self.geofence_editor_widget.gb_map_properties.hide()
            self._set_anchor_authoring_visible(False)

    def _sync_map_height_visibility(self, *_args):
        object_type = self.geofence_editor_widget.cmb_map_type.currentText().strip().lower()
        is_wall = object_type == "wall"
        is_object = object_type == "object"
        is_room = object_type == "room"
        is_anchor = object_type == "anchor"
        self.geofence_editor_widget.lbl_map_height.setText("Height:")
        self.geofence_editor_widget.sb_map_height.setMinimum(0.1)
        self.geofence_editor_widget.lbl_map_height.setVisible(is_wall or is_object)
        self.geofence_editor_widget.sb_map_height.setVisible(is_wall or is_object)
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
                widget.setVisible(
                    is_anchor
                    and name not in {"lbl_scanned_device", "cmb_scanned_anchors"}
                )
        if hasattr(self, "_anchor_layout_group"):
            self._anchor_layout_group.setVisible(is_anchor)
            if is_anchor:
                self._refresh_active_rooms()

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
                id=zone_id, name=f"Rule Zone {number}", zone_type=zone_type, points=points,
                min_z=0.0, max_z=0.0,
                speed_limit=self.geofence_editor_widget.sb_speed.value() if zone_type == "allowed" else 0.0,
                color=self._rule_color_combo.currentData() or ("#22C55E" if zone_type == "allowed" else "#EF4444"),
                object_type="zone",
            )
        else:
            number = sum(1 for obj in objects if getattr(obj, "object_type", "zone") == object_type) + 1
            draft_name = self._properties_panel.txt_name.text().strip() if hasattr(self, "_properties_panel") else ""
            draft_color = self._properties_panel.cmb_color.currentData() if hasattr(self, "_properties_panel") else None
            if object_type == "object":
                subtype = self._properties_panel.cmb_object_kind.currentData() or "generic"
                direction = self._properties_panel.cmb_object_direction.currentData() or "up"
                height = max(0.1, self._properties_panel.sb_height.value())
                center_x = sum(point[0] for point in points) / len(points)
                center_y = sum(point[1] for point in points) / len(points)
                radius = sum(math.hypot(point[0] - center_x, point[1] - center_y) for point in points) / len(points)
                new_zone = GeofenceZone(
                    id=zone_id, name=draft_name or (f"Stairs {number}" if subtype == "stairs" else f"Object {number}"),
                    zone_type="object", points=points, min_z=0.0, max_z=height, speed_limit=0.0,
                    color=draft_color or "#F59E0B", object_type="object",
                    shape_kind=self._canvas.draw_object_shape, object_subtype=subtype,
                    object_direction=direction, radius_m=radius if self._canvas.draw_object_shape == "circle" else 0.0,
                )
            else:
                height = self.geofence_editor_widget.sb_map_height.value() if object_type == "wall" else 0.0
                wall_points = list(points)
                wall_mode = "free_standing"
                host_room_id = None
                if object_type == "wall":
                    host_room, snapped_points = self._detect_boundary_wall_match(points)
                    if host_room is not None:
                        wall_points = snapped_points
                        wall_mode = "boundary_outside"
                        host_room_id = host_room.id
                thickness = 0.2
                if object_type == "wall" and hasattr(self, "_properties_panel"):
                    thickness_spin = getattr(self._properties_panel, "sb_thickness", None)
                    if thickness_spin is not None:
                        thickness = max(0.01, float(thickness_spin.value()))
                new_zone = GeofenceZone(
                    id=zone_id, name=draft_name or f"{object_type.title()} {number}",
                    zone_type=object_type, points=wall_points, min_z=0.0, max_z=height, speed_limit=0.0,
                    color=draft_color or ("#F8FAFC" if object_type == "room" else "#0F172A"),
                    object_type=object_type,
                    shape_kind="polygon",
                    thickness=thickness if object_type == "wall" else 0.2,
                    wall_mode=wall_mode,
                    host_room_id=host_room_id,
                )

        self._canvas._push_undo_state()
        self._vm.add_geofence_zone(new_zone)
        self._canvas.set_geofences(self._vm.get_geofence_zones())
        self._canvas.set_selected_zone(zone_id)
        self._load_zone_properties_to_ui(new_zone)
        if object_type == "room":
            self._refresh_anchor_membership_from_canvas()
            self._refresh_active_rooms()
            QMessageBox.information(
                self,
                "Room Needs Anchors",
                "Room created. Open the Anchor tool and place at least 3 anchors inside this room.",
            )

    def _load_zone_properties_to_ui(self, zone):
        object_type = getattr(zone, "object_type", "zone")
        if object_type != "zone" and hasattr(self, "_properties_panel"):
            if object_type == "wall":
                self._properties_panel.set_rooms_list(self._room_zones())
            self._properties_panel.load_zone(zone)
            self.geofence_editor_widget.editor_tabs.setCurrentIndex(0)
            self.geofence_editor_widget.gb_map_properties.hide()
            self._set_anchor_authoring_visible(False)
            self._properties_scroll.show()
        if object_type == "zone":
            self.geofence_editor_widget.editor_tabs.setCurrentIndex(1)
            self.geofence_editor_widget.txt_zone_name.setText(zone.name)
            self.geofence_editor_widget.cmb_zone_type.setCurrentIndex(0 if zone.zone_type == "allowed" else 1)
            self.geofence_editor_widget.sb_speed.setValue(zone.speed_limit if zone.zone_type == "allowed" else 0.0)
            self.geofence_editor_widget.lbl_prop_speed.setVisible(zone.zone_type == "allowed")
            self.geofence_editor_widget.sb_speed.setVisible(zone.zone_type == "allowed")
            self._refresh_rule_zone_colors(selected_color=zone.color)
            self._canvas.set_draw_object_type("zone")
        else:
            self.geofence_editor_widget.editor_tabs.setCurrentIndex(0)
            self.geofence_editor_widget.txt_map_name.setText(zone.name)
            if object_type == "room":
                type_index = self.geofence_editor_widget.cmb_map_type.findText("Room", Qt.MatchFlag.MatchStartsWith)
            elif object_type == "wall":
                type_index = self.geofence_editor_widget.cmb_map_type.findText("Wall", Qt.MatchFlag.MatchStartsWith)
            else:
                type_index = self.geofence_editor_widget.cmb_map_type.findText("Object", Qt.MatchFlag.MatchStartsWith)
            if type_index >= 0:
                self.geofence_editor_widget.cmb_map_type.setCurrentIndex(type_index)
            if object_type in {"wall", "object"}:
                self.geofence_editor_widget.sb_map_height.setValue(max(0.1, zone.max_z - zone.min_z))
            self._canvas.set_draw_object_type(object_type)
            if object_type == "object":
                self._canvas.set_draw_object_shape(getattr(zone, "shape_kind", "polygon"))
            self._refresh_wall_host_rooms(getattr(zone, "host_room_id", None))
            self._sync_map_height_visibility()
            self._update_geometry_inspector(zone)
            if object_type == "room":
                self._anchor_table_room_id = zone.id
                self._refresh_active_rooms()
                self._refresh_anchor_layout_table()
        self._set_editor_mode("edit_vertices")
    def _apply_map_properties_from_enter(self):
        """Apply the visible map-object/anchor editor exactly like the Update button."""
        if not self.geofence_editor_widget.gb_map_properties.isVisible():
            return
        self._apply_map_properties()

    def _apply_map_properties(self):
        if self._canvas.selected_anchor_idx is not None:
            name = self.geofence_editor_widget.txt_map_name.text().strip()
            anchor_id = None
            
            if name.lower().startswith("a") and name[1:].isdigit():
                anchor_id = self._coerce_int_id(name[1:], 0)
            elif name.isdigit():
                anchor_id = self._coerce_int_id(name, 0)
            if anchor_id is not None:
                for idx, anchor in enumerate(self._canvas.anchors):
                    if idx != self._canvas.selected_anchor_idx and self._coerce_int_id(anchor.get("anchor_id"), -1) == anchor_id:
                        QMessageBox.warning(self, "Duplicate Anchor", f"A{anchor_id} is already placed on the map.")
                        return

            anchor = self._canvas.anchors[self._canvas.selected_anchor_idx]
            room = self._find_room(anchor.get("room_id", anchor.get("zone_id", "")))
            input_x = self.geofence_editor_widget.sb_anchor_x.value()
            input_y = self.geofence_editor_widget.sb_anchor_y.value()
            if room is not None:
                scene_x, scene_y = self._room_local_to_scene(input_x, input_y, room)
                anchor["local_x_m"] = input_x
                anchor["local_y_m"] = input_y
            else:
                scene_x, scene_y = input_x, input_y
            self._canvas.update_selected_anchor(
                anchor_id=anchor_id,
                label=name or None,
                x=scene_x,
                y=scene_y,
                z=self.geofence_editor_widget.sb_anchor_z.value(),
                role="anchor",
                device_type="uwb_anchor",
            )
            self._anchor_layout_commit_pending = True
            self._refresh_anchor_status_label()
            return

        selected_id = self._canvas.selected_zone_id
        if not selected_id or not self._vm:
            QMessageBox.warning(self, "No Selection", "Select a room, wall, or object on the map first.")
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
        if selected_type.startswith("wall"):
            object_type = "wall"
        elif selected_type.startswith("object"):
            object_type = "object"
        else:
            object_type = "room"

        zone.object_type = object_type
        zone.zone_type = object_type
        zone.name = self.geofence_editor_widget.txt_map_name.text().strip() or object_type.title()
        zone.min_z = 0.0
        zone.max_z = self.geofence_editor_widget.sb_map_height.value() if object_type in {"wall", "object"} else 0.0
        zone.speed_limit = 0.0

        panel = getattr(self, "_properties_panel", None)
        color_value = None
        if panel is not None and hasattr(panel, "cmb_color") and panel.cmb_color.count() > 0:
            color_value = panel.cmb_color.currentData()
        if not color_value:
            color_value = getattr(zone, "color", None)
        if object_type == "room":
            zone.color = color_value or "#F8FAFC"
            zone.wall_mode = "free_standing"
            zone.host_room_id = None
            zone.shape_kind = "polygon"
            zone.object_subtype = "generic"
            zone.object_direction = "up"
            zone.radius_m = 0.0
        elif object_type == "wall":
            thickness_val = float(getattr(zone, "thickness", 0.2))
            if panel is not None and hasattr(panel, "sb_thickness"):
                thickness_val = max(0.01, float(panel.sb_thickness.value()))
            wall_mode_val = getattr(zone, "wall_mode", "free_standing")
            if panel is not None and hasattr(panel, "cmb_wall_mode"):
                wall_mode_val = panel.cmb_wall_mode.currentData() or wall_mode_val
            host_room_val = getattr(zone, "host_room_id", None)
            if panel is not None and hasattr(panel, "cmb_wall_host_room"):
                host_room_val = panel.cmb_wall_host_room.currentData() or None
            zone.color = color_value or "#0F172A"
            zone.thickness = thickness_val
            zone.wall_mode = wall_mode_val
            zone.host_room_id = host_room_val
            zone.shape_kind = "polygon"
            zone.object_subtype = "generic"
            zone.object_direction = "up"
            zone.radius_m = 0.0
        else:
            shape_kind_val = getattr(zone, "shape_kind", "polygon")
            if panel is not None and hasattr(panel, "cmb_object_shape"):
                shape_kind_val = panel.cmb_object_shape.currentData() or shape_kind_val
            object_subtype_val = getattr(zone, "object_subtype", "generic")
            if panel is not None and hasattr(panel, "cmb_object_kind"):
                object_subtype_val = panel.cmb_object_kind.currentData() or object_subtype_val
            object_direction_val = getattr(zone, "object_direction", "up")
            if panel is not None and hasattr(panel, "cmb_object_direction"):
                object_direction_val = panel.cmb_object_direction.currentData() or object_direction_val
            if object_subtype_val == "stairs":
                shape_kind_val = "polygon"
            zone.color = color_value or "#F59E0B"
            zone.wall_mode = "free_standing"
            zone.host_room_id = None
            zone.shape_kind = shape_kind_val
            zone.object_subtype = object_subtype_val
            zone.object_direction = object_direction_val
            if shape_kind_val == "circle":
                points = list(getattr(zone, "points", []) or [])
                if len(points) >= 3:
                    center_x = sum(point[0] for point in points) / len(points)
                    center_y = sum(point[1] for point in points) / len(points)
                    zone.radius_m = max(0.01, sum(math.hypot(point[0] - center_x, point[1] - center_y) for point in points) / len(points))
                else:
                    zone.radius_m = max(0.01, float(getattr(zone, "radius_m", 0.0)) or 0.5)
            else:
                zone.radius_m = 0.0

        self._vm.geofence_layout_updated.emit(self._vm.get_geofence_zones())
        if hasattr(self, "_properties_panel"):
            if object_type == "wall":
                self._properties_panel.set_rooms_list(self._room_zones())
            self._properties_panel.load_zone(zone)
        self._canvas.update()
        self._update_geometry_inspector(zone)
        if object_type == "room":
            self._refresh_anchor_membership_from_canvas()
            self._refresh_active_rooms()
            if not self._room_has_anchor_layout(zone.id):
                QMessageBox.information(
                    self,
                    "Room Needs Anchors",
                    "This room has no complete anchor layout. Open the Anchor tool and place at least 3 anchors inside it.",
                )
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
        zone.speed_limit = self.geofence_editor_widget.sb_speed.value() if zone.zone_type == "allowed" else 0.0
        zone.color = self._rule_color_combo.currentData() or (
            "#22C55E" if zone.zone_type == "allowed" else "#EF4444"
        )
        zone.object_type = "zone"
        self._vm.geofence_layout_updated.emit(self._vm.get_geofence_zones())
        self._canvas.update()

    def _delete_selected_zone(self):
        if self._canvas.selected_anchor_idx is not None:
            return

        if not self._vm:
            return
        selected_ids = list(getattr(self._canvas, "selected_zone_ids", set()) or [])
        if not selected_ids and self._canvas.selected_zone_id:
            selected_ids = [self._canvas.selected_zone_id]
        if not selected_ids:
            QMessageBox.warning(self, "No Selection", "Select an object on the map first.")
            return

        zones = self._vm.get_geofence_zones()
        selected_set = set(selected_ids)
        deleted_zones = [z for z in zones if z.id in selected_set]
        if not deleted_zones:
            return

        self._canvas._push_undo_state()
        deleted_room_ids = {z.id for z in deleted_zones if getattr(z, "object_type", "zone") == "room"}
        if deleted_room_ids:
            self._vm.set_active_room_ids([
                room_id for room_id in self._vm.get_active_room_ids()
                if room_id not in deleted_room_ids
            ])
        for zone_id in selected_ids:
            self._vm.remove_geofence_zone(zone_id)
        self._canvas.set_geofences(self._vm.get_geofence_zones())
        self._canvas.set_selected_zone(None)
        self.geofence_editor_widget.txt_zone_name.clear()
        self.geofence_editor_widget.txt_map_name.clear()
        if deleted_room_ids:
            self._refresh_anchor_membership_from_canvas()
            self._refresh_active_rooms()

    def _cut_selected_object(self):
        self._copy_selected_object()
        self._delete_selected_zone()

    def _copy_selected_object(self):
        if not self._vm:
            return
        if self._canvas.selected_anchor_idx is not None and 0 <= self._canvas.selected_anchor_idx < len(self._canvas.anchors):
            anchor = self._canvas.anchors[self._canvas.selected_anchor_idx]
            self._clipboard = {"type": "anchor", "data": dict(anchor)}
            return

        zones = self._vm.get_geofence_zones()
        selected_ids = list(getattr(self._canvas, "selected_zone_ids", set()) or [])
        if not selected_ids and self._canvas.selected_zone_id:
            selected_ids = [self._canvas.selected_zone_id]
        if not selected_ids:
            return
        selected_set = set(selected_ids)
        copied = [zone.to_dict() for zone in zones if zone.id in selected_set]
        if len(copied) == 1:
            self._clipboard = {"type": "zone", "data": copied[0]}
        elif copied:
            self._clipboard = {"type": "zones", "data": copied}

    def _paste_object(self):
        if not self._vm or not self._clipboard:
            return

        mx, my = (0.0, 0.0)
        if hasattr(self._canvas, "mouse_world_pos") and self._canvas.mouse_world_pos:
            mx, my = self._canvas.mouse_world_pos

        clip_type = self._clipboard.get("type")
        clip_data = self._clipboard.get("data")
        if not clip_data:
            return

        if clip_type == "anchor":
            if self._canvas.draw_object_type == "anchor":
                return
            used_ids = {anchor.get("anchor_id") for anchor in self._canvas.anchors}
            anchor_id = clip_data.get("anchor_id", 0)
            while anchor_id in used_ids:
                anchor_id += 1

            new_anchor = dict(clip_data)
            new_anchor["anchor_id"] = anchor_id
            new_anchor["label"] = f"A{anchor_id}"
            new_anchor["x"] = mx
            new_anchor["y"] = my
            new_anchor["room_id"] = ""
            new_anchor["zone_id"] = ""
            new_anchor.pop("local_x_m", None)
            new_anchor.pop("local_y_m", None)
            new_anchor.pop("x_m", None)
            new_anchor.pop("y_m", None)

            annotated = self._annotate_anchor_membership([new_anchor])
            if annotated:
                self._canvas._push_undo_state()
                self._canvas.anchors.append(annotated[0])
                self._canvas.set_selected_anchor(len(self._canvas.anchors) - 1)
                self._on_canvas_anchor_layout_edited(self._canvas.anchors)
                self._canvas.update()
            return

        zone_payloads = [clip_data] if clip_type == "zone" else list(clip_data) if clip_type == "zones" else []
        if not zone_payloads:
            return

        all_points = []
        for payload in zone_payloads:
            for point in payload.get("points", []):
                all_points.append((float(point["x"]), float(point["y"])))
        if all_points:
            cx = sum(point[0] for point in all_points) / len(all_points)
            cy = sum(point[1] for point in all_points) / len(all_points)
        else:
            cx, cy = mx, my
        dx = mx - cx
        dy = my - cy

        self._canvas._push_undo_state()
        new_ids = []
        new_zones = []
        for payload in zone_payloads:
            copied_data = dict(payload)
            zone_id = str(uuid.uuid4())[:8]
            points_list = copied_data.get("points", [])
            if points_list:
                new_pts = [
                    (float(point["x"]) + dx, float(point["y"]) + dy)
                    for point in points_list
                ]
                copied_data["points"] = [{"x": point[0], "y": point[1]} for point in new_pts]
            copied_data["id"] = zone_id
            name = copied_data.get("name", "Object")
            if not name.endswith("(Copy)"):
                copied_data["name"] = f"{name} (Copy)"
            new_zone = GeofenceZone.from_dict(copied_data)
            self._vm.add_geofence_zone(new_zone)
            new_ids.append(zone_id)
            new_zones.append(new_zone)

        self._canvas.set_geofences(self._vm.get_geofence_zones())
        self._canvas.set_selected_zones(new_ids, primary_id=(new_ids[-1] if new_ids else None))
        if new_zones:
            self._load_zone_properties_to_ui(new_zones[-1])
        if any(getattr(zone, "object_type", "zone") == "room" for zone in new_zones):
            self._refresh_anchor_membership_from_canvas()
            self._refresh_active_rooms()
        self._canvas.update()
    def _on_canvas_zone_selected(self, zone_id):
        if not zone_id:
            self.geofence_editor_widget.txt_zone_name.clear()
            if self._is_anchor_tool_active():
                self._clear_anchor_property_fields()
            else:
                self.geofence_editor_widget.txt_map_name.clear()
            return
        if not self._vm:
            return
        zones = self._vm.get_geofence_zones()
        zone = next((z for z in zones if z.id == zone_id), None)
        if not zone:
            return

        if self._canvas.draw_object_type == "anchor":
            if getattr(zone, "object_type", "zone") == "room":
                self._anchor_table_room_id = zone.id
                self._canvas.set_selected_zone(zone.id)
                self._canvas.selected_anchor_idx = None
                self._set_anchor_authoring_visible(True)
                if hasattr(self, "_properties_scroll"):
                    self._properties_scroll.hide()
                self.geofence_editor_widget.gb_map_properties.show()
                self._clear_anchor_property_fields()
                self._refresh_active_rooms()
                self._refresh_anchor_layout_table()
            return

        self._load_zone_properties_to_ui(zone)

    def _on_canvas_anchor_selected(self, anchor_idx):
        if anchor_idx is None or anchor_idx < 0 or anchor_idx >= len(self._canvas.anchors):
            if self._is_anchor_tool_active():
                self._clear_anchor_property_fields()
            return
        anchor = self._canvas.anchors[anchor_idx]
        self.geofence_editor_widget.editor_tabs.setCurrentIndex(0)
        if hasattr(self, "_properties_scroll"):
            self._properties_scroll.hide()
        self.geofence_editor_widget.gb_map_properties.show()
        self._set_anchor_authoring_visible(True)
        self.geofence_editor_widget.txt_map_name.setText(anchor.get("label", f"A{anchor.get('anchor_id', anchor_idx)}"))
        anchor_type_idx = self.geofence_editor_widget.cmb_map_type.findText("Anchor")
        if anchor_type_idx >= 0:
            self.geofence_editor_widget.cmb_map_type.setCurrentIndex(anchor_type_idx)
        if hasattr(self.geofence_editor_widget, "sb_anchor_x"):
            value_x, value_y, value_z, is_local = self._anchor_local_xyz(anchor)
            if is_local:
                self.geofence_editor_widget.lbl_anchor_x.setText("Local X:")
                self.geofence_editor_widget.lbl_anchor_y.setText("Local Y:")
                self.geofence_editor_widget.sb_anchor_x.setToolTip("Local X in the selected room, relative to its Local (0,0).")
                self.geofence_editor_widget.sb_anchor_y.setToolTip("Local Y in the selected room, relative to its Local (0,0).")
            else:
                self.geofence_editor_widget.lbl_anchor_x.setText("Global X:")
                self.geofence_editor_widget.lbl_anchor_y.setText("Global Y:")
                self.geofence_editor_widget.sb_anchor_x.setToolTip("Global map X because this anchor is not assigned to a room.")
                self.geofence_editor_widget.sb_anchor_y.setToolTip("Global map Y because this anchor is not assigned to a room.")
            self.geofence_editor_widget.lbl_anchor_z.setText("Z:")
            self.geofence_editor_widget.sb_anchor_z.setToolTip("Anchor height Z in meters.")
            self._set_anchor_property_enabled(True)
            self.geofence_editor_widget.sb_anchor_x.setValue(value_x)
            self.geofence_editor_widget.sb_anchor_y.setValue(value_y)
            self.geofence_editor_widget.sb_anchor_z.setValue(value_z)


        self._sync_map_height_visibility()
        self._refresh_anchor_status_label()

    def _on_canvas_anchor_layout_edited(self, anchors):
        if not self._vm:
            self._map_3d.set_anchors(anchors)
            self._refresh_anchor_layout_table()
            self._refresh_active_rooms()
            return
        self._refresh_anchor_membership_from_canvas()
        self._map_3d.set_anchors(self._canvas.anchors)
        self._refresh_anchor_layout_table()
        self._refresh_active_rooms()
    def _schedule_geofence_layout_emit(self):
        if not self._vm:
            return
        if not hasattr(self, "_layout_emit_timer"):
            self._vm.geofence_layout_updated.emit(self._vm.get_geofence_zones())
            return
        self._layout_emit_dirty = True
        if not self._layout_emit_timer.isActive():
            self._layout_emit_timer.start()

    def _flush_geofence_layout_emit(self):
        self._layout_emit_timer.stop()
        if not getattr(self, "_layout_emit_dirty", False) or not self._vm:
            return
        self._layout_emit_dirty = False
        self._vm.geofence_layout_updated.emit(self._vm.get_geofence_zones())
    def _on_canvas_zone_modified(self, zone_id, points):
        if not self._vm:
            return
        zones = self._vm.get_geofence_zones()
        zone = next((z for z in zones if z.id == zone_id), None)
        if zone:
            zone.points = points
            canvas_busy = bool(getattr(self._canvas, "_zone_drag_start_world", None) or self._canvas.selected_vertex_idx is not None or self._canvas.selected_edge_idx is not None)
            if not canvas_busy and hasattr(self, "_properties_panel") and self._canvas.selected_zone_id == zone_id:
                self._properties_panel.load_zone(zone)
            self._schedule_geofence_layout_emit()
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
                elif key == "speed_limit":
                    zone.speed_limit = float(val)
                elif key == "thickness":
                    zone.thickness = float(val)
                elif key == "shape_kind":
                    zone.shape_kind = str(val)
                    self._canvas.set_draw_object_shape(zone.shape_kind)
                elif key == "object_subtype":
                    zone.object_subtype = str(val)
                    if zone.object_subtype == "stairs":
                        zone.shape_kind = "polygon"
                elif key == "object_direction":
                    zone.object_direction = str(val)
                elif key == "wall_mode":
                    zone.wall_mode = str(val)
                elif key == "host_room_id":
                    zone.host_room_id = str(val) if val else None
                elif key == "label_offset_x":
                    zone.label_offset_x = float(val)
                elif key == "label_offset_y":
                    zone.label_offset_y = float(val)
            
            # Update sidebar fields if it's currently selected zone
            if self._canvas.selected_zone_id == zone_id:
                self.geofence_editor_widget.blockSignals(True)
                if hasattr(self, "_properties_panel"):
                    self._properties_panel.load_zone(zone)
                self.geofence_editor_widget.blockSignals(False)
                
            self._vm.geofence_layout_updated.emit(self._vm.get_geofence_zones())
            self._update_geometry_inspector(zone)

    def _load_map(self):
        if not self._vm:
            return

        default_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "runtime"))
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
        self._canvas.clear_undo_history()
        self._geofence_anchor_baseline = [dict(anchor) for anchor in map_anchors]
        self._draft_anchor_layout = [dict(anchor) for anchor in map_anchors]
        self._anchor_layout_commit_pending = False
        self._refresh_map_list()
        self._refresh_anchor_status_label()
        self._refresh_active_rooms()
        QMessageBox.information(self, "Map Loaded", f"Loaded geofencing map:\n{os.path.basename(file_path)}")

    def _save_map(self):
        if not self._vm:
            return
        self._vm.geofence_repo.set_anchors(self._annotate_anchor_membership(self._canvas.anchor_layout_for_device()))
        errors, warnings = self._validate_geofence_map()
        if errors:
            QMessageBox.warning(self, "Invalid Map", "\n".join(errors))
            return

        default_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "runtime"))
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
            self._canvas._push_undo_state()
            self._vm.clear_geofence_zones()
            self._canvas.set_geofences([])
            self._canvas.set_anchors([])
            if getattr(self._canvas, "dim_tracking_view", False):
                self._vm.geofence_repo.set_anchors([])
                self._draft_anchor_layout = []
                self._anchor_layout_commit_pending = True
            self._canvas.set_selected_zone(None)
            self.geofence_editor_widget.txt_zone_name.clear()
            if self._is_anchor_tool_active():
                self._clear_anchor_property_fields()
            else:
                self.geofence_editor_widget.txt_map_name.clear()

    def set_developer_mode(self, is_developer: bool):
        self._is_developer_mode = is_developer
        self._canvas.is_developer_mode = is_developer
        self._canvas._show_scale_bar = is_developer
        self._canvas._show_mouse_coords = is_developer
        self._canvas._show_tracking_grid = not is_developer
        # Keep the editor canvas strictly 2D; 2.5D is shown only in the preview dialog.
        self._canvas.set_25d_preview(False)
        if is_developer:
            self.user_map_groupbox.setVisible(False)
            self.geofence_editor_widget.btn_exit_editor.setVisible(False)
            self._enter_geofence_editor()
        else:
            self.geofence_editor_widget.btn_exit_editor.setVisible(True)
            self.user_map_groupbox.setVisible(False)
            self._canvas.set_edit_mode("navigate")
            self._canvas.clear_active_drawing()
            self._canvas.set_selected_zone(None)
            self._canvas.set_selected_anchor(None)
            if self.sidebar_stack.currentIndex() == 1:
                self._exit_geofence_editor()
            elif self._vm:
                self._canvas.set_anchors(self._vm.current_anchor_layout)

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
            self.warning_label.setText(f"OVERSPEED IN {zone_name.upper()}! (Limit: {speed_limit:.1f} m/s)")
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

    def schedule_preview_pane_update(self):
        if not hasattr(self, "_preview_sync_timer"):
            return
        if not hasattr(self, "_map_view_stack") or self._map_view_stack.currentWidget() is not self._map_3d:
            return
        self._preview_sync_dirty = True
        if not self._preview_sync_timer.isActive():
            self._preview_sync_timer.start()

    def _flush_preview_pane_update(self):
        self._preview_sync_timer.stop()
        if not self._preview_sync_dirty:
            return
        self._preview_sync_dirty = False
        self.update_preview_pane()

    def update_preview_pane(self):
        if not hasattr(self, "_map_3d"):
            return
        self._map_3d.set_geofences(self._canvas.geofence_zones)
        self._map_3d.set_anchors(self._canvas.anchors)
