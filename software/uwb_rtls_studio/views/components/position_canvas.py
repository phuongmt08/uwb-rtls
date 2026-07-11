"""
===============================================================================
  UWB RTLS Studio - Position Canvas Component
===============================================================================
"""
import math
import time
from copy import deepcopy
from PyQt6.QtCore import Qt, QEvent, QTimer, pyqtSignal, QPointF, QPoint, QRect, QRectF
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
    zones_undo_restore_requested = pyqtSignal(list)
    room_origin_vertex_picked = pyqtSignal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 300)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.position = {"x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0, "error": 0.0}
        self.has_position = False
        self.fusion_position = None
        self.anchors = []
        self.anchor_mask = 0
        self.anchor_mask_valid = False
        self.anchor_telemetry = {}
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
        self._selection_box_active = False
        self._selection_box_start = None
        self._selection_box_end = None

        # Geofencing properties
        self.geofence_zones = []
        self.edit_mode = "navigate"  # "navigate" | "draw" | "edit_vertices" | "pick_zone"
        self.draw_object_type = "zone"  # "zone" | "room" | "wall" | "anchor"
        self.current_draw_points = []
        self.selected_zone_id = None
        self.selected_zone_ids = set()
        self.selected_vertex_idx = None
        self.selected_edge_idx = None
        self.selected_anchor_idx = None
        self.dragging_anchor_idx = None
        self._anchor_template = None
        self.hovered_edge = None
        self.hovered_zone_id = None
        self._edge_drag_start_world = None
        self._edge_drag_original_points = None
        self._zone_drag_start_world = None
        self._zone_drag_original_points = None
        self._zone_drag_active = False
        self._snap_preview_edges = None
        self._selection_box_active = False
        self._selection_box_start = None
        self._selection_box_end = None
        self.mouse_world_pos = (0.0, 0.0)
        self.dim_tracking_view = False
        self._room_origins = {}
        self._origin_pick_room_id = None
        self._origin_pick_hover_idx = None
        self._probe_dimension_mode = False
        self._probe_dimension_start = None
        self._probe_dimension_end = None

        # Snap & preview grid settings
        self._grid_spacing = GRID_SPACING_M  # meters (configured in config.py)
        self._grid_subdivisions = 5
        self._show_scale_bar = True
        self._show_mouse_coords = True
        self._show_tracking_grid = True
        self.overlay_detail_mode = True
        self._tracking_grid_spacing = 1.0
        self._tracking_grid_subdivisions = 5
        self.is_developer_mode = False
        self.snapped_grid_pt = None
        self.preview_25d = False
        self.draw_object_shape = "polygon"
        self._object_draw_center = None
        self.active_room_ids = set()
        self._undo_stack = []
        self._target_render_fps = 60
        self._render_dirty = False
        self._render_timer = QTimer(self)
        self._render_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._render_timer.setInterval(max(1, int(1000 / self._target_render_fps)))
        self._render_timer.timeout.connect(self._flush_render_update)
        self._dimension_hitboxes = []
        self._label_hitboxes = []
        self._label_drag_zone_id = None
        self._label_drag_start_world = None
        self._label_drag_original_offset = (0.0, 0.0)
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
            self._on_panel_edge_changed(edge_idx, length, angle_deg)
        finally:
            self._dimension_edit_committing = False
            self.setFocus(Qt.FocusReason.OtherFocusReason)
            self.update()

    def set_active_room_ids(self, room_ids):
        self.active_room_ids = {str(room_id) for room_id in (room_ids or []) if room_id}
        self.update()

    def clear_undo_history(self):
        self._undo_stack.clear()

    def _push_undo_state(self):
        self._undo_stack.append(
            {
                "anchors": deepcopy(self.anchors),
                "zones": [deepcopy(zone.to_dict()) for zone in self.geofence_zones],
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
        zone_snapshots = deepcopy(state.get("zones", []))
        if zone_snapshots and isinstance(zone_snapshots[0], dict):
            self.zones_undo_restore_requested.emit(zone_snapshots)
        else:
            zone_points = dict(zone_snapshots)
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
        self.hovered_zone_id = None
        self._edge_drag_start_world = None
        self._edge_drag_original_points = None
        self._zone_drag_start_world = None
        self._zone_drag_original_points = None
        self._zone_drag_active = False
        self._snap_preview_edges = None
        self._label_drag_zone_id = None
        self._label_drag_start_world = None
        self._label_drag_original_offset = (0.0, 0.0)
        self._selection_box_active = False
        self._selection_box_start = None
        self._selection_box_end = None
        if mode == "navigate":
            self.setCursor(Qt.CursorShape.ArrowCursor)
        elif mode == "draw":
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif mode == "edit_vertices":
            self.setCursor(Qt.CursorShape.ArrowCursor)
        elif mode == "pick_zone":
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        elif mode == "insert_vertex":
            self.setCursor(Qt.CursorShape.CrossCursor)
        self.update()

    def toggle_probe_dimension_mode(self):
        self._probe_dimension_mode = not self._probe_dimension_mode
        if self._probe_dimension_mode:
            self._probe_dimension_start = None
            self._probe_dimension_end = None
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self._probe_dimension_start = None
            self._probe_dimension_end = None
            if self.edit_mode == "draw":
                self.setCursor(Qt.CursorShape.CrossCursor)
            elif self.edit_mode == "pick_zone":
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()
        return self._probe_dimension_mode

    def clear_probe_dimension_measurement(self):
        self._probe_dimension_mode = False
        self._probe_dimension_start = None
        self._probe_dimension_end = None
        if self.edit_mode == "draw":
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif self.edit_mode == "pick_zone":
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def _normalized_probe_endpoint(self, start_pt, end_pt):
        if start_pt is None or end_pt is None:
            return end_pt
        dx = end_pt[0] - start_pt[0]
        dy = end_pt[1] - start_pt[1]
        if abs(dx) >= abs(dy):
            return (end_pt[0], start_pt[1])
        return (start_pt[0], end_pt[1])

    def _probe_dimension_payload(self):
        start_pt = self._probe_dimension_start
        end_pt = self._probe_dimension_end
        if start_pt is None or end_pt is None:
            return None
        end_pt = self._normalized_probe_endpoint(start_pt, end_pt)
        dx = end_pt[0] - start_pt[0]
        dy = end_pt[1] - start_pt[1]
        length = abs(dx) if abs(dx) >= abs(dy) else abs(dy)
        if length <= 1e-9:
            return None
        axis = "x" if abs(dx) >= abs(dy) else "y"
        return {
            "start": start_pt,
            "end": end_pt,
            "axis": axis,
            "length": length,
        }

    def set_selected_zone(self, zone_id):
        self.selected_zone_id = zone_id
        self.selected_zone_ids = {zone_id} if zone_id else set()
        if zone_id:
            self.selected_anchor_idx = None
            self.anchor_selected.emit(-1)
        if zone_id and self.property_panel_enabled:
            self.show_property_panel(zone_id)
        else:
            self.property_panel.hide()
        self.update()

    def set_selected_zones(self, zone_ids, *, primary_id=None, emit_anchor_clear=True):
        zone_ids = [zone_id for zone_id in zone_ids if zone_id]
        self.selected_zone_ids = set(zone_ids)
        self.selected_zone_id = primary_id or (zone_ids[-1] if zone_ids else None)
        if zone_ids and emit_anchor_clear:
            self.selected_anchor_idx = None
            self.anchor_selected.emit(-1)
        if self.selected_zone_id and len(self.selected_zone_ids) == 1 and self.property_panel_enabled:
            self.show_property_panel(self.selected_zone_id)
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

    def set_overlay_detail_mode(self, enabled: bool):
        self.overlay_detail_mode = bool(enabled)
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

    def _zones_for_hit_testing(self):
        layer_order = {"room": 0, "wall": 1, "object": 2, "zone": 3}
        return sorted(
            self.geofence_zones,
            key=lambda z: layer_order.get(getattr(z, "object_type", "zone"), 3),
            reverse=True,
        )

    def _label_at_screen_pos(self, screen_x, screen_y):
        point = QPoint(int(screen_x), int(screen_y))
        for rect, zone_id in reversed(self._label_hitboxes):
            if rect.contains(point):
                return zone_id
        return None

    def _zone_contains_screen_pos(self, zone, screen_x, screen_y, world_x=None, world_y=None):
        if zone is None:
            return False
        if world_x is None or world_y is None:
            world_x, world_y = self._screen_to_world(screen_x, screen_y)
        if getattr(zone, "object_type", "zone") == "wall":
            path = self._wall_footprint_path(zone)
            return not path.isEmpty() and path.contains(QPointF(world_x, world_y))
        return self._is_inside_polygon(zone.points, world_x, world_y)

    def _zone_vertex_at_screen_pos(self, zone, screen_x, screen_y, threshold_px=9):
        if zone is None:
            return None
        for idx, pt in enumerate(zone.points):
            if self._is_close(pt, screen_x, screen_y, threshold_px=threshold_px):
                return idx
        return None

    def _zone_edge_at_screen_pos(self, zone, screen_x, screen_y, threshold_px=7):
        if zone is None or len(zone.points) < 2:
            return None
        for edge_idx in range(len(zone.points)):
            pt1 = zone.points[edge_idx]
            pt2 = zone.points[(edge_idx + 1) % len(zone.points)]
            sx1, sy1 = self._world_to_screen(pt1[0], pt1[1])
            sx2, sy2 = self._world_to_screen(pt2[0], pt2[1])
            distance_px, t = self._distance_to_segment_px((screen_x, screen_y), (sx1, sy1), (sx2, sy2))
            if distance_px <= threshold_px and 0.05 <= t <= 0.95:
                return edge_idx
        return None

    def _zone_at_screen_pos(self, screen_x, screen_y, world_x=None, world_y=None):
        if world_x is None or world_y is None:
            world_x, world_y = self._screen_to_world(screen_x, screen_y)
        for zone in self._zones_for_hit_testing():
            object_type = getattr(zone, "object_type", "zone")
            if object_type == "wall":
                path = self._wall_footprint_path(zone)
                if not path.isEmpty() and path.contains(QPointF(world_x, world_y)):
                    return zone
            elif self._is_inside_polygon(zone.points, world_x, world_y):
                return zone
        return None

    def _begin_zone_drag(self, zone, start_world, *, emit_selection=True):
        if zone.id not in self.selected_zone_ids:
            self.set_selected_zone(zone.id)
            if emit_selection:
                self.zone_selected.emit(zone.id)
        elif emit_selection:
            self.zone_selected.emit(zone.id)
        self._push_undo_state()
        self._zone_drag_start_world = start_world
        drag_ids = self.selected_zone_ids or {zone.id}
        self._zone_drag_original_points = {
            item.id: list(item.points)
            for item in self.geofence_zones
            if item.id in drag_ids
        }
        self._zone_drag_active = False
        self._snap_preview_edges = None
        self._selection_box_active = False
        self._selection_box_start = None
        self._selection_box_end = None
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.update()

    def _zone_screen_bounds(self, zone):
        points = list(getattr(zone, "points", []) or [])
        if not points:
            return QRectF()
        xs = []
        ys = []
        if getattr(zone, "object_type", "zone") == "wall":
            path = self._wall_footprint_screen_path(zone)
            if not path.isEmpty():
                return path.boundingRect()
        for x, y in points:
            sx, sy = self._world_to_screen(x, y)
            xs.append(float(sx))
            ys.append(float(sy))
        return QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    def _zones_in_selection_box(self, rect):
        selected = []
        for zone in self.geofence_zones:
            bounds = self._zone_screen_bounds(zone)
            if not bounds.isNull() and rect.intersects(bounds):
                selected.append(zone.id)
        return selected

    def _begin_selection_box(self, pos):
        self._selection_box_active = True
        self._selection_box_start = QPointF(pos)
        self._selection_box_end = QPointF(pos)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.update()

    def _selection_box_rect(self):
        if self._selection_box_start is None or self._selection_box_end is None:
            return QRectF()
        x1 = self._selection_box_start.x()
        y1 = self._selection_box_start.y()
        x2 = self._selection_box_end.x()
        y2 = self._selection_box_end.y()
        return QRectF(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))

    def _finish_selection_box(self):
        rect = self._selection_box_rect()
        self._selection_box_active = False
        self._selection_box_start = None
        self._selection_box_end = None
        if rect.width() < 4 or rect.height() < 4:
            self.update()
            return False
        selected_ids = self._zones_in_selection_box(rect)
        self.set_selected_zones(selected_ids)
        self.zone_selected.emit(self.selected_zone_id or "")
        self.setCursor(Qt.CursorShape.ArrowCursor)
        return bool(selected_ids)
    @staticmethod
    def _translated_points(points, dx, dy):
        return [(round(float(x) + dx, 6), round(float(y) + dy, 6)) for x, y in points]

    def _is_inside_polygon(self, poly_points, wx, wy):
        poly = QPolygonF()
        for pt in poly_points:
            poly.append(QPointF(pt[0], pt[1]))
        return poly.containsPoint(QPointF(wx, wy), Qt.FillRule.OddEvenFill)

    @staticmethod
    def _distance_to_segment_world(point, start, end):
        px, py = point
        x1, y1 = start
        x2, y2 = end
        dx = x2 - x1
        dy = y2 - y1
        seg_len_sq = dx * dx + dy * dy
        if seg_len_sq <= 1e-12:
            return math.hypot(px - x1, py - y1)
        t = ((px - x1) * dx + (py - y1) * dy) / seg_len_sq
        t = max(0.0, min(1.0, t))
        proj_x = x1 + t * dx
        proj_y = y1 + t * dy
        return math.hypot(px - proj_x, py - proj_y)

    def _anchor_room_for_limit(self, anchor):
        room_id = str(anchor.get("room_id") or anchor.get("zone_id") or "")
        if not room_id:
            return None
        return self._room_by_id(room_id)

    def _anchor_can_move_to(self, anchor, world_x, world_y):
        room = self._anchor_room_for_limit(anchor)
        if room is None:
            return True
        points = list(getattr(room, "points", []) or [])
        if len(points) < 3:
            return True
        if self._is_inside_polygon(points, world_x, world_y):
            return True
        edge_tolerance_m = max(0.01, self._snap_step() * 0.25)
        for idx, start in enumerate(points):
            end = points[(idx + 1) % len(points)]
            if self._distance_to_segment_world((world_x, world_y), start, end) <= edge_tolerance_m:
                return True
        return False

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
        thickness = max(0.0, float(getattr(zone, "thickness_m", getattr(zone, "thickness", 0.1))))
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
        thickness = max(0.0, float(getattr(zone, "thickness_m", getattr(zone, "thickness", 0.1))))
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

    @staticmethod
    def _polygon_area_abs(points):
        if len(points) < 3:
            return 0.0
        area = 0.0
        for idx, (x1, y1) in enumerate(points):
            x2, y2 = points[(idx + 1) % len(points)]
            area += (x1 * y2) - (x2 * y1)
        return abs(area) * 0.5

    def _wall_uses_polygon_footprint(self, zone):
        points = list(getattr(zone, "points", []) or [])
        if len(points) < 4:
            return False
        first_x, first_y = points[0]
        last_x, last_y = points[-1]
        is_closed_loop = math.hypot(last_x - first_x, last_y - first_y) <= 1e-6
        if not is_closed_loop or getattr(zone, "wall_mode", "free_standing") == "boundary_outside":
            return False
        core_points = points[:-1]
        return self._polygon_area_abs(core_points) > 1e-6

    def _wall_polygon_footprint_path(self, points):
        footprint_points = list(points or [])
        if len(footprint_points) >= 2 and math.hypot(footprint_points[-1][0] - footprint_points[0][0], footprint_points[-1][1] - footprint_points[0][1]) <= 1e-6:
            footprint_points = footprint_points[:-1]
        path = QPainterPath(QPointF(footprint_points[0][0], footprint_points[0][1]))
        for x, y in footprint_points[1:]:
            path.lineTo(x, y)
        path.closeSubpath()
        return path


    def _wall_footprint_path(self, zone):
        """Create one continuous wall footprint in world coordinates."""
        points = list(getattr(zone, "points", []) or [])
        if len(points) < 2:
            return QPainterPath()
        if self._wall_uses_polygon_footprint(zone):
            return self._wall_polygon_footprint_path(points)

        thickness = max(0.0, float(getattr(zone, "thickness_m", getattr(zone, "thickness", 0.1))))
        if thickness <= 0.0:
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

    def _wall_footprint_screen_path(self, zone, to_screen=None):
        world_path = self._wall_footprint_path(zone)
        if world_path.isEmpty():
            return QPainterPath()
        to_screen = to_screen or self._world_to_screen
        screen_path = QPainterPath()
        for polygon in world_path.toSubpathPolygons():
            if polygon.isEmpty():
                continue
            screen_poly = QPolygonF()
            for point in polygon:
                sx, sy = to_screen(point.x(), point.y())
                screen_poly.append(QPointF(sx, sy))
            screen_path.addPolygon(screen_poly)
        return screen_path
    def update_position(self, position):
        current_time = time.time()
        source = position.get("source", "ranging")
        last_update = self._last_update_by_source.get(source, 0.0)
        if current_time - last_update < self.update_interval:
            return

        self.last_update_time = current_time
        self._last_update_by_source[source] = current_time
        self.has_position = True
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

    def set_anchor_telemetry(self, mask=None, anchors=None, valid=True):
        """Update live anchor selection and per-anchor distance/weight data."""
        self.anchor_mask_valid = bool(valid and mask is not None and mask != "")
        self.anchor_mask = int(mask or 0) if self.anchor_mask_valid else 0
        self.anchor_telemetry = {
            int(item.get("anchor_id", 0)): {
                "distance_mm": int(item.get("distance_mm", 0) or 0),
                "weight": item.get("weight"),
            }
            for item in (anchors or [])
            if int(item.get("anchor_id", 0) or 0) > 0
        }
        self.update()

    def clear_anchor_telemetry(self):
        self.anchor_mask = 0
        self.anchor_mask_valid = False
        self.anchor_telemetry = {}
        self.update()

    def _anchor_is_mask_selected(self, anchor):
        anchor_id = self._coerce_int_id(anchor.get("anchor_id"), 0)
        return bool(
            self.anchor_mask_valid
            and 1 <= anchor_id <= 32
            and self.anchor_mask & (1 << (anchor_id - 1))
        )
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
        self.selected_zone_ids = set()
        self.property_panel.hide()
        self.selected_anchor_idx = anchor_idx
        self.anchor_selected.emit(anchor_idx)
        self.update()

    def add_or_move_anchor_at(self, world_x, world_y):
        if self.selected_anchor_idx is not None and self.selected_anchor_idx < len(self.anchors):
            anchor = self.anchors[self.selected_anchor_idx]
            if not self._anchor_can_move_to(anchor, world_x, world_y):
                return
            self._push_undo_state()
            anchor["x"] = world_x
            anchor["y"] = world_y
            anchor["placed"] = True
            anchor["sync_state"] = "draft"
        else:
            used_ids = {self._coerce_int_id(anchor.get("anchor_id"), idx) for idx, anchor in enumerate(self.anchors)}
            template = self._anchor_template or {}
            if not self._anchor_can_move_to(template, world_x, world_y):
                return
            self._push_undo_state()
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
        self.has_position = False
        self.update()

    def auto_fit(self):
        pts_x = [a["x"] for a in self.anchors]
        pts_y = [a["y"] for a in self.anchors]
        if self.has_position:
            pts_x.append(self.position["x"])
            pts_y.append(self.position["y"])
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
        
        snapped_x, snapped_y = self._snap_world_point(world_x, world_y)
        if self.edit_mode == "draw" and self.draw_object_type == "wall":
            if not self.current_draw_points or self.current_draw_points[-1] != (snapped_x, snapped_y):
                self.current_draw_points.append((snapped_x, snapped_y))
            if len(self.current_draw_points) >= 2:
                self._push_undo_state()
                pts = list(self.current_draw_points)
                self.current_draw_points.clear()
                self.polygon_completed.emit(pts)
            self.update()
            return

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
                if not path.isEmpty() and path.contains(QPointF(world_x, world_y)):
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

        if self.edit_mode == "pick_zone" and event.button() == Qt.MouseButton.LeftButton:
            hit_anchor_idx = self._anchor_at_screen_pos(pos.x(), pos.y())
            if hit_anchor_idx is not None:
                self.set_selected_anchor(hit_anchor_idx)
                event.accept()
                return
            zone = self._zone_at_screen_pos(pos.x(), pos.y(), world_x, world_y)
            if zone is not None:
                self.set_selected_zone(zone.id)
                self.zone_selected.emit(zone.id)
                self.update()
                event.accept()
                return
            if self.selected_zone_id:
                self.set_selected_zone(None)
                self.zone_selected.emit("")
            if self.selected_anchor_idx is not None:
                self.set_selected_anchor(None)
            self.update()
            event.accept()
            return
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
                    self._begin_zone_drag(zone, (snapped_x, snapped_y))
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
            # Click back to the first point closes polygons, and closes wall loops by adding the last segment.
            if self.current_draw_points and self._is_close(self.current_draw_points[0], pos.x(), pos.y()):
                if self.draw_object_type == "wall":
                    if len(self.current_draw_points) >= 2:
                        self._push_undo_state()
                        pts = list(self.current_draw_points)
                        if pts[-1] != pts[0]:
                            pts.append(pts[0])
                        self.current_draw_points.clear()
                        self.polygon_completed.emit(pts)
                    self.update()
                    return
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
            if self.draw_object_type == "wall" and len(self.current_draw_points) >= 2:
                self._push_undo_state()
                pts = list(self.current_draw_points)
                self.current_draw_points.clear()
                self._object_draw_center = None
                self.polygon_completed.emit(pts)
                self.update()
                return
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

            label_zone_id = self._label_at_screen_pos(pos.x(), pos.y())
            if label_zone_id is not None:
                zone = next((z for z in self.geofence_zones if z.id == label_zone_id), None)
                if zone is not None:
                    self.set_selected_zone(zone.id)
                    self.zone_selected.emit(zone.id)
                    self._push_undo_state()
                    self._label_drag_zone_id = zone.id
                    self._label_drag_start_world = (world_x, world_y)
                    self._label_drag_original_offset = (
                        float(getattr(zone, "label_offset_x", 0.0)),
                        float(getattr(zone, "label_offset_y", 0.0)),
                    )
                    self._selection_box_active = False
                    self._selection_box_start = None
                    self._selection_box_end = None
                    self.setCursor(Qt.CursorShape.SizeAllCursor)
                    self.update()
                    return

            # The selected object owns its body drag. This prevents overlapping wall
            # footprints from stealing a move that should apply only to the room/object.
            if self.selected_zone_id:
                sel_zone = next((z for z in self.geofence_zones if z.id == self.selected_zone_id), None)
                if sel_zone:
                    vertex_idx = self._zone_vertex_at_screen_pos(sel_zone, pos.x(), pos.y())
                    if vertex_idx is not None:
                        self._push_undo_state()
                        self.selected_vertex_idx = vertex_idx
                        self.setCursor(Qt.CursorShape.SizeAllCursor)
                        return
                    edge_idx = self._zone_edge_at_screen_pos(sel_zone, pos.x(), pos.y())
                    if edge_idx is not None:
                        self._push_undo_state()
                        self.selected_edge_idx = edge_idx
                        self._edge_drag_start_world = (snapped_x, snapped_y)
                        self._edge_drag_original_points = list(sel_zone.points)
                        self.setCursor(Qt.CursorShape.SizeAllCursor)
                        self.update()
                        return
                    if self._zone_contains_screen_pos(sel_zone, pos.x(), pos.y(), world_x, world_y):
                        self._begin_zone_drag(sel_zone, (snapped_x, snapped_y), emit_selection=False)
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
                    if not path.isEmpty() and path.contains(QPointF(world_x, world_y)):
                        self._begin_zone_drag(zone, (snapped_x, snapped_y))
                        return
                else:
                    if self._is_inside_polygon(zone.points, world_x, world_y):
                        self._begin_zone_drag(zone, (snapped_x, snapped_y))
                        return

            self._begin_selection_box(event.position())
            return

        # Fall back to standard view navigation. Left-drag is reserved for box select.
        if event.button() == Qt.MouseButton.LeftButton and self.edit_mode not in {"draw", "insert_vertex", "pick_zone"}:
            self._begin_selection_box(event.position())
            return
        if event.button() == Qt.MouseButton.MiddleButton:
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
        if self._probe_dimension_mode:
            self.mouse_world_pos = (snapped_x, snapped_y)
            self.snapped_grid_pt = (snapped_x, snapped_y)
            if self._probe_dimension_start is not None:
                self._probe_dimension_end = self._normalized_probe_endpoint(
                    self._probe_dimension_start,
                    (snapped_x, snapped_y),
                )
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.update()
            return
        if self.edit_mode == "pick_zone":
            self.mouse_world_pos = (world_x, world_y)
            self.snapped_grid_pt = None
            hovered_zone = self._zone_at_screen_pos(pos.x(), pos.y(), world_x, world_y)
            self.hovered_zone_id = hovered_zone.id if hovered_zone is not None else None
            self.setCursor(Qt.CursorShape.PointingHandCursor if hovered_zone is not None else Qt.CursorShape.ArrowCursor)
            self.update()
            return
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

        if self._selection_box_active:
            self._selection_box_end = event.position()
            self.update()
            return

        if self._label_drag_zone_id is not None and self._label_drag_start_world is not None:
            zone = next((z for z in self.geofence_zones if z.id == self._label_drag_zone_id), None)
            if zone is not None:
                dx = world_x - self._label_drag_start_world[0]
                dy = world_y - self._label_drag_start_world[1]
                zone.label_offset_x = round(self._label_drag_original_offset[0] + dx, 6)
                zone.label_offset_y = round(self._label_drag_original_offset[1] + dy, 6)
                self.update()
            return

        if self.dragging_anchor_idx is not None and self.dragging_anchor_idx < len(self.anchors):
            anchor = self.anchors[self.dragging_anchor_idx]
            if not self._anchor_can_move_to(anchor, snapped_x, snapped_y):
                return
            anchor["x"] = snapped_x
            anchor["y"] = snapped_y
            anchor["placed"] = True
            anchor["sync_state"] = "draft"
            self.selected_anchor_idx = self.dragging_anchor_idx
            self.update()
            return

        if self._zone_drag_start_world and self._zone_drag_original_points:
            dx = snapped_x - self._zone_drag_start_world[0]
            dy = snapped_y - self._zone_drag_start_world[1]
            originals = self._zone_drag_original_points
            if isinstance(originals, dict):
                changed_primary = None
                for zone in self.geofence_zones:
                    if zone.id not in originals:
                        continue
                    zone.points = self._translated_points(originals[zone.id], dx, dy)
                    if zone.id == self.selected_zone_id:
                        changed_primary = zone
                self._zone_drag_active = True
                self._snap_preview_edges = None
                self.update()
                return
            sel_zone = next((z for z in self.geofence_zones if z.id == self.selected_zone_id), None)
            if sel_zone is not None:
                sel_zone.points = self._translated_points(originals, dx, dy)
                self._zone_drag_active = True
                self._snap_preview_edges = None
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
                    self.hovered_zone_id = self.hovered_edge[0]
                    self.setCursor(Qt.CursorShape.SizeAllCursor)
                else:
                    hovered_zone = self._zone_at_screen_pos(pos.x(), pos.y(), world_x, world_y)
                    self.hovered_zone_id = hovered_zone.id if hovered_zone is not None else None
                    self.setCursor(Qt.CursorShape.PointingHandCursor if hovered_zone is not None else Qt.CursorShape.ArrowCursor)

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
        if event.button() == Qt.MouseButton.LeftButton and self._selection_box_active:
            handled = self._finish_selection_box()
            if not handled:
                self.set_selected_zone(None)
                self.zone_selected.emit("")
                if self.selected_anchor_idx is not None:
                    self.set_selected_anchor(None)
            self.update()
            return

        if self.dragging_anchor_idx is not None:
            self._emit_anchor_layout_edited()
            self.dragging_anchor_idx = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()
            return

        if self._label_drag_zone_id is not None:
            zone = next((z for z in self.geofence_zones if z.id == self._label_drag_zone_id), None)
            if zone is not None:
                self.zone_properties_updated.emit(
                    zone.id,
                    {
                        "label_offset_x": float(getattr(zone, "label_offset_x", 0.0)),
                        "label_offset_y": float(getattr(zone, "label_offset_y", 0.0)),
                    },
                )
                if not self.property_panel.isHidden():
                    self.property_panel.load_zone(zone)
            self._label_drag_zone_id = None
            self._label_drag_start_world = None
            self._label_drag_original_offset = (0.0, 0.0)
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()
            return

        if self._zone_drag_start_world is not None:
            originals = self._zone_drag_original_points
            if self._zone_drag_active:
                if isinstance(originals, dict):
                    for zone in self.geofence_zones:
                        if zone.id in originals:
                            self.zone_modified.emit(zone.id, zone.points)
                elif self.selected_zone_id:
                    zone = next((z for z in self.geofence_zones if z.id == self.selected_zone_id), None)
                    if zone is not None:
                        self.zone_modified.emit(zone.id, zone.points)
                selected = next((z for z in self.geofence_zones if z.id == self.selected_zone_id), None)
                if selected is not None and not self.property_panel.isHidden():
                    self.property_panel.load_zone(selected)
                self.update_property_panel_position()
            self._zone_drag_start_world = None
            self._zone_drag_original_points = None
            self._zone_drag_active = False
            self._snap_preview_edges = None
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

        if event.button() == Qt.MouseButton.MiddleButton and self._dragging:
            self._dragging = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()
            return

        if event.button() == Qt.MouseButton.RightButton and self._rect_zoom:
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
                is_mask_selected = self._anchor_is_mask_selected(anchor)
                anchor_x, anchor_y = to_screen(anchor["x"], anchor["y"])
                color = QColor(34, 211, 238, 180) if is_mask_selected else QColor(99, 102, 241, 24)
                painter.setPen(QPen(color, 2 if is_mask_selected else 1, Qt.PenStyle.DashLine))
                painter.drawLine(pos_x, pos_y, anchor_x, anchor_y)

        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        for idx, anchor in enumerate(self.anchors):
            center_x, center_y = to_screen(anchor["x"], anchor["y"])
            is_selected_anchor = self.selected_anchor_idx == idx
            is_mask_selected = self._anchor_is_mask_selected(anchor)
            is_scanned = bool(anchor.get("is_scanned", False))
            is_draft = anchor.get("sync_state") == "draft"

            if is_selected_anchor:
                ring = QColor(250, 204, 21)
                fill = QColor(34, 211, 238)
            elif is_mask_selected:
                ring = QColor(34, 211, 238)
                fill = QColor(6, 182, 212)
            elif is_scanned:
                ring = QColor(34, 197, 94)
                fill = QColor(22, 163, 74)
            elif is_draft:
                ring = QColor(245, 158, 11)
                fill = QColor(217, 119, 6)
            else:
                ring = QColor(99, 102, 241)
                fill = QColor(79, 70, 229)

            painter.setPen(QPen(ring, 4 if is_mask_selected or is_selected_anchor else 2))
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
            if anchor.get("room_id") or anchor.get("zone_id"):
                coord_text = (
                    f"L({float(anchor.get('local_x_m', anchor['x'])):.1f}, "
                    f"{float(anchor.get('local_y_m', anchor['y'])):.1f}, "
                    f"{anchor.get('z', 0.0):.1f})"
                )
            else:
                coord_text = f"G({anchor['x']:.1f}, {anchor['y']:.1f}, {anchor.get('z', 0.0):.1f})"
            painter.drawText(center_x + 16, center_y + 4, coord_text)
            telemetry = self.anchor_telemetry.get(self._coerce_int_id(anchor.get("anchor_id"), 0))
            if telemetry:
                distance_m = telemetry["distance_mm"] / 1000.0
                weight = telemetry.get("weight")
                live_text = f"{distance_m:.3f} m"
                if weight is not None:
                    live_text += f"  W:{weight}"
                painter.setPen(QColor(103, 232, 249) if is_mask_selected else QColor(148, 163, 184))
                painter.drawText(center_x + 16, center_y + 17, live_text)
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

        self._label_hitboxes = []
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
            # ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ visual style from 05e6a86b ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬
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

            is_selected = (zone.id == self.selected_zone_id or zone.id in self.selected_zone_ids)
            if is_selected:
                border_color = QColor("#FACC15")
                border_width += 1.2

            object_height = max(0.0, zone.max_z - zone.min_z) if object_type in {"wall", "object"} else 0.0
            if object_type == "wall":
                path = self._wall_footprint_screen_path(zone, to_screen)
                if self.preview_25d and object_height > 0 and not path.isEmpty():
                    extruded = False
                    for polygon in path.toSubpathPolygons():
                        if polygon.count() >= 3:
                            draw_extruded_polygon(polygon, fill_color, border_color, object_height)
                            extruded = True
                    if not extruded:
                        painter.fillPath(path, QBrush(fill_color))
                        painter.strokePath(path, QPen(border_color, border_width, pen_style))
                elif not path.isEmpty():
                    painter.fillPath(path, QBrush(fill_color))
                    painter.strokePath(path, QPen(border_color, border_width, pen_style))
            elif self.preview_25d and object_type == "object" and object_height > 0 and len(zone.points) >= 3:
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

            show_label = self.overlay_detail_mode or object_type in {"room", "zone", "object"}
            if show_label and len(zone.points) >= 3:
                cx = sum(p[0] for p in zone.points) / len(zone.points)
                cy = sum(p[1] for p in zone.points) / len(zone.points)
                label_x = cx + float(getattr(zone, "label_offset_x", 0.0))
                label_y = cy + float(getattr(zone, "label_offset_y", 0.0))
                scx, scy = to_screen(label_x, label_y)
                
                painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
                if object_type == "zone":
                    text = f"{zone.name} ({zone.speed_limit:.1f} m/s)" if self.overlay_detail_mode else zone.name
                elif object_type == "wall":
                    text = f"{zone.name} ({object_height:.1f} m high)"
                else:
                    text = zone.name
                text_rect = painter.fontMetrics().boundingRect(text)
                text_rect.translate(int(scx - text_rect.width() / 2), int(scy - text_rect.height() / 2))
                self._label_hitboxes.append((QRect(text_rect.adjusted(-6, -4, 6, 4)), zone.id))
                
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

                    preview_length = math.hypot(self.mouse_world_pos[0] - last_pt[0], self.mouse_world_pos[1] - last_pt[1])
                    if preview_length > 1e-9:
                        preview_text = f"{preview_length:.2f}m"
                        lx = last_pt[0] + (self.mouse_world_pos[0] - last_pt[0]) * 0.65
                        ly = last_pt[1] + (self.mouse_world_pos[1] - last_pt[1]) * 0.65
                        slx, sly = to_screen(lx, ly)
                        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
                        preview_rect = painter.fontMetrics().boundingRect(preview_text)
                        preview_rect.translate(int(slx - preview_rect.width() / 2), int(sly - preview_rect.height() - 10))
                        painter.setPen(Qt.PenStyle.NoPen)
                        painter.setBrush(QColor(15, 23, 42, 220))
                        painter.drawRoundedRect(preview_rect.adjusted(-4, -2, 4, 2), 4, 4)
                        painter.setPen(QColor(34, 211, 238))
                        painter.drawText(preview_rect.x(), preview_rect.y() + preview_rect.height() - 3, preview_text)
                        painter.setPen(QPen(active_color, 2, Qt.PenStyle.SolidLine))
                # Draw vertices
                for pt in self.current_draw_points:
                    sx, sy = to_screen(pt[0], pt[1])
                    painter.setPen(QPen(QColor(255, 255, 255), 1.5))
                    painter.setBrush(active_color)
                    painter.drawEllipse(int(sx - 4), int(sy - 4), 8, 8)

        if self.dim_tracking_view:
            self._draw_anchor_layer(painter, to_screen, draw_connections=False)

        probe_dimension = self._probe_dimension_payload()
        if probe_dimension is not None:
            start_pt = probe_dimension["start"]
            end_pt = probe_dimension["end"]
            sx1, sy1 = to_screen(start_pt[0], start_pt[1])
            sx2, sy2 = to_screen(end_pt[0], end_pt[1])
            measure_color = QColor(34, 211, 238, 230)
            painter.setPen(QPen(measure_color, 2, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawLine(sx1, sy1, sx2, sy2)
            painter.setPen(QPen(QColor(255, 255, 255), 1.5))
            painter.setBrush(QColor(15, 23, 42))
            painter.drawEllipse(int(sx1 - 4), int(sy1 - 4), 8, 8)
            painter.drawEllipse(int(sx2 - 4), int(sy2 - 4), 8, 8)

            label = f"{probe_dimension['length']:.2f} m"
            mx = (start_pt[0] + end_pt[0]) / 2.0
            my = (start_pt[1] + end_pt[1]) / 2.0
            smx, smy = to_screen(mx, my)
            painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            text_rect = painter.fontMetrics().boundingRect(label)
            offset_y = -12 if probe_dimension["axis"] == "x" else -6
            offset_x = 0 if probe_dimension["axis"] == "x" else 12
            text_rect.translate(int(smx - text_rect.width() / 2 + offset_x), int(smy - text_rect.height() + offset_y))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(15, 23, 42, 220))
            painter.drawRoundedRect(text_rect.adjusted(-4, -2, 4, 2), 4, 4)
            painter.setPen(measure_color)
            painter.drawText(text_rect.x(), text_rect.y() + text_rect.height() - 3, label)
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

        is_interacting = bool(
            self._selection_box_active
            or self._zone_drag_start_world
            or self.selected_vertex_idx is not None
            or self.selected_edge_idx is not None
            or self.dragging_anchor_idx is not None
        )
        if self.overlay_detail_mode and self.is_developer_mode and not is_interacting:
            for zone in self.geofence_zones:
                draw_dimensions(zone.points, is_closed=True, zone=zone)
            if self.edit_mode == "draw" and self.current_draw_points:
                draw_dimensions(self.current_draw_points, is_closed=False)
        elif self.overlay_detail_mode and self.selected_zone_id:
            sel_zone = next((z for z in self.geofence_zones if z.id == self.selected_zone_id), None)
            if sel_zone:
                draw_dimensions(sel_zone.points, is_closed=True, zone=sel_zone)
        if self._selection_box_active and self._selection_box_start and self._selection_box_end:
            rect = self._selection_box_rect()
            painter.setPen(QPen(QColor(34, 211, 238), 2, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(34, 211, 238, 28))
            painter.drawRect(rect)

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
        self.selected_zone_ids = set()
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

        old_snapshot = zone.to_dict()
        changed = False
        if prop_name == "name":
            changed = zone.name != str(value)
        elif prop_name == "color":
            changed = zone.color != str(value)
        elif prop_name == "height":
            changed = abs((zone.max_z - zone.min_z) - float(value)) > 1e-9
        elif prop_name == "speed_limit":
            changed = abs(zone.speed_limit - float(value)) > 1e-9
        elif prop_name == "thickness":
            changed = abs(zone.thickness - float(value)) > 1e-9
        elif prop_name == "shape_kind":
            changed = zone.shape_kind != str(value)
        elif prop_name == "object_subtype":
            changed = zone.object_subtype != str(value)
        elif prop_name == "object_direction":
            changed = zone.object_direction != str(value)
        elif prop_name == "wall_mode":
            changed = zone.wall_mode != str(value)
        elif prop_name == "host_room_id":
            new_value = str(value) if value else None
            changed = zone.host_room_id != new_value
        if not changed:
            return

        self._push_undo_state()
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

        if zone.to_dict() == old_snapshot:
            if self._undo_stack:
                self._undo_stack.pop()
            return
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
        current_length = math.hypot(pt2[0] - pt1[0], pt2[1] - pt1[1])
        current_angle = math.degrees(math.atan2(pt2[1] - pt1[1], pt2[0] - pt1[0]))
        angle_delta = abs(((current_angle - float(angle_deg) + 180.0) % 360.0) - 180.0)
        if abs(current_length - float(length)) <= 1e-9 and angle_delta <= 1e-9:
            return

        self._push_undo_state()
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

    def set_render_fps(self, fps: int):
        self._target_render_fps = max(30, min(120, int(fps or 60)))
        if hasattr(self, "_render_timer"):
            self._render_timer.setInterval(max(1, int(1000 / self._target_render_fps)))

    def _flush_render_update(self):
        if hasattr(self, "_render_timer"):
            self._render_timer.stop()
        if not getattr(self, "_render_dirty", False):
            return
        self._render_dirty = False
        super().update()
        parent = getattr(self, "parent_tab", None)
        if parent is not None:
            scheduler = getattr(parent, "schedule_preview_pane_update", None)
            if callable(scheduler):
                scheduler()

    def update(self, *args, **kwargs):
        if not hasattr(self, "_render_timer"):
            super().update(*args, **kwargs)
            return
        self._render_dirty = True
        if not self._render_timer.isActive():
            self._render_timer.start()
