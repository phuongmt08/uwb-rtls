import logging
import math

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

log = logging.getLogger(__name__)

OPENGL_AVAILABLE = False
try:
    import pyqtgraph.opengl as gl

    OPENGL_AVAILABLE = True
except ImportError:
    log.warning("PyOpenGL is not installed. 3D visualization will be disabled.")


class Geofence3DWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._zones = []
        self._anchors = []
        self._tag_position = [0.0, 0.0, 0.0]
        self._tag_yaw = 0.0
        self._trail_points = []
        self._zone_items = []
        self._anchor_items = []

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
        self.gl_widget.setBackgroundColor(QColor("#F8FAFC"))
        self.gl_widget.setCameraPosition(distance=15, elevation=30, azimuth=-45)
        layout.addWidget(self.gl_widget)
        self._initialize_scene()

    def _initialize_scene(self):
        grid = gl.GLGridItem()
        grid.setSize(100000, 100000, 1)
        grid.setSpacing(1, 1, 1)
        grid.setColor(QColor(100, 116, 139, 70))
        self.gl_widget.addItem(grid)

        axes = gl.GLAxisItem()
        axes.setSize(5, 5, 5)
        self.gl_widget.addItem(axes)

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

    @staticmethod
    def _zone_style(zone):
        object_type = getattr(zone, "object_type", "zone")
        color = getattr(zone, "color", "#64748B")
        if object_type == "room":
            return Geofence3DWidget._rgba(color, 0.18), QColor("#7DD3FC")
        if object_type == "wall":
            return Geofence3DWidget._rgba(color, 0.88), QColor("#CBD5E1")
        if getattr(zone, "zone_type", "") == "forbidden":
            return Geofence3DWidget._rgba(color, 0.34), QColor("#F87171")
        return Geofence3DWidget._rgba(color, 0.30), QColor("#4ADE80")

    @staticmethod
    def _zone_height(zone):
        object_type = getattr(zone, "object_type", "zone")
        bottom = float(getattr(zone, "min_z", 0.0))
        top = float(getattr(zone, "max_z", bottom))
        if object_type == "room":
            top = bottom + 0.03
        elif object_type == "zone" and top <= bottom:
            top = bottom + 0.50
        elif top <= bottom:
            top = bottom + 0.10
        return bottom, top

    def set_geofences(self, zones):
        self._zones = list(zones or [])
        if not self.gl_widget:
            return
        for item in self._zone_items:
            self.gl_widget.removeItem(item)
        self._zone_items.clear()

        for zone in self._zones:
            points = list(getattr(zone, "points", []) or [])
            if len(points) < 3:
                continue
            bottom, top = self._zone_height(zone)
            count = len(points)
            vertices = np.array(
                [[x, y, bottom] for x, y in points]
                + [[x, y, top] for x, y in points],
                dtype=float,
            )
            faces = []
            for index in range(1, count - 1):
                faces.append([0, index + 1, index])
                faces.append([count, count + index, count + index + 1])
            for index in range(count):
                next_index = (index + 1) % count
                faces.append([index, next_index, count + next_index])
                faces.append([index, count + next_index, count + index])

            fill, edge = self._zone_style(zone)
            mesh = gl.GLMeshItem(
                vertexes=vertices,
                faces=np.array(faces, dtype=int),
                color=fill,
                smooth=False,
                shader="balloon",
                drawEdges=True,
                edgeColor=edge,
            )
            self.gl_widget.addItem(mesh)
            self._zone_items.append(mesh)

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
