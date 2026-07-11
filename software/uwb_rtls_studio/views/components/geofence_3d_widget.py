import logging
import math

import numpy as np
from PyQt6.QtCore import Qt, QTimer, QPointF, QEvent
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF, QVector4D
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

log = logging.getLogger(__name__)

OPENGL_AVAILABLE = False
try:
    import pyqtgraph.opengl as gl

    OPENGL_AVAILABLE = True
except ImportError:
    log.warning("PyOpenGL is not installed. 3D visualization will be disabled.")

# Register custom_shaded shader once OpenGL is confirmed available.
# This shader keeps colors true to their 2D map colours (75% ambient)
# with only a subtle directional hint from top-front-right (35% max).
_CUSTOM_SHADER_REGISTERED = False
if OPENGL_AVAILABLE:
    try:
        from pyqtgraph.opengl.shaders import ShaderProgram, VertexShader, FragmentShader
        if "custom_shaded" not in ShaderProgram.names:
            ShaderProgram("custom_shaded", [
                VertexShader("""
                    uniform mat4 u_mvp;
                    uniform mat3 u_normal;
                    attribute vec4 a_position;
                    attribute vec3 a_normal;
                    attribute vec4 a_color;
                    varying vec4 v_color;
                    varying vec3 v_normal;
                    void main() {
                        v_normal = normalize(u_normal * a_normal);
                        v_color = a_color;
                        gl_Position = u_mvp * a_position;
                    }
                """),
                FragmentShader("""
                    #ifdef GL_ES
                    precision mediump float;
                    #endif
                    varying vec4 v_color;
                    varying vec3 v_normal;
                    void main() {
                        float p = dot(v_normal, normalize(vec3(0.5, -0.5, -1.0)));
                        p = p < 0. ? 0. : p * 0.35;
                        vec3 rgb = v_color.rgb * (0.75 + p);
                        gl_FragColor = vec4(rgb, v_color.a);
                    }
                """)
            ])
        _CUSTOM_SHADER_REGISTERED = True
    except Exception as _e:
        log.warning("Could not register custom_shaded shader: %s", _e)


class _OrientationGizmo(QWidget):
    """Small SolidWorks-style camera orientation indicator."""

    def __init__(self, view, parent=None):
        super().__init__(parent)
        self._view = view
        self.setFixedSize(96, 96)
        self.setStyleSheet("background: transparent;")

    def _axis_projection(self, axis):
        try:
            view_matrix = self._view.viewMatrix()
            projected = view_matrix.map(QVector4D(float(axis[0]), float(axis[1]), float(axis[2]), 0.0))
            return projected.x(), -projected.y(), projected.z()
        except Exception:
            return float(axis[0]), -float(axis[1]), float(axis[2])

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        center = QPointF(32.0, 64.0)
        axis_length = 24.0
        axes = [
            ("X", (1.0, 0.0, 0.0), QColor("#EF4444")),
            ("Y", (0.0, 1.0, 0.0), QColor("#84CC16")),
            ("Z", (0.0, 0.0, 1.0), QColor("#3B82F6")),
        ]
        projected_axes = []
        for label, axis, color in axes:
            proj_x, proj_y, depth = self._axis_projection(axis)
            magnitude = math.hypot(proj_x, proj_y)
            if magnitude <= 1e-6:
                screen_x, screen_y = 0.0, 0.0
            else:
                screen_x = proj_x / magnitude
                screen_y = proj_y / magnitude
            projected_axes.append((depth, label, color, QPointF(center.x() + screen_x * axis_length, center.y() + screen_y * axis_length)))

        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        for _depth, label, color, endpoint in sorted(projected_axes, key=lambda item: item[0], reverse=True):
            painter.setPen(QPen(color, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(center, endpoint)
            painter.setPen(color)
            painter.drawText(int(endpoint.x() - 6), int(endpoint.y() - 8), 18, 18, Qt.AlignmentFlag.AlignCenter, label)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#0F172A"))
        painter.drawEllipse(center, 3.5, 3.5)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            host = self.parent()
            if host is not None and hasattr(host, "reset_camera_orientation"):
                host.reset_camera_orientation()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class Geofence3DWidget(QWidget):
    _RESET_AZIMUTH = 45.0
    _RESET_ELEVATION = 35.26438968
    def __init__(self, parent=None):
        super().__init__(parent)
        self._zones = []
        self._anchors = []
        self._tag_position = [0.0, 0.0, 0.0]
        self._tag_yaw = 0.0
        self._trail_points = []
        self._zone_items = []
        self._anchor_items = []
        self._last_gizmo_camera = None
        self._active_room_ids: set = set()
        self._camera_reset_timer = QTimer(self)
        self._camera_reset_timer.setInterval(16)
        self._camera_reset_timer.timeout.connect(self._step_camera_reset)
        self._camera_reset_step = 0
        self._camera_reset_steps = 10
        self._camera_reset_start = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if not OPENGL_AVAILABLE:
            warning = QLabel(
                "3D mode is unavailable.\nInstall PyOpenGL and pyqtgraph to enable it.",
                self,
            )
            warning.setAlignment(Qt.AlignmentFlag.AlignCenter)
            warning.setWordWrap(True)
            warning.setStyleSheet(
                "background: #1E293B; color: #F87171; "
                "border: 1px solid #EF4444; font-size: 14px;"
            )
            layout.addWidget(warning)
            self.gl_widget = None
            return

        self.gl_widget = gl.GLViewWidget(self)
        self.gl_widget.installEventFilter(self)
        self.gl_widget.setBackgroundColor(QColor("#F8FAFC"))
        self.gl_widget.setCameraPosition(distance=15, elevation=self._RESET_ELEVATION, azimuth=self._RESET_AZIMUTH)
        layout.addWidget(self.gl_widget)
        self._orientation_gizmo = _OrientationGizmo(self.gl_widget, self)
        self._gizmo_timer = QTimer(self)
        self._gizmo_timer.setInterval(50)
        self._gizmo_timer.timeout.connect(self._update_orientation_gizmo)
        self._gizmo_timer.start()
        self._initialize_scene()
        self._position_orientation_gizmo()

    def eventFilter(self, watched, event):
        if watched is self.gl_widget:
            if event.type() == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
                self.reset_camera_orientation()
                event.accept()
                return True
            if event.type() == QEvent.Type.Wheel:
                delta = event.angleDelta().y() or event.angleDelta().x()
                if delta:
                    steps = float(delta) / 120.0
                    pan_pixels = 55.0 * steps
                    modifiers = event.modifiers()
                    if modifiers & Qt.KeyboardModifier.ShiftModifier:
                        self.gl_widget.pan(-pan_pixels, 0.0, 0.0, relative="view")
                        event.accept()
                        return True
                    if modifiers & Qt.KeyboardModifier.ControlModifier:
                        self.gl_widget.pan(0.0, pan_pixels, 0.0, relative="view")
                        event.accept()
                        return True
        return super().eventFilter(watched, event)

    def _initialize_scene(self):
        grid = gl.GLGridItem()
        grid.setSize(100, 100, 1)
        grid.setSpacing(1, 1, 1)
        grid.setColor(QColor(100, 116, 139, 70))
        self.gl_widget.addItem(grid)

        self._add_global_axes()

        vertices, faces = self._make_tag_arrow(0.0, 0.0, 0.0, 0.0)
        self.tag_arrow = gl.GLMeshItem(
            vertexes=vertices,
            faces=faces,
            color=[14 / 255, 165 / 255, 233 / 255, 1.0],
            smooth=False,
            shader="shaded",
            drawEdges=True,
            edgeColor=QColor("#38BDF8"),
        )
        self.tag_arrow.setGLOptions("translucent")
        self.gl_widget.addItem(self.tag_arrow)
        self.tag_trail = gl.GLLinePlotItem(
            pos=np.array([self._tag_position]),
            color=QColor("#60A5FA"),
            width=2.5,
            antialias=True,
        )
        self.gl_widget.addItem(self.tag_trail)

    def _add_global_axes(self):
        axis_specs = (
            ("X", (0.0, 0.0, 0.04), (4.5, 0.0, 0.04), "#EF4444", (4.8, 0.0, 0.04)),
            ("Y", (0.0, 0.0, 0.04), (0.0, 4.5, 0.04), "#84CC16", (0.0, 4.8, 0.04)),
            ("Z", (0.0, 0.0, 0.0), (0.0, 0.0, 4.8), "#3B82F6", (0.0, 0.0, 5.2)),
        )
        for label, start, end, color, label_pos in axis_specs:
            self._add_global_axis_segment(start, end, color)
            for arrow_start, arrow_end in self._global_axis_arrowheads(label, end):
                self._add_global_axis_segment(arrow_start, arrow_end, color)
            if hasattr(gl, "GLTextItem"):
                text_item = gl.GLTextItem(pos=label_pos, text=label, color=QColor(color))
                self.gl_widget.addItem(text_item)

    def _add_global_axis_segment(self, start, end, color):
        line = gl.GLLinePlotItem(
            pos=np.array([start, end], dtype=float),
            color=QColor(color),
            width=3.0,
            antialias=True,
        )
        line.setGLOptions("translucent")
        line.setDepthValue(100)
        self.gl_widget.addItem(line)

    def _global_axis_arrowheads(self, label, endpoint):
        arrow = 0.30
        spread = 0.14
        x, y, z = endpoint
        if label == "X":
            return [
                ((x, y, z), (x - arrow, y + spread, z)),
                ((x, y, z), (x - arrow, y - spread, z)),
            ]
        if label == "Y":
            return [
                ((x, y, z), (x + spread, y - arrow, z)),
                ((x, y, z), (x - spread, y - arrow, z)),
            ]
        return [
            ((x, y, z), (x + spread, y, z - arrow)),
            ((x, y, z), (x - spread, y, z - arrow)),
            ((x, y, z), (x, y + spread, z - arrow)),
            ((x, y, z), (x, y - spread, z - arrow)),
        ]
    @staticmethod
    def _make_tag_arrow(x, y, z, yaw_deg):
        yaw = math.radians(float(yaw_deg))
        cos_yaw, sin_yaw = math.cos(yaw), math.sin(yaw)
        base = np.array([
            [0.35, 0.0, -0.08], [-0.20, -0.15, -0.08],
            [-0.08, 0.0, -0.08], [-0.20, 0.15, -0.08],
            [0.35, 0.0, 0.08], [-0.20, -0.15, 0.08],
            [-0.08, 0.0, 0.08], [-0.20, 0.15, 0.08],
        ], dtype=float)
        vertices = np.zeros_like(base)
        vertices[:, 0] = base[:, 0] * cos_yaw - base[:, 1] * sin_yaw + float(x)
        vertices[:, 1] = base[:, 0] * sin_yaw + base[:, 1] * cos_yaw + float(y)
        vertices[:, 2] = base[:, 2] + float(z)
        faces = np.array([
            [0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
            [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
            [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7],
        ], dtype=int)
        return vertices, faces

    def _update_tag_arrow(self):
        if not self.gl_widget:
            return
        vertices, faces = self._make_tag_arrow(
            self._tag_position[0], self._tag_position[1], self._tag_position[2], self._tag_yaw
        )
        self.tag_arrow.setMeshData(vertexes=vertices, faces=faces)

    @staticmethod
    def _rgba(hex_color, alpha):
        color = QColor(str(hex_color).replace("_semi", ""))
        if not color.isValid():
            color = QColor("#64748B")
        return color.redF(), color.greenF(), color.blueF(), alpha

    def _zone_style(self, zone):
        """Return (fill_rgba, edge_QColor) for a zone.
        Active rooms are highlighted green; all other objects use their map color.
        """
        object_type = getattr(zone, "object_type", "zone")
        color = getattr(zone, "color", "#64748B")
        zone_id = getattr(zone, "id", None)

        # Active room highlight ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â show green tint in 3D (matches 05e6a86b)
        if object_type == "room" and zone_id in self._active_room_ids:
            return Geofence3DWidget._rgba("#22C55E", 0.60), (0.09, 0.64, 0.37, 1.0)

        if object_type == "room":
            hex_color = color if str(color).startswith("#") else "#1D4ED8"
            fill = Geofence3DWidget._rgba(hex_color, 0.12)
            edge = (0.49, 0.83, 0.99, 1.0)  # #7DD3FC sky blue
            return fill, edge
        if object_type == "wall":
            hex_color = color if str(color).startswith("#") else "#111827"
            r, g, b, a = Geofence3DWidget._rgba(hex_color, 1.0)
            factor = 0.05
            edge = (min(1.0, r + (1.0 - r) * factor),
                    min(1.0, g + (1.0 - g) * factor),
                    min(1.0, b + (1.0 - b) * factor), 1.0)
            return (r, g, b, a), edge
        if object_type == "object":
            subtype = getattr(zone, "object_subtype", "generic")
            hex_color = color if str(color).startswith("#") else ("#D97706" if subtype == "stairs" else "#F59E0B")
            r, g, b, a = Geofence3DWidget._rgba(hex_color, 1.0)
            factor = 0.20
            edge = (min(1.0, r + (1.0 - r) * factor),
                    min(1.0, g + (1.0 - g) * factor),
                    min(1.0, b + (1.0 - b) * factor), 1.0)
            return (r, g, b, a), edge
        if getattr(zone, "zone_type", "") == "forbidden":
            # Rule zones: very low fill alpha so they appear as a flat 2D overlay.
            return Geofence3DWidget._rgba(color, 0.12), QColor("#EF4444")
        # Allowed zone
        return Geofence3DWidget._rgba(color, 0.12), QColor("#22C55E")

    @staticmethod
    def _zone_height(zone):
        object_type = getattr(zone, "object_type", "zone")
        bottom = float(getattr(zone, "min_z", 0.0))
        top = float(getattr(zone, "max_z", bottom))
        if object_type == "room":
            top = bottom + 0.05  # thin 5 cm slab matching 05e6a86b
        elif object_type == "zone" and top <= bottom:
            top = bottom
        elif top <= bottom:
            top = bottom + 0.10
        return bottom, top

    def _add_surface(self, points, z_value, fill, edge):
        """Render a rule zone (allowed/forbidden) as a flat 2D planar overlay.

        Uses flat shading (shader=None) so the fill colour is perfectly uniform
        from every camera angle ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â no balloon-shader alpha distortion.
        A separate thick outline loop is drawn on top for clear zone boundaries.
        """
        points = list(points)
        if len(points) < 3:
            return
        area = sum(
            points[idx][0] * points[(idx + 1) % len(points)][1]
            - points[(idx + 1) % len(points)][0] * points[idx][1]
            for idx in range(len(points))
        )
        if area < 0:
            points.reverse()
        vertices = np.array([[x, y, z_value] for x, y in points], dtype=float)
        faces = np.array(
            [[0, index + 1, index] for index in range(1, len(points) - 1)],
            dtype=int,
        )
        # Flat shader ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â colour is view-angle independent
        mesh = gl.GLMeshItem(
            vertexes=vertices, faces=faces, color=fill,
            smooth=False, shader=None, drawEdges=False,
        )
        mesh.setGLOptions("translucent")
        mesh.setDepthValue(10)
        self.gl_widget.addItem(mesh)
        self._zone_items.append(mesh)

        # Draw a thick outline loop so the boundary is clearly visible
        self._add_zone_outline(points, z_value, edge)

    def _add_zone_outline(self, points, z_value, edge_color):
        """Draw a closed thick coloured border for a rule zone on the ground plane."""
        if len(points) < 3:
            return
        # Close the loop
        loop = list(points) + [points[0]]
        pos = np.array([[x, y, z_value + 0.001] for x, y in loop], dtype=float)
        # Convert QColor edge_color to normalised RGBA tuple for GLLinePlotItem
        if isinstance(edge_color, QColor):
            r = edge_color.redF()
            g = edge_color.greenF()
            b = edge_color.blueF()
            line_color = (r, g, b, 1.0)
        else:
            line_color = edge_color
        outline = gl.GLLinePlotItem(
            pos=pos,
            color=line_color,
            width=3.0,
            antialias=True,
            mode="line_strip",
        )
        outline.setGLOptions("translucent")
        outline.setDepthValue(11)
        self.gl_widget.addItem(outline)
        self._zone_items.append(outline)

    def _normalize_winding(self, points):
        """Ensure points are in counter-clockwise order."""
        if len(points) < 3:
            return points
        area = 0.0
        for i in range(len(points)):
            x1, y1 = points[i]
            x2, y2 = points[(i + 1) % len(points)]
            area += (x1 * y2 - x2 * y1)
        if area < 0:
            return list(reversed(points))
        return points


    @staticmethod
    def _polygon_area_abs(points):
        if len(points) < 3:
            return 0.0
        area = 0.0
        for idx, (x1, y1) in enumerate(points):
            x2, y2 = points[(idx + 1) % len(points)]
            area += (x1 * y2) - (x2 * y1)
        return abs(area) * 0.5

    @staticmethod
    def _closed_loop_points(points):
        pts = list(points or [])
        if len(pts) >= 2 and math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1]) <= 1e-6:
            pts = pts[:-1]
        return pts

    def _wall_uses_polygon_footprint(self, zone):
        points = list(getattr(zone, "points", []) or [])
        if len(points) < 4:
            return False
        if getattr(zone, "wall_mode", "free_standing") == "boundary_outside":
            return False
        closed_points = self._closed_loop_points(points)
        return len(closed_points) >= 3 and self._polygon_area_abs(closed_points) > 1e-6

    def _add_prism_outlines(self, points, min_z, max_z, edge_color, gl_options="opaque"):
        """Draw top+bottom loops and vertical pillars for a prism outline."""
        N = len(points)
        if N < 2:
            return
        # pick a plain edge color tuple
        if isinstance(edge_color, QColor):
            ec = (edge_color.redF(), edge_color.greenF(), edge_color.blueF(), 1.0)
        else:
            ec = tuple(edge_color)

        for z_level in (min_z, max_z):
            loop = [[pt[0], pt[1], z_level] for pt in points] + [[points[0][0], points[0][1], z_level]]
            line = gl.GLLinePlotItem(
                pos=np.array(loop, dtype=float), color=ec,
                width=1.5, mode="line_strip", antialias=True, glOptions=gl_options,
            )
            self.gl_widget.addItem(line)
            self._zone_items.append(line)

        for pt in points:
            pillar = gl.GLLinePlotItem(
                pos=np.array([[pt[0], pt[1], min_z], [pt[0], pt[1], max_z]], dtype=float),
                color=ec, width=1.5, mode="line_strip", antialias=True, glOptions=gl_options,
            )
            self.gl_widget.addItem(pillar)
            self._zone_items.append(pillar)

    def _add_prism(self, points, bottom, top, fill, edge, gl_options="opaque"):
        """Draw a closed prism with custom_shaded shader and wire-frame outlines."""
        points = self._normalize_winding(list(points))
        N = len(points)
        if N < 3:
            return
        verts = [[x, y, bottom] for x, y in points] + [[x, y, top] for x, y in points]
        verts = np.array(verts, dtype=float)
        faces = []
        for i in range(1, N - 1):
            faces.append([0, i, i + 1])
        for i in range(1, N - 1):
            faces.append([N, N + i, N + i + 1])
        for i in range(N):
            ni = (i + 1) % N
            faces.append([i, ni, ni + N])
            faces.append([i, ni + N, i + N])
        faces = np.array(faces, dtype=int)

        shader = "custom_shaded" if _CUSTOM_SHADER_REGISTERED else "shaded"
        mesh = gl.GLMeshItem(
            vertexes=verts, faces=faces, color=fill,
            smooth=False, shader=shader, drawEdges=False,
        )
        mesh.setGLOptions(gl_options)
        self.gl_widget.addItem(mesh)
        self._zone_items.append(mesh)
        self._add_prism_outlines(points, bottom, top, edge, gl_options=gl_options)

    def _draw_stair_blocks(self, points, min_z, max_z, fill, edge, direction="up"):
        """Render stair steps as stepped slab prisms (from commit 05e6a86b).

        Each step is a thin slab (tread_slab) sitting at the top of each riser,
        giving a clear stepped silhouette in 3D.
        * direction=='up'  : steps ascend from min_z to max_z (left to right).
        * direction=='down': steps descend from max_z to min_z (left to right).
        """
        if len(points or []) < 3:
            return
        direction = str(direction).lower()
        min_x = min(p[0] for p in points)
        max_x = max(p[0] for p in points)
        min_y = min(p[1] for p in points)
        max_y = max(p[1] for p in points)
        if max_x <= min_x or max_y <= min_y:
            return

        step_count = 7
        configured_height = max(0.15, float(max_z) - float(min_z))
        total_height = configured_height

        if direction == "down":
            stair_min_z = float(min_z) - total_height
            stair_max_z = float(min_z)
        else:
            stair_min_z = float(min_z)
            stair_max_z = float(min_z) + total_height

        riser = total_height / step_count
        tread_slab = max(0.035, min(0.12, riser * 0.35))
        along_x = (max_x - min_x) >= (max_y - min_y)

        for idx in range(step_count):
            if along_x:
                x1 = min_x + (max_x - min_x) * idx / step_count
                x2 = min_x + (max_x - min_x) * (idx + 1) / step_count
                footprint = [(x1, min_y), (x2, min_y), (x2, max_y), (x1, max_y)]
            else:
                y1 = min_y + (max_y - min_y) * idx / step_count
                y2 = min_y + (max_y - min_y) * (idx + 1) / step_count
                footprint = [(min_x, y1), (max_x, y1), (max_x, y2), (min_x, y2)]

            level_idx = idx + 1 if direction != "down" else step_count - idx
            step_top = stair_min_z + (stair_max_z - stair_min_z) * level_idx / step_count
            step_bottom = max(stair_min_z, step_top - tread_slab)
            self._add_prism(footprint, step_bottom, step_top, fill, edge, gl_options="opaque")

    def set_active_room_ids(self, ids):
        """Highlight the given room IDs in green in the 3D view."""
        new_ids = set(ids or [])
        if new_ids == self._active_room_ids:
            return
        self._active_room_ids = new_ids
        # Re-render so colours are refreshed
        self.set_geofences(self._zones)

    def set_geofences(self, zones):
        self._zones = list(zones or [])
        if not self.gl_widget:
            return
        for item in self._zone_items:
            self.gl_widget.removeItem(item)
        self._zone_items.clear()

        for zone in self._zones:
            points = list(getattr(zone, "points", []) or [])
            if len(points) < 2:
                continue
            bottom, top = self._zone_height(zone)
            fill, edge = self._zone_style(zone)
            object_type = getattr(zone, "object_type", "zone")
            
            if object_type == "zone" and len(points) >= 3:
                # Keep semantic height at exactly zero.
                self._add_surface(points, bottom + 0.055, fill, edge)
            elif object_type == "object" and getattr(zone, "object_subtype", "generic") == "stairs" and len(points) >= 3:
                self._draw_stair_blocks(points, bottom, top, fill, edge, getattr(zone, "object_direction", "up"))
            elif object_type == "room" and len(points) >= 3:
                self._add_prism(points, bottom, top, fill, edge, gl_options="translucent")
            elif object_type == "wall":
                thickness = max(0.0, float(getattr(zone, "thickness", 0.1)))
                if self._wall_uses_polygon_footprint(zone):
                    closed_points = self._closed_loop_points(points)
                    if len(closed_points) >= 3:
                        self._add_prism(closed_points, bottom, top, fill, edge, gl_options="opaque")
                elif len(points) >= 2 and thickness > 0.0:
                    # 1. Draw segment footprints
                    for idx in range(len(points) - 1):
                        footprint = self._wall_segment_footprint(zone, points[idx], points[idx + 1], self._zones)
                        if len(footprint) == 4:
                            self._add_prism(footprint, bottom, top, fill, edge, gl_options="opaque")

                    # 2. Draw corner join footprints
                    for idx in range(1, len(points) - 1):
                        previous_point = points[idx - 1]
                        vertex = points[idx]
                        next_point = points[idx + 1]
                        normal_a = self._wall_outward_normal(zone, previous_point, vertex, self._zones)
                        normal_b = self._wall_outward_normal(zone, vertex, next_point, self._zones)
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
                        join_pts = [vertex, a, intersection, b]
                        self._add_prism(join_pts, bottom, top, fill, edge, gl_options="opaque")
            else:
                if len(points) >= 3:
                    self._add_prism(points, bottom, top, fill, edge, gl_options="opaque")

    def _is_inside_polygon(self, poly_points, wx, wy):
        poly = QPolygonF()
        for pt in poly_points:
            poly.append(QPointF(pt[0], pt[1]))
        return poly.containsPoint(QPointF(wx, wy), Qt.FillRule.OddEvenFill)

    def _room_by_id(self, room_id, zones):
        if not room_id:
            return None
        return next(
            (
                zone for zone in zones
                if getattr(zone, "object_type", "zone") == "room" and zone.id == room_id
            ),
            None,
        )

    def _wall_outward_normal(self, zone, p1, p2, zones):
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
        host_room = self._room_by_id(getattr(zone, "host_room_id", None), zones)
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

    def _wall_segment_footprint(self, zone, p1, p2, zones):
        """Return one wall segment footprint in world coordinates."""
        x1, y1 = p1
        x2, y2 = p2
        dx = x2 - x1
        dy = y2 - y1
        length = math.hypot(dx, dy)
        thickness = max(0.0, float(getattr(zone, "thickness", 0.1)))
        if length <= 1e-9 or thickness <= 0.0:
            return []

        outward = self._wall_outward_normal(zone, p1, p2, zones)
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

    def _line_intersection(self, a, direction_a, b, direction_b):
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

    def set_anchors(self, anchors):
        self._anchors = [dict(anchor) for anchor in (anchors or [])]
        if not self.gl_widget:
            return
        for item in self._anchor_items:
            self.gl_widget.removeItem(item)
        self._anchor_items.clear()
        if not self._anchors:
            return

        positions = np.array(
            [
                [
                    float(anchor.get("x", anchor.get("x_m", 0.0))),
                    float(anchor.get("y", anchor.get("y_m", 0.0))),
                    float(anchor.get("z", anchor.get("z_m", 0.0))),
                ]
                for anchor in self._anchors
            ],
            dtype=float,
        )
        scatter = gl.GLScatterPlotItem(
            pos=positions,
            color=np.tile(
                np.array([[251 / 255, 191 / 255, 36 / 255, 1.0]]),
                (len(positions), 1),
            ),
            size=14,
            pxMode=True,
        )
        scatter.setGLOptions("translucent")
        self.gl_widget.addItem(scatter)
        self._anchor_items.append(scatter)

    def update_position(self, position):
        self._tag_position = [
            float(position.get("x", 0.0)),
            float(position.get("y", 0.0)),
            float(position.get("z", 0.0)),
        ]
        self._tag_yaw = float(position.get("yaw", self._tag_yaw))
        if not self.gl_widget:
            return
        self._update_tag_arrow()
        self._trail_points.append(list(self._tag_position))
        if len(self._trail_points) > 100:
            self._trail_points.pop(0)
        self.tag_trail.setData(pos=np.array(self._trail_points, dtype=float))
        self.gl_widget.update()

    def clear_trail(self):
        self._trail_points.clear()
        if self.gl_widget:
            self.tag_trail.setData(pos=np.array([self._tag_position], dtype=float))

    def set_camera_from_2d(self, center_x, center_y, view_range):
        if self.gl_widget:
            from PyQt6.QtGui import QVector3D

            self.gl_widget.setCameraPosition(
                pos=QVector3D(float(center_x), float(center_y), 0.0),
                distance=max(float(view_range) * 1.70, 1.0),
            )

    def camera_for_2d(self):
        if not self.gl_widget:
            return None
        center = self.gl_widget.opts.get("center")
        distance = float(self.gl_widget.opts.get("distance", 15.0))
        if center is None:
            return None
        return float(center.x()), float(center.y()), max(distance / 1.70, 1.0)

    @staticmethod
    def _normalized_angle(angle):
        return ((float(angle) + 180.0) % 360.0) - 180.0

    def reset_camera_orientation(self):
        if not self.gl_widget:
            return
        curr_center = self.gl_widget.opts.get("center")
        cx = float(curr_center.x()) if curr_center is not None else 0.0
        cy = float(curr_center.y()) if curr_center is not None else 0.0
        cz = float(curr_center.z()) if curr_center is not None else 0.0
        self._camera_reset_start = {
            "azimuth": float(self.gl_widget.opts.get("azimuth", self._RESET_AZIMUTH)),
            "elevation": float(self.gl_widget.opts.get("elevation", self._RESET_ELEVATION)),
            "distance": float(self.gl_widget.opts.get("distance", 15.0)),
            "center": (cx, cy, cz),
        }
        self._camera_reset_step = 0
        self._camera_reset_timer.start()

    def _step_camera_reset(self):
        if not self.gl_widget or not self._camera_reset_start:
            self._camera_reset_timer.stop()
            return
        self._camera_reset_step += 1
        t = min(1.0, self._camera_reset_step / max(1, self._camera_reset_steps))
        ease = 1.0 - pow(1.0 - t, 3)
        start_azimuth = self._camera_reset_start["azimuth"]
        azimuth_delta = self._normalized_angle(self._RESET_AZIMUTH - start_azimuth)
        azimuth = self._normalized_angle(start_azimuth + azimuth_delta * ease)
        elevation = self._camera_reset_start["elevation"] + (
            self._RESET_ELEVATION - self._camera_reset_start["elevation"]
        ) * ease
        distance = self._camera_reset_start["distance"] + (
            15.0 - self._camera_reset_start["distance"]
        ) * ease
        cx = self._camera_reset_start["center"][0] * (1.0 - ease)
        cy = self._camera_reset_start["center"][1] * (1.0 - ease)
        cz = self._camera_reset_start["center"][2] * (1.0 - ease)
        from PyQt6.QtGui import QVector3D
        self.gl_widget.setCameraPosition(
            pos=QVector3D(cx, cy, cz),
            azimuth=azimuth,
            elevation=elevation,
            distance=distance,
        )
        if t >= 1.0:
            self._camera_reset_timer.stop()
            self._camera_reset_start = None

    def _update_orientation_gizmo(self):
        if not self.gl_widget:
            return
        camera = (
            round(float(self.gl_widget.opts.get("azimuth", 0.0)), 2),
            round(float(self.gl_widget.opts.get("elevation", 0.0)), 2),
        )
        if camera == self._last_gizmo_camera:
            return
        self._last_gizmo_camera = camera
        self._orientation_gizmo.update()

    def _position_orientation_gizmo(self):
        gizmo = getattr(self, "_orientation_gizmo", None)
        if gizmo is not None:
            gizmo.move(10, max(10, self.height() - gizmo.height() - 10))
            gizmo.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_orientation_gizmo()
