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
)
from PyQt6.QtWidgets import QWidget
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
    room_origin_vertex_picked = pyqtSignal(str, int)  # room_id, vertex index
    edit_operation_started = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 300)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.position = {"x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0, "error": 0.0}
        self.fusion_position = None
        self.selected_zone_ids = set()
        self.anchors = [
            {"anchor_id": 0, "x": 0.0, "y": 0.0, "z": 0.0, "label": "A0"},
            {"anchor_id": 1, "x": 9.76, "y": 0.0, "z": 0.0, "label": "A1"},
            {"anchor_id": 2, "x": 9.76, "y": 9.76, "z": 0.0, "label": "A2"},
            {"anchor_id": 3, "x": 0.0, "y": 9.76, "z": 0.0, "label": "A3"},
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
        self._panning = False
        self._pan_start = None
        self._pan_view_cx = 0.0
        self._pan_view_cy = 0.0
        self._rect_zoom = False
        self._rect_start = None
        self._rect_end = None
        self._box_selecting = False
        self._box_select_start = None
        self._box_select_end = None

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
        self.dragging_zone_id = None
        self._zone_drag_start_world = None
        self._zone_drag_original_points = None
        self.mouse_world_pos = (0.0, 0.0)
        self.dim_tracking_view = False
        self._wall_path_cache = {}
        # Local-origin pick is separate from drawing/editing geometry.
        self._origin_pick_room_id = None
        self._origin_pick_hover_idx = None

        # Snap grid settings
        self._grid_spacing = GRID_SPACING_M  # meters (configured in config.py)
        self._grid_subdivisions = 5
        self._show_scale_bar = True
        self._show_mouse_coords = True
        self.is_developer_mode = False
        self.snapped_grid_pt = None
        self.draw_object_shape = "polygon"
        self._object_draw_center = None
        self.active_room_ids = set()

        # Floating Property Panel Integration
        self.property_panel = ZonePropertyPanel(self)
        self.property_panel.hide()
        self.property_panel.closed.connect(self._close_property_panel)
        self.property_panel.property_changed.connect(self._on_panel_property_changed)
        self.property_panel.edge_changed.connect(self._on_panel_edge_changed)

        QTimer.singleShot(50, self.auto_fit)

    def set_geofences(self, zones):
        self.geofence_zones = zones
        self._wall_path_cache.clear()
        self.update()

    def set_active_room_ids(self, room_ids):
        self.active_room_ids = {str(room_id) for room_id in (room_ids or []) if room_id}
        self.update()

    def set_active_room_id(self, room_id: str):
        self.active_room_ids = {str(room_id)} if room_id else set()
        self.update()

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

    def begin_insert_vertex(self):
        if not self.selected_zone_id:
            return False
        zone = next((z for z in self.geofence_zones if z.id == self.selected_zone_id), None)
        if zone is None or len(zone.points) < 2:
            return False
        self.set_edit_mode("insert_vertex")
        return True

    def _hit_test_zone(self, sx, sy, wx, wy):
        layer_order = {"room": 0, "wall": 1, "object": 2, "zone": 3}
        sorted_for_click = sorted(
            self.geofence_zones,
            key=lambda z: layer_order.get(getattr(z, "object_type", "zone"), 3),
            reverse=True
        )

        # 1. Check vertices
        for zone in sorted_for_click:
            for pt in zone.points:
                if self._is_close(pt, sx, sy):
                    return zone

        # 2. Check edges
        edge_hit = self._find_edge_near_screen_pos(sx, sy)
        if edge_hit:
            zone_id, _ = edge_hit
            zone = next((z for z in sorted_for_click if z.id == zone_id), None)
            if zone:
                return zone

        # 3. Check inside polygon
        for zone in sorted_for_click:
            if self._is_open_wall(zone):
                continue
            if self._is_inside_polygon(zone.points, wx, wy):
                return zone

        return None

    def _is_inside_or_on_boundary(self, poly_points, wx, wy, tolerance=0.05):
        if self._is_inside_polygon(poly_points, wx, wy):
            return True
        
        N = len(poly_points)
        if N < 2:
            return False
            
        for i in range(N):
            p1 = poly_points[i]
            p2 = poly_points[(i + 1) % N]
            
            dx = p2[0] - p1[0]
            dy = p2[1] - p1[1]
            seg_len_sq = dx * dx + dy * dy
            if seg_len_sq < 1e-9:
                dist = math.hypot(wx - p1[0], wy - p1[1])
            else:
                t = ((wx - p1[0]) * dx + (wy - p1[1]) * dy) / seg_len_sq
                t = max(0.0, min(1.0, t))
                proj_x = p1[0] + t * dx
                proj_y = p1[1] + t * dy
                dist = math.hypot(wx - proj_x, wy - proj_y)
                
            if dist <= tolerance:
                return True
                
        return False

    def _project_to_boundary(self, poly_points, wx, wy):
        if not poly_points:
            return wx, wy
        
        N = len(poly_points)
        if N < 2:
            return poly_points[0][0], poly_points[0][1]
            
        min_dist = float('inf')
        best_x, best_y = wx, wy
        
        for i in range(N):
            p1 = poly_points[i]
            p2 = poly_points[(i + 1) % N]
            
            dx = p2[0] - p1[0]
            dy = p2[1] - p1[1]
            seg_len_sq = dx * dx + dy * dy
            if seg_len_sq < 1e-9:
                dist = math.hypot(wx - p1[0], wy - p1[1])
                proj_x, proj_y = p1[0], p1[1]
            else:
                t = ((wx - p1[0]) * dx + (wy - p1[1]) * dy) / seg_len_sq
                t = max(0.0, min(1.0, t))
                proj_x = p1[0] + t * dx
                proj_y = p1[1] + t * dy
                dist = math.hypot(wx - proj_x, wy - proj_y)
                
            if dist < min_dist:
                min_dist = dist
                best_x, best_y = proj_x, proj_y
                
        return best_x, best_y

    def set_selected_zone(self, zone_id):
        self.selected_zone_id = zone_id
        if zone_id:
            self.selected_zone_ids = {zone_id}
            self.selected_anchor_idx = None
            self.anchor_selected.emit(-1)
        else:
            self.selected_zone_ids = set()
        self.property_panel.hide()
        self.update()

    def toggle_selected_zone(self, zone_id):
        if not zone_id:
            return
        if zone_id in self.selected_zone_ids:
            self.selected_zone_ids.remove(zone_id)
            if self.selected_zone_id == zone_id:
                self.selected_zone_id = list(self.selected_zone_ids)[0] if self.selected_zone_ids else None
        else:
            self.selected_zone_ids.add(zone_id)
            self.selected_zone_id = zone_id
        self.zone_selected.emit(self.selected_zone_id or "")
        self.update()

    def set_room_origin(self, zone_id: str, origin_pt):
        """Mark a specific world-point as the coordinate origin (0,0) for a room zone.
        The point will be highlighted in red on the canvas."""
        if not hasattr(self, "_room_origins"):
            self._room_origins = {}
        if origin_pt is None:
            self._room_origins.pop(zone_id, None)
        else:
            self._room_origins[zone_id] = tuple(origin_pt)
        self.update()

    def begin_room_origin_pick(self, room_id: str) -> bool:
        """Enter a non-destructive mode for choosing one room vertex as local origin."""
        room = next(
            (
                zone for zone in self.geofence_zones
                if zone.id == room_id and getattr(zone, "object_type", "zone") == "room"
            ),
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
        if self._origin_pick_room_id is None:
            return
        self._origin_pick_room_id = None
        self._origin_pick_hover_idx = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def _origin_pick_vertex_at(self, screen_x: float, screen_y: float):
        room_id = self._origin_pick_room_id
        if not room_id:
            return None
        room = next((zone for zone in self.geofence_zones if zone.id == room_id), None)
        if room is None:
            return None
        for idx, point in enumerate(room.points):
            if self._is_close(point, screen_x, screen_y, threshold_px=14):
                return idx
        return None

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
        if object_type not in {"zone", "room", "wall", "object", "anchor"}:
            object_type = "zone"
        if self.draw_object_type != object_type:
            self.current_draw_points.clear()
            self._object_draw_center = None
        self.draw_object_type = object_type
        self.update()

    def set_draw_object_shape(self, shape_kind: str):
        if shape_kind not in {"polygon", "circle"}:
            shape_kind = "polygon"
        self.draw_object_shape = shape_kind
        self._object_draw_center = None
        self.current_draw_points.clear()
        self.update()

    def clear_active_drawing(self):
        self.current_draw_points.clear()
        self._object_draw_center = None
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

    def _edge_hit_distance_px(self, zone, default_px=10):
        if self._is_open_wall(zone):
            return max(default_px, 22)
        return default_px

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
            for idx in range(self._edge_count(zone)):
                pt1 = points[idx]
                pt2 = points[idx + 1] if self._is_open_wall(zone) else points[(idx + 1) % len(points)]
                sx1, sy1 = self._world_to_screen(pt1[0], pt1[1])
                sx2, sy2 = self._world_to_screen(pt2[0], pt2[1])
                distance_px, t = self._distance_to_segment_px((screen_x, screen_y), (sx1, sy1), (sx2, sy2))
                if distance_px <= self._edge_hit_distance_px(zone, max_distance_px) and 0.05 <= t <= 0.95:
                    return zone.id, idx
        return None

    def _polygon_label_center(self, points):
        pts = list(points or [])
        if not pts:
            return 0.0, 0.0
        if len(pts) < 3:
            return (
                sum(point[0] for point in pts) / len(pts),
                sum(point[1] for point in pts) / len(pts),
            )
        signed_area = 0.0
        cx = 0.0
        cy = 0.0
        for idx, p1 in enumerate(pts):
            p2 = pts[(idx + 1) % len(pts)]
            cross = p1[0] * p2[1] - p2[0] * p1[1]
            signed_area += cross
            cx += (p1[0] + p2[0]) * cross
            cy += (p1[1] + p2[1]) * cross
        signed_area *= 0.5
        if abs(signed_area) < 1e-9:
            return (
                sum(point[0] for point in pts) / len(pts),
                sum(point[1] for point in pts) / len(pts),
            )
        return cx / (6.0 * signed_area), cy / (6.0 * signed_area)

    def _draw_object_symbol(self, painter, zone, poly):
        subtype = getattr(zone, "object_subtype", "generic")
        if subtype != "stairs" or poly.isEmpty():
            return
        rect = poly.boundingRect()
        painter.save()
        direction = str(getattr(zone, "object_direction", "up")).lower()
        painter.setPen(QPen(QColor(255, 251, 235), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        step_count = 6
        if rect.width() >= rect.height():
            for idx in range(1, step_count + 1):
                x = rect.left() + rect.width() * idx / (step_count + 1)
                painter.drawLine(int(x), int(rect.top() + 3), int(x), int(rect.bottom() - 3))
        else:
            for idx in range(1, step_count + 1):
                y = rect.top() + rect.height() * idx / (step_count + 1)
                painter.drawLine(int(rect.left() + 3), int(y), int(rect.right() - 3), int(y))
        painter.setPen(QPen(QColor(251, 191, 36), 2))
        painter.drawText(rect.adjusted(2, 2, -2, -2), Qt.AlignmentFlag.AlignCenter, "UP" if direction != "down" else "DN")
        painter.restore()

    def _is_open_wall(self, zone) -> bool:
        return getattr(zone, "object_type", "zone") == "wall"

    def _edge_count(self, zone) -> int:
        points = getattr(zone, "points", []) or []
        if len(points) < 2:
            return 0
        return len(points) - 1 if self._is_open_wall(zone) else len(points)

    @staticmethod
    def _circle_points(center_x: float, center_y: float, radius_m: float, segments: int = 24):
        radius_m = max(0.01, float(radius_m))
        segments = max(8, int(segments))
        points = []
        for idx in range(segments):
            angle = (2.0 * math.pi * idx) / segments
            points.append((center_x + math.cos(angle) * radius_m, center_y + math.sin(angle) * radius_m))
        return points

    def _is_inside_polygon(self, poly_points, wx, wy):
        if len(poly_points or []) < 3:
            return False
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
            self.fusion_position = position
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
                    "local_x_m": float(anchor.get("local_x_m", anchor.get("x_m", anchor.get("x", 0.0)))),
                    "local_y_m": float(anchor.get("local_y_m", anchor.get("y_m", anchor.get("y", 0.0)))),
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
                "local_x_m": float(anchor.get("local_x_m", anchor.get("x", 0.0))),
                "local_y_m": float(anchor.get("local_y_m", anchor.get("y", 0.0))),
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
        self._view_cx = (min_x + max_x) / 2.0
        self._view_cy = (min_y + max_y) / 2.0
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
        pos = event.position()
        world_x, world_y = self._screen_to_world(pos.x(), pos.y())
        snapped_x, snapped_y = self._snap_world_point(world_x, world_y)

        if self.edit_mode == "draw" and self.draw_object_type == "wall":
            if not self.current_draw_points or self.current_draw_points[-1] != (snapped_x, snapped_y):
                self.current_draw_points.append((snapped_x, snapped_y))
            if len(self.current_draw_points) >= 2:
                pts = list(self.current_draw_points)
                self.current_draw_points.clear()
                self.polygon_completed.emit(pts)
            self.update()
            return
        
        # Check if double clicked inside any zone (reverse Z-order)
        layer_order = {"room": 0, "wall": 1, "zone": 2}
        sorted_for_click = sorted(
            self.geofence_zones,
            key=lambda z: layer_order.get(getattr(z, "object_type", "zone"), 3),
            reverse=True
        )
        clicked_zone = None
        for zone in sorted_for_click:
            if self._is_open_wall(zone):
                hit = self._find_edge_near_screen_pos(pos.x(), pos.y())
                if hit and hit[0] == zone.id:
                    clicked_zone = zone
                    break
            elif self._is_inside_polygon(zone.points, world_x, world_y):
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
        self.property_panel.hide()
        self.update()

    def wheelEvent(self, event):
        factor = 0.85 if event.angleDelta().y() > 0 else 1.18
        pos = event.position()
        world_x, world_y = self._screen_to_world(pos.x(), pos.y())
        self._view_range *= factor
        self._view_range = max(0.5, min(self._view_range, 200.0))
        world_x_2, world_y_2 = self._screen_to_world(pos.x(), pos.y())
        self._view_cx -= world_x_2 - world_x
        self._view_cy -= world_y_2 - world_y
        self.property_panel.hide()
        self.update()

    def mousePressEvent(self, event):
        pos = event.position()
        world_x, world_y = self._screen_to_world(pos.x(), pos.y())
        snapped_x, snapped_y = self._snap_world_point(world_x, world_y)

        if self._origin_pick_room_id is not None:
            if event.button() == Qt.MouseButton.LeftButton:
                vertex_idx = self._origin_pick_vertex_at(pos.x(), pos.y())
                if vertex_idx is not None:
                    room_id = self._origin_pick_room_id
                    self.cancel_room_origin_pick()
                    self.room_origin_vertex_picked.emit(room_id, vertex_idx)
            elif event.button() == Qt.MouseButton.RightButton:
                self.cancel_room_origin_pick()
            return

        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_start = event.position()
            self._pan_view_cx = self._view_cx
            self._pan_view_cy = self._view_cy
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return

        if self.edit_mode == "draw" and self.draw_object_type == "anchor" and event.button() == Qt.MouseButton.LeftButton:
            hit_anchor_idx = self._anchor_at_screen_pos(pos.x(), pos.y())
            self.edit_operation_started.emit()
            if hit_anchor_idx is not None:
                self.set_selected_anchor(hit_anchor_idx)
                self.dragging_anchor_idx = hit_anchor_idx
            else:
                self.add_or_move_anchor_at(snapped_x, snapped_y)
                self.dragging_anchor_idx = self.selected_anchor_idx
            self.setCursor(Qt.CursorShape.SizeAllCursor)
            return

        if self.edit_mode == "insert_vertex" and event.button() == Qt.MouseButton.LeftButton:
            target_zone = next((z for z in self.geofence_zones if z.id == self.selected_zone_id), None)
            if target_zone is not None:
                edge_hit = self._find_edge_near_screen_pos(pos.x(), pos.y(), max_distance_px=14)
                if edge_hit and edge_hit[0] == target_zone.id:
                    _zone_id, edge_idx = edge_hit
                    insert_idx = edge_idx + 1
                    target_zone.points.insert(insert_idx, (snapped_x, snapped_y))
                    self.selected_vertex_idx = insert_idx
                    self.edit_mode = "edit_vertices"
                    self.zone_modified.emit(target_zone.id, target_zone.points)
                    self.setCursor(Qt.CursorShape.SizeAllCursor)
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
                    pts = self._circle_points(self._object_draw_center[0], self._object_draw_center[1], radius_m)
                    self.current_draw_points.clear()
                    self._object_draw_center = None
                    self.polygon_completed.emit(pts)
                self.update()
                return
            if self.draw_object_type != "wall" and self.current_draw_points and self._is_close(self.current_draw_points[0], pos.x(), pos.y()):
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
            if self.draw_object_type == "wall" and len(self.current_draw_points) >= 2:
                pts = list(self.current_draw_points)
                self.current_draw_points.clear()
                self._object_draw_center = None
                self.polygon_completed.emit(pts)
                self.update()
                return
            self.current_draw_points.clear()
            self._object_draw_center = None
            self.update()
            return

        # Selection or vertex editing mode
        if self.edit_mode == "edit_vertices" and event.button() == Qt.MouseButton.RightButton:
            self._box_selecting = True
            self._box_select_start = event.position()
            self._box_select_end = event.position()
            self.update()
            return

        if self.edit_mode == "edit_vertices" and event.button() == Qt.MouseButton.LeftButton:
            has_shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            has_ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)

            if has_shift or has_ctrl:
                hit_zone = self._hit_test_zone(pos.x(), pos.y(), world_x, world_y)
                if hit_zone:
                    self.edit_operation_started.emit()
                    if has_shift:
                        self.selected_zone_ids.add(hit_zone.id)
                        self.selected_zone_id = hit_zone.id
                        self.zone_selected.emit(hit_zone.id)
                    elif has_ctrl:
                        if hit_zone.id in self.selected_zone_ids:
                            self.selected_zone_ids.remove(hit_zone.id)
                            self.selected_zone_id = list(self.selected_zone_ids)[0] if self.selected_zone_ids else None
                        else:
                            self.selected_zone_ids.add(hit_zone.id)
                            self.selected_zone_id = hit_zone.id
                        self.zone_selected.emit(self.selected_zone_id or "")
                    self.update()
                return

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
                            self.edit_operation_started.emit()
                            self.selected_vertex_idx = idx
                            self.setCursor(Qt.CursorShape.SizeAllCursor)
                            return
                    for edge_idx in range(self._edge_count(sel_zone)):
                        pt1 = sel_zone.points[edge_idx]
                        pt2 = sel_zone.points[edge_idx + 1] if self._is_open_wall(sel_zone) else sel_zone.points[(edge_idx + 1) % len(sel_zone.points)]
                        sx1, sy1 = self._world_to_screen(pt1[0], pt1[1])
                        sx2, sy2 = self._world_to_screen(pt2[0], pt2[1])
                        distance_px, t = self._distance_to_segment_px((pos.x(), pos.y()), (sx1, sy1), (sx2, sy2))
                        if distance_px <= self._edge_hit_distance_px(sel_zone) and 0.05 <= t <= 0.95:
                            self.edit_operation_started.emit()
                            if self._is_open_wall(sel_zone):
                                clicked_zone_id = sel_zone.id
                                if clicked_zone_id not in self.selected_zone_ids:
                                    self.set_selected_zone(clicked_zone_id)
                                    self.zone_selected.emit(clicked_zone_id)
                                self.dragging_zone_id = clicked_zone_id
                                self._zone_drag_start_world = (snapped_x, snapped_y)
                                self._multi_zone_drag_original_points = {
                                    zid: list(next(z for z in self.geofence_zones if z.id == zid).points)
                                    for zid in self.selected_zone_ids
                                }
                                self.setCursor(Qt.CursorShape.SizeAllCursor)
                                self.update()
                                return
                            self.selected_edge_idx = edge_idx
                            self._edge_drag_start_world = (snapped_x, snapped_y)
                            self._edge_drag_original_points = list(sel_zone.points)
                            self.setCursor(Qt.CursorShape.SizeAllCursor)
                            self.update()
                            return
                    if not self._is_open_wall(sel_zone) and self._is_inside_polygon(sel_zone.points, world_x, world_y):
                        self.edit_operation_started.emit()
                        clicked_zone_id = sel_zone.id
                        if clicked_zone_id not in self.selected_zone_ids:
                            self.set_selected_zone(clicked_zone_id)
                            self.zone_selected.emit(clicked_zone_id)
                        self.dragging_zone_id = clicked_zone_id
                        self._zone_drag_start_world = (snapped_x, snapped_y)
                        self._multi_zone_drag_original_points = {
                            zid: list(next(z for z in self.geofence_zones if z.id == zid).points)
                            for zid in self.selected_zone_ids
                        }
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
                        self.edit_operation_started.emit()
                        self.set_selected_zone(zone.id)
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
                    self.edit_operation_started.emit()
                    if self._is_open_wall(zone):
                        clicked_zone_id = zone.id
                        if clicked_zone_id not in self.selected_zone_ids:
                            self.set_selected_zone(clicked_zone_id)
                            self.zone_selected.emit(clicked_zone_id)
                        self.dragging_zone_id = clicked_zone_id
                        self._zone_drag_start_world = (snapped_x, snapped_y)
                        self._multi_zone_drag_original_points = {
                            zid: list(next(z for z in self.geofence_zones if z.id == zid).points)
                            for zid in self.selected_zone_ids
                        }
                        self.setCursor(Qt.CursorShape.SizeAllCursor)
                        self.update()
                        return
                    self.set_selected_zone(zone.id)
                    self.selected_edge_idx = edge_idx
                    self._edge_drag_start_world = (snapped_x, snapped_y)
                    self._edge_drag_original_points = list(zone.points)
                    self.zone_selected.emit(zone.id)
                    self.setCursor(Qt.CursorShape.SizeAllCursor)
                    self.update()
                    return

            # Check if clicked INSIDE any zone polygon
            for zone in sorted_for_click:
                if self._is_open_wall(zone):
                    continue
                if self._is_inside_polygon(zone.points, world_x, world_y):
                    self.edit_operation_started.emit()
                    clicked_zone_id = zone.id
                    if clicked_zone_id not in self.selected_zone_ids:
                        self.set_selected_zone(clicked_zone_id)
                        self.zone_selected.emit(clicked_zone_id)
                    self.dragging_zone_id = clicked_zone_id
                    self._zone_drag_start_world = (snapped_x, snapped_y)
                    self._multi_zone_drag_original_points = {
                        zid: list(next(z for z in self.geofence_zones if z.id == zid).points)
                        for zid in self.selected_zone_ids
                    }
                    self.setCursor(Qt.CursorShape.SizeAllCursor)
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
        pos = event.position()
        if getattr(self, "_box_selecting", False) and self._box_select_start:
            self._box_select_end = pos
            self.update()
            return

        world_x, world_y = self._screen_to_world(pos.x(), pos.y())
        snapped_x, snapped_y = self._snap_world_point(world_x, world_y)
        if self.edit_mode in {"draw", "edit_vertices"}:
            self.mouse_world_pos = (snapped_x, snapped_y)
            self.snapped_grid_pt = (snapped_x, snapped_y)
        else:
            self.mouse_world_pos = (world_x, world_y)
            self.snapped_grid_pt = None

        if self._panning and self._pan_start is not None:
            dx = pos.x() - self._pan_start.x()
            dy = pos.y() - self._pan_start.y()
            margin = self._margin
            width = self.width() - 2 * margin
            height = self.height() - 2 * margin
            scale = min(width, height) / self._view_range if self._view_range > 0 else 50
            self._view_cx = self._pan_view_cx - dx / scale
            self._view_cy = self._pan_view_cy + dy / scale
            self.property_panel.hide()
            self.update()
            return

        if self._origin_pick_room_id is not None:
            new_hover_idx = self._origin_pick_vertex_at(pos.x(), pos.y())
            if new_hover_idx != self._origin_pick_hover_idx:
                self._origin_pick_hover_idx = new_hover_idx
                self.update()
            self.setCursor(
                Qt.CursorShape.PointingHandCursor
                if new_hover_idx is not None else Qt.CursorShape.CrossCursor
            )
            return

        if self.dragging_anchor_idx is not None and self.dragging_anchor_idx < len(self.anchors):
            anchor = self.anchors[self.dragging_anchor_idx]
            
            # Constraint: Anchor must be inside its associated room zone
            room_id = anchor.get("room_id") or anchor.get("zone_id")
            room = self._room_by_id(room_id)
            if not room:
                rooms = [z for z in self.geofence_zones if getattr(z, "object_type", "zone") == "room"]
                if rooms:
                    cur_x = anchor.get("x", 0.0)
                    cur_y = anchor.get("y", 0.0)
                    for r in rooms:
                        if self._is_inside_or_on_boundary(r.points, cur_x, cur_y):
                            room = r
                            break
                    if not room:
                        room = rooms[0]
            
            if room:
                if not self._is_inside_or_on_boundary(room.points, snapped_x, snapped_y):
                    snapped_x, snapped_y = self._project_to_boundary(room.points, snapped_x, snapped_y)

            anchor["x"] = snapped_x
            anchor["y"] = snapped_y
            anchor["placed"] = True
            anchor["sync_state"] = "draft"
            self.selected_anchor_idx = self.dragging_anchor_idx
            self._emit_anchor_layout_edited()
            self.update()
            return

        if self.dragging_zone_id is not None and self._zone_drag_start_world:
            dx = snapped_x - self._zone_drag_start_world[0]
            dy = snapped_y - self._zone_drag_start_world[1]
            
            if getattr(self, "_multi_zone_drag_original_points", None):
                for zid in self.selected_zone_ids:
                    sel_zone = next((z for z in self.geofence_zones if z.id == zid), None)
                    orig_pts = self._multi_zone_drag_original_points.get(zid)
                    if sel_zone and orig_pts:
                        sel_zone.points = [
                            self._snap_world_point(px + dx, py + dy)
                            for px, py in orig_pts
                        ]
                        self.zone_modified.emit(sel_zone.id, sel_zone.points)
            elif getattr(self, "_zone_drag_original_points", None):
                sel_zone = next((z for z in self.geofence_zones if z.id == self.dragging_zone_id), None)
                if sel_zone:
                    sel_zone.points = [
                        self._snap_world_point(px + dx, py + dy)
                        for px, py in self._zone_drag_original_points
                    ]
                    self.zone_modified.emit(sel_zone.id, sel_zone.points)
            
            self.property_panel.hide()
            self.update()
            return

        # Handle vertex drag
        if self.edit_mode == "edit_vertices" and self.selected_vertex_idx is not None and self.selected_zone_id:
            sel_zone = next((z for z in self.geofence_zones if z.id == self.selected_zone_id), None)
            if sel_zone:
                sel_zone.points[self.selected_vertex_idx] = (snapped_x, snapped_y)
                self.zone_modified.emit(self.selected_zone_id, sel_zone.points)
                self.property_panel.hide()
                self.update()
                return

        if self.edit_mode == "edit_vertices" and self.selected_edge_idx is not None and self.selected_zone_id:
            sel_zone = next((z for z in self.geofence_zones if z.id == self.selected_zone_id), None)
            if sel_zone and self._edge_drag_start_world and self._edge_drag_original_points:
                original_points = self._edge_drag_original_points
                edge_idx = self.selected_edge_idx
                pt1 = original_points[edge_idx]
                pt2 = original_points[edge_idx + 1] if getattr(sel_zone, "object_type", "zone") == "wall" else original_points[(edge_idx + 1) % len(original_points)]
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
                    next_idx = edge_idx + 1 if getattr(sel_zone, "object_type", "zone") == "wall" else (edge_idx + 1) % len(original_points)
                    new_points[next_idx] = (
                        original_points[next_idx][0] + normal_x * offset,
                        original_points[next_idx][1] + normal_y * offset,
                    )
                    sel_zone.points = [self._snap_world_point(px, py) for px, py in new_points]
                    self.zone_modified.emit(self.selected_zone_id, sel_zone.points)
                    self.property_panel.hide()
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
            self.property_panel.hide()
            self.update()
        elif self._rect_zoom and self._rect_start:
            self._rect_end = event.position()
            self.update()
        elif self.edit_mode == "draw":
            self.update()

    def mouseReleaseEvent(self, event):
        pos = event.position()
        world_x, world_y = self._screen_to_world(pos.x(), pos.y())
        
        if event.button() == Qt.MouseButton.RightButton and getattr(self, "_box_selecting", False):
            self._box_selecting = False
            if self._box_select_start and self._box_select_end:
                x1 = self._box_select_start.x()
                y1 = self._box_select_start.y()
                x2 = self._box_select_end.x()
                y2 = self._box_select_end.y()
                
                drag_dist = math.hypot(x2 - x1, y2 - y1)
                if drag_dist < 5:
                    hit_zone = self._hit_test_zone(x1, y1, world_x, world_y)
                    if hit_zone:
                        self.edit_operation_started.emit()
                        self.toggle_selected_zone(hit_zone.id)
                else:
                    start_x = min(x1, x2)
                    end_x = max(x1, x2)
                    start_y = min(y1, y2)
                    end_y = max(y1, y2)
                    
                    center_x = (start_x + end_x) / 2
                    center_y = (start_y + end_y) / 2
                    center_wx, center_wy = self._screen_to_world(center_x, center_y)
                    
                    selected_ids = set()
                    for zone in self.geofence_zones:
                        vertex_inside = False
                        for pt in zone.points:
                            sx, sy = self._world_to_screen(pt[0], pt[1])
                            if start_x <= sx <= end_x and start_y <= sy <= end_y:
                                vertex_inside = True
                                break
                        if vertex_inside:
                            selected_ids.add(zone.id)
                            continue
                        
                        if not self._is_open_wall(zone) and self._is_inside_polygon(zone.points, center_wx, center_wy):
                            selected_ids.add(zone.id)
                    
                    if selected_ids:
                        self.edit_operation_started.emit()
                        self.selected_zone_ids = selected_ids
                        self.selected_zone_id = list(selected_ids)[0]
                        self.zone_selected.emit(self.selected_zone_id)
                    else:
                        self.set_selected_zone(None)
                        self.zone_selected.emit("")
            self._box_select_start = None
            self._box_select_end = None
            self.update()
            return

        if self.dragging_anchor_idx is not None:
            self.dragging_anchor_idx = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()
            return

        if self.dragging_zone_id is not None:
            self.dragging_zone_id = None
            self._zone_drag_start_world = None
            self._zone_drag_original_points = None
            self._multi_zone_drag_original_points = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()
            return

        if self._panning:
            self._panning = False
            self._pan_start = None
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

            painter.setPen(QPen(ring, 2 if is_selected_anchor else 1.4))
            painter.setBrush(QColor(15, 23, 42, 230))
            painter.drawEllipse(center_x - 8, center_y - 8, 16, 16)
            painter.setBrush(fill)
            painter.drawEllipse(center_x - 3, center_y - 3, 6, 6)

            label = anchor.get("label", anchor.get("id", "?"))
            painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            painter.setPen(QColor(248, 250, 252))
            painter.drawText(center_x + 10, center_y - 6, label)

    def _zone_visual_style(self, zone, selected=False):
        design_kind = getattr(zone, "design_kind", None)
        object_type = getattr(zone, "object_type", "zone")
        if design_kind == "room" or object_type == "room":
            base_color = str(zone.color).replace("_semi", "") if getattr(zone, "color", "") else "#1D4ED8"
            fill = QColor(base_color if base_color.startswith("#") else "#1D4ED8")
            fill.setAlpha(34 if not selected else 52)
            border = QColor("#7DD3FC")
            label = QColor("#E0F2FE")
            pen_style = Qt.PenStyle.SolidLine
            border_width = 2.0
        elif design_kind == "wall" or object_type == "wall":
            fill = QColor(zone.color if getattr(zone, "color", "").startswith("#") else "#111827")
            fill.setAlpha(225)
            border = QColor("#CBD5E1")
            label = QColor("#F8FAFC")
            pen_style = Qt.PenStyle.SolidLine
            border_width = 3.0
        elif design_kind == "object" or object_type == "object":
            fill = QColor(zone.color if getattr(zone, "color", "").startswith("#") else "#F59E0B")
            fill.setAlpha(110 if not selected else 140)
            border = QColor("#FDBA74")
            label = QColor("#FFEDD5")
            pen_style = Qt.PenStyle.SolidLine
            border_width = 2.0
        elif design_kind == "no_go_rule" or zone.zone_type == "forbidden":
            fill = QColor(zone.color if getattr(zone, "color", "").startswith("#") else "#EF4444")
            fill.setAlpha(72 if not selected else 92)
            border = QColor("#F87171")
            label = QColor("#FEE2E2")
            pen_style = Qt.PenStyle.SolidLine
            border_width = 2.0
        else:
            fill = QColor(zone.color if getattr(zone, "color", "").startswith("#") else "#22C55E")
            fill.setAlpha(58 if not selected else 78)
            border = QColor("#4ADE80")
            label = QColor("#DCFCE7")
            pen_style = Qt.PenStyle.DashLine
            border_width = 2.0

        if selected:
            border = QColor("#FACC15")
            border_width += 1.2

        return fill, border, label, pen_style, border_width

    def _draw_forbidden_hatch(self, painter, poly, color):
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
        thickness = max(0.0, float(getattr(zone, "thickness_m", 0.1)))
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
        thickness = max(0.0, float(getattr(zone, "thickness_m", 0.1)))
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

    def _wall_footprint_path(self, zone):
        """Create one continuous wall footprint with closed, square/mitered corners."""
        points = list(getattr(zone, "points", []) or [])
        thickness = max(0.0, float(getattr(zone, "thickness_m", 0.1)))
        if len(points) < 2 or thickness <= 0.0:
            return QPainterPath()

        mode = getattr(zone, "wall_mode", "free_standing")
        if mode != "boundary_outside" or self._room_by_id(getattr(zone, "host_room_id", None)) is None:
            from PyQt6.QtGui import QPainterPathStroker
            centerline = QPainterPath(QPointF(points[0][0], points[0][1]))
            for x, y in points[1:]:
                centerline.lineTo(x, y)
            stroker = QPainterPathStroker()
            stroker.setWidth(thickness)
            stroker.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
            stroker.setCapStyle(Qt.PenCapStyle.SquareCap)
            return stroker.createStroke(centerline)

        combined = QPainterPath()
        for idx in range(len(points) - 1):
            footprint = self._wall_segment_footprint(zone, points[idx], points[idx + 1])
            if len(footprint) != 4:
                continue
            segment_path = QPainterPath(QPointF(footprint[0][0], footprint[0][1]))
            for x, y in footprint[1:]:
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
            join_path = QPainterPath(QPointF(vertex[0], vertex[1]))
            join_path.lineTo(a[0], a[1])
            join_path.lineTo(intersection[0], intersection[1])
            join_path.lineTo(b[0], b[1])
            join_path.closeSubpath()
            combined = combined.united(join_path)
        return combined.simplified()

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

        # 1. Draw Fusion History Trail (UKF, solid sky blue)
        if len(self.fusion_history) > 1:
            painter.setPen(QPen(QColor(14, 165, 233, 200), 2, Qt.PenStyle.SolidLine))
            for idx in range(len(self.fusion_history) - 1):
                x1, y1 = to_screen(self.fusion_history[idx][0], self.fusion_history[idx][1])
                x2, y2 = to_screen(self.fusion_history[idx + 1][0], self.fusion_history[idx + 1][1])
                painter.drawLine(x1, y1, x2, y2)

        # 2. Draw History Trail (Ranging/Trilateration, dashed orange)
        if len(self.history) > 1:
            painter.setPen(QPen(QColor(249, 115, 22, 180), 2, Qt.PenStyle.DashLine))
            for idx in range(len(self.history) - 1):
                x1, y1 = to_screen(self.history[idx][0], self.history[idx][1])
                x2, y2 = to_screen(self.history[idx + 1][0], self.history[idx + 1][1])
                painter.drawLine(x1, y1, x2, y2)

        # 3-4. Draw active anchors in normal tracking mode. Editor mode redraws
        # anchors later so the dim/grid overlay does not hide newly placed ones.
        if not self.dim_tracking_view:
            self._draw_anchor_layer(painter, to_screen, draw_connections=True)

        # 5. Draw Trilateration Marker (orange circle with crosshair) ONLY when Sensor Fusion is active
        if self.fusion_position is not None:
            tril_x, tril_y = to_screen(self.position["x"], self.position["y"])
            painter.setPen(QPen(QColor(249, 115, 22), 2))
            painter.setBrush(QColor(249, 115, 22, 80))
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

        # Draw the directional arrow
        painter.save()
        painter.translate(active_x, active_y)
        painter.rotate(-active_tag.get("yaw", 0))
        painter.setPen(
            QPen(
                QColor(14, 165, 233),  # Sky blue color border for the arrow
                2,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        gradient = QLinearGradient(0, -12, 0, 10)
        gradient.setColorAt(0, QColor(56, 189, 248))  # sky blue gradient
        gradient.setColorAt(1, QColor(14, 165, 233))
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
        glow_gradient.setColorAt(0, QColor(56, 189, 248, 60))
        glow_gradient.setColorAt(1, QColor(56, 189, 248, 0))
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

        # Draw box selection rect
        if getattr(self, "_box_selecting", False) and self._box_select_start and self._box_select_end:
            rect_x = min(self._box_select_start.x(), self._box_select_end.x())
            rect_y = min(self._box_select_start.y(), self._box_select_end.y())
            rect_w = abs(self._box_select_end.x() - self._box_select_start.x())
            rect_h = abs(self._box_select_end.y() - self._box_select_start.y())
            painter.setPen(QPen(QColor(34, 197, 94), 2, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(34, 197, 94, 30))
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
            if minor_step * scale_px >= 15:
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

            # Adjust major grid multiplier dynamically so lines/labels are spaced by at least 60 pixels
            grid_mult = 1
            while (step * grid_mult) * scale_px < 60:
                if grid_mult == 1:
                    grid_mult = 2
                elif grid_mult == 2:
                    grid_mult = 5
                elif grid_mult == 5:
                    grid_mult = 10
                else:
                    s = str(grid_mult)
                    if s.startswith('1'):
                        grid_mult = 2 * (10 ** (len(s) - 1))
                    elif s.startswith('2'):
                        grid_mult = 5 * (10 ** (len(s) - 1))
                    else:
                        grid_mult = 10 * (10 ** (len(s) - 1))
            
            major_step = step * grid_mult

            # Major grid lines
            painter.setPen(QPen(major_color, 1, Qt.PenStyle.DotLine))
            grid_x = math.floor(view_x1 / major_step) * major_step
            grid_guard = 0
            while grid_x <= view_x2 and grid_guard < 2000:
                screen_x, _ = to_screen(grid_x, 0)
                painter.drawLine(screen_x, margin, screen_x, self.height() - margin)
                grid_x += major_step
                grid_guard += 1
            grid_y = math.floor(view_y1 / major_step) * major_step
            grid_guard = 0
            while grid_y <= view_y2 and grid_guard < 2000:
                _, screen_y = to_screen(0, grid_y)
                painter.drawLine(margin, screen_y, margin + width, screen_y)
                grid_y += major_step
                grid_guard += 1

            # Grid labels
            painter.setFont(QFont("Segoe UI", 9))
            painter.setPen(label_color)
            grid_x = math.floor(view_x1 / major_step) * major_step
            grid_guard = 0
            while grid_x <= view_x2 and grid_guard < 2000:
                screen_x, _ = to_screen(grid_x, 0)
                label = f"{grid_x:.2f}m" if major_step < 1.0 else f"{grid_x:.0f}m"
                painter.drawText(screen_x - 15, self.height() - margin + 16, label)
                grid_x += major_step
                grid_guard += 1
            grid_y = math.floor(view_y1 / major_step) * major_step
            grid_guard = 0
            while grid_y <= view_y2 and grid_guard < 2000:
                _, screen_y = to_screen(0, grid_y)
                label = f"{grid_y:.2f}m" if major_step < 1.0 else f"{grid_y:.0f}m"
                painter.drawText(4, screen_y + 4, label)
                grid_y += major_step
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

        for zone in sorted_zones:
            poly = QPolygonF()
            for pt in zone.points:
                sx, sy = to_screen(pt[0], pt[1])
                poly.append(QPointF(sx, sy))

            object_type = getattr(zone, "object_type", "zone")
            is_selected = (zone.id in self.selected_zone_ids)
            fill_color, border_color, label_color, pen_style, border_width = self._zone_visual_style(zone, is_selected)

            if object_type == "wall":
                world_path = self._wall_footprint_path(zone)
                wall_path = QPainterPath()
                for polygon in world_path.toSubpathPolygons():
                    if polygon.isEmpty():
                        continue
                    screen_poly = QPolygonF()
                    for point in polygon:
                        sx, sy = to_screen(point.x(), point.y())
                        screen_poly.append(QPointF(sx, sy))
                    wall_path.addPolygon(screen_poly)
                painter.setPen(QPen(border_color, 1.4, Qt.PenStyle.SolidLine))
                painter.setBrush(QBrush(fill_color))
                painter.drawPath(wall_path)
            else:
                painter.setPen(QPen(border_color, border_width, pen_style))
                painter.setBrush(QBrush(fill_color))
                painter.drawPolygon(poly)
                if object_type == "room" and zone.id in self.active_room_ids:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(34, 197, 94, 38))
                    painter.drawPolygon(poly)
                if object_type == "object":
                    self._draw_object_symbol(painter, zone, poly)
                if object_type == "zone" and zone.zone_type == "forbidden":
                    self._draw_forbidden_hatch(painter, poly, border_color)
                if object_type == "room" and getattr(zone, "name", ""):
                    label_x, label_y = self._polygon_label_center(zone.points)
                    text_x, text_y = to_screen(label_x, label_y)
                    label = str(zone.name)
                    painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
                    text_rect = painter.fontMetrics().boundingRect(label)
                    text_rect.moveCenter(QPoint(int(text_x), int(text_y)))
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(15, 23, 42, 190))
                    painter.drawRoundedRect(text_rect.adjusted(-6, -3, 6, 3), 5, 5)
                    painter.setPen(QColor(248, 250, 252))
                    painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, label)

            active_edge_idx = None
            if is_selected and self.selected_edge_idx is not None:
                active_edge_idx = self.selected_edge_idx
            elif self.hovered_edge and self.hovered_edge[0] == zone.id:
                active_edge_idx = self.hovered_edge[1]

            if active_edge_idx is not None and len(zone.points) >= 2:
                edge_start = zone.points[active_edge_idx]
                edge_end = zone.points[active_edge_idx + 1] if self._is_open_wall(zone) else zone.points[(active_edge_idx + 1) % len(zone.points)]
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

            # Draw room coordinate origin marker (red dot at chosen corner)
            room_origins = getattr(self, "_room_origins", {})
            if zone.id in room_origins and object_type == "room":
                ox, oy = room_origins[zone.id]
                sx, sy = to_screen(ox, oy)
                painter.setPen(QPen(QColor(239, 68, 68), 2))
                painter.setBrush(QBrush(QColor(239, 68, 68)))
                painter.drawEllipse(int(sx - 6), int(sy - 6), 12, 12)
                painter.setPen(QPen(QColor(255, 255, 255), 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(int(sx - 7), int(sy - 7), 14, 14)

            if object_type == "room" and zone.id == self._origin_pick_room_id:
                for vertex_idx, point in enumerate(zone.points):
                    sx, sy = to_screen(point[0], point[1])
                    hovered = vertex_idx == self._origin_pick_hover_idx
                    painter.setPen(QPen(QColor(254, 202, 202) if hovered else QColor(248, 113, 113), 2))
                    painter.setBrush(QColor(239, 68, 68, 235) if hovered else QColor(127, 29, 29, 185))
                    radius = 9 if hovered else 6
                    painter.drawEllipse(int(sx - radius), int(sy - radius), radius * 2, radius * 2)
                    if hovered:
                        painter.setPen(QPen(QColor(255, 255, 255), 1))
                        painter.drawText(int(sx + 11), int(sy - 9), f"Origin V{vertex_idx + 1}")

        # 9. Draw active drawing path
        if self.edit_mode == "draw" and self.current_draw_points:
            draw_colors = {
                "room": QColor(125, 211, 252, 220),
                "wall": QColor(203, 213, 225, 230),
                "object": QColor(245, 158, 11, 220),
                "zone": QColor(74, 222, 128, 225),
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

            if not handled_circle_preview and len(self.current_draw_points) > 1:
                for idx in range(len(self.current_draw_points) - 1):
                    x1, y1 = to_screen(self.current_draw_points[idx][0], self.current_draw_points[idx][1])
                    x2, y2 = to_screen(self.current_draw_points[idx+1][0], self.current_draw_points[idx+1][1])
                    painter.drawLine(x1, y1, x2, y2)

            if not handled_circle_preview and self.mouse_world_pos:
                last_pt = self.current_draw_points[-1]
                x1, y1 = to_screen(last_pt[0], last_pt[1])
                x2, y2 = to_screen(self.mouse_world_pos[0], self.mouse_world_pos[1])
                preview_color = QColor(active_color)
                preview_color.setAlpha(145)
                painter.setPen(QPen(preview_color, 1.5, Qt.PenStyle.DashLine))
                painter.drawLine(x1, y1, x2, y2)

            if not handled_circle_preview:
                for pt in self.current_draw_points:
                    sx, sy = to_screen(pt[0], pt[1])
                    painter.setPen(QPen(QColor(255, 255, 255), 1.5))
                    painter.setBrush(active_color)
                    painter.drawEllipse(int(sx - 4), int(sy - 4), 8, 8)

                dimension_points = list(self.current_draw_points)
                if self.mouse_world_pos:
                    if not dimension_points or dimension_points[-1] != self.mouse_world_pos:
                        dimension_points.append(self.mouse_world_pos)
                painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
                for idx in range(max(0, len(dimension_points) - 1)):
                    pt1 = dimension_points[idx]
                    pt2 = dimension_points[idx + 1]
                    length = math.hypot(pt2[0] - pt1[0], pt2[1] - pt1[1])
                    if length <= 1e-6:
                        continue
                    label = f"{length:.2f} m"
                    mx = (pt1[0] + pt2[0]) / 2.0
                    my = (pt1[1] + pt2[1]) / 2.0
                    smx, smy = to_screen(mx, my)
                    text_rect = painter.fontMetrics().boundingRect(label)
                    text_rect.translate(int(smx - text_rect.width() / 2), int(smy - text_rect.height() / 2))
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(15, 23, 42, 220))
                    painter.drawRoundedRect(text_rect.adjusted(-4, -2, 4, 2), 4, 4)
                    painter.setPen(QColor(226, 232, 240))
                    painter.drawText(text_rect.x(), text_rect.y() + text_rect.height() - 3, label)

        if self.dim_tracking_view:
            self._draw_anchor_layer(painter, to_screen, draw_connections=False)

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
        self.update()

    def _on_panel_property_changed(self, prop_name, value):
        if not self.selected_zone_ids:
            return

        self.edit_operation_started.emit()
        for zid in self.selected_zone_ids:
            zone = next((z for z in self.geofence_zones if z.id == zid), None)
            if not zone:
                continue

            if prop_name == "name":
                zone.name = str(value)
            elif prop_name == "color":
                zone.color = str(value)
            elif prop_name == "height":
                zone.min_z = 0.0
                zone.max_z = float(value)
            elif prop_name == "thickness_m":
                zone.thickness_m = float(value)
            elif prop_name == "speed_limit":
                zone.speed_limit = float(value)

            self.zone_properties_updated.emit(zone.id, {prop_name: value})
        self.update()

    def _on_panel_edge_changed(self, edge_idx, length, angle_deg):
        if not self.selected_zone_id:
            return
        zone = next((z for z in self.geofence_zones if z.id == self.selected_zone_id), None)
        if not zone or len(zone.points) < 2:
            return

        self.edit_operation_started.emit()
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

    def _close_property_panel(self):
        self.set_selected_zone(None)

    def show_property_panel(self, zone_id):
        self.property_panel.reset_user_position()
        self.property_panel.hide()

    def update_property_panel_position(self):
        self.property_panel.hide()

