"""
===============================================================================
  UWB RTLS Studio — Geofence 3D Viewer Popup
===============================================================================
  File        : views/popups/geofence_3d_viewer.py
  Description : 3D visualization of 2.5D Geofence maps using pyqtgraph.opengl.
  MVVM Role   : VIEW — 3D layout rendering.
===============================================================================
"""
import logging
import numpy as np
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QMessageBox
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QColor

log = logging.getLogger(__name__)

# Try importing pyqtgraph.opengl and catch ImportError if PyOpenGL is not installed
OPENGL_AVAILABLE = False
try:
    import pyqtgraph.opengl as gl
    OPENGL_AVAILABLE = True
except ImportError:
    log.warning("PyOpenGL is not installed. 3D visualization will be disabled.")


class Geofence3DViewer(QDialog):
    def __init__(self, viewmodel, parent=None):
        super().__init__(parent)
        self._vm = viewmodel
        self.setWindowTitle("UWB RTLS Studio — 2.5D Geofence Viewer")
        self.resize(800, 600)
        self.setStyleSheet("background-color: #0F172A; color: #F8FAFC; font-family: 'Segoe UI';")

        self._tag_pos = [0.0, 0.0, 0.0]
        self._trail_points = []
        self._mesh_items = []

        self._build_ui()
        
        if OPENGL_AVAILABLE:
            self._init_3d_view()
            self._load_data()
            self._connect_signals()
        else:
            self._show_opengl_warning()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        # Header section
        header_layout = QHBoxLayout()
        title_label = QLabel("🌌 2.5D Geofence Map Real-time Viewer")
        title_label.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        title_label.setStyleSheet("color: #22D3EE;")
        header_layout.addWidget(title_label)
        
        btn_close = QPushButton("Đóng")
        btn_close.setStyleSheet("QPushButton { background: #334155; color: white; border: 1px solid #475569; border-radius: 6px; padding: 5px 15px; font-weight: bold; }"
                                "QPushButton:hover { background: #475569; }")
        btn_close.clicked.connect(self.close)
        header_layout.addWidget(btn_close)
        layout.addLayout(header_layout)

        # 3D Container or Warning label
        if OPENGL_AVAILABLE:
            self.gl_widget = gl.GLViewWidget(self)
            self.gl_widget.setStyleSheet("border: 1px solid #334155; border-radius: 8px;")
            self.gl_widget.setCameraPosition(distance=15, elevation=30, azimuth=-45)
            layout.addWidget(self.gl_widget)
        else:
            self.warning_container = QLabel(self)
            self.warning_container.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.warning_container.setWordWrap(True)
            self.warning_container.setStyleSheet("background-color: #1E293B; border: 1px solid #EF4444; border-radius: 8px; padding: 20px; color: #F87171; font-size: 14px;")
            layout.addWidget(self.warning_container)

    def _show_opengl_warning(self):
        warning_msg = (
            "⚠️ Không thể mở chế độ 3D!\n\n"
            "Thư viện PyOpenGL chưa được cài đặt trên máy tính của bạn.\n"
            "Để kích hoạt tính năng hiển thị 3D cho mô hình 2.5D, vui lòng cài đặt bằng lệnh:\n"
            "pip install PyOpenGL PyOpenGL_accelerate"
        )
        self.warning_container.setText(warning_msg)

    def _init_3d_view(self):
        # 1. Add grid floor
        grid = gl.GLGridItem()
        grid.setSize(40, 40, 1)
        grid.setSpacing(1, 1, 1)
        grid.translate(0, 0, 0)
        self.gl_widget.addItem(grid)

        # 2. Add Coordinate Axes (X = Red, Y = Green, Z = Blue)
        axes = gl.GLAxisItem()
        axes.setSize(5, 5, 5)
        self.gl_widget.addItem(axes)

        # 3. Add Tag scatter point
        self.tag_scatter = gl.GLScatterPlotItem(
            pos=np.array([[0.0, 0.0, 0.0]]),
            color=np.array([[34/255, 211/255, 238/255, 1.0]]),
            size=18,
            pxMode=True
        )
        self.gl_widget.addItem(self.tag_scatter)

        # 4. Add Tag trail line
        self.tag_trail = gl.GLLinePlotItem(
            pos=np.array([[0.0, 0.0, 0.0]]),
            color=QColor("#60A5FA"),
            width=2.5,
            antialias=True
        )
        self.gl_widget.addItem(self.tag_trail)

        # 5. Add Anchors scatter item
        self.anchors_scatter = gl.GLScatterPlotItem(
            pos=np.array([[0.0, 0.0, 0.0]]),
            color=np.array([[99/255, 102/255, 241/255, 1.0]]),
            size=12,
            pxMode=True
        )
        self.gl_widget.addItem(self.anchors_scatter)

    def _load_data(self):
        if not self._vm:
            return

        # Load Anchors
        layout = getattr(self._vm, "current_anchor_layout", [])
        if layout:
            pts = []
            for a in layout:
                pts.append([a.get("x_m", a.get("x", 0.0)), a.get("y_m", a.get("y", 0.0)), a.get("z_m", a.get("z", 0.0))])
            self.anchors_scatter.setData(pos=np.array(pts))

        # Load Geofences as 3D Prisms
        zones = self._vm.get_geofence_zones()
        self._draw_zones_3d(zones)

    def _draw_zones_3d(self, zones):
        # Clear old meshes
        for item in self._mesh_items:
            self.gl_widget.removeItem(item)
        self._mesh_items.clear()

        for zone in zones:
            if len(zone.points) < 3:
                continue

            # Triangulate prism vertices
            # Let points = N
            # Vertices 0..N-1 represent bottom face
            # Vertices N..2N-1 represent top face
            N = len(zone.points)
            verts = []
            # Bottom vertices (z = min_z)
            for pt in zone.points:
                verts.append([pt[0], pt[1], zone.min_z])
            # Top vertices (z = max_z)
            for pt in zone.points:
                verts.append([pt[0], pt[1], zone.max_z])

            verts = np.array(verts)

            # Triangulate faces
            faces = []
            
            # Bottom face (triangulated from vertex 0)
            for i in range(1, N - 1):
                faces.append([0, i, i + 1])
                
            # Top face (triangulated from vertex N)
            for i in range(1, N - 1):
                faces.append([N, N + i, N + i + 1])
                
            # Side faces (2 triangles per side segment)
            for i in range(N):
                next_i = (i + 1) % N
                # Triangle 1
                faces.append([i, next_i, next_i + N])
                # Triangle 2
                faces.append([i, next_i + N, i + N])

            faces = np.array(faces)

            # Colors
            if zone.zone_type == "forbidden":
                color = [239/255, 68/255, 68/255, 0.4]  # transparent red
            else:
                color = [34/255, 197/255, 94/255, 0.4]   # transparent green

            mesh = gl.GLMeshItem(
                vertexes=verts,
                faces=faces,
                color=color,
                smooth=False,
                shader='balloon',
                drawEdges=True,
                edgeColor=QColor("#CBD5E1")
            )
            self.gl_widget.addItem(mesh)
            self._mesh_items.append(mesh)

    def _connect_signals(self):
        if self._vm:
            self._vm.position_updated.connect(self._on_position_updated)
            self._vm.geofence_layout_updated.connect(self._draw_zones_3d)

    def _on_position_updated(self, x, y, z, rms):
        self._tag_pos = [x, y, z]
        self.tag_scatter.setData(pos=np.array([[x, y, z]]))

        # Maintain 3D trail points
        self._trail_points.append([x, y, z])
        if len(self._trail_points) > 50:
            self._trail_points.pop(0)

        self.tag_trail.setData(pos=np.array(self._trail_points))
        self.gl_widget.update()
