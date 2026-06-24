"""
===============================================================================
  UWB RTLS Studio - Position Canvas Component
===============================================================================
"""
import math
import time
from copy import deepcopy
from PyQt6.QtCore import Qt, QEvent, QTimer, pyqtSignal, QPointF, QPoint, QRect
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
    QDoubleValidator,
    QPolygon,
)
from PyQt6.QtWidgets import QLineEdit, QWidget
from utils.config_dim import GRID_SPACING_M
from views.components.zone_property_panel import ZonePropertyPanel


class PositionCanvas(QWidget):
    """Interactive 2D position canvas used by the live tracking tab."""

    polygon_completed = pyqtSignal(list)  # list of (x, y) tuples
    zone_selected = pyqtSignal(str)       # zone_id
    zone_modified = pyqtSignal(str, list) # zone_id, list of (x, y) tuples
    zone_properties_updated = pyqtSignal(str, dict) # zone_id, dict of updated properties
    anchor_selected = pyqtSignal(int)     # index in anchors list, -1 means none
    anchor_layout_edited = pyqtSignal(list)
    zones_undo_remove_requested = pyqtSignal(list)
    room_origin_vertex_picked = pyqtSignal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 300)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.position = {"x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0, "error": 0.0}
        self.fusion_position = None
        self.anchors = [
            {"anchor_id": 0, "x": 0.0, "y": 0.0, "z": 0.0, "label": "A0"},
            {"anchor_id": 1, "x": 10.76, "y": 0.0, "z": 0.0, "label": "A1"},
            {"anchor_id": 2, "x": 0.0, "y": 13.2, "z": 0.0, "label": "A2"},
            {"anchor_id": 3, "x": 10.76, "y": 13.2, "z": 0.0, "label": "A3"},
        ]
        self.history = []
        self.fusion_history = []
        self.tril_history = []
        self.max_history = 10000

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
        self.draw_object_type = "zone"  # "zone" | "room" | "wall" | "anchor"
        self.current_draw_points = []
        self.selected_zone_id = None
        self.selected_vertex_idx = None
        self.selected_edge_idx = None
        self.selected_anchor_idx = None
        self.dragging_anchor_idx = None
        self._anchor_template = None
        self.hovered_edge = None
        self._edge_drag_start_world = None
        self._edge_drag_original_points = None
        self.mouse_world_pos = (0.0, 0.0)
        self.dim_tracking_view = False
        self._room_origins = {}
        self._origin_pick_room_id = None
        self._origin_pick_hover_idx = None

        # Snap & preview grid settings
        self._grid_spacing = GRID_SPACING_M  # meters (configured in config.py)
        self._grid_subdivisions = 5
        self._show_scale_bar = True
        self._show_mouse_coords = True
        self._show_tracking_grid = True
        self._tracking_grid_spacing = 1.0
        self._tracking_grid_subdivisions = 5
        self.is_developer_mode = False
        self.snapped_grid_pt = None
        self.preview_25d = False
        self.draw_object_shape = "polygon"
        self._object_draw_center = None
        self.active_room_ids = set()
        self._undo_stack = []
        self._dimension_hitboxes = []
        self._dimension_edit_target = None
        self._dimension_edit_committing = False
        self._dimension_editor = QLineEdit(self)
        self._dimension_editor.setValidator(QDoubleValidator(0.01, 10000.0, 3, self._dimension_editor))
        self._dimension_editor.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._dimension_editor.setStyleSheet(
            "QLineEdit { background: #0F172A; color: #22D3EE; border: 2px solid #22D3EE; "
            "border-radius: 5px; padding: 2px 5px; font-weight: bold; }"
        )
        self._dimension_editor.returnPressed.connect(self._commit_dimension_edit)
        self._dimension_editor.editingFinished.connect(self._commit_dimension_edit)
        self._dimension_editor.installEventFilter(self)
        self._dimension_editor.hide()

        # Floating Property Panel Integration
        self.property_panel = ZonePropertyPanel(self)
        self.property_panel_enabled = False
        self.property_panel.hide()
        self.property_panel.closed.connect(self._close_property_panel)
        self.property_panel.property_changed.connect(self._on_panel_property_changed)
        self.property_panel.edge_changed.connect(self._on_panel_edge_changed)

        QTimer.singleShot(50, self.auto_fit)

    def set_geofences(self, zones):
        self.geofence_zones = zones
        self.update()

    def eventFilter(self, watched, event):
        if watched is self._dimension_editor and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self._cancel_dimension_edit()
                return True
        return super().eventFilter(watched, event)

    def _dimension_hit_at(self, screen_x, screen_y):
        point = QPoint(int(screen_x), int(screen_y))
        for rect, zone_id, edge_idx, length in reversed(self._dimension_hitboxes):
            if rect.contains(point):
                return zone_id, edge_idx, length, rect
        return None

    def _begin_dimension_edit(self, zone_id, edge_idx, length, rect):
        if self._dimension_editor.isVisible():
            self._commit_dimension_edit()
        self.set_selected_zone(zone_id)
        self.zone_selected.emit(zone_id)
        self.selected_edge_idx = edge_idx
        self._dimension_edit_target = (zone_id, edge_idx)
        editor_rect = rect.adjusted(-8, -5, 8, 5)
        editor_rect.setWidth(max(editor_rect.width(), 72))
        editor_rect.moveCenter(rect.center())
        editor_rect.moveLeft(max(2, min(editor_rect.left(), self.width() - editor_rect.width() - 2)))
        editor_rect.moveTop(max(2, min(editor_rect.top(), self.height() - editor_rect.height() - 2)))
        self._dimension_editor.setGeometry(editor_rect)
        self._dimension_editor.setText(f"{float(length):.3f}")
        self._dimension_editor.selectAll()
        self._dimension_editor.show()
        self._dimension_editor.raise_()
        self._dimension_editor.setFocus(Qt.FocusReason.MouseFocusReason)
        self.update()

    def _cancel_dimension_edit(self):
        self._dimension_edit_target = None
        self._dimension_editor.hide()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.update()

    def _commit_dimension_edit(self):
        if self._dimension_edit_committing or not self._dimension_editor.isVisible():
            return
        self._dimension_edit_committing = True
        try:
            target = self._dimension_edit_target
            text = self._dimension_editor.text().strip().replace(",", ".")
            self._dimension_edit_target = None
            self._dimension_editor.hide()
            if target is None:
                return
            try:
                length = float(text)
            except ValueError:
                return
            if not math.isfinite(length) or length < 0.01:
                return
            zone_id, edge_idx = target
            zone = next((item for item in self.geofence_zones if item.id == zone_id), None)
            if zone is None or not (0 <= edge_idx < len(zone.points)):
                return
            pt1 = zone.points[edge_idx]
            pt2 = zone.points[(edge_idx + 1) % len(zone.points)]
            angle_deg = math.degrees(math.atan2(pt2[1] - pt1[1], pt2[0] - pt1[0]))
            self.set_selected_zone(zone_id)
            self.selected_edge_idx = edge_idx
            self._push_undo_state()
            self._on_panel_edge_changed(edge_idx, length, angle_deg)
        finally:
            self._dimension_edit_committing = False
            self.setFocus(Qt.FocusReason.OtherFocusReason)
            self.update()

    def set_active_room_ids(self, room_ids):
        self.active_room_ids = {str(room_id) for room_id in (room_ids or []) if room_id}
        self.update()

    def _push_undo_state(self):
        self._undo_stack.append(
            {
                "anchors": deepcopy(self.anchors),
                "zones": [(zone.id, list(zone.points)) for zone in self.geofence_zones],
            }
        )
        if len(self._undo_stack) > 50:
            self._undo_stack.pop(0)

    def undo_last_action(self):
        if not self.is_developer_mode:
            return False

        if self.current_draw_points:
            self.current_draw_points.pop()
            self.update()
            return True
        if not self._undo_stack:
            return False

        state = self._undo_stack.pop()
        self.anchors = deepcopy(state["anchors"])
        zone_points = dict(state["zones"])
        added_zone_ids = [zone.id for zone in self.geofence_zones if zone.id not in zone_points]
        if added_zone_ids:
            self.zones_undo_remove_requested.emit(added_zone_ids)
        for zone in self.geofence_zones:
            if zone.id in zone_points:
                zone.points = list(zone_points[zone.id])
                self.zone_modified.emit(zone.id, zone.points)
        self.selected_vertex_idx = None
        self.selected_edge_idx = None
        self.dragging_anchor_idx = None
        self._emit_anchor_layout_edited()
        self.update()
        return True

    def set_edit_mode(self, mode):
        self.edit_mode = mode
        self.current_draw_points.clear()
        self._object_draw_center = None
        self.selected_vertex_idx = None
        self.selected_edge_idx = None
        self.dragging_anchor_idx = None
        self.hovered_edge = None
        self._edge_drag_start_world = None
        self._edge_drag_original_points = None
        if mode == "navigate":
            self.setCursor(Qt.CursorShape.ArrowCursor)
        elif mode == "draw":
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif mode == "edit_vertices":
            self.setCursor(Qt.CursorShape.ArrowCursor)
        elif mode == "insert_vertex":
            self.setCursor(Qt.CursorShape.CrossCursor)
        self.update()

    def set_selected_zone(self, zone_id):
        self.selected_zone_id = zone_id
        if zone_id:
            self.selected_anchor_idx = None
            self.anchor_selected.emit(-1)
        if zone_id and self.property_panel_enabled:
            self.show_property_panel(zone_id)
        else:
            self.property_panel.hide()
        self.update()

    def set_room_origin(self, room_id, point):
        if point is None:
            self._room_origins.pop(room_id, None)
        else:
            self._room_origins[room_id] = tuple(point)
        self.update()

    def begin_room_origin_pick(self, room_id):
        room = next(
            (zone for zone in self.geofence_zones if zone.id == room_id and getattr(zone, "object_type", "zone") == "room"),
            None,
        )
        if room is None or len(room.points) < 3:
            return False
        self._origin_pick_room_id = room_id
        self._origin_pick_hover_idx = None
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.update()
        return True

    def cancel_room_origin_pick(self):
        self._origin_pick_room_id = None
        self._origin_pick_hover_idx = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def _origin_vertex_at(self, screen_x, screen_y):
        if not self._origin_pick_room_id:
            return None
        room = next((zone for zone in self.geofence_zones if zone.id == self._origin_pick_room_id), None)
        if room is None:
            return None
        for index, point in enumerate(room.points):
            if self._is_close(point, screen_x, screen_y, threshold_px=14):
                return index
        return None
    def set_grid_spacing(self, spacing_m: float):
        """Set grid line spacing in meters."""
        self.set_grid_settings(spacing_m, self._grid_subdivisions)
        self.update()

    def set_grid_settings(self, major_spacing_m: float, subdivisions: int):
        """Set major grid spacing and snap subdivisions."""
        self._grid_spacing = max(0.1, min(float(major_spacing_m), 15.0))
        self._grid_subdivisions = max(1, min(int(subdivisions), 20))
        self.update()

    def set_draw_object_type(self, object_type: str):
        if object_type not in {"zone", "room", "wall", "object", "anchor"}:
            object_type = "zone"
        if self.draw_object_type != object_type:
            self.current_draw_points.clear()
            self._object_draw_center = None
        self.draw_object_type = object_type
        self.update()

    def set_draw_object_shape(self, shape_kind: str):
        self.draw_object_shape = shape_kind if shape_kind in {"polygon", "circle"} else "polygon"
        self._object_draw_center = None
        self.current_draw_points.clear()
        self.update()

    def begin_insert_vertex(self):
        zone = next((item for item in self.geofence_zones if item.id == self.selected_zone_id), None)
        if zone is None or len(zone.points) < 2:
            return False
        self.set_edit_mode("insert_vertex")
        return True

    @staticmethod
    def _circle_points(center_x, center_y, radius_m, segments=24):
        radius_m = max(0.01, float(radius_m))
        return [
            (
                center_x + math.cos(2.0 * math.pi * idx / segments) * radius_m,
                center_y + math.sin(2.0 * math.pi * idx / segments) * radius_m,
            )
            for idx in range(max(8, int(segments)))
        ]

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

    def _distance_to_segment_px(self, point_xy, seg_start_xy, seg_end_xy):
        px, py = point_xy
        x1, y1 = seg_start_xy
        x2, y2 = seg_end_xy
        dx = x2 - x1
        dy = y2 - y1
        seg_len_sq = dx * dx + dy * dy
        if seg_len_sq <= 1e-9:
            return math.hypot(px - x1, py - y1), 0.0
        t = ((px - x1) * dx + (py - y1) * dy) / seg_len_sq
        t = max(0.0, min(1.0, t))
        proj_x = x1 + t * dx
        proj_y = y1 + t * dy
        return math.hypot(px - proj_x, py - proj_y), t

    def _find_edge_near_screen_pos(self, screen_x, screen_y, max_distance_px=10):
        layer_order = {"room": 0, "wall": 1, "object": 2, "zone": 3}
        sorted_for_hit = sorted(
            self.geofence_zones,
            key=lambda z: layer_order.get(getattr(z, "object_type", "zone"), 3),
            reverse=True,
        )
        for zone in sorted_for_hit:
            points = zone.points
            if len(points) < 2:
                continue
            for idx in range(len(points)):
                pt1 = points[idx]
                pt2 = points[(idx + 1) % len(points)]
                sx1, sy1 = self._world_to_screen(pt1[0], pt1[1])
                sx2, sy2 = self._world_to_screen(pt2[0], pt2[1])
                distance_px, t = self._distance_to_segment_px((screen_x, screen_y), (sx1, sy1), (sx2, sy2))
                if distance_px <= max_distance_px and 0.05 <= t <= 0.95:
                    return zone.id, idx
        return None

    def _is_inside_polygon(self, poly_points, wx, wy):
        poly = QPolygonF()
        for pt in poly_points:
            poly.append(QPointF(pt[0], pt[1]))
        return poly.containsPoint(QPointF(wx, wy), Qt.FillRule.OddEvenFill)

    def _room_by_id(self, room_id):
        if not room_id:
            return None
        return next(
            (
                zone for zone in self.geofence_zones
                if getattr(zone, "object_type", "zone") == "room" and zone.id == room_id
            ),
            None,
        )

    def _wall_outward_normal(self, zone, p1, p2):
        """Return the outward unit normal for a boundary wall segment, or None."""
        x1, y1 = p1
        x2, y2 = p2
        dx = x2 - x1
        dy = y2 - y1
        length = math.hypot(dx, dy)
        if length <= 1e-9:
            return None
        nx = -dy / length
        ny = dx / length
        host_room = self._room_by_id(getattr(zone, "host_room_id", None))
        if getattr(zone, "wall_mode", "free_standing") != "boundary_outside" or host_room is None:
            return None
        mid_x = (x1 + x2) * 0.5
        mid_y = (y1 + y2) * 0.5
        thickness = max(0.0, float(getattr(zone, "thickness", 0.1)))
        epsilon = min(0.02, max(thickness * 0.25, 0.001))
        plus_is_inside = self._is_inside_polygon(
            host_room.points, mid_x + nx * epsilon, mid_y + ny * epsilon
        )
        return (-nx, -ny) if plus_is_inside else (nx, ny)

    def _wall_segment_footprint(self, zone, p1, p2):
        """Return one wall segment footprint in world coordinates."""
        x1, y1 = p1
        x2, y2 = p2
        dx = x2 - x1
        dy = y2 - y1
        length = math.hypot(dx, dy)
        thickness = max(0.0, float(getattr(zone, "thickness", 0.1)))
        if length <= 1e-9 or thickness <= 0.0:
            return []

        outward = self._wall_outward_normal(zone, p1, p2)
        if outward is not None:
            outward_x, outward_y = outward
            return [
                (x1, y1),
                (x2, y2),
                (x2 + outward_x * thickness, y2 + outward_y * thickness),
                (x1 + outward_x * thickness, y1 + outward_y * thickness),
            ]

        nx = -dy / length
        ny = dx / length
        half = thickness * 0.5
        return [
            (x1 + nx * half, y1 + ny * half),
            (x2 + nx * half, y2 + ny * half),
            (x2 - nx * half, y2 - ny * half),
            (x1 - nx * half, y1 - ny * half),
        ]

    @staticmethod
    def _line_intersection(a, direction_a, b, direction_b):
        """Intersection of infinite 2D lines, or None when parallel."""
        ax, ay = a
        adx, ady = direction_a
        bx, by = b
        bdx, bdy = direction_b
        denominator = adx * bdy - ady * bdx
        if abs(denominator) <= 1e-9:
            return None
        cross = (bx - ax) * bdy - (by - ay) * bdx
        scale = cross / denominator
        return ax + scale * adx, ay + scale * ady

    def _draw_forbidden_hatch(self, painter, poly, color):
        """Draw diagonal hatching inside a forbidden zone polygon (from commit 05e6a86b)."""
        from PyQt6.QtGui import QPainterPath
        if poly.count() < 3:
            return
        hatch_color = QColor(color)
        hatch_color.setAlpha(90)
        bounds = poly.boundingRect().adjusted(-20, -20, 20, 20)
        clip_path = QPainterPath()
        clip_path.addPolygon(poly)
        painter.save()
        painter.setClipPath(clip_path)
        painter.setPen(QPen(hatch_color, 1.0, Qt.PenStyle.SolidLine))
        start = int(bounds.left() - bounds.height())
        end = int(bounds.right() + bounds.height())
        step = 14
        x = start
        while x <= end:
            painter.drawLine(int(x), int(bounds.bottom()), int(x + bounds.height()), int(bounds.top()))
            x += step
        painter.restore()

    def _wall_footprint_path(self, zone):
        """Create one continuous wall footprint with closed, square/mitered corners in screen coordinates."""
        points = list(getattr(zone, "points", []) or [])
        thickness = max(0.0, float(getattr(zone, "thickness", 0.1)))
        if len(points) < 2 or thickness <= 0.0:
            return QPainterPath()

        screen_points = [self._world_to_screen(pt[0], pt[1]) for pt in points]
        
        ref_x1, ref_y1 = self._world_to_screen(0.0, 0.0)
        ref_x2, ref_y2 = self._world_to_screen(thickness, 0.0)
        thickness_px = math.hypot(ref_x2 - ref_x1, ref_y2 - ref_y1)

        mode = getattr(zone, "wall_mode", "free_standing")
        if mode != "boundary_outside" or self._room_by_id(getattr(zone, "host_room_id", None)) is None:
            from PyQt6.QtGui import QPainterPathStroker
            centerline = QPainterPath(QPointF(screen_points[0][0], screen_points[0][1]))
            for x, y in screen_points[1:]:
                centerline.lineTo(x, y)
            stroker = QPainterPathStroker()
            stroker.setWidth(thickness_px)
            stroker.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
            stroker.setCapStyle(Qt.PenCapStyle.SquareCap)
            return stroker.createStroke(centerline)

        combined = QPainterPath()
        for idx in range(len(points) - 1):
            footprint = self._wall_segment_footprint(zone, points[idx], points[idx + 1])
            if len(footprint) != 4:
                continue
            s_footprint = [self._world_to_screen(pt[0], pt[1]) for pt in footprint]
            segment_path = QPainterPath(QPointF(s_footprint[0][0], s_footprint[0][1]))
            for x, y in s_footprint[1:]:
                segment_path.lineTo(x, y)
            segment_path.closeSubpath()
            combined = segment_path if combined.isEmpty() else combined.united(segment_path)

        for idx in range(1, len(points) - 1):
            previous_point = points[idx - 1]
            vertex = points[idx]
            next_point = points[idx + 1]
            normal_a = self._wall_outward_normal(zone, previous_point, vertex)
            normal_b = self._wall_outward_normal(zone, vertex, next_point)
            if normal_a is None or normal_b is None:
                continue
            dot = normal_a[0] * normal_b[0] + normal_a[1] * normal_b[1]
            if dot > 0.999:
                continue
            direction_a = (vertex[0] - previous_point[0], vertex[1] - previous_point[1])
            direction_b = (next_point[0] - vertex[0], next_point[1] - vertex[1])
            a = (vertex[0] + normal_a[0] * thickness, vertex[1] + normal_a[1] * thickness)
            b = (vertex[0] + normal_b[0] * thickness, vertex[1] + normal_b[1] * thickness)
            intersection = self._line_intersection(a, direction_a, b, direction_b)
            if intersection is None:
                intersection = (
                    vertex[0] + (normal_a[0] + normal_b[0]) * thickness,
                    vertex[1] + (normal_a[1] + normal_b[1]) * thickness,
                )
            
            s_vertex = self._world_to_screen(vertex[0], vertex[1])
            s_a = self._world_to_screen(a[0], a[1])
            s_intersection = self._world_to_screen(intersection[0], intersection[1])
            s_b = self._world_to_screen(b[0], b[1])

            join_path = QPainterPath(QPointF(s_vertex[0], s_vertex[1]))
            join_path.lineTo(s_a[0], s_a[1])
            join_path.lineTo(s_intersection[0], s_intersection[1])
            join_path.lineTo(s_b[0], s_b[1])
            join_path.closeSubpath()
            combined = combined.united(join_path)

        return combined

    def update_position(self, position):
        current_time = time.time()
        source = position.get("source", "ranging")
        last_update = self._last_update_by_source.get(source, 0.0)
        if current_time - last_update < self.update_interval:
            return

        self.last_update_time = current_time
        self._last_update_by_source[source] = current_time
        if source == "sensor_fusion":
            self.fusion_position = position
            self.fusion_history.append((position["x"], position["y"]))
            if len(self.fusion_history) > self.max_history:
                self.fusion_history.pop(0)
            tril_x = position.get("tril_x")
            tril_y = position.get("tril_y")
            if tril_x is not None and tril_y is not None:
                self.tril_history.append((float(tril_x), float(tril_y)))
                if len(self.tril_history) > self.max_history:
                    self.tril_history.pop(0)
            self.update()
            return

        self.position = position
        self.history.append((position["x"], position["y"]))
        if len(self.history) > self.max_history:
            self.history.pop(0)
        self.update()

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

    def set_anchors(self, anchors):
        normalized = []
        for idx, anchor in enumerate(anchors or []):
            anchor_id = self._coerce_int_id(anchor.get("anchor_id"), idx)
            normalized.append(
                {
                    "anchor_id": anchor_id,
                    "x": float(anchor.get("x", anchor.get("x_m", 0.0))),
                    "y": float(anchor.get("y", anchor.get("y_m", 0.0))),
                    "z": float(anchor.get("z", anchor.get("z_m", 0.0))),
                    "label": anchor.get("label", f"A{anchor_id}"),
                    "role": anchor.get("role", "anchor"),
                    "device_type": anchor.get("device_type", "uwb_anchor"),
                    "device_id": self._coerce_int_id(anchor.get("device_id"), anchor_id),
                    "mac": anchor.get("mac", ""),
                    "zone_id": anchor.get("zone_id", ""),
                    "zone_name": anchor.get("zone_name", ""),
                    "zone_ids": list(anchor.get("zone_ids", [])),
                    "zone_names": list(anchor.get("zone_names", [])),
                    "room_id": anchor.get("room_id", anchor.get("zone_id", "")),
                    "local_x_m": float(anchor.get("local_x_m", anchor.get("x", anchor.get("x_m", 0.0)))),
                    "local_y_m": float(anchor.get("local_y_m", anchor.get("y", anchor.get("y_m", 0.0)))),
                    "placed": bool(anchor.get("placed", True)),
                    "is_scanned": bool(anchor.get("is_scanned", anchor.get("scan_seen", False))),
                    "sync_state": anchor.get("sync_state", "synced"),
                }
            )
        self.anchors = normalized
        if self.selected_anchor_idx is not None and self.selected_anchor_idx >= len(self.anchors):
            self.selected_anchor_idx = None
        self.auto_fit()

    def set_anchor_template(self, anchor_info):
        self._anchor_template = dict(anchor_info) if anchor_info else None

    def anchor_layout_for_device(self):
        return [
            {
                "anchor_id": self._coerce_int_id(anchor.get("anchor_id"), idx),
                "x": float(anchor.get("x", 0.0)),
                "y": float(anchor.get("y", 0.0)),
                "x_m": float(anchor.get("x", 0.0)),
                "y_m": float(anchor.get("y", 0.0)),
                "z_m": float(anchor.get("z", 0.0)),
                "label": anchor.get("label", f"A{anchor.get('anchor_id', idx)}"),
                "role": anchor.get("role", "anchor"),
                "device_type": anchor.get("device_type", "uwb_anchor"),
                "device_id": self._coerce_int_id(anchor.get("device_id"), self._coerce_int_id(anchor.get("anchor_id"), idx)),
                "mac": anchor.get("mac", ""),
                "zone_id": anchor.get("zone_id", ""),
                "zone_name": anchor.get("zone_name", ""),
                "zone_ids": list(anchor.get("zone_ids", [])),
                "zone_names": list(anchor.get("zone_names", [])),
                "room_id": anchor.get("room_id", anchor.get("zone_id", "")),
                "local_x_m": float(anchor.get("local_x_m", anchor.get("x", anchor.get("x_m", 0.0)))),
                "local_y_m": float(anchor.get("local_y_m", anchor.get("y", anchor.get("y_m", 0.0)))),
                "placed": bool(anchor.get("placed", True)),
                "is_scanned": bool(anchor.get("is_scanned", False)),
                "sync_state": anchor.get("sync_state", "draft"),
            }
            for idx, anchor in enumerate(self.anchors)
        ]

    def _emit_anchor_layout_edited(self):
        self.anchor_layout_edited.emit(self.anchor_layout_for_device())

    def _anchor_at_screen_pos(self, screen_x, screen_y, threshold_px=14):
        for idx in range(len(self.anchors) - 1, -1, -1):
            anchor = self.anchors[idx]
            ax, ay = self._world_to_screen(anchor.get("x", 0.0), anchor.get("y", 0.0))
            if math.hypot(ax - screen_x, ay - screen_y) <= threshold_px:
                return idx
        return None

    def set_selected_anchor(self, anchor_idx):
        if anchor_idx is None or anchor_idx < 0 or anchor_idx >= len(self.anchors):
            self.selected_anchor_idx = None
            self.anchor_selected.emit(-1)
            self.update()
            return
        self.selected_zone_id = None
        self.property_panel.hide()
        self.selected_anchor_idx = anchor_idx
        self.anchor_selected.emit(anchor_idx)
        self.update()

    def add_or_move_anchor_at(self, world_x, world_y):
        self._push_undo_state()
        if self.selected_anchor_idx is not None and self.selected_anchor_idx < len(self.anchors):
            anchor = self.anchors[self.selected_anchor_idx]
            anchor["x"] = world_x
            anchor["y"] = world_y
            anchor["placed"] = True
            anchor["sync_state"] = "draft"
        else:
            used_ids = {self._coerce_int_id(anchor.get("anchor_id"), idx) for idx, anchor in enumerate(self.anchors)}
            template = self._anchor_template or {}
            requested_id = template.get("anchor_id")
            anchor_id = self._coerce_int_id(requested_id, 0) if requested_id is not None else 0
            while anchor_id in used_ids:
                anchor_id += 1
            self.anchors.append(
                {
                    "anchor_id": anchor_id,
                    "x": world_x,
                    "y": world_y,
                    "z": float(template.get("z", template.get("z_m", 0.0))),
                    "label": template.get("label", f"A{anchor_id}"),
                    "role": template.get("role", "anchor"),
                    "device_type": template.get("device_type", "uwb_anchor"),
                    "device_id": self._coerce_int_id(template.get("device_id"), anchor_id),
                    "mac": template.get("mac", ""),
                    "zone_id": template.get("zone_id", ""),
                    "zone_name": template.get("zone_name", ""),
                    "zone_ids": list(template.get("zone_ids", [])),
                    "zone_names": list(template.get("zone_names", [])),
                    "room_id": template.get("room_id", template.get("zone_id", "")),
                    "local_x_m": float(template.get("local_x_m", world_x)),
                    "local_y_m": float(template.get("local_y_m", world_y)),
                    "placed": True,
                    "is_scanned": bool(template.get("is_scanned", template.get("scan_seen", False))),
                    "sync_state": "draft",
                }
            )
            self.selected_anchor_idx = len(self.anchors) - 1
            self.anchor_selected.emit(self.selected_anchor_idx)
        self._emit_anchor_layout_edited()
        self.update()

    def update_selected_anchor(self, *, anchor_id=None, label=None, x=None, y=None, z=None, role=None, device_type=None):
        if self.selected_anchor_idx is None or self.selected_anchor_idx >= len(self.anchors):
            return
        self._push_undo_state()
        anchor = self.anchors[self.selected_anchor_idx]
        if anchor_id is not None:
            anchor["anchor_id"] = self._coerce_int_id(anchor_id, 0)
            anchor["device_id"] = self._coerce_int_id(anchor_id, 0)
            if label is None:
                anchor["label"] = f"A{anchor_id}"
        if label is not None:
            anchor["label"] = str(label).strip() or f"A{anchor.get('anchor_id', self.selected_anchor_idx)}"
        if x is not None:
            anchor["x"] = float(x)
        if y is not None:
            anchor["y"] = float(y)
        if z is not None:
            anchor["z"] = float(z)
        if role is not None:
            anchor["role"] = str(role)
        if device_type is not None:
            anchor["device_type"] = str(device_type)
        anchor["placed"] = True
        anchor["sync_state"] = "draft"
        self._emit_anchor_layout_edited()
        self.update()

    def delete_selected_anchor(self):
        if self.selected_anchor_idx is None or self.selected_anchor_idx >= len(self.anchors):
            return False
        self._push_undo_state()
        self.anchors.pop(self.selected_anchor_idx)
        self.selected_anchor_idx = None
        self.dragging_anchor_idx = None
        self.anchor_selected.emit(-1)
        self._emit_anchor_layout_edited()
        self.update()
        return True

    def clear_trail(self):
        self.history.clear()
        self.fusion_history.clear()
        self.tril_history.clear()
        self._last_update_by_source.clear()
        self.fusion_position = None
        self.update()

    def auto_fit(self):
        pts_x = [a["x"] for a in self.anchors] + [self.position["x"]]
        pts_y = [a["y"] for a in self.anchors] + [self.position["y"]]
        if self.fusion_position is not None:
            pts_x.append(self.fusion_position["x"])
            pts_y.append(self.fusion_position["y"])
        for zone in self.geofence_zones:
            for point in zone.points:
                pts_x.append(point[0])
                pts_y.append(point[1])
        if not pts_x:
            return

        min_x = min(pts_x)
        max_x = max(pts_x)
        min_y = min(pts_y)
        max_y = max(pts_y)
        padding = 1.0

        need_x = (max_x - min_x) + 2 * padding
        need_y = (max_y - min_y) + 2 * padding

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
        center_x = (min_x + max_x) / 2.0
        center_y = (min_y + max_y) / 2.0
        if right_panel_width > 0:
            visible_center_x = margin + (visible_width / 2.0)
            full_center_x = margin + (full_width / 2.0)
            center_x += (full_center_x - visible_center_x) / scale
        self._view_cx = center_x
        self._view_cy = center_y
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

    def mouseDoubleClickEvent(self, event):
        if not self.is_developer_mode:
            self.set_edit_mode("navigate")
            self.auto_fit()
            return

        pos = event.position()
        world_x, world_y = self._screen_to_world(pos.x(), pos.y())
        
        # Check if double clicked inside any zone (reverse Z-order)
        layer_order = {"room": 0, "wall": 1, "object": 2, "zone": 3}
        sorted_for_click = sorted(
            self.geofence_zones,
            key=lambda z: layer_order.get(getattr(z, "object_type", "zone"), 3),
            reverse=True
        )
        clicked_zone = None
        for zone in sorted_for_click:
            object_type = getattr(zone, "object_type", "zone")
            if object_type == "wall":
                path = self._wall_footprint_path(zone)
                if not path.isEmpty() and path.contains(pos):
                    clicked_zone = zone
                    break
            else:
                if self._is_inside_polygon(zone.points, world_x, world_y):
                    clicked_zone = zone
                    break
                
        if clicked_zone:
            self.edit_mode = "edit_vertices"
            self.zone_selected.emit(clicked_zone.id)
            self.set_selected_zone(clicked_zone.id)
        else:
            self.auto_fit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._dragging and not self._rect_zoom:
            self.auto_fit()
        self.update_property_panel_position()
        self.update()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return

        modifiers = event.modifiers()
        pan_step = self._view_range * 0.08
        direction = 1.0 if delta > 0 else -1.0

        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            self._view_cx -= direction * pan_step
            self.update_property_panel_position()
            self.update()
            event.accept()
            return

        if modifiers & Qt.KeyboardModifier.ControlModifier:
            self._view_cy += direction * pan_step
            self.update_property_panel_position()
            self.update()
            event.accept()
            return

        factor = 0.85 if delta > 0 else 1.18
        pos = event.position()
        world_x, world_y = self._screen_to_world(pos.x(), pos.y())
        self._view_range *= factor
        self._view_range = max(0.5, min(self._view_range, 200.0))
        world_x_2, world_y_2 = self._screen_to_world(pos.x(), pos.y())
        self._view_cx -= world_x_2 - world_x
        self._view_cy -= world_y_2 - world_y
        self.update_property_panel_position()
        self.update()
        event.accept()

    def mousePressEvent(self, event):
        self.setFocus()
        if not self.is_developer_mode and self.edit_mode != "navigate":
            self.set_edit_mode("navigate")

        pos = event.position()
        if event.button() == Qt.MouseButton.LeftButton:
            dimension_hit = self._dimension_hit_at(pos.x(), pos.y())
            if dimension_hit is not None:
                self._begin_dimension_edit(*dimension_hit)
                event.accept()
                return
        world_x, world_y = self._screen_to_world(pos.x(), pos.y())
        snapped_x, snapped_y = self._snap_world_point(world_x, world_y)

        # Rule zones are the top visual layer. Select them before allowing the
        # currently selected room/wall to capture a shared vertex or edge.
        if self.edit_mode == "edit_vertices" and event.button() == Qt.MouseButton.LeftButton:
            rule_zones = [
                zone for zone in reversed(self.geofence_zones)
                if getattr(zone, "object_type", "zone") == "zone"
                and zone.id != self.selected_zone_id
            ]
            for zone in rule_zones:
                hit_rule = self._is_inside_polygon(zone.points, world_x, world_y)
                if not hit_rule:
                    hit_rule = any(self._is_close(point, pos.x(), pos.y()) for point in zone.points)
                if not hit_rule:
                    for edge_idx in range(len(zone.points)):
                        point_a = zone.points[edge_idx]
                        point_b = zone.points[(edge_idx + 1) % len(zone.points)]
                        screen_a = self._world_to_screen(point_a[0], point_a[1])
                        screen_b = self._world_to_screen(point_b[0], point_b[1])
                        distance_px, _ = self._distance_to_segment_px(
                            (pos.x(), pos.y()), screen_a, screen_b
                        )
                        if distance_px <= 10:
                            hit_rule = True
                            break
                if hit_rule:
                    self.set_selected_zone(zone.id)
                    self.zone_selected.emit(zone.id)
                    self.update()
                    event.accept()
                    return

        if self._origin_pick_room_id is not None:
            if event.button() == Qt.MouseButton.RightButton:
                self.cancel_room_origin_pick()
                return
            if event.button() == Qt.MouseButton.LeftButton:
                vertex_idx = self._origin_vertex_at(pos.x(), pos.y())
                if vertex_idx is not None:
                    room_id = self._origin_pick_room_id
                    self.cancel_room_origin_pick()
                    self.room_origin_vertex_picked.emit(room_id, vertex_idx)
                return
        if self.edit_mode == "draw" and self.draw_object_type == "anchor" and event.button() == Qt.MouseButton.LeftButton:
            hit_anchor_idx = self._anchor_at_screen_pos(pos.x(), pos.y())
            if hit_anchor_idx is not None:
                self._push_undo_state()
                self.set_selected_anchor(hit_anchor_idx)
                self.dragging_anchor_idx = hit_anchor_idx
            else:
                self.add_or_move_anchor_at(snapped_x, snapped_y)
                self.dragging_anchor_idx = self.selected_anchor_idx
            self.setCursor(Qt.CursorShape.SizeAllCursor)
            return

        if self.edit_mode == "insert_vertex" and event.button() == Qt.MouseButton.LeftButton:
            target = next((zone for zone in self.geofence_zones if zone.id == self.selected_zone_id), None)
            edge_hit = self._find_edge_near_screen_pos(pos.x(), pos.y(), max_distance_px=14)
            if target is not None and edge_hit and edge_hit[0] == target.id:
                insert_idx = edge_hit[1] + 1
                self._push_undo_state()
                target.points.insert(insert_idx, (snapped_x, snapped_y))
                self.selected_vertex_idx = insert_idx
                self.edit_mode = "edit_vertices"
                self.zone_modified.emit(target.id, target.points)
                self.update()
                return
            self.set_edit_mode("edit_vertices")
            return

        if self.edit_mode == "draw" and event.button() == Qt.MouseButton.LeftButton:
            if self.draw_object_type == "object" and self.draw_object_shape == "circle":
                if self._object_draw_center is None:
                    self._object_draw_center = (snapped_x, snapped_y)
                    self.current_draw_points = [self._object_draw_center]
                    self.update()
                    return
                radius_m = math.hypot(snapped_x - self._object_draw_center[0], snapped_y - self._object_draw_center[1])
                if radius_m >= 0.01:
                    self._push_undo_state()
                    points = self._circle_points(self._object_draw_center[0], self._object_draw_center[1], radius_m)
                    self.current_draw_points.clear()
                    self._object_draw_center = None
                    self.polygon_completed.emit(points)
                self.update()
                return
            # Check if clicked near the first point to close the polygon
            if self.current_draw_points and self._is_close(self.current_draw_points[0], pos.x(), pos.y()):
                if len(self.current_draw_points) >= 3:
                    self._push_undo_state()
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
            self._object_draw_center = None
            self.update()
            return

        # Selection or vertex editing mode
        if self.edit_mode == "edit_vertices" and event.button() == Qt.MouseButton.LeftButton:
            hit_anchor_idx = self._anchor_at_screen_pos(pos.x(), pos.y())
            if hit_anchor_idx is not None:
                self.set_selected_anchor(hit_anchor_idx)
                self.dragging_anchor_idx = hit_anchor_idx
                self.setCursor(Qt.CursorShape.SizeAllCursor)
                return

            # Check if clicked near any vertex of the selected zone (if selected)
            if self.selected_zone_id:
                sel_zone = next((z for z in self.geofence_zones if z.id == self.selected_zone_id), None)
                if sel_zone:
                    for idx, pt in enumerate(sel_zone.points):
                        if self._is_close(pt, pos.x(), pos.y()):
                            self._push_undo_state()
                            self.selected_vertex_idx = idx
                            self.setCursor(Qt.CursorShape.SizeAllCursor)
                            return
                    for edge_idx in range(len(sel_zone.points)):
                        pt1 = sel_zone.points[edge_idx]
                        pt2 = sel_zone.points[(edge_idx + 1) % len(sel_zone.points)]
                        sx1, sy1 = self._world_to_screen(pt1[0], pt1[1])
                        sx2, sy2 = self._world_to_screen(pt2[0], pt2[1])
                        distance_px, t = self._distance_to_segment_px((pos.x(), pos.y()), (sx1, sy1), (sx2, sy2))
                        if distance_px <= 10 and 0.05 <= t <= 0.95:
                            self._push_undo_state()
                            self.selected_edge_idx = edge_idx
                            self._edge_drag_start_world = (snapped_x, snapped_y)
                            self._edge_drag_original_points = list(sel_zone.points)
                            self.setCursor(Qt.CursorShape.SizeAllCursor)
                            self.update()
                            return

            # Sort zones by reverse Z-order for click priority (upper layer first)
            layer_order = {"room": 0, "wall": 1, "object": 2, "zone": 3}
            sorted_for_click = sorted(
                self.geofence_zones,
                key=lambda z: layer_order.get(getattr(z, "object_type", "zone"), 3),
                reverse=True
            )

            # Check if clicked near any vertex of ANY zone
            for zone in sorted_for_click:
                for idx, pt in enumerate(zone.points):
                    if self._is_close(pt, pos.x(), pos.y()):
                        self.set_selected_zone(zone.id)
                        self._push_undo_state()
                        self.selected_vertex_idx = idx
                        self.zone_selected.emit(zone.id)
                        self.setCursor(Qt.CursorShape.SizeAllCursor)
                        self.update()
                        return

            edge_hit = self._find_edge_near_screen_pos(pos.x(), pos.y())
            if edge_hit:
                zone_id, edge_idx = edge_hit
                zone = next((z for z in sorted_for_click if z.id == zone_id), None)
                if zone:
                    self.set_selected_zone(zone.id)
                    self._push_undo_state()
                    self.selected_edge_idx = edge_idx
                    self._edge_drag_start_world = (snapped_x, snapped_y)
                    self._edge_drag_original_points = list(zone.points)
                    self.zone_selected.emit(zone.id)
                    self.setCursor(Qt.CursorShape.SizeAllCursor)
                    self.update()
                    return

            # Check if clicked INSIDE any zone polygon (walls checked by footprint body)
            for zone in sorted_for_click:
                object_type = getattr(zone, "object_type", "zone")
                if object_type == "wall":
                    path = self._wall_footprint_path(zone)
                    if not path.isEmpty() and path.contains(pos):
                        self.set_selected_zone(zone.id)
                        self.zone_selected.emit(zone.id)
                        self.update()
                        return
                else:
                    if self._is_inside_polygon(zone.points, world_x, world_y):
                        self.set_selected_zone(zone.id)
                        self.zone_selected.emit(zone.id)
                        self.update()
                        return

            # Clicked empty space: clear selection
            if self.selected_zone_id:
                self.set_selected_zone(None)
                self.zone_selected.emit("")
                self.update()
            if self.selected_anchor_idx is not None:
                self.set_selected_anchor(None)

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
        if not self.is_developer_mode and self.edit_mode != "navigate":
            self.set_edit_mode("navigate")

        pos = event.position()
        if self._dimension_editor.isVisible():
            return
        if self._dimension_hit_at(pos.x(), pos.y()) is not None:
            self.hovered_edge = None
            self.setCursor(Qt.CursorShape.IBeamCursor)
            return
        world_x, world_y = self._screen_to_world(pos.x(), pos.y())
        snapped_x, snapped_y = self._snap_world_point(world_x, world_y)
        if self._origin_pick_room_id is not None:
            self._origin_pick_hover_idx = self._origin_vertex_at(pos.x(), pos.y())
            self.setCursor(
                Qt.CursorShape.PointingHandCursor
                if self._origin_pick_hover_idx is not None
                else Qt.CursorShape.CrossCursor
            )
            self.update()
            return
        if self.edit_mode in {"draw", "edit_vertices"}:
            self.mouse_world_pos = (snapped_x, snapped_y)
            self.snapped_grid_pt = (snapped_x, snapped_y)
        else:
            self.mouse_world_pos = (world_x, world_y)
            self.snapped_grid_pt = None

        if self.dragging_anchor_idx is not None and self.dragging_anchor_idx < len(self.anchors):
            anchor = self.anchors[self.dragging_anchor_idx]
            anchor["x"] = snapped_x
            anchor["y"] = snapped_y
            anchor["placed"] = True
            anchor["sync_state"] = "draft"
            self.selected_anchor_idx = self.dragging_anchor_idx
            self._emit_anchor_layout_edited()
            self.update()
            return

        # Handle vertex drag
        if self.edit_mode == "edit_vertices" and self.selected_vertex_idx is not None and self.selected_zone_id:
            sel_zone = next((z for z in self.geofence_zones if z.id == self.selected_zone_id), None)
            if sel_zone:
                sel_zone.points[self.selected_vertex_idx] = (snapped_x, snapped_y)
                self.zone_modified.emit(self.selected_zone_id, sel_zone.points)
                if not self.property_panel.isHidden():
                    self.property_panel.load_zone(sel_zone)
                self.update()
                return

        if self.edit_mode == "edit_vertices" and self.selected_edge_idx is not None and self.selected_zone_id:
            sel_zone = next((z for z in self.geofence_zones if z.id == self.selected_zone_id), None)
            if sel_zone and self._edge_drag_start_world and self._edge_drag_original_points:
                original_points = self._edge_drag_original_points
                edge_idx = self.selected_edge_idx
                pt1 = original_points[edge_idx]
                pt2 = original_points[(edge_idx + 1) % len(original_points)]
                edge_dx = pt2[0] - pt1[0]
                edge_dy = pt2[1] - pt1[1]
                edge_len = math.hypot(edge_dx, edge_dy)
                if edge_len > 1e-9:
                    normal_x = -edge_dy / edge_len
                    normal_y = edge_dx / edge_len
                    move_dx = snapped_x - self._edge_drag_start_world[0]
                    move_dy = snapped_y - self._edge_drag_start_world[1]
                    offset = move_dx * normal_x + move_dy * normal_y
                    new_points = list(original_points)
                    new_points[edge_idx] = (
                        original_points[edge_idx][0] + normal_x * offset,
                        original_points[edge_idx][1] + normal_y * offset,
                    )
                    next_idx = (edge_idx + 1) % len(original_points)
                    new_points[next_idx] = (
                        original_points[next_idx][0] + normal_x * offset,
                        original_points[next_idx][1] + normal_y * offset,
                    )
                    sel_zone.points = [self._snap_world_point(px, py) for px, py in new_points]
                    self.zone_modified.emit(self.selected_zone_id, sel_zone.points)
                    if not self.property_panel.isHidden():
                        self.property_panel.load_zone(sel_zone)
                    self.update()
                    return

        # Hover cues for edit mode
        if self.edit_mode == "edit_vertices" and not self._dragging:
            if self._anchor_at_screen_pos(pos.x(), pos.y()) is not None:
                self.hovered_edge = None
                self.setCursor(Qt.CursorShape.SizeAllCursor)
                return

            over_vertex = False
            for zone in self.geofence_zones:
                for pt in zone.points:
                    if self._is_close(pt, pos.x(), pos.y()):
                        over_vertex = True
                        break
                if over_vertex:
                    break
            if over_vertex:
                self.hovered_edge = None
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self.hovered_edge = self._find_edge_near_screen_pos(pos.x(), pos.y())
                if self.hovered_edge:
                    self.setCursor(Qt.CursorShape.SizeAllCursor)
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
            self.update_property_panel_position()
            self.update()
        elif self._rect_zoom and self._rect_start:
            self._rect_end = event.position()
            self.update()
        elif self.edit_mode == "draw":
            self.update()

    def mouseReleaseEvent(self, event):
        if self.dragging_anchor_idx is not None:
            self.dragging_anchor_idx = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()
            return

        if self.selected_vertex_idx is not None:
            self.selected_vertex_idx = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()
            return

        if self.selected_edge_idx is not None:
            self.selected_edge_idx = None
            self._edge_drag_start_world = None
            self._edge_drag_original_points = None
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

    def _draw_anchor_layer(self, painter, to_screen, *, draw_connections=True):
        active_pos = self.fusion_position if self.fusion_position is not None else self.position
        pos_x, pos_y = to_screen(active_pos["x"], active_pos["y"])

        if draw_connections:
            for anchor in self.anchors:
                anchor_x, anchor_y = to_screen(anchor["x"], anchor["y"])
                painter.setPen(QPen(QColor(99, 102, 241, 40), 1, Qt.PenStyle.DashLine))
                painter.drawLine(pos_x, pos_y, anchor_x, anchor_y)

        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        for idx, anchor in enumerate(self.anchors):
            center_x, center_y = to_screen(anchor["x"], anchor["y"])
            is_selected_anchor = self.selected_anchor_idx == idx
            is_scanned = bool(anchor.get("is_scanned", False))
            is_draft = anchor.get("sync_state") == "draft"

            if is_selected_anchor:
                ring = QColor(250, 204, 21)
                fill = QColor(34, 211, 238)
            elif is_scanned:
                ring = QColor(34, 197, 94)
                fill = QColor(22, 163, 74)
            elif is_draft:
                ring = QColor(245, 158, 11)
                fill = QColor(217, 119, 6)
            else:
                ring = QColor(99, 102, 241)
                fill = QColor(79, 70, 229)

            painter.setPen(QPen(ring, 3 if is_selected_anchor else 2))
            painter.setBrush(QColor(15, 23, 42, 230))
            painter.drawEllipse(center_x - 12, center_y - 12, 24, 24)
            painter.setBrush(fill)
            painter.drawEllipse(center_x - 4, center_y - 4, 8, 8)

            label = anchor.get("label", anchor.get("id", "?"))
            painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
            painter.setPen(QColor(248, 250, 252))
            painter.drawText(center_x + 16, center_y - 10, label)
            painter.setFont(QFont("Segoe UI", 8))
            painter.setPen(QColor(203, 213, 225))
            painter.drawText(
                center_x + 16,
                center_y + 4,
                f"({anchor['x']:.1f}, {anchor['y']:.1f}, {anchor.get('z', 0.0):.1f})",
            )

    def _draw_tracking_grid(self, painter, to_screen, view_x1, view_y1, view_x2, view_y2, margin, width, height):
        """Draw a fixed 1 m grid for User mode without changing Spatial settings."""
        major_step = self._tracking_grid_spacing
        minor_step = major_step / max(self._tracking_grid_subdivisions, 1)
        scale_px = min(width, height) / self._view_range if self._view_range > 0 else 50

        if minor_step * scale_px >= 4:
            painter.setPen(QPen(QColor(148, 163, 184, 24), 1, Qt.PenStyle.DotLine))
            grid_x = math.floor(view_x1 / minor_step) * minor_step
            guard = 0
            while grid_x <= view_x2 and guard < 5000:
                screen_x, _ = to_screen(grid_x, 0)
                painter.drawLine(screen_x, margin, screen_x, self.height() - margin)
                grid_x += minor_step
                guard += 1

            grid_y = math.floor(view_y1 / minor_step) * minor_step
            guard = 0
            while grid_y <= view_y2 and guard < 5000:
                _, screen_y = to_screen(0, grid_y)
                painter.drawLine(margin, screen_y, margin + width, screen_y)
                grid_y += minor_step
                guard += 1

        painter.setPen(QPen(QColor(148, 163, 184, 70), 1, Qt.PenStyle.DotLine))
        grid_x = math.floor(view_x1 / major_step) * major_step
        guard = 0
        while grid_x <= view_x2 and guard < 2000:
            screen_x, _ = to_screen(grid_x, 0)
            painter.drawLine(screen_x, margin, screen_x, self.height() - margin)
            grid_x += major_step
            guard += 1

        grid_y = math.floor(view_y1 / major_step) * major_step
        guard = 0
        while grid_y <= view_y2 and guard < 2000:
            _, screen_y = to_screen(0, grid_y)
            painter.drawLine(margin, screen_y, margin + width, screen_y)
            grid_y += major_step
            guard += 1

        axis_x, axis_y = to_screen(0, 0)
        painter.setPen(QPen(QColor(226, 232, 240, 150), 1.5))
        if margin <= axis_x <= margin + width:
            painter.drawLine(axis_x, margin, axis_x, self.height() - margin)
        if margin <= axis_y <= self.height() - margin:
            painter.drawLine(margin, axis_y, margin + width, axis_y)

        painter.setFont(QFont("Segoe UI", 9))
        painter.setPen(QColor(203, 213, 225))
        grid_x = math.floor(view_x1 / major_step) * major_step
        guard = 0
        while grid_x <= view_x2 and guard < 2000:
            screen_x, _ = to_screen(grid_x, 0)
            painter.drawText(screen_x - 12, self.height() - margin + 16, f"{grid_x:.0f}m")
            grid_x += major_step
            guard += 1

        grid_y = math.floor(view_y1 / major_step) * major_step
        guard = 0
        while grid_y <= view_y2 and guard < 2000:
            _, screen_y = to_screen(0, grid_y)
            painter.drawText(4, screen_y + 4, f"{grid_y:.0f}m")
            grid_y += major_step
            guard += 1

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

        if self._show_tracking_grid and not self.dim_tracking_view:
            self._draw_tracking_grid(
                painter,
                to_screen,
                view_x1,
                view_y1,
                view_x2,
                view_y2,
                margin,
                width,
                height,
            )

        for room_id, point in self._room_origins.items():
            origin_x, origin_y = to_screen(point[0], point[1])
            painter.setPen(QPen(QColor("#F8FAFC"), 2))
            painter.setBrush(QColor("#EF4444"))
            painter.drawEllipse(origin_x - 7, origin_y - 7, 14, 14)
            painter.setPen(QColor("#FCA5A5"))
            painter.drawText(origin_x + 10, origin_y - 8, "Local (0,0)")

        # 1. Draw Fusion History Trail (UKF, solid sky blue)
        if len(self.fusion_history) > 1:
            painter.setPen(QPen(QColor(14, 165, 233, 200), 2, Qt.PenStyle.SolidLine))
            for idx in range(len(self.fusion_history) - 1):
                x1, y1 = to_screen(self.fusion_history[idx][0], self.fusion_history[idx][1])
                x2, y2 = to_screen(self.fusion_history[idx + 1][0], self.fusion_history[idx + 1][1])
                painter.drawLine(x1, y1, x2, y2)

        # 2. Draw Trilateration history as dots
        if self.tril_history:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(251, 146, 60, 180))
            for xw, yw in self.tril_history:
                sx, sy = to_screen(xw, yw)
                painter.drawEllipse(sx - 2, sy - 2, 4, 4)

        # 3-4. Draw active anchors in normal tracking mode. Editor mode redraws
        # anchors later so the dim/grid overlay does not hide newly placed ones.
        if not self.dim_tracking_view:
            self._draw_anchor_layer(painter, to_screen, draw_connections=True)

        # 5. Draw Trilateration Marker (orange circle with crosshair)
        if self.fusion_position is not None:
            tril_world_x = self.fusion_position.get("tril_x", self.fusion_position["x"])
            tril_world_y = self.fusion_position.get("tril_y", self.fusion_position["y"])
            tril_x, tril_y = to_screen(tril_world_x, tril_world_y)
            painter.setPen(QPen(QColor(251, 146, 60), 2))
            painter.setBrush(QColor(251, 146, 60, 80))
            painter.drawEllipse(tril_x - 8, tril_y - 8, 16, 16)
            painter.drawLine(tril_x - 12, tril_y, tril_x + 12, tril_y)
            painter.drawLine(tril_x, tril_y - 12, tril_x, tril_y + 12)

        # 6. Draw Tag Position (Direction and Error Ellipse)
        scale_px = min(width, height) / self._view_range if self._view_range > 0 else 50
        active_tag = self.fusion_position if self.fusion_position is not None else self.position
        active_x, active_y = to_screen(active_tag["x"], active_tag["y"])

        if active_tag.get("error", 0) > 0:
            error_radius = int(active_tag["error"] * scale_px)
            painter.setPen(QPen(QColor(239, 68, 68, 60), 2, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(239, 68, 68, 20))
            painter.drawEllipse(active_x - error_radius, active_y - error_radius, error_radius * 2, error_radius * 2)

        # UKF center marker as a small dot
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(96, 165, 250))
        painter.drawEllipse(active_x - 3, active_y - 3, 6, 6)

        # Draw the directional arrow
        painter.save()
        painter.translate(active_x, active_y)
        painter.rotate(-active_tag.get("yaw", 0))
        painter.setPen(
            QPen(
                QColor(37, 99, 235),  # Blue-600
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

        # Tag glow effect (sky blue)
        glow_gradient = QRadialGradient(active_x, active_y, 18)
        glow_gradient.setColorAt(0, QColor(96, 165, 250, 60))
        glow_gradient.setColorAt(1, QColor(96, 165, 250, 0))
        painter.setBrush(glow_gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(active_x - 18, active_y - 18, 36, 36)

        # Tag coordinates text overlay
        if self.fusion_position is not None:
            coord_text = f"UKF {active_tag['x']:.2f}, {active_tag['y']:.2f}"
        else:
            coord_text = f"{active_tag['x']:.2f}, {active_tag['y']:.2f}"

        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        text_rect = painter.fontMetrics().boundingRect(coord_text)
        text_rect.translate(active_x + 15, active_y + 15)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(15, 23, 42, 180))
        painter.drawRoundedRect(text_rect.adjusted(-4, -2, 4, 2), 4, 4)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(active_x + 15, active_y + 15 + text_rect.height() - 4, coord_text)

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
        layer_order = {"room": 0, "wall": 1, "object": 2, "zone": 3}
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
            # ── visual style from 05e6a86b ──────────────────────────────────
            if object_type == "room":
                base_color = str(zone.color).replace("_semi", "") if getattr(zone, "color", "") else "#1D4ED8"
                fill_color = QColor(base_color if base_color.startswith("#") else "#1D4ED8")
                fill_color.setAlpha(34)
                border_color = QColor("#7DD3FC")
                pen_style = Qt.PenStyle.SolidLine
                border_width = 2.0
            elif object_type == "wall":
                fill_color = QColor(zone.color if getattr(zone, "color", "").startswith("#") else "#111827")
                fill_color.setAlpha(225)
                border_color = QColor("#CBD5E1")
                pen_style = Qt.PenStyle.SolidLine
                border_width = 3.0
            elif object_type == "object":
                fill_color = QColor(zone.color if getattr(zone, "color", "").startswith("#") else "#F59E0B")
                fill_color.setAlpha(110)
                border_color = QColor("#FDBA74")
                pen_style = Qt.PenStyle.SolidLine
                border_width = 2.0
            elif zone.zone_type == "forbidden":
                fill_color = QColor(zone.color if getattr(zone, "color", "").startswith("#") else "#EF4444")
                fill_color.setAlpha(72)
                border_color = QColor("#F87171")
                pen_style = Qt.PenStyle.SolidLine
                border_width = 2.0
            else:  # allowed zone
                fill_color = QColor(zone.color if getattr(zone, "color", "").startswith("#") else "#22C55E")
                fill_color.setAlpha(58)
                border_color = QColor("#4ADE80")
                pen_style = Qt.PenStyle.DashLine
                border_width = 2.0

            is_selected = (zone.id == self.selected_zone_id)
            if is_selected:
                border_color = QColor("#FACC15")
                border_width += 1.2

            object_height = max(0.0, zone.max_z - zone.min_z) if object_type in {"wall", "object"} else 0.0
            if object_type == "wall" and not (self.preview_25d and object_height > 0 and len(zone.points) >= 3):
                path = self._wall_footprint_path(zone)
                if not path.isEmpty():
                    painter.fillPath(path, QBrush(fill_color))
                    painter.strokePath(path, QPen(border_color, border_width, pen_style))
            elif self.preview_25d and object_type in {"wall", "object"} and object_height > 0 and len(zone.points) >= 3:
                draw_extruded_polygon(poly, fill_color, border_color, object_height)
            else:
                painter.setPen(QPen(border_color, border_width, pen_style))
                painter.setBrush(QBrush(fill_color))
                painter.drawPolygon(poly)

            if object_type == "room" and zone.id in self.active_room_ids:
                # Subtle green active room overlay matching 05e6a86b
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(34, 197, 94, 38))
                painter.drawPolygon(poly)
            if object_type == "zone" and zone.zone_type == "forbidden":
                # Diagonal hatch pattern for forbidden zones
                self._draw_forbidden_hatch(painter, poly, border_color)
            if object_type == "object" and getattr(zone, "object_subtype", "generic") == "stairs":
                rect = poly.boundingRect()
                direction_str = getattr(zone, "object_direction", "up")
                label_text = "UP" if direction_str != "down" else "DN"
                # Step lines
                step_color = QColor("#FFF7ED")
                step_color.setAlpha(180)
                painter.setPen(QPen(step_color, 1.5))
                steps = 7
                if rect.width() >= rect.height():
                    for idx in range(1, steps):
                        x = rect.left() + rect.width() * idx / steps
                        painter.drawLine(int(x), int(rect.top()), int(x), int(rect.bottom()))
                else:
                    for idx in range(1, steps):
                        y = rect.top() + rect.height() * idx / steps
                        painter.drawLine(int(rect.left()), int(y), int(rect.right()), int(y))
                # Direction label
                painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
                lbl_rect = painter.fontMetrics().boundingRect(label_text)
                lbl_rect.moveCenter(rect.center().toPoint())
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(15, 23, 42, 190))
                painter.drawRoundedRect(lbl_rect.adjusted(-5, -2, 5, 2), 4, 4)
                painter.setPen(QColor("#FDE68A"))
                painter.drawText(lbl_rect, Qt.AlignmentFlag.AlignCenter, label_text)

            if len(zone.points) >= 3:
                cx = sum(p[0] for p in zone.points) / len(zone.points)
                cy = sum(p[1] for p in zone.points) / len(zone.points)
                scx, scy = to_screen(cx, cy)
                
                painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
                if object_type == "zone":
                    text = f"{zone.name} ({zone.speed_limit:.1f} m/s)"
                elif object_type == "wall":
                    text = f"{zone.name} ({object_height:.1f} m high)"
                else:
                    text = zone.name
                text_rect = painter.fontMetrics().boundingRect(text)
                text_rect.translate(int(scx - text_rect.width() / 2), int(scy - text_rect.height() / 2))
                
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(15, 23, 42, 190))
                painter.drawRoundedRect(text_rect.adjusted(-5, -2, 5, 2), 4, 4)
                
                painter.setPen(QColor(248, 250, 252))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawText(text_rect.x(), text_rect.y() + text_rect.height() - 3, text)

            active_edge_idx = None
            if is_selected and self.selected_edge_idx is not None:
                active_edge_idx = self.selected_edge_idx
            elif self.hovered_edge and self.hovered_edge[0] == zone.id:
                active_edge_idx = self.hovered_edge[1]

            if active_edge_idx is not None and len(zone.points) >= 2:
                edge_start = zone.points[active_edge_idx]
                edge_end = zone.points[(active_edge_idx + 1) % len(zone.points)]
                ex1, ey1 = to_screen(edge_start[0], edge_start[1])
                ex2, ey2 = to_screen(edge_end[0], edge_end[1])
                painter.setPen(QPen(QColor(34, 211, 238), 4))
                painter.drawLine(ex1, ey1, ex2, ey2)

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
                "object": QColor(245, 158, 11, 225),
                "zone": QColor(234, 179, 8, 220),
            }
            active_color = draw_colors.get(self.draw_object_type, QColor(234, 179, 8, 220))
            painter.setPen(QPen(active_color, 2, Qt.PenStyle.SolidLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)

            handled_circle_preview = False
            if self.draw_object_type == "object" and self.draw_object_shape == "circle" and len(self.current_draw_points) == 1:
                center_x, center_y = self.current_draw_points[0]
                sx, sy = to_screen(center_x, center_y)
                if self.mouse_world_pos:
                    mx, my = self.mouse_world_pos
                    radius = math.hypot(mx - center_x, my - center_y)
                    edge_x, edge_y = to_screen(mx, my)
                    painter.setPen(QPen(QColor(active_color.red(), active_color.green(), active_color.blue(), 180), 1.6, Qt.PenStyle.DashLine))
                    painter.drawLine(sx, sy, edge_x, edge_y)
                    pixel_radius = math.hypot(edge_x - sx, edge_y - sy)
                    painter.setBrush(QColor(active_color.red(), active_color.green(), active_color.blue(), 36))
                    painter.drawEllipse(int(sx - pixel_radius), int(sy - pixel_radius), int(pixel_radius * 2), int(pixel_radius * 2))
                    label = f"{radius:.2f} m"
                    painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
                    text_rect = painter.fontMetrics().boundingRect(label)
                    text_rect.translate(int((sx + edge_x) / 2 - text_rect.width() / 2), int((sy + edge_y) / 2 - text_rect.height() / 2))
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(15, 23, 42, 220))
                    painter.drawRoundedRect(text_rect.adjusted(-4, -2, 4, 2), 4, 4)
                    painter.setPen(QColor(226, 232, 240))
                    painter.drawText(text_rect.x(), text_rect.y() + text_rect.height() - 3, label)
                painter.setPen(QPen(QColor(255, 255, 255), 1.5))
                painter.setBrush(active_color)
                painter.drawEllipse(int(sx - 4), int(sy - 4), 8, 8)
                handled_circle_preview = True

            if not handled_circle_preview:
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

        if self.dim_tracking_view:
            self._draw_anchor_layer(painter, to_screen, draw_connections=False)

        # --- 11. Draw edge lengths and vertex coordinates ---
        self._dimension_hitboxes = []
        def draw_dimensions(points, is_closed=True, zone=None):
            if not points:
                return
            n = len(points)
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
                if zone is not None:
                    hit_rect = text_rect.adjusted(-8, -6, 8, 6)
                    self._dimension_hitboxes.append((QRect(hit_rect), zone.id, i, length))

        if self.is_developer_mode:
            for zone in self.geofence_zones:
                draw_dimensions(zone.points, is_closed=True, zone=zone)
            if self.edit_mode == "draw" and self.current_draw_points:
                draw_dimensions(self.current_draw_points, is_closed=False)
        elif self.selected_zone_id:
            sel_zone = next((z for z in self.geofence_zones if z.id == self.selected_zone_id), None)
            if sel_zone:
                draw_dimensions(sel_zone.points, is_closed=True, zone=sel_zone)

        # --- Scale Bar (bottom-left corner) ---
        if self._show_scale_bar:
            scale_px = min(width, height) / self._view_range if self._view_range > 0 else 50
            major_step = self._grid_spacing
            # Keep the reference bar aligned with one configured major grid cell.
            bar_world_m = major_step

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

    def _close_property_panel(self):
        self.selected_zone_id = None
        self.zone_selected.emit("")
        self.property_panel.reset_user_position()
        self.property_panel.hide()
        self.update()

    def _on_panel_property_changed(self, prop_name, value):
        if not self.selected_zone_id:
            return
        zone = next((z for z in self.geofence_zones if z.id == self.selected_zone_id), None)
        if not zone:
            return

        if prop_name == "name":
            zone.name = str(value)
        elif prop_name == "color":
            zone.color = str(value)
        elif prop_name == "height":
            zone.min_z = 0.0
            zone.max_z = float(value)
        elif prop_name == "speed_limit":
            zone.speed_limit = float(value)
        elif prop_name == "thickness":
            zone.thickness = float(value)
        elif prop_name == "shape_kind":
            zone.shape_kind = str(value)
        elif prop_name == "object_subtype":
            zone.object_subtype = str(value)
            if zone.object_subtype == "stairs":
                zone.shape_kind = "polygon"
        elif prop_name == "object_direction":
            zone.object_direction = str(value)
        elif prop_name == "wall_mode":
            zone.wall_mode = str(value)
        elif prop_name == "host_room_id":
            zone.host_room_id = str(value) if value else None

        self.zone_properties_updated.emit(zone.id, {prop_name: value})
        self.update()

    def _on_panel_edge_changed(self, edge_idx, length, angle_deg):
        if not self.selected_zone_id:
            return
        zone = next((z for z in self.geofence_zones if z.id == self.selected_zone_id), None)
        if not zone or len(zone.points) < 2:
            return

        n = len(zone.points)
        pt1 = zone.points[edge_idx]
        pt2 = zone.points[(edge_idx + 1) % n]

        theta = math.radians(angle_deg)
        pt2_new_x = pt1[0] + length * math.cos(theta)
        pt2_new_y = pt1[1] + length * math.sin(theta)

        shift_x = pt2_new_x - pt2[0]
        shift_y = pt2_new_y - pt2[1]

        new_points = list(zone.points)

        if edge_idx < n - 1:
            for k in range(edge_idx + 1, n):
                new_points[k] = (new_points[k][0] + shift_x, new_points[k][1] + shift_y)
        else:
            pt_last_x = zone.points[0][0] - length * math.cos(theta)
            pt_last_y = zone.points[0][1] - length * math.sin(theta)
            new_points[n - 1] = (pt_last_x, pt_last_y)

        zone.points = new_points
        self.zone_modified.emit(zone.id, zone.points)
        self.update()
        
        self.property_panel.load_zone(zone)

    def show_property_panel(self, zone_id):
        if self.edit_mode != "edit_vertices":
            self.property_panel.reset_user_position()
            self.property_panel.hide()
            return

        zone = next((z for z in self.geofence_zones if z.id == zone_id), None)
        if not zone or len(zone.points) < 2:
            self.property_panel.reset_user_position()
            self.property_panel.hide()
            return

        self.property_panel.load_zone(zone)
        if self.property_panel.has_user_position():
            if self.property_panel.isHidden():
                self.property_panel.show()
            return
        
        cx = sum(p[0] for p in zone.points) / len(zone.points)
        cy = sum(p[1] for p in zone.points) / len(zone.points)
        scx, scy = self._world_to_screen(cx, cy)
        
        panel_w = self.property_panel.width()
        panel_h = self.property_panel.height()
        
        target_x = scx + 30
        target_y = scy - panel_h // 2
        
        target_x = max(10, min(target_x, self.width() - panel_w - 10))
        target_y = max(10, min(target_y, self.height() - panel_h - 10))
        
        if self.property_panel.isHidden():
            self.property_panel.show()
            
            from PyQt6.QtWidgets import QGraphicsOpacityEffect
            from PyQt6.QtCore import QPropertyAnimation, QEasingCurve
            
            if not self.property_panel.graphicsEffect():
                opacity_effect = QGraphicsOpacityEffect(self.property_panel)
                self.property_panel.setGraphicsEffect(opacity_effect)
            else:
                opacity_effect = self.property_panel.graphicsEffect()
                
            self.opacity_anim = QPropertyAnimation(opacity_effect, b"opacity")
            self.opacity_anim.setDuration(200)
            self.opacity_anim.setStartValue(0.0)
            self.opacity_anim.setEndValue(1.0)
            
            self.pos_anim = QPropertyAnimation(self.property_panel, b"pos")
            self.pos_anim.setDuration(250)
            self.pos_anim.setStartValue(QPoint(scx, scy))
            self.pos_anim.setEndValue(QPoint(target_x, target_y))
            self.pos_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            
            self.opacity_anim.start()
            self.pos_anim.start()
        else:
            from PyQt6.QtCore import QPropertyAnimation, QEasingCurve
            self.pos_anim = QPropertyAnimation(self.property_panel, b"pos")
            self.pos_anim.setDuration(180)
            self.pos_anim.setStartValue(self.property_panel.pos())
            self.pos_anim.setEndValue(QPoint(target_x, target_y))
            self.pos_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self.pos_anim.start()

    def update_property_panel_position(self):
        if self.property_panel.isHidden() or not self.selected_zone_id or self.property_panel.has_user_position():
            return
            
        zone = next((z for z in self.geofence_zones if z.id == self.selected_zone_id), None)
        if not zone:
            return
            
        cx = sum(p[0] for p in zone.points) / len(zone.points)
        cy = sum(p[1] for p in zone.points) / len(zone.points)
        scx, scy = self._world_to_screen(cx, cy)
        
        panel_w = self.property_panel.width()
        panel_h = self.property_panel.height()
        
        target_x = scx + 30
        target_y = scy - panel_h // 2
        
        target_x = max(10, min(target_x, self.width() - panel_w - 10))
        target_y = max(10, min(target_y, self.height() - panel_h - 10))
        
        self.property_panel.move(target_x, target_y)

    def update(self, *args, **kwargs):
        super().update(*args, **kwargs)
        if hasattr(self, "parent_tab") and self.parent_tab:
            self.parent_tab.update_preview_pane()
