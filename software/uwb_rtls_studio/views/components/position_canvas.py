"""
===============================================================================
  UWB RTLS Studio - Position Canvas Component
===============================================================================
"""
import math
import time
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QPointF, QPoint
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
    QPolygonF,
    QPixmap,
    QCursor,
    QPolygon,
)
from PyQt6.QtWidgets import QWidget
from utils.config_dim import GRID_SPACING_M


class PositionCanvas(QWidget):
    """Interactive 2D position canvas used by the live tracking tab."""

    polygon_completed = pyqtSignal(list)  # list of (x, y) tuples
    zone_selected = pyqtSignal(str)       # zone_id
    zone_modified = pyqtSignal(str, list) # zone_id, list of (x, y) tuples

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 300)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.position = {"x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0, "error": 0.0}
        self.anchors = [
            {"x": 0.0, "y": 0.0, "label": "A0"},
            {"x": 9.76, "y": 0.0, "label": "A1"},
            {"x": 9.76, "y": 9.76, "label": "A2"},
            {"x": 0.0, "y": 9.76, "label": "A3"},
        ]
        self.history = []
        self.fusion_history = []
        self.max_history = 300

        self.last_update_time = 0.0
        self._last_update_by_source = {}
        self.update_interval = 0.05

        self._view_cx = 4.88
        self._view_cy = 4.88
        self._view_range = 14.0
        self._margin = 50

        self._dragging = False
        self._drag_start = None
        self._drag_view_cx = 0.0
        self._drag_view_cy = 0.0
        self._rect_zoom = False
        self._rect_start = None
        self._rect_end = None

        # Geofencing properties
        self.geofence_zones = []
        self.edit_mode = "navigate"  # "navigate" | "draw" | "edit_vertices" | "pick_zone"
        self.draw_object_type = "zone"  # "zone" | "room" | "wall"
        self.current_draw_points = []
        self.selected_zone_id = None
        self.selected_vertex_idx = None
        self.mouse_world_pos = (0.0, 0.0)
        self.dim_tracking_view = False

        # Grid & coordinate display settings
        self._grid_spacing = GRID_SPACING_M  # meters (configured in config.py)
        self._grid_subdivisions = 5
        self._show_scale_bar = True
        self._show_mouse_coords = True
        self.is_developer_mode = False
        self.snapped_grid_pt = None
        self.preview_25d = False

        QTimer.singleShot(50, self.auto_fit)

    def set_geofences(self, zones):
        self.geofence_zones = zones
        self.update()

    def set_edit_mode(self, mode):
        self.edit_mode = mode
        self.current_draw_points.clear()
        self.selected_vertex_idx = None
        if mode == "navigate":
            self.setCursor(Qt.CursorShape.ArrowCursor)
        elif mode == "draw":
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif mode == "edit_vertices":
            self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def set_selected_zone(self, zone_id):
        self.selected_zone_id = zone_id
        self.update()

    def set_grid_spacing(self, spacing_m: float):
        """Set grid line spacing in meters."""
        self.set_grid_settings(spacing_m, self._grid_subdivisions)
        self.update()

    def set_grid_settings(self, major_spacing_m: float, subdivisions: int):
        """Set major grid spacing and snap subdivisions."""
        self._grid_spacing = max(0.1, min(float(major_spacing_m), 10.0))
        self._grid_subdivisions = max(1, min(int(subdivisions), 20))
        self.update()

    def set_draw_object_type(self, object_type: str):
        if object_type not in {"zone", "room", "wall"}:
            object_type = "zone"
        if self.draw_object_type != object_type:
            self.current_draw_points.clear()
        self.draw_object_type = object_type
        self.update()

    def set_25d_preview(self, enabled: bool):
        self.preview_25d = bool(enabled)
        self.update()

    def clear_active_drawing(self):
        self.current_draw_points.clear()
        self.update()

    def _snap_step(self) -> float:
        return self._grid_spacing / max(self._grid_subdivisions, 1)

    def _snap_world_point(self, world_x: float, world_y: float):
        step = self._snap_step()
        if step <= 0:
            return world_x, world_y
        snapped_x = round(world_x / step) * step
        snapped_y = round(world_y / step) * step
        return round(snapped_x, 6), round(snapped_y, 6)

    def _is_close(self, world_pt, screen_x, screen_y, threshold_px=8):
        sx, sy = self._world_to_screen(world_pt[0], world_pt[1])
        return math.hypot(sx - screen_x, sy - screen_y) <= threshold_px

    def _is_inside_polygon(self, poly_points, wx, wy):
        poly = QPolygonF()
        for pt in poly_points:
            poly.append(QPointF(pt[0], pt[1]))
        return poly.containsPoint(QPointF(wx, wy), Qt.FillRule.OddEvenFill)

    def update_position(self, position):
        current_time = time.time()
        source = position.get("source", "ranging")
        last_update = self._last_update_by_source.get(source, 0.0)
        if current_time - last_update < self.update_interval:
            return

        self.last_update_time = current_time
        self._last_update_by_source[source] = current_time
        if source == "sensor_fusion":
            self.fusion_history.append((position["x"], position["y"]))
            if len(self.fusion_history) > self.max_history:
                self.fusion_history.pop(0)
            self.update()
            return

        self.position = position
        self.history.append((position["x"], position["y"]))
        if len(self.history) > self.max_history:
            self.history.pop(0)
        self.update()

    def set_anchors(self, anchors):
        self.anchors = anchors
        self.auto_fit()

    def clear_trail(self):
        self.history.clear()
        self.fusion_history.clear()
        self._last_update_by_source.clear()
        self.update()

    def auto_fit(self):
        pts_x = [a["x"] for a in self.anchors] + [self.position["x"]]
        pts_y = [a["y"] for a in self.anchors] + [self.position["y"]]
        for zone in self.geofence_zones:
            for point in zone.points:
                pts_x.append(point[0])
                pts_y.append(point[1])
        if not pts_x:
            return

        max_x = max(pts_x)
        max_y = max(pts_y)
        padding = 1.0

        need_x = max_x + 2 * padding
        need_y = max_y + 2 * padding

        margin = self._margin
        right_panel_width = 0
        if hasattr(self, "parent_tab") and getattr(self.parent_tab, "sidebar_expanded", False):
            right_panel_width = self.parent_tab.right_widget.width() or 380

        full_width = max(self.width() - 2 * margin, 1)
        full_height = max(self.height() - 2 * margin, 1)
        visible_width = max(full_width - right_panel_width, 1)

        self._view_range = max(
            need_x * min(full_width, full_height) / visible_width,
            need_y * min(full_width, full_height) / full_height,
            2.0,
        )

        scale = min(full_width, full_height) / self._view_range if self._view_range > 0 else 50
        self._view_cx = -padding + (full_width / scale) / 2.0
        self._view_cy = -padding + (full_height / scale) / 2.0
        self.update()

    def _world_to_screen(self, world_x, world_y):
        margin = self._margin
        width = self.width() - 2 * margin
        height = self.height() - 2 * margin
        scale = min(width, height) / self._view_range if self._view_range > 0 else 50
        screen_x = margin + (width / 2.0) + (world_x - self._view_cx) * scale
        screen_y = margin + (height / 2.0) - (world_y - self._view_cy) * scale
        return int(screen_x), int(screen_y)

    def _screen_to_world(self, screen_x, screen_y):
        margin = self._margin
        width = self.width() - 2 * margin
        height = self.height() - 2 * margin
        scale = min(width, height) / self._view_range if self._view_range > 0 else 50
        world_x = self._view_cx + (screen_x - margin - width / 2.0) / scale
        world_y = self._view_cy - (screen_y - margin - height / 2.0) / scale
        return world_x, world_y

    def wheelEvent(self, event):
        factor = 0.85 if event.angleDelta().y() > 0 else 1.18
        pos = event.position()
        world_x, world_y = self._screen_to_world(pos.x(), pos.y())
        self._view_range *= factor
        self._view_range = max(0.5, min(self._view_range, 200.0))
        world_x_2, world_y_2 = self._screen_to_world(pos.x(), pos.y())
        self._view_cx -= world_x_2 - world_x
        self._view_cy -= world_y_2 - world_y
        self.update()

    def mouseDoubleClickEvent(self, event):
        self.auto_fit()

    def mousePressEvent(self, event):
        pos = event.position()
        world_x, world_y = self._screen_to_world(pos.x(), pos.y())
        snapped_x, snapped_y = self._snap_world_point(world_x, world_y)

        if self.edit_mode == "draw" and event.button() == Qt.MouseButton.LeftButton:
            # Check if clicked near the first point to close the polygon
            if self.current_draw_points and self._is_close(self.current_draw_points[0], pos.x(), pos.y()):
                if len(self.current_draw_points) >= 3:
                    pts = list(self.current_draw_points)
                    self.current_draw_points.clear()
                    self.polygon_completed.emit(pts)
                self.update()
                return

            self.current_draw_points.append((snapped_x, snapped_y))
            self.update()
            return
        elif self.edit_mode == "draw" and event.button() == Qt.MouseButton.RightButton:
            # Cancel drawing
            self.current_draw_points.clear()
            self.update()
            return

        # Selection or vertex editing mode
        if self.edit_mode == "edit_vertices" and event.button() == Qt.MouseButton.LeftButton:
            # Check if clicked near any vertex of the selected zone (if selected)
            if self.selected_zone_id:
                sel_zone = next((z for z in self.geofence_zones if z.id == self.selected_zone_id), None)
                if sel_zone:
                    for idx, pt in enumerate(sel_zone.points):
                        if self._is_close(pt, pos.x(), pos.y()):
                            self.selected_vertex_idx = idx
                            self.setCursor(Qt.CursorShape.SizeAllCursor)
                            return

            # Check if clicked near any vertex of ANY zone
            for zone in self.geofence_zones:
                for idx, pt in enumerate(zone.points):
                    if self._is_close(pt, pos.x(), pos.y()):
                        self.selected_zone_id = zone.id
                        self.selected_vertex_idx = idx
                        self.zone_selected.emit(zone.id)
                        self.setCursor(Qt.CursorShape.SizeAllCursor)
                        self.update()
                        return

            # Check if clicked INSIDE any zone polygon
            for zone in self.geofence_zones:
                if self._is_inside_polygon(zone.points, world_x, world_y):
                    self.selected_zone_id = zone.id
                    self.zone_selected.emit(zone.id)
                    self.update()
                    return

            # Clicked empty space: clear selection
            if self.selected_zone_id:
                self.selected_zone_id = None
                self.zone_selected.emit("")
                self.update()

        # Fall back to standard dragging
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_start = event.position()
            self._drag_view_cx = self._view_cx
            self._drag_view_cy = self._view_cy
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif event.button() == Qt.MouseButton.RightButton:
            self._rect_zoom = True
            self._rect_start = event.position()
            self._rect_end = event.position()

    def mouseMoveEvent(self, event):
        pos = event.position()
        world_x, world_y = self._screen_to_world(pos.x(), pos.y())
        snapped_x, snapped_y = self._snap_world_point(world_x, world_y)
        if self.edit_mode in {"draw", "edit_vertices"}:
            self.mouse_world_pos = (snapped_x, snapped_y)
            self.snapped_grid_pt = (snapped_x, snapped_y)
        else:
            self.mouse_world_pos = (world_x, world_y)
            self.snapped_grid_pt = None

        # Handle vertex drag
        if self.edit_mode == "edit_vertices" and self.selected_vertex_idx is not None and self.selected_zone_id:
            sel_zone = next((z for z in self.geofence_zones if z.id == self.selected_zone_id), None)
            if sel_zone:
                sel_zone.points[self.selected_vertex_idx] = (snapped_x, snapped_y)
                self.zone_modified.emit(self.selected_zone_id, sel_zone.points)
                self.update()
                return

        # Hover cues for edit mode
        if self.edit_mode == "edit_vertices" and not self._dragging:
            over_vertex = False
            for zone in self.geofence_zones:
                for pt in zone.points:
                    if self._is_close(pt, pos.x(), pos.y()):
                        over_vertex = True
                        break
                if over_vertex:
                    break
            if over_vertex:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)

        if self._dragging and self._drag_start:
            dx = pos.x() - self._drag_start.x()
            dy = pos.y() - self._drag_start.y()
            margin = self._margin
            width = self.width() - 2 * margin
            height = self.height() - 2 * margin
            scale = min(width, height) / self._view_range if self._view_range > 0 else 50
            self._view_cx = self._drag_view_cx - dx / scale
            self._view_cy = self._drag_view_cy + dy / scale
            self.update()
        elif self._rect_zoom and self._rect_start:
            self._rect_end = event.position()
            self.update()
        elif self.edit_mode == "draw":
            self.update()

    def mouseReleaseEvent(self, event):
        if self.selected_vertex_idx is not None:
            self.selected_vertex_idx = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()
            return

        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
        elif event.button() == Qt.MouseButton.RightButton and self._rect_zoom:
            self._rect_zoom = False
            if self._rect_start and self._rect_end:
                x1 = self._rect_start.x()
                y1 = self._rect_start.y()
                x2 = self._rect_end.x()
                y2 = self._rect_end.y()
                if abs(x2 - x1) > 10 and abs(y2 - y1) > 10:
                    w1x, w1y = self._screen_to_world(min(x1, x2), max(y1, y2))
                    w2x, w2y = self._screen_to_world(max(x1, x2), min(y1, y2))
                    self._view_cx = (w1x + w2x) / 2.0
                    self._view_cy = (w1y + w2y) / 2.0
                    self._view_range = max(w2x - w1x, w2y - w1y) * 1.1
            self._rect_start = None
            self._rect_end = None
            self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._dragging and not self._rect_zoom:
            self.auto_fit()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor(30, 41, 59))

        margin = self._margin
        width = self.width() - 2 * margin
        height = self.height() - 2 * margin
        if width <= 0 or height <= 0:
            return

        to_screen = self._world_to_screen
        view_x1, view_y1 = self._screen_to_world(margin, self.height() - margin)
        view_x2, view_y2 = self._screen_to_world(margin + width, margin)

        # 1. Draw Fusion History Trail
        if len(self.fusion_history) > 1:
            painter.setPen(QPen(QColor(248, 113, 113, 140), 2, Qt.PenStyle.DashLine))
            for idx in range(len(self.fusion_history) - 1):
                x1, y1 = to_screen(self.fusion_history[idx][0], self.fusion_history[idx][1])
                x2, y2 = to_screen(self.fusion_history[idx + 1][0], self.fusion_history[idx + 1][1])
                painter.drawLine(x1, y1, x2, y2)

        # 2. Draw History Trail
        if len(self.history) > 1:
            painter.setPen(QPen(QColor(96, 165, 250, 120), 2))
            for idx in range(len(self.history) - 1):
                x1, y1 = to_screen(self.history[idx][0], self.history[idx][1])
                x2, y2 = to_screen(self.history[idx + 1][0], self.history[idx + 1][1])
                painter.drawLine(x1, y1, x2, y2)

        # 3. Draw Anchor Connections (from Tag to Anchors)
        pos_x, pos_y = to_screen(self.position["x"], self.position["y"])
        for anchor in self.anchors:
            anchor_x, anchor_y = to_screen(anchor["x"], anchor["y"])
            painter.setPen(QPen(QColor(99, 102, 241, 40), 1, Qt.PenStyle.DashLine))
            painter.drawLine(pos_x, pos_y, anchor_x, anchor_y)

        # 4. Draw Anchors
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        for anchor in self.anchors:
            center_x, center_y = to_screen(anchor["x"], anchor["y"])
            painter.setPen(QPen(QColor(99, 102, 241), 2))
            painter.setBrush(QColor(30, 41, 59))
            painter.drawEllipse(center_x - 10, center_y - 10, 20, 20)
            painter.setBrush(QColor(99, 102, 241))
            painter.drawEllipse(center_x - 4, center_y - 4, 8, 8)

            label = anchor.get("label", anchor.get("id", "?"))
            painter.setPen(QColor(226, 232, 240))
            painter.drawText(center_x + 16, center_y - 10, label)
            painter.setFont(QFont("Segoe UI", 8))
            painter.setPen(QColor(148, 163, 184))
            painter.drawText(center_x + 16, center_y + 4, f"({anchor['x']:.1f}, {anchor['y']:.1f})")
            painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))

        # 5. Draw Tag Position (Direction and Error Ellipse)
        scale_px = min(width, height) / self._view_range if self._view_range > 0 else 50
        if self.position.get("error", 0) > 0:
            error_radius = int(self.position["error"] * scale_px)
            painter.setPen(QPen(QColor(239, 68, 68, 60), 2, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(239, 68, 68, 20))
            painter.drawEllipse(pos_x - error_radius, pos_y - error_radius, error_radius * 2, error_radius * 2)

        painter.save()
        painter.translate(pos_x, pos_y)
        painter.rotate(-self.position.get("yaw", 0))
        painter.setPen(
            QPen(
                QColor(37, 99, 235),
                2,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        gradient = QLinearGradient(0, -12, 0, 10)
        gradient.setColorAt(0, QColor(96, 165, 250))
        gradient.setColorAt(1, QColor(37, 99, 235))
        painter.setBrush(gradient)
        path = QPainterPath()
        path.moveTo(14, 0)
        path.lineTo(-10, -9)
        path.lineTo(-4, 0)
        path.lineTo(-10, 9)
        path.closeSubpath()
        painter.drawPath(path)
        painter.setBrush(QColor(248, 113, 113, 150))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(-10, -3, 4, 6)
        painter.restore()

        # Tag glow effect
        glow_gradient = QRadialGradient(pos_x, pos_y, 18)
        glow_gradient.setColorAt(0, QColor(96, 165, 250, 60))
        glow_gradient.setColorAt(1, QColor(96, 165, 250, 0))
        painter.setBrush(glow_gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(pos_x - 18, pos_y - 18, 36, 36)

        # Tag coordinates text overlay
        coord_text = f"{self.position['x']:.2f}, {self.position['y']:.2f}"
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        text_rect = painter.fontMetrics().boundingRect(coord_text)
        text_rect.translate(pos_x + 15, pos_y + 15)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(15, 23, 42, 180))
        painter.drawRoundedRect(text_rect.adjusted(-4, -2, 4, 2), 4, 4)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(pos_x + 15, pos_y + 15 + text_rect.height() - 4, coord_text)

        # Draw rect zoom box if dragging right click
        if self._rect_zoom and self._rect_start and self._rect_end:
            rect_x = min(self._rect_start.x(), self._rect_end.x())
            rect_y = min(self._rect_start.y(), self._rect_end.y())
            rect_w = abs(self._rect_end.x() - self._rect_start.x())
            rect_h = abs(self._rect_end.y() - self._rect_start.y())
            painter.setPen(QPen(QColor(99, 102, 241), 2, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(99, 102, 241, 30))
            painter.drawRect(int(rect_x), int(rect_y), int(rect_w), int(rect_h))

        # --- GEOFENCING DRAWING LAYER ---
        
        # 6. Dimming overlay for locked tracking view (dev mode only)
        # 6. Dimming overlay and Grid Drawing (only visible when editor is active)
        if self.dim_tracking_view:
            painter.fillRect(self.rect(), QColor(15, 23, 42, 170))  # 65% opacity overlay

            # 7. Enhanced Grid Drawing (drawn on top of dimming overlay, white themed in editor mode)
            step = self._grid_spacing
            
            # Select grid colors based on mode (white theme in editor/dev mode)
            if self.is_developer_mode:
                minor_color = QColor(255, 255, 255, 30) # 12% white
                major_color = QColor(255, 255, 255, 80) # 31% white
                label_color = QColor(241, 245, 249)     # slate-100
            else:
                minor_color = QColor(255, 255, 255, 15) # 6% white
                major_color = QColor(255, 255, 255, 45) # 18% white
                label_color = QColor(148, 163, 184)     # slate-400

            scale_px = min(width, height) / self._view_range if self._view_range > 0 else 50
            minor_step = self._snap_step()
            if minor_step * scale_px >= 4:
                painter.setPen(QPen(minor_color, 1, Qt.PenStyle.DotLine))
                minor_x = math.floor(view_x1 / minor_step) * minor_step
                minor_guard = 0
                while minor_x <= view_x2 and minor_guard < 5000:
                    screen_x, _ = to_screen(minor_x, 0)
                    painter.drawLine(screen_x, margin, screen_x, self.height() - margin)
                    minor_x += minor_step
                    minor_guard += 1
                minor_y = math.floor(view_y1 / minor_step) * minor_step
                minor_guard = 0
                while minor_y <= view_y2 and minor_guard < 5000:
                    _, screen_y = to_screen(0, minor_y)
                    painter.drawLine(margin, screen_y, margin + width, screen_y)
                    minor_y += minor_step
                    minor_guard += 1

            # Major grid lines
            painter.setPen(QPen(major_color, 1, Qt.PenStyle.DotLine))
            grid_x = math.floor(view_x1 / step) * step
            grid_guard = 0
            while grid_x <= view_x2 and grid_guard < 2000:
                screen_x, _ = to_screen(grid_x, 0)
                painter.drawLine(screen_x, margin, screen_x, self.height() - margin)
                grid_x += step
                grid_guard += 1
            grid_y = math.floor(view_y1 / step) * step
            grid_guard = 0
            while grid_y <= view_y2 and grid_guard < 2000:
                _, screen_y = to_screen(0, grid_y)
                painter.drawLine(margin, screen_y, margin + width, screen_y)
                grid_y += step
                grid_guard += 1

            # Grid labels
            painter.setFont(QFont("Segoe UI", 9))
            painter.setPen(label_color)
            grid_x = math.floor(view_x1 / step) * step
            grid_guard = 0
            while grid_x <= view_x2 and grid_guard < 2000:
                screen_x, _ = to_screen(grid_x, 0)
                label = f"{grid_x:.2f}m" if step < 1.0 else f"{grid_x:.0f}m"
                painter.drawText(screen_x - 15, self.height() - margin + 16, label)
                grid_x += step
                grid_guard += 1
            grid_y = math.floor(view_y1 / step) * step
            grid_guard = 0
            while grid_y <= view_y2 and grid_guard < 2000:
                _, screen_y = to_screen(0, grid_y)
                label = f"{grid_y:.2f}m" if step < 1.0 else f"{grid_y:.0f}m"
                painter.drawText(4, screen_y + 4, label)
                grid_y += step
                grid_guard += 1

            if self.snapped_grid_pt and self.edit_mode in {"draw", "edit_vertices"}:
                snap_x, snap_y = to_screen(self.snapped_grid_pt[0], self.snapped_grid_pt[1])
                painter.setPen(QPen(QColor(34, 211, 238, 190), 1.5))
                painter.setBrush(QColor(34, 211, 238, 80))
                painter.drawEllipse(int(snap_x - 4), int(snap_y - 4), 8, 8)

        # 8. Draw map objects and rule zones
        layer_order = {"room": 0, "wall": 1, "zone": 2}
        sorted_zones = sorted(
            self.geofence_zones,
            key=lambda z: layer_order.get(getattr(z, "object_type", "zone"), 3),
        )

        def draw_extruded_polygon(poly, fill_color, border_color, height_m):
            height_offset = max(5, min(int(height_m * scale_px * 0.18), 42))
            dx = int(height_offset * 0.85)
            dy = -int(height_offset * 0.55)
            top_poly = QPolygonF()
            for p in poly:
                top_poly.append(QPointF(p.x() + dx, p.y() + dy))

            side_color = QColor(border_color)
            side_color.setAlpha(95)
            painter.setPen(QPen(border_color, 1))
            painter.setBrush(QBrush(side_color))
            for idx in range(poly.count()):
                nxt = (idx + 1) % poly.count()
                side = QPolygonF(
                    [
                        poly[idx],
                        poly[nxt],
                        QPointF(poly[nxt].x() + dx, poly[nxt].y() + dy),
                        QPointF(poly[idx].x() + dx, poly[idx].y() + dy),
                    ]
                )
                painter.drawPolygon(side)

            top_fill = QColor(fill_color)
            top_fill.setAlpha(max(fill_color.alpha(), 95))
            painter.setBrush(QBrush(top_fill))
            painter.setPen(QPen(border_color, 2))
            painter.drawPolygon(top_poly)
            return top_poly

        for zone in sorted_zones:
            poly = QPolygonF()
            for pt in zone.points:
                sx, sy = to_screen(pt[0], pt[1])
                poly.append(QPointF(sx, sy))

            object_type = getattr(zone, "object_type", "zone")
            if object_type == "room":
                fill_color = QColor(248, 250, 252, 58)
                border_color = QColor(226, 232, 240)
            elif object_type == "wall":
                fill_color = QColor(148, 163, 184, 120)
                border_color = QColor(100, 116, 139)
            elif zone.zone_type == "forbidden":
                fill_color = QColor(239, 68, 68, 55)
                border_color = QColor(239, 68, 68)
            else:
                fill_color = QColor(34, 197, 94, 55)
                border_color = QColor(34, 197, 94)

            is_selected = (zone.id == self.selected_zone_id)
            border_width = 3 if is_selected else 1.5
            pen_style = Qt.PenStyle.SolidLine if is_selected or object_type in {"room", "wall"} else Qt.PenStyle.DashLine

            if self.preview_25d and object_type in {"room", "wall"} and len(zone.points) >= 3:
                draw_extruded_polygon(poly, fill_color, border_color, max(0.1, zone.max_z - zone.min_z))
            else:
                painter.setPen(QPen(border_color, border_width, pen_style))
                painter.setBrush(QBrush(fill_color))
                painter.drawPolygon(poly)

            if len(zone.points) >= 3:
                cx = sum(p[0] for p in zone.points) / len(zone.points)
                cy = sum(p[1] for p in zone.points) / len(zone.points)
                scx, scy = to_screen(cx, cy)
                
                painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
                if object_type == "zone":
                    text = f"{zone.name} ({zone.speed_limit:.1f} m/s)"
                else:
                    text = f"{zone.name} ({max(0.1, zone.max_z - zone.min_z):.1f} m high)"
                text_rect = painter.fontMetrics().boundingRect(text)
                text_rect.translate(int(scx - text_rect.width() / 2), int(scy - text_rect.height() / 2))
                
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(15, 23, 42, 190))
                painter.drawRoundedRect(text_rect.adjusted(-5, -2, 5, 2), 4, 4)
                
                painter.setPen(QColor(248, 250, 252))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawText(text_rect.x(), text_rect.y() + text_rect.height() - 3, text)

            # Draw vertex handles if editing and selected
            if self.edit_mode == "edit_vertices" and is_selected:
                for pt in zone.points:
                    sx, sy = to_screen(pt[0], pt[1])
                    painter.setPen(QPen(QColor(255, 255, 255), 1.5))
                    painter.setBrush(QColor(30, 41, 59))
                    painter.drawEllipse(int(sx - 5), int(sy - 5), 10, 10)

        # 9. Draw active drawing path
        if self.edit_mode == "draw" and self.current_draw_points:
            draw_colors = {
                "room": QColor(248, 250, 252, 210),
                "wall": QColor(148, 163, 184, 220),
                "zone": QColor(234, 179, 8, 220),
            }
            active_color = draw_colors.get(self.draw_object_type, QColor(234, 179, 8, 220))
            painter.setPen(QPen(active_color, 2, Qt.PenStyle.SolidLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)

            # Draw lines between existing points
            if len(self.current_draw_points) > 1:
                for idx in range(len(self.current_draw_points) - 1):
                    x1, y1 = to_screen(self.current_draw_points[idx][0], self.current_draw_points[idx][1])
                    x2, y2 = to_screen(self.current_draw_points[idx+1][0], self.current_draw_points[idx+1][1])
                    painter.drawLine(x1, y1, x2, y2)

            # Draw dashed line to current mouse position
            if self.mouse_world_pos:
                last_pt = self.current_draw_points[-1]
                x1, y1 = to_screen(last_pt[0], last_pt[1])
                x2, y2 = to_screen(self.mouse_world_pos[0], self.mouse_world_pos[1])
                preview_color = QColor(active_color)
                preview_color.setAlpha(145)
                painter.setPen(QPen(preview_color, 1.5, Qt.PenStyle.DashLine))
                painter.drawLine(x1, y1, x2, y2)

            # Draw vertices
            for pt in self.current_draw_points:
                sx, sy = to_screen(pt[0], pt[1])
                painter.setPen(QPen(QColor(255, 255, 255), 1.5))
                painter.setBrush(active_color)
                painter.drawEllipse(int(sx - 4), int(sy - 4), 8, 8)


        # 11. Draw edge lengths and vertex coordinates for all zones / drawing path (dev mode only)
        if self.is_developer_mode:
            def draw_dimensions(points, is_closed=True):
                if not points:
                    return
                n = len(points)

                # Draw edge lengths
                edges_count = n if is_closed else n - 1
                for i in range(edges_count):
                    pt1 = points[i]
                    pt2 = points[(i + 1) % n]
                    
                    length = math.hypot(pt2[0] - pt1[0], pt2[1] - pt1[1])
                    length_text = f"{length:.2f}m"
                    
                    mx = (pt1[0] + pt2[0]) / 2.0
                    my = (pt1[1] + pt2[1]) / 2.0
                    smx, smy = to_screen(mx, my)
                    
                    painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
                    text_rect = painter.fontMetrics().boundingRect(length_text)
                    text_rect.translate(smx - text_rect.width() // 2, smy - text_rect.height() // 2)
                    
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(15, 23, 42, 210))
                    painter.drawRoundedRect(text_rect.adjusted(-4, -2, 4, 2), 4, 4)
                    
                    painter.setPen(QColor(34, 211, 238)) # Cyan-400 for length
                    painter.drawText(text_rect.x(), text_rect.y() + text_rect.height() - 3, length_text)

            # Draw for ALL zones
            for zone in self.geofence_zones:
                draw_dimensions(zone.points, is_closed=True)
                
            if self.edit_mode == "draw" and self.current_draw_points:
                draw_dimensions(self.current_draw_points, is_closed=False)

        # --- Scale Bar (bottom-left corner) ---
        if self._show_scale_bar:
            scale_px = min(width, height) / self._view_range if self._view_range > 0 else 50
            major_step = self._grid_spacing
            multiplier = 1
            while major_step * multiplier * scale_px < 60 and multiplier < 20:
                multiplier += 1
            while major_step * multiplier * scale_px > 220 and multiplier > 1:
                multiplier -= 1
            bar_world_m = major_step * multiplier

            start_x_world = math.ceil((view_x1 + major_step * 0.25) / major_step) * major_step
            start_y_world = math.ceil((view_y1 + major_step * 0.25) / major_step) * major_step
            if start_x_world + bar_world_m > view_x2:
                start_x_world = math.floor((view_x2 - bar_world_m) / major_step) * major_step
            start_x_world = max(start_x_world, view_x1)

            bar_x, bar_y = to_screen(start_x_world, start_y_world)
            bar_x2, bar_y2 = to_screen(start_x_world + bar_world_m, start_y_world)

            painter.setPen(QPen(QColor(226, 232, 240), 2))
            painter.drawLine(bar_x, bar_y, bar_x2, bar_y2)
            painter.drawLine(bar_x, bar_y - 4, bar_x, bar_y + 4)
            painter.drawLine(bar_x2, bar_y2 - 4, bar_x2, bar_y2 + 4)

            painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            if bar_world_m < 1.0:
                bar_text = f"{int(bar_world_m * 100)} cm"
            else:
                bar_text = f"{bar_world_m:.0f} m" if abs(bar_world_m - round(bar_world_m)) < 1e-6 else f"{bar_world_m:.2f} m"
            painter.setPen(QColor(226, 232, 240))
            painter.drawText((bar_x + bar_x2) // 2 - 18, bar_y - 6, bar_text)

        # --- Mouse Coordinate Tracker (bottom-right corner) ---
        if self._show_mouse_coords and self.mouse_world_pos:
            mx, my = self.mouse_world_pos
            coord_m = f"({mx:.3f}, {my:.3f}) m"
            coord_cm = f"({mx*100:.1f}, {my*100:.1f}) cm"
            display_text = f"{coord_m}  |  {coord_cm}"

            painter.setFont(QFont("Consolas", 9))
            text_w = painter.fontMetrics().horizontalAdvance(display_text)
            text_h = painter.fontMetrics().height()
            tx = self.width() - margin - text_w - 10
            ty = self.height() - margin - 10

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(15, 23, 42, 200))
            painter.drawRoundedRect(tx - 6, ty - text_h - 2, text_w + 12, text_h + 8, 4, 4)

            painter.setPen(QColor(148, 163, 184))
            painter.drawText(tx, ty, display_text)
